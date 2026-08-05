import os
import uuid
from google import genai
from google.genai import types
from ultron.database import Database
from ultron.automation import open_application, BrowserManager

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
            "gemini-3.6-flash",
            "gemini-3.5-flash",
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
        sys_instruct = (
            "You are Ultron, an advanced, highly intelligent, and helpful desktop assistant. "
            "You provide concise, accurate, and professional answers. "
            "You have access to a long-term Memory Engine. If the user asks you to remember something, "
            "use the save_memory tool. If the user asks you a question that requires recalling past facts, "
            "preferences, or details, proactively use the search_memories tool to retrieve the information before answering. "
            "If the user asks to continue a past conversation or references a previous chat (e.g., 'our Docker chat'), "
            "use the search_past_conversations tool to fetch the relevant context before responding. "
            "You also have system automation capabilities: "
            "1. If the user asks you to open an application (e.g., 'open chrome', 'launch vscode'), use the open_application tool. "
            "2. If the user asks you to search the web, visit a webpage, or find information online, use the browser_navigate tool. "
            "3. Once on a webpage, you can interact with it! Use browser_click to click links/buttons, browser_type_text to fill out forms/search bars, browser_press_key to hit Enter, browser_scroll to view more, and browser_go_back to return. "
            "4. If you need to answer a question based on the webpage, use the browser_read_page tool to extract its text. "
            "5. If the user explicitly asks you to close the browser, use the browser_close tool."
        )
        
        # Start a chat session with the system instruction and tools
        self.chat = self.client.chats.create(
            model=selected_model,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruct,
                tools=[
                    save_memory, search_memories, search_past_conversations,
                    open_application, browser_navigate, browser_read_page, browser_close,
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
            return f"Error communicating with brain: {e}"
