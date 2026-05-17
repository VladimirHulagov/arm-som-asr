#!/usr/bin/env python3
"""
Wake word listener for "Слушай" on Khadas VIM2.
Uses Silero VAD for voice activity detection,
openwakeword AudioFeatures for mel spectrogram extraction,
and custom ONNX model for "Слушай" detection.
"""
import numpy as np
import onnxruntime as ort
import pyaudio
import wave
import io
import time
import logging
import sys
import os

# Suppress onnxruntime warnings
ort.set_default_logger_severity(3)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("slushay-listener")

# Audio config
CHUNK = 512  # 32ms at 16kHz
RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
MIC_DEVICE = "plughw:1,0"  # Jieli USB mic

# Model paths
MODELS_DIR = "/opt/voice-client/models"
SLUSHAY_MODEL = os.path.join(MODELS_DIR, "slushay.onnx")

# VAD config
VAD_THRESHOLD = 0.3
VAD_SILENCE_LIMIT = 1.0  # seconds of silence to stop recording

# Detection config
DETECTION_THRESHOLD = 0.7
COOLDOWN = 2.0  # seconds between detections

class WakeWordListener:
    def __init__(self):
        # Load ONNX models
        log.info("Loading models...")
        
        # Silero VAD
        import torch
        self.vad_model, self.vad_utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            trust_repo=True
        )
        self.get_speech_ts = self.vad_utils[0]
        log.info("VAD model loaded")
        
        # Our custom wake word model
        self.ww_session = ort.InferenceSession(SLUSHAY_MODEL)
        self.input_name = self.ww_session.get_inputs()[0].name
        log.info(f"Wake word model loaded: {SLUSHAY_MODEL}")
        
        # Mel spectrogram extraction using openwakeword
        from openwakeword.utils import AudioFeatures
        self.audio_features = AudioFeatures()
        log.info("AudioFeatures initialized")
        
        # Audio buffer
        self.audio_buffer = np.array([], dtype=np.int16)
        self.recording = False
        self.silence_start = None
        self.last_detection = 0
        
        # PyAudio
        self.pa = pyaudio.PyBaseProxy  # will init in start()
        
    def _get_mic_index(self, pa):
        """Find the Jieli USB mic device index."""
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if 'Jieli' in info.get('name', '') or 'UAC' in info.get('name', ''):
                log.info(f"Found mic: {info['name']} (index {i})")
                return i
        # Fallback: use device name directly
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get('maxInputChannels', 0) > 0:
                log.info(f"Using mic: {info['name']} (index {i})")
                return i
        return None
    
    def detect_wake_word(self, audio_int16):
        """Run wake word detection on audio buffer."""
        if len(audio_int16) < 48000:  # need at least 3 seconds
            return 0.0
        
        # Take last 3 seconds
        clip = audio_int16[-48000:]
        
        # Extract features using openwakeword
        try:
            features = self.audio_features.embed_clips(clip.reshape(1, -1))
            flat = features.flatten().astype(np.float32).reshape(1, -1)
            
            # Run inference
            result = self.ww_session.run(None, {self.input_name: flat})
            score = float(result[0][0][0])
            return score
        except Exception as e:
            log.error(f"Detection error: {e}")
            return 0.0
    
    def start(self):
        """Start listening for wake word."""
        import pyaudio
        
        pa = pyaudio.PyAudio()
        mic_idx = self._get_mic_index(pa)
        
        if mic_idx is None:
            log.error("No microphone found!")
            return
        
        log.info("Starting wake word listener...")
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=mic_idx,
            frames_per_buffer=CHUNK,
        )
        
        log.info("Listening... Say 'Слушай!'")
        
        try:
            vad_buffer = np.array([], dtype=np.float32)
            speech_buffer = np.array([], dtype=np.int16)
            
            while True:
                # Read audio chunk
                data = stream.read(CHUNK, exception_on_overflow=False)
                chunk_int16 = np.frombuffer(data, dtype=np.int16)
                chunk_float32 = chunk_int16.astype(np.float32) / 32768.0
                
                # Add to VAD buffer
                vad_buffer = np.concatenate([vad_buffer, chunk_float32])
                
                # Keep VAD buffer at ~1 second
                if len(vad_buffer) > RATE:
                    vad_buffer = vad_buffer[-RATE:]
                
                # Run VAD
                if len(vad_buffer) >= RATE // 2:  # need at least 0.5s for VAD
                    speech_ts = self.get_speech_ts(
                        vad_buffer, 
                        self.vad_model,
                        threshold=VAD_THRESHOLD,
                        min_speech_duration_ms=200,
                    )
                    
                    is_speech = len(speech_ts) > 0
                    
                    if is_speech:
                        speech_buffer = np.concatenate([speech_buffer, chunk_int16])
                        self.silence_start = None
                        
                        # Check for wake word every 0.5 seconds of speech
                        if len(speech_buffer) >= 48000:  # 3 seconds
                            score = self.detect_wake_word(speech_buffer)
                            now = time.time()
                            
                            if score > DETECTION_THRESHOLD and (now - self.last_detection) > COOLDOWN:
                                log.info(f"WAKE WORD DETECTED! score={score:.3f}")
                                self.last_detection = now
                                speech_buffer = np.array([], dtype=np.int16)
                                # TODO: trigger action (send signal, play sound, etc.)
                            elif score > 0.3:
                                log.debug(f"  score={score:.3f}")
                    else:
                        # Silence - check if we should reset
                        if self.silence_start is None:
                            self.silence_start = time.time()
                        elif time.time() - self.silence_start > VAD_SILENCE_LIMIT:
                            # Reset speech buffer after silence
                            if len(speech_buffer) > 0:
                                log.debug(f"Reset after silence ({len(speech_buffer)/RATE:.1f}s)")
                            speech_buffer = np.array([], dtype=np.int16)
                            self.silence_start = None
                            
        except KeyboardInterrupt:
            log.info("Stopping...")
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()


if __name__ == "__main__":
    listener = WakeWordListener()
    listener.start()
