import os
import sys
import random
import threading
import itertools
import time
import datetime
import ctypes
import queue
import json
from dotenv import load_dotenv

is_processing = False
stdout_lock = threading.Lock()

def reminder_worker(db, output_manager):
    """Background thread to check for pending reminders and trigger toast & speech."""
    from ultron.plugins.notification_plugin import send_toast
    while True:
        try:
            tasks = db.get_pending_tasks()
            now_iso = datetime.datetime.now().isoformat()
            for task in tasks:
                # Handle old schema (4 columns) or new schema (6 columns)
                if len(task) == 4:
                    task_id, desc, scheduled_for, created = task
                    frequency = None
                    until_date = None
                else:
                    task_id, desc, scheduled_for, created, frequency, until_date = task
                    
                if scheduled_for and scheduled_for <= now_iso:
                    # Trigger visual toast
                    send_toast("Ultron Reminder", desc)
                    # Trigger high-priority audio interruption
                    if output_manager:
                        output_manager.enqueue(f"Sir, here is your reminder: {desc}", source="system")
                        
                    # Handle recurrence
                    if frequency:
                        now_dt = datetime.datetime.now()
                        if frequency == 'hourly':
                            next_dt = now_dt + datetime.timedelta(hours=1)
                        elif frequency == 'daily':
                            next_dt = now_dt + datetime.timedelta(days=1)
                        elif frequency == 'weekly':
                            next_dt = now_dt + datetime.timedelta(weeks=1)
                        elif frequency == 'monthly':
                            next_dt = now_dt + datetime.timedelta(days=30)
                        else:
                            next_dt = now_dt + datetime.timedelta(days=1) # Default to daily if unrecognized
                            
                        next_iso = next_dt.isoformat()
                        if until_date and next_iso > until_date:
                            db.delete_task(task_id)
                        else:
                            db.update_task_time(task_id, next_iso)
                    else:
                        db.delete_task(task_id)
        except Exception as e:
            print(f"[Reminder Error] {e}")
        time.sleep(15) # Check every 15 seconds

# Ensure we can import from the ultron package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.brain import Brain
from ultron.speaker import VoiceSpeaker
from ultron.listener import VoiceListener
from ultron.cron_manager import CronManager
from ultron.output_manager import OutputManager

def handle_user_query(user_text, brain, output_manager, was_queued=False):
    """Common handler for processing user queries from keyboard or microphone."""
    global is_processing
    if not user_text or not user_text.strip():
        return

    # If Ultron is asleep and command is not a wake command, stay completely silent
    if brain.is_asleep:
        text_lower = user_text.lower().strip()
        wake_phrases = ["wake up", "get up"]
        wake_words = {"wakeup", "getup", "wake"}
        words = set(text_lower.split())
        is_wake_cmd = any(p in text_lower for p in wake_phrases) or bool(words.intersection(wake_words))
        if not is_wake_cmd:
            return

    if was_queued:
        LOADING_MESSAGES = [
            "Now for your next request...",
            "Moving on to the next one...",
            "Checking that next...",
            "Let me check that too...",
            "Working on your other request..."
        ]
    else:
        LOADING_MESSAGES = [
            "Hmm, let me see...",
            "Just a second, sir...",
            "Looking into that...",
            "Right away, sir...",
            "Let me check...",
            "Working on it, sir..."
        ]
        
    msg = random.choice(LOADING_MESSAGES)
    is_processing = True

    # Print and speak loading phrase
    with stdout_lock:
        sys.stdout.write(f"\rUltron: {msg}\n")
        sys.stdout.flush()
    output_manager.enqueue(msg, source="system", print_msg=False)

    try:
        response = brain.process_input(user_text)
    finally:
        is_processing = False

    if response:
        with stdout_lock:
            print(f"Ultron: {response}")
        output_manager.enqueue(response, source="user", print_msg=False)

def main():
    print("Initializing Ultron Desktop Assistant...")
    
    # Load environment variables from .env file
    load_dotenv()
    
    try:
        # Initialize Brain, Voice Speaker, and OutputManager
        brain = Brain()
        speaker = VoiceSpeaker(voice_name="en_US-bryce-medium")
        output_manager = OutputManager(speaker)
        brain.output_manager = output_manager
        
        command_queue = queue.Queue()

        # Load Settings for microphone control
        settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
        settings = {}
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        mic_active = settings.get("microphone_active", True)

        listener = None
        if mic_active:
            # Callback function triggered when voice is detected by microphone
            def on_voice_detected(spoken_text):
                global is_processing
                was_queued = is_processing
                if was_queued:
                    with stdout_lock:
                        sys.stdout.write(f"\r[Queued Voice Task: \"{spoken_text}\"]\n")
                        sys.stdout.flush()
                else:
                    output_manager.interrupt()
                command_queue.put(('voice', spoken_text, was_queued))
                
            listener = VoiceListener(callback_func=on_voice_detected)
            listener.start_listening()

        # Start the background reminder thread
        reminder_thread = threading.Thread(target=reminder_worker, args=(brain.db, output_manager), daemon=True)
        reminder_thread.start()

        # Start the background Cron Manager (with output_manager for queued speech)
        cron_manager = CronManager(brain=brain, output_manager=output_manager)
        cron_manager.start()

        # Start background keyboard reader thread
        def keyboard_worker():
            global is_processing
            while True:
                try:
                    text = sys.stdin.readline()
                    if text:
                        text_str = text.strip()
                        if text_str:
                            was_queued = is_processing
                            if was_queued:
                                with stdout_lock:
                                    sys.stdout.write(f"\r[Queued Keyboard Task: \"{text_str}\"]\n")
                                    sys.stdout.flush()
                            else:
                                output_manager.interrupt()
                            command_queue.put(('keyboard', text_str, was_queued))
                except Exception:
                    break

        kb_thread = threading.Thread(target=keyboard_worker, daemon=True)
        kb_thread.start()

    except ValueError as e:
        print(f"\n[ERROR] {e}")
        print("Please check your .env and settings.json files for correct API configuration.")
        sys.exit(1)
        
    if mic_active:
        print("\nUltron is online. Continuous background microphone active. Type 'exit' or 'quit' to stop.")
    else:
        print("\nUltron is online. Background microphone is inactive (keyboard input only). Type 'exit' or 'quit' to stop.")
    print("-" * 50)
    
    welcome_msg = "Hello, Ultron welcomes you sir"
    output_manager.enqueue(welcome_msg, source="system")
    time.sleep(0.5)  # Let the welcome message print before showing the "You:" prompt
    
    while True:
        try:
            with stdout_lock:
                sys.stdout.write("\nYou: ")
                sys.stdout.flush()
            
            queue_item = command_queue.get()
            if len(queue_item) == 3:
                source, user_input, was_queued = queue_item
            else:
                source, user_input = queue_item
                was_queued = False
            
            if user_input.lower().strip() in ['exit', 'quit']:
                with stdout_lock:
                    print("\nUltron: Goodbye! Shutting down.")
                break
                
            if source == 'voice':
                with stdout_lock:
                    sys.stdout.write(f"\rYou (Voice): {user_input}\n")
                    sys.stdout.flush()
            
            handle_user_query(user_input, brain, output_manager, was_queued)
            
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
        if 'output_manager' in locals():
            output_manager.stop()
    except Exception:
        pass

if __name__ == "__main__":
    main()
