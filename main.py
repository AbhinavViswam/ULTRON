import os
import sys
import random
import threading
import itertools
import time
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
                
            LOADING_MESSAGES = [
                "Please, let me check first...",
                "Finding the details for you...",
                "Give me a second to process that...",
                "Working on it...",
                "Scanning my databanks...",
                "Let me see what I can find...",
                "Hold on, digging through the web...",
                "Fetching the requested information...",
                "Analyzing your request...",
                "Just a moment...",
                "Looking into it right now...",
                "Gathering the necessary details...",
                "Calculating possibilities...",
                "Let me pull that up for you...",
                "One moment please...",
                "Processing your command...",
                "Checking my memory banks...",
                "Connecting to the mainframe...",
                "Reviewing the data...",
                "Retrieving information..."
            ]
            msg = random.choice(LOADING_MESSAGES)
            done = False
            
            def spin():
                spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
                while not done:
                    sys.stdout.write(f"\rUltron: {msg} {next(spinner)}")
                    sys.stdout.flush()
                    time.sleep(0.1)
                    
            spinner_thread = threading.Thread(target=spin)
            spinner_thread.start()
            
            try:
                response = brain.process_input(user_input)
            finally:
                done = True
                spinner_thread.join()
                sys.stdout.write('\r' + ' ' * (len(msg) + 20) + '\r') # Clear the line
                
            print(f"Ultron: {response}")
            
        except KeyboardInterrupt:
            print("\n\nUltron: Interrupted by user. Goodbye!")
            break
        except Exception as e:
            print(f"\n[An error occurred]: {e}")

if __name__ == "__main__":
    main()
