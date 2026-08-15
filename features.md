# Ultron Desktop Assistant - Features & Architecture

Welcome to the comprehensive feature documentation for **Ultron**, your advanced, highly intelligent hands-free desktop AI assistant.

---

## 🖥️ Desktop UI (PySide6 Orb Overlay)

### 1. The Orb (`ultron/ui/orb.py`)
- **Always-On-Top Floating Bubble:** A frameless, translucent, borderless window that sits above every other app — no taskbar entry, no window chrome.
- **Voice-Driven Animation:** The orb pulses in real time from actual audio energy — the microphone's RMS while you speak, and Piper's own PCM slices while Ultron speaks. It is not a canned animation; it follows the waveform.
- **Five Visual States:**
  - **Idle** — dim, slowly breathing
  - **Listening** — rippling in time with your voice
  - **Thinking** — a dot orbiting the rim
  - **Tool** — an amber sweep with the tool's plain-English label underneath ("searching the web", "opening an app")
  - **Speaking** — a glow that swells with each spoken syllable
- **Muted Badge:** A distinct struck-through microphone badge is drawn on the orb whenever the mic is off, so active and inactive are unmistakable at a glance.
- **Click-Through Background:** A `QRegion` ellipse mask means only the orb itself catches clicks — the transparent area around it passes them straight to whatever is underneath.
- **Draggable & Persistent:** Drag it anywhere on screen; the position is remembered across restarts.
- **Idle Cost:** The 30 fps repaint timer is gated on show/hide, so a hidden orb costs nothing.

### 2. Floating Message Cards (`ultron/ui/cards.py`)
- **Transient Bubbles, No Transcript:** Messages appear as cards directly below the orb, dwell, then fade — there is no persistent chat log cluttering the desktop.
- **Everything Is Shown:** Your typed input, your transcribed speech, queued commands (visually marked as queued), Ultron's replies, reminders, and scheduled/system messages each get their own card style.
- **Smart Stacking:** The stack grows downward from the orb and automatically flips upward when the orb is near the bottom of the screen.
- **Auto-Sizing:** Cards measure their own text and grow to fit, so nothing is clipped regardless of reply length.
- **Long-Lived Roles:** Reminders and scheduled messages dwell far longer (26s) than ordinary chatter, so you don't miss them.

### 3. Input, Tray & Settings (`ultron/ui/overlay.py`, `settings_window.py`)
- **Click to Type:** Clicking the orb opens an inline input card — full keyboard mode without touching a terminal.
- **System Tray Icon:** Restores the orb after hiding, and stays out of the way otherwise.
- **Right-Click Menu:** Microphone toggle, settings, hide, and quit.
- **Live Settings Window:** Provider switch, model names, API keys, Gmail credentials, and start-with-Windows — all editable from the GUI. Changes take effect immediately; no restart.

### 4. Front-End-Agnostic Runtime (`ultron/core.py`)
- **`UltronCore` Separation:** All assistant logic lives behind a callback interface (`on_assistant_message`, `on_user_message`, `on_state_changed`, `on_level`, `on_busy_changed`, `on_status`). The orb, the terminal, and any future front end are just subscribers.
- **Instant Echo:** User input is announced at submit time, not when it is eventually spoken — the card appears the moment you press Enter.
- **Queue Awareness:** A command sent while Ultron is busy is queued and *labelled* as queued rather than silently swallowed.

---

## 🧠 Core AI & Voice Architecture

### 1. AI Engine (Multi-Provider)
- **Three Providers:** OpenRouter, Google Gemini, and any local OpenAI-compatible server (Ollama, LM Studio) via `local_api_url`.
- **Configurable Models:** Custom model names per provider (`openrouter_model`, `gemini_model`, `local_model`).
- **Hot Provider Swap:** Changing the provider or key in settings reconfigures the client in place through a `config.on_change` listener — no restart.
- **Dynamic Tool Bridge (`ToolBridge`):** Converts Python function signatures dynamically into OpenAI-compliant JSON tool schemas via `inspect.signature`.
- **Truth Mode:** A toggle that instructs the model to say "I don't know" rather than improvise.

### 2. Offline Piper Neural Voice Engine (`VoiceSpeaker`)
- **Fully Offline TTS:** Powered by Piper TTS, running entirely on your local CPU for maximum privacy and zero latency.
- **Custom Neural Voices:** Uses the high-quality `en_US-bryce-medium` voice by default. Automatically downloads models to `resources/voices/` on first run.
- **Instant Streaming Playback:** Bypasses file generation and streams raw PCM audio chunks directly to your speakers using `sounddevice` for near-instant (0.25s) voice response.
- **Sliced Level Metering:** Playback is emitted in 1024-sample frames with a per-slice RMS reading, which is what drives the orb's speaking animation.
- **Text Sanitization:** Automatically strips markdown symbols (`**`), code blocks, URLs, and emojis before speaking.

### 3. Continuous Background Voice Listener (`VoiceListener`)
- **Hands-Free Microphone Stream:** Non-blocking background microphone energy stream powered by `sounddevice` and `SpeechRecognition`.
- **Dynamic Ambient Noise Calibration:** Spends 0.8s on startup measuring room noise floor and automatically sets an adaptive speech energy threshold (`1.5x` room ambient noise).
- **Google Neural Speech-to-Text:** Transcribes spoken audio phrases to text in real-time.
- **Live Level Feed:** Streams microphone energy to the UI so the orb ripples while you talk.

### 4. Priority Output Queue (`OutputManager`)
- **Centralized Speech Coordinator:** All speech output (user responses, cron notifications, system messages) is routed through a single priority-aware queue.
- **User Interrupt Priority:** When the user gives a new command, current speech is immediately stopped and all pending cron notifications are cleared from the queue.
- **Cron Message Queuing:** Background cron notifications (e.g., email alerts) wait in the queue and are spoken only when Ultron is idle — they never interrupt user interactions.
- **Reminders Survive Interrupts:** Reminders are enqueued under their own source so an interrupt never drops them.
- **Display Before Speech:** Messages are announced to the UI at enqueue time, so the card is on screen while the sentence is still being spoken.
- **Drain After Response:** After speaking a user response, Ultron automatically drains any queued cron messages before going idle.

### 5. Thread-Safe Queue Event Loop
- **Unified Event Pipeline (`queue.Queue`):** Manages incoming voice commands and keyboard inputs in a thread-safe Queue.
- **Zero-Enter Keypress:** Voice commands trigger Ultron's response loop instantly without waiting for an Enter keypress or hanging input prompts.

### 6. Hardcoded Sleep & Wake Mode (`is_asleep`)
- **Zero-Latency Hardcoded Sleep:** Triggered by *"go to sleep"*, *"take a nap"*, or *"take a rest"*.
- **Total Silence:** While asleep, Ultron remains 100% silent and makes zero LLM API calls for background noise or typed text.
- **Instant Wakeup:** Wakes up immediately when *"wake up"* or *"get up"* is spoken.

### 7. Respectful Persona
- **Sir Rule:** Ultron always addresses the user respectfully as *"sir"* in every response.

---

## ⚙️ Configuration & Packaging

### 1. Central Config Singleton (`ultron/config.py`)
- **One Source of Truth:** Every path (`settings.json`, `keys.json`, `credentials.json`, `token.json`, `usage.json`, `data/`) is resolved in one module instead of being recomputed relative to whatever file happened to import it.
- **Dotted Access:** `config.get("agent_monitor.port")`, `config.set()`, `config.update()`.
- **Hot Reload:** The settings file's mtime is checked on every access, so edits made by hand or by the settings window are picked up live.
- **Change Listeners:** `config.on_change()` lets subsystems (like the LLM client) reconfigure themselves the instant a relevant setting moves.
- **Key Management:** `get_key()` / `set_key()` keep secrets in `keys.json`, mapped per provider.
- **Startup Validation:** `missing_requirements()` reports exactly what is unconfigured, and the GUI opens straight to settings when something is missing.
- **Defaults File:** `settings.default.json` is committed; your real `settings.json` is git-ignored.

### 2. No-Terminal Launching (`ultron/launcher.py`)
- **Headless Output Redirect:** Under `pythonw.exe` there is no console, so stdout/stderr are redirected to `data/ultron.log` with size-capped rotation (2 MB).
- **Desktop & Startup Shortcuts:** `install_shortcuts.py` creates a real Windows `.lnk` via WScript.Shell, with a generated icon.
- **OneDrive-Aware Folders:** Desktop and Startup paths are read from the registry's *User Shell Folders*, so redirected (OneDrive Known Folder Move) desktops work correctly instead of silently writing to an empty `~/Desktop`.
- **Single Instance:** A `QLocalServer` lock claimed *before* the UI is built prevents duplicate orbs; a second launch summons the existing one instead.
- **Start With Windows:** Toggleable from the settings screen.

### 3. Shareable Bundle (`make_bundle.py`, `setup.bat`, `SETUP.md`)
- **Source Zip + Setup Script:** Produces a zip a friend can unpack and run — `setup.bat` creates the venv, installs dependencies, optionally installs the browser engine, and drops a desktop shortcut.
- **Strict Secret Exclusion:** The bundle is built from an **allowlist** and then re-audited after writing. `keys.json`, `credentials.json`, `token.json`, `usage.json`, `settings.json`, `data/` (database and browser profile/cookies) and `venv/` can never be included.
- **Plain-English Setup Guide:** `SETUP.md` walks a non-developer through Python, setup, adding their own API key, and where to look when something breaks.

### 4. Diagnostics
- **Full Tool Logging:** Every tool invocation is logged with its arguments (`[Tool] open_application({'app_name': 'spotify'})`) to `data/ultron.log`, which is the authoritative record when running without a console.
- **Stuck-Key Rescue (`unstick.py`):** Prints which modifier or mouse button is being held and releases it.

---

## 🛠️ Desktop Automation & Tool Suite

**76 tools across 13 groups.** By default the *entire* toolset is exposed to the model on every turn. A keyword-based group filter exists behind `"filter_local_tools": true` for very small local models with tight context windows, but it is off by default so capability is never silently withheld.

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
- **Guaranteed Key Release:** Both routines release every modifier in a `finally` block, so a failure mid-automation can no longer leave Ctrl or Shift latched down.

### 3. Keyboard Safety Net (`release_stuck_keys`, `keys_currently_held`)
- **The Problem It Solves:** A synthetic keystroke that dies between key-down and key-up leaves the modifier stuck — drag-select starts grabbing whole words, clicks multi-select, `Win+V` stops working.
- **Detection:** `keys_currently_held()` reports exactly which modifiers or mouse buttons the OS believes are down (`GetAsyncKeyState`).
- **Repair:** `release_stuck_keys()` is both a registered Ultron tool ("something's stuck, fix it") and a standalone script (`python unstick.py`).

### 4. Smart Folder & Path Resolution
- **Known Folders:** Desktop, Documents, Downloads and friends are resolved from the registry, so OneDrive-redirected folders work.
- **Fuzzy Folder Search (`find_folders`):** "open ultron folder" finds the folder without a path. Search is depth-limited, skips dot-directories and system trees, prunes duplicates, and enforces a 5-second budget — the naive version took 13.6s, this takes ~0.1s.
- **Natural Phrasing:** Strips filler ("the", "my") and suffixes ("folder", "directory") before searching.
- **No Accidental CWD Matches:** Bare names are only treated as paths when they actually look like one, so `ultron` no longer resolves to a nested `ultron\ultron`.

### 5. PC Health & Hardware Monitor (`get_system_health`)
- **Resource Stats:** Powered by `psutil`, reports CPU utilization %, RAM used/total, Storage free space, and Battery charging status %.

### 6. Clipboard Engine (`read_clipboard`, `copy_to_clipboard`)
- **Windows Clipboard Management:** Reads, inspects, or copies any text, link, URL, or note directly to your Windows Clipboard (`pyperclip`).

### 7. File Management & Directory Explorer (`find_files`, `read_file_content`, `create_file`, `delete_file`, `list_directory`)
- **File Search:** Locates files on Desktop, Downloads, or Documents folders (via the registry-resolved real paths).
- **Document Reader:** Reads text from `.txt`, `.md`, `.py`, `.json`, or `.csv` files.
- **File Creator:** Creates or overwrites text files directly with automated parent directory creation.
- **File Deleter:** Deletes specified files safely from disk.
- **Directory Explorer:** Lists files and subfolders inside any directory.

### 8. System Cleanup & Recycle Bin (`empty_recycle_bin`, `clean_temp_files`)
- **Recycle Bin Cleaner:** Empties the Windows Recycle Bin completely using native `SHEmptyRecycleBinW` API.
- **Temp Cache Cleaner:** Scans and removes temporary files in `%TEMP%` to free up disk space.

### 9. System Power Controls (`system_power_control`)
- **Power Actions:** Lock workstation (`LockWorkStation`), put PC to sleep (`SetSuspendState`), schedule system shutdown, or cancel a scheduled shutdown.

### 10. Docker Container Management (`docker_plugin`)
- **Lifecycle Control:** Start, stop, and forcefully remove containers by name or ID.
- **Run & Inspect:** Run new containers in the background from local images, list all downloaded images with their sizes, and list running/stopped containers with their status.

### 11. Screen Awareness Engine (`screen_plugin`)
- **Visual Context:** Ultron can take screenshots (`screen_capture`) to "see" your current screen, passing the image data to vision-capable models.
- **Persistent Screenshots:** Every captured screenshot is automatically saved with a timestamp to the `screenshots/` directory for your records.
- **Screen Context:** Uses `pygetwindow` to determine the active window title and can also track cursor position or find specific UI elements on screen (`screen_find`).
- **OCR (optional):** Reads text off the screen when Tesseract is installed.

### 12. System Media & Volume Controls (`system_media_control`, `adjust_volume`, `search_spotify`)
- **Global Media Controls:** Controls global Windows media (Play, Pause, Skip, Previous).
- **Volume Controls:** Adjusts system volume (Volume Up, Volume Down, Mute).
- **Spotify Integration:** Searches Spotify for an artist, song, or album and opens it directly in the desktop app.
- **No Silent Playback:** Weak models often call `system_media_control` with no `action`. Rather than defaulting to `play` — which used to start music the moment you said "open Spotify" — the call is now **refused** with an error that steers the model to `open_application` instead. Genuine requests ("play some music", "next song", "pause") still infer correctly.

### 13. Interactive Playwright Web Browser (`BrowserManager`)
- **Full Navigation:** Navigates web pages, searches Google, goes back/forward in history, and scrolls.
- **Multi-Tab Support:** Open new tabs, switch between active tabs, and close tabs dynamically (`new_tab`, `switch_tab`, `close_tab`).
- **Interaction & Extraction:** Clicks elements, types text, presses keys, and reads full page text content.
- **Visual Capture:** Takes full-page screenshots of the current browser tab and saves them to `data/screenshots/`.

### 14. API Usage Logger (`usage.json`)
- **Token Tracker:** Automatically records prompt tokens, completion tokens, total requests, and total tokens after every API interaction.
- **Detailed Categorization:** Groups stats by global total, active provider, and model name.
- **Git Ignored:** Saved locally in `usage.json` (ignored in `.gitignore`).

### 15. Background Cron Scheduler (`CronManager`)
- **Configurable Recurring Tasks:** Configured under `"cron_jobs"` in `settings.json` with custom intervals (e.g. `3600` seconds for 1 hour).
- **Unread Email Checker (`unread_emails_check`):** Periodically checks Gmail inbox for unread email count and displays native, non-blocking Windows Toast Notifications (`winotify`) when new emails arrive.
- **Extensible Action Registry:** Register new cron jobs easily by adding action handlers into `CronManager.register_action(name, func)`.

### 16. Background Research Engine (`research_plugin`)
- **Asynchronous Execution:** Conducts multi-step deep web research in a non-blocking background thread while you continue chatting.
- **Search & Scrape Pipeline:** Automatically queries DuckDuckGo (no API keys required), extracts top URLs, and fetches raw article texts by stripping out noise (ads/scripts/navbars).
- **AI Synthesis:** Passes scraped web content to the LLM to generate a clean, highly structured Markdown report (Executive Summary, Key Findings, Pros & Cons, Recommendations, Sources).
- **Silent File Output:** Saves final `.md` reports directly to `data/research/` and slides a native Windows Toast Notification into view to let you know the report is ready.

### 17. Workflow Engine (`workflow_plugin`)
- **One-Command Automation:** Save named multi-step action sequences (e.g., "RateUp Development Setup") and replay them with a single voice or text command.
- **Flexible Step Format:** Each workflow step maps directly to an existing Ultron tool (e.g., `open_application vscode`, `browser_navigate localhost:3000`, `docker_start_daemon`).
- **Fuzzy Name Matching:** Workflow names are matched case-insensitively with partial matching, so "start rateup" finds "RateUp Development Setup".
- **Sequential Execution:** Steps run one-by-one with a 2-second delay between each to allow apps time to launch.
- **Persistent Storage:** Workflows are saved to `data/workflows.json` and persist across sessions.

### 18. Agent Monitor (`agent_monitor_plugin`)
- **Long-Run Watch:** Watches background agent runs and escalates with an alert when one exceeds its configured thresholds (`min_run_seconds`, `escalate_seconds`, `pending_tool_seconds`).

### 19. Argument Healing
- **Alias Normalisation:** Models invent parameter names. Ultron maps common variants back onto the real signature (`at`/`when`/`clock_time` → `time_str`, `task`/`title`/`note` → `description`, `every`/`repeats` → `frequency`, `delay`/`seconds` → `delay_seconds`).
- **Natural Language Time:** Spoken times ("in ten minutes", "tomorrow at 7") are parsed into concrete schedules rather than rejected.

---

## 💾 Memory & Reminders Engine

### 1. Dual-Store Memory (`ultron/database.py`)
- **SQLite (`data/ultron.db`) — Chronological:** The exact, ordered record. Every user input and model response is logged by session ID.
- **ChromaDB (`data/chroma/`) — Semantic:** Two vector collections, `memories` and `chat_history`, so recall works by *meaning* rather than keyword overlap. Asking "what's my email" finds the fact even if it was stored as "contact address".
- **Graceful Degradation:** If `chromadb` is unavailable, Ultron warns and keeps running on SQLite alone rather than failing to boot.
- **Memory Tools (`save_memory`, `search_memories`):** Stores user facts, preferences, and details with a category, key, value and importance score.
- **Conversation Recall (`search_past_conversations`):** Searches the full archive of previous sessions.

### 2. Priority Windows Reminders (`set_reminder`)
- **Background Daemon (`reminder_worker`):** Persistent background thread checking pending scheduled tasks.
- **AI-Optimized Scheduling:** The AI generates reminders using a simple `delay_seconds` integer instead of complex ISO 8601 strings, drastically improving reliability for local LLMs.
- **Triple-Layer Alert:** A reminder fires a non-blocking Toast Notification (`winotify`), queues the spoken reminder at the highest priority in the `OutputManager` — politely interrupting current speech — and raises a long-dwell card under the orb.
