"""What must never end up in a public repository.

github.com/AbhinavViswam/ULTRON is public, so anything git tracks is
published the moment a branch is pushed. Some of these files carry API keys
and OAuth tokens; others carry nothing secret today but sit exactly where a
secret would naturally be added later.

settings.json was the live example. It was listed in .gitignore and tracked
anyway -- gitignore does not untrack a file already in the index, so the
listing looked like protection while providing none. It held only model names
and feature flags, but one `git add -A` would have published whatever it grew
into. These tests fail loudly if any of them is ever staged again.
"""

import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files that must never be tracked, and why each one matters.
NEVER_TRACKED = {
    "keys.json": "API keys for OpenRouter and Google",
    "credentials.json": "the Google OAuth client blob",
    "token.json": "a live Google OAuth token",
    "usage.json": "personal usage history",
    "settings.json": "personal configuration, and where secrets tend to land",
    ".env": "environment secrets",
}


def _tracked():
    """Every path git currently tracks, or None outside a repository."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {line.strip().replace("\\", "/") for line in out.stdout.splitlines()
            if line.strip()}


@pytest.fixture(scope="module")
def tracked():
    files = _tracked()
    if files is None:
        pytest.skip("not a git checkout")
    return files


class TestSecretsAreNotPublished:
    @pytest.mark.parametrize("path,why", sorted(NEVER_TRACKED.items()))
    def test_this_file_is_not_tracked(self, tracked, path, why):
        assert path not in tracked, (
            f"{path} is tracked by git and this repository is public. "
            f"It holds {why}. Run: git rm --cached {path}")

    def test_the_data_directory_is_not_tracked(self, tracked):
        """Ultron's database, logs, and the downloaded speech models."""
        inside = sorted(p for p in tracked if p.startswith("data/"))
        assert not inside, f"generated data is tracked: {inside[:5]}"

    def test_no_new_model_or_archive_is_tracked(self, tracked):
        """Binary weights cannot be removed from git history later without
        rewriting every clone, so the time to catch one is before it lands.

        One is already here: resources/voices/en_US-bryce-medium.onnx, 61MB,
        which speaker.py downloads by itself when absent -- so every clone
        pays for a file the code would have fetched anyway. Removing it is a
        judgement call about existing history rather than a test failure, so
        it is named as a known exception instead of being asserted away.
        """
        known = {"resources/voices/en_US-bryce-medium.onnx"}
        heavy = sorted(p for p in tracked
                       if p.lower().endswith((".onnx", ".gguf", ".fst", ".mdl",
                                              ".zip", ".bin")))
        unexpected = [p for p in heavy if p not in known]
        assert not unexpected, (
            f"new binary weights are tracked: {unexpected[:5]}. These stay in "
            f"history permanently; download them at runtime instead.")

    def test_every_forbidden_file_is_also_ignored(self):
        """Untracking alone is not enough: without a gitignore entry the next
        `git add -A` puts it straight back."""
        missing = []
        for path in NEVER_TRACKED:
            result = subprocess.run(["git", "check-ignore", path], cwd=ROOT,
                                    capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                missing.append(path)
        assert not missing, f"not covered by .gitignore: {missing}"


class TestTheTemplateStaysUsable:
    """settings.json is gone from a fresh clone, so the tracked template has
    to be enough on its own."""

    def test_the_template_is_tracked(self, tracked):
        assert "settings.default.json" in tracked

    def test_it_is_valid_json(self):
        with open(os.path.join(ROOT, "settings.default.json")) as f:
            assert isinstance(json.load(f), dict)

    def test_it_holds_no_secrets(self):
        """The template ships to everyone; a key pasted here goes public.

        Judged on values, not key names. Matching names flagged
        `max_history_tokens` for containing "token", which is the kind of
        false alarm that gets a hygiene test switched off.
        """
        import re

        with open(os.path.join(ROOT, "settings.default.json")) as f:
            settings = json.load(f)

        # Real credentials: a known prefix, or a long opaque run of characters.
        # Model names are long too, but they carry "/" and ":" separators.
        prefixes = ("sk-", "sk_", "ghp_", "gho_", "AIza", "Bearer ", "xoxb-")
        opaque = re.compile(r"^[A-Za-z0-9_\-]{32,}$")
        found = []

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, str):
                if node.startswith(prefixes) or opaque.match(node):
                    found.append((path.lstrip("."), node[:8] + "..."))

        walk(settings)
        assert not found, (
            f"settings.default.json looks like it holds credentials at "
            f"{found}; keys belong in keys.json, which is never tracked")

    def test_ultron_starts_without_a_user_settings_file(self):
        """A fresh clone has no settings.json at all. Defaults must carry it."""
        from ultron.config import BUILTIN_DEFAULTS, Config

        with open(os.path.join(ROOT, "settings.default.json")) as f:
            defaults = json.load(f)

        for required in ("openrouterapi", "geminiapi", "localapi",
                         "microphone_active"):
            assert required in defaults or required in BUILTIN_DEFAULTS, (
                f"{required} has no default, so a fresh clone cannot start")
        # Derived, not listed: a hardcoded tuple here went stale the moment
        # a fourth provider was added, and failed for the wrong reason.
        from ultron.config import PROVIDER_KEYS
        assert Config().active_provider() in PROVIDER_KEYS
