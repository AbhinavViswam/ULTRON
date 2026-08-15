"""How loud does something have to be before Ultron treats it as speech.

Measured, before the fix, at 557.3 on one calibration and 10.0 on the next
seven — in the same silent room, back to back. Whatever happened during that
one 0.8s window set the threshold for the entire session, and at 557 Ultron
cannot hear you at all.

Nothing here opens the microphone; every test scores audio it built itself.
"""

import collections

import numpy as np
import pytest

from ultron.listener import (
    MAX_THRESHOLD,
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
