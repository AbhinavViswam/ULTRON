import os
import subprocess
import ctypes
import winreg
from ctypes import wintypes
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Locating folders
# ---------------------------------------------------------------------------

# Windows lets users move these, and OneDrive's "known folder move" routinely
# repoints Desktop, Documents and Pictures into the OneDrive tree. Building
# them from the home directory opens an empty leftover folder instead of the
# real one, so the actual location is read from the registry.
_USER_SHELL_FOLDERS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
)

_KNOWN_FOLDER_KEYS = {
    "desktop": "Desktop",
    "documents": "Personal",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "My Pictures",
    "music": "My Music",
    "videos": "My Video",
}

# Directories that are never what a person means and cost a fortune to walk.
_SEARCH_SKIP = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "appdata", "node_modules",
    "__pycache__", "venv", ".venv", ".git", "site-packages", "recovery",
    "perflogs", ".cache", "dist", "build",
}

# Bounds on the folder hunt, so a miss cannot hang the assistant.
_SEARCH_MAX_DEPTH = 5
_SEARCH_MAX_DIRS = 40000
# Wall-clock ceiling. The tool watchdog would abandon a runaway search, but
# answering with what was found beats being cut off with nothing.
_SEARCH_MAX_SECONDS = 5.0
# Enough exact hits to judge whether the request was ambiguous.
_SEARCH_ENOUGH_EXACT = 5


# Words people attach when naming a folder out loud.
_TRAILING_WORDS = ("folder", "folders", "directory", "dir")
_LEADING_WORDS = ("the", "my", "a", "folder", "directory", "dir")


def _clean_folder_name(text: str) -> str:
    """Reduces a spoken phrase to a name: 'the ultron folder' -> 'ultron'."""
    cleaned = (text or "").strip().strip("\"'").strip()
    changed = True
    while changed and cleaned:
        changed = False
        lowered = cleaned.lower()
        for word in _TRAILING_WORDS:
            if lowered.endswith(" " + word):
                cleaned = cleaned[: -(len(word) + 1)].strip()
                changed = True
                break
        lowered = cleaned.lower()
        for word in _LEADING_WORDS:
            if lowered.startswith(word + " "):
                cleaned = cleaned[len(word) + 1:].strip()
                changed = True
                break
    return cleaned


def _looks_like_path(text: str) -> bool:
    """True only for something meant as a path, not a bare folder name.

    A bare name must not be resolved against the working directory: launched
    from its shortcut Ultron runs inside its own project, so 'ultron' would
    quietly resolve to the package folder nested inside it rather than the
    folder the user meant.
    """
    return bool(
        os.path.isabs(text)
        or text.startswith("~")
        or "/" in text
        or "\\" in text
    )


def known_folder(name: str) -> str:
    """Returns the real path of a shell folder like 'desktop', or ''.

    Deliberately duplicated in spirit by launcher.py, which resolves the same
    idea for shortcuts; that module is imported at startup and must not pull
    in this one's heavy dependencies.
    """
    key_name = _KNOWN_FOLDER_KEYS.get(name.strip().lower().rstrip("/\\"))
    if not key_name:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USER_SHELL_FOLDERS) as key:
            value, _ = winreg.QueryValueEx(key, key_name)
        path = os.path.expandvars(value)
        if os.path.isdir(path):
            return path
    except OSError as e:
        print(f"[Folders] could not read '{key_name}' from the registry: {e}")

    fallback = os.path.join(os.path.expanduser("~"), name.capitalize())
    return fallback if os.path.isdir(fallback) else ""


def _search_roots():
    """Where to hunt for a folder given only its name, nearest first."""
    roots = [os.path.expanduser("~")]
    for letter in "CDEFGH":
        drive = f"{letter}:\\"
        if os.path.isdir(drive):
            roots.append(drive)
    return roots


def _prune(matches: list) -> list:
    """Keeps the outermost matches, shallowest first.

    Searching for 'ultron' hits both the project and the package folder nested
    inside it. Offering both as a choice is noise — the enclosing one is what
    was meant, and the other is reachable from it.
    """
    ordered = sorted(set(matches), key=lambda p: (p.count(os.sep), len(p)))
    kept = []
    for path in ordered:
        if not any(path.lower().startswith(k.lower() + os.sep) for k in kept):
            kept.append(path)
    return kept


def find_folders(name: str, limit: int = 8) -> list:
    """Finds directories called `name`, searching the user profile then drives.

    Exact name matches win outright; partial matches are only offered when
    nothing matched exactly. Bounded by depth, directories visited and
    wall-clock, because this runs on the thread serving the user.
    """
    import time

    target = name.strip().strip("\"'").lower()
    if not target:
        return []

    exact, partial = [], []
    seen = set()
    visited = 0
    deadline = time.monotonic() + _SEARCH_MAX_SECONDS

    for root in _search_roots():
        stack = [(root, 0)]
        while stack:
            if time.monotonic() > deadline or visited > _SEARCH_MAX_DIRS:
                stack.clear()
                break

            current, depth = stack.pop()
            if depth > _SEARCH_MAX_DEPTH:
                continue
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            visited += 1

            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue

                lowered = entry.name.lower()
                if lowered in _SEARCH_SKIP or lowered.startswith("$"):
                    continue
                # Hidden and tooling directories are never the answer unless
                # the user named them exactly.
                if lowered.startswith(".") and lowered != target:
                    continue

                resolved = os.path.normpath(entry.path)
                key = resolved.lower()
                if key not in seen:
                    if lowered == target:
                        seen.add(key)
                        exact.append(resolved)
                    elif target in lowered:
                        seen.add(key)
                        partial.append(resolved)

                if len(exact) >= _SEARCH_ENOUGH_EXACT:
                    return _prune(exact)[:limit]
                stack.append((entry.path, depth + 1))

    return _prune(exact or partial)[:limit]

# Comprehensive list of allowed Windows desktop applications
ALLOWED_APPS = {
    # Browsers
    "chrome": "start chrome",
    "edge": "start msedge",
    "brave": "start brave",
    "firefox": "start firefox",
    "opera": "start opera",

    # Office & Productivity
    "excel": "start excel",
    "word": "start winword",
    "powerpoint": "start powerpnt",
    "outlook": "start outlook",
    "onenote": "start onenote",
    "notepad": "notepad",
    "paint": "mspaint",
    "snipping tool": "snippingtool",

    # System & Utilities
    "settings": "start ms-settings:",
    "control panel": "control",
    "calculator": "calc",
    "explorer": "explorer",
    "task manager": "taskmgr",
    "cmd": "start cmd",
    "terminal": "start wt",
    "powershell": "start powershell",
    "device manager": "devmgmt.msc",

    # Media & Communication
    "spotify": "start spotify",
    "whatsapp": "explorer.exe whatsapp:",
    "discord": "start discord",
    "telegram": "start telegram",
    "vlc": "start vlc",
    "zoom": "start zoom",
    "teams": "start ms-teams:",

    # Creative & Dev
    "vscode": "code",
    "antigravity": "start antigravity",
    "docker": "start docker",
    "pg admin": "start pgadmin4",
    "photoshop": "start photoshop",
    "premiere": "start premiere",
    "illustrator": "start illustrator",
    "blender": "start blender",

    # Gaming
    "steam": "start steam",
    "epic games": "start com.epicgames.launcher:",
    "minecraft": "start minecraft"
}

# Process executable names for closing applications
APP_PROCESS_NAMES = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "brave": "brave.exe",
    "firefox": "firefox.exe",
    "opera": "opera.exe",
    
    "excel": "EXCEL.EXE",
    "word": "WINWORD.EXE",
    "powerpoint": "POWERPNT.EXE",
    "outlook": "OUTLOOK.EXE",
    "onenote": "ONENOTE.EXE",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    
    "settings": "SystemSettings.exe",
    "control panel": "control.exe",
    "calculator": "CalculatorApp.exe",
    "task manager": "Taskmgr.exe",
    "cmd": "cmd.exe",
    "terminal": "WindowsTerminal.exe",
    "powershell": "powershell.exe",

    "spotify": "spotify.exe",
    "whatsapp": "WhatsApp.Root.exe",
    "discord": "Discord.exe",
    "telegram": "Telegram.exe",
    "vlc": "vlc.exe",
    "zoom": "Zoom.exe",
    "teams": "ms-teams.exe",

    "vscode": "code.exe",
    "docker": "Docker Desktop.exe",
    "pg admin": "pgadmin4.exe",
    "photoshop": "Photoshop.exe",
    "premiere": "Adobe Premiere Pro.exe",
    "blender": "blender.exe",
    
    "steam": "steam.exe",
    "minecraft": "Minecraft.exe"
}

# Virtual-key codes for every modifier that can be left held down, plus the
# mouse buttons. A stuck modifier is invisible: the desktop simply starts
# misbehaving (drag-select picks whole words, clicks multi-select, Win+V dies)
# with nothing on screen to explain it.
_STICKY_KEYS = {
    "Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12,
    "LeftCtrl": 0xA2, "RightCtrl": 0xA3,
    "LeftShift": 0xA0, "RightShift": 0xA1,
    "LeftAlt": 0xA4, "RightAlt": 0xA5,
    "LeftWin": 0x5B, "RightWin": 0x5C,
}
_KEYEVENTF_KEYUP = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEUP = 0x0040


def keys_currently_held() -> list:
    """Names of modifiers and mouse buttons Windows thinks are down."""
    user32 = ctypes.windll.user32
    held = [name for name, vk in _STICKY_KEYS.items()
            if user32.GetAsyncKeyState(vk) & 0x8000]
    for name, vk in (("LeftMouse", 0x01), ("RightMouse", 0x02), ("MiddleMouse", 0x04)):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            held.append(name)
    return held


def release_stuck_keys() -> str:
    """Releases any modifier key or mouse button left held down.

    Automating a hotkey means pressing a modifier, pressing a key, then
    releasing both. If anything fails in between — the target window steals
    focus, the app is not ready — the release never happens and the key stays
    down for every other program on the machine. This puts it right.
    """
    try:
        user32 = ctypes.windll.user32
        held = keys_currently_held()

        for vk in _STICKY_KEYS.values():
            user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        for flag in (_MOUSEEVENTF_LEFTUP, _MOUSEEVENTF_RIGHTUP, _MOUSEEVENTF_MIDDLEUP):
            user32.mouse_event(flag, 0, 0, 0, 0)

        if not held:
            return "No keys were stuck; released the modifiers anyway."
        return f"Released stuck input: {', '.join(held)}."
    except Exception as e:
        return f"Failed to release keys: {e}"


def _release_quietly():
    """Best-effort cleanup for the finally block of a keyboard automation."""
    try:
        user32 = ctypes.windll.user32
        for vk in _STICKY_KEYS.values():
            user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
    except Exception as e:
        # Worth saying loudly: a failure here is what leaves Ctrl latched down
        # and the desktop behaving strangely afterwards.
        print(f"[Keys] failed to release held keys: {e}")


def open_application(app_name: str, file_path: str = None) -> str:
    """Opens any desktop application (e.g. 'excel', 'settings', 'word', 'chrome', 'calculator').
    Args:
        app_name: The name of the application to launch.
        file_path: Optional path to open with the application (e.g., 'C:/project' for vscode).
    """
    app_key = app_name.lower().strip()
    
    # Try to find a match in our mapped list
    command = None
    for key, cmd in ALLOWED_APPS.items():
        if key in app_key or app_key in key:
            command = cmd
            break
            
    # Dynamic fallback for unmapped apps
    if not command:
        command = f"start {app_key}"
        
    # Append the file path if provided
    if file_path:
        # Wrap in quotes in case there are spaces in the path
        command = f'{command} "{file_path}"'
        
    try:
        subprocess.Popen(command, shell=True)
        return f"Successfully launched {app_name}{' with path ' + file_path if file_path else ''}."
    except Exception as e:
        return f"Failed to launch {app_name}. Error: {e}"

def close_application(app_name: str) -> str:
    """Closes any desktop application by name.
    Args:
        app_name: The name of the application (e.g., 'excel', 'settings', 'spotify', 'whatsapp').
    """
    app_key = app_name.lower().strip()
    
    # Prevent killing Windows Explorer by accident
    if "explorer" in app_key:
        return "Closing Windows Explorer is not permitted for safety reasons."
        
    found_key = None
    for key in ALLOWED_APPS.keys():
        if key in app_key or app_key in key:
            found_key = key
            break
            
    process_name = APP_PROCESS_NAMES.get(found_key) if found_key else f"{app_key}.exe"
        
    try:
        # /F forces termination, /IM specifies image name, /T kills child processes
        subprocess.check_output(f'taskkill /F /IM "{process_name}" /T', shell=True, stderr=subprocess.STDOUT, timeout=20)
        return f"Successfully closed {app_name}."
    except Exception:
        # Secondary attempt with wildcards or exact string
        try:
            subprocess.check_output(f'taskkill /F /FI "WINDOWTITLE eq *{app_name}*" /T', shell=True, stderr=subprocess.STDOUT, timeout=20)
            return f"Successfully closed {app_name}."
        except Exception as e:
            return f"Failed to close {app_name}. Error: {e}"
    except subprocess.CalledProcessError as e:
        output = e.output.decode('utf-8', errors='ignore') if e.output else str(e)
        return f"Failed to close {app_name}. It might already be closed. Details: {output}"
    except Exception as e:
        return f"Failed to close {app_name}. Error: {e}"

def search_spotify(query: str) -> str:
    """Searches Spotify for an artist, song, or album and opens it in the desktop app.
    Args:
        query: The search term (e.g., 'Justin Bieber', 'lofi beats').
    """
    try:
        import urllib.parse
        query_encoded = urllib.parse.quote(query)
        # Construct the official Spotify URI for searching
        command = f'start spotify:search:{query_encoded}'
        subprocess.Popen(command, shell=True)
        return f"Successfully opened Spotify and searched for '{query}'."
    except Exception as e:
        return f"Failed to search Spotify. Error: {e}"

def system_media_control(action: str) -> str:
    """Controls global system media (play, pause, next, previous).
    Args:
        action: One of 'play', 'pause', 'next', 'prev'
    """
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_PLAY_PAUSE = 0xB3
    
    action = action.lower()
    
    try:
        if action in ["play", "pause", "toggle"]:
            try:
                import time
                # If Spotify is closed, pressing play won't do anything, so we open it first.
                output = subprocess.check_output('tasklist /FI "IMAGENAME eq spotify.exe"', shell=True, timeout=20).decode()
                if "spotify.exe" not in output.lower():
                    open_application("spotify")
                    time.sleep(5)  # Give Spotify a few seconds to load
            except Exception as e:
                print(f"[Media] could not check whether Spotify is running: {e}")
                
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
            return "Toggled play/pause."
        elif action in ["next", "skip"]:
            ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 2, 0)
            return "Skipped to next track."
        elif action in ["prev", "previous", "back"]:
            ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 2, 0)
            return "Went to previous track."
        else:
            return f"Unknown media action: {action}"
    except Exception as e:
        return f"Failed to control media: {e}"

def adjust_volume(action: str, steps: int = 5) -> str:
    """Adjusts system volume up, down, or mutes it.
    Args:
        action: One of 'volume_up', 'volume_down', 'mute'
        steps: Number of volume increments/decrements (default 5).
    """
    try:
        import pyautogui
        action = action.lower().strip()
        if "up" in action:
            for _ in range(steps):
                pyautogui.press('volumeup')
            return f"Increased volume by {steps} steps."
        elif "down" in action:
            for _ in range(steps):
                pyautogui.press('volumedown')
            return f"Decreased volume by {steps} steps."
        elif "mute" in action:
            pyautogui.press('volumemute')
            return "Toggled volume mute."
        else:
            return f"Unknown volume action: {action}"
    except Exception as e:
        return f"Failed to adjust volume: {e}"

def take_screenshot(filename: str = None) -> str:
    """Takes a full screen desktop screenshot and saves it to disk.
    Args:
        filename: Optional custom filename (e.g. 'desktop_capture').
    """
    import datetime
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shots_dir = os.path.join(base_dir, "screenshots")
    os.makedirs(shots_dir, exist_ok=True)
    
    if not filename:
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"screenshot_{timestamp}.png"
    elif not filename.endswith('.png'):
        filename += '.png'
        
    full_path = os.path.join(shots_dir, filename)

    # First attempt: PIL ImageGrab
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        img.save(full_path)
        return f"Screenshot saved successfully to: {full_path}"
    except Exception as e:
        print(f"[Screen] PIL capture failed, trying PyAutoGUI: {e}")

    # Second attempt: PyAutoGUI
    try:
        import pyautogui
        pyautogui.screenshot(full_path)
        return f"Screenshot saved successfully to: {full_path}"
    except Exception as e:
        print(f"[Screen] PyAutoGUI capture failed, trying PowerShell: {e}")

    # Third attempt: Native Windows PowerShell System.Drawing capture
    try:
        ps_cmd = f'powershell -command "Add-Type -AssemblyName System.Drawing, System.Windows.Forms; $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height); $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); $bmp.Save(\'{full_path}\')"'
        subprocess.run(ps_cmd, shell=True, check=True, timeout=60)
        if os.path.exists(full_path):
            return f"Screenshot saved successfully to: {full_path}"
    except Exception as e:
        return f"Failed to take screenshot: {e}"

# Playwright defaults to 30s per action and no cap on some waits. These are
# what a person will sit through before deciding the assistant is broken.
BROWSER_ACTION_TIMEOUT_MS = 15_000
BROWSER_NAV_TIMEOUT_MS = 30_000


class BrowserManager:
    """Manages a persistent Playwright browser session."""
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.page = None

    def start(self):
        """Starts the browser with a persistent user profile if it isn't already running."""
        if not self.playwright:
            self.playwright = sync_playwright().start()
            
            # Setup a persistent user data directory
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            profile_dir = os.path.join(base_dir, "data", "browser_profile")
            os.makedirs(profile_dir, exist_ok=True)
            
            # Launch persistent context to keep login sessions and avoid CAPTCHAs
            self.browser = self.playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
                no_viewport=True
            )
            
            # Hide webdriver fingerprint to bypass Google Login blocks
            self.browser.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            # Persistent context comes with a default page
            if len(self.browser.pages) > 0:
                self.page = self.browser.pages[0]
            else:
                self.page = self.browser.new_page()

            # Playwright's own timeouts, because its sync API is bound to the
            # thread that created it and so cannot be wrapped in a watchdog
            # thread like the other tools. A page that never finishes loading
            # would otherwise hold Ultron's single worker indefinitely.
            self.browser.set_default_timeout(BROWSER_ACTION_TIMEOUT_MS)
            self.browser.set_default_navigation_timeout(BROWSER_NAV_TIMEOUT_MS)

    def navigate(self, query_or_url: str) -> str:
        """Navigates the browser to a URL or a Google search."""
        try:
            self.start()
            
            is_url = False
            if " " not in query_or_url:
                if query_or_url.startswith("http://") or query_or_url.startswith("https://"):
                    is_url = True
                elif "localhost" in query_or_url or "." in query_or_url:
                    is_url = True
                    
            if is_url:
                url = query_or_url if query_or_url.startswith("http") else f"http://{query_or_url}"
            else:
                url = f"https://www.google.com/search?q={query_or_url}"
                
            self.page.goto(url)
            self.page.wait_for_load_state("domcontentloaded")
            return f"Successfully navigated to: {self.page.title()}"
        except Exception as e:
            return f"Failed to navigate. Error: {e}"

    def click(self, text_or_selector: str) -> str:
        """Clicks an element on the page based on its text content or CSS selector."""
        try:
            if not self.page:
                return "Browser is not open."
            
            locator = self.page.get_by_text(text_or_selector, exact=False).first
            if locator.count() == 0:
                locator = self.page.get_by_role("button", name=text_or_selector).first
            if locator.count() == 0:
                locator = self.page.locator(text_or_selector).first
                
            locator.click(timeout=5000)
            # Short wait to allow navigation or DOM changes
            self.page.wait_for_timeout(1000)
            return f"Successfully clicked '{text_or_selector}'."
        except Exception as e:
            return f"Failed to click '{text_or_selector}'. Error: {e}"

    def type_text(self, text_or_selector: str, input_text: str) -> str:
        """Finds an input field and types into it. You can optionally append 'Enter' to hit enter after typing."""
        try:
            if not self.page:
                return "Browser is not open."
                
            locator = self.page.get_by_placeholder(text_or_selector).first
            if locator.count() == 0:
                locator = self.page.get_by_text(text_or_selector, exact=False).first
            if locator.count() == 0:
                locator = self.page.locator(text_or_selector).first
                
            locator.fill(input_text, timeout=5000)
            return f"Successfully typed into '{text_or_selector}'."
        except Exception as e:
            return f"Failed to type into '{text_or_selector}'. Error: {e}"
            
    def press_key(self, key: str) -> str:
        """Presses a keyboard key on the active page (e.g., 'Enter', 'Escape')."""
        try:
            if not self.page:
                return "Browser is not open."
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(1000)
            return f"Pressed '{key}' key."
        except Exception as e:
            return f"Failed to press key. Error: {e}"

    def go_back(self) -> str:
        """Navigates back to the previous page."""
        try:
            if not self.page:
                return "Browser is not open."
            self.page.go_back(timeout=5000)
            return f"Navigated back to: {self.page.title()}"
        except Exception as e:
            return f"Failed to navigate back. Error: {e}"
            
    def go_forward(self) -> str:
        """Navigates forward to the next page in history."""
        try:
            if not self.page:
                return "Browser is not open."
            self.page.go_forward(timeout=5000)
            return f"Navigated forward to: {self.page.title()}"
        except Exception as e:
            return f"Failed to navigate forward. Error: {e}"

    def new_tab(self, url: str = None) -> str:
        """Opens a new browser tab. If url is provided, navigates to it."""
        try:
            if not self.browser:
                self.start()
            self.page = self.browser.new_page()
            if url:
                if "." in url and " " not in url and not url.startswith("http"):
                    url = f"https://{url}"
                self.page.goto(url)
                self.page.wait_for_load_state("domcontentloaded")
            return f"Opened new tab. Active tabs: {len(self.browser.pages)}"
        except Exception as e:
            return f"Failed to open new tab. Error: {e}"

    def switch_tab(self, index: int) -> str:
        """Switches to the tab at the given index (0-based)."""
        try:
            if not self.browser:
                return "Browser is not open."
            pages = self.browser.pages
            if index < 0 or index >= len(pages):
                return f"Invalid tab index. You have {len(pages)} open tabs."
            self.page = pages[index]
            self.page.bring_to_front()
            return f"Switched to tab {index}: {self.page.title()}"
        except Exception as e:
            return f"Failed to switch tab. Error: {e}"

    def close_tab(self, index: int = None) -> str:
        """Closes the tab at the given index, or the active tab if index is None."""
        try:
            if not self.browser:
                return "Browser is not open."
            pages = self.browser.pages
            if not pages:
                return "No tabs are open."
                
            if index is None:
                self.page.close()
                pages = self.browser.pages
                if pages:
                    self.page = pages[-1]
                    self.page.bring_to_front()
                else:
                    self.page = None
                return "Closed active tab."
            
            if index < 0 or index >= len(pages):
                return f"Invalid tab index. You have {len(pages)} open tabs."
                
            closing_page = pages[index]
            is_active = (closing_page == self.page)
            closing_page.close()
            
            pages = self.browser.pages
            if is_active and pages:
                self.page = pages[-1]
                self.page.bring_to_front()
            elif not pages:
                self.page = None
                
            return f"Closed tab {index}."
        except Exception as e:
            return f"Failed to close tab. Error: {e}"

    def take_screenshot(self, filename: str = None) -> str:
        """Takes a full page screenshot in the browser and saves it."""
        try:
            if not self.page:
                return "Browser is not open."
            
            import os
            import datetime
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            screenshots_dir = os.path.join(base_dir, "data", "screenshots")
            os.makedirs(screenshots_dir, exist_ok=True)
            
            if not filename:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"browser_ss_{timestamp}.png"
                
            if not filename.endswith(".png"):
                filename += ".png"
                
            filepath = os.path.join(screenshots_dir, filename)
            self.page.screenshot(path=filepath, full_page=True)
            return f"Browser screenshot saved to: {filepath}"
        except Exception as e:
            return f"Failed to take browser screenshot. Error: {e}"
            
    def scroll(self, direction: str) -> str:
        """Scrolls the page 'up' or 'down'."""
        try:
            if not self.page:
                return "Browser is not open."
            amount = 800 if direction.lower() == "down" else -800
            self.page.mouse.wheel(0, amount)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Failed to scroll. Error: {e}"

    def read_page(self) -> str:
        """Reads and extracts the main text from the current page."""
        try:
            if not self.page:
                return "Browser is not open. Please navigate somewhere first."
                
            # Extract all text from body, removing script/style tags
            text = self.page.evaluate('''() => {
                const elements = document.body.querySelectorAll('script, style');
                elements.forEach(el => el.remove());
                return document.body.innerText;
            }''')
            
            # Truncate to avoid blowing up the context window
            return text[:4000] + ("..." if len(text) > 4000 else "")
        except Exception as e:
            return f"Failed to read page. Error: {e}"

    def close(self) -> str:
        """Closes the browser session."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
            self.browser = None
            self.page = None
            return "Browser closed successfully."
        return "Browser was already closed."

def get_system_health() -> str:
    """Returns PC health & resource usage (CPU, RAM, Battery, Storage)."""
    try:
        import psutil
        cpu_usage = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        ram_used = memory.used / (1024 ** 3)
        ram_total = memory.total / (1024 ** 3)
        ram_percent = memory.percent
        
        disk = psutil.disk_usage('/')
        disk_free = disk.free / (1024 ** 3)
        disk_total = disk.total / (1024 ** 3)
        
        battery_str = "Battery: N/A (Desktop/AC Power)"
        if hasattr(psutil, "sensors_battery"):
            battery = psutil.sensors_battery()
            if battery:
                plugged = "Plugged in" if battery.power_plugged else "Discharging"
                battery_str = f"Battery: {battery.percent}% ({plugged})"
                
        return (f"System Health Status:\n"
                f"- CPU Usage: {cpu_usage}%\n"
                f"- RAM Usage: {ram_used:.1f} GB / {ram_total:.1f} GB ({ram_percent}%)\n"
                f"- Storage Free: {disk_free:.1f} GB / {disk_total:.1f} GB free\n"
                f"- {battery_str}")
    except Exception as e:
        return f"Failed to retrieve system health: {e}"

def write_in_notepad(text: str, filename: str = None) -> str:
    """Opens Notepad, types the specified text directly into the document, and saves it.
    Args:
        text: The content to write.
        filename: Optional filename to save (e.g. 'notes.txt').
    """
    try:
        import time
        import pyautogui
        import pyperclip
        
        # Open Notepad
        subprocess.Popen(['notepad.exe'])
        time.sleep(1.5)  # Wait for Notepad window to launch
        
        # Copy text to clipboard and paste for instant, error-free typing
        pyperclip.copy(text)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.5)
        
        if filename:
            # Save file
            pyautogui.hotkey('ctrl', 's')
            time.sleep(1.0)
            base_dir = os.path.expanduser("~/Desktop")
            full_path = os.path.join(base_dir, filename)
            pyperclip.copy(full_path)
            pyautogui.hotkey('ctrl', 'v')
            pyautogui.press('enter')
            return f"Successfully typed text in Notepad and saved as '{filename}' on your Desktop."
            
        return "Successfully typed text into Notepad."
    except Exception as e:
        return f"Failed to write in Notepad: {e}"
    finally:
        # A hotkey that failed between press and release would leave Ctrl down
        # across the whole desktop.
        _release_quietly()

def send_whatsapp_message(contact_name: str, message: str) -> str:
    """Opens WhatsApp Desktop, searches for contact_name, types the message, and sends it.
    Args:
        contact_name: Name of the contact to message.
        message: The message text to send.
    """
    try:
        import time
        import pyautogui
        import pyperclip
        
        # Launch WhatsApp application
        open_application("whatsapp")
        time.sleep(3.0)  # Wait for WhatsApp window to open
        
        # Focus search bar (Ctrl+F in WhatsApp)
        pyautogui.hotkey('ctrl', 'f')
        time.sleep(0.5)
        
        # Type contact name
        pyperclip.copy(contact_name)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(1.5)
        pyautogui.press('enter')  # Open chat
        time.sleep(1.0)
        
        # Type message
        pyperclip.copy(message)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.5)
        pyautogui.press('enter')  # Send message
        
        return f"Successfully sent WhatsApp message to '{contact_name}': '{message}'"
    except Exception as e:
        return f"Failed to send WhatsApp message: {e}"
    finally:
        _release_quietly()

def read_clipboard() -> str:
    """Reads and returns text currently copied to the Windows Clipboard."""
    try:
        import pyperclip
        text = pyperclip.paste()
        if not text or not text.strip():
            return "Your clipboard is currently empty."
        return f"Clipboard Content:\n{text[:1000]}"
    except Exception as e:
        return f"Failed to read clipboard: {e}"

def copy_to_clipboard(text: str) -> str:
    """Copies the specified text directly to the Windows Clipboard.
    Args:
        text: The text string to copy (e.g. link, URL, note, code).
    """
    try:
        import pyperclip
        pyperclip.copy(text)
        return f"Successfully copied to clipboard: '{text}'"
    except Exception as e:
        return f"Failed to copy to clipboard: {e}"

def find_files(search_query: str, location: str = 'Desktop') -> str:
    """Finds files matching search_query in Desktop, Downloads, or Documents folder.
    Args:
        search_query: File name or extension (e.g. '.pdf', 'notes', '.txt').
        location: 'Desktop', 'Downloads', or 'Documents'.
    """
    try:
        # Resolved through the registry so a OneDrive-redirected Desktop or
        # Documents is searched, not an empty leftover folder.
        target_dir = known_folder(location) or known_folder("desktop")
        if not target_dir:
            return f"Could not locate the {location} folder on this computer."

        matches = []
        for root, _, files in os.walk(target_dir):
            for file in files:
                if search_query.lower() in file.lower():
                    matches.append(os.path.join(root, file))
                if len(matches) >= 15:
                    break
            if len(matches) >= 15:
                break
                
        if not matches:
            return f"No files matching '{search_query}' were found in {location}."
            
        result_str = "\n".join(f"- {m}" for m in matches)
        return f"Found matching files in {location}:\n{result_str}"
    except Exception as e:
        return f"Failed to search for files: {e}"

def read_file_content(file_path: str) -> str:
    """Reads content of a local text file (.txt, .md, .py, .json, .csv).
    Args:
        file_path: Absolute or relative path to file.
    """
    try:
        if not os.path.exists(file_path):
            return f"File does not exist: {file_path}"
            
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(4000) # Read up to 4000 chars
            return f"File Content ({file_path}):\n{content}"
    except Exception as e:
        return f"Failed to read file content: {e}"

def system_power_control(action: str) -> str:
    """Performs system power actions: lock, sleep, shutdown, or cancel_shutdown.
    Args:
        action: 'lock', 'sleep', 'shutdown', or 'cancel_shutdown'
    """
    action = action.lower().strip()
    try:
        if action in ['lock', 'lock_pc']:
            ctypes.windll.user32.LockWorkStation()
            return "PC locked, sir."
        elif action in ['sleep', 'sleep_pc', 'suspend']:
            subprocess.Popen('rundll32.exe powrprof.dll,SetSuspendState 0,1,0', shell=True)
            return "Putting PC to sleep, sir."
        elif action in ['shutdown', 'poweroff']:
            subprocess.Popen('shutdown /s /t 30', shell=True)
            return "System shutdown scheduled in 30 seconds, sir. Say 'cancel shutdown' if needed."
        elif action in ['cancel_shutdown', 'abort_shutdown']:
            subprocess.Popen('shutdown /a', shell=True)
            return "Shutdown cancelled, sir."
        else:
            return f"Unknown power action: {action}"
    except Exception as e:
        return f"Failed power action: {e}"

def empty_recycle_bin() -> str:
    """Permanently empties the Windows Recycle Bin. This cannot be undone."""
    try:
        # SHERB_NOCONFIRMATION (0x1) | SHERB_NOPROGRESSUI (0x2) | SHERB_NOSOUND (0x4)
        flags = 0x1 | 0x2 | 0x4
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        if res == 0:
            return "Recycle Bin has been emptied successfully, sir."
        else:
            return f"Emptied Recycle Bin (result code: {res})."
    except Exception as e:
        return f"Failed to empty Recycle Bin: {e}"

def clean_temp_files() -> str:
    """Cleans temporary files and cache from the Windows %TEMP% folder to free up space."""
    import tempfile
    try:
        temp_dir = tempfile.gettempdir()
        deleted_count = 0
        freed_bytes = 0
        # Files in use by another program cannot be removed, and there are
        # normally hundreds of them. Counting rather than printing each keeps
        # the count honest without burying the rest of the log.
        skipped = 0
        last_reason = ""
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    size = os.path.getsize(file_path)
                    os.remove(file_path)
                    deleted_count += 1
                    freed_bytes += size
                except Exception as e:
                    skipped += 1
                    last_reason = str(e)
        if skipped:
            print(f"[Temp] skipped {skipped} file(s) still in use; last: {last_reason}")
        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        note = f" {skipped} were in use and left alone." if skipped else ""
        return (f"Temporary files cleaned, sir. Removed {deleted_count} files "
                f"({freed_mb} MB freed).{note}")
    except Exception as e:
        return f"Failed to clean temp files: {e}"

def create_file(file_path: str, content: str = "") -> str:
    """Creates a new text file or overwrites an existing file with the specified content.
    Args:
        file_path: Relative or absolute path where the file should be saved.
        content: Text content to write into the file.
    """
    try:
        parent_dir = os.path.dirname(os.path.abspath(file_path))
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully created file at '{file_path}', sir."
    except Exception as e:
        return f"Failed to create file: {e}"

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 0x0003
# ALLOWUNDO is the whole point: it routes through the Recycle Bin instead of
# unlinking. The rest suppress Explorer's own dialogs, which would otherwise
# block a background thread waiting for a click nobody can see.
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400


def recycle_path(path: str) -> str:
    """Moves a file or folder to the Recycle Bin. Returns '' on success.

    Deleting outright is unrecoverable, and an assistant acting on a misheard
    filename is exactly the case where recovery matters. The Recycle Bin turns
    a mistake into an inconvenience.
    """
    absolute = os.path.abspath(path)
    if not os.path.exists(absolute):
        return f"does not exist: '{path}'"

    operation = _SHFILEOPSTRUCTW()
    operation.wFunc = _FO_DELETE
    # The API takes a double-null-terminated list, not a plain string.
    operation.pFrom = absolute + "\0\0"
    operation.fFlags = (
        _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI
    )

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        return f"Windows refused the operation (code {result})"
    if operation.fAnyOperationsAborted:
        return "the operation was aborted"
    return ""


def delete_file(file_path: str) -> str:
    """Moves a file to the Recycle Bin, where it can still be restored.

    Args:
        file_path: Path to the file to remove.
    """
    try:
        if not os.path.exists(file_path):
            return f"File does not exist: '{file_path}'"
        problem = recycle_path(file_path)
        if problem:
            return f"Failed to delete file: {problem}"
        return (f"Moved '{file_path}' to the Recycle Bin, sir. "
                "It can be restored from there if that was a mistake.")
    except Exception as e:
        return f"Failed to delete file: {e}"

def list_directory(path: str = ".") -> str:
    """Lists files and subdirectories in a folder.
    Args:
        path: Path to folder (defaults to current working directory).
    """
    try:
        user_home = os.path.expanduser("~")
        if path.lower() in ['desktop', 'desktop/']:
            target_path = os.path.join(user_home, 'Desktop')
        elif path.lower() in ['downloads', 'downloads/']:
            target_path = os.path.join(user_home, 'Downloads')
        elif path.lower() in ['documents', 'documents/']:
            target_path = os.path.join(user_home, 'Documents')
        else:
            target_path = path
            
        if not os.path.exists(target_path):
            return f"Folder path does not exist: '{target_path}'"
            
        entries = os.listdir(target_path)
        if not entries:
            return f"Directory '{target_path}' is empty."
            
        items = []
        for entry in entries[:30]:
            full_p = os.path.join(target_path, entry)
            is_dir = "[DIR]" if os.path.isdir(full_p) else "[FILE]"
            items.append(f"{is_dir} {entry}")
            
        return f"Directory contents of '{target_path}':\n" + "\n".join(items)
    except Exception as e:
        return f"Failed to list directory: {e}"

def open_folder(path: str) -> str:
    """Opens a folder in Windows File Explorer.

    Accepts a full path ('C:/projects/ultron'), a shell folder name
    ('Downloads', 'Desktop', 'Documents', 'Pictures', 'Music', 'Videos'), or
    just the folder's name ('ultron') — an unknown name is searched for on the
    user's drives, so you do NOT need to ask the user for a full path first.

    Args:
        path: Folder path, shell folder name, or plain folder name.
    """
    try:
        query = (path or "").strip().strip("\"'")
        if not query:
            return "No folder was given."

        missed_path = ""
        if _looks_like_path(query):
            expanded = os.path.expanduser(os.path.expandvars(query))
            if os.path.isdir(expanded):
                resolved = os.path.abspath(expanded)
                os.startfile(resolved)
                return f"Successfully opened folder: '{resolved}'"
            # Fall through and search on the final component, but remember
            # that what opens is not what was asked for.
            missed_path = query
            query = os.path.basename(os.path.normpath(expanded)) or query

        name = _clean_folder_name(query)

        shell_folder = known_folder(name)
        if shell_folder:
            os.startfile(shell_folder)
            return f"Successfully opened folder: '{shell_folder}'"

        # Treat it as a name and go looking, rather than bouncing the question
        # back to the user.
        matches = find_folders(name)
        if not matches:
            return (f"No folder named '{name}' was found on this computer. "
                    f"Ask the user for the full path.")

        if len(matches) == 1:
            os.startfile(matches[0])
            if missed_path:
                return (f"'{missed_path}' does not exist. Opened the closest "
                        f"match instead: '{matches[0]}'. Tell the user this.")
            return f"Successfully opened folder: '{matches[0]}'"

        listed = "\n".join(f"- {m}" for m in matches)
        return (f"Several folders match '{name}'. Ask the user which one:\n{listed}")
    except Exception as e:
        return f"Failed to open folder: {e}"

def copy_file(source_path: str, destination_path: str) -> str:
    """Copies a file or directory from source_path to destination_path."""
    try:
        import shutil
        if not os.path.exists(source_path):
            return f"Source path does not exist: '{source_path}'"
        if os.path.isdir(source_path):
            shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
            return f"Successfully copied directory to '{destination_path}'"
        else:
            shutil.copy2(source_path, destination_path)
            return f"Successfully copied file to '{destination_path}'"
    except Exception as e:
        return f"Failed to copy: {e}"

def move_file(source_path: str, destination_path: str) -> str:
    """Moves a file or directory from source_path to destination_path."""
    try:
        import shutil
        if not os.path.exists(source_path):
            return f"Source path does not exist: '{source_path}'"
        shutil.move(source_path, destination_path)
        return f"Successfully moved to '{destination_path}'"
    except Exception as e:
        return f"Failed to move: {e}"
