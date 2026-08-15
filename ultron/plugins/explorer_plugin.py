"""Reading what the user is currently looking at in File Explorer.

"this file" and "this folder" are the most natural things to say and the
hardest for an assistant to resolve — the alternative is guessing a path,
which is how you end up opening the wrong folder. Explorer exposes both
through COM, so they can be answered exactly rather than inferred.
"""

import contextlib
import ctypes
import os
import urllib.parse
import urllib.request

import pythoncom
import win32com.client


@contextlib.contextmanager
def _com():
    """Initialises COM for whichever thread this is running on.

    Tools run on the timeout watchdog's worker thread, and COM is per-thread:
    without this, every call here fails with "CoInitialize has not been
    called" — even though the same code works when tried from the main
    thread.
    """
    initialised = False
    try:
        pythoncom.CoInitialize()
        initialised = True
    except pythoncom.com_error:
        # Already initialised on this thread, possibly under a different
        # threading model. Use it as it is and leave it alone afterwards.
        pass
    try:
        yield
    finally:
        if initialised:
            with contextlib.suppress(Exception):
                pythoncom.CoUninitialize()


def _is_explorer(window) -> bool:
    """True for a File Explorer window, in any Windows language.

    Matching on window.Name ("File Explorer") breaks on a localised Windows
    and silently reports nothing selected, so the hosting executable is used
    instead. Internet Explorer shares this collection and must be excluded.
    """
    try:
        return os.path.basename(str(window.FullName or "")).lower() == "explorer.exe"
    except Exception:
        return False


def _folder_of(window) -> str:
    """The filesystem path a window is showing, or '' for anything else.

    Explorer reports its location as a file:// URL. Special locations — This
    PC, Recycle Bin, a network place — have no filesystem path at all, and
    must come back empty rather than as something that looks like a path.
    """
    try:
        url = str(window.LocationURL or "")
    except Exception:
        return ""
    if not url.lower().startswith("file:"):
        return ""
    path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
    return path if os.path.isdir(path) else ""


def explorer_state() -> list:
    """What each open Explorer window is showing, the one in front first.

    Returns plain dicts rather than COM objects: those are only valid while
    COM stays initialised on this thread, so nothing live may escape the
    block below.

    With several windows open, "this folder" means the one being looked at,
    so the foreground window has to win rather than whichever COM happens to
    enumerate first.
    """
    windows = []
    with _com():
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            candidates = [w for w in shell.Windows() if _is_explorer(w)]
        except Exception as e:
            print(f"[Explorer] could not enumerate windows: {e}")
            return []

        try:
            foreground = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            foreground = 0

        for window in candidates:
            try:
                handle = int(window.HWND)
            except Exception:
                handle = 0
            try:
                items = window.Document.SelectedItems()
                selection = [items.Item(i).Path for i in range(items.Count)]
            except Exception as e:
                print(f"[Explorer] could not read the selection: {e}")
                selection = []
            windows.append({
                "folder": _folder_of(window),
                "selection": selection,
                "in_front": handle != 0 and handle == foreground,
            })

    windows.sort(key=lambda w: 0 if w["in_front"] else 1)
    return windows


def get_selected_file_in_explorer() -> list:
    """Gets a list of absolute file paths that are currently selected in Windows File Explorer.
    Use this when the user refers to 'the selected file', 'these files', or 'this file'.
    """
    for window in explorer_state():
        if window["selection"]:
            return window["selection"]
    return []


def get_current_explorer_folder() -> str:
    """Gets the folder path currently open in Windows File Explorer.

    Use this whenever the user says 'this folder', 'here', 'the folder I am
    in', or 'the current folder' — it tells you exactly where they are
    looking, so you never have to guess a path. Combine it with
    list_directory to see what is there, or with read_file_content to read
    something from it.
    """
    windows = explorer_state()
    if not windows:
        return ("No File Explorer window is open, sir. Ask the user which "
                "folder they mean, or have them open it in Explorer.")

    for window in windows:
        if window["folder"]:
            return window["folder"]

    return ("File Explorer is open but not showing a folder on disk — it may "
            "be on This PC, the Recycle Bin, or a network location. Ask the "
            "user which folder they mean.")


def list_current_explorer_folder() -> str:
    """Lists the files and subfolders in the folder currently open in File Explorer.

    Use this for 'what is in this folder', 'what am I looking at', or before
    reading a file the user referred to only as 'the file here'.
    """
    folder = get_current_explorer_folder()
    if not os.path.isdir(folder):
        return folder  # already an explanation of why there is no folder

    try:
        entries = sorted(os.scandir(folder), key=lambda e: (e.is_file(), e.name.lower()))
    except OSError as e:
        return f"Could not read '{folder}': {e}"

    if not entries:
        return f"'{folder}' is empty, sir."

    lines = []
    for entry in entries[:100]:
        try:
            if entry.is_dir():
                lines.append(f"  [folder] {entry.name}")
            else:
                size_kb = max(1, entry.stat().st_size // 1024)
                lines.append(f"  {entry.name} ({size_kb} KB)")
        except OSError:
            lines.append(f"  {entry.name} (unreadable)")

    more = f"\n  … and {len(entries) - 100} more" if len(entries) > 100 else ""
    return f"'{folder}' contains {len(entries)} item(s):\n" + "\n".join(lines) + more
