"""Driving the user's own Chrome instead of launching a second one.

Playwright starts its own Chromium against data/browser_profile: different
logins, different cookies, and a cold browser launch before anything happens.
The user's real Chrome is already open and already signed in.

The cost of using it is that keystrokes go wherever focus is, so every test
here is about not typing at the wrong moment or in the wrong place. Nothing
opens a browser and nothing touches the real keyboard.
"""

import pytest

from ultron import chrome


class Keys:
    """Records keystrokes and typing instead of performing them."""

    def __init__(self):
        self.pressed = []
        self.typed = []

    def press(self, *combo):
        self.pressed.append(combo)

    def write(self, text):
        self.typed.append(text)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def sleep(self, seconds):
        self.now += seconds

    def read(self):
        return self.now


class FakeClipboard:
    def __init__(self, existing="the user's own clipboard", page=""):
        self.contents = existing
        self.page = page
        self.history = []

    def copy(self, text):
        self.contents = text
        self.history.append(text)

    def paste(self):
        return self.contents


class TestKnowingWhenThePageArrived:
    def test_it_waits_for_the_title_to_settle(self):
        """Chrome rewrites the title several times per navigation. What
        matters is not that it changed but that it stopped."""
        clock = Clock()
        titles = iter(["Loading...", "Loading...", "example.com",
                       "Onam - Wikipedia"] + ["Onam - Wikipedia"] * 20)

        result = chrome.wait_for_page(sleep=clock.sleep, clock=clock.read,
                                      title_of=lambda: next(titles))

        assert result == "Onam - Wikipedia"

    def test_a_page_that_never_settles_still_returns(self):
        """A live-updating page must not hang the assistant forever."""
        clock = Clock()
        count = {"n": 0}

        def churning():
            count["n"] += 1
            return f"title {count['n']}"

        chrome.wait_for_page(sleep=clock.sleep, clock=clock.read,
                             title_of=churning, timeout=20.0)

        assert clock.now - 1000.0 <= 21.0, "it waited past its own timeout"


class TestTabs:
    def test_work_goes_into_a_new_tab(self):
        """Typing into the user's current tab navigates away from whatever
        they were reading, and Back is the only undo."""
        keys = Keys()
        chrome.open_tab(keys.press, sleep=lambda s: None)

        assert ("ctrl", "t") in keys.pressed

    def test_the_remembered_tab_is_found_at_the_end(self):
        """Ultron's tab is usually the newest, and Ctrl+9 is the last tab."""
        keys = Keys()
        found = chrome.find_tab("Onam - Wikipedia", keys.press,
                                sleep=lambda s: None,
                                title_of=lambda: "Onam - Wikipedia - Google Chrome")

        assert found is True
        assert ("ctrl", "9") in keys.pressed
        assert ("ctrl", "tab") not in keys.pressed, "it cycled unnecessarily"

    def test_it_walks_the_tabs_when_the_last_one_is_not_it(self):
        keys = Keys()
        seen = {"n": 0}

        def title():
            seen["n"] += 1
            return ("Onam - Wikipedia" if seen["n"] > 3 else "Gmail")

        assert chrome.find_tab("Onam", keys.press, sleep=lambda s: None,
                               title_of=title) is True
        assert ("ctrl", "tab") in keys.pressed

    def test_a_tab_that_is_gone_is_reported_missing(self):
        """It renamed itself, or the user closed it. Either way: new tab."""
        keys = Keys()
        assert chrome.find_tab("Onam", keys.press, sleep=lambda s: None,
                               title_of=lambda: "Gmail") is False

    def test_cycling_is_bounded(self):
        keys = Keys()
        chrome.find_tab("nowhere", keys.press, sleep=lambda s: None,
                        title_of=lambda: "Gmail")

        cycles = [k for k in keys.pressed if k == ("ctrl", "tab")]
        assert len(cycles) <= chrome.MAX_TAB_CYCLES

    def test_nothing_remembered_means_no_hunting(self):
        keys = Keys()
        assert chrome.find_tab("", keys.press, title_of=lambda: "x") is False
        assert chrome.find_tab(None, keys.press, title_of=lambda: "x") is False
        assert keys.pressed == []


class TestReadingThePage:
    def test_the_page_text_comes_back(self):
        keys = Keys()
        clip = FakeClipboard()

        def press(*combo):
            keys.press(*combo)
            if combo == ("ctrl", "c"):
                clip.contents = "Onam is celebrated in Kerala."

        text = chrome.read_page(press, sleep=lambda s: None, clipboard=clip)

        assert text == "Onam is celebrated in Kerala."
        assert ("ctrl", "a") in keys.pressed

    def test_the_users_clipboard_is_given_back(self):
        keys = Keys()
        clip = FakeClipboard()

        def press(*combo):
            if combo == ("ctrl", "c"):
                clip.contents = "page text"

        chrome.read_page(press, sleep=lambda s: None, clipboard=clip)

        assert clip.contents == "the user's own clipboard"

    def test_a_failed_copy_does_not_return_the_old_clipboard(self):
        """Without clearing first, a silent copy failure would hand back
        whatever the user had copied and call it the page."""
        clip = FakeClipboard(existing="my bank password")

        text = chrome.read_page(lambda *k: None, sleep=lambda s: None,
                                clipboard=clip)

        assert text == "", "the user's clipboard was returned as page content"

    def test_the_selection_is_cleared_afterwards(self):
        """Otherwise the user is left with the whole page highlighted."""
        keys = Keys()
        chrome.read_page(keys.press, sleep=lambda s: None,
                         clipboard=FakeClipboard())

        assert ("ctrl", "shift", "home") in keys.pressed


class TestItKnowsWhetherChromeIsInFront:
    @pytest.mark.parametrize("title,expected", [
        ("Onam - Wikipedia - Google Chrome", True),
        ("Google Chrome", True),
        ("Visual Studio Code", False),
        ("", False),
    ])
    def test_foreground_detection(self, title, expected):
        assert chrome.is_chrome_in_front(title_of=lambda: title) is expected


class TestRoutinesMayNotTakeTheKeyboard:
    """The reason this needed a new guard rather than the existing one.

    `unattended` only ever blocked DESTRUCTIVE_TOOLS, so a routine could
    already drive the keyboard through write_in_notepad or WhatsApp -- at
    9am, into whatever the user happened to be typing in.
    """

    @pytest.mark.parametrize("tool", [
        "chrome_search", "chrome_open", "chrome_read_page",
        "send_whatsapp_message", "write_in_notepad",
    ])
    def test_screen_tools_are_refused_unattended(self, brain, tool):
        brain.unattended = True
        result = brain._invoke_tool(tool, {"query": "x", "url": "x",
                                           "text": "x", "contact_name": "x",
                                           "message": "x"})

        assert result.startswith("Error")
        assert "keyboard" in result

    def test_it_says_what_to_do_instead(self, brain):
        brain.unattended = True
        result = brain._invoke_tool("chrome_search", {"query": "onam"})

        assert "Report what you found" in result

    def test_harmless_tools_still_run_in_routines(self, brain):
        """The guard must not turn every routine into a no-op."""
        brain.unattended = True
        assert not brain._invoke_tool("get_system_health", {}).startswith("Error")

    def test_every_keyboard_driving_tool_is_listed(self, brain):
        """A new one added without joining the set would run unattended."""
        from ultron.brain import SCREEN_TOOLS

        import inspect
        from ultron import automation

        suspects = set()
        for name, func in brain.tool_functions.items():
            try:
                source = inspect.getsource(func)
            except (OSError, TypeError):
                continue
            if "pyautogui" in source and "hotkey" in source:
                suspects.add(name)

        missed = suspects - SCREEN_TOOLS
        assert not missed, (
            f"these press keys but are not in SCREEN_TOOLS, so a routine "
            f"could run them while the user is typing: {sorted(missed)}")


class TestTheUserIsWarnedFirst:
    def test_a_screen_tool_announces_before_it_starts(self):
        """Reported afterwards is useless: by then the keys have flown."""
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._on_tool_event)
        assert "SCREEN_TOOLS" in source
        assert "Hands off" in source
        assert source.index("Hands off") < source.index("_active_tool"), (
            "the warning must be queued before the tool is marked as running")
