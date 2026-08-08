# Ultron Desktop Assistant

Welcome to Ultron! Ultron is an advanced, voice-activated AI desktop assistant built to manage your Windows system, automate browser tasks, manage Docker containers, and interact with your personal Gmail.

## 🚀 Quick Start Guide

### 1. Install Dependencies
Make sure you have Python 3.10+ installed. Then, install the required packages:
```bash
pip install -r requirements.txt
```

### 2. Configure Your Settings & API Provider (`settings.json` & `.env`)
Ultron features a dynamic configuration system via [`settings.json`](file:///c:/Users/dilsh/OneDrive/Desktop/PROJECT-2/settings.json).

#### Step A: Set up API Keys in `.env`
Create a `.env` file in the root directory:
```env
OPENROUTER_API_KEY=your_openrouter_key_here
GEMINI_API_KEY=your_gemini_api_key_here
```

#### Step B: Configure [`settings.json`](file:///c:/Users/dilsh/OneDrive/Desktop/PROJECT-2/settings.json)
Customize your AI provider, model selection, microphone mode, and background cron jobs:
```json
{
  "openrouterapi": false,
  "geminiapi": true,
  "openrouter_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
  "gemini_model": "gemini-3.5-flash",
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

- **`openrouterapi` / `geminiapi`**: Set one to `true` to select your active AI provider.
- **`gemini_model` / `openrouter_model`**: Choose the exact AI model you want to use.
- **`microphone_active`**: Set to `true` for hands-free voice control or `false` for keyboard-only mode.
- **`cron_jobs`**: Configure automated background scheduled tasks (e.g. hourly unread email checks).

### 3. Run Ultron
To start the assistant, simply run:
```bash
python main.py
```
Ultron will automatically download the offline Piper Neural TTS voice model on its first run, calibrate your room's ambient noise (if microphone is active), and start processing!

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
