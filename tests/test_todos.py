"""Things the user means to do, as opposed to things Ultron says at a time.

A reminder fires once and is finished. A todo stays pending until it is done,
can be overdue, and usually has no deadline at all. They are close enough to
look like one feature and different enough that sharing the `tasks` table
would have put todos in front of the reminder loop, which would then read
them aloud on their due date -- a different feature than the one asked for.

Stored in SQLite rather than Chroma deliberately: "what is still pending" and
"what is overdue" are exact queries over structured columns, and a vector
index cannot answer them reliably.
"""

import datetime
import sqlite3

import pytest

from ultron import idle_chat


@pytest.fixture
def tools(brain):
    return brain.tool_functions


class TestKeepingAList:
    def test_something_added_comes_back(self, brain, tools):
        tools["add_todo"]("call the bank")
        assert "call the bank" in tools["list_todos"]()

    def test_an_empty_list_says_so(self, tools):
        assert "empty" in tools["list_todos"]().lower()

    def test_a_todo_needs_words(self, tools):
        assert tools["add_todo"]("   ").startswith("Error")

    def test_a_deadline_is_optional(self, brain):
        """Most of what people mean to do has no deadline, and demanding one
        would turn every note into a reminder."""
        brain.db.add_todo("buy rice")
        assert brain.db.list_todos()[0]["due_date"] is None

    def test_an_unreadable_deadline_does_not_lose_the_todo(self, brain, tools):
        result = tools["add_todo"]("fix the mic", "sometime around whenever")

        assert "could not make sense" in result
        assert brain.db.list_todos(), "the todo itself was thrown away"

    def test_dated_todos_come_before_undated_ones(self, brain):
        brain.db.add_todo("someday thing")
        soon = (datetime.datetime.now() + datetime.timedelta(days=1)).isoformat()
        brain.db.add_todo("dated thing", soon)

        assert brain.db.list_todos()[0]["task"] == "dated thing"

    def test_overdue_items_are_marked(self, brain, tools):
        past = (datetime.datetime.now() - datetime.timedelta(days=2)).isoformat()
        brain.db.add_todo("submit the form", past)

        assert "OVERDUE" in tools["list_todos"]()


class TestFinishingThings:
    def test_completing_removes_it_from_pending(self, brain, tools):
        tools["add_todo"]("call the bank")
        tools["complete_todo"]("bank")

        assert "call the bank" not in tools["list_todos"]()

    def test_a_completed_todo_is_kept_not_deleted(self, brain, tools):
        """Done is not gone; the record is how you know you did it."""
        tools["add_todo"]("call the bank")
        tools["complete_todo"]("bank")

        assert "call the bank" in tools["list_todos"](include_done=True)

    def test_it_can_be_completed_by_number(self, brain, tools):
        tools["add_todo"]("buy rice")
        todo_id = brain.db.list_todos()[0]["id"]

        assert "done" in tools["complete_todo"](str(todo_id))

    def test_an_ambiguous_name_is_refused_not_guessed(self, brain, tools):
        """Completing the wrong item is silent. The user would not find out
        until they looked, by which time they have lost the real one."""
        tools["add_todo"]("buy rice")
        tools["add_todo"]("buy rice for onam")

        result = tools["complete_todo"]("rice")

        assert result.startswith("Error")
        assert "more than one" in result
        assert len(brain.db.list_todos()) == 2, "one was completed anyway"

    def test_a_name_that_matches_nothing_says_so(self, tools):
        assert tools["complete_todo"]("nonsense").startswith("Error")

    def test_a_missing_number_says_so(self, tools):
        assert "no todo numbered" in tools["complete_todo"]("99")

    def test_saying_nothing_asks_which(self, tools):
        assert "which todo" in tools["complete_todo"]("").lower()

    def test_an_already_done_todo_is_not_a_success(self, brain, tools):
        tools["add_todo"]("buy rice")
        tools["complete_todo"]("rice")

        assert "already" in tools["complete_todo"]("1")

    def test_it_can_be_put_back(self, brain, tools):
        tools["add_todo"]("call the bank")
        tools["complete_todo"]("bank")
        tools["reopen_todo"]("bank")

        assert "call the bank" in tools["list_todos"]()


class TestDeletingIsDifferentFromFinishing:
    def test_deleting_removes_it_entirely(self, brain, tools):
        tools["add_todo"]("buy rice")
        tools["delete_todo"]("rice")

        assert brain.db.list_todos(include_done=True) == []

    def test_deleting_needs_confirmation(self):
        """Completing is recoverable and deleting is not, so only one of them
        goes through the gate."""
        from ultron.brain import DESTRUCTIVE_TOOLS

        assert "delete_todo" in DESTRUCTIVE_TOOLS
        assert "complete_todo" not in DESTRUCTIVE_TOOLS
        assert "add_todo" not in DESTRUCTIVE_TOOLS

    def test_the_question_explains_the_difference(self):
        from ultron.brain import confirmation_question

        question = confirmation_question("delete_todo", {"which": "buy rice"})
        assert "buy rice" in question
        assert "done" in question, (
            "the user should be told that completing keeps it")

    def test_a_routine_cannot_delete_a_todo(self, brain):
        """Nobody is present to approve it."""
        brain.unattended = True
        brain.db.add_todo("buy rice")

        result = brain._invoke_tool("delete_todo", {"which": "rice"})

        assert result.startswith("Error")
        assert brain.db.list_todos(), "it was deleted with nobody watching"


class TestItDoesNotDisturbReminders:
    """The two live in separate tables precisely so this cannot happen."""

    def test_a_todo_is_not_a_reminder(self, brain, tools):
        tools["add_todo"]("call the bank", "friday")

        assert brain.db.get_pending_tasks() == [], (
            "a todo reached the reminder loop and will be read aloud")

    def test_a_reminder_is_not_a_todo(self, brain, tools):
        soon = (datetime.datetime.now() + datetime.timedelta(hours=1))
        brain.db.add_task("stand up", soon.isoformat())

        assert brain.db.list_todos() == []

    def test_they_are_separate_tables(self, brain):
        with sqlite3.connect(brain.db.db_path) as conn:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"todos", "tasks"} <= names


class TestUltronMentionsThemWhenIdle:
    def test_pending_todos_reach_the_idle_context(self, brain):
        brain.db.add_todo("submit the college form")

        assert "submit the college form" in idle_chat.gather_context(brain)

    def test_completed_ones_do_not(self, brain, tools):
        tools["add_todo"]("buy rice")
        tools["complete_todo"]("rice")

        assert "buy rice" not in idle_chat.gather_context(brain)

    def test_overdue_ones_come_first(self, brain):
        soon = (datetime.datetime.now() + datetime.timedelta(days=5)).isoformat()
        past = (datetime.datetime.now() - datetime.timedelta(days=5)).isoformat()
        brain.db.add_todo("later thing", soon)
        brain.db.add_todo("late thing", past)

        context = idle_chat.gather_context(brain)
        assert context.index("late thing") < context.index("later thing")

    def test_a_long_list_is_not_read_out_in_full(self, brain):
        """Thirty todos spoken aloud is a lecture, not a remark."""
        for n in range(30):
            brain.db.add_todo(f"thing number {n}")

        context = idle_chat.gather_context(brain)

        assert "27 more" in context
        assert context.count("thing number") <= 3

    def test_the_overdue_count_reads_naturally(self, brain):
        """Ultron says this out loud, so "1 of those are" grates."""
        past = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
        brain.db.add_todo("one late thing", past)

        assert "1 of those is" in idle_chat.gather_context(brain)

    def test_an_empty_list_adds_nothing(self, brain, monkeypatch):
        monkeypatch.setattr(idle_chat, "machine_observations", list)
        assert idle_chat.gather_context(brain) == ""

    def test_a_broken_todo_table_does_not_break_the_remark(self, brain,
                                                           monkeypatch):
        monkeypatch.setattr(idle_chat, "machine_observations", list)
        monkeypatch.setattr(brain.db, "list_todos",
                            lambda **kw: (_ for _ in ()).throw(sqlite3.Error("x")))

        assert idle_chat.gather_context(brain) == ""

    def test_an_unreadable_due_date_does_not_hide_the_todo(self, brain):
        brain.db.add_todo("odd one", "not-a-date-at-all")
        assert "odd one" in idle_chat.gather_context(brain)


class TestAskingForThemOutLoud:
    @pytest.mark.parametrize("said", [
        "my todos", "what is on my todo list", "what do I have to do",
        "pending tasks", "show todos",
    ])
    def test_asking_skips_the_model(self, said):
        from ultron.aliases import resolve

        assert resolve(said) == ("list_todos", {})

    @pytest.mark.parametrize("said", [
        "add milk to my todo list",
        "mark the bank call as done",
        "delete my todo about rice",
    ])
    def test_anything_that_changes_the_list_goes_to_the_model(self, said):
        """An alias cannot read out which item was meant."""
        from ultron.aliases import resolve

        assert resolve(said) is None
