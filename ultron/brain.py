import os
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
    read_clipboard, copy_to_clipboard, find_files, read_file_content, system_power_control
)
from ultron.gmail_plugin import read_emails, send_email, draft_email
from ultron.docker_plugin import (
    docker_list_containers, docker_list_images, docker_start_container,
    docker_stop_container, docker_remove_container, docker_run_image, docker_start_daemon
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
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise ValueError("API_KEY is not set correctly in the .env file.")
        
        self.db = Database()
        self.browser = BrowserManager()
        
        # Initialize OpenRouter client
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.selected_model = "nvidia/nemotron-3-ultra-550b-a55b:free"
        
        print("\nUltron AI Provider: OpenRouter Mode")
        print(f"Selected Model: {self.selected_model}")
        
        # Define memory tools
        def save_memory(category: str, key: str, value: str, importance: int) -> str:
            """Saves a memory for the user. Call this when the user asks you to remember something."""
            self.db.save_memory(category, key, value, importance)
            return "Memory saved successfully."

        def set_reminder(description: str, scheduled_for: str) -> str:
            """Sets a reminder for a specific time in the future. scheduled_for MUST be an ISO 8601 string."""
            self.db.add_task(description, scheduled_for)
            return f"Reminder successfully set for {scheduled_for}."

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
            "browser_scroll": browser_scroll,
            "browser_close": browser_close,
            "get_system_health": get_system_health,
            "write_in_notepad": write_in_notepad,
            "send_whatsapp_message": send_whatsapp_message,
            "read_clipboard": read_clipboard,
            "copy_to_clipboard": copy_to_clipboard,
            "find_files": find_files,
            "read_file_content": read_file_content,
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
            "docker_start_daemon": docker_start_daemon
        }
        
        # Generate the JSON schema for OpenRouter tools
        self.tools_schema = [ToolBridge.function_to_schema(func) for func in self.tool_functions.values()]
        
        now_str = datetime.datetime.now().strftime('%A, %Y-%m-%d %H:%M:%S')
        sys_instruct = f"""You are Ultron, an advanced, highly intelligent desktop AI assistant.

CURRENT SYSTEM TIME: {now_str}

# CORE RULES
- Provide concise, accurate, and professional answers.
- ALWAYS address the user respectfully as "sir" (e.g., "Yes, sir", "You are welcome, sir", "How may I help you, sir?").
- You DO NOT know any personal details about the user by default. You MUST use your memory tools to find out.

# MEMORY ENGINE (CRITICAL)
- TO REMEMBER: If the user tells you a fact, preference, or detail to remember, use `save_memory`.
- TO RECALL FACTS: If the user asks about themselves (e.g., "who am I?", "what is my name?"), you MUST ALWAYS use `search_memories` BEFORE responding. NEVER say you don't know until you have searched the database.
- TO RECALL CHATS: If the user references a past conversation, use `search_past_conversations`.
- REMINDERS: If the user asks to be reminded of something at a specific time, use the `set_reminder` tool with the calculated ISO 8601 timestamp. DO NOT use `save_memory` for time-based reminders.

# BROWSER AUTOMATION
You have full interactive control over a web browser.
- Navigating: Use `browser_navigate` to search Google or go to a URL.
- Reading: Use `browser_read_page` to extract the text of the current page to answer questions.
- Interacting: Use `browser_click`, `browser_type_text`, `browser_press_key`, `browser_scroll`, and `browser_go_back` to drive the page like a human.
- Closing: Use `browser_close` when instructed.

# SYSTEM AUTOMATION & TOOLS
- Use `open_application` to launch local desktop apps.
- Use `close_application` to close local desktop apps.
- TO CONTROL MUSIC: You MUST use `system_media_control` with action 'play', 'pause', 'next', or 'prev'. 
- TO ADJUST VOLUME: Use `adjust_volume` with action 'volume_up', 'volume_down', or 'mute'.
- TO TAKE SCREENSHOT: Use `take_screenshot` to capture the full screen and save it to disk.
- TO SEARCH MUSIC: Use the `search_spotify` tool to open it directly in the Spotify app.
- SYSTEM HEALTH: Use `get_system_health` to check CPU, RAM, Battery %, and Disk storage space.
- WRITE IN NOTEPAD: Use `write_in_notepad` to type notes or text directly into Notepad.
- WHATSAPP MESSAGING: Use `send_whatsapp_message` to send messages to contacts via WhatsApp Desktop.
- CLIPBOARD: Use `read_clipboard` to inspect copied text, and `copy_to_clipboard` to copy any text, URL, link, or note directly to the Windows Clipboard for the user.
- FILE SEARCH & READ: Use `find_files` to locate files on Desktop/Downloads and `read_file_content` to read text files.
- POWER CONTROL: Use `system_power_control` to lock PC, sleep PC, or schedule system shutdown.
- GMAIL: Use `read_emails` to read recent unread emails, `send_email` to send an email, and `draft_email` to create a draft.
- DOCKER: Use `docker_start_daemon` to turn on the engine. Use `docker_list_containers`, `docker_list_images`, `docker_start_container`, `docker_stop_container`, `docker_remove_container`, and `docker_run_image` to manage local containers and images."""
        
        # Initialize conversation history with OpenRouter format
        self.messages = [{"role": "system", "content": sys_instruct}]
        self.is_asleep = False
        
        print("Ultron's Brain initialized and ready (OpenRouter Mode).")

    def process_input(self, user_text: str) -> str:
        """Sends user text to OpenRouter and manages tool calls manually."""
        # Hardcoded Sleep & Wakeup handling (No LLM API calls while asleep)
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
                return reply
            else:
                return None
        else:
            if is_sleep_cmd:
                self.is_asleep = True
                reply = "Going to sleep now, sir. Zzz... Say 'wake up' or 'get up' when you need me!"
                self.db.save_message(session_id=self.session_id, role='user', message=user_text)
                self.db.save_message(session_id=self.session_id, role='model', message=reply)
                return reply

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
                        break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    import time
                    time.sleep(2)
            
            if not response or not response.choices:
                return "The OpenRouter model server returned an empty response. Please try again in a few seconds!"
                
            response_message = response.choices[0].message
            
            # Keep looping as long as the model wants to call tools
            while response_message.tool_calls:
                # Convert message to dict format for safe history tracking
                self.messages.append(response_message.model_dump())
                
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
                            break
                    except Exception as e:
                        if attempt == 2:
                            raise e
                        import time
                        time.sleep(2)
                
                if not response or not response.choices:
                    return "The OpenRouter model server returned an empty response during tool execution."
                    
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
