"""Creates Windows shortcuts so Ultron runs without a terminal.

    python install_shortcuts.py            desktop shortcut
    python install_shortcuts.py --startup  also start with Windows
    python install_shortcuts.py --remove   remove both

The shortcuts point at this virtual environment's pythonw.exe, so nothing has
to be activated first and no console window appears.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.launcher import (
    SHORTCUT_NAME, create_shortcut, desktop_dir, launch_target,
    set_startup, startup_dir,
)


def main():
    args = set(sys.argv[1:])

    if "--remove" in args:
        for directory in (desktop_dir(), startup_dir()):
            path = os.path.join(directory, SHORTCUT_NAME)
            if os.path.exists(path):
                os.remove(path)
                print(f"Removed {path}")
            else:
                print(f"Nothing at {path}")
        return 0

    target, arguments = launch_target()
    if not os.path.exists(target):
        print(f"[ERROR] Could not find the interpreter at {target}")
        return 1

    print(f"Launching via: {target} {arguments}")
    print(f"Created {create_shortcut(desktop_dir())}")

    if "--startup" in args:
        print(set_startup(True))
        print(f"Created {os.path.join(startup_dir(), SHORTCUT_NAME)}")
    else:
        print("Tip: pass --startup to also launch Ultron when Windows starts.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
