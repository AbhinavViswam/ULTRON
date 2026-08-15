"""Routines: scheduled instructions Ultron carries out.

The scheduling half is pure arithmetic and gets the heaviest coverage, because
a routine can *act* — so firing on the wrong day is not a cosmetic bug.
"""

import datetime

import pytest

from ultron import routines as sched


class TestParsingTime:
    @pytest.mark.parametrize("spoken,expected", [
        ("08:00", "08:00"), ("8:00", "08:00"), ("17:30", "17:30"),
        ("7 pm", "19:00"), ("7pm", "19:00"), ("7 p.m.", "19:00"),
        ("12 am", "00:00"), ("12 pm", "12:00"), ("9", "09:00"),
    ])
    def test_accepted_forms(self, spoken, expected):
        assert sched.parse_time(spoken) == expected

    @pytest.mark.parametrize("spoken", ["", "later", "25:00", "8:70", "half past"])
    def test_rejected_forms(self, spoken):
        with pytest.raises(sched.ScheduleError):
            sched.parse_time(spoken)


class TestParsingDays:
    @pytest.mark.parametrize("spoken,expected", [
        ("monday", [0]),
        ("mon,tue,sat", [0, 1, 5]),
        ("monday and friday", [0, 4]),
        ("Mondays, Wednesdays", [0, 2]),
        ("sun", [6]),
    ])
    def test_named_days(self, spoken, expected):
        assert sched.parse_days(spoken) == expected

    def test_duplicates_collapse(self):
        assert sched.parse_days("mon, monday, mondays") == [0]

    def test_nothing_recognised(self):
        assert sched.parse_days("whenever") == []


class TestParsingSchedules:
    def test_daily(self):
        assert sched.parse_schedule("daily", "08:00")["kind"] == "daily"

    def test_weekdays(self):
        parsed = sched.parse_schedule("weekdays", "09:00")
        assert parsed["kind"] == "weekly" and parsed["days"] == [0, 1, 2, 3, 4]

    def test_weekends(self):
        assert sched.parse_schedule("weekends", "10:00")["days"] == [5, 6]

    def test_custom_days(self):
        """The case that motivated this: 'monday, tuesday and saturday'."""
        parsed = sched.parse_schedule("monday, tuesday and saturday", "07:30")
        assert parsed["kind"] == "weekly" and parsed["days"] == [0, 1, 5]

    def test_every_n_days(self):
        parsed = sched.parse_schedule("every 3 days", "08:00")
        assert parsed["kind"] == "interval" and parsed["every_n_days"] == 3

    def test_monthly_with_a_day(self):
        parsed = sched.parse_schedule("monthly on the 15th", "08:00")
        assert parsed["kind"] == "monthly" and parsed["day_of_month"] == 15

    def test_one_off_date(self):
        parsed = sched.parse_schedule("2026-08-26", "06:00")
        assert parsed["kind"] == "once" and parsed["once_date"] == "2026-08-26"

    def test_the_time_is_carried_through(self):
        assert sched.parse_schedule("daily", "7 pm")["at_time"] == "19:00"

    @pytest.mark.parametrize("spoken", ["", "sometimes", "when I feel like it", "once"])
    def test_nonsense_is_rejected(self, spoken):
        with pytest.raises(sched.ScheduleError):
            sched.parse_schedule(spoken, "08:00")

    def test_a_bad_interval_is_rejected(self):
        with pytest.raises(sched.ScheduleError):
            sched.parse_schedule("every 0 days", "08:00")


class TestNextRun:
    MONDAY = datetime.datetime(2026, 8, 17, 12, 0)      # a Monday, midday

    def test_daily_later_today(self):
        parsed = sched.parse_schedule("daily", "18:00")
        assert sched.next_run(parsed, self.MONDAY) == \
            datetime.datetime(2026, 8, 17, 18, 0)

    def test_daily_already_passed_goes_to_tomorrow(self):
        parsed = sched.parse_schedule("daily", "08:00")
        assert sched.next_run(parsed, self.MONDAY) == \
            datetime.datetime(2026, 8, 18, 8, 0)

    def test_custom_days_picks_the_next_named_day(self):
        parsed = sched.parse_schedule("saturday", "09:00")
        assert sched.next_run(parsed, self.MONDAY).weekday() == 5

    def test_custom_days_across_a_week(self):
        parsed = sched.parse_schedule("monday, tuesday and saturday", "09:00")
        # From Monday midday: Tuesday is next.
        first = sched.next_run(parsed, self.MONDAY)
        assert first.weekday() == 1
        # Then Saturday, then Monday again.
        second = sched.next_run(parsed, first)
        assert second.weekday() == 5
        assert sched.next_run(parsed, second).weekday() == 0

    def test_weekdays_skips_the_weekend(self):
        parsed = sched.parse_schedule("weekdays", "09:00")
        friday = datetime.datetime(2026, 8, 21, 12, 0)
        assert sched.next_run(parsed, friday).weekday() == 0

    def test_monthly_lands_on_the_named_day(self):
        parsed = sched.parse_schedule("monthly on the 15th", "08:00")
        assert sched.next_run(parsed, self.MONDAY).day == 15

    def test_monthly_on_the_31st_still_fires_in_february(self):
        """Skipping the month entirely would be worse than firing on the 28th."""
        parsed = sched.parse_schedule("monthly on the 31st", "08:00")
        february = datetime.datetime(2026, 2, 1, 0, 0)
        landed = sched.next_run(parsed, february)
        assert landed.month == 2 and landed.day == 28

    def test_interval_counts_from_the_anchor(self):
        parsed = sched.parse_schedule("every 3 days", "08:00")
        anchor = datetime.date(2026, 8, 17)
        first = sched.next_run(parsed, self.MONDAY, anchor=anchor)
        assert first.date() == datetime.date(2026, 8, 20)

    def test_a_one_off_in_the_future(self):
        parsed = sched.parse_schedule("2026-08-26", "06:00")
        assert sched.next_run(parsed, self.MONDAY) == \
            datetime.datetime(2026, 8, 26, 6, 0)

    def test_a_spent_one_off_has_no_next_run(self):
        """None is the signal to retire the routine, not an error."""
        parsed = sched.parse_schedule("2020-01-01", "06:00")
        assert sched.next_run(parsed, self.MONDAY) is None

    def test_the_result_is_always_in_the_future(self):
        for when in ("daily", "weekdays", "saturday", "every 2 days",
                     "monthly on the 15th"):
            parsed = sched.parse_schedule(when, "08:00")
            assert sched.next_run(parsed, self.MONDAY) > self.MONDAY

    def test_scheduling_never_drifts(self):
        """Stepping ten times must keep the same clock time throughout.

        Computing 'now + a day' instead is what silently moved a 10:30
        reminder to 12:30 permanently.
        """
        parsed = sched.parse_schedule("daily", "08:00")
        moment = self.MONDAY
        for _ in range(10):
            moment = sched.next_run(parsed, moment)
            assert moment.strftime("%H:%M") == "08:00"


class TestDescribing:
    @pytest.mark.parametrize("when,at,expected", [
        ("daily", "08:00", "every day at 08:00"),
        ("weekdays", "09:00", "weekdays at 09:00"),
        ("weekends", "10:00", "weekends at 10:00"),
        ("monday and friday", "07:30", "every Monday, Friday at 07:30"),
        ("every 3 days", "08:00", "every 3 days at 08:00"),
        ("monthly on the 15th", "08:00", "on the 15th of each month at 08:00"),
        ("2026-08-26", "06:00", "once on 2026-08-26 at 06:00"),
    ])
    def test_reads_back_in_english(self, when, at, expected):
        assert sched.describe(sched.parse_schedule(when, at)) == expected

    @pytest.mark.parametrize("day,suffix", [
        (1, "st"), (2, "nd"), (3, "rd"), (4, "th"),
        (11, "th"), (12, "th"), (13, "th"), (21, "st"), (22, "nd"),
    ])
    def test_ordinals(self, day, suffix):
        assert sched._ordinal(day) == suffix


class TestStorage:
    def test_a_routine_survives_a_round_trip(self, brain):
        schedule = sched.parse_schedule("monday, tuesday and saturday", "07:30")
        following = sched.next_run(schedule)
        rid = brain.db.add_routine("Day in history", "Search for today's events",
                                   schedule, following.isoformat(), "speak,card")
        stored = brain.db.get_routine(rid)
        assert stored["name"] == "Day in history"
        assert stored["schedule"]["days"] == [0, 1, 5]
        assert stored["schedule"]["at_time"] == "07:30"
        assert stored["enabled"] is True

    def test_updating_only_touches_known_columns(self, brain):
        schedule = sched.parse_schedule("daily", "08:00")
        rid = brain.db.add_routine("x", "y", schedule,
                                   sched.next_run(schedule).isoformat())
        brain.db.update_routine(rid, last_result="found something",
                                nonsense="ignored")
        assert brain.db.get_routine(rid)["last_result"] == "found something"

    def test_deleting(self, brain):
        schedule = sched.parse_schedule("daily", "08:00")
        rid = brain.db.add_routine("x", "y", schedule,
                                   sched.next_run(schedule).isoformat())
        assert brain.db.delete_routine(rid) is True
        assert brain.db.get_routine(rid) is None
        assert brain.db.delete_routine(rid) is False


class TestTools:
    def _create(self, brain, name="Day in history", when="daily", at="08:00"):
        return brain._invoke_tool("create_routine", {
            "name": name, "instruction": "Search for what is notable today",
            "when": when, "at_time": at,
        })

    def test_creating(self, brain):
        result = self._create(brain)
        assert "created" in result
        assert len(brain.db.list_routines()) == 1

    def test_creating_reads_the_schedule_back(self, brain):
        assert "every day at 08:00" in self._create(brain)

    def test_custom_days_through_the_tool(self, brain):
        result = self._create(brain, when="monday, tuesday and saturday")
        assert "Monday, Tuesday, Saturday" in result
        assert brain.db.list_routines()[0]["schedule"]["days"] == [0, 1, 5]

    def test_a_bad_schedule_is_refused(self, brain):
        result = brain._invoke_tool("create_routine", {
            "name": "x", "instruction": "y", "when": "whenever I feel like it"})
        assert result.startswith("Error")
        assert brain.db.list_routines() == []

    def test_duplicate_names_are_refused(self, brain):
        self._create(brain)
        assert "already exists" in self._create(brain)

    def test_a_routine_needs_an_instruction(self, brain):
        result = brain._invoke_tool("create_routine",
                                    {"name": "x", "instruction": "", "when": "daily"})
        assert result.startswith("Error")

    def test_listing(self, brain):
        self._create(brain)
        listing = brain._invoke_tool("list_routines", {})
        assert "Day in history" in listing and "every day at 08:00" in listing

    def test_listing_when_empty(self, brain):
        assert "No routines" in brain._invoke_tool("list_routines", {})

    def test_disable_and_enable(self, brain):
        self._create(brain)
        brain._invoke_tool("disable_routine", {"name": "Day in history"})
        assert brain.db.list_routines()[0]["enabled"] is False
        brain._invoke_tool("enable_routine", {"name": "Day in history"})
        assert brain.db.list_routines()[0]["enabled"] is True

    def test_rescheduling(self, brain):
        self._create(brain)
        result = brain._invoke_tool("reschedule_routine",
                                    {"name": "Day in history", "when": "weekends",
                                     "at_time": "10:00"})
        assert "weekends at 10:00" in result
        assert brain.db.list_routines()[0]["schedule"]["days"] == [5, 6]

    def test_resolving_by_partial_name(self, brain):
        self._create(brain)
        routine, problem = brain._resolve_routine("history")
        assert problem is None and routine["name"] == "Day in history"

    def test_ambiguous_names_refuse(self, brain):
        self._create(brain, name="Morning news")
        self._create(brain, name="Morning email")
        routine, problem = brain._resolve_routine("morning")
        assert routine is None and "matches 2 routines" in problem

    def test_an_unknown_name_is_reported(self, brain):
        self._create(brain)
        routine, problem = brain._resolve_routine("nonexistent")
        assert routine is None and problem.startswith("Error")

    def test_deleting_is_gated(self, brain, refuse_all):
        self._create(brain)
        result = brain._invoke_tool("delete_routine", {"name": "Day in history"})
        assert result.startswith("Error")
        assert len(brain.db.list_routines()) == 1

    def test_deleting_when_approved(self, brain, approve_all):
        self._create(brain)
        brain._invoke_tool("delete_routine", {"name": "Day in history"})
        assert brain.db.list_routines() == []

    def test_the_confirmation_names_the_routine(self, brain, approve_all):
        self._create(brain)
        brain._invoke_tool("delete_routine", {"name": "history"})
        assert approve_all[-1] == "delete the routine 'Day in history'"

    def test_run_now_queues_rather_than_recursing(self, brain):
        """Running a turn inside a turn would tangle the history."""
        queued = []
        brain.routine_runner = queued.append
        self._create(brain)
        result = brain._invoke_tool("run_routine_now", {"name": "Day in history"})
        assert "Running" in result
        assert len(queued) == 1 and queued[0]["name"] == "Day in history"

    def test_run_now_without_a_runner(self, brain):
        brain.routine_runner = None
        self._create(brain)
        assert brain._invoke_tool(
            "run_routine_now", {"name": "Day in history"}).startswith("Error")

    def test_last_result_is_recoverable(self, brain):
        self._create(brain)
        rid = brain.db.list_routines()[0]["id"]
        brain.db.update_routine(rid, last_result="Today is Onam.",
                                last_run="2026-08-26T08:00:00")
        assert "Onam" in brain._invoke_tool(
            "last_routine_result", {"name": "Day in history"})


class TestUnattendedSafety:
    """A routine runs with nobody present, so it must not be able to destroy."""

    def test_destructive_tools_are_refused_while_unattended(self, brain, approve_all, tmp_path):
        probe = tmp_path / "survives.txt"
        probe.write_text("x")
        brain.unattended = True
        try:
            result = brain._invoke_tool("delete_file", {"file_path": str(probe)})
        finally:
            brain.unattended = False
        assert result.startswith("Error")
        assert probe.exists()
        assert not approve_all, "must not even ask — nobody is there to answer"

    def test_the_error_steers_the_model_to_reporting(self, brain):
        brain.unattended = True
        try:
            result = brain._invoke_tool("empty_recycle_bin", {})
        finally:
            brain.unattended = False
        assert "scheduled routine" in result
        assert "Report what you found instead" in result

    @pytest.mark.parametrize("tool,args", [
        ("open_application", {"app_name": "spotify"}),
        ("get_system_health", {}),
        ("list_routines", {}),
    ])
    def test_ordinary_tools_still_work_unattended(self, brain, tool, args):
        """Routines can act — only the destructive ones are off limits."""
        brain.unattended = True
        try:
            assert "cannot run inside a scheduled routine" not in str(
                brain._check_confirmation(tool, args) or "")
        finally:
            brain.unattended = False


class TestNoCatchUp:
    """A missed routine is skipped, never run late.

    Routines can act, and an action taken hours after its moment is at best
    surprising. If Ultron was not running at 08:00, the 08:00 routine did not
    happen.
    """

    def test_the_grace_window_is_short(self):
        from ultron.core import ROUTINE_MISSED_AFTER_SECONDS

        assert 0 < ROUTINE_MISSED_AFTER_SECONDS <= 900

    def test_the_poll_is_faster_than_the_grace_window(self):
        """Otherwise a routine could be missed purely by polling too slowly."""
        from ultron.core import ROUTINE_MISSED_AFTER_SECONDS, ROUTINE_POLL_SECONDS

        assert ROUTINE_POLL_SECONDS < ROUTINE_MISSED_AFTER_SECONDS

    def test_a_missed_routine_reschedules_to_the_next_slot(self, brain):
        """The skipped run must not leave next_run stuck in the past."""
        schedule = sched.parse_schedule("daily", "08:00")
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        rid = brain.db.add_routine("x", "y", schedule, yesterday.isoformat())

        stored = brain.db.get_routine(rid)
        following = sched.next_run(stored["schedule"])
        brain.db.update_routine(rid, next_run=following.isoformat())

        assert datetime.datetime.fromisoformat(
            brain.db.get_routine(rid)["next_run"]) > datetime.datetime.now()
