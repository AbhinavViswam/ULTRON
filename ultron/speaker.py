import os
import re
import time
import threading
import urllib.request
import sounddevice as sd

try:
    from piper.voice import PiperVoice
except ImportError:
    PiperVoice = None

class VoiceSpeaker:
    """Provides human-like voice output using the offline Piper Neural TTS engine with instant audio streaming."""
    def __init__(self, voice_name: str = "en_US-bryce-medium"):
        self.voice_name = voice_name
        self.enabled = True
        self.speech_id = 0
        self.piper_voice = None
        
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.voices_dir = os.path.join(self.base_dir, "resources", "voices")
        os.makedirs(self.voices_dir, exist_ok=True)
        
        self.onnx_path = os.path.join(self.voices_dir, f"{self.voice_name}.onnx")
        self.json_path = os.path.join(self.voices_dir, f"{self.voice_name}.onnx.json")

    def _clean_text_for_speech(self, text: str) -> str:
        """Strips markdown formatting, URLs, code snippets, and emojis for clean natural speech."""
        if not text:
            return ""
        # Replace URLs with "link"
        text = re.sub(r'https?://\S+', 'website link', text)
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        # Remove inline code ticks
        text = re.sub(r'`[^`]*`', '', text)
        # Remove markdown symbols (*, _, ~, #, >, -)
        text = re.sub(r'[*_~#>-]', '', text)
        # Remove non-ASCII / emojis that TTS might mispronounce
        text = re.sub(r'[^\x00-\x7F]+', '', text)
        # Normalize whitespace
        text = ' '.join(text.split())
        return text

    def stop(self):
        """Immediately interrupts and stops any active speech playback."""
        self.speech_id += 1

    def _ensure_model_downloaded(self):
        """Downloads the Piper voice model and config if they don't exist."""
        if not os.path.exists(self.onnx_path) or not os.path.exists(self.json_path):
            print(f"\n[Voice Engine] First run detected. Downloading offline voice model '{self.voice_name}'...")
            
            parts = self.voice_name.split('-')
            if len(parts) >= 3:
                locale = parts[0]
                lang = locale.split('_')[0]
                name = parts[1]
                quality = parts[2]
                
                base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{lang}/{locale}/{name}/{quality}/{self.voice_name}"
                
                try:
                    urllib.request.urlretrieve(base_url + ".onnx", self.onnx_path)
                    urllib.request.urlretrieve(base_url + ".onnx.json", self.json_path)
                    print(f"[Voice Engine] Voice model '{self.voice_name}' downloaded successfully to resources/voices/.")
                except Exception as e:
                    print(f"[Voice Engine Error] Failed to download voice model: {e}")
            else:
                print(f"[Voice Engine Error] Invalid voice name format: {self.voice_name}")

    def _load_voice(self):
        if not PiperVoice:
            print("[Voice Engine Error] piper-tts is not installed. Please run: pip install piper-tts")
            return False
            
        if not self.piper_voice:
            self._ensure_model_downloaded()
            if os.path.exists(self.onnx_path) and os.path.exists(self.json_path):
                self.piper_voice = PiperVoice.load(self.onnx_path, config_path=self.json_path)
            else:
                return False
        return True

    def _speak_sync(self, text: str, my_id: int):
        clean_text = self._clean_text_for_speech(text)
        if not clean_text or not self.enabled or self.speech_id != my_id:
            return

        if not self._load_voice():
            return

        stream = None
        try:
            stream = sd.RawOutputStream(samplerate=self.piper_voice.config.sample_rate, channels=1, dtype='int16')
            stream.start()
            
            for audio_chunk in self.piper_voice.synthesize(clean_text):
                if self.speech_id != my_id:
                    break
                stream.write(audio_chunk.audio_int16_bytes)
            
            if self.speech_id != my_id:
                stream.abort() # instantly clear buffer on interrupt
            else:
                stream.stop()
        except Exception as e:
            pass
        finally:
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

    def speak(self, text: str, my_id: int = None):
        """Synchronous speech execution."""
        if my_id is None:
            my_id = self.speech_id
        self._speak_sync(text, my_id)

    def speak_async(self, text: str):
        """Asynchronous non-blocking speech execution in a background thread."""
        self.stop() # Increments speech_id, killing any old threads and aborting streams
        current_id = self.speech_id
        thread = threading.Thread(target=self.speak, args=(text, current_id), daemon=True)
        thread.start()
