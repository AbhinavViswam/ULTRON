import os
import re
import time
import asyncio
import threading
import tempfile
import subprocess
import edge_tts

class VoiceSpeaker:
    """Provides hyper-realistic human voice output using Microsoft Edge Neural TTS."""
    def __init__(self, voice: str = "en-US-ChristopherNeural"):
        self.voice = voice
        self.enabled = True
        self.current_process = None
        self.should_stop = False

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
        self.should_stop = True
        if self.current_process:
            try:
                self.current_process.kill()
            except Exception:
                pass
            self.current_process = None

    def _play_audio_windows(self, file_path: str):
        """Plays MP3 natively on Windows using PowerShell MediaPlayer with instant interruption support."""
        try:
            abs_path = os.path.abspath(file_path).replace("'", "''")
            ps_command = (
                f"Add-Type -AssemblyName presentationCore; "
                f"$player = New-Object System.Windows.Media.MediaPlayer; "
                f"$player.Open('{abs_path}'); "
                f"$player.Volume = 1.0; "
                f"$player.Play(); "
                f"while ($player.NaturalDuration.HasTimeSpan -eq $false) {{ Start-Sleep -Milliseconds 50 }}; "
                f"while ($player.Position -lt $player.NaturalDuration.TimeSpan) {{ Start-Sleep -Milliseconds 100 }}; "
                f"$player.Close()"
            )
            self.current_process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Poll process until finished or interrupted
            while self.current_process and self.current_process.poll() is None:
                if self.should_stop:
                    try:
                        self.current_process.kill()
                    except Exception:
                        pass
                    break
                time.sleep(0.05)
                
            self.current_process = None
        except Exception:
            pass

    async def _async_speak(self, text: str):
        clean_text = self._clean_text_for_speech(text)
        if not clean_text or not self.enabled or self.should_stop:
            return

        try:
            temp_path = None
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
                temp_path = temp_file.name

            # Boost volume output by +50% for maximum clarity
            communicate = edge_tts.Communicate(clean_text, self.voice, volume="+50%")
            await communicate.save(temp_path)

            if not self.should_stop:
                self._play_audio_windows(temp_path)

            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

        except Exception as e:
            pass

    def speak(self, text: str):
        """Synchronous speech execution."""
        try:
            asyncio.run(self._async_speak(text))
        except Exception:
            pass

    def speak_async(self, text: str):
        """Asynchronous non-blocking speech execution in a background thread."""
        self.stop()
        self.should_stop = False
        thread = threading.Thread(target=self.speak, args=(text,), daemon=True)
        thread.start()
