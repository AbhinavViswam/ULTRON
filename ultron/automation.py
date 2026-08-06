import os
import subprocess
import ctypes
from playwright.sync_api import sync_playwright

# Safe list of allowed applications
# Maps common names to their Windows executable or shell command
ALLOWED_APPS = {
    "chrome": "start chrome",
    "vscode": "code",
    "notepad": "notepad",
    "calculator": "calc",
    "explorer": "explorer",
    "edge": "start msedge",
    "spotify": "start spotify",
    "whatsapp": "explorer.exe whatsapp:",
    "antigravity": "start antigravity",
    "docker": "start docker",
    "pg admin": "start pgadmin4",
    "minecraft": "start minecraft"
}

# Maps allowed apps to their executable process names for closing
APP_PROCESS_NAMES = {
    "chrome": "chrome.exe",
    "vscode": "code.exe",
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe", 
    "edge": "msedge.exe",
    "spotify": "spotify.exe",
    "whatsapp": "WhatsApp.Root.exe",
    "docker": "Docker Desktop.exe",
    "pg admin": "pgadmin4.exe",
    "minecraft": "Minecraft.exe"
}

def open_application(app_name: str) -> str:
    """Opens a safe, predefined desktop application.
    Args:
        app_name: The name of the application (e.g., 'chrome', 'vscode', 'notepad').
    """
    app_key = app_name.lower().strip()
    
    # Try to find a match in our allowed list
    command = None
    for key, cmd in ALLOWED_APPS.items():
        if key in app_key:
            command = cmd
            break
            
    if not command:
        return f"Cannot open '{app_name}'. It is not in the safe list of allowed applications."
        
    try:
        # Run the command via shell so 'start chrome' etc works
        subprocess.Popen(command, shell=True)
        return f"Successfully launched {app_name}."
    except Exception as e:
        return f"Failed to launch {app_name}. Error: {e}"

def close_application(app_name: str) -> str:
    """Closes a safe, predefined desktop application.
    Args:
        app_name: The name of the application (e.g., 'spotify', 'whatsapp').
    """
    app_key = app_name.lower().strip()
    
    # Try to find a match in our allowed list
    found_key = None
    for key in ALLOWED_APPS.keys():
        if key in app_key:
            found_key = key
            break
            
    if not found_key:
        return f"Cannot close '{app_name}'. It is not in the safe list of allowed applications."
        
    process_name = APP_PROCESS_NAMES.get(found_key)
    if not process_name:
        # Fallback to key.exe if not explicitly mapped
        process_name = f"{found_key}.exe"
        
    try:
        # Prevent killing Windows Explorer by accident
        if found_key == "explorer":
            return "Closing Windows Explorer is not permitted for safety reasons."
            
        # /F forces termination, /IM specifies image name, /T kills child processes too
        subprocess.check_output(f'taskkill /F /IM "{process_name}" /T', shell=True, stderr=subprocess.STDOUT)
        return f"Successfully closed {app_name}."
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
