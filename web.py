import os
import sys
import threading
import queue
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure we can import from the ultron package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ultron.config import config
from ultron.core import UltronCore
from ultron.launcher import make_output_safe

make_output_safe()

import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global core reference
core = None
connected_websockets = set()
message_queue = queue.Queue()

async def broadcast_worker():
    """Background async task that drains the thread-safe queue and broadcasts."""
    while True:
        # Check queue without blocking event loop
        while not message_queue.empty():
            msg = message_queue.get()
            disconnected = set()
            for ws in connected_websockets:
                try:
                    await ws.send_json(msg)
                except Exception:
                    disconnected.add(ws)
            for ws in disconnected:
                connected_websockets.remove(ws)
        await asyncio.sleep(0.1)

def on_core_message(text: str, source: str):
    """Callback fired when Ultron speaks."""
    message_queue.put({"type": "bot_message", "data": {"text": text, "source": source}})

def on_user_message(text: str, origin: str, queued: bool):
    """Callback fired when user input is processed (including voice)."""
    if origin != "web":
        message_queue.put({"type": "user_message", "data": {"text": text, "origin": origin, "queued": queued}})

def on_level(level: float):
    """Callback fired continuously with 0.0-1.0 audio volume levels."""
    message_queue.put({"type": "audio_level", "data": {"level": level}})

def on_confirmation(question: str, decide_callback):
    """Callback fired when a tool needs confirmation."""
    app.state.pending_decide = decide_callback
    message_queue.put({"type": "confirmation_request", "data": {"question": question}})

def on_tool_event(phase: str, name: str, detail=None):
    """Callback fired when a tool starts or stops."""
    message_queue.put({"type": "tool_event", "data": {"phase": phase, "name": name}})

def on_state_changed(state: str, detail=None):
    """Callback fired when the AI changes states (idle, listening, thinking, speaking)."""
    message_queue.put({"type": "state_change", "data": {"state": state}})

@app.on_event("startup")
async def startup_event():
    global core
    print("Initializing UltronCore for Web UI...")
    problems = config.missing_requirements()
    if problems:
        print("\n[SETUP REQUIRED]")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)

    try:
        core = UltronCore(echo_to_console=True)
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    # Wire up the core messages to the websocket queue
    core.on_assistant_message(on_core_message)
    core.on_user_message(on_user_message)
    core.on_confirmation_request(on_confirmation)
    core.on_tool_event(on_tool_event)
    core.on_state_changed(on_state_changed)
    core.on_level(on_level)
    
    app.state.pending_decide = None
    core.start()
    
    # Start the async broadcast worker
    asyncio.create_task(broadcast_worker())
    
    print("Ultron Web Server Online!")

@app.on_event("shutdown")
def shutdown_event():
    global core
    if core:
        core.shutdown()

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.add(websocket)
    
    try:
        if core:
            await websocket.send_json({
                "type": "init_state",
                "data": {
                    "mic_active": core.microphone_active,
                    "bot_state": core.state
                }
            })
            
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "chat":
                text = data.get("text", "")
                if text.strip():
                    core.submit(text.strip(), origin="web")
            elif data.get("type") == "toggle_mic":
                active = data.get("active", False)
                core.set_microphone(active)
                message_queue.put({"type": "bot_message", "data": {"text": f"[System] Microphone {'activated' if active else 'deactivated'}."}})
    except WebSocketDisconnect:
        connected_websockets.remove(websocket)

class ConfirmRequest(BaseModel):
    approved: bool

@app.post("/api/confirm")
async def confirm_action(req: ConfirmRequest):
    decide = app.state.pending_decide
    if decide:
        app.state.pending_decide = None
        decide(req.approved)
        return {"status": "ok"}
    return {"status": "no pending confirmation"}

class SettingsUpdate(BaseModel):
    settings: dict = {}
    keys: dict = {}

@app.get("/api/settings")
async def get_settings():
    from ultron.config import config
    all_settings = config.all()
    keys_obj = {}
    for p in ["openrouter", "google", "groq", "vision"]:
        k = config.get_key(p)
        keys_obj[p] = k if k else ""
    return {"settings": all_settings, "keys": keys_obj}

@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    from ultron.config import config
    if req.settings:
        config.update(req.settings)
    if req.keys:
        for k, v in req.keys.items():
            config.set_key(k, v)
    return {"status": "ok"}

# Mount the built React app (if it exists)
ui_path = os.path.join(os.path.dirname(__file__), "ui-web", "dist")
if os.path.exists(ui_path):
    app.mount("/", StaticFiles(directory=ui_path, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("web:app", host="0.0.0.0", port=8000, reload=True)
