import os
import subprocess
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
    "whatsapp": "start whatsapp",
    "antigravity": "start antigravity",
    "docker": "start docker",
    "pg admin": "start pgadmin4",
    "minecraft": "start minecraft"
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
            # Launch in headed mode as requested by user
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
