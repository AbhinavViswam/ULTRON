"""Routines: instructions Ultron carries out on a schedule.

A reminder says a fixed sentence. A workflow runs fixed steps when asked. A
routine is the third thing — at a chosen time it *thinks*: runs an instruction
through the model with tools available, and reports or acts on what it finds.

Everything in this module is pure scheduling arithmetic with no model, no
database and no I/O, so the hard part — which day, which time, and never
drifting — can be tested on its own.
"""

import datetime
import re

# Monday is 0, matching datetime.weekday().
WEEKDAY_NUMBERS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]

# How models and people actually write them.
_WEEKDAY_ALIASES = {}
for _name, _number in WEEKDAY_NUMBERS.items():
    _WEEKDAY_ALIASES[_name] = _number
    _WEEKDAY_ALIASES[_name[:3]] = _number          # mon
    _WEEKDAY_ALIASES[_name + "s"] = _number        # mondays
    _WEEKDAY_ALIASES[_name[:3] + "s"] = _number    # mons

WEEKDAYS = [0, 1, 2, 3, 4]
WEEKEND = [5, 6]

KINDS = ("daily", "weekly", "monthly", "interval", "once")

# A schedule is never searched further ahead than this. Beyond it, something
# is wrong with the schedule rather than merely distant.
_MAX_LOOKAHEAD_DAYS = 400


class ScheduleError(ValueError):
    """The schedule could not be understood."""


def parse_time(text: str) -> str:
    """Normalises a spoken time to 'HH:MM'. Raises ScheduleError if it cannot."""
    raw = str(text or "").strip().lower().replace(".", "")
    if not raw:
        raise ScheduleError("no time given")

    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if not match:
        raise ScheduleError(f"'{text}' is not a time like '08:00' or '7 pm'")

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"'{text}' is not a valid time of day")
    return f"{hour:02d}:{minute:02d}"


def parse_days(text: str) -> list:
    """Reads 'mon, tue and sat' into [0, 1, 5]."""
    found = []
    for word in re.split(r"[,\s]+|\band\b", str(text or "").lower()):
        word = word.strip(".,")
        if word in _WEEKDAY_ALIASES:
            number = _WEEKDAY_ALIASES[word]
            if number not in found:
                found.append(number)
    return sorted(found)


def parse_schedule(when: str, at_time: str = "08:00") -> dict:
    """Turns a spoken schedule into the stored form.

    Deliberately not cron syntax. '0 8 * * 1,2,6' cannot be said out loud or
    read back to someone, and this is an assistant people talk to.

    Accepts: daily, weekdays, weekends, a list of weekdays, 'every 3 days',
    'monthly on the 15th', and a one-off date.
    """
    text = str(when or "").strip().lower()
    if not text:
        raise ScheduleError("no schedule given")

    time_of_day = parse_time(at_time)
    schedule = {"kind": "daily", "days": [], "day_of_month": 0,
                "every_n_days": 0, "at_time": time_of_day, "once_date": ""}

    # One-off on a specific date.
    date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if date_match or text.startswith("once"):
        if not date_match:
            raise ScheduleError("a one-off routine needs a date like 2026-08-26")
        schedule["kind"] = "once"
        schedule["once_date"] = date_match.group(0)
        return schedule

    every = re.search(r"every\s+(\d+)\s+day", text)
    if every:
        count = int(every.group(1))
        if count < 1:
            raise ScheduleError("'every N days' needs N of at least 1")
        schedule["kind"] = "interval"
        schedule["every_n_days"] = count
        return schedule

    if "month" in text:
        day = re.search(r"(\d{1,2})", text)
        schedule["kind"] = "monthly"
        # No day named means "the same day I set it", resolved by the caller.
        schedule["day_of_month"] = int(day.group(1)) if day else 0
        if schedule["day_of_month"] and not 1 <= schedule["day_of_month"] <= 31:
            raise ScheduleError("a day of the month must be between 1 and 31")
        return schedule

    if "weekday" in text or "week day" in text:
        schedule["kind"] = "weekly"
        schedule["days"] = list(WEEKDAYS)
        return schedule

    if "weekend" in text:
        schedule["kind"] = "weekly"
        schedule["days"] = list(WEEKEND)
        return schedule

    named = parse_days(text)
    if named:
        schedule["kind"] = "weekly"
        schedule["days"] = named
        return schedule

    if "dai" in text or "every day" in text or "everyday" in text:
        return schedule

    raise ScheduleError(
        f"'{when}' is not a schedule I understand. Try 'daily', 'weekdays', "
        "'monday and friday', 'every 3 days', 'monthly on the 15th', or a date."
    )


def _day_matches(schedule: dict, day: datetime.date, anchor: datetime.date) -> bool:
    kind = schedule.get("kind", "daily")
    if kind == "daily":
        return True
    if kind == "weekly":
        return day.weekday() in (schedule.get("days") or [])
    if kind == "monthly":
        wanted = int(schedule.get("day_of_month") or anchor.day)
        if day.day == wanted:
            return True
        # The 31st does not exist in every month; fire on the last day instead
        # of skipping the month entirely.
        following = day + datetime.timedelta(days=1)
        return day.day < wanted and following.month != day.month
    if kind == "interval":
        step = max(1, int(schedule.get("every_n_days") or 1))
        return (day - anchor).days % step == 0
    if kind == "once":
        return day.isoformat() == schedule.get("once_date", "")
    return False


def next_run(schedule: dict, after: datetime.datetime = None,
             anchor: datetime.date = None):
    """The next moment this schedule is due, strictly after `after`.

    Returns None when a schedule has no future occurrence — a one-off whose
    date has passed — which is the caller's signal to retire the routine.

    Always computed from the calendar, never from 'now plus an interval', so a
    routine that runs late cannot drag its own schedule later. That mistake
    silently moved a 10:30 reminder to 12:30 permanently, and it is not worth
    repeating here.
    """
    after = after or datetime.datetime.now()
    anchor = anchor or after.date()
    hour, minute = (int(part) for part in schedule.get("at_time", "08:00").split(":"))

    day = after.date()
    for _ in range(_MAX_LOOKAHEAD_DAYS):
        if _day_matches(schedule, day, anchor):
            moment = datetime.datetime.combine(day, datetime.time(hour, minute))
            if moment > after:
                return moment
        day += datetime.timedelta(days=1)
    return None


def describe(schedule: dict) -> str:
    """A plain-English schedule, for reading a routine list back to someone."""
    at = schedule.get("at_time", "08:00")
    kind = schedule.get("kind", "daily")

    if kind == "daily":
        return f"every day at {at}"
    if kind == "weekly":
        days = schedule.get("days") or []
        if days == WEEKDAYS:
            return f"weekdays at {at}"
        if days == WEEKEND:
            return f"weekends at {at}"
        names = ", ".join(WEEKDAY_NAMES[d] for d in days)
        return f"every {names} at {at}"
    if kind == "monthly":
        day = schedule.get("day_of_month") or 1
        return f"on the {day}{_ordinal(day)} of each month at {at}"
    if kind == "interval":
        step = schedule.get("every_n_days") or 1
        return f"every {step} days at {at}" if step > 1 else f"every day at {at}"
    if kind == "once":
        return f"once on {schedule.get('once_date', '?')} at {at}"
    return f"at {at}"


def _ordinal(number: int) -> str:
    if 11 <= number % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
