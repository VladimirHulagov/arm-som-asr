#!/usr/bin/env python3
"""
Wake word listener v4 - FIX: rotate buffer to put speech at start.
The model was trained with the word at the beginning of 3s clips,
so we need to feed it audio where speech is at position 0.
"""
import numpy as np
import onnxruntime as ort
import pyaudio
import time
import logging
import sys
import os
import signal

ort.set_default_logger_severity(3)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("slushay")
logging.getLogger("pyaudio").setLevel(logging.WARNING)

CAPTURE_RATE = 48000
RATE = 16000
CAPTURE_CHUNK = 1440
RESAMPLE = 3
BUFFER_SIZE = 48000  # 3s at 16kHz

MODEL_PATH = "/opt/voice-client/models/slushay.onnx"
DETECT_THRESHOLD = 0.5
COOLDOWN = 2.0
VAD_THRESHOLD = 0.2

class Listener:
    def __init__(self):
        from openwakeword import VAD
        from openwakeword.utils import AudioFeatures
        self.vad = VAD()
        self.ww = ort.InferenceSession(MODEL_PATH, providers=['CPUExecutionProvider'])
        self.ww_input = self.ww.get_inputs()[0].name
        self.af = AudioFeatures()
        # Simple rolling buffer
        self.buffer = np.zeros(BUFFER_SIZE, dtype=np.int16)
        self.buf_idx = 0
        self.last_detection = 0
        self.running = True
        signal.signal(signal.SIGINT, lambda s, f: self._stop())
        signal.signal(signal.SIGTERM, lambda s, f: self._stop())

    def _stop(self):
        self.running = False

    def _push(self, chunk_16k):
        """Push chunk into circular buffer."""
        n = len(chunk_16k)
        for i in range(n):
            self.buffer[self.buf_idx] = chunk_16k[i]
            self.buf_idx = (self.buf_idx + 1) % BUFFER_SIZE

    def _get_ordered(self):
        """Get buffer in chronological order (oldest first)."""
        return np.concatenate([self.buffer[self.buf_idx:], self.buffer[:self.buf_idx]])

    def _detect(self, audio_3s):
        """Run wake word detection. Speech should be at START for best results."""
        try:
            feat = self.af.embed_clips(audio_3s.reshape(1, -1))
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

        speech_chunks = 0
        check_every = 5  # check every 5 speech chunks (~150ms)
        min_speech = 10  # need at least 10 chunks of speech (~300ms)

        try:
            while self.running:
                try:
                    data = stream.read(CAPTURE_CHUNK, exception_on_overflow=False)
                except OSError:
                    continue

                chunk_48k = np.frombuffer(data, dtype=np.int16)
                chunk_16k = chunk_48k[::RESAMPLE]
                self._push(chunk_16k)

                vad_score = self.vad.predict(chunk_16k)
                is_speech = vad_score > VAD_THRESHOLD

                if is_speech:
                    speech_chunks += 1

                    # Check wake word periodically once we have enough speech
                    if speech_chunks >= min_speech and speech_chunks % check_every == 0:
                        # Get the buffer - speech is at the END
                        audio = self._get_ordered()

                        # The model expects speech at the START.
                        # Find the speech start in the buffer and rotate.
                        # Simple approach: find first high-energy frame and use from there
                        window = 1600  # 100ms
                        rms_values = []
                        for i in range(0, len(audio) - window, window):
                            rms = np.sqrt(np.mean(audio[i:i+window].astype(float)**2))
                            rms_values.append((i, rms))

                        # Find speech start (first frame with RMS > 500)
                        speech_start = 0
                        for pos, rms in rms_values:
                            if rms > 500:
                                speech_start = pos
                                break

                        # Rotate: put speech at the beginning
                        rotated = np.concatenate([audio[speech_start:], audio[:speech_start]])
                        # Pad/truncate to 3s
                        if len(rotated) >= BUFFER_SIZE:
                            rotated = rotated[:BUFFER_SIZE]
                        else:
                            rotated = np.pad(rotated, (0, BUFFER_SIZE - len(rotated)))

                        score = self._detect(rotated)
                        now = time.time()

                        log.info(f"score={score:.4f} speech_start={speech_start/RATE:.2f}s chunks={speech_chunks}")

                        if score > DETECT_THRESHOLD and (now - self.last_detection) > COOLDOWN:
                            log.info(f"*** WAKE WORD! score={score:.4f} ***")
                            self.last_detection = now
                            self._on_detected()
                            speech_chunks = 0
                else:
                    # Final check when speech ends
                    if speech_chunks >= min_speech:
                        audio = self._get_ordered()
                        window = 1600
                        speech_start = 0
                        for i in range(0, len(audio) - window, window):
                            rms = np.sqrt(np.mean(audio[i:i+window].astype(float)**2))
                            if rms > 500:
                                speech_start = i
                                break
                        rotated = np.concatenate([audio[speech_start:], audio[:speech_start]])
                        if len(rotated) >= BUFFER_SIZE:
                            rotated = rotated[:BUFFER_SIZE]
                        else:
                            rotated = np.pad(rotated, (0, BUFFER_SIZE - len(rotated)))

                        score = self._detect(rotated)
                        now = time.time()
                        log.info(f"end-score={score:.4f} chunks={speech_chunks}")

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
