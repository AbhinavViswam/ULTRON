"""Driving the user's own Chrome, by keyboard, instead of a second browser.

Playwright launches its own Chromium against a profile in data/browser_profile.
That browser is not the one on screen: different logins, different history,
different cookies, and a cold launch every session before anything happens.
Meanwhile the user's real Chrome is already open, already signed in, and
already warm.

So this drives that one, the way a person would: focus the window, Ctrl+T for
a tab, Ctrl+L for the address bar, type, Enter. Reading the page is Ctrl+A and
Ctrl+C -- the clipboard is the only channel out of a browser that needs no
extension, no debugging port, and no DOM.

Three things this cannot pretend about.

It takes the screen. Keystrokes go wherever focus is, so while this runs the
keyboard belongs to Ultron. That is why callers announce it, and why it is
refused outright when nobody is watching.

Chrome exposes no tab handles. The window title is the *active* tab's title
and nothing else, so a tab is identified by remembering what it was called
and looking for that name again. It is a heuristic, and it says so.

Ctrl+A copies rendered text. It is excellent on articles and poor on
applications that draw their own canvas.
"""

import time

from ultron import desktop_window

# Chrome names its window "<page title> - Google Chrome".
WINDOW_HINT = "google chrome"

# After Enter in the address bar, how long to let a page arrive. Polling the
# title is what actually decides; this only bounds the wait.
PAGE_LOAD_TIMEOUT_SECONDS = 20.0
PAGE_POLL_SECONDS = 0.4

# The title has to stop changing for this long before a page counts as
# settled. Chrome sets it several times during a load - "Loading...", then
# the URL, then the real title.
TITLE_STABLE_SECONDS = 1.2

# Ctrl+Tab this many times when hunting for a remembered tab before giving up
# and opening a fresh one. More than this and the flicker costs more than the
# tab is worth.
MAX_TAB_CYCLES = 12


def _title_now():
    return desktop_window.foreground_title()


def is_chrome_in_front(title_of=None) -> bool:
    """True when Chrome currently holds focus."""
    title = (title_of or _title_now)()
    return WINDOW_HINT in (title or "").lower()


def ensure_chrome(launch, finder=None, sleep=time.sleep,
                  clock=time.monotonic, title_of=None):
    """Chrome, focused and ready, or (None, reason).

    Reuses the window that is already open rather than starting anything,
    which is the entire point: the user's session is in that one.
    """
    return desktop_window.ensure_ready(
        launch,
        finder=finder or (lambda: desktop_window.find_window(WINDOW_HINT)),
        sleep=sleep, clock=clock, title_of=title_of)


def wait_for_page(press=None, sleep=time.sleep, clock=time.monotonic,
                  title_of=None, timeout: float = PAGE_LOAD_TIMEOUT_SECONDS):
    """Waits until the window title stops changing, and returns it.

    There is no load event to listen for from outside the browser, so the
    title is the signal available. Chrome rewrites it several times during a
    navigation, so what matters is not that it changed but that it has
    stopped: a title that holds still for a beat means the page arrived.
    """
    title_of = title_of or _title_now
    deadline = clock() + timeout
    last = object()
    steady_since = None

    while clock() < deadline:
        current = title_of()
        if current != last:
            last = current
            steady_since = clock()
        elif steady_since is not None and clock() - steady_since >= TITLE_STABLE_SECONDS:
            return current
        sleep(PAGE_POLL_SECONDS)
    return title_of()


def open_tab(press, sleep=time.sleep):
    """A new tab, because Ultron's work does not belong in the user's tab.

    Typing into whatever tab happened to be open would navigate away from
    what they were reading, and there is no undo for that beyond Back.
    """
    press("ctrl", "t")
    sleep(0.4)


def go_to(text, press, write, sleep=time.sleep):
    """Puts *text* in the address bar and presses Enter.

    A URL navigates; anything else Chrome treats as a search with the user's
    default engine, which is the behaviour wanted for both cases.
    """
    press("ctrl", "l")
    sleep(0.3)
    write(text)
    sleep(0.2)
    press("enter")


def find_tab(remembered_title, press, sleep=time.sleep, title_of=None):
    """Switches to the tab Ultron last used, if it is still there.

    Chrome gives no way to enumerate or address tabs from outside, so this
    does what a person would: jump to the last tab, and failing that walk
    through them looking at names. A tab renames itself as it navigates, so
    a miss here is expected and simply means a new tab gets opened.
    """
    title_of = title_of or _title_now
    if not remembered_title:
        return False

    wanted = remembered_title.strip().lower()
    if not wanted:
        return False

    # Ultron's tab is usually the newest, and Ctrl+9 is the last tab.
    press("ctrl", "9")
    sleep(0.3)
    if wanted in (title_of() or "").lower():
        return True

    for _ in range(MAX_TAB_CYCLES):
        press("ctrl", "tab")
        sleep(0.25)
        if wanted in (title_of() or "").lower():
            return True
    return False


def read_page(press, sleep=time.sleep, clipboard=None):
    """The page's text, via select-all and copy.

    The clipboard is the user's, so it is saved and handed back. Returning
    text while quietly destroying what they had copied would be a poor trade.
    """
    if clipboard is None:
        import pyperclip as clipboard

    try:
        saved = clipboard.paste()
    except Exception:
        saved = None

    try:
        # A sentinel: if the copy silently fails, the old contents would be
        # returned as though they were the page.
        clipboard.copy("")
        press("ctrl", "a")
        sleep(0.3)
        press("ctrl", "c")
        sleep(0.5)
        text = clipboard.paste() or ""
    finally:
        # Deselect, so the user is not left with the whole page highlighted.
        try:
            press("ctrl", "shift", "home")
        except Exception:
            # Cosmetic only; the text has already been captured.
            pass
        if saved is not None:
            try:
                clipboard.copy(saved)
            except Exception:
                # Restoring is a courtesy, not the job.
                pass

    return text.strip()
