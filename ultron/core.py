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

import datetime
import queue
import random
import threading
import time

from ultron.config import config
from ultron.brain import Brain
from ultron.speaker import VoiceSpeaker
from ultron.listener import VoiceListener
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
    "search_past_conversations": "recalling past conversations",
    "list_reminders": "checking your reminders",
    "delete_reminder": "removing a reminder",
    "get_selected_file_in_explorer": "checking Explorer",
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

# How often the reminder worker scans for due tasks.
REMINDER_POLL_SECONDS = 15

_RECURRENCE_DELTAS = {
    "hourly": datetime.timedelta(hours=1),
    "daily": datetime.timedelta(days=1),
    "weekly": datetime.timedelta(weeks=1),
    "monthly": datetime.timedelta(days=30),
}


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

        self._assistant_listeners = []
        self._user_listeners = []
        self._busy_listeners = []
        self._status_listeners = []
        self._state_listeners = []
        self._level_listeners = []

        # Inputs to the derived state, each owned by a different component.
        self._speaking = False
        self._busy = False
        self._active_tool = None
        self._state = STATE_IDLE

        self.output_manager.on_message(self._emit_assistant)
        self.output_manager.on_speaking_changed(self._on_speaking_changed)
        self.speaker.on_level(self._on_speaker_level)
        self.brain.on_tool_event(self._on_tool_event)

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
        if not speaking:
            self._emit_level(0.0)
        self._recompute_state()

    def _on_tool_event(self, phase: str, name: str, _detail=None):
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

    def start_microphone(self) -> bool:
        """Starts continuous background listening. Returns True if active."""
        if self.listener and self.listener.is_listening:
            return True
        try:
            self.listener = VoiceListener(callback_func=lambda spoken: self.submit(spoken, origin="voice"))
            self.listener.on_level(self._on_mic_level)
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

                    send_toast("Ultron Reminder", desc)
                    # Its own source, so a UI can tell a due reminder apart
                    # from a passing loading phrase. Unlike "cron" this is
                    # never dropped when the user interrupts.
                    self.output_manager.enqueue(
                        f"Sir, here is your reminder: {desc}", source="reminder"
                    )

                    if not frequency:
                        self.brain.db.delete_task(task_id)
                        continue

                    delta = _RECURRENCE_DELTAS.get(frequency, datetime.timedelta(days=1))
                    next_iso = (datetime.datetime.now() + delta).isoformat()
                    if until_date and next_iso > until_date:
                        self.brain.db.delete_task(task_id)
                    else:
                        self.brain.db.update_task_time(task_id, next_iso)
            except Exception as e:
                self._status(f"[Reminder Error] {e}")

            # Sleep in slices so shutdown doesn't wait out a full poll interval.
            for _ in range(REMINDER_POLL_SECONDS * 2):
                if not self._running:
                    return
                time.sleep(0.5)

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
            except Exception:
                pass
