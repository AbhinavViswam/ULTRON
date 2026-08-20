"""The routines window.

Setting a routine by voice means dictating a paragraph of instruction and a
schedule in one breath. A form is better at that, so this is the other way in
— but only if it produces exactly what the voice path produces, which is what
most of these tests are about.
"""

import datetime

import pytest

from ultron import routines as sched
from ultron.ui.routines_panel import (
    CUSTOM_DAYS_INDEX, EVERY_N_INDEX, MONTHLY_INDEX, ONCE_INDEX,
    RoutinesPanel, describe_next_run,
)

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def panel(scratch_db, qt_app):
    ran = []
    widget = RoutinesPanel(lambda: scratch_db, ran.append)
    widget.db = scratch_db
    widget.ran = ran
    return widget


def _fill(panel, name="Test", instruction="Do the thing"):
    panel.name_edit.setText(name)
    panel.instruction_edit.setPlainText(instruction)


class TestTheScheduleControls:
    """The form builds the phrase the voice path uses, not a schedule itself.

    That is the whole reason the two routes cannot drift apart: whatever the
    form assembles goes through the same parse_schedule, with the same tests
    behind it.
    """

    def test_every_day(self, panel):
        panel.repeat_combo.setCurrentIndex(0)
        assert panel._when_text() == "daily"

    def test_weekdays_and_weekends(self, panel):
        panel.repeat_combo.setCurrentIndex(1)
        assert sched.parse_schedule(panel._when_text())["days"] == sched.WEEKDAYS
        panel.repeat_combo.setCurrentIndex(2)
        assert sched.parse_schedule(panel._when_text())["days"] == sched.WEEKEND

    def test_chosen_days(self, panel):
        panel.repeat_combo.setCurrentIndex(CUSTOM_DAYS_INDEX)
        for index in (0, 1, 5):  # Mon, Tue, Sat
            panel.day_checks[index].setChecked(True)

        assert sched.parse_schedule(panel._when_text())["days"] == [0, 1, 5]

    def test_every_n_days(self, panel):
        panel.repeat_combo.setCurrentIndex(EVERY_N_INDEX)
        panel.every_n_spin.setValue(4)

        schedule = sched.parse_schedule(panel._when_text())
        assert schedule["kind"] == "interval"
        assert schedule["every_n_days"] == 4

    def test_monthly_on_a_date(self, panel):
        panel.repeat_combo.setCurrentIndex(MONTHLY_INDEX)
        panel.day_of_month_spin.setValue(15)

        schedule = sched.parse_schedule(panel._when_text())
        assert schedule["kind"] == "monthly"
        assert schedule["day_of_month"] == 15

    def test_once_on_a_date(self, panel):
        from PySide6.QtCore import QDate

        panel.repeat_combo.setCurrentIndex(ONCE_INDEX)
        panel.date_edit.setDate(QDate(2027, 3, 9))

        schedule = sched.parse_schedule(panel._when_text())
        assert schedule["kind"] == "once"
        assert schedule["once_date"] == "2027-03-09"

    def test_only_the_relevant_controls_are_shown(self, panel):
        panel.repeat_combo.setCurrentIndex(0)
        assert not panel.days_row.isVisibleTo(panel)
        assert not panel.date_edit.isVisibleTo(panel)

        panel.repeat_combo.setCurrentIndex(CUSTOM_DAYS_INDEX)
        assert panel.days_row.isVisibleTo(panel)
        assert not panel.every_n_spin.isVisibleTo(panel)

        panel.repeat_combo.setCurrentIndex(ONCE_INDEX)
        assert panel.date_edit.isVisibleTo(panel)
        assert not panel.days_row.isVisibleTo(panel)


class TestCreating:
    def test_a_routine_reaches_the_database(self, panel):
        from PySide6.QtCore import QTime

        _fill(panel, "Day in history", "Search the web for what is notable today.")
        panel.repeat_combo.setCurrentIndex(0)
        panel.time_edit.setTime(QTime(7, 30))
        panel._save()

        routines = panel.db.list_routines()
        assert len(routines) == 1
        assert routines[0]["name"] == "Day in history"
        assert routines[0]["schedule"]["at_time"] == "07:30"
        assert routines[0]["next_run"]

    def test_the_form_clears_after_creating(self, panel):
        _fill(panel)
        panel._save()

        assert panel.name_edit.text() == ""
        assert panel.instruction_edit.toPlainText() == ""

    def test_the_new_routine_appears_in_the_list(self, panel):
        _fill(panel, "Standup notes")
        panel._save()

        assert panel._list.count() == 1
        assert not panel._empty.isVisibleTo(panel)

    def test_the_chosen_delivery_is_saved(self, panel):
        _fill(panel)
        panel.delivery_checks["speak"].setChecked(False)
        panel.delivery_checks["file"].setChecked(True)
        panel._save()

        deliver = panel.db.list_routines()[0]["deliver"].split(",")
        assert set(deliver) == {"card", "file"}


class TestItRefusesRatherThanGuessing:
    def test_a_routine_with_no_name_is_refused(self, panel):
        _fill(panel, name="")
        panel._save()

        assert panel.db.list_routines() == []
        assert "name" in panel.status_label.text().lower()

    def test_a_routine_with_nothing_to_do_is_refused(self, panel):
        _fill(panel, instruction="")
        panel._save()

        assert panel.db.list_routines() == []

    def test_a_duplicate_name_is_refused(self, panel):
        _fill(panel, "Day in history")
        panel._save()
        _fill(panel, "day in HISTORY")
        panel._save()

        assert len(panel.db.list_routines()) == 1
        assert "already" in panel.status_label.text().lower()

    def test_custom_days_with_nothing_ticked_is_refused(self, panel):
        _fill(panel)
        panel.repeat_combo.setCurrentIndex(CUSTOM_DAYS_INDEX)
        panel._save()

        assert panel.db.list_routines() == []
        assert "day" in panel.status_label.text().lower()

    def test_no_delivery_method_is_refused(self, panel):
        _fill(panel)
        for check in panel.delivery_checks.values():
            check.setChecked(False)
        panel._save()

        assert panel.db.list_routines() == []

    def test_a_date_in_the_past_is_refused(self, panel):
        from PySide6.QtCore import QDate

        _fill(panel)
        panel.repeat_combo.setCurrentIndex(ONCE_INDEX)
        panel.date_edit.setDate(QDate(2020, 1, 1))
        panel._save()

        assert panel.db.list_routines() == []
        assert "passed" in panel.status_label.text().lower()

    def test_nothing_is_saved_before_the_core_has_booted(self, qt_app):
        """The window exists before the database does."""
        widget = RoutinesPanel(lambda: None, lambda name: None)
        _fill(widget)
        widget._save()  # must not raise

        assert "starting up" in widget.status_label.text().lower()


class TestEditing:
    def _create(self, panel, name="Weekend plan"):
        _fill(panel, name, "Check the weather.")
        panel.repeat_combo.setCurrentIndex(CUSTOM_DAYS_INDEX)
        for index in (5, 6):
            panel.day_checks[index].setChecked(True)
        panel._save()
        return panel.db.list_routines()[0]

    def test_editing_loads_the_routine_into_the_form(self, panel):
        routine = self._create(panel)
        panel._on_edit(routine["id"])

        assert panel.name_edit.text() == "Weekend plan"
        assert panel.instruction_edit.toPlainText() == "Check the weather."
        assert panel.save_button.text() == "Save changes"
        assert panel.cancel_button.isVisibleTo(panel)

    def test_saving_an_edit_updates_rather_than_duplicates(self, panel):
        routine = self._create(panel)
        panel._on_edit(routine["id"])
        panel.instruction_edit.setPlainText("Check the weather and the news.")
        panel._save()

        routines = panel.db.list_routines()
        assert len(routines) == 1
        assert routines[0]["instruction"] == "Check the weather and the news."

    def test_its_own_name_is_not_a_duplicate_of_itself(self, panel):
        routine = self._create(panel)
        panel._on_edit(routine["id"])
        panel._save()

        assert "already" not in panel.status_label.text().lower()

    def test_changing_the_schedule_recomputes_the_next_run(self, panel):
        from PySide6.QtCore import QTime

        routine = self._create(panel)
        before = routine["next_run"]
        panel._on_edit(routine["id"])
        panel.repeat_combo.setCurrentIndex(0)
        panel.time_edit.setTime(QTime(23, 59))
        panel._save()

        after = panel.db.list_routines()[0]
        assert after["next_run"] != before
        assert after["schedule"]["kind"] == "daily"

    def test_cancelling_leaves_the_routine_alone(self, panel):
        routine = self._create(panel)
        panel._on_edit(routine["id"])
        panel.name_edit.setText("Something else")
        panel._clear_form()

        assert panel.db.list_routines()[0]["name"] == "Weekend plan"
        assert panel.save_button.text() == "Create routine"

    def test_a_weekend_routine_comes_back_as_weekends_not_custom(self, panel):
        """Round trip: what the form wrote, the form must read back."""
        routine = self._create(panel)
        panel._on_edit(routine["id"])

        assert panel.repeat_combo.currentIndex() == 2  # Weekends


class TestTheButtonsOnEachRow:
    def _create(self, panel):
        _fill(panel, "Day in history")
        panel._save()
        return panel.db.list_routines()[0]

    def test_run_now_asks_the_core_by_name(self, panel):
        routine = self._create(panel)
        panel._on_run_now(routine["id"])

        assert panel.ran == ["Day in history"]

    def test_disabling_stops_it_without_deleting_it(self, panel):
        routine = self._create(panel)
        panel._on_toggled(routine["id"], False)

        stored = panel.db.get_routine(routine["id"])
        assert stored is not None
        assert not stored["enabled"]

    def test_re_enabling_moves_a_stale_next_run_forward(self, panel):
        """Otherwise the no-catch-up rule fires the instant it is switched on.

        A routine disabled for a week has a next_run a week in the past. Turn
        it back on without recomputing and the scheduler sees an overdue
        routine — which it skips and silently reschedules, so the first thing
        the user gets after enabling it is nothing at all.
        """
        routine = self._create(panel)
        stale = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
        panel.db.update_routine(routine["id"], enabled=0, next_run=stale)

        panel._on_toggled(routine["id"], True)

        stored = panel.db.get_routine(routine["id"])
        assert stored["enabled"]
        assert datetime.datetime.fromisoformat(stored["next_run"]) > datetime.datetime.now()

    def test_re_enabling_clears_the_failure_count(self, panel):
        routine = self._create(panel)
        panel.db.update_routine(routine["id"], enabled=0, fail_count=3)

        panel._on_toggled(routine["id"], True)

        assert panel.db.get_routine(routine["id"])["fail_count"] == 0

    def test_deleting_asks_first(self, panel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        routine = self._create(panel)
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Cancel)
        panel._on_remove(routine["id"])

        assert len(panel.db.list_routines()) == 1, "cancel must not delete"

        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Yes)
        panel._on_remove(routine["id"])

        assert panel.db.list_routines() == []

    def test_deleting_the_routine_being_edited_clears_the_form(self, panel, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        routine = self._create(panel)
        panel._on_edit(routine["id"])
        monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.Yes)
        panel._on_remove(routine["id"])

        assert panel.name_edit.text() == ""
        assert panel.save_button.text() == "Create routine"


class TestTheList:
    def test_reloading_does_not_leave_the_old_rows_behind(self, panel):
        _fill(panel, "One")
        panel._save()
        _fill(panel, "Two")
        panel._save()

        for _ in range(4):
            panel.reload()

        assert panel._list.count() == 2

    def test_the_empty_state_explains_what_a_routine_is(self, panel):
        assert panel._empty.isVisibleTo(panel)
        assert "on its own" in panel._empty.text()

    def test_it_survives_being_opened_before_the_core_boots(self, qt_app):
        widget = RoutinesPanel(lambda: None, lambda name: None)
        widget.reload()  # must not raise

        assert widget._list.count() == 0


class TestNextRunWording:
    def _routine(self, next_run, enabled=True):
        return {"enabled": enabled, "next_run": next_run}

    def test_today(self):
        soon = datetime.datetime.now().replace(hour=23, minute=15)
        assert describe_next_run(self._routine(soon.isoformat())) == "next today at 23:15"

    def test_tomorrow(self):
        later = (datetime.datetime.now() + datetime.timedelta(days=1)).replace(
            hour=8, minute=0)
        assert "tomorrow" in describe_next_run(self._routine(later.isoformat()))

    def test_further_off_gets_a_date(self):
        later = datetime.datetime(2027, 3, 9, 8, 0)
        assert "09 Mar" in describe_next_run(self._routine(later.isoformat()))

    def test_a_disabled_routine_says_so_instead_of_a_time(self):
        assert describe_next_run(self._routine("2027-01-01T08:00", False)) == "disabled"

    def test_a_broken_timestamp_does_not_crash_the_list(self):
        assert describe_next_run(self._routine("not a date")) == "not scheduled"
