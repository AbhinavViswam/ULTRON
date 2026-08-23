"""Control the user's real Chrome browser via the Chrome DevTools Protocol.

Instead of launching a separate Chromium instance (the old BrowserManager),
this connects Playwright to the Chrome the user already has open — same
logins, same cookies, same history.  Everything Ultron does is visible on
screen in real time.

Chrome must be running with ``--remote-debugging-port=9222``.  The helper
``ensure_chrome_cdp()`` takes care of that: it checks whether the port is
already listening, and if not it (re)launches Chrome with the flag.
"""

import json
import os
import socket
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright, Playwright, Browser, Page

# ── Defaults ──────────────────────────────────────────────────────────────────
CDP_PORT = 9222
CDP_ENDPOINT = f"http://localhost:{CDP_PORT}"

# Where Chrome lives on this machine.  Checked in order.
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# Playwright timeout knobs  (milliseconds)
_NAV_TIMEOUT_MS   = 30_000
_ACTION_TIMEOUT_MS = 10_000


def _find_chrome_exe() -> str | None:
    for path in _CHROME_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None

# ── The browser controller ───────────────────────────────────────────────────
class ChromeBrowser:
    """Drives a visible Chrome session through CDP + Playwright.
    
    Uses a dedicated 'User Data Automation' profile so Chrome allows remote debugging 
    without interfering with the user's main Chrome window.
    """

    def __init__(self):
        self._pw: Playwright | None = None
        self._browser = None  # BrowserContext
        self._page: Page | None = None

    # ── connection ────────────────────────────────────────────────────────
    def _ensure_connected(self) -> str | None:
        """Returns an error string, or *None* when the connection is ready."""
        if self._page and not self._page.is_closed():
            return None                         # already good

        chrome_exe = _find_chrome_exe()
        if not chrome_exe:
            return "Could not find chrome.exe. Install Google Chrome or set its path in settings."

        # Chrome explicitly disables remote debugging on the default User Data directory.
        # We must use a separate automation profile.
        user_data = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data Automation")

        try:
            if not self._pw:
                self._pw = sync_playwright().start()
            
            self._browser = self._pw.chromium.launch_persistent_context(
                user_data_dir=user_data,
                executable_path=chrome_exe,
                channel="chrome",
                headless=False,
                args=["--start-maximized"]
            )
            self._page = self._browser.pages[0] if self._browser.pages else self._browser.new_page()
            self._browser.set_default_timeout(_ACTION_TIMEOUT_MS)
            self._browser.set_default_navigation_timeout(_NAV_TIMEOUT_MS)
            return None
        except Exception as exc:
            return f"Failed to launch Chrome via Playwright: {exc}"

    def _active_page(self) -> Page | None:
        """Return the most recently used page, reconnecting if necessary."""
        err = self._ensure_connected()
        if err:
            return None
        # If the page was closed, grab the latest one.
        if self._page.is_closed():
            pages = self._browser.pages
            self._page = pages[-1] if pages else self._browser.new_page()
        return self._page

    # ── public tools ──────────────────────────────────────────────────────
    def navigate(self, url_or_query: str) -> str:
        """Go to a URL or Google-search a query.  Returns the page title."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            text = (url_or_query or "").strip()
            if not text:
                return "Error: nothing to navigate to."

            is_url = (text.startswith("http://") or text.startswith("https://")
                      or ("." in text and " " not in text)
                      or text.startswith("localhost"))
            if is_url:
                url = text if text.startswith("http") else f"https://{text}"
            else:
                url = f"https://www.google.com/search?q={text}"

            self._page.goto(url, wait_until="domcontentloaded")
            return f"Navigated to: {self._page.title()}"
        except Exception as exc:
            return f"Navigation failed: {exc}"

    def click(self, target: str) -> str:
        """Click an element by its visible text, aria role, or CSS selector."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            page = self._page
            # Try visible text first (most intuitive for LLM).
            loc = page.get_by_text(target, exact=False).first
            if loc.count():
                loc.click()
                page.wait_for_timeout(800)
                return f"Clicked '{target}'."

            # Try role-based match.
            for role in ("link", "button", "menuitem"):
                loc = page.get_by_role(role, name=target).first
                if loc.count():
                    loc.click()
                    page.wait_for_timeout(800)
                    return f"Clicked {role} '{target}'."

            # Fall back to CSS selector.
            loc = page.locator(target).first
            loc.click(timeout=5000)
            page.wait_for_timeout(800)
            return f"Clicked element matching '{target}'."
        except Exception as exc:
            return f"Click failed for '{target}': {exc}"

    def type_text(self, target: str, text: str) -> str:
        """Type *text* into an input field found by placeholder, label, or CSS."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            page = self._page
            loc = page.get_by_placeholder(target, exact=False).first
            if loc.count():
                loc.fill(text)
                return f"Typed into field '{target}'."

            loc = page.get_by_label(target, exact=False).first
            if loc.count():
                loc.fill(text)
                return f"Typed into field '{target}'."

            loc = page.locator(target).first
            loc.fill(text, timeout=5000)
            return f"Typed into element '{target}'."
        except Exception as exc:
            return f"Type failed for '{target}': {exc}"

    def scroll(self, direction: str = "down") -> str:
        """Scroll the page up or down."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            delta = 1500 if direction.lower() == "down" else -1500
            self._page.mouse.wheel(0, delta)
            self._page.wait_for_timeout(400)
            return f"Scrolled {direction}."
        except Exception as exc:
            return f"Scroll failed: {exc}"

    def read_page(self) -> str:
        """Extract the visible text content from the current page."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            title = self._page.title()
            text = self._page.inner_text("body") or ""
            # Trim to a reasonable size for the LLM context window.
            text = text[:6000]
            return f"Page: '{title}'\n\n{text}"
        except Exception as exc:
            return f"Failed to read page: {exc}"

    def screenshot(self, filename: str | None = None) -> str:
        """Take a screenshot of the current page."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
            )
            os.makedirs(base_dir, exist_ok=True)
            fname = filename or f"screenshot_{int(time.time())}.png"
            if not fname.endswith(".png"):
                fname += ".png"
            path = os.path.join(base_dir, fname)
            self._page.screenshot(path=path, full_page=False)
            return f"Screenshot saved to {path}"
        except Exception as exc:
            return f"Screenshot failed: {exc}"

    def go_back(self) -> str:
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            self._page.go_back(wait_until="domcontentloaded")
            return f"Went back to: {self._page.title()}"
        except Exception as exc:
            return f"Go back failed: {exc}"

    def go_forward(self) -> str:
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            self._page.go_forward(wait_until="domcontentloaded")
            return f"Went forward to: {self._page.title()}"
        except Exception as exc:
            return f"Go forward failed: {exc}"

    def new_tab(self, url: str = None) -> str:
        """Open a new tab and optionally navigate."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            self._page = self._browser.new_page()
            if url:
                self._page.goto(url, wait_until="domcontentloaded")
            return f"Opened new tab. Title: {self._page.title()}"
        except Exception as exc:
            return f"New tab failed: {exc}"

    def close_tab(self) -> str:
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            self._page.close()
            pages = self._browser.pages
            if pages:
                self._page = pages[-1]
                return f"Closed the tab. Now on: {self._page.title()}"
            return "Closed the last tab."
        except Exception as exc:
            return f"Close tab failed: {exc}"

    def press_key(self, key: str) -> str:
        """Press a keyboard key (Enter, Escape, Tab, etc.)."""
        err = self._ensure_connected()
        if err:
            return f"Error: {err}"
        try:
            self._page.keyboard.press(key)
            self._page.wait_for_timeout(500)
            return f"Pressed '{key}'."
        except Exception as exc:
            return f"Key press failed: {exc}"

    def close(self):
        """Disconnect from Chrome (does NOT close Chrome itself)."""
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._page = None
