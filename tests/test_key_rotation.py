"""Reading several keys from keys.json, and rotating through them in anger.

Two halves. First that keys.json can hold a list without breaking the single
string every existing install has. Second that Brain.complete moves between
keys for the failures a new key fixes, and only those.

Nothing here reaches the network, and no test touches the real keys.json.
"""

import json

import pytest

from ultron import api_keys, brain as brain_module
from ultron.config import Config


@pytest.fixture
def keys_json(tmp_path, monkeypatch):
    """A Config reading a throwaway keys.json.

    Config resolves KEYS_PATH on every access rather than caching it, so the
    redirect has to hold for the whole test, not just construction.
    """
    import ultron.config as module

    path = tmp_path / "keys.json"
    monkeypatch.setattr(module, "KEYS_PATH", str(path))

    def build(contents):
        path.write_text(json.dumps(contents), encoding="utf-8")
        return Config()

    return build


# ---------------------------------------------------------------------------
# keys.json
# ---------------------------------------------------------------------------

class TestReadingKeys:
    def test_the_old_single_string_still_works(self, keys_json):
        """Every install today has a bare string, and none of them should
        need touching for this."""
        cfg = keys_json({"groq": "gsk_one"})
        assert cfg.get_keys("groq") == ["gsk_one"]
        assert cfg.get_key("groq") == "gsk_one"

    def test_a_list_gives_every_key(self, keys_json):
        cfg = keys_json({"groq": ["gsk_one", "gsk_two", "gsk_three"]})
        assert cfg.get_keys("groq") == ["gsk_one", "gsk_two", "gsk_three"]

    def test_the_first_key_is_what_a_single_key_caller_sees(self, keys_json):
        cfg = keys_json({"groq": ["gsk_one", "gsk_two"]})
        assert cfg.get_key("groq") == "gsk_one"

    def test_blanks_and_whitespace_are_dropped(self, keys_json):
        """A trailing comma in a hand-edited file leaves an empty slot, and
        an empty key would look like a spare and fail every time."""
        cfg = keys_json({"groq": ["gsk_one", "", "   ", "gsk_two"]})
        assert cfg.get_keys("groq") == ["gsk_one", "gsk_two"]

    def test_a_key_pasted_twice_is_counted_once(self, keys_json):
        """Otherwise it looks like a spare quota and rate limits at the same
        instant as the original."""
        cfg = keys_json({"groq": ["gsk_one", "gsk_one", "gsk_two"]})
        assert cfg.get_keys("groq") == ["gsk_one", "gsk_two"]

    def test_a_missing_provider_is_empty_not_an_error(self, keys_json):
        cfg = keys_json({"groq": "gsk_one"})
        assert cfg.get_keys("openrouter") == []

    @pytest.mark.parametrize("junk", [None, 12345, {"key": "value"}, True])
    def test_a_nonsense_value_does_not_crash_startup(self, keys_json, junk):
        cfg = keys_json({"groq": junk})
        assert cfg.get_keys("groq") == []


class TestSavingKeysDoesNotDestroyThem:
    """The settings form shows one key per provider and writes every field
    back on Save, touched or not. A plain write there would silently delete
    the second and third keys, with no copy anywhere."""

    def test_saving_an_unchanged_first_key_keeps_the_others(self, keys_json):
        cfg = keys_json({"groq": ["gsk_one", "gsk_two", "gsk_three"]})
        cfg.set_key("groq", "gsk_one")

        assert cfg.get_keys("groq") == ["gsk_one", "gsk_two", "gsk_three"]

    def test_replacing_the_first_key_keeps_the_others(self, keys_json):
        cfg = keys_json({"groq": ["gsk_one", "gsk_two"]})
        cfg.set_key("groq", "gsk_replacement")

        assert cfg.get_keys("groq") == ["gsk_replacement", "gsk_two"]

    def test_clearing_the_field_removes_only_the_first(self, keys_json):
        cfg = keys_json({"groq": ["gsk_one", "gsk_two"]})
        cfg.set_key("groq", "")

        assert cfg.get_keys("groq") == ["gsk_two"]

    def test_a_single_key_is_replaced_as_it_always_was(self, keys_json):
        cfg = keys_json({"groq": "gsk_one"})
        cfg.set_key("groq", "gsk_new")

        assert cfg.get_keys("groq") == ["gsk_new"]

    def test_set_keys_replaces_the_whole_list(self, keys_json):
        cfg = keys_json({"groq": ["gsk_one", "gsk_two"]})
        cfg.set_keys("groq", ["gsk_a", "gsk_b", "gsk_c"])

        assert cfg.get_keys("groq") == ["gsk_a", "gsk_b", "gsk_c"]


# ---------------------------------------------------------------------------
# Brain.complete
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text="ok"):
        message = type("m", (), {"content": text, "tool_calls": None})()
        self.choices = [type("c", (), {"message": message})()]
        self.usage = None


class FakeClient:
    """Answers according to which key it was built with."""

    def __init__(self, key, behaviour):
        self.key = key
        self.behaviour = behaviour
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.behaviour.setdefault("seen", []).append(outer.key)
                action = outer.behaviour.get(outer.key, FakeResponse())
                # A list is a sequence of answers: a key that fails once and
                # then works, which is what a transient error looks like.
                if isinstance(action, list):
                    action = action.pop(0) if len(action) > 1 else action[0]
                if isinstance(action, Exception):
                    raise action
                return action

        self.chat = type("chat", (), {"completions": Completions()})()


@pytest.fixture
def rotating_brain(brain, monkeypatch):
    """A Brain with three keys and a client that never leaves the process."""

    def build(behaviour, keys=("key_one", "key_two", "key_three")):
        brain.active_api = "groqapi"
        brain.selected_model = "openai/gpt-oss-20b"
        brain._timeout = 5
        brain.keyring = api_keys.KeyRing(list(keys))

        def build_client():
            brain.client = FakeClient(brain.keyring.current(), behaviour)

        monkeypatch.setattr(brain, "_build_client", build_client)
        monkeypatch.setattr(brain, "_record_usage", lambda r: None)
        monkeypatch.setattr(brain_module, "API_RETRY_SECONDS", 0)
        build_client()
        return brain

    return build


def _error(status, message="failed"):
    error = Exception(message)
    error.status_code = status
    return error


class TestRotatingWhenAKeyRunsOut:
    def test_a_rate_limited_key_hands_over_to_the_next(self, rotating_brain):
        behaviour = {"key_one": _error(429, "rate limit"),
                     "key_two": FakeResponse("answered on the second key")}
        b = rotating_brain(behaviour)

        response = b.complete(messages=[{"role": "user", "content": "hi"}])

        assert response.choices[0].message.content == "answered on the second key"
        assert behaviour["seen"] == ["key_one", "key_two"]

    def test_it_walks_all_three_before_giving_up(self, rotating_brain):
        behaviour = {"key_one": _error(429), "key_two": _error(429),
                     "key_three": FakeResponse("third time")}
        b = rotating_brain(behaviour)

        response = b.complete(messages=[])

        assert response.choices[0].message.content == "third time"
        assert behaviour["seen"] == ["key_one", "key_two", "key_three"]

    def test_rotating_does_not_eat_the_retry_budget(self, rotating_brain):
        """Rotations and retries are separate budgets.

        Two keys run out, and the third then hits a one-off timeout. Had
        rotating spent the retry budget there would be nothing left to retry
        with, and a request that should have succeeded would fail on a
        hiccup -- exactly when the user is already short of quota.
        """
        behaviour = {"key_one": _error(429), "key_two": _error(429),
                     "key_three": [TimeoutError("hiccup"),
                                   FakeResponse("reached")]}
        b = rotating_brain(behaviour)

        assert b.complete(messages=[]).choices[0].message.content == "reached"

    def test_a_rejected_key_is_replaced(self, rotating_brain):
        behaviour = {"key_one": _error(401, "invalid api key"),
                     "key_two": FakeResponse("fine")}
        b = rotating_brain(behaviour)
        b.complete(messages=[])

        assert behaviour["seen"] == ["key_one", "key_two"]

    def test_a_rejected_key_is_not_used_again_next_call(self, rotating_brain):
        behaviour = {"key_one": _error(401), "key_two": FakeResponse("fine")}
        b = rotating_brain(behaviour)
        b.complete(messages=[])
        behaviour["seen"] = []
        b.complete(messages=[])

        assert behaviour["seen"] == ["key_two"], "a dead key was tried again"

    def test_the_working_key_is_kept_for_later_calls(self, rotating_brain):
        """Going back to the rate-limited key on every request would fail
        once per request for the whole cooldown."""
        behaviour = {"key_one": _error(429), "key_two": FakeResponse("fine")}
        b = rotating_brain(behaviour)
        b.complete(messages=[])
        behaviour["seen"] = []
        b.complete(messages=[])

        assert behaviour["seen"] == ["key_two"]


class TestWhenRotationIsWrong:
    def test_a_bad_request_does_not_touch_the_other_keys(self, rotating_brain):
        """The 400 Groq returns for roughly one request in six with 89 tool
        schemas. Every key would refuse it identically, so spending them all
        turns a fixable request error into "you are out of keys"."""
        behaviour = {"key_one": _error(400, "tool schema invalid"),
                     "key_two": FakeResponse("should never be reached")}
        b = rotating_brain(behaviour)

        with pytest.raises(Exception, match="tool schema invalid"):
            b.complete(messages=[])

        assert behaviour["seen"] == ["key_one"], "it burned a key on a bad request"

    def test_the_real_reason_survives(self, rotating_brain):
        """Reported as a key problem, this sends the user to look at
        keys.json for a fault that is in the request."""
        b = rotating_brain({"key_one": _error(400, "context length exceeded")})

        with pytest.raises(Exception, match="context length exceeded"):
            b.complete(messages=[])

    def test_a_timeout_retries_the_same_key(self, rotating_brain):
        """A different account does not make the network faster, and the
        first key still has its quota."""
        behaviour = {"key_one": TimeoutError("timed out")}
        b = rotating_brain(behaviour)

        with pytest.raises(Exception):
            b.complete(messages=[])

        assert set(behaviour["seen"]) == {"key_one"}
        assert len(behaviour["seen"]) == brain_module.API_ATTEMPTS

    def test_a_server_error_retries_rather_than_rotating(self, rotating_brain):
        behaviour = {"key_one": _error(503, "overloaded")}
        b = rotating_brain(behaviour)

        with pytest.raises(Exception):
            b.complete(messages=[])

        assert set(behaviour["seen"]) == {"key_one"}


class TestWhenEveryKeyIsSpent:
    def test_the_error_reaches_the_user(self, rotating_brain):
        behaviour = {k: _error(429) for k in
                     ("key_one", "key_two", "key_three")}
        b = rotating_brain(behaviour)

        with pytest.raises(Exception):
            b.complete(messages=[])

    def test_it_does_not_loop_forever(self, rotating_brain):
        behaviour = {k: _error(429) for k in
                     ("key_one", "key_two", "key_three")}
        b = rotating_brain(behaviour)

        with pytest.raises(Exception):
            b.complete(messages=[])

        assert len(behaviour["seen"]) <= 8, (
            f"it made {len(behaviour['seen'])} calls hammering spent keys")


class TestASingleKeyBehavesAsBefore:
    def test_one_key_still_retries(self, rotating_brain):
        behaviour = {"only": _error(429)}
        b = rotating_brain(behaviour, keys=("only",))

        with pytest.raises(Exception):
            b.complete(messages=[])

        assert len(behaviour["seen"]) == brain_module.API_ATTEMPTS

    def test_the_local_provider_has_no_keyring_and_still_works(
            self, brain, monkeypatch):
        """Ollama takes any key at all and has no quota to run out of."""
        behaviour = {"local": FakeResponse("local answer")}
        brain.active_api = "localapi"
        brain.keyring = None
        brain.client = FakeClient("local", behaviour)
        monkeypatch.setattr(brain, "_record_usage", lambda r: None)

        assert brain.complete(messages=[]).choices[0].message.content == \
            "local answer"


class TestTheKeyIsNeverPrinted:
    def test_rotation_logs_positions_not_values(self, rotating_brain, capsys):
        secret = "gsk_liveproductionsecret"
        behaviour = {secret: _error(429), "key_two": FakeResponse("fine")}
        b = rotating_brain(behaviour, keys=(secret, "key_two"))
        b.complete(messages=[])

        printed = capsys.readouterr().out
        assert secret not in printed, "a key value was printed to the log"
        assert "key #" in printed
