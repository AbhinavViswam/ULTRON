import os
import io
import time
import base64
import threading
import collections
from typing import Optional, List, Dict, Any

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

try:
    import mss
except ImportError:
    mss = None

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import uiautomation as auto
except ImportError:
    auto = None

from ultron.config import config
from openai import OpenAI

# Rolling buffer of the last N frames (e.g. 15 frames at 1 fps = 15 seconds)
FRAME_BUFFER_SIZE = 15
_frame_buffer = collections.deque(maxlen=FRAME_BUFFER_SIZE)
_watcher_running = False
_watcher_thread = None

def _watcher_loop():
    global _watcher_running
    if not mss or not Image:
        print("[Watcher] Missing mss or Pillow. Cannot start background watcher.")
        return
        
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        while _watcher_running:
            try:
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                
                # Convert to base64
                buffered = io.BytesIO()
                img.thumbnail((1920, 1080)) # Downscale slightly if needed to save RAM
                img.save(buffered, format="JPEG", quality=70)
                img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                
                _frame_buffer.append({
                    "timestamp": time.time(),
                    "image_b64": img_b64
                })
            except Exception as e:
                print(f"[Watcher] Error capturing frame: {e}")
            time.sleep(1.0) # 1 frame per second

def start_watcher():
    global _watcher_running, _watcher_thread
    if _watcher_running:
        return "Watcher is already running."
    
    _watcher_running = True
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True)
    _watcher_thread.start()
    return "Background Screen Watcher started."

def stop_watcher():
    global _watcher_running
    if not _watcher_running:
        return "Watcher is not running."
    
    _watcher_running = False
    return "Background Screen Watcher stopped."

def get_recent_frames(seconds: int = 5) -> List[Dict[str, Any]]:
    """Returns frames from the last N seconds."""
    cutoff = time.time() - seconds
    return [f for f in _frame_buffer if f["timestamp"] >= cutoff]

# ---------------------------------------------------------------------------
# Set-of-Mark (SoM) Logic
# ---------------------------------------------------------------------------

_SKIP_CONTROLS = {"SeparatorControl", "ScrollBarControl", "ThumbControl", "PaneControl", "WindowControl"}

def _get_ui_elements():
    """Walks the UIA tree and returns a list of clickable elements with their bounding boxes."""
    if not auto:
        return []
    
    elements = []
    seen = set()
    
    def walk(control, depth=0, max_depth=8):
        if depth > max_depth or len(elements) >= 200:
            return
            
        try:
            control_type = control.ControlTypeName or ""
            name = control.Name or ""
            
            if control_type not in _SKIP_CONTROLS:
                r = control.BoundingRectangle
                if r.right > r.left and r.bottom > r.top and r.right - r.left > 10:
                    dedup = f"{r.left},{r.top},{r.right},{r.bottom}"
                    if dedup not in seen:
                        elements.append({
                            "id": len(elements) + 1,
                            "type": control_type,
                            "name": name,
                            "bbox": (r.left, r.top, r.right, r.bottom),
                            "center": ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                        })
                        seen.add(dedup)
        except Exception:
            pass
            
        try:
            child = control.GetFirstChildControl()
            while child and len(elements) < 200:
                walk(child, depth + 1, max_depth)
                try:
                    child = child.GetNextSiblingControl()
                except:
                    break
        except:
            pass
            
    try:
        import ctypes
        ctypes.windll.ole32.CoInitialize(None)
        root = auto.GetRootControl()
        walk(root)
    except Exception as e:
        print(f"[Watcher] UIA walk error: {e}")
    finally:
        try:
            ctypes.windll.ole32.CoUninitialize()
        except:
            pass
            
    return elements

def _annotate_and_encode(elements) -> str:
    """Takes a screenshot, draws SoM boxes, and returns base64 JPEG."""
    if not mss or not Image:
        return ""
        
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()
        
    for el in elements:
        x1, y1, x2, y2 = el["bbox"]
        # Draw red bounding box
        draw.rectangle([x1, y1, x2, y2], outline="red", width=2)
        # Draw ID text background
        text_id = str(el["id"])
        bbox = draw.textbbox((x1, y1), text_id, font=font)
        draw.rectangle(bbox, fill="red")
        draw.text((x1, y1), text_id, fill="white", font=font)
        
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=80)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def _call_vision_model(prompt: str, image_b64: str) -> str:
    """Calls the configured Vision model."""
    provider = config.get("vision_provider", config.active_provider())
    model = config.get("vision_model", "")
    
    if provider == "localapi":
        # Ollama
        api_url = config.get("local_api_url", "http://localhost:11434/v1")
        if not model:
            model = "moondream"
        client = OpenAI(base_url=api_url, api_key="ollama")
    else:
        # OpenRouter or Gemini
        # Prefer the dedicated vision key, fallback to the provider's key
        key_name = "openrouter" if provider == "openrouterapi" else "google"
        api_key = config.get_key("vision") or config.get_key(key_name)
        
        base_url = "https://openrouter.ai/api/v1" if provider == "openrouterapi" else None
        
        if provider == "geminiapi":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            
        client = OpenAI(base_url=base_url, api_key=api_key)
        
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            }
        ],
        max_tokens=300
    )
    
    return response.choices[0].message.content

# ---------------------------------------------------------------------------
# Tool: screen_agent_act
# ---------------------------------------------------------------------------

def watch_screen_and_act(goal: str) -> str:
    """Analyzes the screen and takes physical actions (mouse/keyboard) to achieve the goal.
    Args:
        goal: What you want to do (e.g. 'find the search button', 'click the start menu').
    """
    if not pyautogui:
        return "pyautogui is required. pip install pyautogui"
        
    elements = _get_ui_elements()
    if not elements:
        return "Could not find any UI elements on screen."
        
    el_map_text = "\\n".join([f"ID {el['id']}: {el['type']} '{el['name']}'" for el in elements if el['name'] or el['type']])
    
    image_b64 = _annotate_and_encode(elements)
    if not image_b64:
        return "Failed to capture screen."
        
    prompt = f"""You are a Screen Watcher Agent. The user wants to: {goal}
I have provided a screenshot of the user's screen with numbered red boxes (Set-of-Mark) over clickable elements.
Here is the text map of some elements:
{el_map_text}

Analyze the screenshot and the goal. 
If you need to click an element, output EXACTLY in this format: CLICK [ID] (e.g. CLICK 14)
If you need to type text, output EXACTLY: TYPE [ID] "text" (e.g. TYPE 12 "hello")
If you need to just move the mouse to point at it, output: POINT [ID]
If the goal is just a question about what is on screen, answer it naturally.
Do not output markdown code blocks for commands. Output the command on its own line.
"""

    try:
        result = _call_vision_model(prompt, image_b64)
    except Exception as e:
        return f"Error calling Vision model: {e}"
        
    lines = result.split('\\n')
    action_taken = False
    
    for line in lines:
        line = line.strip()
        if line.startswith("CLICK "):
            try:
                el_id = int(line.split(" ")[1])
                el = next((e for e in elements if e["id"] == el_id), None)
                if el:
                    pyautogui.click(*el["center"])
                    action_taken = True
                    return f"Clicked element {el_id} ({el['name']}). Model reasoning: {result}"
            except:
                pass
        elif line.startswith("TYPE "):
            try:
                parts = line.split(" ", 2)
                el_id = int(parts[1])
                text = parts[2].strip('"')
                el = next((e for e in elements if e["id"] == el_id), None)
                if el:
                    pyautogui.click(*el["center"])
                    time.sleep(0.1)
                    pyautogui.write(text)
                    pyautogui.press('enter')
                    action_taken = True
                    return f"Typed '{text}' into element {el_id}. Model reasoning: {result}"
            except:
                pass
        elif line.startswith("POINT "):
            try:
                el_id = int(line.split(" ")[1])
                el = next((e for e in elements if e["id"] == el_id), None)
                if el:
                    pyautogui.moveTo(*el["center"])
                    action_taken = True
                    return f"Pointed to element {el_id} ({el['name']}). Model reasoning: {result}"
            except:
                pass
                
    if not action_taken:
        return f"Model responded, but no physical action was taken. Response: {result}"
        
    return result
