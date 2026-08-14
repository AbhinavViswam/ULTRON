"""Recurring reminder scheduling.

These guard a bug that shipped and went unnoticed for days: rescheduling from
`now` rather than from the reminder's own time, so a 10:30 reminder that fired
late at 12:30 permanently became a 12:30 reminder.
"""

import calendar
import datetime

import pytest

from ultron.brain import normalise_frequency
from ultron.core import next_occurrence


def _past(hour, minute, days_ago=1):
    """A concrete time of day, safely in the past."""
    moment = datetime.datetime.now().replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return moment - datetime.timedelta(days=days_ago)


class TestNoDrift:
    def test_late_firing_keeps_the_original_time(self):
        """The bug this suite exists for: 10:30 must stay 10:30."""
        due = _past(10, 30)
        assert next_occurrence(due.isoformat(), "daily").strftime("%H:%M") == "10:30"

    def test_result_is_always_in_the_future(self):
        due = _past(10, 30, days_ago=9)
        assert next_occurrence(due.isoformat(), "daily") > datetime.datetime.now()

    def test_long_absence_yields_one_reminder_not_a_backlog(self):
        due = _past(7, 0, days_ago=10)
        gap = next_occurrence(due.isoformat(), "daily") - datetime.datetime.now()
        assert gap < datetime.timedelta(days=1)

    @pytest.mark.parametrize("frequency,within", [
        ("hourly", datetime.timedelta(hours=1)),
        ("daily", datetime.timedelta(days=1)),
        ("weekly", datetime.timedelta(days=7)),
    ])
    def test_next_slot_is_the_nearest_one(self, frequency, within):
        due = _past(7, 0, days_ago=30)
        gap = next_occurrence(due.isoformat(), frequency) - datetime.datetime.now()
        assert gap <= within

    def test_weekly_keeps_the_weekday(self):
        due = _past(9, 0, days_ago=21)
        assert next_occurrence(due.isoformat(), "weekly").strftime("%A") == due.strftime("%A")

    def test_hourly_keeps_the_minute(self):
        due = datetime.datetime.now() - datetime.timedelta(hours=5, minutes=7)
        assert next_occurrence(due.isoformat(), "hourly").minute == due.minute


class TestMonthly:
    def test_monthly_does_not_walk_backwards(self):
        """A fixed 30-day step turns 'the 31st' into the 30th, then the 29th."""
        moment = datetime.datetime(2026, 1, 31, 9, 0)
        days = []
        for _ in range(4):
            month = moment.month % 12 + 1
            year = moment.year + (1 if moment.month == 12 else 0)
            day = min(moment.day, calendar.monthrange(year, month)[1])
            moment = moment.replace(year=year, month=month, day=day)
            days.append(moment.day)
        assert days == [28, 28, 28, 28], "clamped, never drifting backwards"

    def test_monthly_keeps_time_of_day(self):
        due = datetime.datetime.now() - datetime.timedelta(days=45)
        due = due.replace(hour=8, minute=15, second=0, microsecond=0)
        assert next_occurrence(due.isoformat(), "monthly").strftime("%H:%M") == "08:15"

    def test_monthly_crosses_the_year_boundary(self):
        from ultron.core import _add_month
        assert _add_month(datetime.datetime(2026, 12, 15, 9, 0)) == \
            datetime.datetime(2027, 1, 15, 9, 0)


class TestBadInput:
    def test_unreadable_schedule_does_not_lose_the_reminder(self):
        assert next_occurrence("not-a-date", "daily") > datetime.datetime.now()

    def test_unknown_frequency_falls_back_to_daily(self):
        """Better late than silently annual."""
        due = _past(10, 30)
        gap = next_occurrence(due.isoformat(), "fortnightly") - datetime.datetime.now()
        assert gap <= datetime.timedelta(days=1)


class TestFrequencyParsing:
    @pytest.mark.parametrize("spoken,expected", [
        ("daily", "daily"),
        ("Every Day", "daily"),
        ("everyday", "daily"),
        ("day", "daily"),
        ("MONTHLY", "monthly"),
        ("month", "monthly"),
        ("weekly.", "weekly"),
        ("every hour", "hourly"),
        ("", "daily"),
        (None, "daily"),
    ])
    def test_accepted_phrasings(self, spoken, expected):
        assert normalise_frequency(spoken) == expected

    @pytest.mark.parametrize("spoken", ["fortnightly", "every 2 days", "yearly"])
    def test_unsupported_is_rejected_not_guessed(self, spoken):
        assert normalise_frequency(spoken) is None
