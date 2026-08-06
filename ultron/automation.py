import os
import subprocess
import ctypes
from playwright.sync_api import sync_playwright

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

def open_application(app_name: str) -> str:
    """Opens any desktop application (e.g. 'excel', 'settings', 'word', 'chrome', 'calculator').
    Args:
        app_name: The name of the application to launch.
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
        
    try:
        subprocess.Popen(command, shell=True)
        return f"Successfully launched {app_name}."
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
        subprocess.check_output(f'taskkill /F /IM "{process_name}" /T', shell=True, stderr=subprocess.STDOUT)
        return f"Successfully closed {app_name}."
    except Exception:
        # Secondary attempt with wildcards or exact string
        try:
            subprocess.check_output(f'taskkill /F /FI "WINDOWTITLE eq *{app_name}*" /T', shell=True, stderr=subprocess.STDOUT)
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
                output = subprocess.check_output('tasklist /FI "IMAGENAME eq spotify.exe"', shell=True).decode()
                if "spotify.exe" not in output.lower():
                    open_application("spotify")
                    time.sleep(5)  # Give Spotify a few seconds to load
            except Exception:
                pass
                
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
    except Exception:
        pass

    # Second attempt: PyAutoGUI
    try:
        import pyautogui
        pyautogui.screenshot(full_path)
        return f"Screenshot saved successfully to: {full_path}"
    except Exception:
        pass

    # Third attempt: Native Windows PowerShell System.Drawing capture
    try:
        ps_cmd = f'powershell -command "Add-Type -AssemblyName System.Drawing, System.Windows.Forms; $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height); $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); $bmp.Save(\'{full_path}\')"'
        subprocess.run(ps_cmd, shell=True, check=True)
        if os.path.exists(full_path):
            return f"Screenshot saved successfully to: {full_path}"
    except Exception as e:
        return f"Failed to take screenshot: {e}"

class BrowserManager:
    """Manages a persistent Playwright browser session."""
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.page = None

    def start(self):
        """Starts the browser if it isn't already running."""
        if not self.playwright:
            self.playwright = sync_playwright().start()
            # Launch an isolated, temporary browser session (not using personal profile)
            self.browser = self.playwright.chromium.launch(headless=False)
            self.page = self.browser.new_page()

    def navigate(self, query_or_url: str) -> str:
        """Navigates the browser to a URL or a Google search."""
        try:
            self.start()
            if "." in query_or_url and " " not in query_or_url:
                url = query_or_url if query_or_url.startswith("http") else f"https://{query_or_url}"
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

def find_files(search_query: str, location: str = 'Desktop') -> str:
    """Finds files matching search_query in Desktop, Downloads, or Documents folder.
    Args:
        search_query: File name or extension (e.g. '.pdf', 'notes', '.txt').
        location: 'Desktop', 'Downloads', or 'Documents'.
    """
    try:
        user_home = os.path.expanduser("~")
        loc_map = {
            'desktop': os.path.join(user_home, 'Desktop'),
            'downloads': os.path.join(user_home, 'Downloads'),
            'documents': os.path.join(user_home, 'Documents')
        }
        target_dir = loc_map.get(location.lower(), os.path.join(user_home, 'Desktop'))
        
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
    """Performs system power actions: lock, sleep, or shutdown.
    Args:
        action: 'lock', 'sleep', or 'shutdown'
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
