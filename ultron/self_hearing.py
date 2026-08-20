"""Telling Ultron's own voice apart from yours.

On speakers rather than headphones, the microphone hears whatever Ultron just
said. That is not merely noise: voice input interrupts speech and is then
processed as a command, so Ultron cuts itself off mid-sentence and answers its
own words. The louder the reply, the more of it comes back.

The obvious fix — ignore the microphone while speaking — costs the thing that
makes the assistant feel responsive: interrupting it out loud. So the question
here is not "is Ultron speaking" but "are these Ultron's own words", which is
answerable by comparing what was heard against what was just said.
"""

import re
import time

# How long after speech ends its words can still arrive. A phrase is only
# transcribed once the speaker has been quiet for 0.8s, and the request itself
# takes a moment, so the tail outlives the audio by more than it seems.
ECHO_TAIL_SECONDS = 2.5

# Anything older than this is no longer plausibly an echo, whatever it matches.
SPOKEN_MEMORY_SECONDS = 30.0

# What share of the heard words must appear in what Ultron said. Not 1.0:
# transcription of a loudspeaker is lossy, and drops and mangles words.
MATCH_RATIO = 0.6

# Below this, a match means little — a two word phrase collides by accident.
MIN_WORDS_TO_MATCH = 3

# Said over the top of Ultron, these are meant for it. They are short, they
# are common, and they are exactly what a hard "ignore while speaking" rule
# would throw away — so they are never treated as an echo.
BARGE_IN_WORDS = {
    "stop", "wait", "cancel", "quiet", "enough", "shush", "shut",
    "pause", "no", "nope", "abort", "hold", "silence",
}


def normalise(text: str) -> list:
    """Lowercase words, punctuation dropped — the form both sides compare in."""
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def is_barge_in(text: str) -> bool:
    """True for a short interruption clearly aimed at Ultron.

    Kept deliberately narrow: a long sentence that happens to start with "no"
    is a reply, not an interruption, and should be matched on its merits.
    """
    words = normalise(text)
    return bool(words) and len(words) <= 3 and any(w in BARGE_IN_WORDS for w in words)


def echo_ratio(heard: str, spoken: str) -> float:
    """What share of the heard words appear in what was said.

    Counting occurrences rather than using a set, so a heard phrase cannot be
    matched by one word of Ultron's repeated back at it.
    """
    heard_words = normalise(heard)
    if not heard_words:
        return 0.0

    remaining = {}
    for word in normalise(spoken):
        remaining[word] = remaining.get(word, 0) + 1

    matched = 0
    for word in heard_words:
        if remaining.get(word):
            remaining[word] -= 1
            matched += 1
    return matched / len(heard_words)


class SelfHearingGuard:
    """Remembers what Ultron said, so the microphone can be second-guessed."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._spoken = []          # (said_at, text)
        self._speaking_since = None
        self._quiet_since = clock()

    # -- what Ultron is doing -------------------------------------------

    def note_spoken(self, text: str):
        """Records something about to be said aloud."""
        if text and text.strip():
            self._spoken.append((self._clock(), text))
        self._forget_old()

    def note_speaking(self, speaking: bool):
        now = self._clock()
        if speaking:
            self._speaking_since = now
        else:
            self._speaking_since = None
            self._quiet_since = now

    def _forget_old(self):
        cutoff = self._clock() - SPOKEN_MEMORY_SECONDS
        self._spoken = [entry for entry in self._spoken if entry[0] >= cutoff]

    # -- the question ----------------------------------------------------

    def within_echo_window(self) -> bool:
        """True while Ultron's voice could still be reaching the microphone."""
        if self._speaking_since is not None:
            return True
        return (self._clock() - self._quiet_since) <= ECHO_TAIL_SECONDS

    def is_own_voice(self, heard: str) -> bool:
        """True when this transcription is Ultron hearing itself.

        False whenever there is doubt. Discarding a real instruction is the
        worse mistake: an echo that gets through is one confused reply, but an
        instruction that gets dropped leaves the user repeating themselves at
        a machine that appears to be ignoring them.
        """
        if not heard or not heard.strip():
            return False
        if not self.within_echo_window():
            return False
        if is_barge_in(heard):
            return False
        if len(normalise(heard)) < MIN_WORDS_TO_MATCH:
            return False

        self._forget_old()
        return any(echo_ratio(heard, spoken) >= MATCH_RATIO
                   for _said_at, spoken in self._spoken)
