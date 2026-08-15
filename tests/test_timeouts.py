"""Nothing may hold the single worker thread indefinitely.

Ultron serves every command from one thread, so a tool that never returns is
not a slow tool — it is a dead assistant that only a kill recovers.
"""

import time

import pytest

from ultron.brain import (
    DEFAULT_TOOL_TIMEOUT_SECONDS, TOOL_TIMEOUT_SECONDS, tool_timeout
)


class TestBudgets:
    def test_ordinary_tools_get_the_default(self):
        assert tool_timeout("read_clipboard") == DEFAULT_TOOL_TIMEOUT_SECONDS

    @pytest.mark.parametrize("tool", ["run_workflow", "write_in_notepad", "clean_temp_files"])
    def test_slow_by_nature_tools_get_more(self, tool):
        assert tool_timeout(tool) > DEFAULT_TOOL_TIMEOUT_SECONDS

    def test_browser_tools_are_not_wrapped(self):
        """Playwright's sync API is bound to its creating thread."""
        assert tool_timeout("browser_navigate") is None
        assert tool_timeout("browser_click") is None

    def test_every_override_is_a_real_tool(self, brain):
        unknown = set(TOOL_TIMEOUT_SECONDS) - set(brain.tool_functions)
        assert not unknown, f"timeouts set for tools that do not exist: {unknown}"


class TestWatchdog:
    def test_a_hanging_tool_is_abandoned(self, brain, monkeypatch):
        monkeypatch.setitem(TOOL_TIMEOUT_SECONDS, "_hang", 0.5)
        brain.tool_functions["_hang"] = lambda: time.sleep(30)

        started = time.time()
        result = brain._invoke_tool("_hang", {})
        elapsed = time.time() - started

        assert result.startswith("Error")
        assert elapsed < 5, "must not have waited out the full 30s"

    def test_the_error_tells_the_model_not_to_retry(self, brain, monkeypatch):
        monkeypatch.setitem(TOOL_TIMEOUT_SECONDS, "_hang", 0.2)
        brain.tool_functions["_hang"] = lambda: time.sleep(10)
        result = brain._invoke_tool("_hang", {})
        assert "abandoned" in result
        assert "may or may not have taken effect" in result

    def test_fast_tools_are_untouched(self, brain):
        brain.tool_functions["_quick"] = lambda: "done"
        started = time.time()
        assert brain._invoke_tool("_quick", {}) == "done"
        assert time.time() - started < 1

    def test_a_raising_tool_still_reports_its_own_error(self, brain):
        def boom():
            raise ValueError("kaboom")

        brain.tool_functions["_boom"] = boom
        assert "kaboom" in brain._invoke_tool("_boom", {})

    def test_arguments_survive_the_thread_hop(self, brain):
        brain.tool_functions["_echo"] = lambda text="": f"got {text}"
        assert brain._invoke_tool("_echo", {"text": "hello"}) == "got hello"


class TestClients:
    def test_the_llm_client_has_a_timeout(self, brain):
        """The SDK default is 600s with 2 retries — half an hour of frozen."""
        assert brain.client is None or brain.client.timeout not in (None, 600.0)

    def test_playwright_budgets_are_set_and_sane(self):
        from ultron.automation import (
            BROWSER_ACTION_TIMEOUT_MS, BROWSER_NAV_TIMEOUT_MS
        )
        assert 0 < BROWSER_ACTION_TIMEOUT_MS <= 30_000
        assert 0 < BROWSER_NAV_TIMEOUT_MS <= 60_000


class TestGateOrdering:
    def test_confirmation_happens_before_the_watchdog_starts(self, brain):
        """Otherwise waiting for a click would eat the tool's whole budget."""
        import inspect

        source = inspect.getsource(type(brain)._invoke_tool)
        assert source.index("_check_confirmation") < source.index("_run_watched")
