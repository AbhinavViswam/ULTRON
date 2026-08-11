"""
Screen awareness plugin for Ultron Desktop Assistant.

Primary method: walks the UI Automation (UIA) tree to read all visible
text and controls from any window. This is fast, accurate, and works
with any LLM (no vision capability needed).

Fallback: screenshot + base64 for vision-capable models, or OCR for
canvas/game content UIA can't see.

Dependencies:
    pip install uiautomation
    (Optional for OCR: pip install mss pytesseract pillow + Tesseract binary)
"""

import os
import io
import time
import base64
import datetime
from dataclasses import dataclass, asdict
from typing import Optional

import pyautogui

try:
    import uiautomation as auto
    UIA_AVAILABLE = True
except ImportError:
    UIA_AVAILABLE = False


# ---------------------------------------------------------------------------
# UIA data model
# ---------------------------------------------------------------------------

@dataclass
class ScreenElement:
    """A single UI element extracted from the UIA tree."""
    text: str
    control_type: str
    bbox: tuple          # (left, top, right, bottom)
    is_enabled: bool


# Control types that rarely carry useful text — skip them entirely
_SKIP_CONTROLS = {
    "SeparatorControl", "ScrollBarControl", "ThumbControl",
}

# Control types where ValuePattern might have useful content
_VALUE_CONTROLS = {
    "EditControl", "ComboBoxControl", "SliderControl",
    "SpinnerControl", "DocumentControl",
}


def _walk(control, elements: list, depth: int, max_depth: int,
          max_elements: int, seen_texts: set, deadline: float):
    """Recursively walk the UIA tree, collecting visible elements.
    
    Has a hard time deadline to prevent hanging on complex UIs.
    """
    if depth > max_depth or len(elements) >= max_elements:
        return
    # Hard timeout — stop walking if we've taken too long
    if time.monotonic() > deadline:
        return

    try:
        control_type = control.ControlTypeName or ""

        # Skip control types that never carry useful info
        if control_type in _SKIP_CONTROLS:
            return

        name = control.Name or ""

        if name.strip():
            # Deduplicate: skip if we already have this exact text+type
            dedup_key = f"{name}|{control_type}"
            if dedup_key not in seen_texts:
                try:
                    r = control.BoundingRectangle
                    # Only add if it has a real bounding box (not zero-size)
                    if r.right > r.left and r.bottom > r.top:
                        elements.append(ScreenElement(
                            text=name.strip(),
                            control_type=control_type,
                            bbox=(r.left, r.top, r.right, r.bottom),
                            is_enabled=control.IsEnabled,
                        ))
                        seen_texts.add(dedup_key)
                except Exception:
                    pass

        # Only try ValuePattern on control types that actually use it
        if control_type in _VALUE_CONTROLS:
            try:
                pattern = control.GetValuePattern()
                if pattern and pattern.Value and pattern.Value.strip():
                    value = pattern.Value.strip()
                    val_key = f"{value}|Value"
                    if val_key not in seen_texts:
                        r = control.BoundingRectangle
                        elements.append(ScreenElement(
                            text=value,
                            control_type=f"{control_type}:Value",
                            bbox=(r.left, r.top, r.right, r.bottom),
                            is_enabled=control.IsEnabled,
                        ))
                        seen_texts.add(val_key)
            except Exception:
                pass

    except Exception:
        pass  # some controls throw on property access mid-refresh

    # Walk children — use GetFirstChildControl/GetNextSiblingControl 
    # instead of GetChildren() to avoid loading entire child list at once
    try:
        child = control.GetFirstChildControl()
        while child and len(elements) < max_elements and time.monotonic() < deadline:
            _walk(child, elements, depth + 1, max_depth, max_elements, seen_texts, deadline)
            try:
                child = child.GetNextSiblingControl()
            except Exception:
                break
    except Exception:
        pass


def _read_foreground(max_depth: int = 8, max_elements: int = 150,
                     timeout_seconds: float = 2.0) -> list:
    """Read all visible text/controls from the currently focused window.
    
    Runs the UIA walk in a daemon thread so that even if a single COM
    call blocks, we return after timeout_seconds.
    """
    import threading

    elements = []
    error_holder = [None]

    def worker():
        try:
            root = auto.GetForegroundControl()
            deadline = time.monotonic() + timeout_seconds
            _walk(root, elements, 0, max_depth, max_elements, set(), deadline)
        except Exception as e:
            error_holder[0] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)

    if t.is_alive():
        print(f"[Screen Plugin] UIA tree walk timed out after {timeout_seconds}s, returning {len(elements)} elements collected so far.")
    
    return elements


def _read_by_title(title_substring: str, max_depth: int = 8,
                   max_elements: int = 150,
                   timeout_seconds: float = 2.0) -> Optional[list]:
    """Read a specific window by (partial) title match, even if unfocused.
    
    Runs the UIA walk in a daemon thread for safety.
    """
    import threading

    elements = []
    found_flag = [False]

    def worker():
        # UI Automation is COM-based, and COM must be initialized on every thread
        # that touches it. Without this the whole walk fails immediately with
        # "CoInitialize has not been called" and no window is ever found.
        com_ready = False
        try:
            import ctypes
            ctypes.windll.ole32.CoInitialize(None)
            com_ready = True
        except Exception as e:
            print(f"[Screen Plugin] COM initialization failed: {e}")
            return

        try:
            _walk_window()
        finally:
            if com_ready:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def _walk_window():
        # First try exact match
        win = auto.WindowControl(searchDepth=1, Name=title_substring)
        if not win.Exists(maxSearchSeconds=1):
            # Fuzzy match: search children of root for partial title
            try:
                child = auto.GetRootControl().GetFirstChildControl()
                while child:
                    if title_substring.lower() in (child.Name or "").lower():
                        win_found = child
                        found_flag[0] = True
                        deadline = time.monotonic() + timeout_seconds
                        _walk(win_found, elements, 0, max_depth, max_elements, set(), deadline)
                        return
                    try:
                        child = child.GetNextSiblingControl()
                    except Exception:
                        break
            except Exception:
                pass
            return  # Not found

        found_flag[0] = True
        deadline = time.monotonic() + timeout_seconds
        _walk(win, elements, 0, max_depth, max_elements, set(), deadline)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds + 2)  # extra 2s for window search

    if not found_flag[0] and not t.is_alive():
        return None

    if t.is_alive():
        print(f"[Screen Plugin] UIA tree walk timed out, returning {len(elements)} elements collected so far.")

    return elements


def _to_context(elements: list) -> str:
    """Flatten elements into a compact text block for LLM context."""
    lines = []
    for e in elements:
        if e.text.strip():
            lines.append(f"[{e.control_type}] {e.text}")
    return "\n".join(lines) if lines else "(No visible UI elements found)"


def _to_json(elements: list) -> str:
    """Convert elements to detailed JSON with bbox and enabled state."""
    import json
    return json.dumps([asdict(e) for e in elements], indent=2)


# ---------------------------------------------------------------------------
# Tool functions — registered with the Brain
# ---------------------------------------------------------------------------

def screen_read(window_title: str = "") -> str:
    """Reads what is currently visible on screen using UI Automation. Returns a text summary of all visible controls and text.
    This is the fastest way to understand what the user sees. If window_title is provided, reads that specific window even if it is not focused.
    Otherwise reads the currently focused foreground window."""
    if not UIA_AVAILABLE:
        return "Error: uiautomation package is not installed. Install with: pip install uiautomation"

    try:
        start = time.monotonic()

        if window_title:
            elements = _read_by_title(window_title)
            if elements is None:
                return f"Could not find a window matching '{window_title}'."
        else:
            elements = _read_foreground()

        elapsed = time.monotonic() - start
        context = _to_context(elements)

        # Add metadata
        context += f"\n\n[Read {len(elements)} UI elements in {elapsed:.1f}s]"

        # Hint if very few elements found (might be a canvas/game)
        if len(elements) < 3:
            context += "\n[Note: Very few UI elements detected. If the window contains canvas/game/video content, use screen_capture or screen_read_ocr for better results.]"

        return context
    except Exception as e:
        return f"Error reading screen via UIA: {e}"


def screen_read_detailed(window_title: str = "") -> str:
    """Reads the screen using UI Automation and returns detailed JSON with bounding boxes, control types, and enabled states.
    Use this when you need precise layout information about UI elements. If window_title is provided, reads that specific window."""
    if not UIA_AVAILABLE:
        return "Error: uiautomation package is not installed. Install with: pip install uiautomation"

    try:
        if window_title:
            elements = _read_by_title(window_title)
            if elements is None:
                return f"Could not find a window matching '{window_title}'."
        else:
            elements = _read_foreground()

        return _to_json(elements)
    except Exception as e:
        return f"Error reading screen via UIA: {e}"


def screen_capture() -> str:
    """Captures the current screen as a screenshot and returns it as a base64 encoded image.
    Also saves a local copy to the screenshots/ directory. Use this when you need visual/image analysis
    or when the user explicitly asks for a screenshot."""
    try:
        from PIL import Image

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


def screen_read_ocr(region_left: int = 0, region_top: int = 0,
                    region_right: int = 0, region_bottom: int = 0) -> str:
    """OCR fallback for reading text from canvas, games, or custom-drawn UI that UIA cannot see.
    Provide screen coordinates (left, top, right, bottom) to read a specific region.
    If all coordinates are 0, reads the entire screen. Requires pytesseract and Tesseract binary."""
    try:
        import mss
        import pytesseract
        from PIL import Image

        with mss.mss() as sct:
            if region_left == 0 and region_top == 0 and region_right == 0 and region_bottom == 0:
                monitor = sct.monitors[1]  # Full primary screen
            else:
                monitor = {
                    "left": region_left,
                    "top": region_top,
                    "width": region_right - region_left,
                    "height": region_bottom - region_top,
                }
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        text = pytesseract.image_to_string(img)
        return text.strip() if text.strip() else "(No text detected via OCR)"
    except ImportError as e:
        return f"Error: OCR dependencies not installed ({e}). Install with: pip install mss pytesseract pillow (and install Tesseract binary)"
    except Exception as e:
        return f"Error performing OCR: {e}"


def screen_find(image_path: str) -> str:
    """Finds an image on the screen and returns its coordinates. image_path should be a relative path."""
    try:
        location = pyautogui.locateOnScreen(image_path)
        if location:
            return f"Found at x={location.left}, y={location.top}, width={location.width}, height={location.height}"
        return "Image not found on screen."
    except Exception as e:
        return f"Error finding image: {e}"


def screen_get_resolution() -> str:
    """Returns the current screen resolution as width x height."""
    width, height = pyautogui.size()
    return f"Screen resolution: {width}x{height}"


def screen_get_mouse_position() -> str:
    """Returns the current mouse cursor position as x, y coordinates."""
    x, y = pyautogui.position()
    return f"Mouse position: {x}, {y}"


def screen_get_active_window() -> str:
    """Returns the title of the currently active window."""
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
