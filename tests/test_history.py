"""Keeping the conversation from growing forever.

`self.messages` was initialised once and never truncated. Every turn, every
tool result and every unprompted remark accumulated for the life of the
session, so a long day of use would slow Ultron down and eventually exceed the
model's context window outright.

Nothing trimmed is actually lost — every message is written to chat_history,
and search_past_conversations reaches all of it. This only bounds what gets
re-sent on every single request.
"""

import pytest


def _turn(n):
    return [
        {"role": "user", "content": f"question {n} " + "x" * 400},
        {"role": "assistant", "content": f"answer {n} " + "y" * 400},
    ]


def _tool_turn(n):
    """The shape that makes naive trimming unsafe."""
    return [
        {"role": "user", "content": f"do thing {n} " + "x" * 400},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": f"call{n}", "type": "function",
                         "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"call{n}", "content": "z" * 800},
        {"role": "assistant", "content": f"done {n} " + "y" * 400},
    ]


class TestItStaysWithinBudget:
    def test_a_short_conversation_is_left_alone(self, brain):
        brain.messages += _turn(1)
        before = list(brain.messages)

        brain._trim_history()

        assert brain.messages == before

    def test_a_long_conversation_is_cut_down(self, brain):
        for n in range(200):
            brain.messages += _turn(n)
        assert len(brain.messages) == 401

        brain._trim_history()

        assert len(brain.messages) < 401
        conversation = sum(brain._message_size(m) for m in brain.messages[1:])
        assert conversation <= brain.history_budget() * 1.1

    def test_the_system_prompt_is_never_dropped(self, brain):
        system = brain.messages[0]
        for n in range(500):
            brain.messages += _turn(n)

        brain._trim_history()

        assert brain.messages[0] is system
        assert brain.messages[0]["role"] == "system"

    def test_the_most_recent_exchange_survives(self, brain):
        for n in range(200):
            brain.messages += _turn(n)

        brain._trim_history()

        assert "answer 199" in brain.messages[-1]["content"]

    def test_it_is_the_oldest_that_goes(self, brain):
        for n in range(200):
            brain.messages += _turn(n)

        brain._trim_history()

        kept = " ".join(str(m.get("content")) for m in brain.messages)
        assert "question 0 " not in kept
        assert "question 199 " in kept


class TestItNeverBreaksTheHistory:
    """A "tool" message is only valid while the assistant call above it remains.

    Dropping a tool_calls message but keeping its results is not a degraded
    conversation — it is a request the API rejects outright, mid-conversation.
    """

    def test_no_tool_result_is_left_without_its_call(self, brain):
        for n in range(200):
            brain.messages += _tool_turn(n)

        brain._trim_history()

        pending = set()
        for message in brain.messages:
            for call in message.get("tool_calls") or []:
                pending.add(call["id"])
            if message.get("role") == "tool":
                assert message["tool_call_id"] in pending, (
                    "a tool result survived without the call that produced it")

    def test_a_cut_landing_exactly_on_a_tool_result_advances_past_it(self, brain, monkeypatch):
        """Sized so the budget boundary falls precisely on the tool message.

        Without forcing that, a trimmer with no orphan guard passes every
        other test here by luck — the cut simply never lands there. This one
        computes where it lands and puts a tool result in the way.
        """
        brain.messages += [
            {"role": "user", "content": "x" * 4000},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call1", "type": "function",
                             "function": {"name": "web_search",
                                          "arguments": "y" * 4000}}]},
            {"role": "tool", "tool_call_id": "call1", "content": "z" * 40},
            {"role": "assistant", "content": "w" * 40},
        ]
        sizes = [brain._message_size(m) for m in brain.messages]
        # Exactly the last two messages fit, so the cut stops on the tool one.
        monkeypatch.setattr(brain, "history_budget", lambda: sizes[3] + sizes[4])

        brain._trim_history()

        roles = [m["role"] for m in brain.messages]
        assert roles[1] != "tool", (
            f"a tool result was left with no call above it: {roles}")
        assert roles == ["system", "assistant"]

    def test_the_first_kept_message_is_never_a_tool_result(self, brain):
        for n in range(200):
            brain.messages += _tool_turn(n)

        brain._trim_history()

        assert brain.messages[1].get("role") != "tool"

    def test_cutting_inside_a_tool_exchange_advances_past_it(self, brain):
        """Constructed so the budget would otherwise land mid-exchange."""
        brain.messages += _tool_turn(1) + _tool_turn(2)
        huge = "x" * (brain.history_budget() * 4)
        brain.messages.insert(1, {"role": "user", "content": huge})

        brain._trim_history()

        assert len(brain.messages) > 1, "it should not strip the whole history"
        assert brain.messages[1].get("role") != "tool"


class TestTheBudget:
    def test_a_local_model_gets_a_smaller_budget(self, brain, monkeypatch):
        from ultron.config import config

        monkeypatch.setattr(config, "get",
                            lambda key, default=None: None if key == "max_history_tokens" else default)
        brain.active_api = "localapi"
        local = brain.history_budget()
        brain.active_api = "openrouterapi"
        assert local < brain.history_budget()

    def test_a_configured_budget_wins(self, brain, monkeypatch):
        from ultron.config import config

        monkeypatch.setattr(config, "get",
                            lambda key, default=None: 1234 if key == "max_history_tokens" else default)
        assert brain.history_budget() == 1234

    def test_nonsense_in_the_setting_falls_back_instead_of_crashing(self, brain, monkeypatch):
        from ultron.config import config

        monkeypatch.setattr(config, "get",
                            lambda key, default=None: "lots" if key == "max_history_tokens" else default)
        assert brain.history_budget() > 0

    def test_trimming_can_be_switched_off(self, brain, monkeypatch):
        from ultron.config import config

        monkeypatch.setattr(config, "get",
                            lambda key, default=None: 0 if key == "max_history_tokens" else default)
        for n in range(200):
            brain.messages += _turn(n)
        count = len(brain.messages)

        brain._trim_history()

        assert len(brain.messages) == count


class TestWiring:
    def test_every_turn_trims_first(self):
        import inspect

        from ultron.brain import Brain

        source = inspect.getsource(Brain.process_input)
        assert "_trim_history()" in source
        # Mid-turn the history holds a tool call still waiting on its results.
        assert source.index("_trim_history") < source.index("_process_input_local")

    def test_a_message_with_tool_calls_is_measured_not_ignored(self, brain):
        plain = {"role": "assistant", "content": ""}
        with_calls = {"role": "assistant", "content": "",
                      "tool_calls": [{"id": "c", "function": {"arguments": "x" * 400}}]}

        assert brain._message_size(with_calls) > brain._message_size(plain)

    def test_unserialisable_tool_calls_do_not_crash_the_measure(self, brain):
        assert brain._message_size(
            {"role": "assistant", "tool_calls": object()}) > 0
