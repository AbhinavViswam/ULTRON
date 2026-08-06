import os
import sys
import time
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import threading

class VoiceListener:
    """Continuous background microphone listener using sounddevice and SpeechRecognition with dynamic noise calibration."""
    def __init__(self, callback_func=None, sample_rate=16000):
        self.sample_rate = sample_rate
        self.callback_func = callback_func
        self.recognizer = sr.Recognizer()
        self.is_listening = False
        self.thread = None
        self.speech_threshold = 15.0

    def calibrate(self):
        """Calibrates background ambient noise for 0.8 seconds to set dynamic speech threshold."""
        try:
            print("[Voice Engine] Calibrating microphone ambient noise...")
            recording = sd.rec(int(self.sample_rate * 0.8), samplerate=self.sample_rate, channels=1, dtype='int16')
            sd.wait()
            ambient_rms = float(np.sqrt(np.mean(recording.astype(float)**2)))
            # Set threshold to 1.5x ambient noise level, minimum floor of 10.0 for high sensitivity
            self.speech_threshold = max(ambient_rms * 1.5, 10.0)
            print(f"[Voice Engine] Calibration complete. Sensitivity threshold set to {self.speech_threshold:.1f}")
        except Exception as e:
            print(f"[Voice Engine Warning] Microphone calibration error: {e}")
            self.speech_threshold = 20.0

    def _listen_loop(self):
        """Continuously streams microphone data in background chunks and detects voice activity."""
        buffer = []
        silence_start = None
        is_speaking = False
        
        def audio_callback(indata, frames, time_info, status):
            if not self.is_listening:
                raise sd.CallbackStop()
            
            # Calculate current chunk RMS energy
            rms = float(np.sqrt(np.mean(indata.astype(float)**2)))
            
            nonlocal is_speaking, silence_start, buffer
            
            # Compare against dynamic speech threshold
            if rms > self.speech_threshold:
                if not is_speaking:
                    is_speaking = True
                silence_start = None
                buffer.append(indata.copy())
            else:
                if is_speaking:
                    buffer.append(indata.copy())
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > 0.8: # 0.8s pause -> end of phrase
                        audio_np = np.concatenate(buffer, axis=0)
                        buffer = []
                        is_speaking = False
                        silence_start = None
                        
                        # Process speech in a background thread
                        threading.Thread(target=self._process_audio, args=(audio_np,), daemon=True).start()

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16', callback=audio_callback):
                while self.is_listening:
                    sd.sleep(100)
        except Exception as e:
            print(f"\n[Microphone Listening Error]: {e}")

    def _process_audio(self, audio_np):
        try:
            raw_bytes = audio_np.tobytes()
            audio_data = sr.AudioData(raw_bytes, self.sample_rate, 2)
            text = self.recognizer.recognize_google(audio_data)
            if text and text.strip():
                print(f"\n\n[Voice Input Detected]: {text}")
                if self.callback_func:
                    self.callback_func(text)
        except sr.UnknownValueError:
            pass
        except Exception:
            pass

    def start_listening(self, callback_func=None):
        if callback_func:
            self.callback_func = callback_func
            
        if not self.is_listening:
            self.calibrate()
            self.is_listening = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            print("[Voice Engine] Continuous background microphone active.")

    def stop(self):
        self.is_listening = False
