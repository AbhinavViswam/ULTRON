import os
import sys
import random
import threading
import itertools
import time
import datetime
import ctypes
import queue
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
                    # Delete the task from the database so it doesn't clutter
                    db.delete_task(task_id)
                    # Show native Windows popup (0x40 = Info icon, 0x40000 = Topmost)
                    ctypes.windll.user32.MessageBoxW(0, f"Reminder:\n\n{desc}", "Ultron Alert", 0x40 | 0x40000)
        except Exception:
            pass
        time.sleep(15) # Check every 15 seconds

# Ensure we can import from the ultron package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.brain import Brain
from ultron.speaker import VoiceSpeaker
from ultron.listener import VoiceListener

def handle_user_query(user_text, brain, speaker):
    """Common handler for processing user queries from keyboard or microphone."""
    if not user_text or not user_text.strip():
        return

    # Immediately stop speaking if Ultron is currently talking
    speaker.stop()

    # If Ultron is asleep and command is not a wake command, stay completely silent
    if brain.is_asleep:
        text_lower = user_text.lower().strip()
        wake_phrases = ["wake up", "get up"]
        wake_words = {"wakeup", "getup", "wake"}
        words = set(text_lower.split())
        is_wake_cmd = any(p in text_lower for p in wake_phrases) or bool(words.intersection(wake_words))
        if not is_wake_cmd:
            return

    LOADING_MESSAGES = [
        "Let me look into it, sir...",
        "Give me a minute, sir...",
        "Give me a second, sir...",
        "I am working on it, sir...",
        "Please wait, sir...",
        "I will let you know, sir...",
        "Let me gather the information, sir..."
    ]
    msg = random.choice(LOADING_MESSAGES)
    done = False

    # Print and speak loading phrase
    sys.stdout.write(f"\rUltron: {msg}  ")
    sys.stdout.flush()
    speaker.speak_async(msg)

    def spin():
        spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
        while not done:
            sys.stdout.write(f"\b{next(spinner)}")
            sys.stdout.flush()
            time.sleep(0.1)

    spinner_thread = threading.Thread(target=spin)
    spinner_thread.start()

    try:
        response = brain.process_input(user_text)
    finally:
        done = True
        spinner_thread.join()
        clear_len = len(msg) + 12
        sys.stdout.write('\b' * clear_len + ' ' * clear_len + '\b' * clear_len)
        sys.stdout.flush()

    if response:
        print(f"\nUltron: {response}")
        speaker.speak_async(response)

def main():
    print("Initializing Ultron Desktop Assistant...")
    
    # Load environment variables from .env file
    load_dotenv()
    
    try:
        # Initialize Brain, Voice Speaker, and Background Voice Listener
        brain = Brain()
        speaker = VoiceSpeaker(voice_name="en_US-bryce-medium")
        
        command_queue = queue.Queue()

        # Callback function triggered when voice is detected by microphone
        def on_voice_detected(spoken_text):
            command_queue.put(('voice', spoken_text))
            
        listener = VoiceListener(callback_func=on_voice_detected)
        listener.start_listening()

        # Start the background reminder thread
        reminder_thread = threading.Thread(target=reminder_worker, args=(brain.db,), daemon=True)
        reminder_thread.start()

        # Start background keyboard reader thread
        def keyboard_worker():
            while True:
                try:
                    text = sys.stdin.readline()
                    if text:
                        text_str = text.strip()
                        if text_str:
                            command_queue.put(('keyboard', text_str))
                except Exception:
                    break

        kb_thread = threading.Thread(target=keyboard_worker, daemon=True)
        kb_thread.start()

    except ValueError as e:
        print(f"\n[ERROR] {e}")
        print("Please open the '.env' file and add your OpenRouter API_KEY.")
        sys.exit(1)
        
    print("\nUltron is online. Continuous background microphone active. Type 'exit' or 'quit' to stop.")
    print("-" * 50)
    
    welcome_msg = "Hello, welcome sir! How can I assist you today?"
    print(f"\nUltron: {welcome_msg}")
    speaker.speak_async(welcome_msg)
    
    while True:
        try:
            sys.stdout.write("\nYou: ")
            sys.stdout.flush()
            
            source, user_input = command_queue.get()
            
            if user_input.lower().strip() in ['exit', 'quit']:
                print("\nUltron: Goodbye! Shutting down.")
                break
                
            if source == 'voice':
                sys.stdout.write(f"\rYou (Voice): {user_input}\n")
                sys.stdout.flush()
            
            handle_user_query(user_input, brain, speaker)
            
        except KeyboardInterrupt:
            print("\n\nUltron: Interrupted by user. Goodbye!")
            break
        except Exception as e:
            print(f"\n[An error occurred]: {e}")

    # Ensure Playwright & Listener clean up cleanly on exit
    try:
        if 'listener' in locals() and listener:
            listener.stop()
        if 'brain' in locals() and hasattr(brain, 'browser') and brain.browser:
            brain.browser.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()
