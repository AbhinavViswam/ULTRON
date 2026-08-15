# Setting up Ultron

Ultron is a voice-driven desktop assistant for Windows. It sits on your screen
as a floating orb, listens, answers out loud, and can drive apps, files, your
browser and Gmail for you.

## 1. Install Python (once)

Ultron needs **Python 3.10 or newer**. If you already have it, skip this.

1. Download it from <https://www.python.org/downloads/>
2. During installation, tick **"Add python.exe to PATH"** — this matters
3. Finish the installer

## 2. Run the setup

Double-click **`setup.bat`**.

It creates a private environment for Ultron and downloads what it needs. That
is roughly **1.5 GB**, so it takes a few minutes. Leave the window open until
it says setup is complete. If it fails partway, just run it again — it carries
on from where it stopped.

It will ask whether to install a browser engine (another ~300 MB). That is only
needed for the web-browsing features; everything else works without it.

When it finishes you will have an **Ultron** shortcut on your desktop.

## 3. Add your own API key

Ultron talks to a large language model, and you need your own key for that.
A free one takes a minute:

1. Go to <https://openrouter.ai/keys> and create a key
2. Launch **Ultron** from the desktop
3. It opens on the settings screen and tells you the key is missing
4. Paste the key into **OpenRouter**, then press **Save**

Ultron restarts its connection immediately — no need to close anything.

> Prefer Google's models? Choose **Gemini** as the provider and paste a key
> from <https://aistudio.google.com/app/apikey> instead.

## 4. Using it

- **Click the orb** to type a question
- **Speak** — the microphone is on by default, and the orb ripples when it
  hears you
- **Drag** the orb anywhere; it remembers where you put it
- **Right-click** it for the microphone toggle, settings, hide and quit
- The tray icon brings it back after hiding

The orb shows what it is doing: dim when idle, rippling while listening, a
circling dot while thinking, an amber sweep with a label while it uses a tool,
and glowing in time with its own voice while speaking.

## Optional extras

**Start with Windows** — tick it in the settings screen.

**Gmail** — reading and sending email needs Google credentials of your own.
In Google Cloud Console create an OAuth 2.0 **Desktop App** credential,
download the JSON, then use **Choose credentials.json…** in Ultron's settings
and press **Connect**.

**Screen reading (OCR)** — install Tesseract from
<https://github.com/UB-Mannheim/tesseract/wiki> if you want Ultron to read
text off your screen.

## If something goes wrong

Ultron runs without a console, so anything it would have printed goes to:

```
data\ultron.log
```

Check there first. If the orb does not appear at all, open that file and look
at the last few lines.
