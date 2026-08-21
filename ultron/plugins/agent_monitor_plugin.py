"""
Agent monitor plugin for Ultron Desktop Assistant.

Watches coding agents (Claude Code in VSCode, Antigravity, etc.) and alerts the
user when one stops, finishes, asks a question, or dies.

Detection is push-based. Claude Code fires lifecycle hooks; a tiny hook script
installed in %USERPROFILE%\\.claude\\ POSTs each event to the local HTTP listener
this module runs on 127.0.0.1:8787. Because the hook lives in the *user-level*
settings.json, every Claude Code session on the machine reports in automatically,
in any IDE — VSCode, Antigravity, or a bare terminal. Run `install_agent_hooks()`
once to set it up.

NOTE ON OTHER AGENTS: monitoring an agent that has no hook API (e.g. Antigravity's
own built-in agent) was attempted by scraping the window via UI Automation and
deliberately removed. Both VSCode and Antigravity are Electron apps that render to
a canvas, so UIA exposes only window chrome — never terminal content — and the
Antigravity tree walk blocked for ~60s. It cannot be made to work this way.

Settings (settings.json -> "agent_monitor"):
    {
      "agent_monitor": {
        "enabled": true,
        "port": 8787,
        "alert_mode": "both",           // "toast" | "voice" | "both"
        "min_run_seconds": 10,          // ignore Stop for turns shorter than this
        "escalate_seconds": 120,        // speak if a WAITING alert is ignored this long
        "alert_when_focused": true,     // false = stay quiet if you're looking at it
        "pending_tool_seconds": 10      // PreToolUse with no PostToolUse = blocked on you
      }
    }
"""

import os
import sys
import json
import time
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ultron.config import config
from ultron.plugins.notification_plugin import send_toast


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8787

# States a watched agent session can be in
RUNNING = "RUNNING"
WAITING = "WAITING"   # asking a question / needs permission
IDLE = "IDLE"         # finished its turn
GONE = "GONE"         # session ended or window disappeared

# Claude Code hook event -> state
_EVENT_STATE = {
    "SessionStart": RUNNING,
    "UserPromptSubmit": RUNNING,
    "PreToolUse": RUNNING,
    "PostToolUse": RUNNING,
    "Notification": WAITING,
    "Stop": IDLE,
    "SessionEnd": GONE,
}

# Events that never alert on their own
_SILENT_EVENTS = {"PreToolUse", "PostToolUse", "SubagentStop", "PreCompact", "SessionStart"}


def _now():
    return time.monotonic()


def _fmt_age(seconds: float) -> str:
    secs = int(seconds)
    if secs < 60:
        return f"{secs} seconds"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    hours = mins // 60
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _label_from_cwd(cwd: str) -> str:
    if not cwd:
        return "unknown"
    return os.path.basename(os.path.normpath(cwd)) or cwd


def _load_settings() -> dict:
    return config.get("agent_monitor", {}) or {}


# ---------------------------------------------------------------------------
# Session record
# ---------------------------------------------------------------------------

class _Session:
    """One watched agent instance."""

    def __init__(self, key, agent, label):
        self.key = key
        self.agent = agent
        self.label = label
        self.state = RUNNING
        self.state_since = _now()
        self.busy_since = _now()        # when the current RUNNING stretch began
        self.last_message = ""
        self.last_seen = _now()
        self.escalated = False
        self.last_alert_key = None   # (state, message) of the last alert we raised
        self.pending_tool = None     # tool announced by PreToolUse, not yet PostToolUse'd
        self.pending_since = 0.0
        self.pending_alerted = False

    def describe(self) -> str:
        line = f"{self.agent} ({self.label}): {self.state} for {_fmt_age(_now() - self.state_since)}"
        if self.last_message:
            line += f' — "{self.last_message[:90]}"'
        return line


# ---------------------------------------------------------------------------
# HTTP listener
# ---------------------------------------------------------------------------

class _HookHandler(BaseHTTPRequestHandler):
    monitor = None  # injected by AgentMonitor

    def do_POST(self):
        if self.path.rstrip("/") != "/agent-event":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except (ValueError, OSError) as e:
            self._reply(400, {"error": str(e)})
            return

        try:
            if self.monitor:
                self.monitor.handle_event(payload)
        except Exception as e:
            print(f"[AgentMonitor] event handling error: {e}")
        self._reply(200, {"ok": True})

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/health":
            self._reply(200, {"ok": True, "sessions": len(self.monitor.sessions) if self.monitor else 0})
        elif path == "/status":
            # Same text agent_monitor_status() speaks, so the monitor can be
            # inspected without asking Ultron out loud.
            self._reply(200, {"ok": True, "status": self.monitor.status() if self.monitor else "no monitor"})
        else:
            self._reply(404, {"error": "not found"})

    def _reply(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            # The client hung up before reading the reply. Routine for a local
            # hook that fires and forgets, and nothing can be done about it.
            pass

    def log_message(self, *args):
        pass  # silence the default stderr access log


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------

class AgentMonitor:
    """Tracks coding-agent sessions and raises toast/voice alerts on state changes."""

    def __init__(self, output_manager=None):
        self.output_manager = output_manager
        self.sessions = {}
        self._lock = threading.RLock()
        self._server = None
        self._server_thread = None
        self._tick_thread = None
        self._running = False
        self.reload_settings()

    # -- config ------------------------------------------------------------

    def reload_settings(self):
        s = _load_settings()
        self.port = int(s.get("port", DEFAULT_PORT))
        self.alert_mode = s.get("alert_mode", "both")
        self.min_run_seconds = int(s.get("min_run_seconds", 60))
        self.escalate_seconds = int(s.get("escalate_seconds", 120))
        self.alert_when_focused = bool(s.get("alert_when_focused", True))
        self.pending_tool_seconds = int(s.get("pending_tool_seconds", 12))
        self._settings_enabled = bool(s.get("enabled", True))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        if self._running:
            return f"Agent monitor is already running on port {self.port}."

        self.reload_settings()
        _HookHandler.monitor = self
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _HookHandler)
        except OSError as e:
            return (f"Could not start the agent monitor listener on port {self.port}: {e}. "
                    "Another process may already be using it.")
        self._server.daemon_threads = True

        self._running = True
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
        self._server_thread.start()

        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

        return (f"Agent monitor started. Listening on 127.0.0.1:{self.port} "
                "for Claude Code hook events.")

    def stop(self) -> str:
        if not self._running:
            return "Agent monitor is not running."
        self._running = False
        try:
            if self._server:
                self._server.shutdown()
                self._server.server_close()
        except Exception as e:
            print(f"[Agent Monitor] failed to stop the server: {e}")
        self._server = None
        return "Agent monitor stopped."

    @property
    def is_running(self) -> bool:
        return self._running

    # -- event intake ------------------------------------------------------

    def handle_event(self, payload: dict):
        event = (payload.get("event") or payload.get("hook_event_name") or "").strip()
        agent = (payload.get("agent") or "Claude Code").strip()
        cwd = payload.get("cwd") or ""
        session_id = payload.get("session_id") or f"{agent}:{cwd}"
        message = (payload.get("message") or "").strip()
        ide = (payload.get("ide") or "").strip()

        key = f"{agent}:{session_id}"
        state = _EVENT_STATE.get(event)

        # "rateup-api in Antigravity" reads better than a bare folder name when
        # the same project is open in two editors at once.
        label = _label_from_cwd(cwd)
        if ide:
            label = f"{label} in {ide}"

        with self._lock:
            sess = self.sessions.get(key)
            if sess is None:
                sess = _Session(key, agent, label)
                self.sessions[key] = sess
            elif ide and sess.label != label:
                sess.label = label
            sess.last_seen = _now()
            if message:
                sess.last_message = message

            # Pause watchdog: PreToolUse fires, then execution blocks on any
            # permission dialog or question until PostToolUse arrives. A Pre with
            # no matching Post means the agent is sitting there waiting on you.
            if event == "PreToolUse":
                sess.pending_tool = payload.get("tool_name") or "a tool"
                sess.pending_since = _now()
                sess.pending_alerted = False
            elif event in ("PostToolUse", "Stop", "Notification", "UserPromptSubmit", "SessionEnd"):
                sess.pending_tool = None
                sess.pending_alerted = False

            if state is None:
                return

            prev_state = sess.state
            # Suppress only an exact repeat — same state AND same message. A second,
            # different question while already WAITING is real news and must get through.
            alert_key = (state, message)
            if state in (IDLE, WAITING) and alert_key == sess.last_alert_key:
                return

            if state != prev_state:
                sess.state = state
                sess.state_since = _now()
                sess.escalated = False

            if state == RUNNING:
                if prev_state != RUNNING:
                    sess.busy_since = _now()
                return

            busy_for = sess.state_since - sess.busy_since

            if state == GONE:
                self.sessions.pop(key, None)
                # A session ending while the agent was mid-work means it died or
                # was killed — worth knowing. A clean exit from an idle session
                # is just the user closing a terminal; stay quiet.
                if prev_state not in (RUNNING, WAITING):
                    return

        if event in _SILENT_EVENTS:
            return

        if state == WAITING:
            sess.last_alert_key = alert_key
            self._alert(sess, "needs your input",
                        message or "The agent is asking a question or waiting for permission.")
        elif state == IDLE:
            if busy_for < self.min_run_seconds:
                return  # short turn — not worth interrupting the user for
            sess.last_alert_key = alert_key
            self._alert(sess, "has finished",
                        message or f"Finished after {int(busy_for)}s and is waiting for you.")
        elif state == GONE:
            self._alert(sess, "stopped unexpectedly",
                        message or "The session ended while the agent was still working.")

    # -- alerting ----------------------------------------------------------

    def _is_focused(self, sess) -> bool:
        """True if the user is currently looking at this agent's window."""
        try:
            from ultron.plugins.screen_plugin import screen_get_active_window
            title = screen_get_active_window() or ""
        except Exception:
            return False
        title_l = title.lower()
        return bool(sess.label) and sess.label.lower() in title_l

    def _alert(self, sess, headline: str, detail: str, force_voice: bool = False):
        if not self.alert_when_focused and self._is_focused(sess):
            return

        title = f"{sess.agent} — {sess.label}"
        body = f"{headline}. {detail}".strip()

        mode = self.alert_mode
        if mode in ("toast", "both"):
            try:
                send_toast(title, body)
            except Exception as e:
                print(f"[AgentMonitor] toast failed: {e}")

        if force_voice or mode in ("voice", "both"):
            spoken = f"Sir, {sess.agent} in {sess.label} {headline}."
            if self.output_manager:
                self.output_manager.enqueue(spoken, source="cron")
            else:
                print(f"\n[AgentMonitor] {spoken}")

        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n[AgentMonitor {stamp}] {title}: {body}")

    # -- background tick: pause watchdog + escalation ----------------------

    def _tick_loop(self):
        while self._running:
            try:
                self._check_pending()
                self._check_escalations()
            except Exception as e:
                print(f"[AgentMonitor] tick error: {e}")
            time.sleep(2)

    def _check_pending(self):
        """Catch an agent blocked on a prompt: PreToolUse arrived, PostToolUse never did.

        This is what catches "asked a question and paused" immediately, instead of
        waiting for Claude Code's own Notification event (which only fires after the
        prompt has gone unanswered for about a minute).
        """
        due = []
        with self._lock:
            for sess in self.sessions.values():
                if (sess.pending_tool and not sess.pending_alerted
                        and (_now() - sess.pending_since) >= self.pending_tool_seconds):
                    sess.pending_alerted = True
                    if sess.state != WAITING:
                        sess.state = WAITING
                        sess.state_since = _now()
                        sess.escalated = False
                    due.append((sess, sess.pending_tool))
        for sess, tool in due:
            self._alert(sess, "is paused waiting on you",
                        f"It is asking before running {tool}.")

    def _check_escalations(self):
        """A WAITING agent you never came back to gets spoken about, loudly, once."""
        due = []
        with self._lock:
            for sess in self.sessions.values():
                if (sess.state == WAITING and not sess.escalated
                        and (_now() - sess.state_since) >= self.escalate_seconds):
                    sess.escalated = True
                    due.append(sess)
        for sess in due:
            self._alert(sess, f"is still waiting after {_fmt_age(_now() - sess.state_since)}",
                        sess.last_message or "It has not moved since it asked you.",
                        force_voice=True)

    # -- status ------------------------------------------------------------

    def status(self) -> str:
        if not self._running:
            return "The agent monitor is not running, sir. Say 'start agent monitor' to enable it."
        with self._lock:
            sessions = list(self.sessions.values())
        if not sessions:
            return (f"Agent monitor is active on port {self.port}, but no agent sessions "
                    "have reported in yet.")
        lines = [f"Agent monitor is active. {len(sessions)} session(s) tracked:"]
        lines += [f"  - {s.describe()}" for s in sessions]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton + tool functions exposed to the Brain
# ---------------------------------------------------------------------------

_MONITOR = None


def get_monitor(output_manager=None) -> AgentMonitor:
    """Returns the process-wide AgentMonitor, creating it on first use."""
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = AgentMonitor(output_manager=output_manager)
    elif output_manager is not None and _MONITOR.output_manager is None:
        _MONITOR.output_manager = output_manager
    return _MONITOR


def agent_monitor_start() -> str:
    """Starts monitoring coding agents (Claude Code, Antigravity, etc.) and alerts the user
    when an agent stops, finishes a task, or asks a question."""
    return get_monitor().start()


def agent_monitor_stop() -> str:
    """Stops monitoring coding agents. No further agent alerts will be raised."""
    return get_monitor().stop()


def agent_monitor_status() -> str:
    """Reports which coding agents are currently being monitored and what state each one is in
    (running, waiting for input, or finished). Use this when the user asks about their agents,
    e.g. 'is Claude done yet' or 'what are my agents doing'."""
    return get_monitor().status()


def agent_monitor_configure(alert_mode: str) -> str:
    """Changes how agent alerts are delivered. alert_mode must be 'toast', 'voice', or 'both'."""
    mode = (alert_mode or "").strip().lower()
    if mode not in ("toast", "voice", "both"):
        return "alert_mode must be one of: toast, voice, both."

    try:
        config.set("agent_monitor.alert_mode", mode)
    except OSError as e:
        return f"Could not save the setting: {e}"

    get_monitor().alert_mode = mode
    return f"Agent alerts will now be delivered via: {mode}."


# ---------------------------------------------------------------------------
# Global hook installation
# ---------------------------------------------------------------------------

_HOOK_SCRIPT = '''"""Ultron agent-monitor hook. Installed by Ultron; safe to delete.

Claude Code invokes this on lifecycle events with the event JSON on stdin.
It forwards the event to the local Ultron listener and always exits 0 so it
can never block or slow down the agent.
"""
import os, sys, json, socket

PORT = {port}


def send(body):
    """Fire-and-forget POST over a raw socket.

    Deliberately avoids urllib.request: importing it costs ~100ms, which is
    unacceptable on PreToolUse since that hook blocks every tool call. We also
    never read the response — the listener's reply is of no use to us.
    """
    conn = socket.create_connection(("127.0.0.1", PORT), timeout=1)
    try:
        conn.sendall(
            b"POST /agent-event HTTP/1.1\\r\\n"
            b"Host: 127.0.0.1\\r\\n"
            b"Content-Type: application/json\\r\\n"
            b"Content-Length: " + str(len(body)).encode() + b"\\r\\n"
            b"Connection: close\\r\\n\\r\\n" + body)
    finally:
        conn.close()

# Substring of the executable name -> friendly IDE name. Matched as a substring,
# not an exact name: the real Antigravity process is "Antigravity IDE.exe", and
# VSCode forks are similarly inconsistent. Order matters — most specific first,
# since every VSCode fork also ships an executable containing "code".
IDES = [
    ("antigravity", "Antigravity"),
    ("code - insiders", "VSCode Insiders"),
    ("cursor", "Cursor"),
    ("windsurf", "Windsurf"),
    ("code", "VSCode"),
    ("windowsterminal", "Terminal"),
]


def detect_ide():
    """Walk up the process tree to find the host editor."""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        for _ in range(12):
            proc = proc.parent()
            if proc is None:
                break
            name = (proc.name() or "").lower()
            for needle, friendly in IDES:
                if needle in name:
                    return friendly
    except Exception as e:
        print(f"[Agent Monitor] could not identify the parent IDE: {{e}}")
    # Fallback only if psutil is unavailable. Note every VSCode fork sets
    # TERM_PROGRAM=vscode, so this cannot distinguish them — it is a last resort.
    if (os.environ.get("TERM_PROGRAM") or "").lower() == "vscode":
        return "VSCode-family"
    return ""


# PreToolUse/PostToolUse fire on EVERY tool call and PreToolUse blocks the tool,
# so the ~180ms process-tree walk must never run on them. The session's IDE is
# already known from SessionStart, and the monitor keeps the existing label when
# "ide" comes through empty.
HOT_EVENTS = ("PreToolUse", "PostToolUse")


def main():
    try:
        event = sys.argv[1] if len(sys.argv) > 1 else "Unknown"
        try:
            payload = json.loads(sys.stdin.read() or "{{}}")
        except Exception:
            payload = {{}}
        name = payload.get("hook_event_name") or event
        body = json.dumps({{
            "agent": "Claude Code",
            "ide": "" if name in HOT_EVENTS else detect_ide(),
            "event": name,
            "cwd": payload.get("cwd") or "",
            "session_id": payload.get("session_id") or "",
            "tool_name": payload.get("tool_name") or "",
            "message": payload.get("message") or "",
        }}).encode("utf-8")
        send(body)
    except Exception as e:
        print(f"[Agent Monitor] could not deliver the hook event: {{e}}")

main()
sys.exit(0)
'''

_HOOK_EVENTS = ["Notification", "Stop", "SessionStart", "SessionEnd",
                "UserPromptSubmit", "PreToolUse", "PostToolUse"]


def install_agent_hooks() -> str:
    """Installs the global Claude Code hooks so that EVERY Claude Code session on this machine
    reports its status to Ultron. Writes a hook script and updates the user-level
    ~/.claude/settings.json. Run this once; restart Claude Code afterwards."""
    claude_dir = os.path.join(os.path.expanduser("~"), ".claude")
    try:
        os.makedirs(claude_dir, exist_ok=True)
    except OSError as e:
        return f"Could not create {claude_dir}: {e}"

    monitor = get_monitor()
    script_path = os.path.join(claude_dir, "ultron_hook.py")
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_HOOK_SCRIPT.format(port=monitor.port))
    except OSError as e:
        return f"Could not write the hook script: {e}"

    settings_file = os.path.join(claude_dir, "settings.json")
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            cc_settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cc_settings = {}

    python_exe = sys.executable or "python"
    hooks = cc_settings.setdefault("hooks", {})
    for event in _HOOK_EVENTS:
        command = f'"{python_exe}" "{script_path}" {event}'
        entries = hooks.setdefault(event, [])
        # Drop any previous Ultron entry so re-running is idempotent
        entries[:] = [
            e for e in entries
            if not any("ultron_hook.py" in str(h.get("command", ""))
                       for h in (e.get("hooks") or []))
        ]
        entries.append({"hooks": [{"type": "command", "command": command}]})

    try:
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(cc_settings, f, indent=2)
    except OSError as e:
        return f"Could not update {settings_file}: {e}"

    return (f"Global agent hooks installed, sir.\n"
            f"  Hook script: {script_path}\n"
            f"  Registered in: {settings_file}\n"
            f"  Events: {', '.join(_HOOK_EVENTS)}\n"
            f"Restart any running Claude Code sessions for the hooks to take effect.")
