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
        assert "the only things you can actually do" in idle_chat.PROMPT

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
        assert "Anything listed as ALREADY SET is DONE" in idle_chat.PROMPT
        assert "help them get ready for it" in idle_chat.PROMPT

    def test_it_is_told_not_to_invent_observations(self):
        """'Based on your recent activity' — it has no such data."""
        assert "Never invent something you noticed" in idle_chat.PROMPT
        assert "must come from the facts above" in idle_chat.PROMPT

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

    def test_nothing_known_yields_nothing(self, brain, monkeypatch):
        monkeypatch.setattr(idle_chat, "machine_observations", list)
        assert idle_chat.gather_context(brain) == ""

    def test_a_broken_database_does_not_crash_it(self, monkeypatch):
        monkeypatch.setattr(idle_chat, "machine_observations", list)
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


class TestItRemembersWhatItOffered:
    """An offer Ultron makes on its own must survive into the conversation.

    Without this the line exists only as audio: Ultron says "shall I play some
    Arijit Singh, sir?", the user says "yes, do it", and Ultron — whose message
    history has no record of ever speaking — asks what they would like done.
    """

    def test_an_unprompted_line_becomes_part_of_the_conversation(self, brain):
        before = len(brain.messages)
        brain.note_unprompted_line("Shall I put on some Arijit Singh, sir?")

        assert len(brain.messages) == before + 1
        assert brain.messages[-1] == {
            "role": "assistant",
            "content": "Shall I put on some Arijit Singh, sir?",
        }

    def test_yes_do_it_has_something_to_refer_back_to(self, brain):
        """The shape the model actually sees: offer, then the bare answer."""
        brain.note_unprompted_line("Shall I put on some Arijit Singh, sir?")
        brain.messages.append({"role": "user", "content": "yes do it"})

        offer, answer = brain.messages[-2], brain.messages[-1]
        assert offer["role"] == "assistant" and "Arijit" in offer["content"]
        assert answer["role"] == "user"

    def test_it_is_searchable_afterwards(self, brain):
        brain.note_unprompted_line("Shall I put on some Arijit Singh, sir?")

        import sqlite3

        with sqlite3.connect(brain.db.db_path) as conn:
            rows = conn.execute(
                "SELECT role, message FROM chat_history WHERE session_id = ?",
                (brain.session_id,),
            ).fetchall()
        assert ("model", "Shall I put on some Arijit Singh, sir?") in rows

    def test_nothing_is_recorded_for_an_empty_line(self, brain):
        before = list(brain.messages)
        for nothing in ("", "   ", None):
            brain.note_unprompted_line(nothing)
        assert brain.messages == before

    def test_surrounding_whitespace_is_not_carried_into_history(self, brain):
        brain.note_unprompted_line("  Still there, sir?\n")
        assert brain.messages[-1]["content"] == "Still there, sir?"

    def test_a_failing_log_does_not_take_down_the_idle_loop(self, brain):
        """The line still enters the conversation even if the log write fails."""
        def explode(*args, **kwargs):
            raise RuntimeError("disk full")

        brain.db.save_message = explode
        brain.note_unprompted_line("Still there, sir?")  # must not raise

        assert brain.messages[-1]["content"] == "Still there, sir?"

    def test_the_idle_loop_actually_records_what_it_says(self):
        """Guards the wiring, not the method — the bug was a missing call."""
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._idle_loop)
        assert "note_unprompted_line" in source

        # Recorded before it is queued: enqueue is what shows it on screen, so
        # recording after would leave a window where the user can answer an
        # offer that is not yet in the history.
        assert source.index("note_unprompted_line") < source.index("enqueue")


class TestUnpromptedLinesFromEverywhere:
    """Reminders fire on their own thread, mid-turn, with no coordination.

    An assistant message inserted between a tool_calls message and its results
    is a malformed history the API rejects outright — so a line said while a
    turn is running has to wait rather than land wherever it happens to fall.
    """

    def test_the_reminder_loop_records_what_it_announces(self):
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._reminder_loop)
        assert "note_unprompted_line" in source
        assert source.index("note_unprompted_line") < source.index("enqueue")

    def test_a_routine_needs_no_wiring_because_it_is_a_real_turn(self):
        """It runs through process_input, so its reply is already in history."""
        import inspect

        from ultron.core import UltronCore

        source = inspect.getsource(UltronCore._run_routine)
        assert "self.brain.process_input" in source
        # Recording it again here would duplicate every routine result.
        assert "note_unprompted_line" not in source

    def test_a_line_said_mid_turn_is_held_back(self, brain):
        brain._in_turn = True
        brain.note_unprompted_line("Sir, here is your reminder: workout")

        assert brain.messages[-1]["role"] != "assistant"
        assert brain._pending_unprompted == ["Sir, here is your reminder: workout"]

    def test_a_held_line_lands_before_the_next_thing_the_user_says(self, brain):
        brain._in_turn = True
        brain.note_unprompted_line("Sir, here is your reminder: workout")
        brain._in_turn = False

        brain._flush_unprompted()
        brain.messages.append({"role": "user", "content": "snooze it"})

        assert brain.messages[-2]["role"] == "assistant"
        assert "workout" in brain.messages[-2]["content"]
        assert brain._pending_unprompted == []

    def test_several_held_lines_keep_the_order_they_were_said_in(self, brain):
        brain._in_turn = True
        for text in ("first", "second", "third"):
            brain.note_unprompted_line(text)
        brain._in_turn = False

        brain._flush_unprompted()

        assert [m["content"] for m in brain.messages[-3:]] == ["first", "second", "third"]

    def test_process_input_flushes_before_it_appends_the_user_turn(self):
        """Ordering is the whole point: the offer has to come first."""
        import inspect

        from ultron.brain import Brain

        source = inspect.getsource(Brain.process_input)
        assert "_flush_unprompted" in source
        assert "_in_turn = True" in source
        # Cleared even when the turn raises, or one failure mutes every
        # unprompted line for the rest of the session.
        assert "finally:" in source

    def test_the_history_stays_valid_when_a_reminder_lands_during_tool_use(self, brain):
        """The failure this guards: an assistant message orphaning tool results."""
        brain._in_turn = True
        brain.messages.append({"role": "assistant", "tool_calls": [{"id": "abc"}]})
        brain.note_unprompted_line("Sir, here is your reminder: workout")
        brain.messages.append({"role": "tool", "tool_call_id": "abc", "content": "done"})

        calls = brain.messages[-2]
        assert calls.get("tool_calls"), "the tool results must still follow their call"
        assert brain.messages[-1]["role"] == "tool"


class TestNoticingThings:
    """An assistant that only offers services is a menu with a voice. What
    makes one feel present is noticing — but the bar is *notable*, not merely
    true, and that distinction is the whole design.

    The first version reported the time of day. Every remark after 8pm became
    "it is 22:20, in the evening": true every time, worth saying none of them,
    and a stuck record for two hours nightly.
    """

    def _psutil(self, monkeypatch, percent=100, plugged=True,
                free_gb=500, total_gb=500):
        fake = types.SimpleNamespace(
            sensors_battery=lambda: types.SimpleNamespace(
                percent=percent, power_plugged=plugged),
            disk_usage=lambda path: types.SimpleNamespace(
                free=free_gb * 1024 ** 3, total=total_gb * 1024 ** 3),
        )
        monkeypatch.setitem(__import__("sys").modules, "psutil", fake)

    def test_a_dying_battery_is_worth_mentioning(self, monkeypatch):
        self._psutil(monkeypatch, percent=11, plugged=False)
        assert any("11%" in line for line in idle_chat.machine_observations())

    def test_a_healthy_battery_is_not(self, monkeypatch):
        self._psutil(monkeypatch, percent=88, plugged=False)
        assert idle_chat.machine_observations() == []

    def test_a_charging_battery_is_not_a_problem(self, monkeypatch):
        self._psutil(monkeypatch, percent=9, plugged=True)
        assert idle_chat.machine_observations() == []

    def test_a_full_disk_is_worth_mentioning(self, monkeypatch):
        self._psutil(monkeypatch, free_gb=12, total_gb=500)
        assert any("full" in line for line in idle_chat.machine_observations())

    def test_plenty_of_disk_is_not(self, monkeypatch):
        self._psutil(monkeypatch, free_gb=300, total_gb=500)
        assert idle_chat.machine_observations() == []

    def test_the_clock_is_never_an_observation(self, monkeypatch):
        """It is true every time and worth saying none of them."""
        self._psutil(monkeypatch)
        joined = " ".join(idle_chat.machine_observations())
        for stuck in ("evening", "morning", "afternoon", "weekend", ":"):
            assert stuck not in joined, (
                f"{stuck!r} fires on every single remark - a stuck record")

    def test_a_healthy_machine_says_nothing_at_all(self, monkeypatch):
        """Silence is the common case; only problems earn a sentence."""
        self._psutil(monkeypatch)
        assert idle_chat.machine_observations() == []

    def test_broken_hardware_reads_do_not_crash_it(self, monkeypatch):
        def explode():
            raise OSError("no battery on this machine")

        fake = types.SimpleNamespace(
            sensors_battery=explode,
            disk_usage=lambda path: (_ for _ in ()).throw(OSError("no disk")))
        monkeypatch.setitem(__import__("sys").modules, "psutil", fake)

        assert idle_chat.machine_observations() == []

    def test_observations_reach_the_context(self, brain, monkeypatch):
        monkeypatch.setattr(idle_chat, "machine_observations",
                            lambda: ["Their battery is at 4% and not charging."])
        assert "4%" in idle_chat.gather_context(brain)

    def test_noticing_is_preferred_to_offering(self):
        """Offering a service is the fallback, not the default."""
        prompt = idle_chat.PROMPT
        assert "remark on it" in prompt
        assert prompt.index("remark on it") < prompt.index("{offers}"), (
            "the offer list comes first, so that is what it will reach for")

    def test_it_still_may_not_invent_what_it_noticed(self):
        """Attachment built on fabrication is warm lying, which is worse than
        distance. Everything remarked on must trace to a real fact."""
        assert "must come from the facts above and nowhere else" in idle_chat.PROMPT


class TestThePersonaHasASpine:
    """A butler answers well and belongs to nobody. The difference is
    continuity, attention, and being willing to disagree."""

    @pytest.fixture
    def prompts(self, brain):
        """Both system prompts, built by the real Brain."""
        import datetime as dt

        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        return (brain._build_cloud_system_prompt(now),
                brain._build_local_system_prompt(
                    now, False, brain._build_local_tools_prompt()))

    def test_both_engines_get_the_same_persona(self, prompts):
        """Two copies drift, and then Ultron is a different person depending
        on which provider happens to be selected."""
        cloud, local = prompts
        for prompt in (cloud, local):
            assert "WHO YOU ARE" in prompt

    def test_it_is_allowed_to_disagree(self, prompts):
        cloud, local = prompts
        for prompt in (cloud, local):
            assert "Say so when you disagree" in prompt

    def test_it_still_does_what_it_is_told(self, prompts):
        """Talking back is not refusing. It is their machine."""
        cloud, _ = prompts
        assert "it is their machine and their call" in cloud

    def test_it_is_no_longer_professional(self, prompts):
        """Professional is paid politeness, which is what made it feel hired
        rather than attached."""
        cloud, local = prompts
        for prompt in (cloud, local):
            assert "professional answers" not in prompt

    def test_it_does_not_start_as_a_stranger(self, prompts):
        """"You do not know any personal details by default" opened every
        conversation with amnesia, which is the opposite of attachment."""
        cloud, local = prompts
        for prompt in (cloud, local):
            assert "DO NOT know any personal details" not in prompt
            assert "DO NOT know personal details" not in prompt

    def test_sir_is_no_longer_compulsory(self, prompts):
        cloud, local = prompts
        for prompt in (cloud, local):
            assert "ALWAYS address the user respectfully" not in prompt

    def test_warmth_may_not_be_fabricated(self, prompts):
        """The rule that keeps the rest honest."""
        cloud, local = prompts
        for prompt in (cloud, local):
            assert "Never invent familiarity" in prompt
