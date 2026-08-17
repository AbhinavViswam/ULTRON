"""Failures must be diagnosable.

Ultron runs without a console, so a swallowed exception becomes "it just
didn't do anything" with nothing to look at afterwards. Each of these forces a
real failure and asserts something reaches the log.
"""

import contextlib
import io

import numpy as np
import pytest


def output_of(call):
    """Runs `call` and returns whatever it printed."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        with contextlib.suppress(Exception):
            call()
    return buffer.getvalue()


class TestSpeechToText:
    """The clearest case: a network drop looked exactly like saying nothing."""

    def test_transcription_failure_is_reported(self):
        from ultron.listener import VoiceListener

        listener = VoiceListener()

        class Unreachable:
            def recognize_google(self, *args, **kwargs):
                raise OSError("network is unreachable")

        listener.recognizer = Unreachable()
        printed = output_of(
            lambda: listener._process_audio(np.zeros(1600, dtype=np.int16))
        )
        assert "transcription failed" in printed
        assert "network is unreachable" in printed

    def test_unintelligible_speech_is_reported_but_rate_limited(self):
        """Silence made "ignored me" and "never heard me" identical, and they
        have opposite fixes. But this fires many times a minute in a noisy
        room, so it is reported rarely rather than never."""
        import speech_recognition as sr

        from ultron.listener import VoiceListener

        listener = VoiceListener()

        class Mumble:
            def recognize_google(self, *args, **kwargs):
                raise sr.UnknownValueError()

        listener.recognizer = Mumble()
        audio = np.zeros(1600, dtype=np.int16)

        first = output_of(lambda: listener._process_audio(audio))
        assert "could not make out" in first
        assert "threshold" in first, "the numbers are what make it diagnosable"

        # Immediately again: the room is noisy, not the user repeating himself.
        assert output_of(lambda: listener._process_audio(audio)) == ""


class TestCallbackFailures:
    def test_a_broken_mic_level_listener_is_reported(self):
        from ultron.listener import VoiceListener

        listener = VoiceListener()
        listener.on_level(lambda level, speech: 1 / 0)
        assert "level listener failed" in output_of(lambda: listener._emit_level(0.5, False))

    def test_a_broken_voice_level_listener_is_reported(self):
        from ultron.speaker import VoiceSpeaker

        speaker = VoiceSpeaker.__new__(VoiceSpeaker)
        speaker._level_listeners = [lambda level: 1 / 0]
        assert "level listener failed" in output_of(lambda: speaker._emit_level(0.3))


class TestToolLayer:
    def test_an_unparsable_tool_call_is_reported(self, brain):
        printed = output_of(
            lambda: brain._parse_tool_calls_from_text(
                '<tool_call>{"name": "x", oops}</tool_call>'
            )
        )
        assert "unparsable tool call" in printed

    def test_a_failed_usage_write_is_reported(self, brain, monkeypatch):
        import builtins
        import types

        real_open = builtins.open

        def deny(path, mode="r", *args, **kwargs):
            if "w" in mode:
                raise PermissionError("read-only filesystem")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", deny)
        brain.active_api, brain.selected_model = "localapi", "test"
        response = types.SimpleNamespace(
            usage=types.SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            )
        )
        assert "could not write" in output_of(lambda: brain._record_usage(response))


class TestStuckKeys:
    def test_a_failed_key_release_is_reported(self, monkeypatch):
        """Silence here is why the desktop stays broken with no explanation."""
        import ultron.automation as automation

        class NoDll:
            def __getattr__(self, name):
                raise OSError("no user32")

        monkeypatch.setattr(automation.ctypes, "windll", NoDll())
        assert "failed to release held keys" in output_of(automation._release_quietly)


class TestNoSilentHandlersRemain:
    """Any new `except: pass` must carry a comment explaining itself."""

    def test_every_silent_handler_is_justified(self):
        import glob
        import os
        import re

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        paths = glob.glob(os.path.join(root, "ultron", "**", "*.py"), recursive=True)
        for path in paths:
            lines = open(path, encoding="utf-8").read().splitlines()
            for index, line in enumerate(lines):
                if not re.match(r"\s*except[^\n]*:\s*$", line):
                    continue
                cursor = index + 1
                commented = False
                while cursor < len(lines) and lines[cursor].strip().startswith("#"):
                    commented = True
                    cursor += 1
                if cursor < len(lines) and lines[cursor].strip() == "pass" and not commented:
                    offenders.append(f"{os.path.relpath(path, root)}:{index + 1}")
        assert not offenders, (
            "silent exception handlers without a reason:\n  " + "\n  ".join(offenders)
        )
