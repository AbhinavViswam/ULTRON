"""Breaking the silence after a long quiet spell.

An assistant that only ever reacts feels like a command line with a voice.
This is the part that speaks first — occasionally, when nothing has happened
for a while, in a way that has something to do with the person it is talking
to rather than a generic "still there?".

The hard part is not deciding what to say. It is deciding when saying nothing
is better, which is most of the time.
"""

import ctypes
import datetime
import random

from ultron.config import config

# Nothing to do with the user for this long makes it fair to speak up.
DEFAULT_IDLE_MINUTES = 25

# Silence overnight, so it never starts talking at 2am.
DEFAULT_QUIET_START = 22
DEFAULT_QUIET_END = 8

# Kept short: this is an opener, not a monologue, and it interrupts a silence
# the user may have been enjoying.
MAX_WORDS = 30

# Used when the model is unreachable — better a plain line than a failure the
# user never sees. Also what makes the feature survive losing the network.
FALLBACK_LINES = [
    "Still there, sir?",
    "It has gone quiet. Anything I can take off your plate?",
    "Let me know if you need anything, sir.",
    "I am here whenever you need me.",
]

# Things Ultron can genuinely deliver if the user says yes.
#
# Without this the model invents plausible assistant work — "compile your
# recent performance data", "organise your files for review" — and saying yes
# sends it hunting for a tool that does not exist. An offer it cannot keep is
# worse than not speaking at all.
OFFER_LINES = [
    "play a song, artist or album on Spotify",
    "check for unread email",
    "research a topic in the background and write it up",
    "search the web for something, or read out the news",
    "check battery, CPU or disk health",
    "set a reminder for something new",
    "open an app, a folder, or a website",
    "read a document, PDF or the file open in Explorer",
    "tell them what you remember about them",
]


def offers() -> str:
    """The capability list, in a different order each time.

    A small model latches onto whichever item it reads first and offers the
    same thing all day. Shuffling costs nothing and is the difference between
    variety and a stuck record.
    """
    shuffled = random.sample(OFFER_LINES, len(OFFER_LINES))
    return "\n".join(f"- {line}" for line in shuffled)

PROMPT = """You are Ultron, {user}'s assistant. Nothing has happened for
{minutes} minutes. Say ONE short thing, the way someone who works alongside
them would.

It is {when} on {weekday}, {clock}.
{context}

Rules:
- One sentence, under {max_words} words.
- If anything above is worth remarking on, remark on it. Noticing something
  true is always better than offering a service.
- Only if there is nothing worth remarking on, offer exactly ONE of these,
  which are the only things you can actually do:
{offers}
- Say "sir" at most once, and not in every line.
- Never invent something you noticed. You cannot see their screen, their
  mood, their habits, or what they are working on. Everything you remark on
  must come from the facts above and nowhere else.
- Anything listed as ALREADY SET is DONE. Never offer to set, schedule or
  remind them of it again. Ask about it instead, or offer something that
  would help them get ready for it.
- If there is nothing above at all, just offer one thing.
  Do not say there is nothing scheduled, and never mention being idle.
- Do not greet them as though they just arrived.
- Reply with the sentence only. No quotes, no preamble, no tool calls."""


def in_quiet_hours(now=None, start=None, end=None) -> bool:
    """True during the overnight window when nothing may be said.

    The window wraps midnight, so it cannot be a simple 'between' test.
    """
    now = now or datetime.datetime.now()
    start = DEFAULT_QUIET_START if start is None else start
    end = DEFAULT_QUIET_END if end is None else end
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def is_fullscreen_app_in_front() -> bool:
    """True when the focused window covers the whole screen.

    Games, video and presentations all look like this, and all of them are
    moments where speaking up would be unwelcome.
    """
    try:
        user32 = ctypes.windll.user32
        window = user32.GetForegroundWindow()
        if not window:
            return False

        class Rect(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = Rect()
        if not user32.GetWindowRect(window, ctypes.byref(rect)):
            return False
        # The desktop itself is fullscreen by definition and is not an app.
        if window in (user32.GetDesktopWindow(), user32.GetShellWindow()):
            return False
        return (rect.right - rect.left >= user32.GetSystemMetrics(0)
                and rect.bottom - rect.top >= user32.GetSystemMetrics(1))
    except Exception as e:
        print(f"[Idle] could not check the foreground window: {e}")
        return False


# How many memories a hosted model is given. Kept small because every one of
# them is billed on every idle nudge, all day, forever.
HOSTED_MEMORY_SAMPLE = 2


def choose_memories(memories: list) -> list:
    """Which memories to put in front of the model.

    A local model costs nothing per token, so it gets everything — the whole
    picture, and no sampling to make it repeat itself. A hosted model is paid
    for by the token on every nudge, so it gets a small random sample of the
    lower-importance facts, which is the conversational material anyway.
    """
    if config.active_provider() == "localapi":
        return list(memories)

    interesting = [
        m for m in memories
        if m["importance"] <= 8 and m["category"] not in ("personal_contact",)
    ] or memories
    return random.sample(interesting, min(HOSTED_MEMORY_SAMPLE, len(interesting)))


def _time_of_day(now: datetime.datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


# A battery this low is worth interrupting someone for; above it, they can
# see the same icon Ultron can and do not need telling.
LOW_BATTERY_PERCENT = 25

# Free space below this stops being a statistic and starts being a problem.
LOW_DISK_PERCENT = 10


def machine_observations() -> list:
    """Things about the machine that are worth saying out loud.

    An assistant that only offers services is a menu with a voice; noticing
    is what makes one feel present. But the bar is *notable*, not merely
    true, and the difference matters more than it sounds.

    The first version of this reported the time of day. Every remark after
    8pm became "it is 22:20, in the evening" — true every time, worth saying
    none of them, and a stuck record for two hours nightly. Two tests caught
    it. A clock reading, the day of the week and a healthy battery are all
    things the user can already see; only the ones they would want
    interrupting for belong here.
    """
    seen = []
    try:
        import psutil
    except Exception as e:
        print(f"[Idle] psutil unavailable, nothing to notice: {e}")
        return seen

    try:
        battery = (psutil.sensors_battery()
                   if hasattr(psutil, "sensors_battery") else None)
        if (battery is not None and not battery.power_plugged
                and battery.percent <= LOW_BATTERY_PERCENT):
            seen.append(f"Their battery is at {int(battery.percent)}% "
                        f"and not charging.")
    except Exception as e:
        print(f"[Idle] could not read the battery: {e}")

    try:
        disk = psutil.disk_usage("/")
        free_percent = disk.free / disk.total * 100 if disk.total else 100
        if free_percent <= LOW_DISK_PERCENT:
            seen.append(f"Their disk is {int(100 - free_percent)}% full, with "
                        f"{disk.free / (1024 ** 3):.0f} GB left.")
    except Exception as e:
        print(f"[Idle] could not read the disk: {e}")

    return seen


def gather_context(brain) -> str:
    """A few true things worth mentioning, or '' if there are none.

    Everything here comes from what Ultron already knows. Nothing is invented,
    so the opener can refer to something real rather than making small talk.
    """
    lines = list(machine_observations())

    try:
        upcoming = []
        now = datetime.datetime.now()
        for task in brain.db.get_pending_tasks():
            scheduled = task[2]
            if not scheduled:
                continue
            try:
                due = datetime.datetime.fromisoformat(scheduled)
            except (TypeError, ValueError):
                continue
            hours = (due - now).total_seconds() / 3600
            if 0 < hours <= 6:
                upcoming.append(f"{task[1]} at {due:%H:%M}")
        if upcoming:
            # Spelled out as *already set*. Described only as "coming up", the
            # model offers to set a reminder for the reminder — reasonable
            # given what it was told, and useless to the user.
            lines.append(
                "Reminders they have ALREADY set (these are scheduled; never "
                "offer to set them again): " + "; ".join(upcoming[:3]) + "."
            )
    except Exception as e:
        print(f"[Idle] could not read reminders for context: {e}")

    try:
        memories = brain.db.list_memories()
        if memories:
            picked = choose_memories(memories)
            lines.append("Things you know about them: "
                         + "; ".join(f"{m['key']}: {m['value']}" for m in picked) + ".")
    except Exception as e:
        print(f"[Idle] could not read memories for context: {e}")

    return "\n".join(lines)


def compose(brain, idle_minutes: int) -> str:
    """Writes the line to say. Falls back to a plain one if the model fails."""
    now = datetime.datetime.now()
    user_name = "the user"
    try:
        for memory in brain.db.list_memories():
            if memory["key"].lower() in ("user_name", "name"):
                user_name = memory["value"]
                break
    except Exception as e:
        print(f"[Idle] could not look up the user's name: {e}")

    prompt = PROMPT.format(
        user=user_name,
        minutes=idle_minutes,
        when=_time_of_day(now),
        weekday=now.strftime("%A"),
        clock=now.strftime("%H:%M"),
        # Deliberately blank rather than "(nothing scheduled)": told there is
        # nothing, the model announces that fact instead of just speaking.
        context=gather_context(brain),
        offers=offers(),
        max_words=MAX_WORDS,
    )

    try:
        response = brain.client.chat.completions.create(
            model=brain.selected_model,
            messages=[{"role": "user", "content": prompt}],
        )
        brain._record_usage(response)
        text = (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[Idle] could not compose a line ({e}) — using a plain one")
        return random.choice(FALLBACK_LINES)

    text = text.strip().strip('"').strip()
    # Small models narrate ("Here is a line:") or run on. Neither is speakable,
    # and this is not worth a second API call to fix.
    if not text or len(text.split()) > MAX_WORDS * 2 or "<tool_call>" in text:
        return random.choice(FALLBACK_LINES)
    return text.split("\n")[0].strip()


def blocked_reason(brain, microphone_active: bool):
    """Why it must not speak right now, or None if it may.

    Every guard other than quiet hours is opt-in, because each one also means
    staying quiet in cases where speaking would have been welcome.
    """
    if not config.get("idle_chat.enabled", True):
        return "turned off"

    if in_quiet_hours(
        start=config.get("idle_chat.quiet_start_hour", DEFAULT_QUIET_START),
        end=config.get("idle_chat.quiet_end_hour", DEFAULT_QUIET_END),
    ):
        return "quiet hours"

    if config.get("idle_chat.silent_when_mic_off", False) and not microphone_active:
        return "microphone is off"

    if config.get("idle_chat.silent_in_fullscreen", False) and is_fullscreen_app_in_front():
        return "a fullscreen app is in front"

    return None
