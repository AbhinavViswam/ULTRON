"""Several API keys for one provider, and knowing when to move between them.

A hosted provider counts requests per *account*. Holding three keys from
three accounts therefore means three quotas, and hitting the limit on one is
not a reason to stop -- it is a reason to use the next.

The whole difficulty is telling apart the failures a different key fixes from
the ones it cannot:

    429  the account is out of quota          -> another key helps
    401  this key is wrong or revoked         -> another key helps
    400  the request itself is malformed      -> every key rejects it
    5xx  the provider is having a bad moment  -> every key sees the same

That 400 row is the one that matters here. Groq answered roughly one request
in six with a 400 when 89 tool schemas were attached. Rotating on that would
spend all three keys on a fault that was never about the keys, and then
report "out of keys" for what is actually a bad request.

Nothing in this module ever logs a key. Keys are referred to by position.
"""

import re
import threading
import time

# How long a rate-limited key is left alone when the provider does not say.
# Most per-minute windows have refilled well inside this.
DEFAULT_COOLDOWN_SECONDS = 60.0

# A provider asking for a very long wait is usually a daily quota. Honour it,
# but not past the point where the key would never be reconsidered.
MAX_COOLDOWN_SECONDS = 900.0

# What a failure means for the choice of key.
RATE_LIMIT = "rate_limit"   # this account is spent, try another
AUTH = "auth"               # this key is bad, stop using it
FATAL = "fatal"             # the request is wrong; no key will take it
RETRY = "retry"             # transient; the same key should try again


def _status_of(error) -> int:
    """The HTTP status behind an exception, or 0 if it has none.

    The OpenAI SDK puts it on the exception, but a wrapped or re-raised
    error may only carry it in the text, and guessing wrong here decides
    whether three keys get burned.
    """
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value

    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value

    match = re.search(r"\b(4\d\d|5\d\d)\b", str(error))
    return int(match.group(1)) if match else 0


def classify(error) -> str:
    """What kind of failure this is, in terms of what to do about it."""
    status = _status_of(error)

    if status == 429:
        return RATE_LIMIT
    if status in (401, 403):
        return AUTH
    if status in (408, 409, 425, 500, 502, 503, 504, 529):
        return RETRY
    if 400 <= status < 500:
        # A bad request, an unsupported model, a payload too large. The next
        # key would refuse it in exactly the same words.
        return FATAL

    text = str(error).lower()
    if "rate limit" in text or "too many requests" in text or "quota" in text:
        return RATE_LIMIT
    if "invalid api key" in text or "unauthorized" in text:
        return AUTH

    # Timeouts and dropped connections arrive with no status at all, and they
    # are the single most common failure. Retrying is right for those.
    return RETRY


def retry_after(error) -> float:
    """Seconds the provider asked us to wait, or 0.0 if it did not say."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    raw = None
    if headers is not None:
        try:
            raw = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:
            # Some header objects are not dict-like; the default is fine.
            raw = None

    if raw is None:
        # Groq states it in prose: "Please try again in 7.5s".
        match = re.search(r"try again in ([\d.]+)\s*s", str(error), re.I)
        raw = match.group(1) if match else None

    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return seconds if seconds > 0 else 0.0


class KeyRing:
    """The keys for one provider, and which of them is worth trying now.

    Selection is sticky rather than round-robin. Both Groq and OpenRouter
    cache prompt prefixes against the key that sent them, so spreading
    requests across keys for balance would throw that cache away on every
    call to save a quota that was not close to running out.
    """

    def __init__(self, keys, clock=time.monotonic):
        self._keys = [k for k in (keys or []) if k]
        self._clock = clock
        self._index = 0
        self._cooling = {}      # position -> when it may be used again
        self._dead = set()      # positions rejected by the provider
        # Background research runs on its own thread and goes through the
        # same ring, so two threads can be rotating at once.
        self._lock = threading.RLock()

    def __len__(self):
        return len(self._keys)

    @property
    def count(self) -> int:
        return len(self._keys)

    def current(self) -> str:
        """The key to send with the next request, or "" if there are none."""
        return self._keys[self._index] if self._keys else ""

    def label(self) -> str:
        """How to name the current key in a log, without printing it."""
        if not self._keys:
            return "no key"
        if len(self._keys) == 1:
            return "the key"
        return f"key #{self._index + 1} of {len(self._keys)}"

    def _available(self, position: int) -> bool:
        if position in self._dead:
            return False
        until = self._cooling.get(position)
        return until is None or self._clock() >= until

    def _move_to_next_available(self) -> bool:
        for step in range(1, len(self._keys) + 1):
            position = (self._index + step) % len(self._keys)
            if self._available(position):
                self._index = position
                return True
        return False

    def rate_limited(self, seconds: float = 0.0) -> bool:
        """Parks the current key and moves on. True if another was free.

        False means every key is spent, which is a real answer and not an
        error: the caller should wait rather than hammer them in turn.
        """
        if not self._keys:
            return False
        with self._lock:
            return self._park(seconds)

    def _park(self, seconds: float) -> bool:
        wait = seconds if seconds > 0 else DEFAULT_COOLDOWN_SECONDS
        self._cooling[self._index] = self._clock() + min(wait, MAX_COOLDOWN_SECONDS)
        return self._move_to_next_available()

    def rejected(self) -> bool:
        """Retires the current key for this session and moves on.

        A revoked or mistyped key will be refused every time, so unlike a
        rate limit this has no cooldown to wait out. It is not deleted from
        keys.json -- that is the user's file, and a key can be re-enabled by
        fixing it there and restarting.
        """
        if not self._keys:
            return False
        with self._lock:
            self._dead.add(self._index)
            return self._move_to_next_available()

    def exhausted(self) -> bool:
        """True when no key can be used right now."""
        return not any(self._available(i) for i in range(len(self._keys)))

    def status(self) -> str:
        """A one-line summary for logs. Positions only, never values."""
        if not self._keys:
            return "no keys configured"
        parts = []
        for position in range(len(self._keys)):
            if position in self._dead:
                state = "rejected"
            elif not self._available(position):
                left = self._cooling[position] - self._clock()
                state = f"cooling {max(0, int(left))}s"
            else:
                state = "ready"
            marker = " <-" if position == self._index else ""
            parts.append(f"#{position + 1} {state}{marker}")
        return ", ".join(parts)
