"""Pushing a reminder back.

The reason this is a tool at all: without one the model was asked to snooze,
had no way to do it, and answered "I have updated your reminder" having done
nothing. A confident false confirmation is worse than admitting it cannot.
"""

import datetime


def _pending(brain):
    return brain.db.get_pending_tasks()


def _when(task):
    return datetime.datetime.fromisoformat(task[2])


class TestSnoozingWhatJustFired:
    """"Snooze it" names nothing, and by then the reminder may be gone."""

    def test_a_one_off_that_already_fired_can_still_be_snoozed(self, brain):
        # A one-off is deleted the moment it fires, so the table is empty.
        brain.last_fired_reminder = {"description": "workout", "frequency": None}

        result = brain._invoke_tool("snooze_reminder", {"minutes": 20})

        assert "workout" in result
        tasks = _pending(brain)
        assert len(tasks) == 1
        minutes = (_when(tasks[0]) - datetime.datetime.now()).total_seconds() / 60
        assert 19 < minutes <= 20

    def test_snoozing_a_recurring_reminder_leaves_its_schedule_alone(self, brain):
        """The trap: moving the row would snooze every future occurrence."""
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).replace(
            hour=17, minute=30, second=0, microsecond=0)
        brain.db.add_task("workout", tomorrow.isoformat(), "daily")
        brain.last_fired_reminder = {"description": "workout", "frequency": "daily"}

        brain._invoke_tool("snooze_reminder", {"minutes": 20})

        recurring = [t for t in _pending(brain) if t[4] == "daily"]
        assert len(recurring) == 1
        assert _when(recurring[0]) == tomorrow, "the daily 17:30 must not move"

        one_offs = [t for t in _pending(brain) if not t[4]]
        assert len(one_offs) == 1, "the snooze is a separate one-time nudge"

    def test_the_default_snooze_is_ten_minutes(self, brain):
        brain.last_fired_reminder = {"description": "workout", "frequency": None}
        brain._invoke_tool("snooze_reminder", {})

        minutes = (_when(_pending(brain)[0]) - datetime.datetime.now()).total_seconds() / 60
        assert 9 < minutes <= 10


class TestSnoozingByName:
    def test_a_pending_one_off_is_moved_rather_than_duplicated(self, brain):
        soon = datetime.datetime.now() + datetime.timedelta(minutes=5)
        brain.db.add_task("call the dentist", soon.isoformat())

        brain._invoke_tool("snooze_reminder", {"minutes": 30, "which": "dentist"})

        tasks = _pending(brain)
        assert len(tasks) == 1, "moving it must not leave the old one behind"
        minutes = (_when(tasks[0]) - datetime.datetime.now()).total_seconds() / 60
        assert 29 < minutes <= 30

    def test_a_partial_name_is_enough(self, brain):
        soon = datetime.datetime.now() + datetime.timedelta(minutes=5)
        brain.db.add_task("call the dentist about the appointment", soon.isoformat())

        result = brain._invoke_tool("snooze_reminder", {"minutes": 30, "which": "DENTIST"})
        assert "dentist" in result.lower()

    def test_an_ambiguous_name_asks_instead_of_guessing(self, brain):
        soon = (datetime.datetime.now() + datetime.timedelta(minutes=5)).isoformat()
        brain.db.add_task("call the dentist", soon)
        brain.db.add_task("call the bank", soon)

        result = brain._invoke_tool("snooze_reminder", {"minutes": 30, "which": "call"})

        assert "which one" in result.lower()
        for task in _pending(brain):
            assert _when(task) < datetime.datetime.now() + datetime.timedelta(minutes=6)

    def test_an_unknown_name_says_so_rather_than_inventing_one(self, brain):
        result = brain._invoke_tool("snooze_reminder", {"minutes": 30, "which": "yoga"})

        assert "no reminder" in result.lower()
        assert _pending(brain) == []


class TestWhenItCannotTell:
    def test_it_asks_when_nothing_has_fired_and_several_are_pending(self, brain):
        soon = (datetime.datetime.now() + datetime.timedelta(minutes=5)).isoformat()
        brain.db.add_task("call the dentist", soon)
        brain.db.add_task("call the bank", soon)
        brain.last_fired_reminder = None

        result = brain._invoke_tool("snooze_reminder", {"minutes": 30})
        assert "which reminder" in result.lower()

    def test_a_single_pending_reminder_needs_no_asking(self, brain):
        soon = datetime.datetime.now() + datetime.timedelta(minutes=5)
        brain.db.add_task("call the dentist", soon.isoformat())
        brain.last_fired_reminder = None

        brain._invoke_tool("snooze_reminder", {"minutes": 30})

        minutes = (_when(_pending(brain)[0]) - datetime.datetime.now()).total_seconds() / 60
        assert 29 < minutes <= 30


class TestBadInput:
    def test_a_spoken_number_is_refused_clearly(self, brain):
        brain.last_fired_reminder = {"description": "workout", "frequency": None}
        result = brain._invoke_tool("snooze_reminder", {"minutes": "twenty"})

        assert result.startswith("Error")
        assert _pending(brain) == []

    def test_zero_and_negative_snoozes_are_refused(self, brain):
        brain.last_fired_reminder = {"description": "workout", "frequency": None}
        for bad in (0, -5):
            assert brain._invoke_tool("snooze_reminder", {"minutes": bad}).startswith("Error")
        assert _pending(brain) == []

    def test_a_number_given_as_text_still_works(self, brain):
        """Local models routinely send "20" rather than 20."""
        brain.last_fired_reminder = {"description": "workout", "frequency": None}
        brain._invoke_tool("snooze_reminder", {"minutes": "20"})

        assert len(_pending(brain)) == 1


class TestWiring:
    def test_the_reminder_loop_records_what_fired(self):
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._reminder_loop)
        assert "last_fired_reminder" in source

    def test_the_tool_is_always_reachable(self):
        from ultron.brain import TOOL_GROUPS

        assert "snooze_reminder" in TOOL_GROUPS["core"]

    def test_snoozing_is_not_treated_as_destructive(self, brain, refuse_all):
        """It must not sit behind a confirmation prompt — it is undoable."""
        brain.last_fired_reminder = {"description": "workout", "frequency": None}
        brain._invoke_tool("snooze_reminder", {"minutes": 20})

        assert refuse_all == [], "snoozing should never ask for confirmation"
        assert len(_pending(brain)) == 1
