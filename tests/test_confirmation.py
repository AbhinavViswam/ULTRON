"""The destructive-action gate.

Ultron drives a real desktop with a small local model that demonstrably
invents tool arguments. These tests assert that approval is enforced in code
rather than requested in the system prompt, because a prompt instruction is
something a model may simply ignore.
"""

import os

import pytest

from ultron.brain import confirmation_question


class TestWhatIsGated:
    @pytest.mark.parametrize("tool,args", [
        ("delete_file", {"file_path": "a.txt"}),
        ("empty_recycle_bin", {}),
        ("clean_temp_files", {}),
        ("system_power_control", {"action": "shutdown"}),
        ("system_power_control", {"action": "sleep"}),
        ("delete_reminder", {"task_id": 3}),
        ("delete_workflow", {"name": "x"}),
        ("delete_memory", {"which": "1"}),
    ])
    def test_destructive_tools_ask(self, tool, args):
        assert confirmation_question(tool, args) is not None

    @pytest.mark.parametrize("tool,args", [
        ("system_power_control", {"action": "lock"}),
        ("system_power_control", {"action": "cancel_shutdown"}),
        ("open_application", {"app_name": "spotify"}),
        ("read_clipboard", {}),
        ("save_memory", {"category": "c", "key": "k", "value": "v", "importance": 1}),
        ("list_memories", {}),
    ])
    def test_reversible_tools_do_not(self, tool, args):
        assert confirmation_question(tool, args) is None

    def test_the_question_describes_the_action_in_english(self):
        question = confirmation_question("delete_file", {"file_path": "notes.txt"})
        assert "notes.txt" in question and "Recycle Bin" in question

    def test_a_malformed_call_still_asks(self):
        """A destructive tool called with nonsense is the last thing to wave through."""
        assert confirmation_question("empty_recycle_bin", None) is not None


class TestEnforcement:
    def test_refused_when_there_is_nobody_to_ask(self, brain):
        brain.set_confirm_handler(None)
        result = brain._invoke_tool("empty_recycle_bin", {})
        assert result.startswith("Error")

    def test_model_cannot_self_certify(self, brain):
        """The old design let the model pass confirmed=True itself."""
        brain.set_confirm_handler(None)
        result = brain._invoke_tool("empty_recycle_bin", {"confirmed": True})
        assert result.startswith("Error")

    def test_refusal_leaves_the_file_alone(self, brain, refuse_all, tmp_path):
        probe = tmp_path / "keep_me.txt"
        probe.write_text("important")
        result = brain._invoke_tool("delete_file", {"file_path": str(probe)})
        assert result.startswith("Error")
        assert probe.exists()
        assert len(refuse_all) == 1, "the user was actually asked"

    def test_approval_lets_it_through(self, brain, approve_all, tmp_path):
        probe = tmp_path / "bin_me.txt"
        probe.write_text("junk")
        result = brain._invoke_tool("delete_file", {"file_path": str(probe)})
        assert not result.startswith("Error")
        assert not probe.exists()

    def test_handler_that_raises_counts_as_refusal(self, brain, tmp_path):
        def broken(tool, args, question):
            raise RuntimeError("UI is gone")

        brain.set_confirm_handler(broken)
        probe = tmp_path / "survives.txt"
        probe.write_text("x")
        assert brain._invoke_tool("delete_file", {"file_path": str(probe)}).startswith("Error")
        assert probe.exists()

    def test_saved_workflows_cannot_bypass_the_gate(self, brain, refuse_all, tmp_path):
        """run_workflow used to call the raw functions, skipping _invoke_tool."""
        probe = tmp_path / "workflow_target.txt"
        probe.write_text("x")
        gated = brain._gated_tools()
        result = gated["delete_file"](str(probe))
        assert str(result).startswith("Error")
        assert probe.exists()

    def test_gated_wrappers_keep_their_signature(self, brain):
        """The workflow runner inspects signatures to count arguments."""
        import inspect

        gated = brain._gated_tools()
        for name in ("delete_file", "open_application", "save_memory"):
            assert inspect.signature(gated[name]) == \
                inspect.signature(brain.tool_functions[name])


class TestRecycleBin:
    def test_delete_file_is_recoverable(self, brain, approve_all, tmp_path):
        probe = tmp_path / "recoverable.txt"
        probe.write_text("x")
        result = brain._invoke_tool("delete_file", {"file_path": str(probe)})
        assert "Recycle Bin" in result, "the reply must say it can be restored"

    def test_missing_file_is_reported_not_crashed(self, brain, approve_all, tmp_path):
        result = brain._invoke_tool(
            "delete_file", {"file_path": str(tmp_path / "never_existed.txt")}
        )
        assert "does not exist" in result.lower()


class TestBlockingBridge:
    """UltronCore._confirm blocks the worker until a human answers."""

    def _core(self):
        import ultron.core as core_mod

        class Bridge:
            _confirm = core_mod.UltronCore._confirm
            on_confirmation_request = core_mod.UltronCore.on_confirmation_request

            def __init__(self):
                self._confirm_listeners = []

            def _fire(self, listeners, *args):
                for callback in list(listeners):
                    callback(*args)

            def _status(self, text):
                pass

        return Bridge()

    def test_no_front_end_means_no(self):
        assert self._core()._confirm("delete_file", {}, "delete x") is False

    def test_waits_for_the_answer(self):
        import threading
        import time

        bridge = self._core()
        bridge.on_confirmation_request(
            lambda q, decide: threading.Timer(0.2, decide, (True,)).start()
        )
        started = time.time()
        assert bridge._confirm("delete_file", {}, "delete x") is True
        assert time.time() - started >= 0.2, "it really blocked"

    def test_silence_expires_as_refusal(self, monkeypatch):
        import ultron.core as core_mod

        monkeypatch.setattr(core_mod, "CONFIRM_TIMEOUT_SECONDS", 0.3)
        bridge = self._core()
        bridge.on_confirmation_request(lambda q, decide: None)
        assert bridge._confirm("empty_recycle_bin", {}, "empty it") is False

    def test_a_late_answer_cannot_revive_an_expired_request(self, monkeypatch):
        import ultron.core as core_mod

        monkeypatch.setattr(core_mod, "CONFIRM_TIMEOUT_SECONDS", 0.2)
        bridge = self._core()
        captured = {}
        bridge.on_confirmation_request(lambda q, d: captured.setdefault("decide", d))
        assert bridge._confirm("delete_file", {}, "delete y") is False
        captured["decide"](True)  # must not raise, must not change the verdict
