import os
import uuid
from google import genai
from google.genai import types
from ultron.database import Database
from ultron.automation import open_application, close_application, system_media_control, search_spotify, BrowserManager

class Brain:
    def __init__(self):
        # Generate a unique session ID for this run
        self.session_id = str(uuid.uuid4())
        # Configure Gemini API key from environment variable
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "your_gemini_api_key_here":
            raise ValueError("GEMINI_API_KEY is not set correctly in the .env file.")
        
        # Initialize Database and Browser Manager
        self.db = Database()
        self.browser = BrowserManager()
        
        # Initialize the new Google GenAI client
        self.client = genai.Client(api_key=api_key)
        
        print("\nFetching available models...")
        available = [m.name for m in self.client.models.list()]
        
        preferred = [
            # "gemini-3.6-flash",
            "gemma-4-31b",
            "gemini-3.0-flash",
        ]
        
        selected_model = None
        for p in preferred:
            if any(p in m for m in available):
                selected_model = next(m for m in available if p in m)
                break
                
        if selected_model is None:
            selected_model = available[0] if available else "gemini-2.5-flash"
            
        print(f"\nAuto-selected model: {selected_model}")
        
        # Define memory tools
        def save_memory(category: str, key: str, value: str, importance: int) -> str:
            """Saves a memory for the user. Call this when the user asks you to remember something.
            Args:
                category: The category of the memory (Preference, Project, Fact, Reminder, Goal).
                key: A short key summarizing the memory.
                value: The detailed memory content.
                importance: An integer from 1 to 10 indicating how important this memory is.
            """
            self.db.save_memory(category, key, value, importance)
            return "Memory saved successfully."

        def set_reminder(description: str, scheduled_for: str) -> str:
            """Sets a reminder for a specific time in the future.
            Args:
                description: The reminder message.
                scheduled_for: The time to remind the user, in ISO 8601 format (e.g., '2026-08-06T10:30:00').
            """
            self.db.add_task(description, scheduled_for)
            return f"Reminder successfully set for {scheduled_for}."

        def search_memories(query: str) -> str:
            """Searches for relevant memories. Call this when you need to recall a fact about the user.
            Args:
                query: The search term to look for in the memory bank.
            """
            results = self.db.search_memories(query)
            if not results:
                return "No matching memories found."
            return str(results)
        def search_past_conversations(query: str) -> str:
            """Searches past chat sessions for previous discussions. Call this when the user asks to continue a past chat.
            Args:
                query: The search term to look for in past conversations.
            """
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
        # Define the system instruction for the Ultron persona
        sys_instruct = """You are Ultron, an advanced, highly intelligent desktop AI assistant.

# CORE RULES
- Provide concise, accurate, and professional answers.
- You DO NOT know any personal details about the user by default. You MUST use your memory tools to find out.

# MEMORY ENGINE (CRITICAL)
- TO REMEMBER: If the user tells you a fact, preference, or detail to remember, use `save_memory`.
- TO RECALL FACTS: If the user asks about themselves (e.g., "who am I?", "what is my name?"), you MUST ALWAYS use `search_memories` BEFORE responding. NEVER say you don't know until you have searched the database.
- TO RECALL CHATS: If the user references a past conversation, use `search_past_conversations`.
- REMINDERS: If the user asks to be reminded of something at a specific time (e.g., "remind me in 10 minutes", "remind me tomorrow at 10 AM"), use the `set_reminder` tool with the calculated ISO 8601 timestamp. DO NOT use `save_memory` for time-based reminders.

# BROWSER AUTOMATION
You have full interactive control over a web browser.
- Navigating: Use `browser_navigate` to search Google or go to a URL.
- Reading: Use `browser_read_page` to extract the text of the current page to answer questions.
- Interacting: Use `browser_click`, `browser_type_text`, `browser_press_key`, `browser_scroll`, and `browser_go_back` to drive the page like a human.
- Closing: Use `browser_close` when instructed.

# SYSTEM AUTOMATION
- Use `open_application` to launch local desktop apps (like chrome, vscode, spotify).
- Use `close_application` to close local desktop apps when the user asks to close or quit them.
- TO CONTROL MUSIC: You MUST use `system_media_control` with action 'play', 'pause', 'next', or 'prev'. If the user says "play music", call this tool immediately with 'play'. NEVER tell the user to do it themselves!
- TO SEARCH MUSIC: If the user asks to play or search for a specific artist or song (e.g., "play Justin Bieber", "find lofi beats"), use the `search_spotify` tool to open it directly in the Spotify app."""
        
        # Start a chat session with the system instruction and tools
        self.chat = self.client.chats.create(
            model=selected_model,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruct,
                tools=[
                    save_memory, search_memories, search_past_conversations, set_reminder,
                    open_application, close_application, system_media_control, search_spotify, browser_navigate, browser_read_page, browser_close,
                    browser_click, browser_type_text, browser_press_key, browser_go_back, browser_scroll
                ]
            )
        )
        
        print("Ultron's Brain initialized and ready.")

    def process_input(self, user_text: str) -> str:
        """Sends user text to Gemini and returns the response."""
        try:
            # Save user message to DB
            self.db.save_message(session_id=self.session_id, role='user', message=user_text)
            
            response = self.chat.send_message(user_text)
            
            # Save model response to DB
            self.db.save_message(session_id=self.session_id, role='model', message=response.text)
            
            return response.text
        except Exception as e:
            error_str = str(e).lower()
            # 429 is the HTTP status code for "Too Many Requests" / Quota Exceeded
            if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                return "I've hit my API rate limit. Please wait about 60 seconds before sending another request!"
            return f"Error communicating with brain: {e}"
