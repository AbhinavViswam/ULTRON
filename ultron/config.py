"""Centralized configuration for Ultron.

Every piece of on-disk configuration lives here so the rest of the codebase
never has to rebuild `os.path.dirname` chains or re-parse JSON by hand.

Four files, four different natures:

    settings.default.json  Tracked template. Ships with the repo.
    settings.json          The user's overrides. Gitignored. Merged over the
                           template on load, so adding a new default setting
                           never breaks an existing install.
    keys.json              API keys. Gitignored.
    credentials.json       Google OAuth *client* blob from Cloud Console.
    token.json             Google OAuth token, written by the auth flow.

Settings are read through a process-wide singleton that reloads itself when
settings.json changes on disk, so hand-edits and UI edits both propagate
without a restart. Components that cache derived state (the Brain's LLM
client, for instance) can register a callback via `on_change`.
"""

import json
import os
import threading

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SETTINGS_PATH = os.path.join(PROJECT_ROOT, "settings.json")
DEFAULT_SETTINGS_PATH = os.path.join(PROJECT_ROOT, "settings.default.json")
KEYS_PATH = os.path.join(PROJECT_ROOT, "keys.json")
CREDENTIALS_PATH = os.path.join(PROJECT_ROOT, "credentials.json")
TOKEN_PATH = os.path.join(PROJECT_ROOT, "token.json")
# Everything Ultron generates: the database, logs, research and routine logs.
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Fallback used when settings.default.json is missing or unreadable. Keeps the
# assistant bootable from a bare checkout.
BUILTIN_DEFAULTS = {
    "openrouterapi": True,
    "geminiapi": False,
    "groqapi": False,
    "localapi": False,
    "openrouter_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "gemini_model": "gemini-2.5-flash",
    "groq_model": "openai/gpt-oss-20b",
    "local_model": "gemma4:e2b",
    "local_api_url": "http://localhost:11434/v1",
    "microphone_active": True,
    "truth_mode": False,
    "cron_jobs": {},
    "agent_monitor": {"enabled": True},
}

# Which API keys each provider requires, for readiness checks.
PROVIDER_KEYS = {
    "openrouterapi": "openrouter",
    "geminiapi": "google",
    "groqapi": "groq",
    "localapi": None,
}


def _read_json(path: str) -> dict:
    """Reads a JSON object from disk, returning {} for missing/invalid files."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: str, data: dict):
    """Writes JSON atomically so a crash mid-write can't corrupt the config."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merges `override` onto a copy of `base`."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """Thread-safe, self-reloading view over the configuration files."""

    def __init__(self):
        self._lock = threading.RLock()
        self._settings = {}
        self._user_settings = {}
        self._keys = {}
        self._settings_mtime = None
        self._keys_mtime = None
        self._listeners = []
        self.reload()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def reload(self):
        """Re-reads every config file from disk and notifies listeners."""
        with self._lock:
            defaults = _read_json(DEFAULT_SETTINGS_PATH) or dict(BUILTIN_DEFAULTS)
            self._user_settings = _read_json(SETTINGS_PATH)
            self._settings = _deep_merge(defaults, self._user_settings)
            self._keys = _read_json(KEYS_PATH)
            self._settings_mtime = self._mtime(SETTINGS_PATH)
            self._keys_mtime = self._mtime(KEYS_PATH)
        self._notify()

    @staticmethod
    def _mtime(path: str):
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _maybe_reload(self):
        """Reloads if settings.json or keys.json changed since the last read.

        This is a stat() per access, which is cheap enough for the read rates
        here and keeps hand-edits working the way they did before.
        """
        if (self._mtime(SETTINGS_PATH) != self._settings_mtime
                or self._mtime(KEYS_PATH) != self._keys_mtime):
            self.reload()

    # ------------------------------------------------------------------
    # Settings access
    # ------------------------------------------------------------------

    def get(self, path: str, default=None):
        """Reads a setting by dotted path, e.g. `agent_monitor.port`."""
        self._maybe_reload()
        with self._lock:
            node = self._settings
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def all(self) -> dict:
        """Returns the full merged settings dict (a copy)."""
        self._maybe_reload()
        with self._lock:
            return json.loads(json.dumps(self._settings))

    def set(self, path: str, value):
        """Sets a setting by dotted path and persists it to settings.json.

        Only the user's overrides are written, so the tracked template stays
        untouched and future default changes still reach existing installs.
        """
        with self._lock:
            node = self._user_settings
            parts = path.split(".")
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = value
            _write_json(SETTINGS_PATH, self._user_settings)
        self.reload()

    def update(self, values: dict):
        """Applies several dotted-path settings in one write."""
        with self._lock:
            for path, value in values.items():
                node = self._user_settings
                parts = path.split(".")
                for part in parts[:-1]:
                    if not isinstance(node.get(part), dict):
                        node[part] = {}
                    node = node[part]
                node[parts[-1]] = value
            _write_json(SETTINGS_PATH, self._user_settings)
        self.reload()

    # ------------------------------------------------------------------
    # API keys
    # ------------------------------------------------------------------

    def get_key(self, name: str) -> str:
        """Returns an API key from keys.json, or "" if unset.

        When several keys are stored for a provider this is the first one.
        Callers that can rotate should use get_keys() instead.
        """
        keys = self.get_keys(name)
        return keys[0] if keys else ""

    def get_keys(self, name: str) -> list:
        """Every API key stored under *name*, in order, blanks removed.

        A provider may hold one key or several. Several only helps when they
        come from separate accounts -- keys on one account share that
        account's quota, so rotating between them rotates nothing.

            "groq": "gsk_one"                  -> ["gsk_one"]
            "groq": ["gsk_one", "gsk_two"]     -> ["gsk_one", "gsk_two"]

        The plain string is what every install has today and stays valid;
        nothing needs migrating.
        """
        self._maybe_reload()
        with self._lock:
<<<<<<< HEAD
            raw = self._keys.get(name)

        if isinstance(raw, str):
            raw = [raw]
        elif not isinstance(raw, (list, tuple)):
            # A number, a dict, None -- nothing usable as a credential.
            return []

        seen, keys = set(), []
        for item in raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            # A duplicate is one account listed twice: it would look like a
            # spare key and then rate limit at the same moment as the first.
            if value and value not in seen:
                seen.add(value)
                keys.append(value)
        return keys
=======
            val = self._keys.get(name)
            if isinstance(val, list):
                val = val[0] if val else ""
            return (val or "").strip()
>>>>>>> 8d4de01 (feat: implement agent_monitor_plugin to track coding agent activity via local HTTP hook events)

    def set_key(self, name: str, value: str):
        """Stores an API key in keys.json.

        A settings form shows one key per provider -- the first -- and saves
        every field back whether or not it was touched. Writing a bare string
        there would delete the spare keys of anyone holding several, without
        saying so and without a copy anywhere. So an unchanged first key
        leaves the stored list exactly as it was.
        """
        value = (value or "").strip()
        with self._lock:
            existing = self._keys.get(name)
            if isinstance(existing, (list, tuple)) and len(existing) > 1:
                current = self.get_keys(name)
                if current and value == current[0]:
                    return
                # Genuinely replacing the first key: keep the others.
                self._keys[name] = ([value] + current[1:]) if value else current[1:]
            else:
                self._keys[name] = value
            _write_json(KEYS_PATH, self._keys)
        self.reload()

    def set_keys(self, name: str, values: list):
        """Replaces every key stored under *name*."""
        with self._lock:
            cleaned = [v.strip() for v in values
                       if isinstance(v, str) and v.strip()]
            self._keys[name] = cleaned if len(cleaned) != 1 else cleaned[0]
            _write_json(KEYS_PATH, self._keys)
        self.reload()

    def key_names(self) -> list:
        """Names of the keys currently present in keys.json."""
        self._maybe_reload()
        with self._lock:
            return sorted(self._keys.keys())

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    def active_provider(self) -> str:
        """The first provider flag set to true, defaulting to OpenRouter."""
        for name in PROVIDER_KEYS:
            if self.get(name) is True:
                return name
        return "openrouterapi"

    def set_active_provider(self, provider: str):
        """Enables one provider and disables the others."""
        if provider not in PROVIDER_KEYS:
            raise ValueError(f"Unknown provider: {provider}")
        self.update({name: (name == provider) for name in PROVIDER_KEYS})

    def model_for(self, provider: str = None) -> str:
        """The configured model name for a provider."""
        provider = provider or self.active_provider()
        setting = {
            "openrouterapi": "openrouter_model",
            "geminiapi": "gemini_model",
            "groqapi": "groq_model",
            "localapi": "local_model",
        }[provider]
        return self.get(setting) or self.get("model") or BUILTIN_DEFAULTS[setting]

    def has_credentials_file(self) -> bool:
        """True if the Google OAuth client blob is present."""
        return os.path.exists(CREDENTIALS_PATH)

    def has_google_token(self) -> bool:
        """True if Gmail has already been authorized."""
        return os.path.exists(TOKEN_PATH)

    def missing_requirements(self) -> list:
        """Human-readable list of what still needs configuring.

        Empty means the assistant can start. A UI should show its settings
        screen instead of the main window when this is non-empty; the CLI
        prints it and exits.
        """
        problems = []
        provider = self.active_provider()
        key_name = PROVIDER_KEYS.get(provider)
        if key_name and not self.get_key(key_name):
            problems.append(
                f"The '{key_name}' API key is not set in keys.json "
                f"(required by the active provider: {provider})."
            )
        return problems

    # ------------------------------------------------------------------
    # Change notification
    # ------------------------------------------------------------------

    def on_change(self, callback):
        """Registers a callback invoked after any reload.

        Used by components that cache derived state — the Brain rebuilds its
        LLM client this way when the provider or model changes.
        """
        with self._lock:
            self._listeners.append(callback)
        return callback

    def _notify(self):
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(self)
            except Exception as e:
                print(f"[Config] Listener error: {e}")


# Process-wide singleton. Import this, not the class.
config = Config()
