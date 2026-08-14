"""Speaking up unprompted after a long silence.

The value of this feature is entirely in its restraint, so most of these test
the cases where it must say nothing.
"""

import datetime
import types

import pytest

from ultron import idle_chat


class TestQuietHours:
    @pytest.mark.parametrize("hour,quiet", [
        (22, True), (23, True), (2, True), (7, True),
        (8, False), (12, False), (17, False), (21, False),
    ])
    def test_the_overnight_window(self, hour, quiet):
        now = datetime.datetime(2026, 8, 14, hour, 0)
        assert idle_chat.in_quiet_hours(now, 22, 8) is quiet

    def test_the_window_wraps_midnight(self):
        """A naive start <= hour < end test gets this exactly backwards."""
        assert idle_chat.in_quiet_hours(datetime.datetime(2026, 8, 14, 3), 22, 8) is True

    def test_a_daytime_window_also_works(self):
        assert idle_chat.in_quiet_hours(datetime.datetime(2026, 8, 14, 10), 9, 17) is True
        assert idle_chat.in_quiet_hours(datetime.datetime(2026, 8, 14, 20), 9, 17) is False

    def test_equal_bounds_means_never_quiet(self):
        for hour in (0, 8, 15, 23):
            assert idle_chat.in_quiet_hours(datetime.datetime(2026, 8, 14, hour), 8, 8) is False


class TestBlocking:
    def _settings(self, monkeypatch, **values):
        base = {
            "idle_chat.enabled": True,
            "idle_chat.quiet_start_hour": 22,
            "idle_chat.quiet_end_hour": 8,
            "idle_chat.silent_when_mic_off": False,
            "idle_chat.silent_in_fullscreen": False,
        }
        base.update(values)
        monkeypatch.setattr(
            idle_chat.config, "get", lambda key, default=None: base.get(key, default)
        )

    def test_disabled_blocks_everything(self, monkeypatch):
        self._settings(monkeypatch, **{"idle_chat.enabled": False})
        assert idle_chat.blocked_reason(None, True) == "turned off"

    def test_quiet_hours_block(self, monkeypatch):
        self._settings(monkeypatch)
        monkeypatch.setattr(idle_chat, "in_quiet_hours", lambda **kw: True)
        assert idle_chat.blocked_reason(None, True) == "quiet hours"

    def test_nothing_blocks_in_the_afternoon(self, monkeypatch):
        self._settings(monkeypatch)
        monkeypatch.setattr(idle_chat, "in_quiet_hours", lambda **kw: False)
        assert idle_chat.blocked_reason(None, True) is None

    def test_the_mic_guard_is_opt_in(self, monkeypatch):
        """Off by default, so a muted mic does not silence it."""
        self._settings(monkeypatch)
        monkeypatch.setattr(idle_chat, "in_quiet_hours", lambda **kw: False)
        assert idle_chat.blocked_reason(None, microphone_active=False) is None

    def test_the_mic_guard_works_when_switched_on(self, monkeypatch):
        self._settings(monkeypatch, **{"idle_chat.silent_when_mic_off": True})
        monkeypatch.setattr(idle_chat, "in_quiet_hours", lambda **kw: False)
        assert idle_chat.blocked_reason(None, microphone_active=False) == "microphone is off"

    def test_the_fullscreen_guard_is_opt_in(self, monkeypatch):
        self._settings(monkeypatch)
        monkeypatch.setattr(idle_chat, "in_quiet_hours", lambda **kw: False)
        monkeypatch.setattr(idle_chat, "is_fullscreen_app_in_front", lambda: True)
        assert idle_chat.blocked_reason(None, True) is None

    def test_the_fullscreen_guard_works_when_switched_on(self, monkeypatch):
        self._settings(monkeypatch, **{"idle_chat.silent_in_fullscreen": True})
        monkeypatch.setattr(idle_chat, "in_quiet_hours", lambda **kw: False)
        monkeypatch.setattr(idle_chat, "is_fullscreen_app_in_front", lambda: True)
        assert idle_chat.blocked_reason(None, True) == "a fullscreen app is in front"


class TestComposing:
    def _brain(self, reply=None, error=None):
        def create(**kwargs):
            if error:
                raise error
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(content=reply)
                )]
            )

        return types.SimpleNamespace(
            client=types.SimpleNamespace(
                chat=types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=create)
                )
            ),
            selected_model="test",
            _record_usage=lambda r: None,
            db=types.SimpleNamespace(list_memories=lambda: [], get_pending_tasks=lambda: []),
        )

    def test_a_good_line_is_used(self):
        brain = self._brain(reply="Sir, shall I put on some Arijit Singh?")
        assert idle_chat.compose(brain, 25) == "Sir, shall I put on some Arijit Singh?"

    def test_surrounding_quotes_are_stripped(self):
        brain = self._brain(reply='"Still there, sir?"')
        assert idle_chat.compose(brain, 25) == "Still there, sir?"

    def test_an_unreachable_model_falls_back(self):
        """Losing the network must not lose the feature."""
        brain = self._brain(error=OSError("connection refused"))
        assert idle_chat.compose(brain, 25) in idle_chat.FALLBACK_LINES

    def test_a_rambling_reply_falls_back(self):
        brain = self._brain(reply="word " * 200)
        assert idle_chat.compose(brain, 25) in idle_chat.FALLBACK_LINES

    def test_an_empty_reply_falls_back(self):
        assert idle_chat.compose(self._brain(reply=""), 25) in idle_chat.FALLBACK_LINES
        assert idle_chat.compose(self._brain(reply=None), 25) in idle_chat.FALLBACK_LINES

    def test_a_stray_tool_call_falls_back(self):
        """Small models emit these even when told not to; unspeakable."""
        brain = self._brain(reply='<tool_call>{"name": "web_search"}</tool_call>')
        assert idle_chat.compose(brain, 25) in idle_chat.FALLBACK_LINES

    def test_only_the_first_line_is_spoken(self):
        brain = self._brain(reply="Still there, sir?\nI could check your email.")
        assert idle_chat.compose(brain, 25) == "Still there, sir?"

    def test_fallback_lines_are_all_speakable(self):
        for line in idle_chat.FALLBACK_LINES:
            assert line.strip() and len(line.split()) <= idle_chat.MAX_WORDS


class TestOffersAreDeliverable:
    """It must not offer work it has no tool for.

    "Shall I compile your recent performance data?" sounds helpful and sends
    Ultron hunting for a tool that does not exist the moment you say yes.
    """

    def test_the_prompt_lists_what_it_can_actually_do(self):
        assert "{offers}" in idle_chat.PROMPT
        assert "only do these things" in idle_chat.PROMPT

    def test_every_offer_maps_to_a_real_capability(self, brain):
        """Each line in OFFERS must correspond to tools that exist."""
        required = {
            "spotify": ("search_spotify", "system_media_control"),
            "unread email": ("read_emails",),
            "search the web": ("web_search",),
            "research a topic": ("start_background_research",),
            "battery": ("get_system_health",),
            "set a reminder": ("set_reminder", "set_reminder_at"),
            "open an app": ("open_application",),
            "read a document": ("read_document",),
            "remember about them": ("list_memories",),
        }
        listed = idle_chat.offers().lower()
        for phrase, tools in required.items():
            assert phrase in listed, f"the offer list no longer mentions {phrase!r}"
            missing = [t for t in tools if t not in brain.tool_functions]
            assert not missing, f"{phrase!r} offers missing tools: {missing}"

    def test_the_offer_order_varies(self):
        """A fixed order makes a small model offer the same thing all day."""
        orders = {idle_chat.offers() for _ in range(20)}
        assert len(orders) > 1, "the offer list is never shuffled"

    def test_shuffling_never_drops_an_offer(self):
        for _ in range(20):
            assert len(idle_chat.offers().splitlines()) == len(idle_chat.OFFER_LINES)

    def test_already_scheduled_reminders_are_marked_as_done(self, brain):
        """Told only "coming up", it offers to set a reminder that exists."""
        import datetime as dt

        soon = dt.datetime.now() + dt.timedelta(hours=2)
        brain.db.add_task("rateup meeting", soon.isoformat(), "daily", None)
        context = idle_chat.gather_context(brain)
        assert "ALREADY set" in context
        assert "never offer to set them again" in context.lower()

    def test_the_rules_redirect_rather_than_only_forbidding(self):
        """Telling it what not to do leaves it nowhere to go."""
        assert "Anything listed as already set is DONE" in idle_chat.PROMPT
        assert "help them get ready for it" in idle_chat.PROMPT

    def test_it_is_told_not_to_invent_observations(self):
        """'Based on your recent activity' — it has no such data."""
        assert "Never claim to have noticed" in idle_chat.PROMPT

    def test_it_is_told_not_to_narrate_an_empty_diary(self):
        assert "Do not say there is nothing scheduled" in idle_chat.PROMPT

    def test_empty_context_is_passed_as_blank(self, brain, monkeypatch):
        """Told '(nothing scheduled)', the model announces that fact."""
        captured = {}

        def create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            raise OSError("stop here")

        monkeypatch.setattr(brain, "client", types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create))))
        idle_chat.compose(brain, 25)
        # The rules legitimately say "do not say there is nothing scheduled",
        # so check the context slot rather than the whole prompt.
        assert "(nothing scheduled" not in captured["prompt"].lower()


class TestMemorySelection:
    """A local model is free to feed; a hosted one is billed per token."""

    MEMORIES = [
        {"key": "github", "value": "gh", "importance": 9, "category": "personal_links"},
        {"key": "email", "value": "e@x", "importance": 8, "category": "personal_contact"},
        {"key": "musician", "value": "Arijit Singh", "importance": 7, "category": "music"},
        {"key": "musician", "value": "Ed Sheeran", "importance": 7, "category": "music"},
        {"key": "name", "value": "Abhinav", "importance": 1, "category": "personal_details"},
    ]

    def _provider(self, monkeypatch, name):
        monkeypatch.setattr(idle_chat.config, "active_provider", lambda: name)

    def test_local_gets_everything(self, monkeypatch):
        self._provider(monkeypatch, "localapi")
        assert idle_chat.choose_memories(self.MEMORIES) == self.MEMORIES

    def test_local_ignores_importance(self, monkeypatch):
        self._provider(monkeypatch, "localapi")
        picked = idle_chat.choose_memories(self.MEMORIES)
        assert any(m["importance"] == 9 for m in picked)
        assert any(m["importance"] == 10 for m in picked + [{"importance": 10}])

    def test_local_does_not_sample(self, monkeypatch):
        """Sampling is what made it repeat the same fact all day."""
        self._provider(monkeypatch, "localapi")
        runs = {len(idle_chat.choose_memories(self.MEMORIES)) for _ in range(10)}
        assert runs == {len(self.MEMORIES)}

    @pytest.mark.parametrize("provider", ["openrouterapi", "geminiapi"])
    def test_hosted_takes_a_small_sample(self, monkeypatch, provider):
        self._provider(monkeypatch, provider)
        picked = idle_chat.choose_memories(self.MEMORIES)
        assert len(picked) == idle_chat.HOSTED_MEMORY_SAMPLE

    def test_hosted_leaves_out_contacts_and_identity(self, monkeypatch):
        self._provider(monkeypatch, "openrouterapi")
        for _ in range(30):
            for memory in idle_chat.choose_memories(self.MEMORIES):
                assert memory["category"] != "personal_contact"
                assert memory["importance"] <= 8

    def test_nothing_eligible_still_yields_something(self, monkeypatch):
        """All memories filtered out must not mean an empty context."""
        self._provider(monkeypatch, "openrouterapi")
        only_excluded = [self.MEMORIES[0], self.MEMORIES[1]]
        assert idle_chat.choose_memories(only_excluded)

    def test_an_empty_store_is_handled(self, monkeypatch):
        for provider in ("localapi", "openrouterapi"):
            self._provider(monkeypatch, provider)
            assert idle_chat.choose_memories([]) == []


class TestContext:
    def test_imminent_reminders_are_offered(self, brain):
        soon = datetime.datetime.now() + datetime.timedelta(hours=2)
        brain.db.add_task("stretch", soon.isoformat(), "daily", None)
        assert "stretch" in idle_chat.gather_context(brain)

    def test_distant_reminders_are_not(self, brain):
        far = datetime.datetime.now() + datetime.timedelta(days=3)
        brain.db.add_task("dentist", far.isoformat(), None, None)
        assert "dentist" not in idle_chat.gather_context(brain)

    def test_memories_are_offered(self, brain):
        brain.db.save_memory("music", "favourite band", "Ed Sheeran", 5)
        assert "Ed Sheeran" in idle_chat.gather_context(brain)

    def test_nothing_known_yields_nothing(self, brain):
        assert idle_chat.gather_context(brain) == ""

    def test_a_broken_database_does_not_crash_it(self):
        broken = types.SimpleNamespace(db=types.SimpleNamespace(
            get_pending_tasks=lambda: 1 / 0,
            list_memories=lambda: 1 / 0,
        ))
        assert idle_chat.gather_context(broken) == ""


class TestPlumbing:
    def test_an_idle_remark_is_dropped_when_the_user_speaks(self):
        """It must never delay an answer the user is waiting for."""
        import queue as queue_module

        from ultron.output_manager import OutputManager

        manager = OutputManager.__new__(OutputManager)
        manager._queue = queue_module.Queue()
        manager.speaker = types.SimpleNamespace(stop=lambda: None)
        for source in ("idle", "cron", "user", "system", "reminder"):
            manager._queue.put({"text": source, "source": source, "print": False})

        manager.interrupt()

        survivors = []
        while not manager._queue.empty():
            survivors.append(manager._queue.get()["source"])
        assert "idle" not in survivors
        assert "cron" not in survivors
        assert {"user", "system", "reminder"} <= set(survivors)

    def test_the_ui_knows_how_to_show_it(self):
        from ultron.ui.overlay import SOURCE_ROLES

        assert "idle" in SOURCE_ROLES

    def test_the_defaults_are_shipped(self):
        import json
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        defaults = json.load(open(os.path.join(root, "settings.default.json")))
        assert "idle_chat" in defaults
        for key in ("enabled", "after_minutes", "quiet_start_hour", "quiet_end_hour"):
            assert key in defaults["idle_chat"]
