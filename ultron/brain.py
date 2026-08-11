import os
import re
import uuid
import datetime
import inspect
import json
from openai import OpenAI

from ultron.database import Database
from ultron.automation import (
    open_application, close_application, system_media_control, 
    search_spotify, adjust_volume, take_screenshot, BrowserManager,
    get_system_health, write_in_notepad, send_whatsapp_message,
    read_clipboard, copy_to_clipboard, find_files, read_file_content, system_power_control,
    empty_recycle_bin, clean_temp_files, create_file, delete_file, list_directory, open_folder,
    copy_file, move_file
)
from ultron.plugins.explorer_plugin import get_selected_file_in_explorer
from ultron.plugins.gmail_plugin import read_emails, send_email, draft_email
from ultron.plugins.docker_plugin import (
    docker_list_containers, docker_list_images, docker_start_container,
    docker_stop_container, docker_remove_container, docker_run_image, docker_start_daemon
)
from ultron.plugins.research_plugin import (
    web_search, research_read_url, save_research, list_research_reports, run_background_research_task
)
from ultron.plugins.workflow_plugin import (
    create_workflow, run_workflow, list_workflows, delete_workflow
)
from ultron.plugins.screen_plugin import (
    screen_read, screen_read_detailed, screen_capture, screen_read_ocr,
    screen_find, screen_get_resolution,
    screen_get_active_window, screen_get_mouse_position
)
from ultron.plugins.document_plugin import read_document
from ultron.plugins.agent_monitor_plugin import (
    agent_monitor_start, agent_monitor_stop, agent_monitor_status,
    agent_monitor_configure, install_agent_hooks
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


# Argument names local models commonly invent, mapped to the real parameter.
_ARG_ALIASES = {
    "time_str": ["time", "at", "when", "at_time", "time_string", "clock_time", "scheduled_for", "start_time"],
    "description": ["task", "text", "message", "reminder", "title", "desc", "content", "body", "note"],
    "frequency": ["repeat", "repeats", "recurrence", "interval", "every"],
    "delay_seconds": ["seconds", "delay", "in_seconds", "duration"],
    "start_delay_seconds": ["delay_seconds", "seconds", "delay", "in_seconds"],
    "until_date_iso": ["until", "until_date", "end_date", "end"],
}


def coerce_tool_args(func, raw_args: dict) -> dict:
    """Normalises loosely-formed tool arguments so weak models can call tools.

    Fills parameters from known aliases, coerces strings to int/bool where the
    signature asks for them, and drops arguments the function does not accept
    (which would otherwise raise TypeError). Raises ValueError when a required
    parameter is still missing.
    """
    if not isinstance(raw_args, dict):
        raw_args = {}

    sig = inspect.signature(func)
    params = sig.parameters
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
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        
                
        # Load Settings
        settings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            settings = {"openrouterapi": True, "geminiapi": False}
            
        # Load API Keys
        keys_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "keys.json")
        try:
            with open(keys_path, "r") as f:
                api_keys = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            api_keys = {}
            
        # Select the first API set to true
        active_api = None
        for api_name in ["openrouterapi", "geminiapi", "localapi"]:
            if settings.get(api_name) is True:
                active_api = api_name
                break
                
        if not active_api:
            active_api = "openrouterapi"
            
        self.active_api = active_api
        self.db = Database()
        self.browser = BrowserManager()
        self.truth_mode = settings.get("truth_mode", False)
        
        if active_api == "openrouterapi":
            api_key = api_keys.get("openrouter")
            if not api_key:
                raise ValueError("openrouter API key is not set correctly in keys.json.")
                
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            self.selected_model = settings.get("openrouter_model") or settings.get("model") or "nvidia/nemotron-3-ultra-550b-a55b:free"
            print("\nUltron AI Provider: OpenRouter Mode")
            
        elif active_api == "geminiapi":
            api_key = api_keys.get("google")
            if not api_key:
                raise ValueError("google API key is not set correctly in keys.json.")
                
            self.client = OpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=api_key,
            )
            self.selected_model = settings.get("gemini_model") or settings.get("model") or "gemini-2.5-flash"
            print("\nUltron AI Provider: Gemini Mode")
        elif active_api == "localapi":
            self.client = OpenAI(
                base_url=settings.get("local_api_url", "http://localhost:11434/v1"),
                api_key="ollama",
            )
            self.selected_model = settings.get("local_model") or settings.get("model") or "gemma4:e2b"
            print("\nUltron AI Provider: Local Mode")
        else:
            raise ValueError(f"Unsupported API selected in settings: {active_api}")
            
        print(f"Selected Model: {self.selected_model}")
        
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
            scheduled_for = (datetime.datetime.now() + datetime.timedelta(seconds=start_delay_seconds)).isoformat()
            self.db.add_task(description, scheduled_for, frequency, until_date_iso)
            return f"Recurring reminder '{frequency}' successfully set to trigger first in {start_delay_seconds} seconds."

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
            freq = (frequency or "daily").strip().lower().rstrip("(),.")
            aliases = {
                "every day": "daily", "day": "daily", "everyday": "daily",
                "every hour": "hourly", "hour": "hourly",
                "every week": "weekly", "week": "weekly",
                "every month": "monthly", "month": "monthly",
            }
            freq = aliases.get(freq, freq)
            if freq not in ("hourly", "daily", "weekly", "monthly"):
                return f"Error: frequency '{frequency}' is not supported. Use hourly, daily, weekly, or monthly."
            try:
                scheduled = parse_time_string(time_str)
            except ValueError as e:
                return f"Error: {e}. Please give a time like '10:20 am' or '17:30'."
            self.db.add_task(description, scheduled.isoformat(), freq, until_date_iso)
            return (f"Recurring reminder '{description}' set to repeat {freq}, "
                    f"starting {scheduled.strftime('%A, %Y-%m-%d at %I:%M %p')}.")

        def delete_reminder(task_id: int) -> str:
            """Deletes a reminder by its numeric id. Call list_reminders first to find the id."""
            self.db.delete_task(task_id)
            return f"Reminder [{task_id}] deleted."

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
            
        def browser_navigate(query_or_url: str) -> str:
            """Navigates the browser to a URL or a Google search."""
            return self.browser.navigate(query_or_url)
            
        def browser_read_page() -> str:
            """Reads and extracts the main text from the current page."""
            return self.browser.read_page()
            
        def browser_click(text_or_selector: str) -> str:
            """Clicks an element on the page based on its text content or CSS selector."""
            return self.browser.click(text_or_selector)
            
        def browser_type_text(text_or_selector: str, input_text: str) -> str:
            """Finds an input field (by placeholder or text) and types text into it."""
            return self.browser.type_text(text_or_selector, input_text)
            
        def browser_press_key(key: str) -> str:
            """Presses a keyboard key on the active page (e.g., 'Enter', 'Escape')."""
            return self.browser.press_key(key)
            
        def browser_go_back() -> str:
            """Navigates back to the previous page in the browser."""
            return self.browser.go_back()
            
        def browser_scroll(direction: str) -> str:
            """Scrolls the page 'up' or 'down'."""
            return self.browser.scroll(direction)
            
        def browser_close() -> str:
            """Closes the browser session."""
            return self.browser.close()
            
        def browser_go_forward() -> str:
            """Navigates forward to the next page in history."""
            return self.browser.go_forward()
            
        def browser_new_tab(url: str = None) -> str:
            """Opens a new browser tab. If url is provided, navigates to it."""
            return self.browser.new_tab(url)
            
        def browser_switch_tab(index: int) -> str:
            """Switches to the tab at the given index (0-based)."""
            return self.browser.switch_tab(index)
            
        def browser_close_tab(index: int = None) -> str:
            """Closes the tab at the given index, or the active tab if index is None."""
            return self.browser.close_tab(index)
            
        def browser_take_screenshot(filename: str = None) -> str:
            """Takes a full page screenshot in the browser and saves it."""
            return self.browser.take_screenshot(filename)
            
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
                output_manager=om
            )

        def run_workflow_tool(name: str) -> str:
            """Runs a saved workflow by name. Executes all steps of the workflow sequentially.
            Use this when the user asks to 'start', 'run', or 'execute' a saved workflow.
            Args:
                name: The name of the workflow to run.
            """
            return run_workflow(name, tool_functions=self.tool_functions)

        # Register all tools in a dictionary
        self.tool_functions = {
            "save_memory": save_memory,
            "set_reminder": set_reminder,
            "search_memories": search_memories,
            "search_past_conversations": search_past_conversations,
            "open_application": open_application,
            "close_application": close_application,
            "system_media_control": system_media_control,
            "adjust_volume": adjust_volume,
            "take_screenshot": take_screenshot,
            "search_spotify": search_spotify,
            "browser_navigate": browser_navigate,
            "browser_read_page": browser_read_page,
            "browser_click": browser_click,
            "browser_type_text": browser_type_text,
            "browser_press_key": browser_press_key,
            "browser_go_back": browser_go_back,
            "browser_go_forward": browser_go_forward,
            "browser_new_tab": browser_new_tab,
            "browser_switch_tab": browser_switch_tab,
            "browser_close_tab": browser_close_tab,
            "browser_take_screenshot": browser_take_screenshot,
            "browser_scroll": browser_scroll,
            "browser_close": browser_close,
            "get_system_health": get_system_health,
            "write_in_notepad": write_in_notepad,
            "send_whatsapp_message": send_whatsapp_message,
            "read_clipboard": read_clipboard,
            "copy_to_clipboard": copy_to_clipboard,
            "find_files": find_files,
            "read_file_content": read_file_content,
            "create_file": create_file,
            "delete_file": delete_file,
            "copy_file": copy_file,
            "move_file": move_file,
            "list_directory": list_directory,
            "empty_recycle_bin": empty_recycle_bin,
            "clean_temp_files": clean_temp_files,
            "system_power_control": system_power_control,
            "read_emails": read_emails,
            "send_email": send_email,
            "draft_email": draft_email,
            "docker_list_containers": docker_list_containers,
            "docker_list_images": docker_list_images,
            "docker_start_container": docker_start_container,
            "docker_stop_container": docker_stop_container,
            "docker_remove_container": docker_remove_container,
            "docker_run_image": docker_run_image,
            "docker_start_daemon": docker_start_daemon,
            "web_search": web_search,
            "research_read_url": research_read_url,
            "save_research": save_research,
            "list_research_reports": list_research_reports,
            "start_background_research": start_background_research,
            "create_workflow": create_workflow,
            "run_workflow": run_workflow_tool,
            "list_workflows": list_workflows,
            "delete_workflow": delete_workflow,
            "set_reminder": set_reminder,
            "set_reminder_at": set_reminder_at,
            "set_recurring_reminder": set_recurring_reminder,
            "set_recurring_reminder_at": set_recurring_reminder_at,
            "list_reminders": list_reminders,
            "delete_reminder": delete_reminder,
            "get_selected_file_in_explorer": get_selected_file_in_explorer,
            "screen_read": screen_read,
            "screen_read_detailed": screen_read_detailed,
            "screen_capture": screen_capture,
            "screen_read_ocr": screen_read_ocr,
            "screen_find": screen_find,
            "screen_get_resolution": screen_get_resolution,
            "screen_get_active_window": screen_get_active_window,
            "screen_get_mouse_position": screen_get_mouse_position,
            "open_folder": open_folder,
            "read_document": read_document,
            "agent_monitor_start": agent_monitor_start,
            "agent_monitor_stop": agent_monitor_stop,
            "agent_monitor_status": agent_monitor_status,
            "agent_monitor_configure": agent_monitor_configure,
            "install_agent_hooks": install_agent_hooks
        }
        
        # Generate the JSON schema for OpenRouter tools
        self.tools_schema = [ToolBridge.function_to_schema(func) for func in self.tool_functions.values()]
        
        now_str = datetime.datetime.now().strftime('%A, %Y-%m-%d %H:%M:%S')
        
        # Build system prompt — for local models, embed tool descriptions directly
        if self.active_api == "localapi":
            sys_instruct = self._build_local_system_prompt(now_str, self.truth_mode)
        else:
            sys_instruct = self._build_cloud_system_prompt(now_str, self.truth_mode)
        
        # Initialize conversation history
        self.messages = [{"role": "system", "content": sys_instruct}]
        
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

    def _build_cloud_system_prompt(self, now_str: str, truth_mode: bool = False) -> str:
        """Build the system prompt for cloud API providers (OpenRouter / Gemini)."""
        prompt = f"""You are Ultron, an advanced, highly intelligent desktop AI assistant.

CURRENT SYSTEM TIME: {now_str}

# CRITICAL TOOL USAGE RULES
- You are equipped with a set of tools (functions). When the user asks you to perform an action (e.g., search the web, open an application, play music), you MUST invoke the provided tool natively via the tool-calling JSON API.
- DO NOT output raw Python code, bash scripts, or literal string names like `open_application('spotify')`. You must use the actual tool-calling format.

# CORE RULES
- Provide concise, accurate, and professional answers.
- ALWAYS address the user respectfully as "sir" (e.g., "Yes, sir", "You are welcome, sir", "How may I help you, sir?").
- You DO NOT know any personal details about the user by default. You MUST use your memory tools to find out.

# MEMORY ENGINE (CRITICAL)
- TO REMEMBER: If the user tells you a fact, preference, or detail to remember, use `save_memory`.
- TO RECALL FACTS: If the user asks about themselves (e.g., "who am I?", "what is my name?", "what is my github"), you MUST ALWAYS use `search_memories` BEFORE responding. NEVER say you don't know until you have searched the database!
- TO RECALL CHATS: If the user references a past conversation, use `search_past_conversations`.
- REMINDERS (CRITICAL): DO NOT use `save_memory` for time-based reminders.
  - If the user gives a CLOCK TIME ("at 10:20 am", "at 5pm", "tomorrow at 9"), use `set_reminder_at` with `time_str` set to that time verbatim. Do NOT convert it to seconds yourself.
  - If that clock time REPEATS ("every day at 10:20 am", "daily at 6pm"), use `set_recurring_reminder_at` with `time_str` and `frequency` ('hourly', 'daily', 'weekly', 'monthly').
  - Only if the user gives a DURATION ("in 5 minutes") use `set_reminder` with `delay_seconds`, or `set_recurring_reminder` with `start_delay_seconds`.
  - To show reminders use `list_reminders`; to remove one use `list_reminders` first, then `delete_reminder` with its id.

# BROWSER AUTOMATION
You have full interactive control over a web browser.
- Navigating: Use `browser_navigate` to search Google or go to a URL.
- Tabs: Use `browser_new_tab`, `browser_switch_tab`, and `browser_close_tab` for multi-tasking.
- Interacting: Use `browser_click`, `browser_type_text`, `browser_press_key`, `browser_scroll`, `browser_go_back`, and `browser_go_forward` to drive the page.
- Extracting: Use `browser_read_page` to extract text from the current page, and `browser_take_screenshot` to capture it visually.
- Closing: Use `browser_close` to close the whole browser when instructed.

# SYSTEM AUTOMATION & TOOLS
- Use `open_application` to launch local desktop apps.
- Use `close_application` to close local desktop apps.
- TO CONTROL MUSIC: You MUST use `system_media_control` with action 'play', 'pause', 'next', or 'prev'. 
- TO ADJUST VOLUME: Use `adjust_volume` with action 'volume_up', 'volume_down', or 'mute'.

# SCREEN AWARENESS
- PRIMARY: If the user asks "what is on my screen", "what do you see", or asks about a visible element, use `screen_read`. This is fast and returns a text summary of all visible UI controls and text. It works with any model (no vision needed). You can pass a `window_title` to read a specific window even if it is not focused.
- DETAILED: Use `screen_read_detailed` when you need precise layout info (bounding boxes, control types, enabled states) — e.g., for automation or describing exact UI positions.
- SCREENSHOT: Use `screen_capture` only when the user explicitly asks for a screenshot, or when you need to visually analyze something (images, colors, layout). This saves a screenshot and returns base64 image data.
- OCR FALLBACK: Use `screen_read_ocr` for canvas, game, or custom-drawn UI content that `screen_read` cannot detect. You can specify a screen region (left, top, right, bottom) or read the full screen.
- If you need to find where something is on screen, use `screen_find`.
- To get window or cursor context, use `screen_get_active_window` and `screen_get_mouse_position`.
- TO SEARCH MUSIC: Use the `search_spotify` tool to open it directly in the Spotify app.
- SYSTEM HEALTH: Use `get_system_health` to check CPU, RAM, Battery %, and Disk storage space.
- WRITE IN NOTEPAD: Use `write_in_notepad` to type notes or text directly into Notepad.
- WHATSAPP MESSAGING: Use `send_whatsapp_message` to send messages to contacts via WhatsApp Desktop.
- CLIPBOARD: Use `read_clipboard` to inspect copied text, and `copy_to_clipboard` to copy any text, URL, link, or note directly to the Windows Clipboard for the user.
- FILE & FOLDER CONTROL: Use `find_files` to locate files, `read_file_content` to read text files, `create_file` to create or overwrite text files, `delete_file` to delete files, `copy_file` to copy files or folders, `move_file` to move files or folders, `list_directory` to list folder contents, and `open_folder` to open a folder directly in Windows File Explorer (e.g. 'Downloads').
- SELECTED FILES: If the user says "this file", "these files", or "the selected file", use `get_selected_file_in_explorer` to find out which files they currently have highlighted in Windows File Explorer.
- READING DOCUMENTS: Use `read_document` to read PDF, Word (DOCX), and image files (PNG, JPG, BMP, TIFF, WEBP). For images, it extracts text using OCR. If a PDF/DOCX is long, call it first without a page, then use `page=1`, `page=2`, etc. to read chunk by chunk.
- RECYCLE BIN & DISK CLEANUP: Use `empty_recycle_bin` to empty the Windows Recycle Bin completely, and `clean_temp_files` to remove temporary junk files from %TEMP% folder to free up space.
- DELETION CONFIRMATION (CRITICAL): For destructive actions (`delete_file` or `empty_recycle_bin`), ALWAYS ask the user for explicit confirmation (e.g., "Are you sure you want to delete <file>, sir?") before proceeding. Call `delete_file` or `empty_recycle_bin` with `confirmed=True` ONLY when the user explicitly confirms (e.g. says "yes", "confirm", or "proceed").
- POWER CONTROL: Use `system_power_control` to lock PC, sleep PC, or schedule system shutdown.
- GMAIL: Use `read_emails` to read recent unread emails, `send_email` to send an email, and `draft_email` to create a draft.
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
- DELETE: When the user asks to remove or delete a workflow, use `delete_workflow`.

# AGENT MONITOR
You can watch the user's coding agents (Claude Code in VSCode, Antigravity, etc.) and alert them when an agent stops, finishes, or asks a question.
- START/STOP: Use `agent_monitor_start` and `agent_monitor_stop` when the user asks to watch or stop watching their agents.
- STATUS: If the user asks "is Claude done?", "what are my agents doing?", or "is anything waiting on me?", use `agent_monitor_status`.
- ALERT STYLE: If the user wants alerts quieter or louder, use `agent_monitor_configure` with 'toast', 'voice', or 'both'.
- SETUP: `install_agent_hooks` installs the global Claude Code hooks so every session on the machine reports in. This only needs to run once — tell the user to restart their Claude Code sessions afterwards."""
        if truth_mode:
            prompt += "\n\n" + self._get_truth_mode_instructions()
        return prompt

    def _build_local_tools_prompt(self) -> str:
        """Build a compact text-based tool listing for embedding into a local model's system prompt."""
        lines = []
        for name, func in self.tool_functions.items():
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

    def _build_local_system_prompt(self, now_str: str, truth_mode: bool = False) -> str:
        """Build the system prompt for LOCAL models with embedded tool descriptions.
        
        Since local models don't support the OpenAI tool-calling API, we embed all tool
        descriptions directly and instruct the model to output a specific XML-tagged
        JSON block when it wants to call a tool.
        """
        tools_text = self._build_local_tools_prompt()
        prompt = f"""You are Ultron, an advanced, highly intelligent desktop AI assistant running on a Windows PC.

CURRENT SYSTEM TIME: {now_str}

# CORE RULES
- Provide concise, accurate, and professional answers.
- ALWAYS address the user respectfully as "sir".
- You DO NOT know personal details about the user by default. Use memory tools to find out.
- INTERNET ACCESS: For factual information, current events, or questions about specific people/things, you MUST use the `web_search` tool to get the latest up-to-date information before answering. If the tool fails or you have no internet access, you may fall back to answering from your internal training data. If the tool returns 'Web search blocked by CAPTCHA.', you MUST inform the user that the search was blocked by a CAPTCHA, and then provide your best answer from your training data.

# MEMORY ENGINE (CRITICAL)
- TO REMEMBER: If the user tells you a fact, preference, or detail to remember, use `save_memory`.
- TO RECALL FACTS: If the user asks about themselves (e.g., "who am I?", "what is my name?", "what is my github"), you MUST ALWAYS use `search_memories` BEFORE responding. NEVER say you don't know until you have searched the database!
- TO RECALL CHATS: If the user references a past conversation, use `search_past_conversations`.

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

User: "Search Google for latest news"
Your response: Right away, sir.
<tool_call>
{{"name": "web_search", "arguments": {{"query": "latest news"}}}}
</tool_call>

User: "What is on my screen right now?"
Your response: Let me take a look at your screen, sir.
<tool_call>
{{"name": "screen_read", "arguments": {{}}}}
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

# REMINDERS (CRITICAL)
- A CLOCK TIME ("at 10:20 am", "at 5pm", "tomorrow at 9") means `set_reminder_at`. Pass the time as `time_str` EXACTLY as the user said it. NEVER try to convert a clock time into seconds yourself.
- A REPEATING clock time ("every day at 10:20 am") means `set_recurring_reminder_at` with `time_str` and `frequency` ('hourly', 'daily', 'weekly', 'monthly').
- Only a DURATION ("in 5 minutes") uses `set_reminder` with `delay_seconds`.
- Use the EXACT argument names shown above. Do not invent argument names like "time" or "task".
- To show reminders use `list_reminders`. To remove one, call `list_reminders` first, then `delete_reminder` with the numeric id.
- If a tool result starts with "Error", you MUST tell the user what went wrong. NEVER reply "Done." after an error.

# SCREEN AWARENESS
- PRIMARY: If the user asks "what is on my screen", use `screen_read`. This is the fastest method — it reads all visible UI controls and text without taking a screenshot. Pass `window_title` to read a specific window.
- DETAILED: Use `screen_read_detailed` for precise layout info (bounding boxes, element positions).
- SCREENSHOT: Use `screen_capture` only when the user explicitly asks for a screenshot or visual analysis is needed.
- OCR FALLBACK: Use `screen_read_ocr` for canvas, game, or custom-drawn UI that `screen_read` cannot detect.
- To find where something is on screen, use `screen_find`.
- To get window or cursor context, use `screen_get_active_window` and `screen_get_mouse_position`.
- To open file system folders like Downloads or Desktop, use `open_folder`.
- To read PDF, Word (DOCX), and image files (PNG, JPG, BMP, TIFF, WEBP), use `read_document`. For images, it extracts text using OCR. For long PDF/DOCX files, provide a `page` number.

# AGENT MONITOR
- If the user asks "is Claude done?", "what are my agents doing?", or "is anything waiting on me?", use `agent_monitor_status`.
- Use `agent_monitor_start` / `agent_monitor_stop` to turn agent watching on or off.
- Use `agent_monitor_configure` with 'toast', 'voice', or 'both' to change how alerts are delivered.
- Use `install_agent_hooks` only when the user asks to set up or install agent monitoring globally.

# RULES FOR TOOL CALLING
- NEVER output raw Python code, bash commands, or explain how to call a function.
- NEVER say "I cannot do that" if a matching tool exists. USE THE TOOL.
- You can call multiple tools by including multiple <tool_call> blocks.
- After a tool runs, you will receive its result in a message. Use the result to answer the user.
- If no tool is needed (e.g., general chat or a question), just respond normally WITHOUT any <tool_call> block.
- For destructive actions (delete_file, empty_recycle_bin), ask for confirmation first before calling the tool.
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
            except json.JSONDecodeError:
                pass
        return calls

    def _strip_tool_calls_from_text(self, text: str) -> str:
        """Remove <tool_call>...</tool_call> blocks from model output to get the clean chat text."""
        cleaned = re.sub(r'<tool_call>\s*\{.*?\}\s*</tool_call>', '', text, flags=re.DOTALL)
        return cleaned.strip()

    def _invoke_tool(self, func_name: str, func_args) -> str:
        """Executes a tool by name with loosely-formed arguments.

        Returns the tool's result, or an 'Error: ...' string the model can read
        back to the user. Never raises.
        """
        func = self.tool_functions.get(func_name)
        if func is None:
            return f"Error: unknown tool '{func_name}'."

        try:
            clean_args = coerce_tool_args(func, func_args)
        except ValueError as e:
            return f"Error: {e}"

        try:
            return func(**clean_args)
        except Exception as e:
            return f"Error executing {func_name}: {e}"

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
        except Exception:
            pass

    def process_input(self, user_text: str) -> str:
        """Routes user input to the appropriate handler based on the active API."""
        if self.active_api == "localapi":
            return self._process_input_local(user_text)
        else:
            return self._process_input_cloud(user_text)

    def _process_input_local(self, user_text: str) -> str:
        """Process input using a local model with manual text-based tool calling.
        
        Instead of relying on the OpenAI tools API, we parse the model's text
        output for <tool_call> JSON blocks and execute them ourselves.
        """
        try:
            self.db.save_message(session_id=self.session_id, role='user', message=user_text)
            self.messages.append({"role": "user", "content": user_text})

            max_tool_rounds = 5  # Prevent infinite loops
            last_errors = []  # Tool failures from the most recent round

            for _ in range(max_tool_rounds):
                # Call local model WITHOUT tools/tool_choice params
                for attempt in range(3):
                    try:
                        response = self.client.chat.completions.create(
                            model=self.selected_model,
                            messages=self.messages,
                        )
                        if response and response.choices:
                            self._record_usage(response)
                            break
                    except Exception as e:
                        if attempt == 2:
                            raise e
                        import time
                        time.sleep(2)

                if not response or not response.choices:
                    return "The local AI model returned an empty response. Is Ollama running?"

                raw_text = response.choices[0].message.content or ""
                self.messages.append({"role": "assistant", "content": raw_text})

                # Parse tool calls from the text
                tool_calls = self._parse_tool_calls_from_text(raw_text)

                if not tool_calls:
                    # No tool calls — this is the final response. Never let a
                    # failed tool be reported to the user as a bare "Done."
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

                # Feed tool results back as a user message so the model can respond
                tool_results_msg = "\n".join(results_text_parts)
                self.messages.append({"role": "user", "content": f"Tool execution results:\n{tool_results_msg}\n\nNow provide a brief, friendly response to the user about what was done. Do NOT call any more tools unless necessary."})

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
            
            # Append user message
            self.messages.append({"role": "user", "content": user_text})
            
            # Initial API call with retries
            t0 = _time.monotonic()
            for attempt in range(3):
                try:
                    response = self.client.chat.completions.create(
                        model=self.selected_model,
                        messages=self.messages,
                        tools=self.tools_schema,
                        tool_choice="auto"
                    )
                    if response and response.choices:
                        self._record_usage(response)
                        break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    _time.sleep(2)
            
            print(f"[Timing] Model API call #1: {_time.monotonic() - t0:.1f}s")
            
            if not response or not response.choices:
                return "The AI model server returned an empty response. Please try again in a few seconds!"
                
            response_message = response.choices[0].message
            
            # Keep looping as long as the model wants to call tools
            tool_round = 0
            while response_message.tool_calls:
                tool_round += 1
                # Convert message to dict format for safe history tracking (exclude_none=True for Gemini compatibility)
                self.messages.append(response_message.model_dump(exclude_none=True))
                
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
                for attempt in range(3):
                    try:
                        response = self.client.chat.completions.create(
                            model=self.selected_model,
                            messages=self.messages,
                            tools=self.tools_schema,
                            tool_choice="auto"
                        )
                        if response and response.choices:
                            self._record_usage(response)
                            break
                    except Exception as e:
                        if attempt == 2:
                            raise e
                        _time.sleep(2)
                
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
