"""Counting which tools earn the prompt space they cost.

89 tools is 2,690 tokens of description to a local model and 7,445 tokens of
JSON schema to Groq, paid on every request whether or not a tool has ever
been called. Cutting them needs evidence, because intuition is unreliable
here: the tools that feel important are the recently written ones, not the
daily ones.

Nothing in here may touch the real tally in data/.
"""

import json

import pytest

from ultron import tool_usage


@pytest.fixture
def tally(tmp_path):
    """A throwaway tally file. The real one is Ultron's own record."""
    return str(tmp_path / "tool_usage.json")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestTheQuestionItExistsToAnswer:
    def test_every_tool_is_listed_before_it_is_ever_called(self, tally):
        """A tally of only what has run cannot say what never runs, which is
        the entire question."""
        tool_usage.register(["open_application", "delete_file"], path=tally)

        assert set(_load(tally)) == {"open_application", "delete_file"}
        assert _load(tally)["delete_file"]["calls"] == 0

    def test_never_used_tools_are_reported(self, tally):
        tool_usage.register(["used_one", "unused_one"], path=tally)
        tool_usage.record("used_one", path=tally)

        report = tool_usage.report(path=tally)
        assert report["never_used"] == ["unused_one"]

    def test_registering_again_does_not_reset_the_counts(self, tally):
        """Ultron registers every tool at every startup."""
        tool_usage.register(["a"], path=tally)
        tool_usage.record("a", path=tally)
        tool_usage.register(["a", "b"], path=tally)

        assert _load(tally)["a"]["calls"] == 1
        assert _load(tally)["b"]["calls"] == 0

    def test_a_new_tool_joins_an_existing_tally(self, tally):
        tool_usage.register(["a"], path=tally)
        tool_usage.register(["a", "brand_new"], path=tally)

        assert "brand_new" in _load(tally)


class TestCounting:
    def test_calls_accumulate(self, tally):
        for _ in range(3):
            tool_usage.record("open_application", path=tally)

        assert _load(tally)["open_application"]["calls"] == 3

    def test_failures_are_counted_separately(self, tally):
        """Forty calls that all error is a broken tool, not a popular one,
        and a single number cannot tell them apart."""
        tool_usage.record("flaky", ok=True, path=tally)
        tool_usage.record("flaky", ok=False, path=tally)

        row = _load(tally)["flaky"]
        assert row["calls"] == 2 and row["errors"] == 1

    def test_a_tool_that_only_ever_fails_is_flagged(self, tally):
        for _ in range(5):
            tool_usage.record("broken", ok=False, path=tally)
        tool_usage.record("fine", ok=True, path=tally)

        report = tool_usage.report(path=tally)
        assert [n for n, _r in report["always_failing"]] == ["broken"]

    def test_the_last_use_is_recorded(self, tally):
        tool_usage.record("a", when="2026-08-19T10:00:00", path=tally)
        assert _load(tally)["a"]["last_used"] == "2026-08-19T10:00:00"

    def test_the_busiest_tool_comes_first(self, tally):
        tool_usage.record("rare", path=tally)
        for _ in range(9):
            tool_usage.record("common", path=tally)

        assert tool_usage.report(path=tally)["used"][0][0] == "common"

    def test_recording_an_unregistered_tool_still_works(self, tally):
        """A tool added mid-session must not be dropped on the floor."""
        tool_usage.record("appeared_later", path=tally)
        assert _load(tally)["appeared_later"]["calls"] == 1

    def test_a_nameless_call_is_ignored(self, tally):
        tool_usage.record("", path=tally)
        tool_usage.record(None, path=tally)
        assert tool_usage.report(path=tally)["total_tools"] == 0


class TestItNeverBreaksUltron:
    """Bookkeeping that can take the assistant down is worse than no
    bookkeeping."""

    def test_a_corrupt_file_starts_over_instead_of_raising(self, tally):
        with open(tally, "w", encoding="utf-8") as f:
            f.write("{not json at all")

        tool_usage.record("a", path=tally)
        assert _load(tally)["a"]["calls"] == 1

    def test_a_file_holding_the_wrong_shape_is_survived(self, tally):
        with open(tally, "w", encoding="utf-8") as f:
            json.dump({"a": "this should have been an object"}, f)

        tool_usage.record("a", path=tally)
        assert _load(tally)["a"]["calls"] == 1

    def test_an_unwritable_path_does_not_raise(self, tmp_path, capsys):
        blocked = str(tmp_path / "a-file" / "nested" / "usage.json")
        (tmp_path / "a-file").write_text("I am a file, not a directory")

        tool_usage.record("a", path=blocked)      # must not raise

    def test_report_on_a_missing_file_is_empty_not_an_error(self, tmp_path):
        report = tool_usage.report(path=str(tmp_path / "nothing.json"))
        assert report["total_tools"] == 0
        assert report["never_used"] == []


class TestItIsWiredToRealToolCalls:
    def test_every_registered_tool_appears_at_startup(self, brain, monkeypatch):
        """Brain seeds the tally as it builds the tool table."""
        import ultron.tool_usage as module

        seeded = {}
        monkeypatch.setattr(module, "register",
                            lambda names, path=None: seeded.update(
                                {"names": list(names)}))
        from ultron.brain import Brain

        Brain()
        assert len(seeded["names"]) > 50, "the whole tool table should be seeded"

    def test_a_successful_call_is_counted(self, brain, monkeypatch, tally):
        import ultron.brain as module

        calls = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: calls.append((name, ok)))
        brain._invoke_tool("get_system_health", {})

        assert calls and calls[0][0] == "get_system_health"

    def test_a_failing_call_is_counted_as_a_failure(self, brain, monkeypatch):
        import ultron.brain as module

        calls = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: calls.append((name, ok)))
        monkeypatch.setitem(brain.tool_functions, "boom",
                            lambda: (_ for _ in ()).throw(RuntimeError("no")))
        brain._invoke_tool("boom", {})

        assert calls == [("boom", False)]

    def test_an_alias_is_counted_too(self, brain, monkeypatch):
        """Aliases skip the model but still run a tool, and a tool used only
        through an alias must not look unused."""
        import ultron.brain as module

        calls = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: calls.append(name))
        monkeypatch.setattr(brain, "_run_watched",
                            lambda name, func, args: "Paused.")
        brain.process_input("pause")

        assert "system_media_control" in calls

    def test_an_unknown_tool_is_not_counted(self, brain, monkeypatch):
        import ultron.brain as module

        calls = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: calls.append(name))
        brain._invoke_tool("no_such_tool", {})

        assert calls == [], "a typo would invent a tool in the tally"


class TestWhereItLives:
    def test_it_is_kept_out_of_version_control(self):
        """It records what the user does with their own machine."""
        import os
        import subprocess

        from ultron.config import DATA_DIR

        # The real location, not the redirected one the test fixtures use.
        real = os.path.join(DATA_DIR, "tool_usage.json")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        relative = os.path.relpath(real, root).replace("\\", "/")
        result = subprocess.run(["git", "check-ignore", relative], cwd=root,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"{relative} is not gitignored"


class TestWhatTheErrorCountDoesNotCatch:
    """A known limit, written down so the number is not over-trusted.

    39 places in automation.py report trouble without the word "Error":
    "Failed to ...", "Could not ...", "File does not exist". Those calls are
    counted as successes.

    Widening the match was the obvious fix and the wrong one. "No files
    matching 'x'" is a successful search that found nothing, and "the file
    does not exist" is the correct answer to a question about a missing file.
    Counting those as failures would recommend deleting tools that work.
    """

    def test_a_tool_reporting_failure_in_prose_reads_as_a_success(
            self, brain, monkeypatch):
        import ultron.brain as module

        seen = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: seen.append(ok))
        monkeypatch.setitem(brain.tool_functions, "prose_failure",
                            lambda: "Failed to reach the server.")

        brain._invoke_tool("prose_failure", {})

        assert seen == [True], (
            "if this now reports False the convention changed, and the "
            "docstring on tool_usage.report is out of date")

    def test_an_explicit_error_return_is_caught(self, brain, monkeypatch):
        import ultron.brain as module

        seen = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: seen.append(ok))
        monkeypatch.setitem(brain.tool_functions, "honest_failure",
                            lambda: "Error: could not reach the server.")

        brain._invoke_tool("honest_failure", {})
        assert seen == [False]

    def test_a_search_finding_nothing_is_not_a_failure(self, brain,
                                                       monkeypatch):
        """The reason the match is not widened."""
        import ultron.brain as module

        seen = []
        monkeypatch.setattr(module.tool_usage, "record",
                            lambda name, ok=True, **kw: seen.append(ok))
        monkeypatch.setitem(brain.tool_functions, "empty_search",
                            lambda: "No files matching 'x' were found.")

        brain._invoke_tool("empty_search", {})
        assert seen == [True]
