"""Ultron must not say music is playing when it is not.

`search_spotify` opens Spotify's search results — a list, sitting there. The
model, reading the tool's name and its old return value ("Successfully opened
Spotify and searched for X"), filled in the rest and told the user "playback
should begin shortly". Nothing was playing.

A confident false confirmation is the worst outcome available here: the user
believes the request worked and only finds out when the silence continues.
"""

import pytest

from ultron import automation


@pytest.fixture
def opened(monkeypatch):
    """Captures the launch command instead of actually opening Spotify."""
    launched = []
    monkeypatch.setattr(automation.subprocess, "Popen",
                        lambda cmd, **kw: launched.append(cmd))
    return launched


class TestWhatTheToolReports:
    def test_it_still_opens_the_search(self, opened):
        automation.search_spotify("Arijit Singh")

        assert len(opened) == 1
        assert "spotify:search:" in opened[0]
        assert "Arijit" in opened[0]

    def test_the_result_says_nothing_is_playing(self, opened):
        result = automation.search_spotify("Arijit Singh")

        assert "NOTHING IS PLAYING" in result

    @pytest.mark.parametrize("claim", [
        "successfully", "playing", "started", "now playing", "began",
    ])
    def test_the_result_never_claims_playback(self, opened, claim):
        result = automation.search_spotify("Arijit Singh").lower()

        # "nothing is playing" is the one allowed use of the word.
        stripped = result.replace("nothing is playing", "")
        assert claim not in stripped, f"the tool result implies playback: {result!r}"

    def test_a_failure_is_reported_as_a_failure(self, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("spotify is not installed")

        monkeypatch.setattr(automation.subprocess, "Popen", explode)
        result = automation.search_spotify("Arijit Singh")

        assert result.startswith("Failed")
        assert "not installed" in result


class TestWhatTheModelIsTold:
    def test_the_docstring_does_not_promise_playback(self):
        """It is the tool description the model actually reads."""
        doc = automation.search_spotify.__doc__.lower()

        assert "does not start playing" in doc

    def test_the_system_prompt_spells_it_out(self, brain):
        prompt = brain._build_local_system_prompt("now", False).lower()

        assert "does not start playback" in prompt
        assert "never tell the user music is playing" in prompt


class TestPrintingToolResults:
    """A search result with an emoji in it used to kill the whole turn.

    Piped into a file, stdout drops to cp1252, which cannot represent an
    emoji. Tool results are printed mid-turn, so the UnicodeEncodeError came
    back to the user as "Error communicating with brain" — on music requests
    especially, where the web search results are full of them.
    """

    def test_an_emoji_does_not_take_down_the_stream(self, capsysbinary, monkeypatch):
        import io
        import sys

        from ultron.launcher import make_output_safe

        pipe = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        monkeypatch.setattr(sys, "stdout", pipe)
        monkeypatch.setattr(sys, "stderr", pipe)

        with pytest.raises(UnicodeEncodeError):
            print("[Tool Result] top lofi 🎧")

        make_output_safe()
        print("[Tool Result] top lofi 🎧")  # must not raise

    def test_both_entry_points_harden_their_output(self):
        import inspect
        import pathlib

        from ultron import launcher

        assert "make_output_safe()" in inspect.getsource(
            launcher.redirect_output_if_headless)

        root = pathlib.Path(__file__).resolve().parent.parent
        assert "make_output_safe()" in (root / "main.py").read_text(encoding="utf-8")
