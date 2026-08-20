"""Three keys from three accounts, and moving between them when one runs out.

Rate limits are counted per account, so three keys from three accounts is
three quotas. The value of that depends entirely on rotating for the right
reasons: a 429 is another account's turn, but a 400 is a bad request that
every account will refuse identically. Rotating on the second kind spends
every key on a fault that was never about the keys.

Nothing here makes a network call, and no test may print a key.
"""

import pytest

from ultron import api_keys
from ultron.api_keys import KeyRing


class Error(Exception):
    """An SDK error, as far as anything here needs to know."""

    def __init__(self, message="boom", status_code=None, headers=None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.response = type("r", (), {"headers": headers, "status_code": status_code})()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def read(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------------------
# Deciding what a failure means
# ---------------------------------------------------------------------------

class TestTellingTheFailuresApart:
    @pytest.mark.parametrize("status,expected", [
        (429, api_keys.RATE_LIMIT),
        (401, api_keys.AUTH),
        (403, api_keys.AUTH),
        (400, api_keys.FATAL),
        (404, api_keys.FATAL),
        (413, api_keys.FATAL),
        (500, api_keys.RETRY),
        (502, api_keys.RETRY),
        (503, api_keys.RETRY),
    ])
    def test_status_decides(self, status, expected):
        assert api_keys.classify(Error(status_code=status)) == expected

    def test_a_bad_request_is_never_a_reason_to_change_keys(self):
        """Groq answered about one request in six with a 400 once 89 tool
        schemas were attached. Three keys would have gone on one bad
        payload, and the user would be told they were out of keys."""
        error = Error("tool schema invalid", status_code=400)
        assert api_keys.classify(error) == api_keys.FATAL

    def test_a_timeout_retries_the_same_key(self):
        """No status at all, and the commonest failure there is. A different
        key does not make the network faster."""
        assert api_keys.classify(TimeoutError("timed out")) == api_keys.RETRY

    def test_a_rate_limit_is_recognised_without_a_status(self):
        """Wrapped and re-raised errors lose the attribute."""
        assert api_keys.classify(
            Exception("Rate limit reached for model")) == api_keys.RATE_LIMIT

    def test_a_status_in_the_message_is_found(self):
        assert api_keys.classify(Exception("Error code: 429 - too many")) == \
            api_keys.RATE_LIMIT

    def test_an_unrecognisable_error_retries_rather_than_burning_a_key(self):
        """The safe default: retrying wastes a moment, rotating wastes a
        quota that was never spent."""
        assert api_keys.classify(Exception("something odd")) == api_keys.RETRY


class TestHowLongToWait:
    def test_the_providers_own_number_is_used(self):
        assert api_keys.retry_after(
            Error(status_code=429, headers={"retry-after": "30"})) == 30.0

    def test_groqs_prose_is_read(self):
        """Groq puts it in the message: "Please try again in 7.5s"."""
        assert api_keys.retry_after(
            Error("Rate limit reached. Please try again in 7.5s")) == 7.5

    def test_silence_means_zero_not_a_guess(self):
        assert api_keys.retry_after(Error(status_code=429)) == 0.0

    def test_nonsense_does_not_raise(self):
        assert api_keys.retry_after(
            Error(status_code=429, headers={"retry-after": "soon"})) == 0.0

    def test_a_header_object_that_is_not_a_dict_is_survived(self):
        assert api_keys.retry_after(Error(status_code=429, headers=object())) == 0.0


# ---------------------------------------------------------------------------
# The ring itself
# ---------------------------------------------------------------------------

class TestChoosingAKey:
    def test_it_starts_on_the_first_key(self):
        ring = KeyRing(["a", "b", "c"])
        assert ring.current() == "a"

    def test_a_rate_limit_moves_to_the_next(self):
        ring = KeyRing(["a", "b", "c"])
        assert ring.rate_limited() is True
        assert ring.current() == "b"

    def test_it_stays_put_between_failures(self):
        """Round-robin would balance load and throw away the prompt cache,
        which both Groq and OpenRouter key off the API key."""
        ring = KeyRing(["a", "b", "c"])
        assert [ring.current() for _ in range(5)] == ["a"] * 5

    def test_it_wraps_around_to_a_recovered_key(self):
        """After both keys cool off, the ring must come back round to the
        first rather than walking off the end."""
        clock = Clock()
        ring = KeyRing(["a", "b"], clock=clock.read)
        ring.rate_limited(30)          # a parked, now on b
        assert ring.current() == "b"
        ring.rate_limited(30)          # b parked, nothing free
        assert ring.exhausted() is True

        clock.advance(31)              # both windows have refilled
        assert ring.rate_limited() is True
        assert ring.current() == "a"

    def test_every_key_spent_is_reported_rather_than_hidden(self):
        ring = KeyRing(["a", "b"])
        ring.rate_limited()
        assert ring.rate_limited() is False, "it claimed a free key it did not have"
        assert ring.exhausted() is True

    def test_a_cooled_key_comes_back(self):
        clock = Clock()
        ring = KeyRing(["a", "b"], clock=clock.read)
        ring.rate_limited(60)
        ring.rate_limited(60)
        assert ring.exhausted() is True

        clock.advance(61)
        assert ring.exhausted() is False

    def test_the_providers_wait_is_honoured(self):
        clock = Clock()
        ring = KeyRing(["a"], clock=clock.read)
        ring.rate_limited(45)

        clock.advance(44)
        assert ring.exhausted() is True
        clock.advance(2)
        assert ring.exhausted() is False

    def test_an_enormous_wait_is_capped(self):
        """A daily quota can report tens of thousands of seconds. Honouring
        it literally means the key is gone for the rest of the day even
        after the provider resets."""
        clock = Clock()
        ring = KeyRing(["a"], clock=clock.read)
        ring.rate_limited(86400)

        clock.advance(api_keys.MAX_COOLDOWN_SECONDS + 1)
        assert ring.exhausted() is False


class TestARejectedKey:
    def test_it_is_not_tried_again(self):
        """A revoked key is refused every time; a cooldown would just mean
        failing on it once a minute forever."""
        clock = Clock()
        ring = KeyRing(["a", "b"], clock=clock.read)
        ring.rejected()
        assert ring.current() == "b"

        clock.advance(100000)
        ring.rate_limited()
        assert ring.current() == "b", "it went back to a key that was rejected"

    def test_all_keys_rejected_is_reported(self):
        ring = KeyRing(["a", "b"])
        ring.rejected()
        assert ring.rejected() is False
        assert ring.exhausted() is True


class TestNoKeysAtAll:
    def test_an_empty_ring_does_not_raise(self):
        ring = KeyRing([])
        assert ring.current() == ""
        assert ring.rate_limited() is False
        assert ring.rejected() is False
        assert ring.count == 0

    def test_a_single_key_has_nowhere_to_go(self):
        ring = KeyRing(["only"])
        assert ring.rate_limited() is False
        assert ring.current() == "only"


class TestItNeverPrintsAKey:
    """keys.json is gitignored and the value is the whole secret. A log line
    or an exception message carrying it defeats that entirely."""

    SECRET = "gsk_thisisthesecretvalue"

    def test_the_label_names_a_position(self):
        ring = KeyRing([self.SECRET, "second"])
        assert self.SECRET not in ring.label()
        assert "#1" in ring.label()

    def test_the_status_line_names_positions(self):
        clock = Clock()
        ring = KeyRing([self.SECRET, "second"], clock=clock.read)
        ring.rate_limited(30)

        status = ring.status()
        assert self.SECRET not in status and "second" not in status
        assert "cooling" in status

    def test_a_lone_key_is_still_not_named(self):
        ring = KeyRing([self.SECRET])
        assert self.SECRET not in ring.label()
