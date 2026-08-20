"""Knowing whether WhatsApp Desktop is actually ready to be typed into.

The old code launched WhatsApp, slept three seconds, and then began pressing
keys. Three seconds is a guess, and on a cold start -- or on a machine busy
running a local model -- WhatsApp is still showing its splash screen and
loading chats when the guess expires.

What happens then is the problem. The keystrokes are not queued for WhatsApp;
they go to whatever window has focus. Ctrl+F, paste, Enter, paste, Enter,
into a document, a browser, a terminal. The message text is typed somewhere
it was never meant to go, and Enter is pressed twice.

So nothing here sleeps and hopes. It waits for a real window, brings it to
the front, and confirms that it actually arrived there. Every step can fail,
and a failure means "do not type", which is the whole point.

The window lookup is injectable so this can be tested without WhatsApp
installed and without stealing focus from whoever is running the tests.
"""

import ctypes
import time

# A cold start pulls a large Electron app off disk and then syncs. Generous,
# because the cost of waiting is a pause and the cost of giving up early is a
# message typed into the wrong window.
LAUNCH_TIMEOUT_SECONDS = 30.0

# How often to look while waiting.
POLL_SECONDS = 0.5

# After the window is in front, the interface still needs a moment to accept
# input. A cold start needs meaningfully longer than a window that was
# already open and merely had to be raised.
COLD_SETTLE_SECONDS = 3.0
WARM_SETTLE_SECONDS = 0.5

# How long to keep trying to bring the window forward once it exists.
FOCUS_TIMEOUT_SECONDS = 5.0


def find_window(title_hint: str = "whatsapp"):
    """The WhatsApp Desktop window, or None if it is not open.

    Matched on title because that is what is available without a dependency
    on the process table. An exact title wins over a partial one: a browser
    tab called "WhatsApp Web - Google Chrome" contains the word but is not
    the application, and typing into it would be its own kind of wrong.
    """
    try:
        import pygetwindow as gw
    except Exception as e:
        print(f"[WhatsApp] cannot inspect windows: {e}")
        return None

    try:
        hint = title_hint.lower()
        matches = [w for w in gw.getAllWindows()
                   if w.title and hint in w.title.lower()]
    except Exception as e:
        print(f"[WhatsApp] could not list windows: {e}")
        return None

    if not matches:
        return None
    for window in matches:
        if window.title.strip().lower() == hint:
            return window
    return matches[0]


def foreground_title() -> str:
    """The title of whatever window currently has focus."""
    try:
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        if not handle:
            return ""
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value or ""
    except Exception as e:
        print(f"[WhatsApp] could not read the focused window: {e}")
        return ""


def wait_for_window(timeout: float = LAUNCH_TIMEOUT_SECONDS,
                    finder=None, sleep=time.sleep, clock=time.monotonic):
    """Polls until the window exists, or gives up and returns None.

    Polling rather than sleeping is the difference between waiting exactly as
    long as this machine needs and guessing a number that is too short on a
    cold start and wasteful on a warm one.
    """
    finder = finder or find_window
    deadline = clock() + timeout
    while True:
        window = finder()
        if window is not None:
            return window
        if clock() >= deadline:
            return None
        sleep(POLL_SECONDS)


def focus(window, timeout: float = FOCUS_TIMEOUT_SECONDS,
          sleep=time.sleep, clock=time.monotonic, title_of=None) -> bool:
    """Brings the window to the front and confirms it got there.

    Asking is not the same as arriving: a modal dialog elsewhere, a full
    screen game, or Windows' own focus-stealing rules can all refuse the
    request. Since the next thing that happens is typing, the answer has to
    be checked rather than assumed.
    """
    title_of = title_of or foreground_title
    wanted = (getattr(window, "title", "") or "").strip().lower()
    if not wanted:
        return False

    deadline = clock() + timeout
    asked = False
    while True:
        if title_of().strip().lower() == wanted:
            return True
        if not asked or clock() < deadline:
            for method in ("restore", "activate"):
                try:
                    action = getattr(window, method, None)
                    if action:
                        action()
                except Exception:
                    # Minimised windows raise from restore(); activate() can
                    # fail outright. Neither is fatal on its own, because the
                    # foreground check below is what actually decides.
                    pass
            asked = True
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)


def ensure_ready(launch, timeout: float = LAUNCH_TIMEOUT_SECONDS,
                 finder=None, sleep=time.sleep, clock=time.monotonic,
                 title_of=None):
    """Gets WhatsApp open and focused, or explains why it could not.

    Returns (window, None) when it is safe to type, or (None, reason).

    Whether it was already open matters: a window that was merely raised is
    ready almost at once, while one that has just been launched is still
    loading chats and would swallow the first keystrokes.
    """
    finder = finder or find_window

    window = finder()
    cold = window is None
    if cold:
        launch()
        window = wait_for_window(timeout, finder=finder, sleep=sleep,
                                 clock=clock)
        if window is None:
            return None, (f"WhatsApp did not open within {timeout:.0f} seconds, "
                          f"so nothing was typed and no message was sent.")

    if not focus(window, sleep=sleep, clock=clock, title_of=title_of):
        front = (title_of or foreground_title)()
        return None, (f"WhatsApp is open but would not come to the front"
                      f"{f' - {front!r} kept focus' if front else ''}, so "
                      f"nothing was typed and no message was sent.")

    sleep(COLD_SETTLE_SECONDS if cold else WARM_SETTLE_SECONDS)
    return window, None
