"""Releases any keyboard modifier or mouse button left stuck down.

Run this when the desktop starts misbehaving — drag-select grabs whole words,
clicks multi-select, Win+V stops opening the clipboard. Those are the symptoms
of a modifier key held down by an automation that failed between pressing and
releasing it.

    python unstick.py

It prints what it found, so you can tell which key was stuck.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.automation import keys_currently_held, release_stuck_keys


def main():
    held = keys_currently_held()
    if held:
        print(f"Stuck: {', '.join(held)}")
    else:
        print("Nothing appears stuck right now.")

    print(release_stuck_keys())

    remaining = keys_currently_held()
    if remaining:
        print(f"\nStill held: {', '.join(remaining)}")
        print("If that is a physical key, tap it once on your keyboard.")
    else:
        print("All modifiers and mouse buttons are now released.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
