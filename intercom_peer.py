#!/usr/bin/env python3
"""Khadas Intercom Peer — connects to ESP32 via HA intercom_native protocol.

Acts as a software intercom endpoint on Khadas, bridging:
  - Microphone (arecord) → TCP → ESP32 speaker
  - ESP32 microphone → TCP → Speaker (aplay)

Protocol: intercom_native TCP on port 6054
  Header: [type:u8][flags:u8][length:u16LE]
  Messages: START, STOP, AUDIO, PING, PONG, RING, ANSWER

Audio: 16kHz mono int16, 1024 bytes per chunk (32ms)
"""

import argparse
import asyncio
import logging
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

# Protocol constants
MSG_AUDIO = 0x01
MSG_START = 0x02
MSG_STOP = 0x03
MSG_PING = 0x04
MSG_PONG = 0x05
MSG_ERROR = 0x06
MSG_RING = 0x07
MSG_ANSWER = 0x08

FLAG_NONE = 0x00
FLAG_NO_RING = 0x02

HEADER_SIZE = 4
AUDIO_CHUNK = 1024  # bytes (512 samples * 2 bytes = 32ms at 16kHz)
MAX_PAYLOAD = 2048

SAMPLE_RATE = 16000

logger = logging.getLogger("intercom_peer")


class AudioBridge:
    """Bridges arecord/aplay subprocesses with intercom TCP."""

    def __init__(self, mic_device: str, snd_device: str, snd_rate: int = 16000):
        self.mic_device = mic_device
        self.snd_device = snd_device
        self.snd_rate = snd_rate
        self.mic_proc = None
        self.snd_proc = None
        self.mic_stdout = None
        self.snd_stdin = None
        self._running = False

    async def start_mic(self):
        """Start microphone capture subprocess."""
        cmd = [
            "arecord", "-D", self.mic_device,
            "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"
        ]
        logger.info(f"Starting mic: {' '.join(cmd)}")
        self.mic_proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self.mic_stdout = self.mic_proc.stdout
        self._running = True
        return True

    async def start_speaker(self):
        """Start speaker playback subprocess."""
        cmd = [
            "aplay", "-D", self.snd_device,
            "-f", "S16_LE", "-r", str(self.snd_rate), "-c", "1", "-t", "raw"
        ]
        logger.info(f"Starting speaker: {' '.join(cmd)}")
        self.snd_proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self.snd_stdin = self.snd_proc.stdin
        return True

    async def read_mic_chunk(self) -> bytes | None:
        """Read one audio chunk from microphone."""
        if not self.mic_stdout:
            return None
        try:
            data = await asyncio.wait_for(self.mic_stdout.read(AUDIO_CHUNK), timeout=1.0)
            return data if len(data) == AUDIO_CHUNK else None
        except asyncio.TimeoutError:
            return None

    def write_speaker(self, data: bytes):
        """Write audio data to speaker."""
        if self.snd_stdin and not self.snd_stdin.is_closing():
            try:
                self.snd_stdin.write(data)
            except BrokenPipeError:
                logger.warning("Speaker pipe broken")

    async def drain_speaker(self):
        """Drain speaker buffer."""
        if self.snd_stdin and not self.snd_stdin.is_closing():
            try:
                await self.snd_stdin.drain()
            except (BrokenPipeError, ConnectionError):
                pass

    async def stop(self):
        """Stop all audio subprocesses."""
        self._running = False
        for proc in [self.mic_proc, self.snd_proc]:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    proc.kill()
        self.mic_proc = self.snd_proc = None
        self.mic_stdout = self.snd_stdin = None


class IntercomPeer:
    """TCP peer implementing the intercom_native protocol."""

    def __init__(self, esp_host: str, esp_port: int, bridge: AudioBridge,
                 name: str = "Khadas"):
        self.host = esp_host
        self.port = esp_port
        self.bridge = bridge
        self.name = name
        self.reader = None
        self.writer = None
        self._connected = False
        self._streaming = False
        self._ringing = False
        self._call_active = False
        self._mic_task = None
        self._recv_task = None
        self._drain_counter = 0

    async def connect(self) -> bool:
        """Connect to ESP32 intercom TCP server."""
        try:
            logger.info(f"Connecting to {self.host}:{self.port}...")
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=5.0
            )
            self._connected = True
            logger.info("Connected!")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def disconnect(self):
        """Disconnect from ESP32."""
        self._connected = False
        self._streaming = False
        self._call_active = False

        if self._mic_task:
            self._mic_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()

        if self.writer:
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=1.0)
            except Exception:
                pass
            self.writer = None
            self.reader = None

        await self.bridge.stop()
        logger.info("Disconnected")

    async def _send_msg(self, msg_type: int, data: bytes = b"", flags: int = FLAG_NONE):
        """Send a protocol message."""
        if not self.writer:
            return False
        try:
            header = struct.pack("<BBH", msg_type, flags, len(data))
            self.writer.write(header + data)
            await self.writer.drain()
            return True
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False

    async def start_call(self):
        """Initiate outgoing call to ESP32."""
        if not self._connected:
            if not await self.connect():
                return "error"

        # Start audio bridge
        await self.bridge.start_mic()
        await self.bridge.start_speaker()

        # Send START with our name as payload
        payload = self.name.encode("utf-8")
        result = await self._send_msg(MSG_START, data=payload)
        if not result:
            return "error"

        # Wait for response (PONG=auto-answer, RING=need manual answer)
        for _ in range(50):  # 500ms
            await asyncio.sleep(0.01)
            if self._streaming:
                break
            if self._ringing:
                break

        self._call_active = True
        self._mic_task = asyncio.create_task(self._mic_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

        state = "streaming" if self._streaming else ("ringing" if self._ringing else "streaming")
        logger.info(f"Call started: {state}")
        return state

    async def answer_call(self):
        """Answer incoming ringing call."""
        if not self._ringing:
            return False
        await self._send_msg(MSG_ANSWER)
        return True

    async def hangup(self):
        """End active call."""
        if self._call_active:
            await self._send_msg(MSG_STOP)
        await self.disconnect()

    async def _auto_answer(self):
        """Auto-answer after ringing for a few seconds."""
        await asyncio.sleep(2)
        if self._ringing and self._connected:
            logger.info("Auto-answering...")
            await self._send_msg(MSG_PONG)  # PONG = accept in some versions
            await self._send_msg(MSG_ANSWER)
            self._streaming = True
            self._ringing = False

    async def _mic_loop(self):
        """Read mic chunks and send to ESP32."""
        logger.info("Mic loop started")
        buf = b""
        try:
            while self._connected and self._call_active:
                if not self._streaming:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    data = await asyncio.wait_for(self.bridge.mic_stdout.read(AUDIO_CHUNK * 2), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    continue
                buf += data
                # Send complete chunks only
                while len(buf) >= AUDIO_CHUNK:
                    chunk = buf[:AUDIO_CHUNK]
                    buf = buf[AUDIO_CHUNK:]
                    await self._send_msg(MSG_AUDIO, data=chunk)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Mic loop error: {e}")

    async def _recv_loop(self):
        """Receive messages from ESP32."""
        logger.info("Receive loop started")
        try:
            while self._connected and self.reader:
                header_data = await asyncio.wait_for(
                    self.reader.readexactly(HEADER_SIZE), timeout=60.0
                )
                msg_type, flags, length = struct.unpack("<BBH", header_data)

                payload = b""
                if length > 0:
                    if length > MAX_PAYLOAD:
                        logger.error(f"Bad length {length}, disconnecting")
                        break
                    payload = await asyncio.wait_for(
                        self.reader.readexactly(length), timeout=5.0
                    )

                await self._handle_msg(msg_type, flags, payload)

        except asyncio.TimeoutError:
            logger.warning("Receive timeout")
        except asyncio.IncompleteReadError:
            logger.info("Connection closed by ESP32")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Receive error: {e}")
        finally:
            self._call_active = False
            self._streaming = False

    async def _handle_msg(self, msg_type: int, flags: int, payload: bytes):
        if msg_type == MSG_AUDIO:
            # Play audio through speaker
            self.bridge.write_speaker(payload)
            self._drain_counter += 1
            if self._drain_counter % 10 == 0:
                await self.bridge.drain_speaker()

        elif msg_type == MSG_PONG:
            # ACK for START (auto-answer) or ANSWER
            if not self._streaming:
                self._streaming = True
                self._ringing = False
                logger.info("PONG → streaming")

        elif msg_type == MSG_RING:
            self._ringing = True
            logger.info("RING → ringing, auto-answering in 3s...")
            # Auto-answer after brief delay to allow ringtone to play
            asyncio.ensure_future(self._auto_answer())

        elif msg_type == MSG_ANSWER:
            self._streaming = True
            self._ringing = False
            logger.info("ANSWER → streaming")

        elif msg_type == MSG_STOP:
            logger.info("STOP received")
            self._call_active = False
            self._streaming = False

        elif msg_type == MSG_PING:
            await self._send_msg(MSG_PONG)

        elif msg_type == MSG_ERROR:
            code = payload[0] if payload else 0
            logger.error(f"ERROR from ESP: code={code}")
            self._call_active = False


async def idle_loop(peer: IntercomPeer):
    """Main idle loop — wait for incoming calls from ESP32."""
    logger.info(f"Listening for incoming calls from {peer.host}:{peer.port}...")

    while True:
        try:
            # Connect and wait for START from ESP32
            if not peer._connected:
                if not await peer.connect():
                    logger.info("Retrying connection in 5s...")
                    await asyncio.sleep(5)
                    continue

            # Read messages — wait for START from ESP32 (incoming call)
            header_data = await asyncio.wait_for(
                peer.reader.readexactly(HEADER_SIZE), timeout=60.0
            )
            msg_type, flags, length = struct.unpack("<BBH", header_data)

            payload = b""
            if length > 0:
                if length > MAX_PAYLOAD:
                    logger.error(f"Bad length {length}")
                    await peer.disconnect()
                    continue
                payload = await asyncio.wait_for(
                    peer.reader.readexactly(length), timeout=5.0
                )

            if msg_type == MSG_START:
                # Incoming call!
                caller = payload.decode("utf-8", errors="replace") if payload else "Unknown"
                logger.info(f"Incoming call from: {caller}")

                # Start audio bridge
                await peer.bridge.start_mic()
                await peer.bridge.start_speaker()

                # Auto-answer: send PONG to accept
                await peer._send_msg(MSG_PONG)
                peer._streaming = True
                peer._call_active = True

                # Start mic and recv loops
                peer._mic_task = asyncio.create_task(peer._mic_loop())
                peer._recv_task = asyncio.create_task(peer._recv_loop())

                # Wait until call ends
                while peer._call_active:
                    await asyncio.sleep(0.1)

                logger.info("Call ended, cleaning up...")
                await peer.bridge.stop()
                if peer._mic_task:
                    peer._mic_task.cancel()
                if peer._recv_task:
                    peer._recv_task.cancel()
                peer._mic_task = peer._recv_task = None

                # Disconnect, will reconnect on next iteration
                await peer.disconnect()
                await asyncio.sleep(1)

            elif msg_type == MSG_PING:
                await peer._send_msg(MSG_PONG)

            elif msg_type == MSG_STOP:
                logger.info("STOP while idle")
                await peer.disconnect()

        except asyncio.TimeoutError:
            # Normal — no incoming call
            continue
        except asyncio.IncompleteReadError:
            logger.info("Disconnected, reconnecting...")
            await peer.disconnect()
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Idle loop error: {e}")
            await peer.disconnect()
            await asyncio.sleep(3)


async def interactive_call(peer: IntercomPeer):
    """Make an outgoing call interactively."""
    result = await peer.start_call()
    logger.info(f"Call result: {result}")

    if result in ("error",):
        return

    print(f"\nCall active ({result}). Press Ctrl+C to hangup.\n")
    try:
        while peer._call_active:
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        await peer.hangup()
        print("Call ended.")


def main():
    parser = argparse.ArgumentParser(description="Khadas Intercom Peer")
    parser.add_argument("--host", default="REDACTED_HOST", help="ESP32 IP address")
    parser.add_argument("--port", type=int, default=6054, help="TCP port")
    parser.add_argument("--mic-device", default="plughw:CARD=UACDemoV10,DEV=0",
                        help="ALSA mic device")
    parser.add_argument("--snd-device", default="plughw:CARD=UACDemoV10,DEV=0",
                        help="ALSA speaker device")
    parser.add_argument("--snd-rate", type=int, default=16000,
                        help="Speaker sample rate (Hz)")
    parser.add_argument("--name", default="Khadas", help="Our name for caller ID")
    parser.add_argument("--call", action="store_true", help="Make outgoing call")
    parser.add_argument("--listen", action="store_true", default=True,
                        help="Listen for incoming calls (default)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    bridge = AudioBridge(args.mic_device, args.snd_device, args.snd_rate)
    peer = IntercomPeer(args.host, args.port, bridge, args.name)

    loop = asyncio.new_event_loop()

    def _signal_handler():
        logger.info("Signal received, shutting down...")
        asyncio.ensure_future(peer.hangup())
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    if args.call:
        loop.run_until_complete(interactive_call(peer))
    else:
        loop.run_until_complete(idle_loop(peer))


if __name__ == "__main__":
    main()
