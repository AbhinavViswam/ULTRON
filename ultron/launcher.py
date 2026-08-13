"""Running Ultron without a terminal.

Covers the three things needed to treat this as a normal desktop app rather
than a script: somewhere for output to go when there is no console, Windows
shortcuts (desktop and Startup), and a guard against a second copy starting.
"""

import os
import sys

from ultron.config import PROJECT_ROOT

LOG_PATH = os.path.join(PROJECT_ROOT, "data", "ultron.log")
# Trim the log once it passes this, so an assistant left running for weeks
# cannot quietly fill the disk.
MAX_LOG_BYTES = 2_000_000

SHORTCUT_NAME = "Ultron.lnk"
ICON_PATH = os.path.join(PROJECT_ROOT, "resources", "ultron.ico")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def redirect_output_if_headless():
    """Sends stdout/stderr to a log file when launched without a console.

    pythonw.exe leaves sys.stdout and sys.stderr as None. Python discards
    prints in that state rather than failing, which is worse than a crash
    here: this codebase reports errors from the brain, the speaker and several
    background threads by printing, and with no console those reports would
    vanish. Redirecting to a file is the only way to diagnose a shortcut
    launch, so this runs before anything that prints is imported.

    Returns the log path if redirected, otherwise None.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    try:
        if os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".old")
    except OSError:
        pass

    handle = open(LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = handle
    sys.stderr = handle
    return LOG_PATH


# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------

def launch_target():
    """The (executable, arguments) pair a shortcut should point at.

    Uses pythonw.exe from whichever interpreter is running, so the shortcut
    inherits this virtual environment without needing it activated.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""

    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return pythonw, f'"{os.path.join(PROJECT_ROOT, "gui.py")}"'


def _special_folder(name: str, fallback: str) -> str:
    """Resolves a Windows shell folder, honouring any redirection.

    OneDrive's "known folder move" repoints Desktop at
    %USERPROFILE%\\OneDrive\\Desktop, so assuming ~/Desktop writes shortcuts
    to a folder the user may never see — and leaves the real one unfindable
    when removing them.
    """
    try:
        import win32com.client

        path = win32com.client.Dispatch("WScript.Shell").SpecialFolders(name)
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return fallback


def startup_dir() -> str:
    return _special_folder("Startup", os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    ))


def desktop_dir() -> str:
    return _special_folder("Desktop", os.path.join(os.path.expanduser("~"), "Desktop"))


def ensure_icon() -> str:
    """Writes the drawn app icon out as a .ico for shortcuts to use.

    The icon is generated in code rather than shipped as a binary, so it has
    to be materialised on disk before Windows can reference it.
    """
    if os.path.exists(ICON_PATH):
        return ICON_PATH
    try:
        from ultron.ui import theme

        os.makedirs(os.path.dirname(ICON_PATH), exist_ok=True)
        pixmap = theme.make_app_icon(256).pixmap(256, 256)
        if pixmap.save(ICON_PATH, "ICO"):
            return ICON_PATH
    except Exception:
        pass
    return ""


def create_shortcut(directory: str) -> str:
    """Creates the Ultron shortcut in `directory` and returns its path."""
    import win32com.client

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, SHORTCUT_NAME)
    target, arguments = launch_target()

    shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path)
    shortcut.TargetPath = target
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = PROJECT_ROOT
    shortcut.Description = "Ultron desktop assistant"
    icon = ensure_icon()
    if icon:
        shortcut.IconLocation = icon
    shortcut.Save()
    return path


def startup_enabled() -> bool:
    return os.path.exists(os.path.join(startup_dir(), SHORTCUT_NAME))


def set_startup(enabled: bool) -> str:
    """Adds or removes Ultron from the Windows Startup folder."""
    path = os.path.join(startup_dir(), SHORTCUT_NAME)
    if enabled:
        create_shortcut(startup_dir())
        return "Ultron will start with Windows."
    if os.path.exists(path):
        os.remove(path)
    return "Ultron will no longer start with Windows."


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------

SERVER_NAME = "ultron-overlay-single-instance"


def claim_single_instance():
    """Returns a QLocalServer if this is the only copy, else None.

    Call this before building anything: a second copy would open its own
    microphone stream and try to bind the agent monitor's port, so it has to
    bow out before any of that starts, not after.

    The returned server must be kept alive for the process's lifetime.
    """
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(300):
        # Someone is already listening — ask them to surface, then step aside.
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return None

    # A crashed instance can leave the socket behind and block every future
    # launch, so clear any stale name before listening.
    QLocalServer.removeServer(SERVER_NAME)

    server = QLocalServer()
    if not server.listen(SERVER_NAME):
        return None
    return server


def serve_summons(server, callback):
    """Runs `callback` whenever another launch asks this copy to show."""

    def handle():
        connection = server.nextPendingConnection()
        if connection:
            connection.readyRead.connect(callback)
            connection.disconnected.connect(connection.deleteLater)

    server.newConnection.connect(handle)
