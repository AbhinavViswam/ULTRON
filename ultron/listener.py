import collections
import os
import queue
import sys
import time
import numpy as np
import sounddevice as sd
import speech_recognition as sr
import threading

from ultron.config import config
from ultron import vosk_engine

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

# Audio waiting to be decoded, when recognition happens on this machine.
#
# Decoding takes about a fifth of real time, so this normally holds one chunk.
# It is bounded anyway: if the CPU is briefly taken by something else, falling
# behind and dropping the oldest audio is recoverable, whereas a queue that
# grows without limit takes the whole process down.
AUDIO_QUEUE_CHUNKS = 64

# Longest stretch of audio sent to the online recogniser as one phrase.
#
# Only reached if the offline endpointer never calls an end, which means
# something is wrong. Trimming the front loses the oldest audio rather than
# the most recent, because the most recent is what someone just said.
MAX_SEGMENT_SECONDS = 30.0

# Confidence below which a phrase is treated as noise rather than speech.
#
# Zero by default, and deliberately so. The complaint being fixed here is
# "it does not hear me", and a filter set by guesswork reproduces exactly
# that complaint while looking like a different bug. The confidence of every
# phrase is printed, so this can be set from observation rather than taste.
MIN_CONFIDENCE = 0.0

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

        # Offline recognition, when it is available. While this is set there
        # is no amplitude threshold in the path at all: every chunk reaches
        # the recogniser and the acoustic model decides what was speech.
        self._vosk = None
        self._audio_queue = None
        self._decoder = None
        # Audio for the phrase currently being spoken, kept so the online
        # recogniser can be given exactly what the offline one delimited.
        self._segment = []

    def language(self) -> str:
        """The locale to transcribe against.

        An empty or blank setting means the same as an absent one — fall back
        rather than send "" and quietly get en-US again.
        """
        if self._language:
            return self._language
        configured = config.get("speech_language", DEFAULT_SPEECH_LANGUAGE)
        return (configured or "").strip() or DEFAULT_SPEECH_LANGUAGE

    def speech_engine(self) -> str:
        """Which recogniser turns audio into words.

        Two separate questions hide behind "which engine": who decides that
        someone is speaking, and who works out what they said. They have
        different best answers. Google is markedly better at Indian English
        than a 55MB offline model; but Google is a network call, which is the
        entire reason an amplitude threshold existed to ration it — and that
        threshold is what stopped hearing quiet speech.

        So they are separate settings. This one is the transcriber.
        """
        value = (config.get("speech_engine", "google") or "google")
        value = str(value).strip().lower()
        return value if value in ("google", "vosk") else "google"

    def uses_offline_endpointing(self) -> bool:
        """Whether Vosk decides where phrases begin and end.

        When it does, no amplitude threshold is consulted at any point: the
        acoustic model says when speech started, so the quiet beginning of a
        word is inside the segment rather than cut off before it.
        """
        if self.speech_engine() == "vosk":
            return True
        return config.get("vosk.use_for_endpointing", True) is not False

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

            if self._vosk is not None:
                # Nothing is filtered out. The recogniser sees the quiet start
                # of every word, which is the part an amplitude gate ate.
                if self._level_listeners:
                    reference = max(self.speech_threshold * 4.0, 200.0)
                    self._emit_level(min(1.0, rms / reference),
                                     rms > self.speech_threshold)
                self._enqueue(indata)
                return

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

            self._deliver(text)
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

    def _deliver(self, text: str):
        """Hands a transcription to whoever asked for it, unless vetoed.

        The veto is asked before the callback, not after: the callback
        interrupts speech and runs the command, so a late check would still
        have let Ultron cut itself off and obey its own voice.
        """
        if not text or not text.strip():
            return
        if self.ignore_check and self.ignore_check(text):
            return
        print(f"\n\n[Voice Input Detected]: {text}")
        if self.callback_func:
            self.callback_func(text)

    def _decode_loop(self):
        """Turns queued audio into phrases, off the audio thread.

        Decoding a chunk takes roughly a fifth of its duration, which would
        fit inside the audio callback — but only on average. That callback
        runs on the driver's realtime thread, and one slow pass there drops
        input outright, which would look exactly like the microphone fault
        this is meant to fix. So the callback only ever enqueues.
        """
        while self.is_listening:
            try:
                chunk = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                heard = self._vosk.accept(chunk)
            except Exception as e:
                print(f"[Voice Engine] offline recognition failed: {e}")
                continue
            self._segment.append(chunk)
            self._trim_segment()
            if heard:
                self._finish_phrase(*heard)

        # Whatever was still being said when listening stopped.
        try:
            remaining = self._vosk.flush()
        except Exception:
            remaining = None
        if remaining:
            self._finish_phrase(*remaining)

    def _trim_segment(self):
        """Keeps the pending segment bounded, dropping the oldest audio."""
        limit = int(self.sample_rate * MAX_SEGMENT_SECONDS) * 2   # int16 bytes
        total = sum(len(chunk) for chunk in self._segment)
        while self._segment and total > limit:
            total -= len(self._segment.pop(0))

    def _finish_phrase(self, text: str, confidence: float):
        """A phrase has ended. Whoever transcribes it, Vosk found it.

        Vosk having produced text is the useful signal even when its words are
        not used: it means that stretch of audio contained speech, so it is
        worth the network call. Silence never gets sent.
        """
        segment, self._segment = self._segment, []
        if self.speech_engine() == "vosk":
            self._on_phrase(text, confidence)
            return
        if not segment:
            return
        audio = np.frombuffer(b"".join(segment), dtype=np.int16)
        self._process_audio(audio)

    def min_confidence(self) -> float:
        """How sure the recogniser must be before a phrase is acted on."""
        configured = config.get("vosk.min_confidence", MIN_CONFIDENCE)
        try:
            return float(configured)
        except (TypeError, ValueError):
            return MIN_CONFIDENCE

    def _on_phrase(self, text: str, confidence: float):
        """A completed phrase from the offline recogniser."""
        floor = self.min_confidence()
        if confidence < floor:
            print(f"[Voice Engine] ignoring {text!r} - confidence "
                  f"{confidence:.2f} is below {floor:.2f}")
            return
        self._deliver(text)

    def _start_offline_engine(self) -> bool:
        """Loads Vosk if it is there, and says so either way.

        Falling back silently would be the worst outcome available: the
        threshold bug exists only in the fallback path, so a quiet fallback
        means the symptom returns with nothing to explain why.
        """
        if not self.uses_offline_endpointing():
            print(f"[Voice Engine] Google ({self.language()}), gated on an "
                  f"amplitude threshold (vosk.use_for_endpointing is off)")
            return False

        reason = vosk_engine.unavailable_reason()
        if reason:
            print(f"[Voice Engine] offline model unavailable - {reason}")
            print(f"[Voice Engine] Google ({self.language()}) alone, which "
                  f"needs an amplitude threshold to ration the network calls")
            return False
        try:
            self._vosk = vosk_engine.VoskTranscriber(vosk_engine.model_path(),
                                                     self.sample_rate)
        except Exception as e:
            self._vosk = None
            print(f"[Voice Engine] could not load the offline model: {e}")
            return False

        self._audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_CHUNKS)
        self._decoder = threading.Thread(target=self._decode_loop, daemon=True)
        self._decoder.start()
        if self.speech_engine() == "vosk":
            print(f"[Voice Engine] offline: {self._vosk.name} both finds and "
                  f"transcribes speech. No network, no amplitude threshold.")
        else:
            print(f"[Voice Engine] {self._vosk.name} finds the phrase, Google "
                  f"({self.language()}) transcribes it. No amplitude "
                  f"threshold; only real speech is sent.")
        return True

    def _enqueue(self, indata):
        """Queues audio, dropping the oldest if the decoder falls behind."""
        data = indata.tobytes()
        try:
            self._audio_queue.put_nowait(data)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(data)
            except (queue.Empty, queue.Full):
                # Another thread beat us to it. Losing one chunk under load is
                # the intended outcome here; raising into the realtime audio
                # thread would stop the stream altogether.
                pass

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

            # Before the decoder starts: its loop runs while this is set, so
            # a thread started ahead of it would exit immediately.
            self.is_listening = True
            self._start_offline_engine()

            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            print("[Voice Engine] Continuous background microphone active.")

    def stop(self):
        self.is_listening = False
        decoder, self._decoder = self._decoder, None
        if decoder and decoder.is_alive():
            # Long enough for the loop to notice and hand back the last
            # phrase, short enough not to hang a shutdown on it.
            decoder.join(timeout=1.0)
