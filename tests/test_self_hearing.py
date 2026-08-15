"""Ultron answering its own voice.

On speakers the microphone hears the reply. Voice input interrupts speech and
is then obeyed, so Ultron cut itself off mid-sentence and processed its own
words as an instruction — the louder the reply, the more of it came back.

The tempting fix is to ignore the microphone while speaking, which costs the
ability to interrupt out loud. So most of what is tested here is the other
half: that a real interruption still gets through.
"""

import pytest

from ultron.self_hearing import (
    ECHO_TAIL_SECONDS, SelfHearingGuard, echo_ratio, is_barge_in, normalise,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def guard(clock):
    return SelfHearingGuard(clock=clock)


def _speaking(guard, text):
    """Ultron begins saying something."""
    guard.note_spoken(text)
    guard.note_speaking(True)


class TestItCatchesItself:
    def test_its_own_sentence_repeated_back(self, guard):
        _speaking(guard, "I have brought up the search results for Arijit Singh.")

        assert guard.is_own_voice("I have brought up the search results for Arijit Singh")

    def test_a_partial_pickup_still_counts(self, guard):
        """The microphone gets a lossy version, not a transcript."""
        _speaking(guard, "Sir, here is your reminder: workout at half past five.")

        assert guard.is_own_voice("here is your reminder workout at half past")

    def test_words_mangled_by_the_speaker_still_count(self, guard):
        _speaking(guard, "Today marks India's Independence Day, celebrated "
                         "across the nation with cultural events.")

        assert guard.is_own_voice("today marks indias independence day celebrated "
                                  "across the nation")

    def test_it_still_catches_it_just_after_speech_ends(self, guard, clock):
        """A phrase is only transcribed once the room has been quiet a moment."""
        _speaking(guard, "I have brought up the search results for Arijit Singh.")
        guard.note_speaking(False)
        clock.advance(1.5)

        assert guard.is_own_voice("brought up the search results for Arijit Singh")

    def test_an_earlier_sentence_is_still_remembered(self, guard, clock):
        guard.note_spoken("Sir, here is your reminder: workout.")
        clock.advance(3)
        _speaking(guard, "Anything else?")

        assert guard.is_own_voice("sir here is your reminder workout")


class TestItLetsYouInterrupt:
    """The whole reason this is not simply "ignore the mic while speaking"."""

    @pytest.mark.parametrize("shout", ["stop", "wait", "cancel", "shut up", "no"])
    def test_short_interruptions_always_get_through(self, guard, shout):
        _speaking(guard, "Stop me if you have heard this one. Wait, no, cancel that.")

        assert not guard.is_own_voice(shout), f"{shout!r} must reach Ultron"

    @pytest.mark.parametrize("shout", ["no stop wait", "stop stop stop",
                                       "wait no stop"])
    def test_an_interruption_made_of_ultrons_own_words_gets_through(self, guard, shout):
        """The case the barge-in rule exists for, and the only one.

        Shorter shouts are already let through for being too short to match.
        These are long enough to match, and every word of them is in what
        Ultron is saying — so on word overlap alone they look exactly like an
        echo. They are the most urgent thing a person can say.
        """
        _speaking(guard, "Stop me if you have heard this one. Wait, no, stop, "
                         "stop that, wait.")

        assert not guard.is_own_voice(shout), f"{shout!r} must reach Ultron"

    def test_a_real_instruction_over_the_top_gets_through(self, guard):
        _speaking(guard, "I have brought up the search results for Arijit Singh.")

        assert not guard.is_own_voice("play the second one instead")

    def test_a_long_sentence_starting_with_no_is_not_an_interruption(self, guard):
        """It should be judged on its words, not on its first one."""
        assert not is_barge_in("no I meant the other playlist entirely")

    def test_a_follow_up_that_shares_a_few_words_gets_through(self, guard):
        _speaking(guard, "I have brought up the search results for Arijit Singh.")

        assert not guard.is_own_voice("what else do you know about Arijit Singh")


class TestItDoesNotOverreach:
    def test_nothing_is_ignored_once_the_echo_window_has_passed(self, guard, clock):
        _speaking(guard, "I have brought up the search results for Arijit Singh.")
        guard.note_speaking(False)
        clock.advance(ECHO_TAIL_SECONDS + 1)

        assert not guard.is_own_voice("I have brought up the search results for Arijit Singh")

    def test_nothing_is_ignored_when_ultron_has_said_nothing(self, guard):
        guard.note_speaking(True)

        assert not guard.is_own_voice("play some music please")

    def test_very_short_phrases_are_never_matched(self, guard):
        """Two words collide by accident far too easily."""
        _speaking(guard, "Opening Spotify for you now, sir.")

        assert not guard.is_own_voice("spotify now")

    def test_a_stale_sentence_is_forgotten(self, guard, clock):
        guard.note_spoken("Sir, here is your reminder: workout at half past five.")
        clock.advance(120)
        guard.note_speaking(True)

        assert not guard.is_own_voice("here is your reminder workout at half past five")

    def test_silence_is_not_an_echo(self, guard):
        _speaking(guard, "Something.")

        assert not guard.is_own_voice("")
        assert not guard.is_own_voice("   ")


class TestTheMeasure:
    def test_punctuation_and_case_are_ignored(self):
        assert normalise("Sir, HERE is  your reminder!") == [
            "sir", "here", "is", "your", "reminder"]

    def test_a_full_match_scores_one(self):
        assert echo_ratio("play some music", "play some music") == 1.0

    def test_nothing_in_common_scores_zero(self):
        assert echo_ratio("play some music", "the weather is fine") == 0.0

    def test_one_repeated_word_cannot_match_a_whole_phrase(self):
        """Counting occurrences, not membership."""
        assert echo_ratio("the the the the", "the weather") == pytest.approx(0.25)

    def test_an_empty_phrase_scores_zero_rather_than_dividing_by_nothing(self):
        assert echo_ratio("", "anything at all") == 0.0


class TestWiring:
    def test_the_listener_asks_before_delivering(self):
        import inspect

        from ultron.listener import VoiceListener

        source = inspect.getsource(VoiceListener._process_audio)
        assert "ignore_check" in source
        # Checked before the callback, or the command runs anyway.
        assert source.index("ignore_check") < source.index("self.callback_func(text)")

    def test_the_core_records_what_it_says_and_when_it_speaks(self):
        import inspect

        from ultron.core import UltronCore

        setup = inspect.getsource(UltronCore.__init__)
        assert "note_spoken" in setup
        assert "SelfHearingGuard()" in setup
        assert "note_speaking" in inspect.getsource(UltronCore._on_speaking_changed)

    def test_the_core_hands_the_check_to_the_listener(self):
        import inspect

        from ultron.core import UltronCore

        assert "ignore_check" in inspect.getsource(UltronCore.start_microphone)

    def test_it_can_be_switched_off_for_headphones(self, monkeypatch):
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._is_own_voice)
        assert "self_hearing_guard" in source

    def test_the_setting_ships_with_a_default(self):
        import json
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        defaults = json.load(open(os.path.join(root, "settings.default.json")))
        assert defaults["self_hearing_guard"] is True
