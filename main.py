import os
import sys
from dotenv import load_dotenv

# Ensure we can import from the ultron package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.brain import Brain

def main():
    print("Initializing Ultron Desktop Assistant...")
    
    # Load environment variables from .env file
    load_dotenv()
    
    try:
        # Initialize Brain (this will check for API key and set up Gemini)
        brain = Brain()
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        print("Please open the '.env' file and add your GEMINI_API_KEY.")
        sys.exit(1)
        
    print("\nUltron is online. Type 'exit' or 'quit' to stop.")
    print("-" * 50)
    
    while True:
        try:
            user_input = input("\nYou: ")
            
            if user_input.lower().strip() in ['exit', 'quit']:
                print("\nUltron: Goodbye! Shutting down.")
                break
            
            if not user_input.strip():
                continue
                
            response = brain.process_input(user_input)
            print(f"\nUltron: {response}")
            
        except KeyboardInterrupt:
            print("\n\nUltron: Interrupted by user. Goodbye!")
            break
        except Exception as e:
            print(f"\n[An error occurred]: {e}")

if __name__ == "__main__":
    main()
