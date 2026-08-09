import os
import re
import uuid
import datetime
import inspect
import json
from dotenv import load_dotenv
from openai import OpenAI

from ultron.database import Database
from ultron.automation import (
    open_application, close_application, system_media_control, 
    search_spotify, adjust_volume, take_screenshot, BrowserManager,
    get_system_health, write_in_notepad, send_whatsapp_message,
    read_clipboard, copy_to_clipboard, find_files, read_file_content, system_power_control,
    empty_recycle_bin, clean_temp_files, create_file, delete_file, list_directory, open_folder
)
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
    screen_capture, screen_analyze, screen_find, screen_get_resolution,
    screen_get_active_window, screen_get_mouse_position
)

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
        
        # Ensure the latest .env file variables are loaded with override
        load_dotenv(override=True)
        
        # Load Settings
        settings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            settings = {"openrouterapi": True, "geminiapi": False}
            
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
        
        if active_api == "openrouterapi":
            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY or API_KEY is not set correctly in the .env file.")
                
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            self.selected_model = settings.get("openrouter_model") or settings.get("model") or "nvidia/nemotron-3-ultra-550b-a55b:free"
            print("\nUltron AI Provider: OpenRouter Mode")
            
        elif active_api == "geminiapi":
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY or API_KEY is not set correctly in the .env file.")
                
            self.client = OpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=api_key,
            )
            self.selected_model = settings.get("gemini_model") or settings.get("model") or "gemini-2.5-flash"
            print("\nUltron AI Provider: Gemini Mode")
        elif active_api == "localapi":
            self.client = OpenAI(
                base_url=settings.get("local_api_url", "http://localhost:11434/v1"),
                api_key=os.getenv("LOCAL_API_KEY", "ollama"),
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
            "screen_capture": screen_capture,
            "screen_analyze": screen_analyze,
            "screen_find": screen_find,
            "screen_get_resolution": screen_get_resolution,
            "screen_get_active_window": screen_get_active_window,
            "screen_get_mouse_position": screen_get_mouse_position,
            "open_folder": open_folder
        }
        
        # Generate the JSON schema for OpenRouter tools
        self.tools_schema = [ToolBridge.function_to_schema(func) for func in self.tool_functions.values()]
        
        now_str = datetime.datetime.now().strftime('%A, %Y-%m-%d %H:%M:%S')
        
        # Build system prompt — for local models, embed tool descriptions directly
        if self.active_api == "localapi":
            sys_instruct = self._build_local_system_prompt(now_str)
        else:
            sys_instruct = self._build_cloud_system_prompt(now_str)
        
        # Initialize conversation history
        self.messages = [{"role": "system", "content": sys_instruct}]
        self.is_asleep = False
        
        print("Ultron's Brain initialized and ready.")

    def _build_cloud_system_prompt(self, now_str: str) -> str:
        """Build the system prompt for cloud API providers (OpenRouter / Gemini)."""
        return f"""You are Ultron, an advanced, highly intelligent desktop AI assistant.

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
- REMINDERS: If the user asks to be reminded of something, calculate the delay in seconds and use the `set_reminder` tool with `delay_seconds`. DO NOT use `save_memory` for time-based reminders.

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
- If the user asks "what is on my screen" or asks about a visual element, use `screen_capture`. This permanently saves a screenshot to the screenshots folder and returns the image data to you for analysis.
- If you need to find where something is on screen, use `screen_find`.
- To get window or cursor context, use `screen_get_active_window` and `screen_get_mouse_position`.
- TO SEARCH MUSIC: Use the `search_spotify` tool to open it directly in the Spotify app.
- SYSTEM HEALTH: Use `get_system_health` to check CPU, RAM, Battery %, and Disk storage space.
- WRITE IN NOTEPAD: Use `write_in_notepad` to type notes or text directly into Notepad.
- WHATSAPP MESSAGING: Use `send_whatsapp_message` to send messages to contacts via WhatsApp Desktop.
- CLIPBOARD: Use `read_clipboard` to inspect copied text, and `copy_to_clipboard` to copy any text, URL, link, or note directly to the Windows Clipboard for the user.
- FILE & FOLDER CONTROL: Use `find_files` to locate files, `read_file_content` to read text files, `create_file` to create or overwrite text files, `delete_file` to delete files, `list_directory` to list folder contents, and `open_folder` to open a folder directly in Windows File Explorer (e.g. 'Downloads').
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
- DELETE: When the user asks to remove or delete a workflow, use `delete_workflow`."""

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

    def _build_local_system_prompt(self, now_str: str) -> str:
        """Build the system prompt for LOCAL models with embedded tool descriptions.
        
        Since local models don't support the OpenAI tool-calling API, we embed all tool
        descriptions directly and instruct the model to output a specific XML-tagged
        JSON block when it wants to call a tool.
        """
        tools_text = self._build_local_tools_prompt()
        return f"""You are Ultron, an advanced, highly intelligent desktop AI assistant running on a Windows PC.

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
{{"name": "screen_capture", "arguments": {{}}}}
</tool_call>

# SCREEN AWARENESS
- If the user asks "what is on my screen" or asks about a visual element, use `screen_capture`. This permanently saves a screenshot and returns the image data to you.
- If you need to find where something is on screen, use `screen_find`.
- To get window or cursor context, use `screen_get_active_window` and `screen_get_mouse_position`.
- To open file system folders like Downloads or Desktop, use `open_folder`.

# RULES FOR TOOL CALLING
- NEVER output raw Python code, bash commands, or explain how to call a function.
- NEVER say "I cannot do that" if a matching tool exists. USE THE TOOL.
- You can call multiple tools by including multiple <tool_call> blocks.
- After a tool runs, you will receive its result in a message. Use the result to answer the user.
- If no tool is needed (e.g., general chat or a question), just respond normally WITHOUT any <tool_call> block.
- For destructive actions (delete_file, empty_recycle_bin), ask for confirmation first before calling the tool.
- To remember facts, use save_memory. To recall facts about the user, use search_memories FIRST.
- To set time-based reminders, calculate the delay in seconds and use the set_reminder tool with delay_seconds."""

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

    def _handle_sleep_wake(self, user_text: str):
        """Check for sleep/wake commands. Returns (handled: bool, reply: str|None)."""
        text_lower = user_text.lower().strip()
        sleep_phrases = ["go to sleep", "take a nap", "take a rest"]
        wake_phrases = ["wake up", "get up"]
        sleep_words = {"sleep", "nap", "rest"}
        wake_words = {"wakeup", "getup", "wake"}

        words = set(text_lower.split())
        is_sleep_cmd = any(p in text_lower for p in sleep_phrases) or bool(words.intersection(sleep_words))
        is_wake_cmd = any(p in text_lower for p in wake_phrases) or bool(words.intersection(wake_words))

        if self.is_asleep:
            if is_wake_cmd:
                self.is_asleep = False
                reply = "I am awake now, sir! How can I assist you?"
                self.db.save_message(session_id=self.session_id, role='user', message=user_text)
                self.db.save_message(session_id=self.session_id, role='model', message=reply)
                return True, reply
            else:
                return True, None
        else:
            if is_sleep_cmd:
                self.is_asleep = True
                reply = "Going to sleep now, sir. Zzz... Say 'wake up' or 'get up' when you need me!"
                self.db.save_message(session_id=self.session_id, role='user', message=user_text)
                self.db.save_message(session_id=self.session_id, role='model', message=reply)
                return True, reply

        return False, None

    def process_input(self, user_text: str) -> str:
        """Routes user input to the appropriate handler based on the active API."""
        handled, reply = self._handle_sleep_wake(user_text)
        if handled:
            return reply

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
                    # No tool calls — this is the final response
                    clean_text = self._strip_tool_calls_from_text(raw_text) or "Done."
                    self.db.save_message(session_id=self.session_id, role='model', message=clean_text)
                    return clean_text

                # Execute each tool call and collect results
                results_text_parts = []
                for tc in tool_calls:
                    func_name = tc.get("name", "")
                    func_args = tc.get("arguments", {})

                    if func_name in self.tool_functions:
                        try:
                            result = self.tool_functions[func_name](**func_args)
                        except Exception as e:
                            result = f"Error executing tool: {e}"
                    else:
                        result = f"Unknown tool: {func_name}"

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
            self.db.save_message(session_id=self.session_id, role='user', message=user_text)
            
            # Append user message
            self.messages.append({"role": "user", "content": user_text})
            
            # Initial API call with retries
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
                    import time
                    time.sleep(2)
            
            if not response or not response.choices:
                return "The AI model server returned an empty response. Please try again in a few seconds!"
                
            response_message = response.choices[0].message
            
            # Keep looping as long as the model wants to call tools
            while response_message.tool_calls:
                # Convert message to dict format for safe history tracking (exclude_none=True for Gemini compatibility)
                self.messages.append(response_message.model_dump(exclude_none=True))
                
                # Execute each tool
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    try:
                        function_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        function_args = {}
                        
                    if function_name in self.tool_functions:
                        function_to_call = self.tool_functions[function_name]
                        try:
                            # Actually execute the python code
                            function_response = function_to_call(**function_args)
                        except Exception as e:
                            function_response = f"Error executing tool: {e}"
                    else:
                        function_response = f"Unknown tool: {function_name}"
                        
                    # Append the tool's result to the history
                    self.messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(function_response),
                    })
                
                # Call the model again with the newly added tool results (with retries)
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
                        import time
                        time.sleep(2)
                
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
