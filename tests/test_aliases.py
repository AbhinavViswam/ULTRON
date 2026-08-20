"""Commands that skip the model entirely.

"Pause" took fifty seconds on this machine, because every utterance went to a
local model running mostly on the CPU. Nothing about "pause" needs a model.

Speed is the easy half. The hard half is that an alias also skips the
reasoning that would question a bad instruction, so most of these tests are
about what must *not* match.
"""

import pytest

from ultron import aliases
from ultron.aliases import ALIASES, normalise, resolve


class TestThePhrasesPeopleActuallySay:
    @pytest.mark.parametrize("said,action", [
        ("pause", "pause"),
        ("Pause.", "pause"),
        ("Ultron, pause please", "pause"),
        ("hey ultron could you pause the music", "pause"),
        ("next song", "next"),
        ("skip", "next"),
        ("previous track", "prev"),
        ("resume", "play"),
    ])
    def test_media_commands_resolve(self, said, action):
        assert resolve(said) == ("system_media_control", {"action": action})

    @pytest.mark.parametrize("said,action", [
        ("volume up", "volume_up"),
        ("louder", "volume_up"),
        ("turn down the volume", "volume_down"),
        ("mute", "mute"),
    ])
    def test_volume_commands_resolve(self, said, action):
        assert resolve(said) == ("adjust_volume", {"action": action})

    @pytest.mark.parametrize("said,tool", [
        ("what are my reminders", "list_reminders"),
        ("show routines", "list_routines"),
        ("battery", "get_system_health"),
        ("check the battery please", "get_system_health"),
        ("what do you remember about me", "list_memories"),
        ("my keys are stuck", "release_stuck_keys"),
    ])
    def test_lookups_resolve(self, said, tool):
        assert resolve(said) == (tool, {})

    def test_the_returned_arguments_cannot_be_mutated_into_the_table(self):
        """A caller editing its args must not rewrite the alias for everyone."""
        _tool, args = resolve("pause")
        args["action"] = "vandalised"

        assert resolve("pause")[1] == {"action": "pause"}


class TestWhatMustNeverMatch:
    """A wrong alias fires something the user did not ask for. That is worse
    than a slow answer, so anything short of certain goes to the model."""

    @pytest.mark.parametrize("said", [
        "why did you pause",
        "do not pause",
        "remind me to pause the recording at 5",
        "pause the video in chrome and then close it",
        "what does pause do",
        "can you explain how volume up works",
        "play arijit singh",
        "play some music",
        "delete the file",
        "empty the recycle bin",
        "shut down the pc",
        "send an email to my wife",
        "",
        "   ",
    ])
    def test_these_go_to_the_model(self, said):
        assert resolve(said) is None, f"{said!r} must not be shortcut"

    def test_stop_is_deliberately_absent(self):
        """It reads as 'stop talking' as often as 'stop the music'."""
        assert resolve("stop") is None

    def test_a_sentence_containing_an_alias_is_not_an_alias(self):
        """Matching is whole-phrase; containment would fire constantly."""
        assert resolve("i was going to pause but changed my mind") is None

    def test_no_destructive_tool_is_reachable(self):
        from ultron.brain import DESTRUCTIVE_TOOLS

        aliased = {tool for tool, _args in ALIASES.values()}
        assert not (aliased & set(DESTRUCTIVE_TOOLS)), (
            "an alias skips the confirmation gate")

    def test_every_alias_names_a_real_tool(self, brain):
        aliased = {tool for tool, _args in ALIASES.values()}
        unknown = aliased - set(brain.tool_functions)
        assert not unknown, f"aliases point at tools that do not exist: {unknown}"

    def test_every_alias_passes_arguments_the_tool_accepts(self, brain):
        import inspect

        for phrase, (tool, args) in ALIASES.items():
            signature = inspect.signature(brain.tool_functions[tool])
            for name in args:
                assert name in signature.parameters, (
                    f"'{phrase}' passes {name!r} which {tool} does not take")


class TestNormalising:
    def test_punctuation_and_case_go(self):
        assert normalise("Pause, please!") == "pause"

    def test_the_wake_word_is_filler(self):
        assert normalise("hey ultron volume up") == "volume up"

    def test_nothing_but_filler_reduces_to_nothing(self):
        assert normalise("please can you") == ""
        assert resolve("please can you") is None


class TestRunningThem:
    def test_an_alias_runs_the_tool_without_the_model(self, brain, monkeypatch):
        called = []
        monkeypatch.setattr(brain, "_invoke_tool",
                            lambda name, args: called.append((name, args)) or "Paused.")
        # Any attempt to reach the model must fail loudly rather than pass.
        monkeypatch.setattr(brain, "_process_input_local",
                            lambda text: pytest.fail("the model was called"))
        monkeypatch.setattr(brain, "_process_input_cloud",
                            lambda text: pytest.fail("the model was called"))

        assert brain.process_input("pause") == "Paused."
        assert called == [("system_media_control", {"action": "pause"})]

    def test_the_exchange_is_remembered(self, brain, monkeypatch):
        """So "why did you do that" has something to read."""
        monkeypatch.setattr(brain, "_invoke_tool", lambda name, args: "Paused.")
        brain.process_input("pause")

        assert brain.messages[-2] == {"role": "user", "content": "pause"}
        assert brain.messages[-1] == {"role": "assistant", "content": "Paused."}

    def test_it_is_searchable_afterwards(self, brain, monkeypatch):
        import sqlite3

        monkeypatch.setattr(brain, "_invoke_tool", lambda name, args: "Paused.")
        brain.process_input("pause")

        with sqlite3.connect(brain.db.db_path) as conn:
            rows = conn.execute(
                "SELECT role, message FROM chat_history WHERE session_id = ?",
                (brain.session_id,)).fetchall()
        assert ("user", "pause") in rows
        assert ("model", "Paused.") in rows

    def test_a_destructive_alias_is_refused_even_if_someone_adds_one(
            self, brain, monkeypatch):
        """The table is curated; this is the guard that does not rely on that."""
        monkeypatch.setitem(ALIASES, "wipe it", ("delete_file", {"file_path": "x"}))
        monkeypatch.setattr(brain, "_process_input_local",
                            lambda text: "[went to the model]")
        brain.active_api = "localapi"

        assert brain.process_input("wipe it") == "[went to the model]"

    def test_it_can_be_switched_off(self, brain, monkeypatch):
        from ultron.config import config

        monkeypatch.setattr(config, "get",
                            lambda key, default=None: False if key == "aliases.enabled" else default)
        monkeypatch.setattr(brain, "_process_input_local",
                            lambda text: "[went to the model]")
        brain.active_api = "localapi"

        assert brain.process_input("pause") == "[went to the model]"

    def test_the_setting_ships_with_a_default(self):
        import json
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        defaults = json.load(open(os.path.join(root, "settings.default.json")))
        assert defaults["aliases"]["enabled"] is True
