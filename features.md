# Ultron Desktop Assistant - Features & Architecture

Welcome to the comprehensive feature documentation for **Ultron**, your advanced, highly intelligent hands-free desktop AI assistant.

---

## 🧠 Core AI & Voice Architecture

### 1. AI Engine (OpenRouter)
- **OpenRouter Integration:** Connects exclusively to OpenRouter (`openrouter.ai/api/v1`).
- **Models Supported:** `nvidia/nemotron-3-ultra-550b-a55b:free` (Configurable via `brain.py`).
- **Dynamic Tool Bridge (`ToolBridge`):** Converts Python function signatures dynamically into OpenAI-compliant JSON tool schemas via `inspect.signature`.

### 2. Offline Piper Neural Voice Engine (`VoiceSpeaker`)
- **Fully Offline TTS:** Powered by Piper TTS, running entirely on your local CPU for maximum privacy and zero latency.
- **Custom Neural Voices:** Uses the high-quality `en_US-bryce-medium` voice by default. Automatically downloads models to `resources/voices/` on first run.
- **Instant Streaming Playback:** Bypasses file generation and streams raw PCM audio chunks directly to your speakers using `sounddevice` for near-instant (0.25s) voice response.
- **Text Sanitization:** Automatically strips markdown symbols (`**`), code blocks, URLs, and emojis before speaking.

### 3. Continuous Background Voice Listener (`VoiceListener`)
- **Hands-Free Microphone Stream:** Non-blocking background microphone energy stream powered by `sounddevice` and `SpeechRecognition`.
- **Dynamic Ambient Noise Calibration:** Spends 0.8s on startup measuring room noise floor and automatically sets an adaptive speech energy threshold (`1.5x` room ambient noise).
- **Google Neural Speech-to-Text:** Transcribes spoken audio phrases to text in real-time.

### 4. Thread-Safe Queue Event Loop (`main.py`)
- **Unified Event Pipeline (`queue.Queue`):** Manages incoming voice commands and keyboard inputs in a thread-safe Queue.
- **Zero-Enter Keypress:** Voice commands trigger Ultron's response loop instantly without waiting for an Enter keypress or hanging input prompts.

### 5. Hardcoded Sleep & Wake Mode (`is_asleep`)
- **Zero-Latency Hardcoded Sleep:** Triggered by *"go to sleep"*, *"take a nap"*, or *"take a rest"*.
- **Total Silence:** While asleep, Ultron remains 100% silent and makes zero LLM API calls for background noise or typed text.
- **Instant Wakeup:** Wakes up immediately when *"wake up"* or *"get up"* is spoken.

### 6. Respectful Persona
- **Sir Rule:** Ultron always addresses the user respectfully as *"sir"* in every response.

---

## 🛠️ Desktop Automation & Tool Suite

### 1. Complete Desktop Application Ecosystem
- **Universal Launcher (`open_application`):** Launches mapped apps or dynamically resolves Windows executables via shell (`start`).
- **Universal App Killer (`close_application`):** Forcefully terminates app processes via `taskkill /F /IM` or window title matching.
- **Supported Apps:** 
  - ⚙️ **Settings & Utilities:** Windows Settings (`ms-settings:`), Control Panel, Task Manager, Calculator, CMD, PowerShell, Windows Terminal, Device Manager, Snipping Tool.
  - 📊 **Office:** Excel, Word, PowerPoint, Outlook, OneNote.
  - 🌐 **Browsers:** Chrome, Edge, Brave, Firefox, Opera.
  - 💬 **Messaging & Media:** WhatsApp, Discord, Telegram, Teams, Zoom, Spotify, VLC.
  - 🎨 **Creative & Coding:** VSCode, Notepad, Paint, Photoshop, Premiere, Blender, Docker, PGAdmin, Antigravity.
  - 🎮 **Gaming:** Steam, Epic Games, Minecraft.

### 2. Deep App Writing & Messaging
- **Direct Notepad Typing (`write_in_notepad`):** Opens Notepad, types out text directly into the window, and optionally saves to Desktop.
- **WhatsApp Desktop Messaging (`send_whatsapp_message`):** Opens WhatsApp Desktop, searches for contacts, and sends messages.

### 3. PC Health & Hardware Monitor (`get_system_health`)
- **Resource Stats:** Powered by `psutil`, reports CPU utilization %, RAM used/total, Storage free space, and Battery charging status %.

### 4. Clipboard Engine (`read_clipboard`, `copy_to_clipboard`)
- **Windows Clipboard Management:** Reads, inspects, or copies any text, link, URL, or note directly to your Windows Clipboard (`pyperclip`).

### 5. File Finder & Document Reader (`find_files`, `read_file_content`)
- **File Search:** Locates files on Desktop, Downloads, or Documents folders.
- **Text File Reader:** Reads content from `.txt`, `.md`, `.py`, `.json`, or `.csv` files.

### 6. System Power Controls (`system_power_control`)
- **Power Actions:** Lock workstation (`LockWorkStation`), put PC to sleep (`SetSuspendState`), schedule system shutdown, or cancel a scheduled shutdown.

### 7. Docker Container Management (`docker_plugin`)
- **Lifecycle Control:** Start, stop, and forcefully remove containers by name or ID.
- **Run & Inspect:** Run new containers in the background from local images, list all downloaded images with their sizes, and list running/stopped containers with their status.

### 8. Desktop Screenshots (`take_screenshot`)
- **Triple-Layer Capture Engine:** 
  1. PIL `ImageGrab.grab(all_screens=True)`
  2. PyAutoGUI `screenshot()`
  3. Native Windows PowerShell `.NET System.Drawing` Graphics capture.
- Saves captures to `screenshots/` directory.

### 9. System Media & Volume Controls (`system_media_control`, `adjust_volume`, `search_spotify`)
- **Global Media Controls:** Controls global Windows media (Play, Pause, Skip, Previous).
- **Volume Controls:** Adjusts system volume (Volume Up, Volume Down, Mute).
- **Spotify Integration:** Searches Spotify for an artist, song, or album and opens it directly in the desktop app.

### 10. Interactive Playwright Web Browser (`BrowserManager`)
- Navigates web pages, searches Google, reads page text, clicks buttons, types text, scrolls, and navigates history in an isolated Playwright Chromium instance.

---

## 💾 Memory & Reminders Engine

### 1. SQLite Local Database (`data/ultron.db`)
- **Memory Storage (`save_memory`, `search_memories`):** Stores user facts, preferences, and details with smart keyword search and stop-word filtering.
- **Chat Archive (`search_past_conversations`):** Logs user inputs and model responses by unique session ID.

### 2. Native Windows Reminders (`set_reminder`)
- **Background Daemon (`reminder_worker`):** Persistent background thread checking pending ISO 8601 tasks.
- **Native OS Popups:** Displays native Windows `MessageBoxW` alerts and purges tasks immediately upon trigger.
