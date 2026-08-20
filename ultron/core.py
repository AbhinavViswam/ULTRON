"""Front-end-agnostic runtime for Ultron.

Owns the brain, the voice pipeline, and every background worker, and exposes
them through callbacks instead of stdout. The console entry point (main.py)
and the desktop overlay (gui.py) are both thin shells over this class.

Typical use:

    core = UltronCore()
    core.on_assistant_message(lambda text, source: ...)
    core.on_user_message(lambda text, origin: ...)
    core.on_busy_changed(lambda busy: ...)
    core.start()
    core.submit("what's the weather")
    ...
    core.shutdown()

All callbacks fire on background threads. A GUI must marshal them onto its
own event loop.
"""

import calendar
import datetime
import queue
import random
import threading
import time

from ultron.config import config, DATA_DIR
from ultron.brain import Brain
from ultron.speaker import VoiceSpeaker
from ultron.listener import VoiceListener
from ultron.self_hearing import SelfHearingGuard
from ultron.cron_manager import CronManager
from ultron.output_manager import OutputManager

FIRST_LOADING_MESSAGES = [
    "Hmm, let me see...",
    "Just a second, sir...",
    "Looking into that...",
    "Right away, sir...",
    "Let me check...",
    "Working on it, sir...",
]

QUEUED_LOADING_MESSAGES = [
    "Now for your next request...",
    "Moving on to the next one...",
    "Checking that next...",
    "Let me check that too...",
    "Working on your other request...",
]

WELCOME_MESSAGE = "Hello, Ultron welcomes you sir"

# What the assistant is doing, most urgent first. A UI can render each of these
# differently without knowing anything about the pieces that produce them.
STATE_SPEAKING = "speaking"
STATE_TOOL = "tool"
STATE_THINKING = "thinking"
STATE_LISTENING = "listening"
STATE_IDLE = "idle"

# Human phrasing for tool activity. Anything unlisted falls back to the tool
# name with underscores spaced out, which reads acceptably on its own.
TOOL_LABELS = {
    "web_search": "searching the web",
    "start_background_research": "researching in the background",
    "list_research_reports": "checking research reports",
    "read_emails": "reading your email",
    "send_email": "sending an email",
    "draft_email": "drafting an email",
    "open_application": "opening an app",
    "close_application": "closing an app",
    "system_media_control": "controlling playback",
    "search_spotify": "searching Spotify",
    "adjust_volume": "adjusting the volume",
    "get_system_health": "checking system health",
    "system_power_control": "controlling power",
    "write_in_notepad": "writing in Notepad",
    "send_whatsapp_message": "sending a WhatsApp message",
    "read_clipboard": "reading the clipboard",
    "copy_to_clipboard": "copying to the clipboard",
    "find_files": "searching your files",
    "read_file_content": "reading a file",
    "create_file": "creating a file",
    "delete_file": "deleting a file",
    "copy_file": "copying a file",
    "move_file": "moving a file",
    "list_directory": "listing a folder",
    "open_folder": "opening a folder",
    "empty_recycle_bin": "emptying the recycle bin",
    "clean_temp_files": "cleaning temp files",
    "save_memory": "saving to memory",
    "search_memories": "searching memory",
    "list_memories": "checking what it remembers",
    "delete_memory": "forgetting something",
    "search_past_conversations": "recalling past conversations",
    "list_reminders": "checking your reminders",
    "delete_reminder": "removing a reminder",
    "get_selected_file_in_explorer": "checking Explorer",
    "get_current_explorer_folder": "checking which folder you are in",
    "list_current_explorer_folder": "looking in your current folder",
    "run_workflow_tool": "running a workflow",
}

# Prefix rules for families of tools, checked when there is no exact match.
TOOL_LABEL_PREFIXES = (
    ("browser_", "browsing the web"),
    ("docker_", "working with Docker"),
    ("set_reminder", "setting a reminder"),
    ("set_recurring_reminder", "setting a recurring reminder"),
    ("agent_monitor", "checking your agents"),
    ("screen_", "looking at your screen"),
    ("workflow", "working with workflows"),
)


def tool_label(name: str) -> str:
    """Turns a tool function name into something worth showing a person."""
    if name in TOOL_LABELS:
        return TOOL_LABELS[name]
    for prefix, label in TOOL_LABEL_PREFIXES:
        if name.startswith(prefix):
            return label
    return name.replace("_", " ")

# How long to keep waiting for the local model to load, so its real context
# window can be read. Generous: it only loads when the user first speaks.
CONTEXT_CHECK_WINDOW_SECONDS = 900
CONTEXT_CHECK_POLL_SECONDS = 20

# How often the reminder worker scans for due tasks.
REMINDER_POLL_SECONDS = 15

# How often to look for routines that have come due.
ROUTINE_POLL_SECONDS = 30

# A routine later than this was missed rather than merely delayed, and is
# skipped. Routines can act, and an action taken hours after its moment is at
# best surprising. Generous enough to survive a slow poll or a busy turn.
ROUTINE_MISSED_AFTER_SECONDS = 300

# Consecutive failures before a routine switches itself off rather than
# failing silently every morning forever.
ROUTINE_MAX_FAILURES = 3

# How often to check whether the silence has gone on long enough. Coarse on
# purpose: the threshold is measured in tens of minutes, so polling faster
# would only burn cycles.
IDLE_POLL_SECONDS = 60

# How long a destructive action waits for a yes or no before giving up. Long
# enough to read the card and decide, short enough that an unnoticed question
# does not hold the worker thread for the rest of the session.
CONFIRM_TIMEOUT_SECONDS = 45

_RECURRENCE_DELTAS = {
    "hourly": datetime.timedelta(hours=1),
    "daily": datetime.timedelta(days=1),
    "weekly": datetime.timedelta(weeks=1),
}


def _add_month(moment: datetime.datetime) -> datetime.datetime:
    """Steps to the same day of the following month.

    A fixed 30-day delta is not a month: it walks a reminder set for the 1st
    backwards to the 31st, then the 30th, and so on around the calendar.
    Months of unequal length are handled by clamping — the 31st becomes the
    30th in a 30-day month, and the 28th or 29th in February.
    """
    month = moment.month % 12 + 1
    year = moment.year + (1 if moment.month == 12 else 0)
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def next_occurrence(scheduled_for: str, frequency: str) -> datetime.datetime:
    """Advances a recurring reminder from its own schedule, never from now.

    Stepping from the current time moves the reminder permanently: a 10:30
    daily reminder that only fired at 12:30 — because Ultron was closed at
    10:30 — would become a 12:30 reminder, and drift further every day.
    Stepping from the time it was *meant* to run keeps it at 10:30 and simply
    skips over the slots that were missed, so a week away still produces one
    reminder rather than seven.
    """
    now = datetime.datetime.now()
    try:
        moment = datetime.datetime.fromisoformat(scheduled_for)
    except (TypeError, ValueError):
        # An unreadable schedule is no reason to lose the reminder.
        moment = now

    if frequency == "monthly":
        advance = _add_month
    else:
        # Anything unrecognised repeats daily, as it always has — a reminder
        # with a bad frequency is better late than silently annual.
        step = _RECURRENCE_DELTAS.get(frequency, datetime.timedelta(days=1))
        advance = lambda m: m + step  # noqa: E731

    moment = advance(moment)
    while moment <= now:
        moment = advance(moment)
    return moment


class UltronCore:
    """Everything Ultron does, with no opinion about how it is displayed."""

    def __init__(self, echo_to_console: bool = True, voice_name: str = "en_US-bryce-medium"):
        self.echo_to_console = echo_to_console

        self.brain = Brain()
        self.speaker = VoiceSpeaker(voice_name=voice_name)
        self.output_manager = OutputManager(self.speaker, echo_to_console=echo_to_console)
        self.brain.output_manager = self.output_manager

        self.listener = None
        self.cron_manager = None
        self.agent_monitor = None

        self._command_queue = queue.Queue()
        self._running = False
        self._threads = []
        self._is_processing = False
        # When the user last said something, or Ultron last spoke. Both count:
        # nudging seconds after a reminder has just been read out would feel
        # like being talked at rather than talked to.
        self._last_exchange = time.monotonic()
        self._idle_nudges_unanswered = 0

        self._assistant_listeners = []
        self._user_listeners = []
        self._busy_listeners = []
        self._status_listeners = []
        self._state_listeners = []
        self._level_listeners = []
        self._confirm_listeners = []

        # Inputs to the derived state, each owned by a different component.
        self._speaking = False
        self._busy = False
        # On speakers, the microphone hears Ultron. Voice input interrupts
        # speech and is then obeyed, so without this it cuts itself off and
        # answers its own words.
        self._self_hearing = SelfHearingGuard()
        self._active_tool = None
        self._state = STATE_IDLE

        self.output_manager.on_message(self._emit_assistant)
        self.output_manager.on_message(
            lambda text, _source: self._self_hearing.note_spoken(text))
        self.output_manager.on_speaking_changed(self._on_speaking_changed)
        self.speaker.on_level(self._on_speaker_level)
        self.brain.on_tool_event(self._on_tool_event)
        # Destructive tools ask a human before running. Refused by default if
        # no front end registers itself as able to ask.
        self.brain.set_confirm_handler(self._confirm)
        # "Run it now" has to go through the same queue as everything else, so
        # one turn never starts on top of another.
        self.brain.routine_runner = (
            lambda routine: self._command_queue.put(("routine", routine, False)))

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def on_assistant_message(self, callback):
        """callback(text, source) for anything Ultron says."""
        self._assistant_listeners.append(callback)
        return callback

    def on_user_message(self, callback):
        """callback(text, origin, queued) as each input is accepted.

        origin is 'voice' or 'keyboard'. queued is True when a turn was
        already in flight, so the input is waiting its turn.
        """
        self._user_listeners.append(callback)
        return callback

    def on_busy_changed(self, callback):
        """callback(is_busy) around each brain turn, for a thinking indicator."""
        self._busy_listeners.append(callback)
        return callback

    def on_status(self, callback):
        """callback(text) for diagnostic lines that used to go to stdout."""
        self._status_listeners.append(callback)
        return callback

    def on_state_changed(self, callback):
        """callback(state, detail) whenever what Ultron is doing changes.

        state is one of the STATE_* constants. detail carries the human tool
        label while in STATE_TOOL, and is None otherwise.
        """
        self._state_listeners.append(callback)
        return callback

    def on_level(self, callback):
        """callback(level) with 0.0-1.0 loudness of whichever voice is active.

        While Ultron speaks this is its own output; otherwise it is the
        microphone. One stream either way, so a UI can drive a single meter.
        """
        self._level_listeners.append(callback)
        return callback

    # ------------------------------------------------------------------
    # Emitters
    # ------------------------------------------------------------------

    @staticmethod
    def _fire(listeners, *args):
        for callback in list(listeners):
            try:
                callback(*args)
            except Exception as e:
                print(f"[UltronCore] Listener error: {e}")

    def _emit_assistant(self, text, source):
        self._fire(self._assistant_listeners, text, source)

    def _emit_user(self, text, origin, queued=False):
        self._fire(self._user_listeners, text, origin, queued)

    def _set_busy(self, busy: bool):
        self._is_processing = busy
        self._busy = busy
        self._fire(self._busy_listeners, busy)
        self._recompute_state()

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    def _recompute_state(self):
        """Collapses the component signals into one state, most urgent wins.

        Speaking outranks everything because it is what the user perceives;
        a tool running is more specific than "thinking", so it outranks it.
        """
        if self._speaking:
            state, detail = STATE_SPEAKING, None
        elif self._active_tool:
            state, detail = STATE_TOOL, tool_label(self._active_tool)
        elif self._busy:
            state, detail = STATE_THINKING, None
        elif self.microphone_active:
            state, detail = STATE_LISTENING, None
        else:
            state, detail = STATE_IDLE, None

        if state == self._state and state != STATE_TOOL:
            return
        self._state = state
        self._fire(self._state_listeners, state, detail)

    @property
    def state(self) -> str:
        return self._state

    def _on_speaking_changed(self, speaking: bool):
        self._speaking = speaking
        self._self_hearing.note_speaking(speaking)
        if not speaking:
            self._last_exchange = time.monotonic()
        if not speaking:
            self._emit_level(0.0)
        self._recompute_state()

    def _check_context_window(self):
        """Says so when the local model cannot hold Ultron's own instructions.

        This failed silently for weeks: the window was 4,096, the system prompt
        6,736, and everything past the limit was dropped without a word. Ultron
        looked forgetful when it was in fact being truncated. Whatever else is
        true, that should never again go unmentioned.
        """
        if self.brain.active_api != "localapi":
            return

        from ultron import local_model

        url = config.get("local_api_url", "http://localhost:11434/v1")
        deadline = time.monotonic() + CONTEXT_CHECK_WINDOW_SECONDS
        while self._running and time.monotonic() < deadline:
            try:
                length = local_model.fetch_context_length(
                    url, self.brain.selected_model)
            except Exception as e:
                self._status(f"[Model] could not check the context window: {e}")
                return

            if length:
                warning = local_model.diagnose(
                    self.brain._message_size(self.brain.messages[0]),
                    length,
                    self.brain.selected_model,
                )
                if warning:
                    self._status(warning)
                return

            # Unless a Modelfile pins num_ctx, the window does not exist until
            # Ollama loads the model — which happens on the first request, not
            # at startup. Waiting for that is the only way to catch the case
            # that caused this: an unpinned model quietly given 4,096 tokens.
            for _ in range(CONTEXT_CHECK_POLL_SECONDS * 2):
                if not self._running:
                    return
                time.sleep(0.5)

    def _is_own_voice(self, heard: str) -> bool:
        """Whether a transcription is Ultron hearing itself through the speakers.

        Switchable off, because on headphones there is nothing to hear and the
        guard can only cost you: it has no upside and a small chance of
        discarding something you actually said.
        """
        if not config.get("self_hearing_guard", True):
            return False

        ignored = self._self_hearing.is_own_voice(heard)
        if ignored:
            self._status(f"[Voice] ignored its own voice: {heard!r}")
        return ignored

    def _on_tool_event(self, phase: str, name: str, _detail=None):
        from ultron.brain import SCREEN_TOOLS

        if phase == "start" and name in SCREEN_TOOLS:
            # Warned before the keys start flying, not reported afterwards.
            # Anything typed while the user is still using the keyboard ends
            # up interleaved with what they were doing.
            self.output_manager.enqueue(
                "Hands off the keyboard and mouse for a moment, sir.",
                source="system")

        self._active_tool = name if phase == "start" else None
        self._recompute_state()

    def _on_speaker_level(self, level: float):
        self._emit_level(level)

    def _on_mic_level(self, level: float, _is_speech: bool):
        # Ultron's own voice would otherwise fight the microphone for the meter.
        if not self._speaking:
            self._emit_level(level)

    def _emit_level(self, level: float):
        self._fire(self._level_listeners, level)

    def _status(self, text: str):
        if self.echo_to_console:
            print(text)
        self._fire(self._status_listeners, text)

    @property
    def is_processing(self) -> bool:
        return self._is_processing

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def submit(self, text: str, origin: str = "keyboard"):
        """Queues user input for processing.

        Input arriving while a turn is in flight is queued rather than
        interrupting it; input arriving while idle cuts off any speech still
        playing, which is what makes the assistant feel responsive.
        """
        if not text or not text.strip():
            return
        text = text.strip()
        self._last_exchange = time.monotonic()
        self._idle_nudges_unanswered = 0
        was_queued = self._is_processing
        if not was_queued:
            self.output_manager.interrupt()
        # Announce at submit time, not when the worker gets to it, so input
        # that lands mid-turn is visible while it waits rather than appearing
        # out of nowhere minutes later.
        self._emit_user(text, origin, was_queued)
        self._command_queue.put((origin, text, was_queued))

    def _worker_loop(self):
        while self._running:
            try:
                origin, text, was_queued = self._command_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if text is None:
                break
            if origin == "routine":
                self._run_routine(text)
                continue
            self._handle(text, was_queued)

    def _handle(self, text: str, was_queued: bool):
        pool = QUEUED_LOADING_MESSAGES if was_queued else FIRST_LOADING_MESSAGES
        self._set_busy(True)
        self.output_manager.enqueue(random.choice(pool), source="system")

        try:
            response = self.brain.process_input(text)
        except Exception as e:
            response = f"Sorry sir, something went wrong: {e}"
        finally:
            self._set_busy(False)

        if response:
            self.output_manager.enqueue(response, source="user")

    # ------------------------------------------------------------------
    # Microphone
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Confirmation of destructive actions
    # ------------------------------------------------------------------

    def on_confirmation_request(self, callback):
        """Registers the front end that asks the user to approve an action.

        The callback receives (question, decide) on the worker thread and must
        arrange for `decide(True/False)` to be called once. A GUI shows a card
        and answers on click; a console prompts and answers on the next line.
        """
        self._confirm_listeners.append(callback)
        return callback

    def _confirm(self, _tool_name: str, _args: dict, question: str) -> bool:
        """Blocks the worker until the user answers, or the wait runs out.

        Waiting is the point — the tool must not run until a human agrees —
        but an unanswered question must not wedge the assistant forever, so
        silence expires and counts as no.
        """
        if not self._confirm_listeners:
            return False

        decision = {"approved": False}
        answered = threading.Event()

        def decide(approved: bool):
            # Only the first answer counts; a second click must not resurrect
            # a request that already timed out and reported itself refused.
            if answered.is_set():
                return
            decision["approved"] = bool(approved)
            answered.set()

        self._fire(self._confirm_listeners, question, decide)

        if not answered.wait(CONFIRM_TIMEOUT_SECONDS):
            answered.set()
            self._status(f"[Confirm] no answer about '{question}' — treating as no")
            return False
        return decision["approved"]

    def start_microphone(self) -> bool:
        """Starts continuous background listening. Returns True if active."""
        if self.listener and self.listener.is_listening:
            return True
        try:
            self.listener = VoiceListener(callback_func=lambda spoken: self.submit(spoken, origin="voice"))
            self.listener.on_level(self._on_mic_level)
            self.listener.ignore_check = self._is_own_voice
            self.listener.start_listening()
            self._recompute_state()
            return True
        except Exception as e:
            self._status(f"[Microphone Error] {e}")
            self.listener = None
            return False

    def stop_microphone(self):
        if self.listener:
            self.listener.stop()
            self.listener = None
            self._emit_level(0.0)
            self._recompute_state()

    def set_microphone(self, active: bool) -> bool:
        """Toggles the microphone and persists the choice to settings."""
        config.set("microphone_active", bool(active))
        if active:
            return self.start_microphone()
        self.stop_microphone()
        return False

    @property
    def microphone_active(self) -> bool:
        return bool(self.listener and self.listener.is_listening)

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    def _reminder_loop(self):
        """Fires due reminders as toast plus speech, rescheduling recurring ones."""
        from ultron.plugins.notification_plugin import send_toast

        while self._running:
            try:
                now_iso = datetime.datetime.now().isoformat()
                for task in self.brain.db.get_pending_tasks():
                    # Tolerate the older 4-column task schema.
                    if len(task) == 4:
                        task_id, desc, scheduled_for, _created = task
                        frequency = until_date = None
                    else:
                        task_id, desc, scheduled_for, _created, frequency, until_date = task

                    if not scheduled_for or scheduled_for > now_iso:
                        continue

                    # Settle the schedule before announcing it. If the database
                    # write failed afterwards the reminder would still read as
                    # due, and it would be spoken again on every poll.
                    if frequency:
                        next_iso = next_occurrence(scheduled_for, frequency).isoformat()
                        if until_date and next_iso > until_date:
                            self.brain.db.delete_task(task_id)
                        else:
                            self.brain.db.update_task_time(task_id, next_iso)
                    else:
                        self.brain.db.delete_task(task_id)

                    send_toast("Ultron Reminder", desc)
                    # What a bare "snooze it" refers to. Recorded even for a
                    # one-off, which has just been deleted above and would
                    # otherwise be unreachable the instant it went off.
                    self.brain.last_fired_reminder = {
                        "description": desc, "frequency": frequency,
                    }
                    spoken = f"Sir, here is your reminder: {desc}"
                    # So that "snooze it" or "what was that?" has something to
                    # refer to — the reminder was announced by this loop, not
                    # by a turn, so nothing else puts it in the conversation.
                    self.brain.note_unprompted_line(spoken)
                    # Its own source, so a UI can tell a due reminder apart
                    # from a passing loading phrase. Unlike "cron" this is
                    # never dropped when the user interrupts.
                    self.output_manager.enqueue(spoken, source="reminder")
            except Exception as e:
                self._status(f"[Reminder Error] {e}")

            # Sleep in slices so shutdown doesn't wait out a full poll interval.
            for _ in range(REMINDER_POLL_SECONDS * 2):
                if not self._running:
                    return
                time.sleep(0.5)

    def _routine_loop(self):
        """Runs due routines, and quietly reschedules the ones that were missed.

        Missed routines are deliberately NOT run late. A routine can act — open
        apps, start containers, send things — and an action taken hours after
        its moment is at best surprising and at worst wrong. If Ultron was not
        running at 08:00, the 08:00 routine did not happen; it is due again
        tomorrow.
        """
        from ultron import routines as sched

        while self._running:
            for _ in range(ROUTINE_POLL_SECONDS * 2):
                if not self._running:
                    return
                time.sleep(0.5)

            try:
                now = datetime.datetime.now()
                for routine in self.brain.db.list_routines():
                    if not routine["enabled"] or not routine["next_run"]:
                        continue
                    try:
                        due = datetime.datetime.fromisoformat(routine["next_run"])
                    except (TypeError, ValueError):
                        self._reschedule_routine(routine, now)
                        continue
                    if due > now:
                        continue

                    late = (now - due).total_seconds()
                    self._reschedule_routine(routine, now)

                    if late > ROUTINE_MISSED_AFTER_SECONDS:
                        self._status(
                            f"[Routine] '{routine['name']}' was missed by "
                            f"{late / 60:.0f} min — skipping, not running late"
                        )
                        continue

                    self._command_queue.put(("routine", routine, False))
            except Exception as e:
                self._status(f"[Routine Error] {e}")

    def _reschedule_routine(self, routine: dict, now: datetime.datetime):
        """Moves a routine to its next slot, or retires a spent one-off."""
        from ultron import routines as sched

        following = sched.next_run(routine["schedule"], after=now)
        if following is None:
            self.brain.db.update_routine(routine["id"], enabled=0, next_run=None)
            self._status(f"[Routine] '{routine['name']}' has no future runs — disabled")
        else:
            self.brain.db.update_routine(routine["id"], next_run=following.isoformat())

    def _run_routine(self, routine: dict):
        """Carries out one routine's instruction with tools available."""
        from ultron import routines as sched

        name = routine["name"]
        self._status(f"[Routine] running '{name}'")
        self._set_busy(True)

        now = datetime.datetime.now()
        location = ""
        try:
            for memory in self.brain.db.list_memories():
                if memory["key"].lower() in ("user_location", "location"):
                    location = memory["value"]
                    break
        except Exception as e:
            self._status(f"[Routine] could not read location: {e}")

        # The date is stated rather than left to the model, which does not
        # reliably know what day it is and will invent one if asked.
        prompt = (
            f"This is your scheduled routine '{name}', running automatically.\n"
            f"Today is {now:%A, %d %B %Y}, the time is {now:%H:%M}.\n"
            + (f"The user is in {location}.\n" if location else "")
            + "Use your tools to find real information — do not answer from "
              "memory alone, and never state a fact you have not looked up.\n\n"
            f"Your instruction: {routine['instruction']}"
        )

        # Destructive tools are refused while this runs: nobody is present to
        # approve them, and the confirmation gate would only block and refuse
        # after a timeout anyway.
        self.brain.unattended = True
        try:
            result = self.brain.process_input(prompt)
        except Exception as e:
            result = ""
            self._status(f"[Routine] '{name}' failed: {e}")
        finally:
            self.brain.unattended = False
            self._set_busy(False)

        if not result:
            fails = routine["fail_count"] + 1
            updates = {"fail_count": fails, "last_run": now.isoformat()}
            if fails >= ROUTINE_MAX_FAILURES:
                # Failing silently every morning forever helps nobody.
                updates["enabled"] = 0
                self.output_manager.enqueue(
                    f"Sir, the routine '{name}' has failed {fails} times, so I "
                    "have switched it off.", source="system")
            self.brain.db.update_routine(routine["id"], **updates)
            return

        self.brain.db.update_routine(
            routine["id"], last_run=now.isoformat(), last_result=result,
            fail_count=0,
        )

        deliver = routine["deliver"]
        if "toast" in deliver:
            try:
                from ultron.plugins.notification_plugin import send_toast
                send_toast(f"Ultron — {name}", result[:200])
            except Exception as e:
                self._status(f"[Routine] toast failed: {e}")
        if "file" in deliver:
            self._append_routine_log(name, now, result)
        if "speak" in deliver or "card" in deliver:
            self.output_manager.enqueue(result, source="routine")

    def _append_routine_log(self, name: str, when: datetime.datetime, result: str):
        """Keeps a routine's history as markdown worth going back to."""
        import os
        import re

        try:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "routine"
            folder = os.path.join(DATA_DIR, "routines")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{slug}.md"), "a", encoding="utf-8") as f:
                f.write(f"\n\n## {when:%A, %d %B %Y at %H:%M}\n\n{result}\n")
        except Exception as e:
            self._status(f"[Routine] could not write the log: {e}")

    def _idle_loop(self):
        """Speaks up unprompted after a long enough silence.

        The whole value of this is that it is rare. Anything that makes it
        frequent — nudging while busy, nudging straight after a reminder,
        nudging again before the user has had a chance to answer — turns a
        nice touch into something you want to switch off.
        """
        from ultron import idle_chat

        while self._running:
            for _ in range(IDLE_POLL_SECONDS * 2):
                if not self._running:
                    return
                time.sleep(0.5)

            try:
                minutes = int(config.get("idle_chat.after_minutes",
                                         idle_chat.DEFAULT_IDLE_MINUTES))
                idle_for = time.monotonic() - self._last_exchange
                if idle_for < minutes * 60:
                    continue

                # Mid-turn is not silence. Waiting for the next poll costs
                # nothing and avoids talking over Ultron's own answer.
                if self._is_processing or self._speaking or self._active_tool:
                    continue

                limit = int(config.get("idle_chat.give_up_after", 0) or 0)
                if limit and self._idle_nudges_unanswered >= limit:
                    continue

                blocked = idle_chat.blocked_reason(self.brain, self.microphone_active)
                if blocked:
                    self._status(f"[Idle] staying quiet — {blocked}")
                    # Reset the clock, or the moment the reason clears it
                    # would fire immediately with hours of "silence" banked.
                    self._last_exchange = time.monotonic()
                    continue

                line = idle_chat.compose(self.brain, int(idle_for // 60))
                if not line:
                    continue

                self._idle_nudges_unanswered += 1
                # Into the conversation before it is said, so that "yes, do
                # it" has an "it" to refer to. Without this the offer exists
                # only as audio and Ultron has to ask what the user meant.
                self.brain.note_unprompted_line(line)
                # Its own source so a UI can style it, and so an interrupt
                # discards it — an unprompted remark must never delay a real
                # answer the user is waiting for.
                self.output_manager.enqueue(line, source="idle")
                self._last_exchange = time.monotonic()
            except Exception as e:
                self._status(f"[Idle Error] {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, greet: bool = True):
        """Starts every background service. Safe to call once."""
        if self._running:
            return
        self._running = True

        worker = threading.Thread(target=self._worker_loop, daemon=True)
        worker.start()
        self._threads.append(worker)

        reminders = threading.Thread(target=self._reminder_loop, daemon=True)
        reminders.start()
        self._threads.append(reminders)

        idle = threading.Thread(target=self._idle_loop, daemon=True)
        idle.start()
        self._threads.append(idle)

        routines = threading.Thread(target=self._routine_loop, daemon=True)
        routines.start()
        self._threads.append(routines)

        # Off the startup path: it talks to Ollama, which may be slow or down,
        # and nothing else waits on the answer.
        context_check = threading.Thread(target=self._check_context_window, daemon=True)
        context_check.start()
        self._threads.append(context_check)

        if config.get("microphone_active", True):
            self.start_microphone()

        if config.get("agent_monitor.enabled", True):
            from ultron.plugins.agent_monitor_plugin import get_monitor
            self.agent_monitor = get_monitor(output_manager=self.output_manager)
            self._status(f"[AgentMonitor] {self.agent_monitor.start()}")

        self.cron_manager = CronManager(brain=self.brain, output_manager=self.output_manager)
        self.cron_manager.start()

        if greet:
            self.output_manager.enqueue(WELCOME_MESSAGE, source="system")

    def shutdown(self):
        """Stops every background service and releases the browser session."""
        self._running = False
        self._command_queue.put(("system", None, False))

        for stop in (
            self.stop_microphone,
            lambda: self.cron_manager and self.cron_manager.stop(),
            lambda: self.brain.browser and self.brain.browser.close(),
            self.output_manager.stop,
            lambda: self.agent_monitor and self.agent_monitor.is_running and self.agent_monitor.stop(),
        ):
            try:
                stop()
            except Exception as e:
                # Shutdown continues regardless — one stubborn subsystem must
                # not keep the others running — but say which one failed.
                print(f"[Shutdown] {getattr(stop, '__name__', stop)} failed: {e}")
