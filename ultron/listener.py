import collections
import os
import sys
import time
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import threading

from ultron.config import config

# Voice activity is judged 20ms at a time, and so is the noise floor.
FRAME_SECONDS = 0.02

# Long enough that a passing noise is a small share of the sample.
CALIBRATION_SECONDS = 1.5

# The first moments after a stream opens belong to the driver, not the room.
CALIBRATION_DISCARD_SECONDS = 0.2

# The room's floor is where it sits *most* of the time. A low percentile finds
# that; an average is dragged upwards by every clack, cough and passing car.
NOISE_PERCENTILE = 25

# How far above the floor something has to be to count as speech.
THRESHOLD_MULTIPLIER = 3.0

# Never so low that the room's own hiss counts as talking...
MIN_THRESHOLD = 10.0

# ...and never so high that Ultron cannot hear. A threshold this high means
# calibration went wrong, and being over-sensitive is the better failure: the
# recogniser discards noise, whereas nothing recovers a word never captured.
MAX_THRESHOLD = 300.0

# Retuning watches this much recent audio. Speech has to be under
# NOISE_PERCENTILE of it for the floor to stay put, which a sentence at a time
# comfortably is.
NOISE_WINDOW_SECONDS = 6.0
RETUNE_EVERY_SECONDS = 2.0

# The floor drops quickly when a room goes quiet, and rises slowly, so a burst
# of talking cannot walk the threshold up mid-conversation and cut you off.
FLOOR_RISE_RATE = 0.10
FLOOR_FALL_RATE = 0.50

# Audio kept from *before* the threshold was crossed.
#
# Without this, recording starts at the moment speech gets loud enough — which
# is part-way into the first word, because words begin softly. "Pause" arrives
# as "-se", the recogniser cannot make it out, and the command is lost. Short
# utterances suffer worst: there is no second word to recover the meaning from.
PREROLL_SECONDS = 0.5

# Silence that ends a phrase. Long enough to survive thinking mid-sentence,
# short enough that the reply does not feel delayed.
END_OF_PHRASE_SECONDS = 0.8

# The accent and vocabulary the recogniser should expect.
#
# SpeechRecognition defaults to en-US when no language is passed, and that
# default was being taken silently. Google does not merely re-spell the output
# per locale: each locale is a different acoustic model with a different
# language prior. Against en-US, Indian English vowels and retroflex
# consonants are scored as mispronounced American ones, and Indian names,
# places and code-switched words lose to American vocabulary that the prior
# ranks higher. The result is a recogniser that mishears a whole accent.
DEFAULT_SPEECH_LANGUAGE = "en-IN"

# How often, at most, to report sound that could not be understood.
#
# Silence made "Ultron ignored me" and "Ultron never heard me" identical, and
# they have opposite fixes. But in a noisy room this fires many times a minute
# and would bury everything else, so it is reported rarely rather than never.
UNHEARD_REPORT_SECONDS = 15.0


class VoiceListener:
    """Continuous background microphone listener using sounddevice and SpeechRecognition with dynamic noise calibration."""
    def __init__(self, callback_func=None, sample_rate=16000, language=None):
        self.sample_rate = sample_rate
        # None means "whatever the setting says at the time", so changing the
        # locale takes effect on the next phrase rather than the next launch.
        self._language = language
        self.callback_func = callback_func
        self.recognizer = sr.Recognizer()
        # Optional veto, called with each transcription before it is delivered.
        # Set by the core so Ultron does not answer its own voice coming back
        # through the speakers.
        self.ignore_check = None
        self.is_listening = False
        self.thread = None
        self.speech_threshold = 15.0
        self._level_listeners = []

        # Recent chunk loudness, for following the room as it changes.
        self._noise_floor = MIN_THRESHOLD / THRESHOLD_MULTIPLIER
        self._recent_levels = collections.deque()
        self._last_retune = 0.0
        # The last half second of audio, so a phrase can be recorded from
        # before the moment it got loud enough to notice.
        self._preroll = collections.deque()
        # Rate limit for the "could not make that out" report.
        self._last_unheard_report = 0.0

    def language(self) -> str:
        """The locale to transcribe against.

        An empty or blank setting means the same as an absent one — fall back
        rather than send "" and quietly get en-US again.
        """
        if self._language:
            return self._language
        configured = config.get("speech_language", DEFAULT_SPEECH_LANGUAGE)
        return (configured or "").strip() or DEFAULT_SPEECH_LANGUAGE

    def on_level(self, callback):
        """Registers callback(level, is_speech) with 0.0-1.0 mic loudness.

        Reuses the RMS the voice detector already computes, so a UI can show
        the microphone reacting without opening a second audio stream.
        Fires on the audio thread; keep the callback cheap.
        """
        self._level_listeners.append(callback)
        return callback

    def _emit_level(self, level: float, is_speech: bool):
        for callback in list(self._level_listeners):
            try:
                callback(level, is_speech)
            except Exception as e:
                print(f"[Voice Engine] level listener failed: {e}")

    def noise_floor(self, samples) -> float:
        """The level the room sits at, ignoring whatever briefly happened in it.

        Averaging the whole window squares every sample, so one 60ms clack in
        an otherwise silent room moved the measured floor from 0.5 to over 270
        — and the threshold with it. Scoring 20ms frames and taking a low
        percentile asks a different question: not "how loud was that window"
        but "how loud is it here normally", which is the one that matters.
        """
        samples = np.asarray(samples, dtype=float).ravel()
        frame = int(self.sample_rate * FRAME_SECONDS)
        usable = len(samples) // frame * frame
        if usable < frame:
            # Too short to frame at all; the plain RMS is all there is.
            return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
        frames = samples[:usable].reshape(-1, frame)
        return float(np.percentile(np.sqrt((frames ** 2).mean(axis=1)),
                                   NOISE_PERCENTILE))

    def threshold_for(self, floor: float) -> float:
        """The speech threshold a given noise floor deserves, within limits."""
        return float(min(max(floor * THRESHOLD_MULTIPLIER, MIN_THRESHOLD),
                         MAX_THRESHOLD))

    def calibrate(self):
        """Measures the room once, to start from something sensible.

        This is only a starting point now. Whatever happens during these two
        seconds, _retune corrects it within a few seconds of listening — which
        is what stops a single noise here from deafening Ultron for the rest
        of the session.
        """
        try:
            print("[Voice Engine] Calibrating microphone ambient noise...")
            recording = sd.rec(int(self.sample_rate * CALIBRATION_SECONDS),
                               samplerate=self.sample_rate, channels=1, dtype='int16')
            sd.wait()
            samples = recording.astype(float).ravel()
            samples = samples[int(self.sample_rate * CALIBRATION_DISCARD_SECONDS):]

            self._noise_floor = self.noise_floor(samples)
            self.speech_threshold = self.threshold_for(self._noise_floor)
            print(f"[Voice Engine] Calibration complete. Noise floor "
                  f"{self._noise_floor:.1f}, sensitivity threshold set to "
                  f"{self.speech_threshold:.1f}")
        except Exception as e:
            print(f"[Voice Engine Warning] Microphone calibration error: {e}")
            self._noise_floor = MIN_THRESHOLD / THRESHOLD_MULTIPLIER
            self.speech_threshold = MIN_THRESHOLD

    def _retune(self):
        """Follows the room while listening, so a bad calibration self-corrects.

        Deliberately independent of whether a chunk was judged to be speech: if
        the threshold is badly wrong then that judgement is wrong too, and a
        loop that trusts it stays stuck. A low percentile of recent audio needs
        no such judgement — speech is a minority of any normal few seconds.
        """
        if len(self._recent_levels) < 8:
            return

        observed = float(np.percentile(self._recent_levels, NOISE_PERCENTILE))
        rate = FLOOR_RISE_RATE if observed > self._noise_floor else FLOOR_FALL_RATE
        self._noise_floor += (observed - self._noise_floor) * rate
        self.speech_threshold = self.threshold_for(self._noise_floor)

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

            # Keep roughly NOISE_WINDOW_SECONDS of history. Chunk size is the
            # driver's choice and can change, so it is measured rather than
            # assumed.
            if self._recent_levels.maxlen is None and frames:
                chunks = max(4, int(NOISE_WINDOW_SECONDS /
                                    (frames / self.sample_rate)))
                self._recent_levels = collections.deque(self._recent_levels,
                                                        maxlen=chunks)
            self._recent_levels.append(rms)

            now = time.time()
            if now - self._last_retune >= RETUNE_EVERY_SECONDS:
                self._last_retune = now
                # Never mid-phrase: the threshold that started this phrase is
                # the one that should decide where it ends.
                if not is_speaking:
                    self._retune()

            if self._level_listeners:
                # Scale against the calibrated threshold so the reported level
                # means the same thing in a quiet room and a noisy one.
                reference = max(self.speech_threshold * 4.0, 200.0)
                self._emit_level(min(1.0, rms / reference), rms > self.speech_threshold)

            # Always kept, so the run-up to a phrase is available once one
            # starts. This is the only copy of the quiet beginning of a word.
            if self._preroll.maxlen is None and frames:
                self._preroll = collections.deque(
                    self._preroll,
                    maxlen=max(2, int(PREROLL_SECONDS / (frames / self.sample_rate))))
            self._preroll.append(indata.copy())

            # Compare against dynamic speech threshold
            if rms > self.speech_threshold:
                if not is_speaking:
                    is_speaking = True
                    # Start from what was already being said. The current chunk
                    # is the last entry, so this takes it too.
                    buffer = list(self._preroll)
                else:
                    buffer.append(indata.copy())
                silence_start = None
            else:
                if is_speaking:
                    buffer.append(indata.copy())
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > END_OF_PHRASE_SECONDS:
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
            text = self.recognizer.recognize_google(audio_data,
                                                    language=self.language())
            if not text or not text.strip():
                return

            # Asked before the callback, not after: the callback interrupts
            # speech and runs the command, so a late check would still have
            # let Ultron cut itself off and obey its own voice.
            if self.ignore_check and self.ignore_check(text):
                return

            print(f"\n\n[Voice Input Detected]: {text}")
            if self.callback_func:
                self.callback_func(text)
        except sr.UnknownValueError:
            # Something crossed the threshold and then could not be made out.
            #
            # This used to pass silently, which made "Ultron ignored me"
            # indistinguishable from "Ultron never heard me" — the two have
            # opposite fixes, and there was no way to tell them apart. The
            # numbers are what separate them: a very short clip means the
            # phrase was clipped, a quiet one means the threshold is too high.
            now = time.time()
            if now - self._last_unheard_report < UNHEARD_REPORT_SECONDS:
                return
            self._last_unheard_report = now

            seconds = len(audio_np) / self.sample_rate
            peak = float(np.abs(audio_np).max()) if len(audio_np) else 0.0
            print(f"[Voice Engine] heard {seconds:.1f}s of sound but could not "
                  f"make out any words (peak {peak:.0f}, threshold "
                  f"{self.speech_threshold:.1f}, language {self.language()})")
        except Exception as e:
            # Anything else means the transcription never happened — usually
            # the network. Without this it looks identical to saying nothing.
            print(f"[Voice Engine] transcription failed: {e}")

    def start_listening(self, callback_func=None):
        if callback_func:
            self.callback_func = callback_func
            
        if not self.is_listening:
            # Start from a clean slate; the device may have changed since the
            # last session, and stale levels would be retuned against.
            self._recent_levels = collections.deque()
            self._preroll = collections.deque()
            self._last_retune = time.time()
            self.calibrate()
            self.is_listening = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            print("[Voice Engine] Continuous background microphone active.")

    def stop(self):
        self.is_listening = False
