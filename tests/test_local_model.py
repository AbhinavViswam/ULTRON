"""Noticing when the local model cannot hold Ultron's own instructions.

Measured on this machine: Ollama gave gemma4:e2b a 4,096 token window while
the system prompt alone was 6,736. Everything past the limit was dropped
without a word, so Ultron sent the whole conversation every turn and the model
saw none of it — and answered as though it had no memory rather than as though
it had been truncated.

Nothing errored. That silence is what these tests are about.
"""

import pytest

from ultron.local_model import (
    HEADROOM_TOKENS, base_url, diagnose, fetch_context_length, parse_num_ctx,
)


class TestReadingTheWindow:
    def test_a_loaded_model_is_asked_directly(self):
        """The running model's window is the only number that is really real."""
        def fake_get(url, payload=None):
            assert url.endswith("/api/ps")
            return {"models": [{"name": "ultron-gemma:latest",
                                "context_length": 16384}]}

        assert fetch_context_length(
            "http://localhost:11434/v1", "ultron-gemma:latest", fake_get) == 16384

    def test_an_unloaded_model_falls_back_to_its_pinned_parameter(self):
        def fake_get(url, payload=None):
            if url.endswith("/api/ps"):
                return {"models": []}
            return {"parameters": "num_ctx                     16384\ntemperature 1"}

        assert fetch_context_length(
            "http://localhost:11434/v1", "ultron-gemma:latest", fake_get) == 16384

    def test_an_unpinned_unloaded_model_is_honestly_unknown(self):
        """Ollama decides at load time. Guessing here would be a lie."""
        def fake_get(url, payload=None):
            if url.endswith("/api/ps"):
                return {"models": []}
            return {"parameters": "temperature 1\ntop_k 64"}

        assert fetch_context_length(
            "http://localhost:11434/v1", "gemma4:e2b", fake_get) is None

    def test_ollama_being_down_is_not_a_crash(self):
        def fake_get(url, payload=None):
            raise OSError("connection refused")

        assert fetch_context_length(
            "http://localhost:11434/v1", "gemma4:e2b", fake_get) is None

    def test_the_openai_url_is_turned_into_ollamas_own(self):
        assert base_url("http://localhost:11434/v1") == "http://localhost:11434"
        assert base_url("http://localhost:11434/v1/") == "http://localhost:11434"
        assert base_url("http://localhost:11434") == "http://localhost:11434"

    def test_no_url_or_no_model_is_answered_with_nothing(self):
        assert fetch_context_length("", "gemma4:e2b") is None
        assert fetch_context_length("http://localhost:11434/v1", "") is None


class TestParsingNumCtx:
    def test_it_is_found_among_the_other_parameters(self):
        assert parse_num_ctx("temperature 1\nnum_ctx  16384\ntop_p 0.95") == 16384

    def test_absent_means_none(self):
        assert parse_num_ctx("temperature 1\ntop_k 64") is None

    def test_junk_does_not_raise(self):
        assert parse_num_ctx("num_ctx lots") is None
        assert parse_num_ctx("") is None
        assert parse_num_ctx(None) is None


class TestTheWarning:
    def test_the_real_failure_is_reported(self):
        """The exact numbers measured on this machine."""
        warning = diagnose(6736, 4096, "gemma4:e2b")

        assert warning is not None
        assert "4,096" in warning and "6,736" in warning
        assert "will not remember" in warning
        assert "num_ctx" in warning, "it must say how to fix it"

    def test_a_window_that_fits_says_nothing(self):
        assert diagnose(6736, 16384, "ultron-gemma:latest") is None

    def test_a_tight_fit_is_still_worth_mentioning(self):
        """Room for the prompt but not for a conversation is the same problem."""
        warning = diagnose(6736, 6736 + HEADROOM_TOKENS - 100)

        assert warning is not None
        assert "forget earlier messages" in warning

    def test_an_unknown_window_produces_no_warning(self):
        """A guess dressed as a warning teaches people to ignore warnings."""
        assert diagnose(6736, None) is None
        assert diagnose(6736, 0) is None

    def test_nothing_is_claimed_without_a_prompt_to_measure(self):
        assert diagnose(0, 4096) is None


class TestWiring:
    def test_the_core_checks_at_startup(self):
        import inspect

        from ultron.core import UltronCore

        assert "_check_context_window" in inspect.getsource(UltronCore.start)

    def test_it_waits_for_the_model_to_load(self):
        """The bug happened with an unloaded model, whose window is unknown
        until Ollama picks one on the first request."""
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._check_context_window)
        assert "while" in source, "a single check would miss an unloaded model"
        assert "_running" in source, "it must give up when Ultron shuts down"

    def test_cloud_providers_are_left_alone(self):
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._check_context_window)
        assert 'active_api != "localapi"' in source
