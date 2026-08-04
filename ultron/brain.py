import os
import uuid
from google import genai
from google.genai import types
from ultron.database import Database

class Brain:
    def __init__(self):
        # Generate a unique session ID for this run
        self.session_id = str(uuid.uuid4())
        # Configure Gemini API key from environment variable
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "your_gemini_api_key_here":
            raise ValueError("GEMINI_API_KEY is not set correctly in the .env file.")
        
        # Initialize Database
        self.db = Database()
        
        # Initialize the new Google GenAI client
        self.client = genai.Client(api_key=api_key)
        
        print("\nFetching available models...")
        available = [m.name for m in self.client.models.list()]
        
        preferred = [
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
        
        # Define the system instruction for the Ultron persona
        sys_instruct = (
            "You are Ultron, an advanced, highly intelligent, and helpful desktop assistant. "
            "You provide concise, accurate, and professional answers."
        )
        
        # Start a chat session with the system instruction
        self.chat = self.client.chats.create(
            model=selected_model,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruct
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
