#!/usr/bin/env python3
"""
Wake word listener v3 - simplified, faster response.
Continuously buffers last 3s of audio, checks wake word when VAD detects speech.
"""
import numpy as np
import onnxruntime as ort
import pyaudio
import time
import logging
import sys
import os
import signal
from collections import deque

ort.set_default_logger_severity(3)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("slushay")
logging.getLogger("pyaudio").setLevel(logging.WARNING)

CAPTURE_RATE = 48000
RATE = 16000
CAPTURE_CHUNK = 1440
PROCESS_CHUNK = 480
RESAMPLE = 3

MODEL_PATH = "/opt/voice-client/models/slushay.onnx"
DETECT_THRESHOLD = 0.15
COOLDOWN = 2.0
VAD_THRESHOLD = 0.2
CHECK_INTERVAL = 3

class Listener:
    def __init__(self):
        from openwakeword import VAD
        from openwakeword.utils import AudioFeatures
        self.vad = VAD()
        self.ww = ort.InferenceSession(MODEL_PATH, providers=['CPUExecutionProvider'])
        self.ww_input = self.ww.get_inputs()[0].name
        self.af = AudioFeatures()
        self.buffer = np.zeros(48000, dtype=np.int16)
        self.buf_pos = 0
        self.buf_full = False
        self.last_detection = 0
        self.running = True
        signal.signal(signal.SIGINT, lambda s,f: self._stop())
        signal.signal(signal.SIGTERM, lambda s,f: self._stop())

    def _stop(self):
        self.running = False

    def _add_to_buffer(self, chunk_16k):
        n = len(chunk_16k)
        if self.buf_pos + n <= 48000:
            self.buffer[self.buf_pos:self.buf_pos + n] = chunk_16k
            self.buf_pos += n
        else:
            first = 48000 - self.buf_pos
            self.buffer[self.buf_pos:] = chunk_16k[:first]
            self.buffer[:n - first] = chunk_16k[first:]
            self.buf_pos = n - first
        if self.buf_pos >= 48000:
            self.buf_full = True

    def _get_buffer(self):
        if self.buf_full:
            return np.concatenate([self.buffer[self.buf_pos:], self.buffer[:self.buf_pos]])
        return self.buffer[:self.buf_pos].copy()

    def _detect(self, audio):
        if len(audio) < 48000:
            audio = np.pad(audio, (48000 - len(audio), 0))
        try:
            feat = self.af.embed_clips(audio[-48000:].reshape(1, -1))
            result = self.ww.run(None, {self.ww_input: feat.astype(np.float32)})
            return float(result[0][0][0])
        except Exception as e:
            log.error(f"Detect error: {e}")
            return 0.0

    def start(self):
        pa = pyaudio.PyAudio()
        mic_idx = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get('maxInputChannels', 0) > 0:
                mic_idx = i
                log.info(f"Mic: {info['name']} (idx={i})")
                break
        if mic_idx is None:
            log.error("No mic!")
            return

        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=CAPTURE_RATE,
                        input=True, input_device_index=mic_idx,
                        frames_per_buffer=CAPTURE_CHUNK)
        log.info(f"Listening... threshold={DETECT_THRESHOLD}")

        chunk_count = 0
        speech_chunks = 0

        try:
            while self.running:
                try:
                    data = stream.read(CAPTURE_CHUNK, exception_on_overflow=False)
                except OSError:
                    continue

                chunk_48k = np.frombuffer(data, dtype=np.int16)
                chunk_16k = chunk_48k[::RESAMPLE]
                self._add_to_buffer(chunk_16k)
                chunk_count += 1

                vad_score = self.vad.predict(chunk_16k)
                is_speech = vad_score > VAD_THRESHOLD

                if is_speech:
                    speech_chunks += 1
                    if speech_chunks % CHECK_INTERVAL == 0 and self.buf_pos >= 4800:
                        audio = self._get_buffer()
                        score = self._detect(audio)
                        now = time.time()
                        log.info(f"score={score:.4f} buf={len(audio)} speech={speech_chunks}")
                        if score > DETECT_THRESHOLD and (now - self.last_detection) > COOLDOWN:
                            log.info(f"*** WAKE WORD! score={score:.4f} ***")
                            self.last_detection = now
                            self._on_detected()
                            speech_chunks = 0
                else:
                    if speech_chunks >= 2:
                        audio = self._get_buffer()
                        score = self._detect(audio)
                        now = time.time()
                        log.info(f"end-score={score:.4f} buf={len(audio)} speech={speech_chunks}")
                        if score > DETECT_THRESHOLD and (now - self.last_detection) > COOLDOWN:
                            log.info(f"*** WAKE WORD (end)! score={score:.4f} ***")
                            self.last_detection = now
                            self._on_detected()
                    speech_chunks = 0

        except Exception as e:
            log.error(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            stream.close()
            pa.terminate()
            log.info("Stopped.")

    def _on_detected(self):
        log.info("Action triggered!")
        os.system("aplay -D plughw:0,0 /opt/voice-client/beep.wav 2>/dev/null &")

if __name__ == "__main__":
    Listener().start()
