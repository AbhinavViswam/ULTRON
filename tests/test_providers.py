"""Adding a provider means editing five separate lists.

Groq took: PROVIDER_KEYS and the model-name map in config, the base URL and
the display label in Brain, the flag and model default in
settings.default.json, and the dropdown plus its model map in the settings
panel. Miss one and the failure is quiet and specific -- the provider appears
in the UI but has no base URL, or works from the API but cannot be selected,
or is selectable and silently falls back to another provider's model.

These tests derive everything from PROVIDER_KEYS, so a sixth provider that is
only half-wired fails here rather than in front of the user.
"""

import json
import os

import pytest

from ultron.brain import Brain
from ultron.config import BUILTIN_DEFAULTS, PROVIDER_KEYS, config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def defaults():
    with open(os.path.join(ROOT, "settings.default.json")) as f:
        return json.load(f)


@pytest.mark.parametrize("provider", sorted(PROVIDER_KEYS))
class TestEveryProviderIsFullyWired:
    def test_it_has_a_display_label(self, provider):
        assert provider in Brain.PROVIDER_LABELS, (
            f"{provider} would show as a raw settings key in the UI")

    def test_it_has_a_base_url(self, provider):
        if provider == "localapi":
            # Deliberately configurable: Ollama's address is the user's choice.
            assert config.get("local_api_url"), "the local URL setting is gone"
            return
        url = Brain.PROVIDER_BASE_URLS.get(provider)
        assert url and url.startswith("https://"), (
            f"{provider} has no base URL, so selecting it cannot work")

    def test_it_has_a_model_setting_that_resolves(self, provider):
        model = config.model_for(provider)
        assert model, f"{provider} resolves to no model name"

    def test_its_flag_ships_in_the_template(self, provider, defaults):
        assert provider in defaults, (
            f"{provider} cannot be enabled from a fresh clone")
        assert isinstance(defaults[provider], bool)

    def test_its_model_default_ships_in_the_template(self, provider, defaults):
        if provider == "localapi":
            assert "local_model" in defaults
            return
        setting = {"openrouterapi": "openrouter_model",
                   "geminiapi": "gemini_model",
                   "groqapi": "groq_model"}[provider]
        assert defaults.get(setting), f"{setting} has no default"

    def test_it_appears_in_the_settings_panel(self, provider):
        """Otherwise it is reachable only by hand-editing settings.json."""
        panel = os.path.join(ROOT, "ultron", "ui", "settings_panel.py")
        with open(panel, encoding="utf-8") as f:
            source = f.read()
        assert f'"{provider}"' in source, (
            f"{provider} is missing from the settings panel")

    def test_selecting_it_disables_the_others(self, provider, monkeypatch):
        """Two providers enabled at once means the active one is whichever
        happens to be listed first, which is not a choice anyone made."""
        written = {}
        monkeypatch.setattr(config, "update", written.update)
        config.set_active_provider(provider)

        assert written.get(provider) is True
        assert all(written[other] is False
                   for other in PROVIDER_KEYS if other != provider)


class TestTheProviderListsAgree:
    def test_exactly_one_provider_is_on_by_default(self, defaults):
        enabled = [p for p in PROVIDER_KEYS if defaults.get(p) is True]
        assert len(enabled) == 1, f"{enabled} are all enabled in the template"

    def test_no_provider_is_listed_that_does_not_exist(self):
        for provider in Brain.PROVIDER_BASE_URLS:
            assert provider in PROVIDER_KEYS, (
                f"{provider} has a base URL but is not a known provider")
        for provider in Brain.PROVIDER_LABELS:
            assert provider in PROVIDER_KEYS

    def test_an_unknown_provider_is_refused(self):
        with pytest.raises(ValueError):
            config.set_active_provider("nope")

    def test_the_builtin_fallback_covers_every_provider(self):
        """BUILTIN_DEFAULTS is what boots Ultron when the template is missing
        or corrupt, so a provider absent from it cannot be selected then."""
        for provider in PROVIDER_KEYS:
            assert provider in BUILTIN_DEFAULTS, (
                f"{provider} is missing from the bare-checkout fallback")


class TestGroqSpecifically:
    def test_it_needs_the_groq_key(self):
        assert PROVIDER_KEYS["groqapi"] == "groq"

    def test_missing_requirements_names_the_key(self, monkeypatch):
        """A blank key should say which one, not just fail to answer."""
        monkeypatch.setattr(config, "active_provider", lambda: "groqapi")
        monkeypatch.setattr(config, "get_key", lambda name: "")

        problems = config.missing_requirements()
        assert problems and "groq" in problems[0]

    def test_it_points_at_groqs_openai_compatible_endpoint(self):
        assert Brain.PROVIDER_BASE_URLS["groqapi"] == "https://api.groq.com/openai/v1"

    def test_the_default_model_is_a_chat_model(self, defaults):
        """llama-prompt-guard-2 is a 22M classifier that returns a probability,
        not text. Defaulting to one would look like a broken assistant rather
        than a misconfigured one."""
        assert "guard" not in defaults["groq_model"].lower()
        assert "whisper" not in defaults["groq_model"].lower()
