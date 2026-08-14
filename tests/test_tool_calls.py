"""Argument handling for tool calls made by small, unreliable models.

Ultron's local model invents parameter names and omits required ones. These
tests cover the layer that heals what it can and refuses what it cannot.
"""

import datetime

import pytest

from ultron.brain import coerce_tool_args, parse_time_string


class TestArgumentHealing:
    def test_known_aliases_are_mapped(self):
        def set_reminder_at(description: str, time_str: str):
            pass

        healed = coerce_tool_args(
            set_reminder_at, {"task": "stretch", "at": "10:20 am"}
        )
        assert healed == {"description": "stretch", "time_str": "10:20 am"}

    def test_unexpected_arguments_are_dropped(self):
        """Notably `confirmed`, which the model used to grant itself."""
        def empty_recycle_bin():
            pass

        assert coerce_tool_args(empty_recycle_bin, {"confirmed": True}) == {}

    def test_numbers_arrive_as_numbers(self):
        def set_reminder(description: str, delay_seconds: int):
            pass

        healed = coerce_tool_args(
            set_reminder, {"description": "tea", "delay_seconds": "300"}
        )
        assert healed["delay_seconds"] == 300

    def test_booleans_survive_being_spoken(self):
        def toggle(flag: bool = False):
            pass

        for spoken in ("true", "yes", "1", "y"):
            assert coerce_tool_args(toggle, {"flag": spoken})["flag"] is True


class TestTimeParsing:
    def test_a_clock_time_becomes_a_real_datetime(self):
        assert isinstance(parse_time_string("10:20 am"), datetime.datetime)

    @pytest.mark.parametrize("spoken", ["10:20 am", "17:30", "7pm", "tomorrow 9am"])
    def test_accepted_phrasings_land_in_the_future(self, spoken):
        assert parse_time_string(spoken) > datetime.datetime.now()

    def test_a_time_already_past_today_moves_to_tomorrow(self):
        now = datetime.datetime.now()
        earlier = (now - datetime.timedelta(hours=2)).strftime("%H:%M")
        parsed = parse_time_string(earlier)
        assert parsed > now
        assert parsed.strftime("%H:%M") == earlier

    def test_nonsense_is_rejected(self):
        with pytest.raises(ValueError):
            parse_time_string("sometime soonish")


class TestMediaInference:
    """The Spotify bug: 'open spotify' silently started playing music."""

    def test_a_bare_call_with_no_media_request_is_refused(self, brain):
        brain.recent_user_text = "open spotify"
        result = brain._invoke_tool("system_media_control", {})
        assert result.startswith("Error")
        assert "open_application" in result, "should steer the model to the right tool"

    def test_a_genuine_request_still_works(self, brain, monkeypatch):
        captured = {}
        brain.tool_functions["system_media_control"] = \
            lambda action="": captured.setdefault("action", action) or "ok"
        monkeypatch.setattr(brain, "_infer_media_action", lambda: "play")
        brain._invoke_tool("system_media_control", {})
        assert captured["action"] == "play"

    def test_an_explicit_action_is_never_second_guessed(self, brain):
        captured = {}
        brain.tool_functions["system_media_control"] = \
            lambda action="": captured.setdefault("action", action) or "ok"
        brain._invoke_tool("system_media_control", {"action": "pause"})
        assert captured["action"] == "pause"


class TestToolCallParsing:
    def test_a_well_formed_call_is_found(self, brain):
        calls = brain._parse_tool_calls_from_text(
            '<tool_call>{"name": "read_clipboard", "arguments": {}}</tool_call>'
        )
        assert len(calls) == 1 and calls[0]["name"] == "read_clipboard"

    def test_a_malformed_call_is_skipped_not_crashed(self, brain):
        assert brain._parse_tool_calls_from_text(
            '<tool_call>{"name": "x", oops}</tool_call>'
        ) == []

    def test_plain_prose_yields_nothing(self, brain):
        assert brain._parse_tool_calls_from_text("Certainly sir, right away.") == []


class TestRegistry:
    def test_every_grouped_tool_exists(self, brain):
        from ultron.brain import TOOL_GROUPS

        for group, names in TOOL_GROUPS.items():
            missing = [n for n in names if n not in brain.tool_functions]
            assert not missing, f"group '{group}' lists missing tools: {missing}"

    def test_every_gated_tool_exists(self, brain):
        from ultron.brain import DESTRUCTIVE_TOOLS

        missing = set(DESTRUCTIVE_TOOLS) - set(brain.tool_functions)
        assert not missing, f"gate configured for missing tools: {missing}"

    def test_an_unknown_tool_is_reported(self, brain):
        assert brain._invoke_tool("no_such_tool", {}).startswith("Error")
