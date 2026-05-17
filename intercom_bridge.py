#!/usr/bin/env python3
"""Intercom Bridge v2 with debug counters."""

import asyncio
import base64
import json
import logging
import os
import signal
import ssl
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_LOGGER = logging.getLogger(__name__)
logging.getLogger("websockets").setLevel(logging.WARNING)

HA_WS_URL = os.environ.get("HA_WS_URL", "wss://REDACTED/api/websocket")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
DEVICE_ID = os.environ.get("INTERCOM_DEVICE_ID", "REDACTED_DEVICE_ID")
DEVICE_HOST = os.environ.get("INTERCOM_HOST", "REDACTED_HOST")

MIC_CMD = "arecord -D plughw:CARD=UACDemoV10,DEV=0 -f S16_LE -r 16000 -c 1 -t raw"
PLAY_CMD = "aplay -D plughw:CARD=UACDemoV10,DEV=0 -f S16_LE -r 16000 -c 1 -t raw"
AUDIO_CHUNK_SIZE = 3200  # 100ms at 16kHz S16_LE mono


class IntercomBridge:
    def __init__(self):
        self._ws = None
        self._msg_id = 0
        self._audio_sub_id = None
        self._connected = False
        self._streaming = False
        self._player_proc = None
        self._recorder_proc = None
        self._mic_task = None
        self._device_id = DEVICE_ID
        self._host = DEVICE_HOST
        self._mic_frames_sent = 0
        self._audio_frames_recv = 0
        self._last_status = 0

    def _next_id(self):
        self._msg_id += 1
        return self._msg_id

    async def _send(self, msg):
        if self._ws:
            await self._ws.send(json.dumps(msg))

    async def _log_status(self):
        """Periodic status logger."""
        while self._streaming:
            await asyncio.sleep(3)
            _LOGGER.info(
                "STATUS: mic_sent=%d audio_recv=%d streaming=%.0fs",
                self._mic_frames_sent, self._audio_frames_recv,
                time.time() - self._stream_start if hasattr(self, '_stream_start') else 0
            )

    async def connect(self):
        import websockets
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        _LOGGER.info("Connecting to %s", HA_WS_URL)
        self._ws = await websockets.connect(
            HA_WS_URL, ssl=ssl_ctx, ping_interval=20, ping_timeout=30, max_size=2**23
        )

        msg = json.loads(await self._ws.recv())
        _LOGGER.info("HA auth_required (v%s)", msg.get("ha_version"))

        await self._send({"type": "auth", "access_token": HA_TOKEN})
        msg = json.loads(await self._ws.recv())
        if msg.get("type") != "auth_ok":
            raise Exception(f"Auth failed: {msg}")
        _LOGGER.info("Authenticated")

        sid = self._next_id()
        await self._send({"id": sid, "type": "subscribe_events", "event_type": "intercom_state"})

        self._audio_sub_id = self._next_id()
        await self._send({
            "id": self._audio_sub_id,
            "type": "intercom_native/subscribe_audio",
            "device_id": self._device_id,
        })
        _LOGGER.info("Subscribed to state+audio (sub_id=%d)", self._audio_sub_id)
        self._connected = True

    async def start_call(self):
        sid = self._next_id()
        await self._send({
            "id": sid, "type": "intercom_native/start",
            "device_id": self._device_id, "host": self._host,
        })
        # Wait for result
        deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("id") == sid and msg.get("type") == "result":
                state = msg.get("result", {}).get("state", "")
                _LOGGER.info("Start result: state=%s", state)
                if state == "streaming":
                    await self._start_audio()
                elif state == "ringing":
                    _LOGGER.info("Ringing, waiting...")
                return
            await self._handle_message(msg)

    async def hangup(self):
        sid = self._next_id()
        await self._send({"id": sid, "type": "intercom_native/stop", "device_id": self._device_id})
        await self._stop_audio()

    async def _start_audio(self):
        if self._streaming:
            return
        self._streaming = True
        self._stream_start = time.time()
        self._mic_frames_sent = 0
        self._audio_frames_recv = 0
        _LOGGER.info("Starting audio pipeline...")

        self._player_proc = await asyncio.create_subprocess_exec(
            *PLAY_CMD.split(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        _LOGGER.info("Player PID=%d", self._player_proc.pid)

        self._recorder_proc = await asyncio.create_subprocess_exec(
            *MIC_CMD.split(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _LOGGER.info("Recorder PID=%d", self._recorder_proc.pid)
        self._mic_task = asyncio.create_task(self._mic_reader())
        asyncio.create_task(self._log_status())

    async def _stop_audio(self):
        self._streaming = False
        if self._mic_task:
            self._mic_task.cancel()
            try: await self._mic_task
            except asyncio.CancelledError: pass
            self._mic_task = None
        for attr in ("_recorder_proc", "_player_proc"):
            proc = getattr(self, attr)
            if proc and proc.returncode is None:
                try:
                    if attr == "_player_proc": proc.stdin.close()
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except Exception: pass
                setattr(self, attr, None)
        _LOGGER.info("Audio stopped (mic_sent=%d, audio_recv=%d, duration=%.1fs)",
                     self._mic_frames_sent, self._audio_frames_recv,
                     time.time() - self._stream_start if hasattr(self, '_stream_start') else 0)

    async def _mic_reader(self):
        _LOGGER.info("Mic reader started")
        try:
            while self._streaming and self._recorder_proc:
                data = await self._recorder_proc.stdout.read(AUDIO_CHUNK_SIZE)
                if not data:
                    _LOGGER.warning("Mic EOF")
                    break
                audio_b64 = base64.b64encode(data).decode("ascii")
                await self._send({
                    "type": "intercom_native/audio",
                    "device_id": self._device_id,
                    "audio": audio_b64,
                })
                self._mic_frames_sent += 1
        except asyncio.CancelledError: pass
        except Exception as e:
            _LOGGER.error("Mic error: %s", e)
        finally:
            _LOGGER.info("Mic reader ended (sent=%d frames)", self._mic_frames_sent)

    def _play_audio(self, audio_b64):
        if not self._player_proc or self._player_proc.returncode is not None:
            return
        try:
            self._player_proc.stdin.write(base64.b64decode(audio_b64))
            self._audio_frames_recv += 1
        except (BrokenPipeError, OSError) as e:
            _LOGGER.warning("Player write: %s", e)

    async def _handle_message(self, msg):
        msg_type = msg.get("type", "")
        if msg_type == "pong":
            return
        if msg_type == "event":
            event = msg.get("event", {})
            et = event.get("event_type", "")
            if et == "intercom_state":
                data = event.get("data", {})
                state = data.get("state", "")
                dev = data.get("device_id", "")
                _LOGGER.info("State: %s (dev=%s)", state, dev)
                if dev != self._device_id:
                    return
                if state == "streaming":
                    await self._start_audio()
                elif state == "ringing":
                    _LOGGER.info("Incoming call, auto-answering...")
                    aid = self._next_id()
                    await self._send({"id": aid, "type": "intercom_native/answer", "device_id": self._device_id})
                elif state in ("idle", "disconnected"):
                    await self._stop_audio()

    async def listen_loop(self):
        import websockets
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "event" and msg.get("id") == self._audio_sub_id:
                    audio_b64 = msg.get("event", {}).get("audio", "")
                    if audio_b64 and self._streaming:
                        self._play_audio(audio_b64)
                    continue
                await self._handle_message(msg)
        except websockets.exceptions.ConnectionClosed as e:
            _LOGGER.error("WS closed: %s", e)
        except Exception as e:
            _LOGGER.error("Listen error: %s", e)
        self._connected = False

    async def run(self):
        while True:
            try:
                await self.connect()
                _LOGGER.info("Connected! Starting call...")
                await self.start_call()
                await self.listen_loop()
            except Exception as e:
                _LOGGER.error("Error: %s", e)
            await self._stop_audio()
            self._connected = False
            _LOGGER.info("Reconnecting in 5s...")
            await asyncio.sleep(5)


async def main():
    global HA_TOKEN
    if not HA_TOKEN:
        for p in ["/opt/ha_token.txt", "/tmp/ha_token.txt"]:
            if Path(p).exists():
                HA_TOKEN = Path(p).read_text().strip()
                _LOGGER.info("Token from %s (%d chars)", p, len(HA_TOKEN))
                break
    if not HA_TOKEN:
        _LOGGER.error("HA_TOKEN not set!")
        sys.exit(1)

    bridge = IntercomBridge()
    loop = asyncio.get_event_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(bridge.run())
    await stop.wait()
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

if __name__ == "__main__":
    asyncio.run(main())
