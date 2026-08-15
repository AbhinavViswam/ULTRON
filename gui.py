"""Desktop overlay entry point for Ultron.

Run `python gui.py` for the floating orb, or `python main.py` for the console
version. Both drive the same UltronCore.

To run it without a terminal at all, double-click the desktop shortcut created
by `python install_shortcuts.py`, which launches this through pythonw.exe.
"""

import os
import sys

# Ensure we can import from the ultron package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Must happen before anything that prints is imported: launched from a
# shortcut there is no console, and an unguarded print would raise.
from ultron.launcher import redirect_output_if_headless

_LOG_PATH = redirect_output_if_headless()

from PySide6.QtWidgets import QApplication

from ultron.launcher import claim_single_instance, serve_summons
from ultron.ui import theme
from ultron.ui.overlay import OrbOverlay


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ultron")
    app.setWindowIcon(theme.make_app_icon())

    # Hiding the orb must not count as the last window closing, or the process
    # would exit the moment it is dismissed to the tray.
    app.setQuitOnLastWindowClosed(False)

    # Claimed before anything is built: a second launch must bow out before it
    # opens a microphone or binds the agent monitor's port.
    server = claim_single_instance()
    if server is None:
        print("Ultron is already running; asked the existing instance to show.")
        return 0

    if _LOG_PATH:
        print(f"--- Ultron started (no console; logging to {_LOG_PATH}) ---")

    overlay = OrbOverlay()
    overlay.single_instance_server = server  # keep it alive for the session
    serve_summons(server, overlay.summon)

    overlay.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
