import os
import io
import base64
import time
import datetime
import pyautogui
from PIL import Image

def screen_capture():
    """Captures the current screen and returns the image as a base64 encoded string.
    Also saves a local copy to the screenshots/ directory."""
    try:
        # Take screenshot
        img = pyautogui.screenshot()
        
        # Save to disk permanently
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        screenshots_dir = os.path.join(project_root, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        filepath = os.path.join(screenshots_dir, filename)
        img.save(filepath)
        
        # Convert to base64 for AI API
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        print(f"\n[Screen Plugin] Screenshot saved to: {filepath}")
        return f"data:image/png;base64,{img_b64}"
    except Exception as e:
        return f"Error capturing screen: {e}"

def screen_analyze(query: str):
    """Placeholder for analyzing screen (for compatibility if needed, though usually just handled by LLM)."""
    return "Error: Use screen_capture directly instead."

def screen_find(image_path: str):
    """Finds an image on the screen and returns its coordinates. image_path should be a relative path."""
    try:
        location = pyautogui.locateOnScreen(image_path)
        if location:
            return f"Found at x={location.left}, y={location.top}, width={location.width}, height={location.height}"
        return "Image not found on screen."
    except Exception as e:
        return f"Error finding image: {e}"

def screen_get_resolution():
    """Returns the current screen resolution as a tuple (width, height)."""
    width, height = pyautogui.size()
    return f"Screen resolution: {width}x{height}"

def screen_get_mouse_position():
    """Returns the current mouse cursor position as a tuple (x, y)."""
    x, y = pyautogui.position()
    return f"Mouse position: {x}, {y}"

def screen_get_active_window():
    """Returns the title of the currently active window (requires pygetwindow on Windows)."""
    try:
        import pygetwindow as gw
        active_window = gw.getActiveWindow()
        if active_window:
            return f"Active window title: {active_window.title}"
        return "No active window found."
    except ImportError:
        return "Error: pygetwindow is not installed."
    except Exception as e:
        return f"Error getting active window: {e}"
