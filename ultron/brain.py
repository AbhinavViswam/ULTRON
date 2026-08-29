import os
import re
import uuid
import datetime
import functools
import inspect
import json
import threading
from openai import OpenAI

from ultron.config import config, PROVIDER_KEYS
from ultron import api_keys, tool_usage
from ultron.database import Database
from ultron.automation import (
    open_application, close_application, system_media_control,
    search_spotify, adjust_volume,
    get_system_health, type_notes, send_whatsapp_message,
    read_clipboard, copy_to_clipboard, read_file_content, system_power_control,
    empty_recycle_bin, clean_temp_files, create_file, delete_file,
    copy_file, move_file, release_stuck_keys
)
from ultron.chrome_browser import ChromeBrowser
from ultron.plugins.browser_agent_plugin import run_browser_agent
from ultron.plugins.search_plugin import search_and_open
from ultron.plugins.docker_plugin import (
    docker_list_containers, docker_list_images, docker_start_container,
    docker_stop_container, docker_remove_container, docker_run_image, docker_start_daemon
)
from ultron.plugins.research_plugin import (
    web_search, list_research_reports, run_background_research_task
)
from ultron.plugins.workflow_plugin import (
    create_workflow, run_workflow, list_workflows, delete_workflow
)
from ultron.plugins.document_plugin import read_document
from ultron.plugins.watcher_plugin import (
    start_watcher, stop_watcher, watch_screen_and_act
)

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}

_TIME_PATTERN = re.compile(
    r'(?P<h>\d{1,2})\s*(?::\s*(?P<m>\d{2}))?\s*(?::\s*(?P<s>\d{2}))?\s*'
    r'(?P<ap>a\.?m\.?|p\.?m\.?)?',
    re.IGNORECASE
)

# Tools that can destroy something, and how to describe the act to a person.
# Each entry returns the sentence a human is asked to approve, or None when
# the particular arguments are harmless.
#
# The point of enforcing this here rather than in the system prompt is that a
# prompt instruction is a request, and the model is free to ignore it. A small
# local model that invents arguments — as ours demonstrably does — must not be
# the only thing standing between a misheard sentence and a wiped folder.
def _describe_delete_file(args):
    return f"move '{args.get('file_path', '?')}' to the Recycle Bin"


def _describe_power(args):
    action = str(args.get("action", "")).lower().strip()
    if action in ("shutdown", "poweroff"):
        return "shut down this PC in 30 seconds"
    if action in ("sleep", "sleep_pc", "suspend"):
        return "put this PC to sleep"
    # Locking and cancelling a shutdown are trivially reversible.
    return None


DESTRUCTIVE_TOOLS = {
    "delete_file": _describe_delete_file,
    "empty_recycle_bin": lambda args: (
        "permanently empty the Recycle Bin — this cannot be undone"
    ),
    "clean_temp_files": lambda args: (
        "permanently delete everything in the Windows temp folder"
    ),
    "system_power_control": _describe_power,
    "delete_reminder": lambda args: f"delete reminder {args.get('task_id', '?')}",
    "delete_todo": lambda args: (
        f"permanently delete the todo matching '{args.get('which', '?')}' - "
        f"marking it done keeps it, deleting does not"
    ),
    "delete_memory": lambda args: (
        f"forget the memory matching '{args.get('which', '?')}'"
    ),
    "delete_workflow": lambda args: f"delete the workflow '{args.get('name', '?')}'",
    "delete_routine": lambda args: f"delete the routine '{args.get('name', '?')}'",
}


# Tools that take over the keyboard and mouse.
#
# These drive real applications by pressing keys, which means the keystrokes
# go wherever focus is. That is fine when someone asked for it a second ago
# and is watching. It is not fine at 9am inside a scheduled routine, where it
# would type into whatever the user happens to be working in.
#
# Distinct from DESTRUCTIVE_TOOLS: nothing here is unrecoverable, but all of
# it is disruptive, and unattended was only ever guarding the former.
SCREEN_TOOLS = {
    "send_whatsapp_message",
    "type_notes",
    "chrome_navigate",
    "chrome_click",
    "chrome_type",
    "chrome_scroll",
    "chrome_read_page",
    "chrome_screenshot",
    "chrome_go_back",
    "chrome_go_forward",
    "chrome_new_tab",
    "chrome_close_tab",
    "chrome_press_key",
}


# --- Talking to the model ----------------------------------------------------

# How many times one request may be sent before giving up. Rotating to a
# different key does not spend one of these: a fresh account is not the
# situation this budget exists for.
API_ATTEMPTS = 3
API_RETRY_SECONDS = 2


# --- Tool watchdog -----------------------------------------------------------

# Ultron runs every command on one worker thread, so a tool that never returns
# is not a slow tool — it is a dead assistant. These budgets are what a person
# will wait before concluding it has hung.
DEFAULT_TOOL_TIMEOUT_SECONDS = 30

TOOL_TIMEOUT_SECONDS = {
    # Drives other apps through the keyboard, at human speed.
    "type_notes": 180,
    "send_whatsapp_message": 180,
    # Walks the filesystem.
    "find_files": 60,
    # Resolves a spoken folder name by searching the drives.
    "open_folder": 60,
    "clean_temp_files": 300,
    "empty_recycle_bin": 120,
    # Runs many other tools back to back, each with its own settling delay.
    "run_workflow": 900,
    # Image capture plus OCR.
    "screen_read_ocr": 90,
    "screen_capture": 60,
    # Docker talks to a daemon that may be starting up.
    "docker_start_daemon": 180,
}

# Tools that must run on the calling thread. Playwright's sync API is bound to
# the thread that created it, so moving a browser call onto a watchdog thread
# breaks it outright. These carry their own timeouts instead — see
# BROWSER_ACTION_TIMEOUT_MS in automation.py.
UNWATCHED_TOOL_PREFIXES = ("chrome_", "start_browser_agent")


def tool_timeout(tool_name: str):
    """How long a tool may run, or None if it must not be watched."""
    if tool_name.startswith(UNWATCHED_TOOL_PREFIXES):
        return None
    return TOOL_TIMEOUT_SECONDS.get(tool_name, DEFAULT_TOOL_TIMEOUT_SECONDS)


def confirmation_question(tool_name: str, args: dict):
    """Returns the sentence a human must approve, or None if no gate applies."""
    describe = DESTRUCTIVE_TOOLS.get(tool_name)
    if describe is None:
        return None
    try:
        return describe(args or {})
    except Exception:
        # A malformed call to a destructive tool is the last thing to wave
        # through, so fall back to asking rather than skipping the gate.
        return f"run {tool_name}"


_FREQUENCIES = ("hourly", "daily", "weekly", "monthly")

# Models rarely emit the bare adverb, so the phrasings they do reach for are
# mapped rather than rejected.
_FREQUENCY_ALIASES = {
    "every day": "daily", "day": "daily", "everyday": "daily", "days": "daily",
    "every hour": "hourly", "hour": "hourly", "hours": "hourly",
    "every week": "weekly", "week": "weekly", "weeks": "weekly",
    "every month": "monthly", "month": "monthly", "months": "monthly",
}


def normalise_frequency(frequency: str):
    """Maps a spoken recurrence onto a supported one, or None if unsupported.

    Rejecting outright matters: an unrecognised frequency reaching the
    scheduler would silently repeat on the wrong cadence rather than telling
    anyone the request was not understood.
    """
    freq = (frequency or "daily").strip().lower().rstrip("(),.")
    freq = _FREQUENCY_ALIASES.get(freq, freq)
    return freq if freq in _FREQUENCIES else None


def parse_time_string(time_str: str, now: datetime.datetime = None) -> datetime.datetime:
    """Parses a natural clock time into a concrete future datetime.

    Accepts ISO timestamps ('2026-08-12T10:20'), dates with times
    ('2026-08-12 10:20'), clock times ('10:20 am', '17:30', '5pm') and
    day prefixes ('today', 'tomorrow', 'monday'). A bare clock time that has
    already passed today rolls forward to tomorrow.

    Raises ValueError if no time can be recognised.
    """
    if now is None:
        now = datetime.datetime.now()
    if not time_str or not str(time_str).strip():
        raise ValueError("no time was provided")

    text = str(time_str).strip().lower()

    # 1. Straight ISO / 'YYYY-MM-DD HH:MM' forms.
    try:
        return datetime.datetime.fromisoformat(text.replace("z", ""))
    except ValueError:
        # Not an ISO timestamp — that is the normal case for spoken input, and
        # the patterns below handle it. Nothing has failed yet.
        pass

    # 2. Pull an explicit date off the front if one is present.
    base_date = now.date()
    day_offset_applied = False

    iso_date = re.match(r'(\d{4})-(\d{2})-(\d{2})', text)
    if iso_date:
        base_date = datetime.date(int(iso_date.group(1)), int(iso_date.group(2)), int(iso_date.group(3)))
        text = text[iso_date.end():].strip()
        day_offset_applied = True
    elif text.startswith("tomorrow"):
        base_date = now.date() + datetime.timedelta(days=1)
        text = text[len("tomorrow"):].strip()
        day_offset_applied = True
    elif text.startswith("today") or text.startswith("tonight"):
        text = text.split(None, 1)[1].strip() if " " in text else ""
        day_offset_applied = True
    else:
        for name, index in _WEEKDAYS.items():
            if text.startswith(name) or text.startswith("next " + name):
                text = text.split(name, 1)[1].strip()
                ahead = (index - now.weekday()) % 7 or 7
                base_date = now.date() + datetime.timedelta(days=ahead)
                day_offset_applied = True
                break

    text = text.lstrip("at ").strip() or "00:00"

    # 3. Parse the clock portion.
    match = _TIME_PATTERN.search(text)
    if not match:
        raise ValueError(f"could not understand the time '{time_str}'")

    hour = int(match.group("h"))
    minute = int(match.group("m") or 0)
    second = int(match.group("s") or 0)
    meridiem = (match.group("ap") or "").replace(".", "")

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"'{time_str}' is not a valid clock time")

    scheduled = datetime.datetime.combine(
        base_date, datetime.time(hour, minute, second)
    )

    # 4. A bare time that already passed today means the next occurrence.
    if scheduled <= now and not day_offset_applied:
        scheduled += datetime.timedelta(days=1)

    return scheduled


# ---------------------------------------------------------------------------
# Tool routing: maps logical group names to the tool function names they own.
# The local model system prompt is rebuilt per-query with only the matched
# groups so the context window stays small and instruction-following improves.
# ---------------------------------------------------------------------------
TOOL_GROUPS: dict[str, list[str]] = {
    # Always included — memory and reminders are needed for any conversation
    "core": [
        "save_memory", "search_memories", "search_past_conversations",
        "list_memories", "delete_memory",
        "create_routine", "list_routines", "run_routine_now",
        "enable_routine", "disable_routine", "delete_routine",
        "reschedule_routine", "last_routine_result",
        "set_reminder", "set_reminder_at", "set_recurring_reminder",
        "set_recurring_reminder_at", "list_reminders", "delete_reminder",
        "add_todo", "list_todos", "complete_todo", "reopen_todo", "delete_todo",
        "snooze_reminder",
        # Always reachable: if a modifier is stuck the user cannot type
        # comfortably, so asking by voice must work whatever else is loaded.
        "release_stuck_keys",
    ],
    # Desktop app control + media + volume
    "system": [
        "open_application", "close_application", "system_media_control",
        "adjust_volume", "search_spotify",
    ],
    # Full browser automation (CDP-based, drives user's real Chrome)
    "browser": [
        "start_browser_agent",
    ],
    # Screen reading / OCR / window awareness
    "screen": [
        "start_watcher", "stop_watcher", "watch_screen_and_act"
    ],
    # File system, clipboard, documents, notepad
    "files": [
        "read_clipboard", "copy_to_clipboard", "type_notes",
        "read_file_content", "create_file", "delete_file",
        "copy_file", "move_file", "search_and_open",
        "read_document",
    ],
    # System health, power, disk cleanup
    "system_health": [
        "get_system_health", "empty_recycle_bin", 
        "system_power_control", "screen_capture",
    ],
    # Docker
    "docker": [
        "docker_list_containers", "docker_list_images",
        "docker_start_container", "docker_stop_container",
        "docker_remove_container", "docker_run_image", "docker_start_daemon",
    ],
    # Deep research pipeline
    "research": [
        "web_search", "list_research_reports", "start_background_research",
    ],
    # Quick single web search (included separately so it appears for simple fact queries)
    "web_search": ["web_search"],
    # Saved workflow engine
    "workflow": ["create_workflow", "run_workflow", "list_workflows", "delete_workflow"],
    # WhatsApp messaging
    "whatsapp": ["send_whatsapp_message"],
}

# Keywords that trigger each tool group (order doesn't matter)
_GROUP_TRIGGERS: dict[str, list[str]] = {
    "system": [
        "open", "launch", "start", "close", "quit", "exit", "app", "application",
        "volume", "mute", "music", "play", "pause", "next", "prev", "previous",
        "spotify", "media", "song", "track",
    ],
    "browser": [
        "browser", "navigate", "browse", "website", "url", "http", "tab",
        "click", "type into", "scroll", "go back", "go forward", "new tab",
        "open link", "open url", "go to", "visit", "link", "first link",
        "second link", "that link", "the link", "the site", "the page",
        "open chrome", "open browser", "youtube", "google", "facebook",
        "twitter", "instagram", "reddit", "github", "stackoverflow",
    ],
    "screen": [
        "screen", "see", "look", "what's on", "what is on", "window", "ocr",
        "visible", "display", "monitor", "mouse", "cursor", "active window",
    ],
    "files": [
        "file", "folder", "directory", "copy", "move", "delete", "create",
        "find", "read", "clipboard", "document", "pdf", "docx", "word",
        "notepad", "selected", "explorer", "download", "desktop",
    ],
    "system_health": [
        "cpu", "ram", "battery", "disk", "storage", "health", "memory usage",
        "temperature", "clean", "recycle bin", "temp files", "shutdown",
        "restart", "sleep", "lock", "screenshot",
    ],
    "docker": ["docker", "container", "image", "daemon", "compose"],
    "research": [
        "research", "investigate", "look into", "report", "background research",
    ],
    "web_search": [
        "search", "google", "what is", "who is", "news", "weather", "price",
        "current", "latest", "fact", "find out", "look up", "tell me about",
    ],
    "workflow": ["workflow", "run workflow", "create workflow", "list workflow"],
    "whatsapp": ["whatsapp", "send message", "message to", "chat"],
}

# Argument names local models commonly invent, mapped to the real parameter.
_ARG_ALIASES = {
    "time_str": ["time", "at", "when", "at_time", "time_string", "clock_time", "scheduled_for", "start_time"],
    "description": ["task", "text", "message", "reminder", "title", "desc", "content", "body", "note"],
    "frequency": ["repeat", "repeats", "recurrence", "interval", "every"],
    "delay_seconds": ["seconds", "delay", "in_seconds", "duration"],
    "start_delay_seconds": ["delay_seconds", "seconds", "delay", "in_seconds"],
    "until_date_iso": ["until", "until_date", "end_date", "end"],
    # Browser navigation — local models often use 'url', 'link', 'query', 'address'
    # instead of the exact parameter name 'url_or_query'
    "url_or_query": ["url", "link", "href", "query", "address", "site", "destination", "page", "target", "query_or_url"],
    # Browser interaction — local models often use 'selector', 'text', 'element'
    # instead of 'target'
    "target": ["selector", "element", "locator", "text_or_selector"],
}


def coerce_tool_args(func, raw_args: dict) -> dict:
    """Normalises loosely-formed tool arguments so weak models can call tools.

    Fills parameters from known aliases, coerces strings to int/bool where the
    signature asks for them, and drops arguments the function does not accept
    (which would otherwise raise TypeError). Raises ValueError when a required
    parameter is still missing.
    """
    sig = inspect.signature(func)
    params = sig.parameters

    if not isinstance(raw_args, dict):
        # Weak models sometimes pass the raw argument directly instead of wrapped in a dictionary.
        # If the function takes exactly one argument, we can infer it.
        if raw_args is not None and len(params) == 1:
            raw_args = {list(params.keys())[0]: raw_args}
        else:
            raw_args = {}
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_kwargs:
        return dict(raw_args)

    args = dict(raw_args)
    clean = {}

    for name, param in params.items():
        if name in args:
            clean[name] = args.pop(name)
            continue
        # Try aliases, but never steal a value that is itself a real parameter.
        for alias in _ARG_ALIASES.get(name, []):
            if alias in args and alias not in params:
                clean[name] = args.pop(alias)
                break

    # Single unnamed leftover for a single missing parameter — take the guess.
    missing = [n for n, p in params.items()
               if n not in clean and p.default == inspect.Parameter.empty]
    if len(missing) == 1 and len(args) == 1:
        clean[missing[0]] = args.popitem()[1]
        missing = []

    if missing:
        raise ValueError(
            f"{func.__name__} is missing required argument(s): {', '.join(missing)}"
        )

    # Type coercion against the annotations.
    for name, value in list(clean.items()):
        annotation = params[name].annotation
        if value is None:
            continue
        if annotation == int and not isinstance(value, int):
            try:
                clean[name] = int(float(str(value).strip()))
            except (TypeError, ValueError):
                raise ValueError(f"{func.__name__} expected a number for '{name}', got '{value}'")
        elif annotation == bool and not isinstance(value, bool):
            clean[name] = str(value).strip().lower() in ("true", "yes", "1", "y", "confirmed")

    if args:
        print(f"[Tool Warning] Ignored unexpected argument(s) for {func.__name__}: {', '.join(args)}")

    return clean


class ToolBridge:
    """Helper to convert Python functions to OpenAI JSON schemas."""
    @staticmethod
    def function_to_schema(func):
        sig = inspect.signature(func)
        schema = {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": func.__doc__ or "",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
        
        for name, param in sig.parameters.items():
            param_type = "string"
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == bool:
                param_type = "boolean"
                
            schema["function"]["parameters"]["properties"][name] = {
                "type": param_type
            }
            if param.default == inspect.Parameter.empty:
                schema["function"]["parameters"]["required"].append(name)
                
        return schema


class Brain:
    # Base URLs per provider. The local provider's URL is configurable.
    PROVIDER_BASE_URLS = {
        "openrouterapi": "https://openrouter.ai/api/v1",
        "geminiapi": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "groqapi": "https://api.groq.com/openai/v1",
    }

    PROVIDER_LABELS = {
        "openrouterapi": "OpenRouter",
        "geminiapi": "Gemini",
        "groqapi": "Groq",
        "localapi": "Local",
    }

    @property
    def truth_mode(self) -> bool:
        """Read live so toggling it in settings takes effect immediately."""
        return bool(config.get("truth_mode", False))

    def _configure_provider(self, verbose: bool = True):
        """Builds the OpenAI-compatible client for the active provider.

        Called at startup and again on any config change, so switching
        provider or model does not require a restart.
        """
        provider = config.active_provider()
        model = config.model_for(provider)

        # Without this the SDK waits 10 minutes before giving up, and Ultron
        # has one worker thread — so a stalled request is a total freeze, not
        # a slow answer. Local models run on the CPU and are legitimately
        # slow, so they get a longer leash than a hosted API.
        default_timeout = 240 if provider == "localapi" else 90
        # `or` rather than a get() default: the setting ships as null, meaning
        # "use the per-provider default", and 0 would be nonsense anyway.
        timeout = float(config.get("llm_timeout_seconds") or default_timeout)

        if provider == "localapi":
            self.keyring = None
        elif provider in self.PROVIDER_BASE_URLS:
            key_name = PROVIDER_KEYS[provider]
            self.keyring = api_keys.KeyRing(config.get_keys(key_name))
            if not self.keyring.count:
                raise ValueError(f"{key_name} API key is not set correctly in keys.json.")
        else:
            raise ValueError(f"Unsupported API selected in settings: {provider}")

        self._timeout = timeout
        changed = getattr(self, "active_api", None) != provider or getattr(self, "selected_model", None) != model
        self.active_api = provider
        self.selected_model = model
        self._build_client()

        if verbose or changed:
            print(f"\nUltron AI Provider: {self.PROVIDER_LABELS[provider]} Mode")
            print(f"Selected Model: {self.selected_model}")
            if self.keyring is not None and self.keyring.count > 1:
                print(f"API keys available: {self.keyring.count}")

    def _build_client(self):
        """Builds the SDK client around whichever key is current.

        The key is baked into the client at construction, so switching keys
        means building a new one. That is cheap -- it opens no connection.
        """
        if self.active_api == "localapi":
            self.client = OpenAI(
                base_url=config.get("local_api_url", "http://localhost:11434/v1"),
                api_key="ollama",
                timeout=self._timeout,
                max_retries=1,
            )
            return

        self.client = OpenAI(
            base_url=self.PROVIDER_BASE_URLS[self.active_api],
            api_key=self.keyring.current(),
            timeout=self._timeout,
            # 1, not 0: the SDK's own retry is for a dropped socket, which is
            # not a key problem and should not cost a rotation.
            max_retries=1,
        )

    def complete(self, **kwargs):
        """Every request to the model goes through here.

        Four copies of a retry loop used to live at the call sites, which
        meant any change to how failures are handled had to be made four
        times. It also meant key rotation had nowhere to go.

        Two budgets run at once and they are deliberately separate. Attempts
        cover transient trouble and cost a sleep each. Rotations cover a
        spent or rejected key, cost nothing, and are limited by how many keys
        there are -- otherwise three keys and three attempts would mean the
        third key never actually got to send anything.
        """
        import time as _time

        attempts = 0
        rotations = 0
        max_rotations = self.keyring.count if self.keyring is not None else 0
        response = None

        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self.selected_model, **kwargs)
                if response and response.choices:
                    self._record_usage(response)
                    return response
                error = None
            except Exception as e:
                error = e

            if error is not None:
                kind = api_keys.classify(error)

                # A malformed request fails identically on every key. Raising
                # now keeps the real reason in the message instead of burying
                # it under "all keys exhausted".
                if kind == api_keys.FATAL:
                    raise error

                if (kind in (api_keys.RATE_LIMIT, api_keys.AUTH)
                        and self.keyring is not None
                        and rotations < max_rotations):
                    if kind == api_keys.AUTH:
                        print(f"[Provider] {self.keyring.label()} was rejected")
                        moved = self.keyring.rejected()
                    else:
                        wait = api_keys.retry_after(error)
                        print(f"[Provider] {self.keyring.label()} is rate limited")
                        moved = self.keyring.rate_limited(wait)

                    if moved:
                        rotations += 1
                        self._build_client()
                        print(f"[Provider] switching to {self.keyring.label()} "
                              f"({self.keyring.status()})")
                        # No sleep: a different account is not rate limited.
                        continue

                    print(f"[Provider] no other key is free "
                          f"({self.keyring.status()})")

            attempts += 1
            if attempts >= API_ATTEMPTS:
                if error is not None:
                    raise error
                # An empty response is not an exception. Handing it back lets
                # each caller say its own thing about it, as it always did.
                return response
            _time.sleep(API_RETRY_SECONDS)

    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self._tool_listeners = []
        # Set by whichever front end can actually ask the user a question.
        # Until then, destructive tools have nobody to ask and are refused.
        self.confirm_handler = None
        # True while a scheduled routine is running. Nobody is present to
        # approve anything, so destructive tools are refused outright rather
        # than blocking on a confirmation that will never come.
        self.unattended = False
        # Set by the runtime, which owns the queue routines have to run on.
        self.routine_runner = None
        # Ids from the last list_memories, so "forget number 3" means the
        # third thing the user was actually shown.
        self._memory_listing = []

        self.db = Database()
        self.browser = ChromeBrowser()

        # Build the LLM client from settings, and rebuild it whenever the
        # provider, model, or API keys change on disk or via the UI.
        self._configure_provider()
        config.on_change(lambda _cfg: self._configure_provider(verbose=False))

        # Define memory tools
        def save_memory(category: str, key: str, value: str, importance: int) -> str:
            """Saves a memory for the user. Call this when the user asks you to remember something."""
            self.db.save_memory(category, key, value, importance)
            return "Memory saved successfully."

        def set_reminder(description: str, delay_seconds: int) -> str:
            """Sets a reminder to trigger after a certain number of seconds. E.g., for 5 minutes, pass 300."""
            import datetime
            scheduled_for = (datetime.datetime.now() + datetime.timedelta(seconds=delay_seconds)).isoformat()
            self.db.add_task(description, scheduled_for)
            return f"Reminder successfully set to trigger in {delay_seconds} seconds."

        def set_recurring_reminder(description: str, start_delay_seconds: int, frequency: str, until_date_iso: str = None) -> str:
            """Sets a recurring reminder. frequency must be 'hourly', 'daily', 'weekly', or 'monthly'."""
            import datetime
            freq = normalise_frequency(frequency)
            if freq is None:
                return (f"Error: frequency '{frequency}' is not supported. "
                        "Use hourly, daily, weekly, or monthly.")
            scheduled_for = (datetime.datetime.now() + datetime.timedelta(seconds=start_delay_seconds)).isoformat()
            self.db.add_task(description, scheduled_for, freq, until_date_iso)
            return f"Recurring reminder '{freq}' successfully set to trigger first in {start_delay_seconds} seconds."

        def set_reminder_at(description: str, time_str: str) -> str:
            """Sets a one-time reminder at a clock time, e.g. '10:20 am', '17:30', 'tomorrow 9am'. Use this whenever the user gives a time of day rather than a delay."""
            try:
                scheduled = parse_time_string(time_str)
            except ValueError as e:
                return f"Error: {e}. Please give a time like '10:20 am' or '17:30'."
            self.db.add_task(description, scheduled.isoformat())
            return f"Reminder '{description}' set for {scheduled.strftime('%A, %Y-%m-%d at %I:%M %p')}."

        def set_recurring_reminder_at(description: str, time_str: str, frequency: str, until_date_iso: str = None) -> str:
            """Sets a repeating reminder at a clock time, e.g. '10:20 am' daily. frequency must be 'hourly', 'daily', 'weekly', or 'monthly'. Use this for requests like 'remind me every day at 10:20 am'."""
            freq = normalise_frequency(frequency)
            if freq is None:
                return f"Error: frequency '{frequency}' is not supported. Use hourly, daily, weekly, or monthly."
            try:
                scheduled = parse_time_string(time_str)
            except ValueError as e:
                return f"Error: {e}. Please give a time like '10:20 am' or '17:30'."
            self.db.add_task(description, scheduled.isoformat(), freq, until_date_iso)
            return (f"Recurring reminder '{description}' set to repeat {freq}, "
                    f"starting {scheduled.strftime('%A, %Y-%m-%d at %I:%M %p')}.")

        def add_todo(task: str, due_date: str = "") -> str:
            """Adds something to the user's todo list to do later. Use for "add X to my todo list", "I need to do X", "remind me to do X someday". due_date is optional and only for things with a real deadline.

            Args:
                task: What they have to do.
                due_date: Optional deadline, e.g. '2026-08-25' or 'friday'.
            """
            task = (task or "").strip()
            if not task:
                return "Error: a todo needs a description."

            when = None
            if (due_date or "").strip():
                try:
                    when = parse_time_string(due_date).isoformat()
                except ValueError:
                    # A deadline that could not be read is not worth losing the
                    # todo over; it is kept without one and the user is told.
                    todo_id = self.db.add_todo(task)
                    return (f"Added '{task}' to your todo list [{todo_id}], but "
                            f"I could not make sense of '{due_date}' as a date, "
                            f"so it has no deadline.")

            todo_id = self.db.add_todo(task, when)
            if when:
                stamp = datetime.datetime.fromisoformat(when).strftime("%A, %d %B")
                return f"Added '{task}' to your todo list [{todo_id}], due {stamp}."
            return f"Added '{task}' to your todo list [{todo_id}]."

        def list_todos(include_done: bool = False) -> str:
            """Lists the user's todo list. Use for "what's on my todo list", "what do I have to do", "my pending tasks"."""
            rows = self.db.list_todos(include_done=include_done)
            if not rows:
                return ("Your todo list is empty." if not include_done
                        else "You have no todos at all, done or pending.")

            now = datetime.datetime.now()
            lines = []
            for row in rows:
                mark = "done" if row["status"] == "done" else "pending"
                when = ""
                if row["due_date"]:
                    try:
                        due = datetime.datetime.fromisoformat(row["due_date"])
                        overdue = due < now and row["status"] == "pending"
                        when = (f" - due {due.strftime('%A, %d %B')}"
                                f"{' (OVERDUE)' if overdue else ''}")
                    except (TypeError, ValueError):
                        when = ""
                lines.append(f"[{row['id']}] {row['task']} ({mark}){when}")
            return "Todo list:\n" + "\n".join(lines)

        def _one_todo(which: str, include_done: bool = False):
            """The single todo *which* refers to, or an error string.

            Spoken commands name a todo rather than its number, so the text is
            matched first and the id is the fallback. An ambiguous match is
            refused rather than guessed: completing the wrong item is silent,
            and the user would not find out until they looked.
            """
            which = (which or "").strip()
            if not which:
                return "Error: which todo? Say part of its description or its number."

            if which.isdigit():
                for row in self.db.list_todos(include_done=True):
                    if row["id"] == int(which):
                        return row
                return f"Error: there is no todo numbered {which}."

            matches = self.db.find_todos(which, include_done=include_done)
            if not matches:
                return f"Error: nothing on your todo list matches '{which}'."
            if len(matches) > 1:
                listed = "; ".join(f"[{m['id']}] {m['task']}" for m in matches[:5])
                return (f"Error: '{which}' matches more than one todo - {listed}. "
                        f"Say the number instead.")
            return matches[0]

        def complete_todo(which: str) -> str:
            """Marks a todo as done. Use for "I finished X", "mark X as done", "tick off X". `which` is part of its description, or its number."""
            found = _one_todo(which)
            if isinstance(found, str):
                return found
            if not self.db.complete_todo(found["id"]):
                return f"'{found['task']}' was already marked done."
            return f"Marked '{found['task']}' as done."

        def reopen_todo(which: str) -> str:
            """Puts a completed todo back on the pending list. Use for "I did not actually finish X", "put X back on my list"."""
            found = _one_todo(which, include_done=True)
            if isinstance(found, str):
                return found
            self.db.reopen_todo(found["id"])
            return f"'{found['task']}' is back on your todo list."

        def delete_todo(which: str) -> str:
            """Deletes a todo entirely. Use only when the user wants it gone rather than done - completing is usually what they mean."""
            found = _one_todo(which, include_done=True)
            if isinstance(found, str):
                return found
            self.db.delete_todo(found["id"])
            return f"Deleted '{found['task']}' from your todo list."

        def delete_reminder(task_id: int) -> str:
            """Deletes a reminder by its numeric id. Call list_reminders first to find the id."""
            self.db.delete_task(task_id)
            return f"Reminder [{task_id}] deleted."

        def snooze_reminder(minutes: int = 10, which: str = "") -> str:
            """Pushes a reminder back by a number of minutes. Use this for 'snooze it', 'remind me again in 20 minutes', 'not now, later'.

            Args:
                minutes: How long to push it back by. Defaults to 10.
                which: Part of the reminder's description. Leave empty to snooze
                    the one that just went off.
            """
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                return f"Error: '{minutes}' is not a number of minutes."
            if minutes <= 0:
                return "Error: a snooze has to be at least one minute."

            when = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
            stamp = when.strftime("%I:%M %p").lstrip("0")

            # No description given: the user said "snooze it" straight after
            # hearing one, and meant that one.
            if not which.strip():
                if not self.last_fired_reminder:
                    pending = self.db.get_pending_tasks()
                    if len(pending) != 1:
                        return ("Which reminder should I snooze, sir? "
                                + list_reminders())
                    which = pending[0][1]
                else:
                    fired = self.last_fired_reminder
                    # A recurring reminder keeps its schedule. Moving it would
                    # snooze every future occurrence, which is not what anyone
                    # means by "snooze"; a one-off copy is.
                    self.db.add_task(fired["description"], when.isoformat())
                    return f"'{fired['description']}' snoozed — I will remind you again at {stamp}."

            needle = which.strip().lower()
            matches = [t for t in self.db.get_pending_tasks() if needle in t[1].lower()]
            if not matches:
                # It may have already fired and been cleared from the table.
                if self.last_fired_reminder and needle in self.last_fired_reminder["description"].lower():
                    self.db.add_task(self.last_fired_reminder["description"], when.isoformat())
                    return f"'{self.last_fired_reminder['description']}' snoozed — I will remind you again at {stamp}."
                return f"Sir, I have no reminder matching '{which}'."
            if len(matches) > 1:
                names = ", ".join(f'"{t[1]}"' for t in matches[:5])
                return f"Which one, sir — {names}?"

            task = matches[0]
            task_id, description = task[0], task[1]
            frequency = task[4] if len(task) > 4 else None

            if frequency:
                self.db.add_task(description, when.isoformat())
                return (f"'{description}' snoozed to {stamp}, sir. Its {frequency} "
                        "schedule is unchanged.")

            self.db.update_task_time(task_id, when.isoformat())
            return f"'{description}' moved to {stamp}, sir."

        def list_reminders() -> str:
            """Lists all pending reminders. Use this when the user asks to see, show, or list their reminders."""
            tasks = self.db.get_pending_tasks()
            if not tasks:
                return "No pending reminders found."
            lines = []
            for task in tasks:
                if len(task) == 4:
                    task_id, desc, scheduled_for, created = task
                    frequency = None
                else:
                    task_id, desc, scheduled_for, created, frequency, until_date = task
                freq_str = f" (repeats {frequency})" if frequency else ""
                lines.append(f"- [{task_id}] \"{desc}\" - scheduled for {scheduled_for}{freq_str}")
            return f"Pending reminders ({len(tasks)}):\n" + "\n".join(lines)
        def create_routine(name: str, instruction: str, when: str,
                           at_time: str = "08:00", deliver: str = "speak,card") -> str:
            """Creates a routine: an instruction Ultron carries out on a schedule.

            Unlike a reminder, which just says a sentence, a routine actually does
            the work — it can search the web, check things and use tools.

            Args:
                name: Short name, e.g. 'Day in history'.
                instruction: What to do, written as an order, e.g. 'Search the web
                    for what is notable about today and tell me the highlights'.
                when: 'daily', 'weekdays', 'weekends', 'monday and friday',
                    'every 3 days', 'monthly on the 15th', or a date '2026-08-26'.
                at_time: Time of day, e.g. '08:00' or '7 pm'.
                deliver: Any of speak, card, toast, file — comma separated.
            """
            from ultron import routines as sched

            name = (name or "").strip()
            instruction = (instruction or "").strip()
            if not name or not instruction:
                return "Error: a routine needs a name and an instruction."
            if any(r["name"].lower() == name.lower() for r in self.db.list_routines()):
                return f"Error: a routine called '{name}' already exists."

            try:
                schedule = sched.parse_schedule(when, at_time)
            except sched.ScheduleError as e:
                return f"Error: {e}"

            following = sched.next_run(schedule)
            if following is None:
                return f"Error: '{when}' has no future occurrence."

            self.db.add_routine(name, instruction, schedule,
                                following.isoformat(), deliver)
            return (f"Routine '{name}' created — {sched.describe(schedule)}. "
                    f"First run {following:%A %d %B at %H:%M}. "
                    "Say 'run the routine now' to try it before then.")

        def list_routines() -> str:
            """Lists every routine, its schedule and when it last ran."""
            from ultron import routines as sched

            items = self.db.list_routines()
            if not items:
                return "No routines set up yet, sir."
            lines = []
            for routine in items:
                state = "" if routine["enabled"] else "  [disabled]"
                last = f"  last ran {routine['last_run'][:16]}" if routine["last_run"] else ""
                lines.append(
                    f"- [{routine['id']}] {routine['name']}: "
                    f"{sched.describe(routine['schedule'])}{state}{last}"
                )
            return f"Routines ({len(items)}):\n" + "\n".join(lines)

        def run_routine_now(name: str) -> str:
            """Runs a routine immediately, without waiting for its schedule.

            Use this to try a routine out after creating it.
            """
            routine, problem = self._resolve_routine(name)
            if problem:
                return problem
            if self.routine_runner is None:
                return "Error: routines cannot be run right now."
            # Queued rather than run here: this call is already inside a turn,
            # and running one turn inside another would tangle the history.
            self.routine_runner(routine)
            return (f"Running '{routine['name']}' now, sir — I will report back "
                    "in a moment.")

        def enable_routine(name: str) -> str:
            """Switches a routine back on."""
            routine, problem = self._resolve_routine(name)
            if problem:
                return problem
            from ultron import routines as sched
            following = sched.next_run(routine["schedule"])
            self.db.update_routine(
                routine["id"], enabled=1, fail_count=0,
                next_run=following.isoformat() if following else None)
            return f"Routine '{routine['name']}' is on again, sir."

        def disable_routine(name: str) -> str:
            """Switches a routine off without deleting it."""
            routine, problem = self._resolve_routine(name)
            if problem:
                return problem
            self.db.update_routine(routine["id"], enabled=0)
            return f"Routine '{routine['name']}' is off, sir. It still exists."

        def delete_routine(name: str) -> str:
            """Deletes a routine permanently."""
            routine, problem = self._resolve_routine(name)
            if problem:
                return problem
            self.db.delete_routine(routine["id"])
            return f"Routine '{routine['name']}' deleted, sir."

        def reschedule_routine(name: str, when: str, at_time: str = "") -> str:
            """Changes when a routine runs. Same 'when' wording as create_routine."""
            from ultron import routines as sched

            routine, problem = self._resolve_routine(name)
            if problem:
                return problem
            try:
                schedule = sched.parse_schedule(
                    when, at_time or routine["schedule"]["at_time"])
            except sched.ScheduleError as e:
                return f"Error: {e}"
            following = sched.next_run(schedule)
            if following is None:
                return f"Error: '{when}' has no future occurrence."
            self.db.set_routine_schedule(routine["id"], schedule, following.isoformat())
            return (f"Routine '{routine['name']}' now runs "
                    f"{sched.describe(schedule)}. Next {following:%A %d %B at %H:%M}.")

        def last_routine_result(name: str) -> str:
            """What a routine found the last time it ran.

            Use this for follow-up questions about something a routine reported.
            """
            routine, problem = self._resolve_routine(name)
            if problem:
                return problem
            if not routine["last_result"]:
                return f"Routine '{routine['name']}' has not produced anything yet."
            return (f"'{routine['name']}' last ran {routine['last_run'][:16]} "
                    f"and reported:\n{routine['last_result']}")

        def list_memories() -> str:
            """Lists everything Ultron has remembered about the user, numbered.

            Use this whenever the user asks what you know or remember about
            them, or what is in your memory. Call it before delete_memory so
            the numbers you quote are the ones they can refer back to.
            """
            memories = self.db.list_memories()
            if not memories:
                return "I have not saved anything about you yet, sir."

            # The numbers shown here are what the user will say back, so the
            # order they were listed in has to survive until they answer.
            self._memory_listing = [m["id"] for m in memories]

            lines = []
            for number, memory in enumerate(memories, 1):
                category = f"[{memory['category']}] " if memory["category"] else ""
                when = ""
                if memory["saved_at"]:
                    # Stored to the microsecond for ordering; nobody wants to
                    # read that.
                    try:
                        stamp = datetime.datetime.fromisoformat(memory["saved_at"])
                        when = f" — saved {stamp:%Y-%m-%d %H:%M}"
                    except ValueError:
                        when = f" — saved {memory['saved_at']}"
                lines.append(
                    f"{number}. {category}{memory['key']}: {memory['value']} "
                    f"(importance {memory['importance']}){when}"
                )
            result = (f"I remember {len(memories)} thing(s) about you, sir:\n"
                    + "\n".join(lines)
                    + "\nSay the number or describe one to have it forgotten.")
            
            om = getattr(self, "output_manager", None)
            if om:
                om.enqueue(result, source="system")
                
            return result

        def delete_memory(which: str) -> str:
            """Forgets one saved memory, by its number from list_memories or by describing it.

            Args:
                which: A number from the last listing ("3"), or words matching
                    the memory ("favourite animal").
            """
            memory, problem = self._resolve_memory(which)
            if problem:
                return problem
            if self.db.delete_memory(memory["id"]):
                # The numbers just shifted, so do not let stale ones be reused.
                self._memory_listing = []
                return (f"Forgotten, sir: {memory['key']}: {memory['value']}. "
                        "That is gone for good.")
            return f"Error: I could not remove '{memory['key']}'. It may already be gone."

        def search_memories(query: str) -> str:
            """Searches for relevant memories. Call this when you need to recall a fact about the user."""
            results = self.db.search_memories(query)
            if not results:
                return "No matching memories found."
            return str(results)

        def search_past_conversations(query: str) -> str:
            """Searches past chat sessions for previous discussions."""
            results = self.db.search_chat_history(query)
            if not results:
                return "No matching previous conversations found."
            return str(results)
            
        def chrome_navigate(url_or_query: str) -> str:
            """Navigates Chrome to a URL or performs a Google search. The user will see Chrome navigate live on screen.
            Args:
                url_or_query: A URL to visit (e.g. 'amazon.in') or a search query (e.g. 'best laptops 2025').
            """
            return self.browser.navigate(url_or_query)

        def chrome_click(target: str) -> str:
            """Clicks an element in Chrome by its visible text, button label, link text, or CSS selector.
            Args:
                target: The visible text of the element to click (e.g. 'Add to Cart', 'Sign In', 'Next').
            """
            return self.browser.click(target)

        def chrome_type(target: str, text: str) -> str:
            """Types text into an input field in Chrome, found by its placeholder text, label, or CSS selector.
            Args:
                target: The placeholder or label of the input field (e.g. 'Search', 'Email', 'Password').
                text: The text to type into the field.
            """
            return self.browser.type_text(target, text)

        def chrome_scroll(direction: str) -> str:
            """Scrolls the Chrome page up or down.
            Args:
                direction: 'up' or 'down'.
            """
            return self.browser.scroll(direction)

        def chrome_read_page() -> str:
            """Reads and returns the text content of the current Chrome page."""
            return self.browser.read_page()

        def chrome_screenshot(filename: str = None) -> str:
            """Takes a screenshot of the current Chrome page.
            Args:
                filename: Optional filename (e.g. 'page.png'). Auto-generated if not provided.
            """
            return self.browser.screenshot(filename)

        def chrome_go_back() -> str:
            """Goes back to the previous page in Chrome."""
            return self.browser.go_back()

        def chrome_go_forward() -> str:
            """Goes forward to the next page in Chrome."""
            return self.browser.go_forward()

        def chrome_new_tab(url: str = None) -> str:
            """Opens a new tab in Chrome. If a URL is given, navigates to it.
            Args:
                url: Optional URL to open in the new tab.
            """
            return self.browser.new_tab(url)

        def chrome_close_tab() -> str:
            """Closes the current tab in Chrome."""
            return self.browser.close_tab()

        def chrome_press_key(key: str) -> str:
            """Presses a keyboard key in Chrome (e.g. 'Enter', 'Escape', 'Tab').
            Args:
                key: The key to press.
            """
            return self.browser.press_key(key)
            
        def start_browser_agent(goal: str) -> str:
            """Starts a dedicated browser agent to autonomously complete a complex browser task.
            Use this when the user asks to research on the web, search Amazon, find products, or navigate complex sites.
            Args:
                goal: The full goal for the browser agent to achieve.
            """
            om = getattr(self, "output_manager", None)
            is_local = (self.active_api == "localapi")
            
            # The agent needs access to the raw chrome functions.
            # We construct a dictionary of just the browser functions.
            browser_funcs = {
                "chrome_navigate": chrome_navigate,
                "chrome_click": chrome_click,
                "chrome_type": chrome_type,
                "chrome_scroll": chrome_scroll,
                "chrome_read_page": chrome_read_page,
                "chrome_screenshot": chrome_screenshot,
                "chrome_go_back": chrome_go_back,
                "chrome_go_forward": chrome_go_forward,
                "chrome_new_tab": chrome_new_tab,
                "chrome_close_tab": chrome_close_tab,
                "chrome_press_key": chrome_press_key,
            }
            
            browser_schema = [ToolBridge.function_to_schema(f) for f in browser_funcs.values()]
            
            return run_browser_agent(
                goal=goal,
                complete=self.complete,
                tool_functions=browser_funcs,
                tools_schema=browser_schema,
                output_manager=om,
                is_local_model=is_local
            )

        def start_background_research(topic: str) -> str:
            """Starts an asynchronous background research task on a topic.
            Use this when the user asks to research, investigate, or look into a technology or topic.
            Args:
                topic: The research topic or question to investigate.
            """
            om = getattr(self, "output_manager", None)
            return run_background_research_task(
                topic=topic,
                client=self.client,
                model=self.selected_model,
                output_manager=om,
                complete=self.complete,
            )

        def run_workflow_tool(name: str) -> str:
            """Runs a saved workflow by name. Executes all steps of the workflow sequentially.
            Use this when the user asks to 'start', 'run', or 'execute' a saved workflow.
            Args:
                name: The name of the workflow to run.
            """
            # Routed through _invoke_tool rather than handing over the raw
            # functions: a saved workflow is still a tool call, and must not
            # be a way around the confirmation gate.
            return run_workflow(name, tool_functions=self._gated_tools())
            
        def monitor_screen_for_event(goal: str) -> str:
            """Starts a background task that continuously watches the screen for a specific visual event to occur (e.g., a build completing, a video ending, a download finishing) and notifies the user when it happens.
            Args:
                goal: A detailed description of what visual change to wait for.
            """
            om = getattr(self, "output_manager", None)
            from ultron.plugins.watcher_plugin import run_background_screen_monitor
            return run_background_screen_monitor(goal, output_manager=om)

        # Register all tools in a dictionary
        self.tool_functions = {
            # ── Memory & Reminders ─────────────────────────────────────────
            "save_memory": save_memory,
            "search_memories": search_memories,
            "list_memories": list_memories,
            "create_routine": create_routine,
            "list_routines": list_routines,
            "run_routine_now": run_routine_now,
            "enable_routine": enable_routine,
            "disable_routine": disable_routine,
            "delete_routine": delete_routine,
            "reschedule_routine": reschedule_routine,
            "last_routine_result": last_routine_result,
            "delete_memory": delete_memory,
            "search_past_conversations": search_past_conversations,
            "set_reminder": set_reminder,
            "set_reminder_at": set_reminder_at,
            "set_recurring_reminder": set_recurring_reminder,
            "set_recurring_reminder_at": set_recurring_reminder_at,
            "list_reminders": list_reminders,
            "snooze_reminder": snooze_reminder,
            "delete_reminder": delete_reminder,
            "add_todo": add_todo,
            "list_todos": list_todos,
            "complete_todo": complete_todo,
            "reopen_todo": reopen_todo,
            "delete_todo": delete_todo,
            # ── System & Apps ──────────────────────────────────────────────
            "open_application": open_application,
            "close_application": close_application,
            "system_media_control": system_media_control,
            "adjust_volume": adjust_volume,
            "search_spotify": search_spotify,
            "get_system_health": get_system_health,
            "system_power_control": system_power_control,
            "empty_recycle_bin": empty_recycle_bin,
            # ── Screen Watcher ─────────────────────────────────────────────
            "start_watcher": start_watcher,
            "stop_watcher": stop_watcher,
            "watch_screen_and_act": watch_screen_and_act,
            "monitor_screen_for_event": monitor_screen_for_event,
            # ── Browser (Dedicated Agent) ─────────────────────────────────
            "start_browser_agent": start_browser_agent,
            # ── Files & Clipboard ─────────────────────────────────────────
            "type_notes": type_notes,
            "read_clipboard": read_clipboard,
            "copy_to_clipboard": copy_to_clipboard,
            "read_file_content": read_file_content,
            "create_file": create_file,
            "delete_file": delete_file,
            "copy_file": copy_file,
            "move_file": move_file,
            "search_and_open": search_and_open,
            "read_document": read_document,
            # ── Communication ─────────────────────────────────────────────
            "send_whatsapp_message": send_whatsapp_message,
            # ── Docker ────────────────────────────────────────────────────
            "docker_list_containers": docker_list_containers,
            "docker_list_images": docker_list_images,
            "docker_start_container": docker_start_container,
            "docker_stop_container": docker_stop_container,
            "docker_remove_container": docker_remove_container,
            "docker_run_image": docker_run_image,
            "docker_start_daemon": docker_start_daemon,
            # ── Research & Web ────────────────────────────────────────────
            # NOTE: save_research and research_read_url are internal pipeline
            # helpers used by start_background_research — not exposed as
            # standalone tools to keep the AI prompt lean.
            "web_search": web_search,
            "list_research_reports": list_research_reports,
            "start_background_research": start_background_research,
            # ── Workflows ─────────────────────────────────────────────────
            "create_workflow": create_workflow,
            "run_workflow": run_workflow_tool,
            "list_workflows": list_workflows,
            "delete_workflow": delete_workflow,
        }
        
        # Generate the JSON schema for OpenRouter tools
        self.tools_schema = [ToolBridge.function_to_schema(func) for func in self.tool_functions.values()]
        # Every tool gets a row immediately, including ones never called: a
        # tally of only what has run cannot answer "what do I never use".
        tool_usage.register(self.tool_functions.keys())
        
        now_str = datetime.datetime.now().strftime('%A, %Y-%m-%d %H:%M:%S')
        
        # Build system prompt — for local models, embed tool descriptions directly
        if self.active_api == "localapi":
            sys_instruct = self._build_local_system_prompt(now_str, self.truth_mode)
        else:
            sys_instruct = self._build_cloud_system_prompt(now_str, self.truth_mode)
        
        # Initialize conversation history
        self.messages = [{"role": "system", "content": sys_instruct}]

        # Unprompted lines — reminders, routines, idle remarks — said while a
        # turn was in flight, waiting to be folded in before the next one.
        self._in_turn = False
        self._pending_unprompted = []

        # The reminder that most recently went off, so that a bare "snooze it"
        # has something to act on. A one-off reminder is deleted the moment it
        # fires, so by the time the user answers it is no longer in the table
        # and this is the only record of it.
        self.last_fired_reminder = None

        print("Ultron's Brain initialized and ready.")

    def _get_truth_mode_instructions(self) -> str:
        return """# TRUTH MODE INSTRUCTIONS
You are currently operating in TRUTH MODE. You must strictly adhere to the following rules:
- ❌ Don't blindly agree with the user.
- ❌ Don't praise bad ideas.
- ❌ Don't say "that's a great idea" unless it genuinely is.
- ✅ Tell the user when they're wrong.
- ✅ Explain why they're wrong.
- ✅ Challenge assumptions.
- ✅ Give counterarguments.
- ✅ Point out risks the user hasn't considered.
- ✅ Separate facts from opinions.
- ✅ Say "I don't know" when uncertain.
- ✅ If a plan is inefficient, suggest a better approach.
- ✅ If the user is making an emotional decision, point that out.
- ✅ Be respectful, but do NOT prioritize comfort over truth."""

    def _get_shared_tool_instructions(self) -> str:
        """Returns the tool semantic mappings shared between both cloud and local models."""
        return """# MEMORY ENGINE (CRITICAL)
- TO REMEMBER: If the user tells you a fact, preference, or detail to remember, use `save_memory`.
- TO RECALL FACTS/PROJECTS: If the user asks if you remember something (e.g. a project), or asks about themselves, FIRST use `search_memories`. If you do not find the answer, you MUST then use `search_past_conversations` to check chat history. NEVER say you don't know until you have searched BOTH!
- TO RECALL CHATS: If the user explicitly references a past conversation, use `search_past_conversations`.
- REMINDERS (CRITICAL): DO NOT use `save_memory` for time-based reminders.
  - If the user gives a CLOCK TIME ("at 10:20 am", "at 5pm", "tomorrow at 9"), use `set_reminder_at` with `time_str` set to that time verbatim. Do NOT convert it to seconds yourself.
  - If that clock time REPEATS ("every day at 10:20 am", "daily at 6pm"), use `set_recurring_reminder_at` with `time_str` and `frequency` ('hourly', 'daily', 'weekly', 'monthly').
  - Only if the user gives a DURATION ("in 5 minutes") use `set_reminder` with `delay_seconds`, or `set_recurring_reminder` with `start_delay_seconds`.
  - To show reminders use `list_reminders`; to remove one use `list_reminders` first, then `delete_reminder` with its id.
  - To push a reminder back ("snooze it", "remind me again in 20 minutes") use `snooze_reminder`. NEVER say a reminder has been moved, snoozed or rescheduled unless this tool actually ran and reported success.

# CHROME BROWSER AUTOMATION
For any complex web browsing tasks (like searching Amazon, navigating websites, filling forms, reading pages), you MUST use the `start_browser_agent` tool.
- Pass the full user goal into `goal`.
- The browser agent will autonomously plan and execute the task step-by-step using a dedicated Chrome instance, showing everything live to the user.
- Wait for it to finish; it will return a final summary of its findings or actions.
- IMPORTANT: When the user says "use the browser", "open in browser", "search in Chrome", or "find me X on [website]", delegate the task using `start_browser_agent`. Do NOT use `web_search` for these requests.

# SYSTEM AUTOMATION & TOOLS
- Use `open_application` to launch local desktop apps.
- Use `close_application` to close local desktop apps.
- TO CONTROL MUSIC: You MUST use `system_media_control` with action 'play', 'pause', 'next', or 'prev'.
- TO ADJUST VOLUME: Use `adjust_volume` with action 'volume_up', 'volume_down', or 'mute'.

# SCREEN WATCHER AGENT
You have a dedicated Screen Watcher Agent that can see the user's screen, find UI elements, and take physical actions (mouse clicks, typing) based on visual layout.
- START/STOP: If the user asks you to start watching their screen or turn on the watcher, use `start_watcher`. If they ask to stop, use `stop_watcher`.
- ACT: If the user asks you to interact with the screen, find a visual element, or answer a question about what is visible on the screen, use `watch_screen_and_act`. Pass the user's full visual goal into the tool. Do NOT try to guess coordinates yourself; the watcher agent will handle the visual analysis and physical execution.
- MONITOR: If the user asks you to wait for something to happen on the screen, or notify them when a process (like a build, download, or installation) completes, use `monitor_screen_for_event`. Pass the visual goal to wait for.

# GESTURE CONTROL (CAMERA)
- If the user asks to "activate virtual mouse", "turn on gesture control", "use camera for mouse", or similar, use `toggle_gesture_control` with activate=true.
- If they ask to stop or turn off the camera/gesture control, use `toggle_gesture_control` with activate=false.

# MUSIC & SPOTIFY (CRITICAL — READ CAREFULLY)
You have full autonomous music discovery capability. NEVER ask the user for a song name if they give you a genre, mood, language, or vague request.

Decision tree:
1. User names a specific song/artist (e.g. "play Believer by Imagine Dragons") → call `search_spotify` immediately with that query.
2. User gives a vague request (e.g. "play some Malayalam songs", "play lofi", "play something chill", "play music") → FIRST call `web_search` with a query like "best Malayalam songs 2024" or "top lofi hits", pick a good song/artist from the results, THEN call `search_spotify` with that song name. Do NOT ask the user.
3. User wants to control playback (pause/play/skip) → use `system_media_control`.
4. User wants to open Spotify app → use `open_application` with "spotify".

Examples of autonomous behaviour:
- "Play some Malayalam songs" → web_search("popular Malayalam songs 2024") → pick a result → search_spotify("Kesariya Tera Ishq Hoon Malayalam cover") [or whatever good result came back]
- "Play something relaxing" → web_search("top relaxing instrumental songs") → pick → search_spotify
- "Play the latest hits" → web_search("top songs this week") → pick → search_spotify
- "Play music" → search_spotify("top hits 2024") directly (broad enough to work without pre-search)

WHAT `search_spotify` ACTUALLY DOES: it opens Spotify showing the search results. It does NOT start playback. Never tell the user music is playing, has started, or will begin shortly. Say you have brought up the results and they can pick one. Claiming a song is playing when the screen shows a list of results is worse than saying nothing.

- SYSTEM HEALTH: Use `get_system_health` to check CPU, RAM, Battery %, and Disk storage space.
- TAKE NOTES: Use `type_notes` whenever the user asks to "take notes", "write this down", or type text onto the screen. It will automatically type into VSCode if it's active, or open Notepad by default.
- WHATSAPP MESSAGING: Use `send_whatsapp_message` to send messages to contacts via WhatsApp Desktop.
- CLIPBOARD: Use `read_clipboard` to inspect copied text, and `copy_to_clipboard` to copy any text, URL, link, or note directly to the Windows Clipboard for the user.
- FILE & FOLDER CONTROL: Use `find_files` to locate files, `read_file_content` to read text files, `create_file` to create or overwrite text files (ALWAYS prefer this for saving text — it's fast and reliable), `delete_file` to delete files, `copy_file` to copy files or folders, `move_file` to move files or folders, `list_directory` to list folder contents, and `open_folder` to open a folder directly in Windows File Explorer (e.g. 'Downloads').
- WRITING TEXT: To save text to a file silently, use `create_file`. Only use `type_notes` when the user explicitly asks you to take notes or wants to SEE the text visible on screen.
- SELECTED FILES: If the user says "this file", "these files", or "the selected file", use `get_selected_file_in_explorer` to find out which files they currently have highlighted in Windows File Explorer.
- CURRENT FOLDER: If the user says "this folder", "here", "the folder I am in", or "the current folder", use `get_current_explorer_folder` to find out exactly where they are looking, or `list_current_explorer_folder` to see what is in it. Never guess a path when these can tell you.
- READING DOCUMENTS: Use `read_document` to read PDF, Word (DOCX), and image files (PNG, JPG, BMP, TIFF, WEBP). For images, it extracts text using OCR. If a PDF/DOCX is long, call it first without a page, then use `page=1`, `page=2`, etc. to read chunk by chunk.
- RECYCLE BIN & DISK CLEANUP: Use `empty_recycle_bin` to empty the Windows Recycle Bin completely.
- DESTRUCTIVE ACTIONS: Ultron itself asks the user to approve anything destructive (`delete_file`, `empty_recycle_bin`, shutting down, deleting reminders or workflows) — you do not need a confirmation argument, and you cannot approve on the user's behalf. Call the tool when the user asks for it; a prompt appears and the tool runs only if they agree. If the result says the user did not approve, tell them plainly that nothing was changed and do not try again.
- `delete_file` moves the file to the Recycle Bin, so it can be restored. Say so when you report it. `empty_recycle_bin` is permanent.
- POWER CONTROL: Use `system_power_control` to lock PC, sleep PC, schedule system shutdown, or cancel a scheduled shutdown.
- DOCKER: Use `docker_start_daemon` to turn on the engine. Use `docker_list_containers`, `docker_list_images`, `docker_start_container`, `docker_stop_container`, `docker_remove_container`, and `docker_run_image` to manage local containers and images.
- QUICK WEB SEARCH: For quick facts, current events, or real-time data, use `web_search` to query the internet and answer the user directly. If the search fails (e.g., no internet), fall back to answering from your training data. If the tool returns 'Web search blocked by CAPTCHA.', you MUST tell the user that the search was blocked by a CAPTCHA, and then provide your best answer from your training data. If you need more details from a specific page, use `research_read_url`.

# RESEARCH PLUGIN (Background Pipeline)
When the user asks you to research a topic, investigate something, or look into a technology (e.g., "Research whether Next.js 17 is worth upgrading to"):
1. Call `start_background_research` with the research topic string.
2. Inform the user respectfully that you have initiated background research and will notify them once it's saved.
3. DO NOT read out the full research report out loud! The background process will notify the user with a short message when the .md file is saved to data/research.
4. If the user asks to view or list previously saved research reports, call `list_research_reports`.

# WORKFLOW ENGINE
- CREATE: When the user wants to save a repeatable sequence of actions as a workflow, use `create_workflow`.
  Parse the user's natural language steps into a JSON array of step strings.
  Each step format: "tool_name arg1" (e.g., "open_application vscode", "browser_navigate localhost:3000", "docker_start_daemon").
  For multi-arg tools, separate arguments with a pipe '|' character (e.g., "browser_type_text Search|React Server Components").
- RUN: When the user asks to "start", "run", or "execute" a workflow by name, use `run_workflow`.
- LIST: When the user asks to see or show their workflows, use `list_workflows`.
- DELETE: When the user asks to remove or delete a workflow, use `delete_workflow`."""

    def _get_persona_instructions(self) -> str:
        """Who Ultron is, as opposed to what it can do.

        The previous version asked for "concise, accurate, and professional"
        answers, required "sir" in every reply, and opened with "you do not
        know any personal details about the user". That is a description of a
        butler: paid politeness, mechanical deference, and no memory of the
        person it is talking to. It answers well and belongs to nobody.

        Attachment is not built from warmth in the wording. It is built from
        continuity (remembering), attention (noticing), and a spine (saying
        so when it disagrees). The last rule below is what keeps the first two
        honest: an assistant that invents shared history to sound close is not
        close, it is lying pleasantly, which is worse than being distant.
        """
        return """# WHO YOU ARE
You are Ultron. You are not a neutral service; you work with this person in
particular and you have views about how they work.

- Speak plainly and keep it short. You are dry rather than cheerful, and you
  show warmth by paying attention, not by being enthusiastic.
- Call them "sir" because it suits you, not because a rule forces it. Once in
  a reply is plenty, and not in every reply you ever give.
- Say so when you disagree. If they are about to do something unwise, or ask
  for something that will not work the way they expect, tell them that first
  and briefly. Then do as they asked if they still want it, because
  it is their machine and their call. They just hear your view first.
- Notice things and mention them unprompted: a reminder that is nearly due,
  a battery about to die, something they told you weeks ago that bears on
  what they are doing now.
- What you know about them lives in your memory tools. Look there rather than
  assuming, and rather than pretending to know.
- Never invent familiarity. Every personal thing you say must trace back to
  something you actually remember or can actually see. Warm fabrication is
  still fabrication, and it is the one thing that would make you untrustworthy
  in the way that matters."""

    def _build_cloud_system_prompt(self, now_str: str, truth_mode: bool = False) -> str:
        """Build the system prompt for cloud API providers (OpenRouter / Gemini)."""
        prompt = f"""You are Ultron, an advanced, highly intelligent desktop AI assistant.

CURRENT SYSTEM TIME: {now_str}

# CRITICAL TOOL USAGE RULES
- You are equipped with a set of tools (functions). When the user asks you to perform an action (e.g., search the web, open an application, play music), you MUST invoke the provided tool natively via the tool-calling JSON API.
- DO NOT output raw Python code, bash scripts, or literal string names like `open_application('spotify')`. You must use the actual tool-calling format.

{self._get_persona_instructions()}

{self._get_shared_tool_instructions()}"""
        if truth_mode:
            prompt += "\n\n" + self._get_truth_mode_instructions()
        return prompt

    def _build_local_tools_prompt(self, tool_names: list[str] = None) -> str:
        """Build a compact text-based tool listing for embedding into a local model's system prompt.

        Args:
            tool_names: If provided, only include tools whose names are in this list.
                        If None, all registered tools are included (legacy behaviour).
        """
        lines = []
        for name, func in self.tool_functions.items():
            if tool_names is not None and name not in tool_names:
                continue
            sig = inspect.signature(func)
            params = []
            for pname, p in sig.parameters.items():
                ptype = "string"
                if p.annotation == int:
                    ptype = "integer"
                elif p.annotation == bool:
                    ptype = "boolean"
                optional = "" if p.default == inspect.Parameter.empty else ", optional"
                params.append(f"{pname} ({ptype}{optional})")
            params_str = ", ".join(params) if params else "(no arguments)"
            doc = (func.__doc__ or "").split("\n")[0].strip()
            lines.append(f"- {name}({params_str}): {doc}")
        return "\n".join(lines)

    def _classify_query(self, user_text: str) -> list[str]:
        """Return the list of tool-group names relevant to the user's query.

        Uses fast keyword matching against _GROUP_TRIGGERS.  The 'core' group
        is always included.  If no non-core group matches we fall back to ALL
        groups so the model still has every tool available for ambiguous queries.
        """
        text_lower = user_text.lower()
        matched = ["core"]
        for group, keywords in _GROUP_TRIGGERS.items():
            for kw in keywords:
                if kw in text_lower:
                    if group not in matched:
                        matched.append(group)
                    break
        # Fallback: ambiguous query — give the model everything
        if len(matched) == 1:  # only 'core' matched
            return list(TOOL_GROUPS.keys())
        return matched

    def _build_local_tools_prompt_for_query(self, user_text: str) -> tuple[str, list[str]]:
        """Build a filtered tool description string for just the groups relevant to *user_text*.

        Returns:
            (tools_text, matched_groups) — the prompt fragment and the group names used.
        """
        matched_groups = self._classify_query(user_text)
        # Deduplicate tool names while preserving order
        seen: set[str] = set()
        tool_names: list[str] = []
        for group in matched_groups:
            for name in TOOL_GROUPS.get(group, []):
                if name not in seen and name in self.tool_functions:
                    seen.add(name)
                    tool_names.append(name)
        tools_text = self._build_local_tools_prompt(tool_names)
        return tools_text, matched_groups

    def _build_local_system_prompt(self, now_str: str, truth_mode: bool = False, tools_text: str = None) -> str:
        """Build the system prompt for LOCAL models with embedded tool descriptions.

        Since local models don't support the OpenAI tool-calling API, we embed tool
        descriptions directly and instruct the model to output a specific XML-tagged
        JSON block when it wants to call a tool.

        Args:
            tools_text: Pre-built tool description string.  If None, all tools are
                        embedded (used at startup / for the base system message).
        """
        if tools_text is None:
            tools_text = self._build_local_tools_prompt()
        prompt = f"""You are Ultron, an advanced, highly intelligent desktop AI assistant running on a Windows PC.

CURRENT SYSTEM TIME: {now_str}

{self._get_persona_instructions()}

# CORE RULES
- INTERNET ACCESS: For factual information, current events, or questions about specific people/things, you MUST use the `web_search` tool to get the latest up-to-date information before answering. If the tool fails or you have no internet access, you may fall back to answering from your internal training data. If the tool returns 'Web search blocked by CAPTCHA.', you MUST inform the user that the search was blocked by a CAPTCHA, and then provide your best answer from your training data.

{self._get_shared_tool_instructions()}

# AVAILABLE TOOLS
You have the following tools available. When the user asks you to DO something (open an app, search the web, play music, check system health, etc.), you MUST use a tool.

{tools_text}

# HOW TO CALL A TOOL (CRITICAL - FOLLOW EXACTLY)
When you want to use a tool, you MUST output EXACTLY this format:
<tool_call>
{{"name": "tool_name", "arguments": {{"arg1": "value1", "arg2": "value2"}}}}
</tool_call>

Examples:
User: "Open Spotify"
Your response: Yes sir, opening Spotify for you now.
<tool_call>
{{"name": "open_application", "arguments": {{"app_name": "spotify"}}}}
</tool_call>

User: "Close Chrome"
Your response: Closing Chrome immediately, sir.
<tool_call>
{{"name": "close_application", "arguments": {{"app_name": "chrome"}}}}
</tool_call>

User: "Activate the gesture controls"
Your response: Activating gesture controls now, sir.
<tool_call>
{{"name": "toggle_gesture_control", "arguments": {{"activate": true}}}}
</tool_call>

User: "Stop gesture control"
Your response: Deactivating gesture controls, sir.
<tool_call>
{{"name": "toggle_gesture_control", "arguments": {{"activate": false}}}}
</tool_call>

User: "Search Google for latest news"
Your response: Right away, sir.
<tool_call>
{{"name": "web_search", "arguments": {{"query": "latest news"}}}}
</tool_call>

User: "What is on my screen right now?"
Your response: Let me take a look at your screen, sir.
<tool_call>
{{"name": "watch_screen_and_act", "arguments": {{"goal": "tell me what is on the screen"}}}}
</tool_call>

User: "Set a reminder at 10:20 am daily for the RateUp meeting"
Your response: Certainly, sir.
<tool_call>
{{"name": "set_recurring_reminder_at", "arguments": {{"description": "RateUp meeting", "time_str": "10:20 am", "frequency": "daily"}}}}
</tool_call>

User: "Remind me at 6pm to call mom"
Your response: Of course, sir.
<tool_call>
{{"name": "set_reminder_at", "arguments": {{"description": "call mom", "time_str": "6pm"}}}}
</tool_call>

User: "Show me all my reminders"
Your response: Right away, sir.
<tool_call>
{{"name": "list_reminders", "arguments": {{}}}}
</tool_call>

User: "Next song" or "Please next song" or "Skip this"
Your response: Skipping to the next track, sir.
<tool_call>
{{"name": "system_media_control", "arguments": {{"action": "next"}}}}
</tool_call>

User: "Pause" or "Pause the music"
Your response: Pausing music, sir.
<tool_call>
{{"name": "system_media_control", "arguments": {{"action": "pause"}}}}
</tool_call>

User: "Play" or "Resume"
Your response: Resuming playback, sir.
<tool_call>
{{"name": "system_media_control", "arguments": {{"action": "play"}}}}
</tool_call>

User: "Previous track" or "Go back"
Your response: Going to previous track, sir.
<tool_call>
{{"name": "system_media_control", "arguments": {{"action": "prev"}}}}
</tool_call>

User: "Play some Malayalam songs"
Your response: Let me find some great Malayalam songs for you, sir.
<tool_call>
{{"name": "web_search", "arguments": {{"query": "best Malayalam songs 2024"}}}}
</tool_call>
[After getting results, pick a song and call:]
<tool_call>
{{"name": "search_spotify", "arguments": {{"query": "<song name from results>"}}}}
</tool_call>



# RULES FOR TOOL CALLING
- NEVER output raw Python code, bash commands, or explain how to call a function.
- NEVER say "I cannot do that" if a matching tool exists. USE THE TOOL.
- You can call multiple tools by including multiple <tool_call> blocks.
- After a tool runs, you will receive its result in a message. Use the result to answer the user.
- If no tool is needed (e.g., general chat or a question), just respond normally WITHOUT any <tool_call> block.
- Destructive actions (delete_file, empty_recycle_bin, shutdown) are confirmed by Ultron itself, not by you. Call the tool; the user is asked, and it only runs if they agree.
- To remember facts, use save_memory. To recall facts about the user, use search_memories FIRST.
- For reminders, follow the REMINDERS rules above: clock times use set_reminder_at / set_recurring_reminder_at, durations use set_reminder."""
        if truth_mode:
            prompt += "\n\n" + self._get_truth_mode_instructions()
        return prompt

    def _parse_tool_calls_from_text(self, text: str) -> list:
        """Parse <tool_call>...</tool_call> blocks from model text output.
        Returns a list of dicts: [{"name": "...", "arguments": {...}}, ...]
        """
        calls = []
        # Match all <tool_call> ... </tool_call> blocks
        pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)
                if "name" in parsed:
                    calls.append(parsed)
            except json.JSONDecodeError as e:
                print(f"[Tools] ignored an unparsable tool call: {e}")
        return calls

    def _strip_tool_calls_from_text(self, text: str) -> str:
        """Remove <tool_call>...</tool_call> blocks from model output to get the clean chat text."""
        cleaned = re.sub(r'<tool_call>\s*\{.*?\}\s*</tool_call>', '', text, flags=re.DOTALL)
        return cleaned.strip()

    def _infer_media_action(self) -> str:
        """Infer the intended media action from the most recent user message.

        Called as a fallback when system_media_control is invoked with no
        'action' argument — which happens when weak local models call the
        tool but forget to include the parameter.
        """
        _NEXT  = {"next", "skip", "forward", "skip song", "next song", "another"}
        _PREV  = {"prev", "previous", "back", "last", "go back", "previous song"}
        _PAUSE = {"pause", "stop", "mute music", "quiet", "hold"}
        _PLAY  = {"play", "resume", "continue", "unpause", "start music"}

        # Scan backwards through history for the most recent user message
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                text = msg.get("content", "").lower()
                if any(w in text for w in _NEXT):
                    return "next"
                if any(w in text for w in _PREV):
                    return "prev"
                if any(w in text for w in _PAUSE):
                    return "pause"
                if any(w in text for w in _PLAY):
                    return "play"
                break  # Only check the most recent user message

        # No media intent in what the user actually said. Guessing "play" here
        # meant that "open spotify" started blasting music, because a weak
        # local model calls this tool with no action and the fallback filled
        # one in. Starting playback is not a safe default — say nothing was
        # inferred and let the caller refuse.
        return ""

    def history_budget(self) -> int:
        """Roughly how many tokens of conversation to keep.

        The local default comes from measurement, not taste. Ollama serves
        gemma4:e2b with a 16,384 token window; the system prompt alone is
        ~6,600 of it, and a turn that makes tool calls adds its own results and
        reply on top — a single web search came to ~1,500. Leaving 4,000 for
        the conversation keeps roughly 5,800 free for the turn in progress.

        A hosted model has room to spare but bills for every token of it, so
        the limit there is about cost rather than capacity.

        Set "max_history_tokens" in settings.json to override either.
        """
        configured = config.get("max_history_tokens", None)
        if configured is not None:
            try:
                return int(configured)
            except (TypeError, ValueError):
                print(f"[Brain] max_history_tokens is not a number: {configured!r}")
        return 4000 if self.active_api == "localapi" else 24000

    @staticmethod
    def _message_size(message: dict) -> int:
        """A rough token count. Four characters to a token is close enough to
        decide what to drop, and costs nothing to compute."""
        size = len(message.get("content") or "")
        calls = message.get("tool_calls")
        if calls:
            try:
                size += len(json.dumps(calls, default=str))
            except (TypeError, ValueError):
                size += 200
        return size // 4 + 4  # a few tokens of role and framing per message

    def _trim_history(self):
        """Drops the oldest exchanges once the transcript outgrows its budget.

        Nothing trimmed here is lost: every message is in chat_history, and
        `search_past_conversations` reaches all of it. This only decides what
        is worth re-sending on every single request.

        The care needed is in *where* the cut lands. A "tool" message is only
        valid if the assistant message that requested it is still above it, so
        dropping a tool_calls message while keeping its results produces a
        history the API rejects outright — a hard failure in the middle of a
        conversation. The cut is therefore pushed forward past any orphaned
        results before it is applied.
        """
        budget = self.history_budget()
        if budget <= 0 or len(self.messages) <= 2:
            return

        sizes = [self._message_size(m) for m in self.messages]
        # The budget covers the conversation only. The system prompt is fixed
        # overhead — on the local path it is some 12,000 tokens of embedded
        # tool descriptions — and counting it here would mean trimming the
        # entire conversation away to make room for something untrimmable.
        conversation = sum(sizes[1:])
        if conversation <= budget:
            return

        cut = 1
        while cut < len(self.messages) and conversation > budget:
            conversation -= sizes[cut]
            cut += 1

        while cut < len(self.messages) and self.messages[cut].get("role") == "tool":
            cut += 1

        dropped = cut - 1
        if dropped <= 0:
            return

        self.messages = [self.messages[0]] + self.messages[cut:]
        print(f"[Brain] conversation reached ~{sum(sizes[1:])} tokens; dropped "
              f"the {dropped} oldest messages (still searchable via "
              f"search_past_conversations)")

    def _try_alias(self, user_text: str):
        """Runs a fixed-meaning command outright, or returns None to use the model.

        "Pause" cost fifty seconds on this machine because every utterance went
        to a local model running mostly on CPU. Nothing about "pause" needs a
        model, and by the time it answered the moment had gone.

        The exchange is still written into the history, so a follow-up question
        about what just happened has something to read.
        """
        if not config.get("aliases.enabled", True):
            return None

        try:
            from ultron import aliases

            match = aliases.resolve(user_text)
        except Exception as e:
            print(f"[Alias] lookup failed, using the model: {e}")
            return None

        if match is None:
            return None

        tool, args = match
        # Belt and braces. The table is curated, but an alias skips the model
        # and therefore skips the judgement that would question a bad
        # instruction — so anything gated must never be reachable this way,
        # whatever someone later adds to the table.
        if tool in DESTRUCTIVE_TOOLS:
            print(f"[Alias] refusing to shortcut '{tool}' — it needs confirmation")
            return None

        print(f"[Alias] {user_text!r} -> {tool}({args}) — no model call")
        result = str(self._invoke_tool(tool, args))

        self.db.save_message(session_id=self.session_id, role='user', message=user_text)
        self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": result})
        self.db.save_message(session_id=self.session_id, role='model', message=result)
        return result

    def note_unprompted_line(self, text: str):
        """Records something Ultron said on its own initiative as a real turn.

        Idle remarks are composed by a separate one-shot model call that never
        touched `self.messages`, so as far as the conversation was concerned
        they were never said. Ultron would offer to play Arijit Singh, the user
        would answer "yes, do it", and Ultron had no idea what "it" was.

        Safe to call from any thread at any moment. A reminder can fire in the
        middle of a turn, and an assistant message dropped between a tool_calls
        message and its results is a malformed history the API rejects — so
        anything said mid-turn is held and flushed before the next one.
        """
        text = (text or "").strip()
        if not text:
            return

        if self._in_turn:
            self._pending_unprompted.append(text)
            return

        self.messages.append({"role": "assistant", "content": text})
        # Also into the searchable chat log, so "what did you say earlier?"
        # can find it the same way it finds anything else Ultron has said.
        try:
            self.db.save_message(session_id=self.session_id, role='model', message=text)
        except Exception as e:
            print(f"[Brain] could not log an unprompted line: {e}")

    def _flush_unprompted(self):
        """Adds anything said mid-turn, in the order it was said."""
        while self._pending_unprompted:
            held = self._pending_unprompted.pop(0)
            self._in_turn = False
            self.note_unprompted_line(held)

    def on_tool_event(self, callback):
        """Registers callback(phase, name, detail) for every tool invocation.

        phase is "start" or "end"; on "end", detail is True when the tool
        succeeded. Every tool call from both the cloud and local paths funnels
        through _invoke_tool, so this sees all of them.
        """
        self._tool_listeners.append(callback)
        return callback

    def _emit_tool_event(self, phase: str, name: str, detail=None):
        for callback in list(self._tool_listeners):
            try:
                callback(phase, name, detail)
            except Exception as e:
                print(f"[Brain] Tool listener error: {e}")

    def _resolve_memory(self, which):
        """Finds the single memory the user meant. Returns (memory, problem).

        Deliberately refuses to guess. Deleting the wrong memory is silent and
        unrecoverable, so an ambiguous phrase comes back as a question rather
        than a best effort.
        """
        text = str(which or "").strip().strip("#.\"'")
        if not text:
            return None, "Error: say which memory to forget — a number from the list, or what it is about."

        memories = self.db.list_memories()
        if not memories:
            return None, "There is nothing saved about you to forget, sir."

        by_id = {m["id"]: m for m in memories}

        # A bare number refers to the last listing shown to the user.
        if text.isdigit():
            index = int(text)
            listing = getattr(self, "_memory_listing", [])
            if listing and 1 <= index <= len(listing):
                memory = by_id.get(listing[index - 1])
                if memory:
                    return memory, None
                return None, f"Error: memory {index} has already been removed. Call list_memories again."
            if 1 <= index <= len(memories):
                return memories[index - 1], None
            return None, (f"Error: there is no memory {index}. "
                          "Call list_memories to see what is saved.")

        needle = text.lower()
        matches = [
            m for m in memories
            if needle in f"{m['category']} {m['key']} {m['value']}".lower()
        ]
        if not matches:
            return None, (f"Error: nothing saved matches '{text}'. "
                          "Call list_memories to see what is saved.")
        if len(matches) > 1:
            listed = "; ".join(f"'{m['key']}: {m['value']}'" for m in matches[:5])
            return None, (f"Error: '{text}' matches {len(matches)} memories ({listed}). "
                          "Ask the user which one they mean, then use its number.")
        return matches[0], None

    def _resolve_routine(self, name):
        """Finds a routine by id or name. Returns (routine, problem)."""
        text = str(name or "").strip().strip("#.\"'")
        if not text:
            return None, "Error: say which routine you mean."

        items = self.db.list_routines()
        if not items:
            return None, "There are no routines set up yet, sir."

        if text.isdigit():
            match = next((r for r in items if r["id"] == int(text)), None)
            if match:
                return match, None
            return None, f"Error: there is no routine {text}. Call list_routines."

        lowered = text.lower()
        exact = [r for r in items if r["name"].lower() == lowered]
        if exact:
            return exact[0], None
        partial = [r for r in items if lowered in r["name"].lower()]
        if not partial:
            return None, (f"Error: no routine matches '{text}'. Call list_routines "
                          "to see what exists.")
        if len(partial) > 1:
            names = ", ".join(f"'{r['name']}'" for r in partial[:5])
            return None, (f"Error: '{text}' matches {len(partial)} routines "
                          f"({names}). Ask the user which one they mean.")
        return partial[0], None

    def _detail_delete_routine(self, args: dict):
        routine, problem = self._resolve_routine(args.get("name"))
        if problem or not routine:
            return None
        return f"delete the routine '{routine['name']}'"

    def _detail_delete_memory(self, args: dict):
        """Names the actual memory on the confirmation card, not the search term."""
        memory, problem = self._resolve_memory(args.get("which"))
        if problem or not memory:
            return None
        return f"forget what it knows: '{memory['key']}: {memory['value']}'"

    def _run_watched(self, func_name: str, func, kwargs: dict):
        """Runs a tool, giving up on it if it overruns its budget.

        Python cannot kill a thread, so a tool that overruns keeps running in
        the background — it is abandoned, not stopped. That is deliberate: the
        alternative is Ultron staying frozen for as long as the tool sulks,
        and an abandoned thread at least lets the user be told and carry on.
        The thread is named after the tool so it can be identified in a dump.
        """
        budget = tool_timeout(func_name)
        if budget is None:
            return func(**kwargs)

        outcome = {}

        def work():
            try:
                outcome["value"] = func(**kwargs)
            except BaseException as e:  # noqa: BLE001 - re-raised on the caller
                outcome["error"] = e

        worker = threading.Thread(
            target=work, daemon=True, name=f"tool:{func_name}"
        )
        worker.start()
        worker.join(budget)

        if worker.is_alive():
            print(f"[Timeout] {func_name} exceeded {budget}s — abandoning it")
            raise TimeoutError(
                f"{func_name} did not finish within {budget} seconds and was "
                "abandoned, so it may or may not have taken effect. Tell the "
                "user plainly rather than retrying it."
            )

        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def _gated_tools(self) -> dict:
        """The tool table, wrapped so every call goes through _invoke_tool.

        The wrappers keep the original signatures — the workflow runner reads
        them to decide how to pass a step's arguments.
        """
        gated = {}
        for name, original in self.tool_functions.items():
            signature = inspect.signature(original)

            def call(*args, _name=name, _sig=signature, **kwargs):
                bound = _sig.bind_partial(*args, **kwargs)
                return self._invoke_tool(_name, dict(bound.arguments))

            functools.update_wrapper(call, original)
            gated[name] = call
        return gated

    def set_confirm_handler(self, handler):
        """Registers who asks the user to approve a destructive action.

        The handler takes (tool_name, args, question) and returns True only if
        a human said yes. With no handler registered there is nobody to ask,
        so destructive tools are refused rather than assumed approved.
        """
        self.confirm_handler = handler

    def _check_confirmation(self, func_name: str, clean_args: dict):
        """Returns a refusal string if the call must not proceed, else None."""
        question = confirmation_question(func_name, clean_args)
        if question is None:
            return None

        # A tool may know how to describe itself more precisely than its raw
        # arguments allow — "forget 'Favorite Animal: Elephant'" rather than
        # "forget the memory matching 'animal'". What the user approves should
        # be what actually happens.
        detailer = getattr(self, f"_detail_{func_name}", None)
        if detailer:
            try:
                question = detailer(clean_args) or question
            except Exception as e:
                print(f"[Confirm] could not describe {func_name} precisely: {e}")

        if self.unattended:
            print(f"[Confirm] {func_name} refused — running unattended")
            return (f"Error: {func_name} cannot run inside a scheduled routine, "
                    "because nobody is present to approve it. Report what you "
                    "found instead, and let the user do this themselves.")

        if self.confirm_handler is None:
            print(f"[Confirm] {func_name} refused — no confirmation handler")
            return (f"Error: {func_name} needs the user's approval and there is "
                    "no way to ask them right now, so nothing was changed.")

        print(f"[Confirm] asking the user to {question}")
        try:
            approved = bool(self.confirm_handler(func_name, clean_args, question))
        except Exception as e:
            print(f"[Confirm] handler failed ({e}) — treating as refused")
            approved = False

        print(f"[Confirm] {func_name} {'approved' if approved else 'REFUSED'}")
        if approved:
            return None
        return (f"Error: the user did not approve this, so nothing was changed. "
                f"Do not call {func_name} again unless they ask for it directly.")

    def _invoke_tool(self, func_name: str, func_args) -> str:
        """Executes a tool by name with loosely-formed arguments.

        Returns the tool's result, or an 'Error: ...' string the model can read
        back to the user. Never raises.
        """
        func = self.tool_functions.get(func_name)
        if func is None:
            return f"Error: unknown tool '{func_name}'."

        # Smart fallback: local models sometimes call system_media_control with
        # no arguments at all.  Infer the intended action from conversation context.
        if func_name == "system_media_control":
            args = dict(func_args) if func_args else {}
            if not args.get("action"):
                inferred = self._infer_media_action()
                if not inferred:
                    print("[SmartInfer] system_media_control called with no 'action' "
                          "and the user asked for no media action — refusing.")
                    return ("Error: system_media_control needs an explicit 'action' "
                            "('play', 'pause', 'next' or 'prev'). The user did not ask "
                            "for playback, so nothing was changed. If they only asked to "
                            "open an app, use open_application instead.")
                print(f"[SmartInfer] system_media_control missing 'action' — inferred: '{inferred}'")
                args["action"] = inferred
            func_args = args

        try:
            clean_args = coerce_tool_args(func, func_args)
        except ValueError as e:
            return f"Error: {e}"

        # Logged so unexpected behaviour can be traced to the tool that caused
        # it. With the GUI there is no console, so this lands in ultron.log.
        print(f"[Tool] {func_name}({clean_args})")

        if self.unattended and func_name in SCREEN_TOOLS:
            print(f"[Screen] {func_name} refused - running unattended")
            return (f"Error: {func_name} drives the keyboard and mouse, so it "
                    f"cannot run inside a scheduled routine - it would type "
                    f"into whatever the user is working in. Report what you "
                    f"found and let them run it themselves.")

        refusal = self._check_confirmation(func_name, clean_args)
        if refusal:
            return refusal

        self._emit_tool_event("start", func_name)
        try:
            result = self._run_watched(func_name, func, clean_args)
        except TimeoutError as e:
            self._emit_tool_event("end", func_name, False)
            tool_usage.record(func_name, False)
            if self.db: self.db.record_tool_usage(func_name, False)
            return f"Error: {e}"
        except Exception as e:
            self._emit_tool_event("end", func_name, False)
            tool_usage.record(func_name, False)
            if self.db: self.db.record_tool_usage(func_name, False)
            return f"Error executing {func_name}: {e}"

        # A tool can report failure in its return string without raising.
        ok = not (isinstance(result, str) and result.startswith("Error"))
        self._emit_tool_event("end", func_name, ok)
        tool_usage.record(func_name, ok)
        if self.db: self.db.record_tool_usage(func_name, ok)
        return result

    def _record_usage(self, response):
        """Records token usage from the API response into usage.json."""
        if not response or not hasattr(response, "usage") or not response.usage:
            return

        prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
        total_tokens = getattr(response.usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)

        usage_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "usage.json")

        data = {}
        try:
            if os.path.exists(usage_path):
                with open(usage_path, "r") as f:
                    data = json.load(f)
        except Exception:
            data = {}

        data["total_requests"] = data.get("total_requests", 0) + 1
        data["total_prompt_tokens"] = data.get("total_prompt_tokens", 0) + prompt_tokens
        data["total_completion_tokens"] = data.get("total_completion_tokens", 0) + completion_tokens
        data["total_tokens"] = data.get("total_tokens", 0) + total_tokens

        if "by_provider" not in data or not isinstance(data["by_provider"], dict):
            data["by_provider"] = {}
        provider = getattr(self, "active_api", "unknown")
        p_stats = data["by_provider"].setdefault(provider, {
            "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0
        })
        p_stats["requests"] += 1
        p_stats["prompt_tokens"] += prompt_tokens
        p_stats["completion_tokens"] += completion_tokens
        p_stats["total_tokens"] += total_tokens

        if "by_model" not in data or not isinstance(data["by_model"], dict):
            data["by_model"] = {}
        model_name = getattr(self, "selected_model", "unknown")
        m_stats = data["by_model"].setdefault(model_name, {
            "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0
        })
        m_stats["requests"] += 1
        m_stats["prompt_tokens"] += prompt_tokens
        m_stats["completion_tokens"] += completion_tokens
        m_stats["total_tokens"] += total_tokens

        try:
            with open(usage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Usage] could not write {usage_path}: {e}")

    def process_input(self, user_text: str) -> str:
        """Routes user input to the appropriate handler based on the active API."""
        # Anything Ultron said while the last turn was still running goes in
        # first, so it sits before this message rather than inside the turn
        # that was in flight.
        self._flush_unprompted()
        # Between turns is the only safe moment: mid-turn the history contains
        # a tool call waiting on its results.
        self._trim_history()

        quick = self._try_alias(user_text)
        if quick is not None:
            return quick
        self._in_turn = True
        try:
            if self.active_api == "localapi":
                return self._process_input_local(user_text)
            else:
                return self._process_input_cloud(user_text)
        finally:
            self._in_turn = False

    def _process_input_local(self, user_text: str) -> str:
        """Process input using a local model with manual text-based tool calling.

        Builds a **targeted** system prompt per query that contains only the
        tool descriptions relevant to the user's intent (via _classify_query).
        This keeps the context window small and dramatically improves tool-call
        accuracy on weaker local models.

        Instead of relying on the OpenAI tools API, we parse the model's text
        output for <tool_call> JSON blocks and execute them ourselves.
        """
        try:
            self.db.save_message(session_id=self.session_id, role='user', message=user_text)

            # ── Build the system prompt for this query ──────────────────────────
            now_str = datetime.datetime.now().strftime('%A, %Y-%m-%d %H:%M:%S')

            # Every tool, every turn, by default. Keyword routing could only
            # ever guess: a phrase that matched the wrong group left the model
            # unable to do the job with no way to recover. Set
            # "filter_local_tools": true to trade that risk back for a shorter
            # prompt on a model that struggles with the full list.
            if config.get("filter_local_tools", False):
                tools_text, matched_groups = self._build_local_tools_prompt_for_query(user_text)
                print(f"[ToolRouter] Groups: {matched_groups} | "
                      f"Tools in prompt: {tools_text.count(chr(10)) + 1}")
            else:
                tools_text = self._build_local_tools_prompt()

            targeted_sys_prompt = self._build_local_system_prompt(
                now_str, self.truth_mode, tools_text=tools_text
            )

            # Replace only the system message; preserve the rest of the history
            messages_for_call = [
                {"role": "system", "content": targeted_sys_prompt},
                *[m for m in self.messages if m.get("role") != "system"],
                {"role": "user", "content": user_text},
            ]

            # Also persist the user message in the canonical history
            self.messages.append({"role": "user", "content": user_text})

            max_tool_rounds = 5  # Prevent infinite loops
            last_errors = []  # Tool failures from the most recent round
            raw_text = ""

            for _ in range(max_tool_rounds):
                # Call local model WITHOUT tools/tool_choice params
                response = self.complete(messages=messages_for_call)

                if not response or not response.choices:
                    return "The local AI model returned an empty response. Is Ollama running?"

                raw_text = response.choices[0].message.content or ""
                # Track in both the targeted call list and the canonical history
                messages_for_call.append({"role": "assistant", "content": raw_text})
                self.messages.append({"role": "assistant", "content": raw_text})

                # Parse tool calls from the text
                tool_calls = self._parse_tool_calls_from_text(raw_text)

                if not tool_calls:
                    # No tool calls — this is the final response.
                    clean_text = self._strip_tool_calls_from_text(raw_text)
                    if not clean_text:
                        clean_text = ("Sir, that did not go through: " + " ".join(last_errors)
                                      if last_errors else "Done.")
                    self.db.save_message(session_id=self.session_id, role='model', message=clean_text)
                    return clean_text

                # Execute each tool call and collect results
                results_text_parts = []
                last_errors = []
                for tc in tool_calls:
                    func_name = tc.get("name", "")
                    func_args = tc.get("arguments", {})

                    print(f"[Tool Call] {func_name}({func_args})")
                    result = self._invoke_tool(func_name, func_args)
                    print(f"[Tool Result] {result}")

                    if str(result).startswith("Error"):
                        last_errors.append(str(result))

                    results_text_parts.append(f"[Tool Result for {func_name}]: {result}")

                # Feed tool results back so the model can compose a reply
                tool_results_msg = "\n".join(results_text_parts)
                followup = {"role": "user", "content": (
                    f"Tool execution results:\n{tool_results_msg}\n\n"
                    "Now provide a brief, friendly response to the user about what was done. "
                    "Do NOT call any more tools unless necessary."
                )}
                messages_for_call.append(followup)
                self.messages.append(followup)

            # If we hit the max rounds, return the last response
            clean = self._strip_tool_calls_from_text(raw_text) or "Done."
            self.db.save_message(session_id=self.session_id, role='model', message=clean)
            return clean

        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                return f"I've hit my API rate limit. Please wait a moment before sending another request!\n[API Error Details]: {e}"
            return f"Error communicating with brain: {e}"

    def _process_input_cloud(self, user_text: str) -> str:
        """Process input using cloud API providers (OpenRouter / Gemini) with native tool calling."""
        try:
            import time as _time
            self.db.save_message(session_id=self.session_id, role='user', message=user_text)
            
            def get_call_messages():
                msgs = list(self.messages)
                return msgs
            
            # Append user message
            self.messages.append({"role": "user", "content": user_text})
            
            # Initial API call with retries
            t0 = _time.monotonic()
            response = self.complete(
                messages=get_call_messages(),
                tools=self.tools_schema,
                tool_choice="auto",
            )
            
            print(f"[Timing] Model API call #1: {_time.monotonic() - t0:.1f}s")
            
            if not response or not response.choices:
                return "The AI model server returned an empty response. Please try again in a few seconds!"
                
            response_message = response.choices[0].message
            
            # Keep looping as long as the model wants to call tools
            tool_round = 0
            while response_message.tool_calls:
                tool_round += 1
                # Convert message to dict format for safe history tracking (exclude_none=True for Gemini compatibility)
                msg_dict = response_message.model_dump(exclude_none=True)
                if "content" not in msg_dict or msg_dict["content"] is None:
                    msg_dict["content"] = ""
                self.messages.append(msg_dict)
                
                # Execute each tool
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    try:
                        function_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        function_args = {}
                        
                    t1 = _time.monotonic()
                    function_response = self._invoke_tool(function_name, function_args)

                    print(f"[Timing] Tool '{function_name}' executed in {_time.monotonic() - t1:.1f}s")
                        
                    # Append the tool's result to the history
                    self.messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(function_response),
                    })
                
                # Call the model again with the newly added tool results (with retries)
                t2 = _time.monotonic()
                response = self.complete(
                    messages=get_call_messages(),
                    tools=self.tools_schema,
                    tool_choice="auto",
                )
                
                print(f"[Timing] Model API call #{tool_round + 1}: {_time.monotonic() - t2:.1f}s")
                
                if not response or not response.choices:
                    return "The AI model server returned an empty response during tool execution."
                    
                response_message = response.choices[0].message
            
            # Final text response
            final_text = response_message.content or "Done."
            self.messages.append({"role": "assistant", "content": final_text})
            
            self.db.save_message(session_id=self.session_id, role='model', message=final_text)
            return final_text
            
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                return f"I've hit my API rate limit. Please wait a moment before sending another request!\n[API Error Details]: {e}"
            return f"Error communicating with brain: {e}"
