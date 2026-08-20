"""Not typing a message into whatever window happens to be in front.

The old path launched WhatsApp, slept three seconds, and started pressing
keys. On a cold start -- or on a machine already busy running a local model
-- WhatsApp is still on its splash screen when three seconds are up, and the
keystrokes go wherever focus actually is: a document, a browser, a terminal.
Ctrl+F, paste, Enter, paste, Enter.

Every test here is about refusing to type. Nothing opens WhatsApp, and
nothing touches the real keyboard.
"""

import pytest

from ultron import desktop_window as ww


class FakeWindow:
    def __init__(self, title="WhatsApp"):
        self.title = title
        self.activated = 0

    def activate(self):
        self.activated += 1

    def restore(self):
        pass


class Clock:
    """A clock that only moves when something sleeps."""

    def __init__(self):
        self.now = 1000.0

    def sleep(self, seconds):
        self.now += seconds

    def read(self):
        return self.now


class TestFindingTheWindow:
    def _windows(self, monkeypatch, titles):
        fake = type("gw", (), {
            "getAllWindows": staticmethod(
                lambda: [FakeWindow(t) for t in titles])})
        monkeypatch.setitem(__import__("sys").modules, "pygetwindow", fake)

    def test_it_finds_whatsapp(self, monkeypatch):
        self._windows(monkeypatch, ["Notepad", "WhatsApp"])
        assert ww.find_window().title == "WhatsApp"

    def test_no_whatsapp_means_none(self, monkeypatch):
        self._windows(monkeypatch, ["Notepad", "Chrome"])
        assert ww.find_window() is None

    def test_the_real_app_beats_a_browser_tab(self, monkeypatch):
        """"WhatsApp Web - Google Chrome" contains the word and is not the
        application. Typing into it would be its own kind of wrong."""
        self._windows(monkeypatch,
                      ["WhatsApp Web - Google Chrome", "WhatsApp"])
        assert ww.find_window().title == "WhatsApp"

    def test_a_missing_window_library_is_not_a_crash(self, monkeypatch):
        monkeypatch.setitem(__import__("sys").modules, "pygetwindow", None)
        assert ww.find_window() is None


class TestWaitingRatherThanGuessing:
    def test_it_waits_for_a_slow_cold_start(self):
        """Three seconds was a guess. This waits as long as the machine needs."""
        clock = Clock()
        appears_at = clock.now + 12.0
        window = ww.wait_for_window(
            timeout=30.0,
            finder=lambda: FakeWindow() if clock.read() >= appears_at else None,
            sleep=clock.sleep, clock=clock.read)

        assert window is not None
        assert clock.now >= appears_at, "it returned before the window existed"

    def test_it_gives_up_rather_than_waiting_forever(self):
        clock = Clock()
        window = ww.wait_for_window(timeout=30.0, finder=lambda: None,
                                    sleep=clock.sleep, clock=clock.read)

        assert window is None
        assert clock.now <= 1000.0 + 31.0, "it waited past its own timeout"

    def test_an_already_open_window_is_returned_at_once(self):
        clock = Clock()
        ww.wait_for_window(timeout=30.0, finder=lambda: FakeWindow(),
                           sleep=clock.sleep, clock=clock.read)

        assert clock.now == 1000.0, "it slept even though the window was there"


class TestFocusIsConfirmedNotAssumed:
    def test_focus_is_verified_against_the_real_foreground(self):
        clock = Clock()
        window = FakeWindow()
        assert ww.focus(window, sleep=clock.sleep, clock=clock.read,
                        title_of=lambda: "WhatsApp") is True

    def test_a_window_that_never_comes_forward_is_reported(self):
        """Asking is not arriving. A modal dialog or a fullscreen game can
        refuse, and the next thing that happens is typing."""
        clock = Clock()
        assert ww.focus(FakeWindow(), sleep=clock.sleep, clock=clock.read,
                        title_of=lambda: "Some Game") is False

    def test_it_keeps_trying_before_giving_up(self):
        clock = Clock()
        window = FakeWindow()
        arrives_at = clock.now + 2.0

        result = ww.focus(
            window, sleep=clock.sleep, clock=clock.read,
            title_of=lambda: "WhatsApp" if clock.read() >= arrives_at else "Other")

        assert result is True

    def test_a_window_without_a_title_is_refused(self):
        clock = Clock()
        assert ww.focus(FakeWindow(""), sleep=clock.sleep, clock=clock.read,
                        title_of=lambda: "") is False


class TestTheGateAsAWhole:
    def test_an_open_window_is_not_relaunched(self):
        clock = Clock()
        launched = []
        window, problem = ww.ensure_ready(
            lambda: launched.append(True), finder=lambda: FakeWindow(),
            sleep=clock.sleep, clock=clock.read, title_of=lambda: "WhatsApp")

        assert problem is None and window is not None
        assert launched == [], "it relaunched an app that was already open"

    def test_a_warm_window_settles_faster_than_a_cold_one(self):
        """A window that was merely raised is ready; one just launched is
        still loading chats."""
        warm = Clock()
        ww.ensure_ready(lambda: None, finder=lambda: FakeWindow(),
                        sleep=warm.sleep, clock=warm.read,
                        title_of=lambda: "WhatsApp")

        cold = Clock()
        seen = {"n": 0}

        def after_launch():
            seen["n"] += 1
            return None if seen["n"] == 1 else FakeWindow()

        ww.ensure_ready(lambda: None, finder=after_launch, sleep=cold.sleep,
                        clock=cold.read, title_of=lambda: "WhatsApp")

        assert cold.now - 1000.0 > warm.now - 1000.0

    def test_a_launch_that_never_appears_refuses_to_type(self):
        clock = Clock()
        window, problem = ww.ensure_ready(
            lambda: None, timeout=30.0, finder=lambda: None,
            sleep=clock.sleep, clock=clock.read, title_of=lambda: "Notepad")

        assert window is None
        assert "did not open" in problem
        assert "no message was sent" in problem

    def test_a_window_that_will_not_focus_refuses_to_type(self):
        clock = Clock()
        window, problem = ww.ensure_ready(
            lambda: None, finder=lambda: FakeWindow(),
            sleep=clock.sleep, clock=clock.read,
            title_of=lambda: "Visual Studio Code")

        assert window is None
        assert "no message was sent" in problem
        assert "Visual Studio Code" in problem, (
            "knowing what stole focus is how this gets diagnosed")


class TestFocusLostDuringTheSettle:
    """The gap this missed for real, on the user's machine.

    ensure_ready confirmed focus, slept three seconds to let a cold WhatsApp
    finish loading, and then reported ready -- on the strength of a check
    that was by then three seconds stale. Observed live: it returned
    (window, None) while the IDE actually held focus, so the caller's first
    keystroke went into an editor.

    Three seconds is ample for the launching window to take focus back, for
    a notification to steal it, or for the user to alt-tab.
    """

    def _lost_after(self, seconds, clock, regains=False):
        """A foreground that is WhatsApp, then something else."""
        start = clock.read()

        def title():
            if clock.read() - start < seconds:
                return "WhatsApp"
            return "WhatsApp" if regains else "Visual Studio Code"

        return title

    def test_focus_lost_while_loading_is_caught(self):
        clock = Clock()
        window, problem = ww.ensure_ready(
            lambda: None, finder=lambda: FakeWindow(),
            sleep=clock.sleep, clock=clock.read,
            title_of=self._lost_after(0.2, clock))

        assert window is None, "it reported ready with another window in front"
        assert "no message was sent" in problem

    def test_the_thief_is_named(self):
        """Knowing what took focus is how this gets diagnosed at all."""
        clock = Clock()
        _window, problem = ww.ensure_ready(
            lambda: None, finder=lambda: FakeWindow(),
            sleep=clock.sleep, clock=clock.read,
            title_of=self._lost_after(0.2, clock))

        assert "Visual Studio Code" in problem

    def test_an_app_that_raises_itself_late_is_not_refused(self):
        """WhatsApp coming to the front a moment after being asked is normal.
        Refusing outright would trade a wrong-window bug for a tool that
        rarely works."""
        clock = Clock()
        start = clock.read()

        def title():
            return "Other" if clock.read() - start < 1.0 else "WhatsApp"

        window, problem = ww.ensure_ready(
            lambda: None, finder=lambda: FakeWindow(),
            sleep=clock.sleep, clock=clock.read, title_of=title)

        assert problem is None and window is not None

    def test_focus_held_throughout_still_succeeds(self):
        clock = Clock()
        window, problem = ww.ensure_ready(
            lambda: None, finder=lambda: FakeWindow(),
            sleep=clock.sleep, clock=clock.read, title_of=lambda: "WhatsApp")

        assert problem is None and window is not None


class TestTheToolItself:
    """Driven with fakes: no window is opened and no key is pressed."""

    def _patch(self, monkeypatch, ready=(FakeWindow(), None),
               foreground="WhatsApp"):
        import ultron.automation as auto

        pressed, copied = [], []
        fake_gui = type("g", (), {
            "hotkey": staticmethod(lambda *k: pressed.append(k)),
            "press": staticmethod(lambda k: pressed.append((k,))),
        })
        fake_clip = type("c", (), {
            "copy": staticmethod(lambda t: copied.append(t)),
            "paste": staticmethod(lambda: "the user's own clipboard"),
        })
        monkeypatch.setitem(__import__("sys").modules, "pyautogui", fake_gui)
        monkeypatch.setitem(__import__("sys").modules, "pyperclip", fake_clip)
        monkeypatch.setattr(ww, "ensure_ready", lambda *a, **k: ready)
        monkeypatch.setattr(ww, "foreground_title", lambda: foreground)
        monkeypatch.setattr(auto, "_release_quietly", lambda: None)
        return pressed, copied

    def test_nothing_is_typed_when_whatsapp_never_opened(self, monkeypatch):
        from ultron.automation import send_whatsapp_message

        pressed, _copied = self._patch(
            monkeypatch, ready=(None, "WhatsApp did not open in time, "
                                      "so no message was sent."))

        result = send_whatsapp_message("Amma", "on my way")

        assert result.startswith("Error")
        assert pressed == [], "keys were pressed with WhatsApp not in front"

    def test_a_message_is_typed_when_it_is_ready(self, monkeypatch):
        from ultron.automation import send_whatsapp_message

        pressed, copied = self._patch(monkeypatch)
        send_whatsapp_message("Amma", "on my way")

        assert ("ctrl", "f") in pressed
        assert "Amma" in copied and "on my way" in copied

    def test_focus_lost_mid_way_stops_before_the_message(self, monkeypatch):
        """The chat opens, then a notification steals focus. The message must
        not be typed into whatever took it."""
        from ultron.automation import send_whatsapp_message

        pressed, copied = self._patch(monkeypatch, foreground="Slack")
        result = send_whatsapp_message("Amma", "on my way")

        assert result.startswith("Error") and "Slack" in result
        assert "on my way" not in copied, "the message was typed anyway"

    def test_the_clipboard_is_given_back(self, monkeypatch):
        """Two pastes used to overwrite whatever the user had copied."""
        from ultron.automation import send_whatsapp_message

        _pressed, copied = self._patch(monkeypatch)
        send_whatsapp_message("Amma", "on my way")

        assert copied[-1] == "the user's own clipboard"

    def test_the_clipboard_is_restored_even_when_it_bails_out(self, monkeypatch):
        from ultron.automation import send_whatsapp_message

        _pressed, copied = self._patch(monkeypatch, foreground="Slack")
        send_whatsapp_message("Amma", "on my way")

        assert copied[-1] == "the user's own clipboard"

    @pytest.mark.parametrize("contact,message", [
        ("", "hello"), ("   ", "hello"), ("Amma", ""), ("Amma", "   "),
    ])
    def test_empty_input_is_refused_before_anything_opens(
            self, monkeypatch, contact, message):
        from ultron.automation import send_whatsapp_message

        pressed, _copied = self._patch(monkeypatch)
        result = send_whatsapp_message(contact, message)

        assert result.startswith("Error")
        assert pressed == []

    def test_it_does_not_claim_delivery_to_a_person(self, monkeypatch):
        """Which chat the search highlighted is never checked, so asserting
        it reached a named person claims more than is known."""
        from ultron.automation import send_whatsapp_message

        self._patch(monkeypatch)
        result = send_whatsapp_message("Amma", "on my way")

        assert "Successfully sent" not in result
        assert "check it went to the right chat" in result
