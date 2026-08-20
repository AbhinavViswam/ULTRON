"""Recognition that happens on this machine, and the threshold it removes.

The microphone bug was never really about calibration. Google's recogniser is
a network call, so *something* had to decide which audio was worth sending,
and that something was an amplitude threshold. Set above the quiet start of a
word, commands vanished. Every fix was a hunt for a number loud enough to
reject a fan and quiet enough to accept a tired voice at midnight.

Vosk runs locally, so nothing has to be filtered out before recognition. The
tests that matter most here are the ones proving no amplitude gate remains:
audio far below MIN_THRESHOLD must still reach the recogniser.

Nothing here loads the real model — these have to pass on a clone with no
55MB download, which is also the reason the fallback is tested at all.
"""

import json
import os
import queue
import threading

import numpy as np
import pytest

from ultron import vosk_engine
from ultron.listener import MIN_THRESHOLD, VoiceListener


class FakeVosk:
    """Records every chunk handed to it; returns a phrase when told to."""

    name = "fake-model"

    def __init__(self, phrase_after=None, text="pause", confidence=0.9):
        self.chunks = []
        self.phrase_after = phrase_after
        self.text = text
        self.confidence = confidence

    def accept(self, chunk):
        self.chunks.append(chunk)
        if self.phrase_after is not None and len(self.chunks) == self.phrase_after:
            return (self.text, self.confidence)
        return None

    def flush(self):
        return None


@pytest.fixture
def listener():
    return VoiceListener()


def _chunk(amplitude, size=800):
    return np.full((size, 1), amplitude, dtype=np.int16)


def _drive(listener, monkeypatch, chunks):
    """Feeds chunks through the real audio callback."""
    import ultron.listener as module

    holder = {}

    class FakeStream:
        def __init__(self, **kwargs):
            holder["callback"] = kwargs["callback"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(module.sd, "InputStream", FakeStream)
    monkeypatch.setattr(module.sd, "sleep",
                        lambda ms: setattr(listener, "is_listening", False))
    listener.is_listening = True
    listener._listen_loop()          # captures the callback, then stops
    listener.is_listening = True
    for chunk in chunks:
        holder["callback"](chunk, len(chunk), None, None)


class TestNoAmplitudeGateRemains:
    """The whole point of the change. If these fail, the old bug is back."""

    def test_audio_far_below_the_old_threshold_still_reaches_the_recogniser(
            self, listener, monkeypatch):
        listener._vosk = FakeVosk()
        listener._audio_queue = queue.Queue(maxsize=64)
        listener.speech_threshold = 300.0        # absurdly deaf, deliberately

        whisper = int(MIN_THRESHOLD) - 9         # 1: essentially inaudible
        _drive(listener, monkeypatch, [_chunk(whisper) for _ in range(5)])

        queued = listener._audio_queue.qsize()
        assert queued == 5, (
            f"only {queued} of 5 near-silent chunks were queued — something "
            f"is still filtering on loudness")

    def test_every_chunk_is_kept_whatever_its_level(self, listener, monkeypatch):
        listener._vosk = FakeVosk()
        listener._audio_queue = queue.Queue(maxsize=64)
        listener.speech_threshold = 50.0

        levels = [0, 1, 5, 9, 40, 900, 3, 0]
        _drive(listener, monkeypatch, [_chunk(v) for v in levels])

        assert listener._audio_queue.qsize() == len(levels)

    def test_the_quiet_start_of_a_word_is_not_clipped(self, listener,
                                                      monkeypatch):
        """No pre-roll is needed when nothing was discarded to begin with."""
        listener._vosk = FakeVosk()
        listener._audio_queue = queue.Queue(maxsize=64)
        listener.speech_threshold = 100.0

        ramp = [2, 4, 8, 16, 32, 64, 128, 256]      # a word getting louder
        _drive(listener, monkeypatch, [_chunk(v) for v in ramp])

        heard = np.frombuffer(b"".join(listener._audio_queue.queue),
                              dtype=np.int16)
        assert set(np.unique(heard)) == set(ramp), "the run-up was lost"

    def test_the_level_meter_still_works(self, listener, monkeypatch):
        listener._vosk = FakeVosk()
        listener._audio_queue = queue.Queue(maxsize=64)
        seen = []
        listener.on_level(lambda level, speech: seen.append(level))

        _drive(listener, monkeypatch, [_chunk(400)])

        assert seen, "the UI meter went dead on the offline path"


class TestTheQueue:
    def test_it_is_bounded_and_drops_the_oldest(self, listener):
        listener._audio_queue = queue.Queue(maxsize=3)
        for value in (1, 2, 3, 4):
            listener._enqueue(np.full((2, 1), value, dtype=np.int16))

        assert listener._audio_queue.qsize() == 3
        kept = [np.frombuffer(c, dtype=np.int16)[0]
                for c in listener._audio_queue.queue]
        assert 1 not in kept, "the oldest should be dropped, not the newest"
        assert 4 in kept

    def test_a_full_queue_never_raises_into_the_audio_thread(self, listener):
        listener._audio_queue = queue.Queue(maxsize=1)
        for _ in range(10):
            listener._enqueue(_chunk(5))


class TestConfidence:
    def test_a_confident_phrase_is_delivered(self, listener):
        got = []
        listener.callback_func = got.append
        listener._on_phrase("pause the music", 0.9)

        assert got == ["pause the music"]

    def test_nothing_is_filtered_by_default(self, listener):
        """The complaint is "it does not hear me". A guessed filter is that
        same complaint wearing a different hat."""
        got = []
        listener.callback_func = got.append
        listener._on_phrase("pause", 0.01)

        assert got == ["pause"], "the default must not silently drop speech"

    def test_a_configured_floor_is_enforced(self, listener, monkeypatch):
        import ultron.listener as module

        monkeypatch.setattr(module.config, "get",
                            lambda key, default=None:
                            0.7 if key == "vosk.min_confidence" else default)
        got = []
        listener.callback_func = got.append
        listener._on_phrase("mumble", 0.4)
        listener._on_phrase("pause", 0.8)

        assert got == ["pause"]

    @pytest.mark.parametrize("bad", ["", None, "abc", {}])
    def test_a_broken_setting_falls_back_instead_of_deafening_it(
            self, listener, monkeypatch, bad):
        import ultron.listener as module

        monkeypatch.setattr(module.config, "get",
                            lambda key, default=None:
                            bad if key == "vosk.min_confidence" else default)
        assert listener.min_confidence() == 0.0

    def test_the_self_hearing_veto_still_applies(self, listener):
        got = []
        listener.callback_func = got.append
        listener.ignore_check = lambda text: text == "its own voice"

        listener._on_phrase("its own voice", 1.0)
        listener._on_phrase("a real command", 1.0)

        assert got == ["a real command"]


class TestReadingResults:
    def test_the_weakest_word_is_the_confidence(self):
        """Averaging hides the one word guessed out of noise, and that word is
        what turns a real phrase into a wrong command."""
        raw = json.dumps({"text": "play arijit singh",
                          "result": [{"conf": 0.99}, {"conf": 0.2},
                                     {"conf": 0.98}]})
        assert vosk_engine.VoskTranscriber._read(raw) == ("play arijit singh", 0.2)

    def test_empty_text_is_not_a_phrase(self):
        assert vosk_engine.VoskTranscriber._read(
            json.dumps({"text": "   "})) is None

    def test_missing_confidences_are_not_treated_as_zero(self):
        """A result without word detail must not look like total uncertainty."""
        assert vosk_engine.VoskTranscriber._read(
            json.dumps({"text": "pause"})) == ("pause", 1.0)

    @pytest.mark.parametrize("raw", ["not json", "", None, "[]"])
    def test_junk_does_not_crash_the_decoder(self, raw):
        assert vosk_engine.VoskTranscriber._read(raw) is None


class TestChoosingTheModel:
    def _only(self, key_wanted, value):
        return lambda key, default=None: value if key == key_wanted else default

    def test_a_configured_path_that_exists_wins(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vosk_engine.config, "get",
                            self._only("vosk.model_path", str(tmp_path)))
        assert vosk_engine.model_path() == str(tmp_path)

    def test_a_configured_path_that_is_wrong_is_not_silently_replaced(
            self, monkeypatch, tmp_path):
        """Quietly loading some other model would be worse than finding none:
        the user would be told it works while hearing a model they did not
        choose."""
        missing = str(tmp_path / "not-here")
        monkeypatch.setattr(vosk_engine.config, "get",
                            self._only("vosk.model_path", missing))
        assert vosk_engine.model_path() is None

    def test_any_unzipped_model_is_found(self, monkeypatch, tmp_path):
        (tmp_path / "vosk-model-en-in-0.5").mkdir()
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(tmp_path))
        monkeypatch.setattr(vosk_engine.config, "get",
                            self._only("vosk.model_path", ""))
        assert vosk_engine.model_path().endswith("vosk-model-en-in-0.5")

    def test_no_model_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(tmp_path))
        monkeypatch.setattr(vosk_engine.config, "get",
                            self._only("vosk.model_path", ""))
        assert vosk_engine.model_path() is None


class TestFallingBack:
    """A missing 55MB download must not leave someone with no voice input."""

    def test_it_says_why_rather_than_failing_quietly(self, listener,
                                                     monkeypatch, capsys):
        monkeypatch.setattr(vosk_engine, "unavailable_reason",
                            lambda: "no model in data/models")

        assert listener._start_offline_engine() is False
        out = capsys.readouterr().out
        assert "no model in data/models" in out
        assert "Google" in out, "the user must know which engine is running"

    def test_the_threshold_path_is_untouched_without_vosk(self, listener,
                                                          monkeypatch):
        assert listener._vosk is None
        listener.speech_threshold = 100.0
        listener._audio_queue = queue.Queue(maxsize=8)

        _drive(listener, monkeypatch, [_chunk(5) for _ in range(4)])

        assert listener._audio_queue.qsize() == 0, (
            "the fallback path must not be feeding the offline queue")

    def test_a_model_that_fails_to_load_falls_back(self, listener, monkeypatch,
                                                   capsys):
        monkeypatch.setattr(vosk_engine, "unavailable_reason", lambda: None)
        monkeypatch.setattr(vosk_engine, "model_path", lambda: "x")

        def boom(*args, **kwargs):
            raise RuntimeError("corrupt model")

        monkeypatch.setattr(vosk_engine, "VoskTranscriber", boom)

        assert listener._start_offline_engine() is False
        assert listener._vosk is None, "a half-loaded engine would eat audio"
        assert "corrupt model" in capsys.readouterr().out

    def test_it_can_be_switched_off(self, monkeypatch):
        monkeypatch.setattr(vosk_engine.config, "get",
                            lambda key, default=None:
                            False if key == "vosk.enabled" else default)
        assert "disabled" in vosk_engine.unavailable_reason()

    def test_the_settings_ship_with_defaults(self):
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "settings.default.json")) as f:
            defaults = json.load(f)
        assert defaults["vosk"]["enabled"] is True
        assert defaults["vosk"]["min_confidence"] == 0.0


def _engine(monkeypatch, name, endpointing=True):
    import ultron.listener as module

    def get(key, default=None):
        if key == "speech_engine":
            return name
        if key == "vosk.use_for_endpointing":
            return endpointing
        return default

    monkeypatch.setattr(module.config, "get", get)


class TestWhoFindsTheWordsAndWhoReadsThem:
    """Two questions hide behind "which engine": who notices that someone is
    speaking, and who works out what they said. Google is much better at
    Indian English than a 55MB model — but Google is a network call, and
    rationing those calls is the entire reason a threshold existed."""

    def _speak(self, listener, monkeypatch, engine):
        _engine(monkeypatch, engine)
        listener._vosk = FakeVosk()
        listener._audio_queue = queue.Queue(maxsize=64)
        listener._segment = [np.full(400, 7, dtype=np.int16).tobytes()]
        return listener

    def test_google_is_handed_the_audio_vosk_delimited(self, listener,
                                                       monkeypatch):
        self._speak(listener, monkeypatch, "google")
        sent = []
        monkeypatch.setattr(listener, "_process_audio", sent.append)
        delivered = []
        listener.callback_func = delivered.append

        listener._finish_phrase("vosks rough guess", 0.9)

        assert len(sent) == 1, "Google never received the phrase"
        assert list(np.unique(sent[0])) == [7]
        assert delivered == [], "Vosk's words were used instead of Google's"

    def test_the_offline_engine_uses_its_own_words(self, listener, monkeypatch):
        self._speak(listener, monkeypatch, "vosk")
        monkeypatch.setattr(listener, "_process_audio",
                            lambda audio: pytest.fail("the network was used"))
        delivered = []
        listener.callback_func = delivered.append

        listener._finish_phrase("pause the music", 0.9)

        assert delivered == ["pause the music"]

    def test_silence_is_never_sent_over_the_network(self, listener,
                                                    monkeypatch):
        """Vosk producing text is the signal that the audio held speech. No
        phrase, no call — which is what a threshold used to be for."""
        _engine(monkeypatch, "google")
        listener._vosk = FakeVosk()
        listener._audio_queue = queue.Queue(maxsize=8)
        listener.is_listening = False
        monkeypatch.setattr(listener, "_process_audio",
                            lambda audio: pytest.fail("silence was uploaded"))

        listener._decode_loop()          # flush returns nothing

    def test_a_phrase_with_no_audio_behind_it_is_not_uploaded(self, listener,
                                                              monkeypatch):
        """Reached at shutdown, when the recogniser reports a phrase but the
        audio for it has already been handed over. There is nothing to send,
        and sending nothing is a wasted round trip on an empty clip."""
        _engine(monkeypatch, "google")
        listener._segment = []
        monkeypatch.setattr(listener, "_process_audio",
                            lambda audio: pytest.fail("uploaded an empty clip"))

        listener._finish_phrase("something", 0.9)

    def test_the_pending_segment_is_bounded(self, listener):
        listener._segment = [np.zeros(16000, dtype=np.int16).tobytes()
                             for _ in range(120)]      # ~60s
        listener._trim_segment()

        held = sum(len(c) for c in listener._segment) / 2 / listener.sample_rate
        assert held <= 30.0

    def test_trimming_keeps_the_most_recent_audio(self, listener):
        """The newest audio is what someone just said."""
        listener._segment = [np.full(16000, i, dtype=np.int16).tobytes()
                             for i in range(1, 121)]
        listener._trim_segment()

        kept = np.frombuffer(b"".join(listener._segment), dtype=np.int16)
        assert 120 in kept and 1 not in kept

    @pytest.mark.parametrize("configured,expected", [
        ("google", "google"), ("vosk", "vosk"), ("VOSK", "vosk"),
        ("  google  ", "google"), ("nonsense", "google"), ("", "google"),
        (None, "google"),
    ])
    def test_the_setting_is_read_forgivingly(self, listener, monkeypatch,
                                             configured, expected):
        import ultron.listener as module
        monkeypatch.setattr(module.config, "get",
                            lambda key, default=None:
                            configured if key == "speech_engine" else default)
        assert listener.speech_engine() == expected

    def test_endpointing_can_be_turned_off_for_the_old_behaviour(
            self, listener, monkeypatch, capsys):
        _engine(monkeypatch, "google", endpointing=False)

        assert listener.uses_offline_endpointing() is False
        assert listener._start_offline_engine() is False
        assert "amplitude threshold" in capsys.readouterr().out

    def test_the_offline_engine_always_endpoints_for_itself(self, listener,
                                                            monkeypatch):
        """Vosk cannot transcribe segments it was never asked to find."""
        _engine(monkeypatch, "vosk", endpointing=False)
        assert listener.uses_offline_endpointing() is True

    def test_the_settings_ship_with_the_split(self):
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "settings.default.json")) as f:
            defaults = json.load(f)
        assert defaults["speech_engine"] == "google"
        assert defaults["vosk"]["use_for_endpointing"] is True


class TestShutdown:
    def test_the_last_phrase_is_not_lost(self, listener, monkeypatch):
        """Stopping mid-sentence should still deliver what was said."""
        _engine(monkeypatch, "vosk")
        fake = FakeVosk()
        fake.flush = lambda: ("goodnight", 0.9)
        listener._vosk = fake
        listener._audio_queue = queue.Queue(maxsize=8)
        listener.is_listening = False

        got = []
        listener.callback_func = got.append
        listener._decode_loop()

        assert got == ["goodnight"]

    def test_the_last_phrase_reaches_google_too(self, listener, monkeypatch):
        _engine(monkeypatch, "google")
        fake = FakeVosk()
        fake.flush = lambda: ("goodnight", 0.9)
        listener._vosk = fake
        listener._audio_queue = queue.Queue(maxsize=8)
        listener._segment = [np.full(400, 3, dtype=np.int16).tobytes()]
        listener.is_listening = False

        sent = []
        monkeypatch.setattr(listener, "_process_audio", sent.append)
        listener._decode_loop()

        assert len(sent) == 1

    def test_stop_waits_for_the_decoder(self, listener):
        listener._decoder = threading.Thread(target=lambda: None)
        listener._decoder.start()
        listener.stop()

        assert listener.is_listening is False
        assert listener._decoder is None


def _make_archive(tmp_path, inner_name="vosk-model-small-en-in-0.4"):
    """A zip shaped like a real model download, served from disk."""
    import zipfile

    root = tmp_path / "src" / inner_name
    (root / "am").mkdir(parents=True)
    (root / "am" / "final.mdl").write_text("weights")
    (root / "README").write_text("model")

    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for item in root.rglob("*"):
            bundle.write(item, item.relative_to(root.parent))
    return archive.as_uri()          # file:// - real download code, no network


class TestGettingTheModelInTheFirstPlace:
    """The model was downloaded by hand on one machine. Everywhere else the
    engine reported itself missing and fell back to Google *and its amplitude
    threshold*, so the microphone bug returned on any fresh clone while the
    code still looked correct."""

    @pytest.fixture(autouse=True)
    def _sandbox(self, monkeypatch, tmp_path):
        """No test may write to the real models directory or the network."""
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(tmp_path / "models"))
        monkeypatch.setattr(vosk_engine, "download_model",
                            lambda *a, **k: pytest.fail("downloaded unexpectedly"))

    def _settings(self, monkeypatch, **values):
        monkeypatch.setattr(vosk_engine.config, "get",
                            lambda key, default=None: values.get(key, default))

    def test_an_existing_model_is_never_re_downloaded(self, monkeypatch,
                                                      tmp_path):
        model = tmp_path / "models" / "vosk-model-small-en-in-0.4"
        model.mkdir(parents=True)
        self._settings(monkeypatch)

        assert vosk_engine.ensure_model() == str(model)

    def test_nothing_is_downloaded_when_the_engine_is_off(self, monkeypatch):
        self._settings(monkeypatch, **{"vosk.enabled": False})
        assert vosk_engine.ensure_model() is None

    def test_auto_download_can_be_declined(self, monkeypatch):
        """Someone on a metered connection gets to say no."""
        self._settings(monkeypatch, **{"vosk.auto_download": False})
        assert vosk_engine.ensure_model() is None

    def test_declining_is_explained_rather_than_looking_broken(self,
                                                               monkeypatch):
        self._settings(monkeypatch, **{"vosk.auto_download": False})
        reason = vosk_engine.unavailable_reason()
        assert "auto-download is" in reason and "off" in reason

    def test_a_real_archive_is_unpacked_into_place(self, monkeypatch, tmp_path):
        monkeypatch.undo()      # this one exercises the real download_model
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(tmp_path / "models"))
        url = _make_archive(tmp_path)

        got = vosk_engine.download_model(url=url)

        assert got is not None
        assert os.path.isfile(os.path.join(got, "am", "final.mdl"))

    def test_an_archive_named_differently_is_still_found(self, monkeypatch,
                                                         tmp_path):
        monkeypatch.undo()
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(tmp_path / "models"))
        url = _make_archive(tmp_path, inner_name="vosk-model-en-in-0.5")

        got = vosk_engine.download_model(url=url, name="whatever-we-asked-for")

        assert got is not None and os.path.isdir(got)

    def test_a_failed_download_leaves_nothing_behind(self, monkeypatch,
                                                    tmp_path):
        """A half-unpacked directory would look like a working model and fail
        later, far from the cause."""
        monkeypatch.undo()
        models = tmp_path / "models"
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(models))

        assert vosk_engine.download_model(
            url=(tmp_path / "nothing-here.zip").as_uri()) is None

        leftovers = list(models.iterdir()) if models.exists() else []
        assert leftovers == [], f"scratch files survived: {leftovers}"

    def test_an_archive_without_a_model_is_refused(self, monkeypatch, tmp_path):
        import zipfile

        monkeypatch.undo()
        models = tmp_path / "models"
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(models))
        junk = tmp_path / "junk.zip"
        with zipfile.ZipFile(junk, "w") as bundle:
            bundle.writestr("readme.txt", "not a model")

        assert vosk_engine.download_model(url=junk.as_uri()) is None
        assert list(models.iterdir()) == []

    def test_a_failure_says_ultron_still_works(self, monkeypatch, tmp_path,
                                               capsys):
        """The fallback is real, and someone reading the log should know it."""
        monkeypatch.undo()
        monkeypatch.setattr(vosk_engine, "MODELS_DIR", str(tmp_path / "models"))

        vosk_engine.download_model(url=(tmp_path / "gone.zip").as_uri())

        out = capsys.readouterr().out
        assert "still works" in out and "Google" in out

    def test_the_setting_ships_with_a_default(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "settings.default.json")) as f:
            defaults = json.load(f)
        assert defaults["vosk"]["auto_download"] is True


class TestStartupFetchesTheModel:
    def test_the_listener_tries_before_giving_up(self, listener, monkeypatch):
        """Otherwise the download never happens on the one run that needs it."""
        called = []
        monkeypatch.setattr(vosk_engine, "ensure_model",
                            lambda: called.append(True))
        monkeypatch.setattr(vosk_engine, "unavailable_reason",
                            lambda: "no model")

        listener._start_offline_engine()

        assert called, "startup never attempted to fetch the model"


class TestASentenceSpokenWithABreathInIt:
    """One sentence must not arrive as two requests.

    The offline endpointer calls the end of an utterance on a short trailing
    pause. That is right for "pause the music" and wrong for someone drawing
    breath mid-sentence: the phrase ends, the rest arrives separately, and
    Ultron answers half a sentence and then answers the remainder with no
    idea it belonged to the first.

    So an ended phrase is held for a moment. If more speech follows inside
    that window, the two are joined and delivered as the one sentence they
    always were.
    """

    def _listener(self, monkeypatch):
        from ultron.listener import VoiceListener

        listener = VoiceListener(callback_func=lambda t: heard.append(t))
        heard = []
        listener.delivered = heard
        listener.ignore_check = None

        clock = {"now": 1000.0}
        listener._clock = lambda: clock["now"]
        listener.clock = clock
        monkeypatch.setattr(listener, "_finish_phrase",
                            lambda text, confidence: heard.append(text))
        return listener

    def test_two_halves_of_a_sentence_become_one_request(self, monkeypatch):
        listener = self._listener(monkeypatch)

        listener._hold_phrase("send a message to", 0.9)
        listener.clock["now"] += 0.4          # a breath, not a full stop
        listener._hold_phrase("amma saying I am late", 0.8)
        listener.clock["now"] += 2.0          # genuinely finished now
        listener._release_if_due()

        assert listener.delivered == ["send a message to amma saying I am late"]

    def test_nothing_is_sent_while_the_sentence_may_continue(self, monkeypatch):
        """Delivering at once is the bug. It has to wait to know."""
        listener = self._listener(monkeypatch)

        listener._hold_phrase("what is the weather", 0.9)
        listener.clock["now"] += 0.3
        listener._release_if_due()

        assert listener.delivered == [], "it sent half a sentence"

    def test_a_finished_sentence_is_still_delivered(self, monkeypatch):
        """The join must not turn into a listener that never answers."""
        listener = self._listener(monkeypatch)

        listener._hold_phrase("pause the music", 0.9)
        listener.clock["now"] += 1.5
        listener._release_if_due()

        assert listener.delivered == ["pause the music"]

    def test_three_fragments_all_join(self, monkeypatch):
        listener = self._listener(monkeypatch)

        for fragment in ("open chrome", "and search for", "onam recipes"):
            listener._hold_phrase(fragment, 0.9)
            listener.clock["now"] += 0.5
        listener.clock["now"] += 2.0
        listener._release_if_due()

        assert listener.delivered == ["open chrome and search for onam recipes"]

    def test_two_separate_commands_stay_separate(self, monkeypatch):
        """The window must be short enough that a real pause still divides
        two commands, or every following sentence would be glued on."""
        listener = self._listener(monkeypatch)

        listener._hold_phrase("pause the music", 0.9)
        listener.clock["now"] += 3.0
        listener._release_if_due()
        listener._hold_phrase("what is the time", 0.9)
        listener.clock["now"] += 3.0
        listener._release_if_due()

        assert listener.delivered == ["pause the music", "what is the time"]

    def test_the_least_confident_part_speaks_for_the_whole(self, monkeypatch):
        """A sentence is only as trustworthy as its worst-heard piece."""
        from ultron.listener import VoiceListener

        listener = VoiceListener()
        clock = {"now": 1000.0}
        listener._clock = lambda: clock["now"]
        seen = []
        listener._finish_phrase = lambda text, confidence: seen.append(confidence)

        listener._hold_phrase("clear words", 0.95)
        listener._hold_phrase("mumbled bit", 0.20)
        clock["now"] += 2.0
        listener._release_if_due()

        assert seen == [0.20]

    def test_stopping_delivers_what_is_held(self, monkeypatch):
        """Otherwise the last thing said before shutdown is silently lost."""
        listener = self._listener(monkeypatch)

        listener._hold_phrase("turn off the lights", 0.9)
        listener._release_pending()

        assert listener.delivered == ["turn off the lights"]

    def test_the_wait_can_be_turned_off(self, monkeypatch):
        """Zero means the old behaviour, for anyone who prefers speed."""
        listener = self._listener(monkeypatch)
        monkeypatch.setattr(listener, "join_window", lambda: 0.0)

        listener._hold_phrase("pause", 0.9)

        assert listener.delivered == ["pause"], "zero should deliver at once"

    def test_a_nonsense_setting_falls_back(self, monkeypatch):
        from ultron.listener import PHRASE_JOIN_SECONDS, VoiceListener
        import ultron.listener as module

        listener = VoiceListener()
        monkeypatch.setattr(module.config, "get",
                            lambda path, default=None: "soon")
        assert listener.join_window() == PHRASE_JOIN_SECONDS

    def test_a_negative_setting_is_not_treated_as_the_past(self, monkeypatch):
        from ultron.listener import VoiceListener
        import ultron.listener as module

        listener = VoiceListener()
        monkeypatch.setattr(module.config, "get",
                            lambda path, default=None: -5)
        assert listener.join_window() == 0.0


class TestTheDecodeLoopActuallyReleases:
    """Holding a phrase is only half of it -- something has to let go.

    In use, the release happens because silence follows, and silence is
    precisely when no audio chunk is arriving. Testing _release_if_due on its
    own cannot see whether the loop ever calls it: dropping the call from the
    silent branch left every unit test passing while a held sentence would
    hang until the next word was spoken.
    """

    def _running_listener(self, monkeypatch, heard_text="turn the lights off"):
        import queue as queue_module

        from ultron.listener import VoiceListener

        listener = VoiceListener()
        listener.ignore_check = None
        listener._audio_queue = queue_module.Queue()
        listener.is_listening = True
        # A short window so the test does not sit for a real second.
        monkeypatch.setattr(listener, "join_window", lambda: 0.05)

        calls = {"n": 0}

        class FakeVosk:
            def accept(self, chunk):
                calls["n"] += 1
                return (heard_text, 0.9) if calls["n"] == 1 else None

            def flush(self):
                return None

        listener._vosk = FakeVosk()

        delivered = []
        monkeypatch.setattr(listener, "_finish_phrase",
                            lambda text, confidence: delivered.append(text))
        return listener, delivered

    def test_silence_releases_a_held_phrase(self, monkeypatch):
        import threading
        import time as real_time

        listener, delivered = self._running_listener(monkeypatch)
        listener._audio_queue.put(b"\x00\x00" * 100)

        thread = threading.Thread(target=listener._decode_loop, daemon=True)
        thread.start()
        try:
            deadline = real_time.monotonic() + 3.0
            while not delivered and real_time.monotonic() < deadline:
                real_time.sleep(0.05)
            # Read *before* stopping. Shutdown flushes whatever is held, so
            # checking afterwards cannot tell "released because the room went
            # quiet" from "released because the listener was turned off" --
            # and it is the first one this exists to prove.
            released_while_listening = list(delivered)
        finally:
            listener.is_listening = False
            thread.join(timeout=5.0)

        assert released_while_listening == ["turn the lights off"], (
            "a finished phrase was never released while the room was quiet")

    def test_stopping_flushes_rather_than_dropping(self, monkeypatch):
        import threading
        import time as real_time

        listener, delivered = self._running_listener(monkeypatch,
                                                     heard_text="good night")
        monkeypatch.setattr(listener, "join_window", lambda: 600.0)
        listener._audio_queue.put(b"\x00\x00" * 100)

        thread = threading.Thread(target=listener._decode_loop, daemon=True)
        thread.start()
        real_time.sleep(0.4)
        listener.is_listening = False
        thread.join(timeout=5.0)

        assert delivered == ["good night"], (
            "the last thing said before shutdown was dropped")
