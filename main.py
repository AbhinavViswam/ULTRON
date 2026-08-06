import os
import sys
import random
import threading
import itertools
import time
import datetime
import ctypes
from dotenv import load_dotenv

def reminder_worker(db):
    """Background thread to check for pending reminders and show a popup."""
    while True:
        try:
            tasks = db.get_pending_tasks()
            now_iso = datetime.datetime.now().isoformat()
            for task in tasks:
                task_id, desc, scheduled_for, created = task
                if scheduled_for and scheduled_for <= now_iso:
                    # Update status first so it doesn't fire multiple times
                    db.update_task_status(task_id, 'completed')
                    # Show native Windows popup (0x40 = Info icon, 0x40000 = Topmost)
                    ctypes.windll.user32.MessageBoxW(0, f"Reminder:\n\n{desc}", "Ultron Alert", 0x40 | 0x40000)
        except Exception:
            pass
        time.sleep(15) # Check every 15 seconds

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
        
        # Start the background reminder thread
        reminder_thread = threading.Thread(target=reminder_worker, args=(brain.db,), daemon=True)
        reminder_thread.start()
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
            
            # Print the prefix exactly once
            sys.stdout.write(f"\rUltron: {msg}  ")
            sys.stdout.flush()
            
            def spin():
                spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
                while not done:
                    # Backspace 1 char to overwrite just the spinner
                    sys.stdout.write(f"\b{next(spinner)}")
                    sys.stdout.flush()
                    time.sleep(0.1)
                    
            spinner_thread = threading.Thread(target=spin)
            spinner_thread.start()
            
            try:
                response = brain.process_input(user_input)
            finally:
                done = True
                spinner_thread.join()
                # Safely clear the line using backspaces instead of \r
                clear_len = len(msg) + 12
                sys.stdout.write('\b' * clear_len + ' ' * clear_len + '\b' * clear_len)
                sys.stdout.flush()
            print(f"Ultron: {response}")
            
        except KeyboardInterrupt:
            print("\n\nUltron: Interrupted by user. Goodbye!")
            break
        except Exception as e:
            print(f"\n[An error occurred]: {e}")

    # Ensure Playwright browser cleans up cleanly so Node.js doesn't throw EPIPE
    try:
        if 'brain' in locals() and hasattr(brain, 'browser') and brain.browser:
            brain.browser.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()
