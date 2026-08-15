# Ultron Desktop Assistant

Welcome to Ultron! Ultron is an advanced, voice-activated AI desktop assistant built to manage your Windows system, automate browser tasks, manage Docker containers, and interact with your personal Gmail.

## 🚀 Quick Start Guide

### 1. Install Dependencies
Make sure you have Python 3.10+ installed. Then, install the required packages:
```bash
pip install -r requirements.txt
```

### 2. Configure Your Settings & API Keys

Ultron features a dynamic configuration system via `settings.json` and a separate `keys.json` file for your secure API keys.

#### Step A: Set up API Keys in `keys.json`
Create a `keys.json` file in the root directory and paste the following:
```json
{
  "openrouter": "your_openrouter_key_here",
  "google": "your_gemini_api_key_here"
}
```

#### Step B: Configure [`settings.json`](file:///c:/Users/dilsh/OneDrive/Desktop/PROJECT-2/settings.json)
Customize your AI provider, model selection, microphone mode, and background cron jobs in [`settings.json`](file:///c:/Users/dilsh/OneDrive/Desktop/PROJECT-2/settings.json):
```json
{

  "openrouterapi": false,
  "geminiapi": true,
  "localapi": false,
  "openrouter_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
  "gemini_model": "gemini-3.5-flash",
  "local_model": "gemma4:e2b",
  "microphone_active": false,
  "cron_jobs": {
    "unread_emails_check": {
      "enabled": true,
      "interval_seconds": 3600,
      "notify_popup": true
    }
  }
}
```


- **`openrouterapi` / `geminiapi` / `localapi`**: Set one to `true` to select your active AI provider.
- **`microphone_active`**: Set to `true` for hands-free voice control or `false` for keyboard-only mode.
- **`cron_jobs`**: Configure automated background scheduled tasks (e.g. hourly unread email checks).

### 3. Run Ultron
Ultron ships with two front-ends over the same engine. Pick either:

```bash
python gui.py     # desktop overlay: floating orb + system tray
python main.py    # console mode
```

#### Running without a terminal
Ultron is meant to sit there all day, so it does not need a console window kept open. Once:

```bash
python install_shortcuts.py            # desktop shortcut
python install_shortcuts.py --startup  # ...and launch it when Windows starts
python install_shortcuts.py --remove   # undo both
```

The shortcut points at this virtual environment's `pythonw.exe`, so nothing has to be activated and no terminal appears. You can also toggle **Start Ultron when Windows starts** in the settings screen.

Launching a second time just brings the running orb forward rather than starting a rival copy with its own microphone.

With no console there is nowhere for errors to print, so everything Ultron would have written goes to **`data/ultron.log`** (trimmed automatically at 2 MB). Check there first if it does not appear.

Ultron will automatically download the offline Piper Neural TTS voice model on its first run, calibrate your room's ambient noise (if microphone is active), and start processing!

#### The desktop overlay
A floating orb that sits above everything else on screen. It has no chat window — messages appear as cards beside it and fade away on their own.

**The orb tells you what Ultron is doing**, and every state is driven by a real signal rather than a canned animation:

| State | What you see | What drives it |
|---|---|---|
| Idle | Dim, slowly breathing | — |
| Listening | Rings pushing outward | Your microphone's live level |
| Thinking | A dot orbiting the rim | A request in flight |
| Using a tool | Amber arc sweeping, tool named underneath | The actual tool being called |
| Speaking | Swells and glows with the voice | Ultron's own audio, sampled as it plays |

**Cards appear directly below the orb**, newest first, so whatever Ultron is saying right now sits closest to it. Near the bottom of the screen the stack flips and grows upward instead, and it stays clear of the screen edges wherever you park the orb.

They are labelled by where they came from, so a due reminder never looks like a passing "just a second, sir":

| Card | Shown for |
|---|---|
| `YOU` | What you typed |
| `YOU · SPOKEN` | What the microphone heard — check here if it misheard you |
| `YOU · QUEUED` / `YOU · SPOKEN · QUEUED` | Input accepted while Ultron was mid-turn, waiting its turn |
| `ULTRON` | The answer to your request |
| `REMINDER` | A scheduled reminder falling due (amber, stays ~26s, click to dismiss) |
| `SCHEDULED` | Background jobs reporting in, e.g. the unread-email check |
| *(unlabelled)* | Loading phrases and diagnostics — dim and short-lived |

Reminders and scheduled results outlive ordinary chatter and are the last to be pushed off a busy stack, so a burst of replies can't bury one before you read it.

**Controls:** click the orb to type, drag it anywhere (it remembers where), right-click for the menu — microphone, stop talking, settings, hide, quit. The tray icon toggles it. Everywhere except the orb itself is click-through, so it never blocks the app underneath.

**Settings** covers everything in this README — provider and model, API keys, microphone, truth mode, the agent monitor, and Gmail authorization — so you never have to edit a JSON file by hand. Changing the provider or model applies immediately, without a restart.

On a fresh install the overlay opens settings directly and tells you what is still missing, so you can configure Ultron entirely from the UI.

> **Note on config files:** `settings.default.json` is the tracked template; your own `settings.json` overrides it and is gitignored. Any setting you leave out simply falls back to the template.

---

## 📧 Gmail Integration Setup

Ultron can read, draft, and send emails directly from your Gmail account. Because Google has strict security for personal data, you must configure a private OAuth credential file for Ultron to use. 

Follow these steps to set up the Gmail plugin:

### Step 1: Create a Google Cloud Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project dropdown in the top left and select **New Project**.
3. Name it "Ultron Assistant" (or anything you like) and click **Create**.

### Step 2: Enable the Gmail API
1. In your new project, navigate to **APIs & Services > Library**.
2. Search for **"Gmail API"** and click on it.
3. Click the blue **Enable** button.

### Step 3: Configure the OAuth Consent Screen
1. Go to **APIs & Services > OAuth consent screen**.
2. Select **External** (unless you have a Google Workspace) and click **Create**.
3. Fill in the required fields (App name: "Ultron", User support email, Developer contact info).
4. **Important**: Go to the **Test users** section and add your personal `@gmail.com` address. If you don't do this, Google will block you with an "Access denied" error.
5. Save and continue.

### Step 4: Generate Credentials
1. Go to **APIs & Services > Credentials**.
2. Click **+ CREATE CREDENTIALS** at the top and select **OAuth client ID**.
3. Select **Desktop app** as the Application type. Name it "Ultron Desktop".
4. Click **Create**.
5. You will see a popup with your Client ID and Client Secret. Click **DOWNLOAD JSON**.
6. Rename the downloaded file to exactly `credentials.json`.
7. Move `credentials.json` into the root directory of this project (the same folder as `main.py`).

### Step 5: Authorize Ultron
1. Run `python main.py`.
2. Ask Ultron: *"Check my unread emails"* or *"Send an email"*.
3. A browser window will automatically pop up asking you to log into your Google account.
4. Because the app is in testing mode, Google will show a warning: *"Google hasn't verified this app."* Click **Advanced**, and then click **Go to Ultron (unsafe)**.
5. Click **Allow** to grant Ultron permission to manage your emails.
6. A `token.json` file will automatically be created in the project folder. You will never have to log in again!

*(Note: Both `credentials.json` and `token.json` contain sensitive access keys and are automatically ignored by Git so they won't be uploaded anywhere).*
