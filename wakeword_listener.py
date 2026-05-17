#!/usr/bin/env python3
"""
Wake word listener using VAD + ASR with audio feedback.
"""

import subprocess
import time
import requests
import tempfile
import wave
import numpy as np
from pathlib import Path

SERVER_URL = "http://192.168.128.3:8765"
AUDIO_DEVICE = "plughw:1,0"
SAMPLE_RATE = 16000
CHANNELS = 1
WAKE_WORDS = ["луй", "луи", "луйи", "лую"]

def play_beep(freq=880, duration=0.1):
    """Play a short beep."""
    subprocess.run(
        ["speaker-test", "-D", AUDIO_DEVICE, "-c", "2", "-t", "sine", 
         "-f", str(freq), "-l", "1", "-s", "1"],
        capture_output=True
    )

def play_double_beep():
    """Play two beeps - wake word detected."""
    subprocess.run(
        ["speaker-test", "-D", AUDIO_DEVICE, "-c", "2", "-t", "sine", 
         "-f", "1000", "-l", "1", "-s", "1"],
        capture_output=True
    )
    time.sleep(0.05)
    subprocess.run(
        ["speaker-test", "-D", AUDIO_DEVICE, "-c", "2", "-t", "sine", 
         "-f", "1200", "-l", "1", "-s", "1"],
        capture_output=True
    )

def rms(data):
    samples = np.frombuffer(data, dtype=np.int16)
    return np.sqrt(np.mean(samples.astype(np.float32)**2))

def process_utterance(audio_buffer, server_url, wake_words):
    if len(audio_buffer) < 5:
        return False
    
    audio_data = b''.join(audio_buffer)
    
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_path = f.name
        with wave.open(f, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data)
    
    try:
        with open(temp_path, 'rb') as f:
            resp = requests.post(f"{server_url}/transcribe", files={"file": (temp_path, f)}, timeout=10)
            text = resp.json().get("text", "").lower()
            
            if text:
                print(f"\n>>> Heard: {text}")
                
                for w in wake_words:
                    if w in text:
                        return True
            return False
    except Exception as e:
        print(f"\nError: {e}")
        return False
    finally:
        Path(temp_path).unlink(missing_ok=True)

def record_command(duration=5):
    cmd = ["arecord", "-D", AUDIO_DEVICE, "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", str(CHANNELS), "-d", str(duration), "-t", "wav", "-q"]
    result = subprocess.run(cmd, capture_output=True)
    return result.stdout

def transcribe_command(audio_data, server_url):
    temp_path = "/tmp/cmd.wav"
    with open(temp_path, 'wb') as f:
        f.write(audio_data)
    
    try:
        with open(temp_path, 'rb') as f:
            resp = requests.post(f"{server_url}/transcribe", files={"file": (temp_path, f)}, timeout=30)
            return resp.json().get("text", "")
    finally:
        Path(temp_path).unlink(missing_ok=True)

def execute_command(text):
    print(f"\n>>> Command: {text}")
    text_lower = text.lower()
    
    if "погода" in text_lower:
        print("[CMD] Getting weather...")
    elif "время" in text_lower or "час" in text_lower:
        print(f"[CMD] Time: {time.strftime('%H:%M')}")
    else:
        print(f"[CMD] Would execute: {text}")

def main():
    print("=" * 50)
    print("  WAKE WORD LISTENER")
    print("  Say 'Слушай Луи' to activate!")
    print("=" * 50)
    print()
    print("Listening... (beep = speech detected)")
    print()
    
    chunk_size = int(SAMPLE_RATE * 0.1 * 2)
    vad_threshold = 500
    max_silence = 15
    
    cmd = ["arecord", "-D", AUDIO_DEVICE, "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", str(CHANNELS), "-t", "raw", "-q"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    
    try:
        recording = False
        audio_buffer = []
        silence_frames = 0
        beep_played = False
        
        while True:
            data = proc.stdout.read(chunk_size)
            if len(data) == 0:
                continue
            
            r = rms(data)
            
            if r > vad_threshold:
                if not recording:
                    print("[VAD] Speech detected!", flush=True)
                    play_beep()
                    beep_played = False
                    recording = True
                audio_buffer.append(data)
                silence_frames = 0
            elif recording:
                silence_frames += 1
                audio_buffer.append(data)
                
                if silence_frames >= max_silence:
                    print("[VAD] Speech ended, processing...", flush=True)
                    
                    if process_utterance(audio_buffer, SERVER_URL, WAKE_WORDS):
                        print("\n*** WAKE WORD DETECTED! ***")
                        play_double_beep()
                        print("Recording command (5 sec)...", flush=True)
                        audio = record_command(5)
                        cmd_text = transcribe_command(audio, SERVER_URL)
                        if cmd_text:
                            execute_command(cmd_text)
                        print()
                        print("Listening...")
                    else:
                        print("No wake word in utterance")
                    
                    audio_buffer = []
                    recording = False
                    silence_frames = 0
    
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        proc.terminate()

if __name__ == "__main__":
    main()
