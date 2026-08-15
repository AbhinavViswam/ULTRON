"""Console entry point for Ultron.

A thin shell over UltronCore: reads lines from stdin, prints what comes back.
For the desktop overlay, run gui.py instead.
"""

import os
import sys
import threading

# Ensure we can import from the ultron package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.config import config
from ultron.core import UltronCore
from ultron.launcher import make_output_safe

# Before anything prints: piping this console into a file drops stdout to the
# locale encoding, and one emoji in a search result would otherwise take down
# the turn that fetched it.
make_output_safe()

stdout_lock = threading.Lock()


def main():
    print("Initializing Ultron Desktop Assistant...")

    # Fail early and legibly on incomplete configuration. The GUI calls the
    # same check and shows its settings screen instead of exiting.
    problems = config.missing_requirements()
    if problems:
        print("\n[SETUP REQUIRED]")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)

    try:
        core = UltronCore(echo_to_console=True)
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        print("Please check your settings.json file for correct API configuration.")
        sys.exit(1)

    # Voice input needs echoing back; typed input is already on screen. Either
    # kind is flagged when it had to queue behind a turn in progress.
    def show_user_message(text, origin, queued):
        if origin != "voice" and not queued:
            return
        label = "You (Voice)" if origin == "voice" else "You"
        suffix = "  [queued]" if queued else ""
        with stdout_lock:
            sys.stdout.write(f"\r{label}: {text}{suffix}\n")
            sys.stdout.flush()

    core.on_user_message(show_user_message)

    # A destructive tool blocks its worker until a human answers. The console
    # already owns stdin, so the question is posted here and the next typed
    # line is read as the answer instead of being sent to Ultron.
    pending_confirmation = {"decide": None}

    def ask_confirmation(question, decide):
        pending_confirmation["decide"] = decide
        with stdout_lock:
            sys.stdout.write(f"\n\n[CONFIRM] Ultron wants to {question}.\n")
            sys.stdout.write("Type 'yes' to allow, anything else to refuse.\n")
            sys.stdout.flush()

    core.on_confirmation_request(ask_confirmation)
    core.start()

    if core.microphone_active:
        print("\nUltron is online. Continuous background microphone active. Type 'exit' or 'quit' to stop.")
    else:
        print("\nUltron is online. Background microphone is inactive (keyboard input only). Type 'exit' or 'quit' to stop.")
    print("-" * 50)

    try:
        while True:
            with stdout_lock:
                sys.stdout.write("\nYou: ")
                sys.stdout.flush()

            line = sys.stdin.readline()
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            if text.lower() in ("exit", "quit"):
                with stdout_lock:
                    print("\nUltron: Goodbye! Shutting down.")
                break

            decide = pending_confirmation["decide"]
            if decide is not None:
                pending_confirmation["decide"] = None
                approved = text.lower() in ("yes", "y", "yeah", "yep", "confirm", "do it")
                with stdout_lock:
                    print("Approved." if approved else "Refused — nothing was changed.")
                decide(approved)
                continue

            core.submit(text, origin="keyboard")
    except KeyboardInterrupt:
        print("\n\nUltron: Interrupted by user. Goodbye!")
    except Exception as e:
        print(f"\n[An error occurred]: {e}")
    finally:
        core.shutdown()


if __name__ == "__main__":
    main()
