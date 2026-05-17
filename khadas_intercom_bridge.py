#!/usr/bin/env python3
"""Khadas Intercom Bridge - connects to ESP32 intercom via TCP.

Audio: 16kHz mono int16 (matches ESP32 intercom protocol)
Mic: arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1
Speaker: aplay -D plughw:1,0 -f S16_LE -r 16000 -c 1
"""

import socket, struct, subprocess, threading, sys, signal, time

ESP32_IP = 'REDACTED_HOST'
ESP32_PORT = 6054

MSG_AUDIO = 0x01
MSG_START = 0x02
MSG_STOP = 0x03
MSG_PING = 0x04
MSG_PONG = 0x05
MSG_ERROR = 0x06
MSG_RING = 0x07
MSG_ANSWER = 0x08
FLAG_NO_RING = 0x02
HEADER_SIZE = 4
CHUNK_SIZE = 1024  # 512 samples * 2 bytes
AUDIO_DEVICE = 'plughw:1,0'
SAMPLE_RATE = 16000

def make_header(msg_type, flags=0, length=0):
    return struct.pack('<BBH', msg_type, flags, length)

def parse_header(data):
    if len(data) < HEADER_SIZE:
        return None, None, None
    return struct.unpack('<BBH', data)

class IntercomBridge:
    def __init__(self):
        self.sock = None
        self.running = False
        self.mic_proc = None
        self.spk_proc = None
        self.stats = {'audio_sent': 0, 'audio_recv': 0, 'start_time': 0}

    def connect(self):
        print(f'Connecting to {ESP32_IP}:{ESP32_PORT}...')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((ESP32_IP, ESP32_PORT))
        print('TCP connected')

    def start_call(self):
        # Send START with NO_RING (auto_answer mode)
        self.sock.sendall(make_header(MSG_START, FLAG_NO_RING))
        print('START sent, waiting for PONG...')
        
        # Wait for PONG
        self.sock.settimeout(10)
        while True:
            header = self.sock.recv(HEADER_SIZE)
            if len(header) < HEADER_SIZE:
                raise ConnectionError('Connection lost')
            msg_type, flags, length = parse_header(header)
            if length > 0:
                data = self.sock.recv(length)
            else:
                data = b''
            
            if msg_type == MSG_PONG:
                print(f'PONG received! Streaming active.')
                return True
            elif msg_type == MSG_RING:
                print('RING received (waiting for answer)...')
            elif msg_type == MSG_AUDIO:
                # Might get audio before PONG in some cases
                pass
            else:
                print(f'Unexpected msg: type={msg_type:#x} flags={flags} len={length}')

    def mic_to_esp(self):
        """Read from mic, send to ESP32."""
        print('Mic thread started')
        try:
            self.mic_proc = subprocess.Popen(
                ['arecord', '-D', AUDIO_DEVICE, '-f', 'S16_LE', '-r', str(SAMPLE_RATE),
                 '-c', '1', '-t', 'raw', '--buffer-size=2048'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            while self.running:
                chunk = self.mic_proc.stdout.read(CHUNK_SIZE)
                if not chunk or len(chunk) == 0:
                    break
                try:
                    header = make_header(MSG_AUDIO, 0, len(chunk))
                    self.sock.sendall(header + chunk)
                    self.stats['audio_sent'] += 1
                except (BrokenPipeError, ConnectionError):
                    break
        except Exception as e:
            print(f'Mic error: {e}')
        finally:
            print('Mic thread stopped')

    def esp_to_speaker(self):
        """Read from ESP32, play to speaker."""
        print('Speaker thread started')
        self.sock.settimeout(1)
        try:
            self.spk_proc = subprocess.Popen(
                ['aplay', '-D', AUDIO_DEVICE, '-f', 'S16_LE', '-r', str(SAMPLE_RATE),
                 '-c', '1', '--buffer-size=2048'],
                stdin=subprocess.PIPE, stderr=subprocess.PIPE
            )
            while self.running:
                try:
                    header = self.sock.recv(HEADER_SIZE)
                except socket.timeout:
                    continue
                if len(header) < HEADER_SIZE:
                    break
                msg_type, flags, length = parse_header(header)
                data = b''
                if length > 0:
                    while len(data) < length:
                        chunk = self.sock.recv(length - len(data))
                        if not chunk:
                            break
                        data += chunk
                
                if msg_type == MSG_AUDIO:
                    self.spk_proc.stdin.write(data)
                    self.spk_proc.stdin.flush()
                    self.stats['audio_recv'] += 1
                elif msg_type == MSG_STOP:
                    print('STOP received from ESP32')
                    self.running = False
                    break
                elif msg_type == MSG_PING:
                    # Respond with PONG
                    self.sock.sendall(make_header(MSG_PONG))
                elif msg_type == MSG_ERROR:
                    print(f'ERROR from ESP32: {data}')
                    self.running = False
                    break
        except Exception as e:
            print(f'Speaker error: {e}')
        finally:
            print('Speaker thread stopped')

    def run(self, duration=30):
        """Run bridge for given duration (seconds)."""
        self.running = True
        self.stats['start_time'] = time.time()
        
        try:
            self.connect()
            self.start_call()
            
            # Start audio threads
            mic_thread = threading.Thread(target=self.mic_to_esp, daemon=True)
            spk_thread = threading.Thread(target=self.esp_to_speaker, daemon=True)
            mic_thread.start()
            spk_thread.start()
            
            # Wait for duration or until stopped
            print(f'Bridge running for {duration}s. Press Ctrl+C to stop.')
            while self.running and (time.time() - self.stats['start_time']) < duration:
                time.sleep(0.5)
                elapsed = time.time() - self.stats['start_time']
                if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                    print(f'  [{elapsed:.0f}s] sent={self.stats["audio_sent"]} recv={self.stats["audio_recv"]}')
                    time.sleep(1)  # Avoid printing every 0.5s
            
        except KeyboardInterrupt:
            print('\nInterrupted')
        except Exception as e:
            print(f'Error: {e}')
        finally:
            self.running = False
            # Send STOP
            try:
                self.sock.sendall(make_header(MSG_STOP))
                print('STOP sent')
            except:
                pass
            # Cleanup
            for p in (self.mic_proc, self.spk_proc):
                if p:
                    p.terminate()
                    p.wait(timeout=2)
            if self.sock:
                self.sock.close()
            elapsed = time.time() - self.stats['start_time']
            print(f'Bridge stopped. Duration: {elapsed:.1f}s, sent={self.stats["audio_sent"]}, recv={self.stats["audio_recv"]}')

if __name__ == '__main__':
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    bridge = IntercomBridge()
    bridge.run(duration)
