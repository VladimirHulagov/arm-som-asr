#!/usr/bin/env python3
"""
Wake word listener for "Слушай" on Khadas VIM2.
Uses openwakeword built-in Silero VAD (ONNX, no torch needed)
and custom trained ONNX model for "Слушай" detection.
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("slushay-listener")
# Mute overly noisy loggers
logging.getLogger("pyaudio").setLevel(logging.WARNING)

# Audio config
# USB mic only supports 48kHz, so we capture at 48k and resample to 16k
CAPTURE_RATE = 48000
RATE = 16000       # internal processing rate
CHANNELS = 1
FORMAT = pyaudio.paInt16
CAPTURE_CHUNK = 1440  # 30ms at 48kHz (480 * 3)
PROCESS_CHUNK = 480   # 30ms at 16kHz (Silero VAD optimal)

# Paths
MODELS_DIR = "/opt/voice-client/models"
SLUSHAY_MODEL = os.path.join(MODELS_DIR, "slushay.onnx")

# VAD config
VAD_THRESHOLD = 0.3

# Detection config
DETECTION_THRESHOLD = 0.10  # optimized from training
COOLDOWN = 3.0              # seconds between detections
MIN_SPEECH_FOR_DETECT = 12800  # 0.8 seconds of speech before checking


class WakeWordListener:
    def __init__(self):
        log.info("Loading models...")

        # VAD - built into openwakeword (Silero VAD as ONNX)
        from openwakeword import VAD
        self.vad = VAD()
        log.info("VAD loaded (Silero ONNX)")

        # Wake word model
        self.ww_session = ort.InferenceSession(
            SLUSHAY_MODEL,
            providers=['CPUExecutionProvider']
        )
        # Get expected input shape
        inp = self.ww_session.get_inputs()[0]
        self.ww_input_name = inp.name
        log.info(f"Wake word model loaded: {SLUSHAY_MODEL}")
        log.info(f"  Input: {inp.name} shape={inp.shape} type={inp.type}")

        # AudioFeatures from openwakeword for mel spectrogram
        from openwakeword.utils import AudioFeatures
        self.af = AudioFeatures()
        log.info("AudioFeatures initialized")

        # State
        self.speech_buffer = np.array([], dtype=np.int16)
        self.silence_frames = 0
        self.last_detection = 0
        self.speech_start_time = None
        self.running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        log.info(f"Signal {sig} received, stopping...")
        self.running = False

    def _get_mic_index(self, pa):
        """Find the Jieli USB mic device index."""
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get('maxInputChannels', 0) > 0:
                log.info(f"Using mic: {info['name']} (index {i}, "
                         f"channels={info['maxInputChannels']}, "
                         f"rate={info['defaultSampleRate']})")
                return i
        return None

    def detect_wake_word(self, audio_int16):
        """Run wake word detection on audio buffer."""
        if len(audio_int16) < MIN_SPEECH_FOR_DETECT:
            return 0.0

        # Take last 3 seconds, pad if needed
        if len(audio_int16) >= 48000:
            clip = audio_int16[-48000:]
        else:
            # Pad with zeros on the left to make 3s
            clip = np.pad(audio_int16, (48000 - len(audio_int16), 0))

        try:
            # Extract mel features via openwakeword
            features = self.af.embed_clips(clip.reshape(1, -1))
            # features shape: (1, 28, 96) — pass directly to model
            result = self.ww_session.run(
                None,
                {self.ww_input_name: features.astype(np.float32)}
            )
            return float(result[0][0][0])
        except Exception as e:
            log.error(f"Detection error: {e}")
            import traceback
            traceback.print_exc()
            return 0.0

    def start(self):
        """Start listening for wake word."""
        pa = pyaudio.PyAudio()
        mic_idx = self._get_mic_index(pa)

        if mic_idx is None:
            log.error("No microphone found!")
            pa.terminate()
            return

        log.info("Opening audio stream...")
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=CAPTURE_RATE,
            input=True,
            input_device_index=mic_idx,
            frames_per_buffer=CAPTURE_CHUNK,
        )

        log.info("Listening... Say 'Слушай!'")
        log.info(f"Detection threshold: {DETECTION_THRESHOLD}")
        log.info(f"Capture: {CAPTURE_RATE}Hz, Process: {RATE}Hz")

        SILENCE_LIMIT_FRAMES = 50  # ~1.5 sec of silence (50 x 30ms)
        detect_counter = 0

        # Simple resampling: 48k -> 16k = take every 3rd sample
        RESAMPLE_RATIO = CAPTURE_RATE // RATE

        try:
            while self.running:
                # Read audio chunk at 48kHz
                try:
                    data = stream.read(CAPTURE_CHUNK, exception_on_overflow=False)
                except OSError as e:
                    log.warning(f"Audio read error: {e}")
                    continue

                chunk_48k = np.frombuffer(data, dtype=np.int16)
                # Downsample 48k -> 16k by taking every 3rd sample
                chunk_int16 = chunk_48k[::RESAMPLE_RATIO]

                # VAD: predict speech probability
                vad_score = self.vad.predict(chunk_int16)
                is_speech = vad_score > VAD_THRESHOLD

                if is_speech:
                    if len(self.speech_buffer) == 0:
                        self.speech_start_time = time.time()
                        log.info("Speech started")
                    self.speech_buffer = np.concatenate(
                        [self.speech_buffer, chunk_int16]
                    )
                    self.silence_frames = 0

                    # Check for wake word once we have enough speech
                    if len(self.speech_buffer) >= MIN_SPEECH_FOR_DETECT:
                        detect_counter += 1
                        # Only check every ~0.5s to reduce CPU load
                        if detect_counter % 5 == 0:
                            score = self.detect_wake_word(self.speech_buffer)
                            now = time.time()
                            log.info(f"check: score={score:.4f} buf={len(self.speech_buffer)/RATE:.1f}s")

                            if (score > DETECTION_THRESHOLD and
                                    (now - self.last_detection) > COOLDOWN):
                                latency = time.time() - self.speech_start_time if self.speech_start_time else 0
                                log.info(f"*** WAKE WORD DETECTED! "
                                         f"score={score:.4f} latency={latency:.2f}s ***")
                                self.last_detection = now
                                self._on_detected()
                                self.speech_buffer = np.array([], dtype=np.int16)
                else:
                    self.silence_frames += 1
                    if self.silence_frames > SILENCE_LIMIT_FRAMES:
                        if len(self.speech_buffer) > 0:
                            log.debug(f"Reset after silence "
                                      f"({len(self.speech_buffer)/RATE:.1f}s)")
                        self.speech_buffer = np.array([], dtype=np.int16)
                        self.silence_frames = 0
                        detect_counter = 0

        except Exception as e:
            log.error(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
            log.info("Stopped.")

    def _on_detected(self):
        """Called when wake word is detected. Override or extend."""
        # Play a beep to acknowledge
        log.info("Wake word action triggered!")
        # TODO: send signal to voice assistant pipeline
        # For now, play a short beep
        try:
            os.system("aplay -D plughw:0,0 /opt/voice-client/beep.wav 2>/dev/null &")
        except Exception:
            pass


if __name__ == "__main__":
    listener = WakeWordListener()
    listener.start()
