"""How loud does something have to be before Ultron treats it as speech.

Measured, before the fix, at 557.3 on one calibration and 10.0 on the next
seven — in the same silent room, back to back. Whatever happened during that
one 0.8s window set the threshold for the entire session, and at 557 Ultron
cannot hear you at all.

Nothing here opens the microphone; every test scores audio it built itself.
"""

import collections
import types

import numpy as np
import pytest

from ultron.listener import (
    MAX_THRESHOLD,
    PREROLL_SECONDS,
    MIN_THRESHOLD,
    THRESHOLD_MULTIPLIER,
    VoiceListener,
)


@pytest.fixture
def listener():
    return VoiceListener()


def _room(listener, seconds=1.3, level=0.5):
    """Audio from a room that is simply sitting there."""
    return np.random.normal(0, level, int(listener.sample_rate * seconds))


def _with_burst(listener, room, amplitude, seconds=0.06):
    """The same room, plus one clack, cough or slammed door."""
    noisy = room.copy()
    start = int(listener.sample_rate * 0.4)
    length = int(listener.sample_rate * seconds)
    noisy[start:start + length] += np.random.normal(0, amplitude, length)
    return noisy


class TestOneNoiseCannotDeafenIt:
    """The actual bug: a transient dominates a squared average."""

    @pytest.mark.parametrize("amplitude", [200, 1000, 3000, 8000])
    def test_a_single_burst_does_not_move_the_floor(self, listener, amplitude):
        room = _room(listener)
        quiet_floor = listener.noise_floor(room)
        burst_floor = listener.noise_floor(_with_burst(listener, room, amplitude))

        assert burst_floor == pytest.approx(quiet_floor, rel=0.5), (
            f"a burst of {amplitude} moved the measured floor from "
            f"{quiet_floor:.1f} to {burst_floor:.1f}"
        )

    def test_the_old_formula_would_have_failed_this(self, listener):
        """Kept as the record of what was wrong, so it cannot quietly return."""
        room = _room(listener)
        noisy = _with_burst(listener, room, 1000)

        old_way = float(np.sqrt((noisy ** 2).mean())) * 1.5
        assert old_way > 100, "the burst really is loud enough to matter"
        assert listener.threshold_for(listener.noise_floor(noisy)) == MIN_THRESHOLD

    def test_sustained_noise_does_raise_it(self, listener):
        """It must follow a genuinely noisy room, not ignore everything."""
        loud = _room(listener, level=40.0)
        assert listener.noise_floor(loud) > 20
        assert listener.threshold_for(listener.noise_floor(loud)) > MIN_THRESHOLD


class TestTheThresholdStaysUsable:
    def test_a_silent_room_does_not_make_it_hair_trigger(self, listener):
        assert listener.threshold_for(0.0) == MIN_THRESHOLD

    def test_it_can_never_be_set_so_high_that_ultron_goes_deaf(self, listener):
        assert listener.threshold_for(999999.0) == MAX_THRESHOLD

    def test_between_the_limits_it_tracks_the_room(self, listener):
        assert listener.threshold_for(20.0) == pytest.approx(20.0 * THRESHOLD_MULTIPLIER)

    def test_a_microphone_that_fails_leaves_it_sensitive_not_deaf(self, listener, monkeypatch):
        """A failed calibration must not be indistinguishable from silence."""
        import ultron.listener as module

        def explode(*args, **kwargs):
            raise OSError("no input device")

        monkeypatch.setattr(module.sd, "rec", explode)
        listener.calibrate()

        assert listener.speech_threshold == MIN_THRESHOLD


class TestItCorrectsItself:
    """The real protection: calibration is a starting point, not a verdict."""

    def _quiet_history(self, listener, level=0.5, chunks=90):
        listener._recent_levels = collections.deque([level] * chunks, maxlen=chunks)

    def test_a_calibration_ruined_by_noise_recovers(self, listener):
        listener._noise_floor = 200.0
        listener.speech_threshold = listener.threshold_for(200.0)
        assert listener.speech_threshold == MAX_THRESHOLD  # deaf

        self._quiet_history(listener)
        for _ in range(20):
            listener._retune()

        assert listener.speech_threshold == MIN_THRESHOLD

    def test_recovery_does_not_need_the_threshold_to_be_right_first(self, listener):
        """Retuning must not consult a judgement the broken threshold produced."""
        import inspect

        source = inspect.getsource(VoiceListener._retune)
        assert "is_speaking" not in source
        assert "speech_threshold" not in source.split("self.speech_threshold =")[0]

    def test_a_noisy_room_raises_it_gradually(self, listener):
        listener._noise_floor = 0.5
        self._quiet_history(listener, level=40.0)

        seen = []
        for _ in range(6):
            listener._retune()
            seen.append(listener.speech_threshold)

        assert seen == sorted(seen), "it should climb, not jump around"
        assert seen[0] < 40, "one noisy moment must not raise it all at once"
        assert seen[-1] > seen[0]

    def test_it_falls_faster_than_it_rises(self, listener):
        """A room going quiet should be followed promptly; talking should not
        walk the threshold up and cut the speaker off."""
        listener._noise_floor = 0.5
        self._quiet_history(listener, level=100.0)
        listener._retune()
        risen = listener._noise_floor

        listener._noise_floor = 100.0
        self._quiet_history(listener, level=0.5)
        listener._retune()
        fallen = 100.0 - listener._noise_floor

        assert fallen > (risen - 0.5), "it must come back down faster than it went up"

    def test_it_waits_for_enough_audio_before_deciding(self, listener):
        listener._noise_floor = 5.0
        listener._recent_levels = collections.deque([500.0, 500.0], maxlen=90)
        listener._retune()

        assert listener._noise_floor == 5.0, "two chunks is not evidence"

    def test_the_listen_loop_retunes_but_never_mid_phrase(self):
        import inspect

        source = inspect.getsource(VoiceListener._listen_loop)
        assert "_retune()" in source
        # Changing where the bar is halfway through a sentence would cut it off.
        assert "if not is_speaking:" in source


class TestShortAudio:
    def test_a_chunk_too_short_to_frame_still_returns_something(self, listener):
        assert listener.noise_floor(np.zeros(10)) == 0.0

    def test_no_audio_at_all_is_not_a_crash(self, listener):
        assert listener.noise_floor(np.array([])) == 0.0


class TestTheQuietStartOfAWord:
    """Recording began the instant speech got loud enough — part-way into the
    first word, because words start softly. "Pause" reached the recogniser as
    "-se" and could not be made out. Short commands suffered worst: there is no
    second word to recover the meaning from."""

    CHUNK_SECONDS = 0.05

    def _run(self, listener, chunks, monkeypatch):
        """Feeds chunks through the real callback and returns captured phrases.

        The detector measures silence with the wall clock, so a fake one is
        advanced by a chunk's worth of time per callback — otherwise no phrase
        ever ends and nothing is ever captured.
        """
        import ultron.listener as module

        clock = {"now": 1000.0}
        monkeypatch.setattr(module.time, "time", lambda: clock["now"])

        captured = []
        monkeypatch.setattr(listener, "_process_audio", captured.append)
        # Inline, so the assertions are not racing a thread.
        monkeypatch.setattr(module.threading, "Thread",
                            lambda target, args=(), daemon=None: type(
                                "Inline", (), {"start": lambda self: target(*args)})())

        holder = {}

        class FakeStream:
            def __init__(self, **kwargs):
                holder["callback"] = kwargs["callback"]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(module.sd, "InputStream", FakeStream)
        monkeypatch.setattr(module.sd, "sleep",
                            lambda ms: setattr(listener, "is_listening", False))

        listener.is_listening = True
        listener._listen_loop()            # captures the callback, then stops
        callback = holder["callback"]

        listener.is_listening = True
        for chunk in chunks:
            callback(chunk, len(chunk), None, None)
            clock["now"] += self.CHUNK_SECONDS
        return captured

    def _chunk(self, listener, amplitude):
        size = int(listener.sample_rate * self.CHUNK_SECONDS)
        return np.full((size, 1), amplitude, dtype=np.int16)

    def test_audio_from_before_the_threshold_is_kept(self, listener, monkeypatch):
        listener.speech_threshold = 100.0
        quiet = [self._chunk(listener, 5) for _ in range(6)]
        loud = [self._chunk(listener, 400) for _ in range(4)]
        trailing = [self._chunk(listener, 0) for _ in range(24)]

        captured = self._run(listener, quiet + loud + trailing, monkeypatch)

        assert captured, "a phrase should have been captured"
        phrase = captured[0].ravel()
        assert int(np.sum(np.abs(phrase) == 5)) > 0, (
            "the run-up to the phrase was discarded — the first word is clipped")

    def test_the_whole_phrase_still_arrives(self, listener, monkeypatch):
        listener.speech_threshold = 100.0
        chunks = ([self._chunk(listener, 5) for _ in range(4)]
                  + [self._chunk(listener, 400) for _ in range(6)]
                  + [self._chunk(listener, 0) for _ in range(24)])

        captured = self._run(listener, chunks, monkeypatch)

        loud_samples = int(np.sum(np.abs(captured[0].ravel()) == 400))
        expected = 6 * int(listener.sample_rate * self.CHUNK_SECONDS)
        assert loud_samples == expected, "the loud part must not be truncated"

    def test_the_preroll_is_bounded(self, listener, monkeypatch):
        listener.speech_threshold = 100.0
        silence = [self._chunk(listener, 0) for _ in range(200)]

        self._run(listener, silence, monkeypatch)

        assert listener._preroll.maxlen is not None
        assert len(listener._preroll) <= listener._preroll.maxlen
        assert listener._preroll.maxlen * self.CHUNK_SECONDS <= PREROLL_SECONDS + 0.1


class TestFailuresAreVisible:
    def test_sound_that_could_not_be_understood_is_reported(self, listener, capsys):
        """Silence used to be the response to both "ignored you" and "never
        heard you", which have opposite fixes."""
        import speech_recognition as sr

        listener.recognizer = types.SimpleNamespace(
            recognize_google=lambda audio: (_ for _ in ()).throw(sr.UnknownValueError()))
        listener._process_audio(np.full((16000, 1), 300, dtype=np.int16))

        out = capsys.readouterr().out
        assert "could not make out" in out
        assert "threshold" in out, "the numbers are what make it diagnosable"
