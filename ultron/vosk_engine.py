"""Offline speech recognition, and why it changes the microphone problem.

Google's recogniser is a network call, so something had to decide which audio
was worth sending. That something was an amplitude threshold, and it was the
cause of the microphone trouble: set above the quiet start of a word, whole
commands vanished with no way to tell "ignored you" from "never heard you".
Every attempt to fix it was a search for a number that is loud enough to
reject a fan and quiet enough to accept a tired voice at midnight. There may
not be such a number.

Vosk runs on this machine. There is no per-request cost, no rate limit and no
network, so nothing has to be filtered out before recognition — every chunk
can go to the recogniser and the acoustic model can decide what is speech.
Measured here at a real-time factor of 0.196, which is one fifth of one core.
That is what removes the threshold from the path rather than retuning it.

Two consequences worth knowing:

* The model *is* the language. Indian English is a choice of model, not a
  parameter, which is why the default is an en-in one.
* Vosk decides where a phrase ends, so the silence timer goes too.

If Vosk or its model is absent this module reports so and the caller keeps
using Google. A missing 55MB download must not leave someone with no voice
input at all.
"""

import glob
import json
import os

from ultron.config import DATA_DIR, config

# Models are data, not source: large, downloaded, and already gitignored.
MODELS_DIR = os.path.join(DATA_DIR, "models")

# Indian English, small variant. The 1GB en-in model is more accurate, but
# this machine is already sharing its CPU with a local LLM and the small one
# decodes several times faster than real time.
DEFAULT_MODEL_NAME = "vosk-model-small-en-in-0.4"
MODEL_URL = f"https://alphacephei.com/vosk/models/{DEFAULT_MODEL_NAME}.zip"


def model_path():
    """The model directory to load, or None if there is not one.

    An explicitly configured path is never second-guessed: if it is set and
    wrong, that is worth failing visibly rather than silently loading some
    other model the user did not ask for.
    """
    configured = (config.get("vosk.model_path") or "").strip()
    if configured:
        return configured if os.path.isdir(configured) else None

    preferred = os.path.join(MODELS_DIR, DEFAULT_MODEL_NAME)
    if os.path.isdir(preferred):
        return preferred

    # Whatever else was dropped in, so swapping to the large model is a
    # matter of unzipping it rather than editing settings.
    for candidate in sorted(glob.glob(os.path.join(MODELS_DIR, "vosk-model-*"))):
        if os.path.isdir(candidate):
            return candidate
    return None


def ensure_model():
    """The model directory, fetching it once if this is a fresh install.

    Without this the offline engine is only ever available on the machine that
    downloaded the model by hand. Everywhere else it reports itself missing
    and quietly falls back to Google *and its amplitude threshold* — so the
    microphone bug returns on any fresh clone while the code still looks
    correct. A 36MB download is a much smaller cost than that.

    Guarded so a disabled or uninstalled engine never downloads anything.
    """
    if config.get("vosk.enabled", True) is False:
        return None
    try:
        import vosk  # noqa: F401
    except Exception:
        return None

    existing = model_path()
    if existing:
        return existing
    if config.get("vosk.auto_download", True) is False:
        return None
    return download_model()


def download_model(url: str = MODEL_URL, name: str = DEFAULT_MODEL_NAME):
    """Downloads and unpacks a model, or returns None having said why.

    Everything lands in a scratch directory and is renamed into place only
    once it is complete. An interrupted download must not leave behind
    something that looks like a working model: that fails later, further from
    the cause, and looks like a corrupt install rather than a partial one.
    """
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    final = os.path.join(MODELS_DIR, name)
    try:
        os.makedirs(MODELS_DIR, exist_ok=True)
    except OSError as e:
        print(f"[Voice Engine] cannot create {MODELS_DIR}: {e}")
        return None

    scratch = tempfile.mkdtemp(prefix="vosk-download-", dir=MODELS_DIR)
    archive = os.path.join(scratch, "model.zip")
    print(f"[Voice Engine] First run: downloading the offline speech model "
          f"({name}). About 36MB, once.")

    last = [-1]

    def progress(blocks, block_size, total):
        if total <= 0:
            return
        percent = min(100, int(blocks * block_size * 100 / total))
        if percent >= last[0] + 20:
            last[0] = percent
            print(f"[Voice Engine]   {percent}%")

    try:
        urllib.request.urlretrieve(url, archive, reporthook=progress)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(scratch)

        unpacked = os.path.join(scratch, name)
        if not os.path.isdir(unpacked):
            # Some archives are named differently from their contents.
            candidates = [os.path.join(scratch, entry)
                          for entry in os.listdir(scratch)
                          if entry.startswith("vosk-model")]
            candidates = [c for c in candidates if os.path.isdir(c)]
            if not candidates:
                raise RuntimeError("the archive held no model directory")
            unpacked = candidates[0]

        os.replace(unpacked, final)
        print(f"[Voice Engine] Offline speech model ready ({name}).")
        return final
    except Exception as e:
        print(f"[Voice Engine] could not download the offline model: {e}")
        print(f"[Voice Engine] Ultron still works; it will use Google instead. "
              f"To install it by hand, unzip {url} into {MODELS_DIR}")
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def unavailable_reason():
    """Why offline recognition cannot be used, or None if it can.

    A sentence rather than a boolean, because the two causes need different
    things done about them and the caller prints this to the user.
    """
    if config.get("vosk.enabled", True) is False:
        return "disabled in settings (vosk.enabled)"
    try:
        import vosk  # noqa: F401
    except Exception as e:
        return f"the vosk package is not installed ({e})"
    if model_path() is None:
        if config.get("vosk.auto_download", True) is False:
            return (f"no model in {MODELS_DIR} and auto-download is "
                    f"off - unzip {MODEL_URL} there")
        return f"no model in {MODELS_DIR} and it could not be downloaded"
    return None


class VoskTranscriber:
    """Streams audio into Vosk and hands back phrases as they complete."""

    def __init__(self, path: str, sample_rate: int = 16000):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        # Vosk narrates its own startup to stderr; the caller reports what
        # matters in a single line instead.
        SetLogLevel(-1)
        self.path = path
        self.name = os.path.basename(path.rstrip("/\\"))
        self._recognizer = KaldiRecognizer(Model(path), sample_rate)
        # Per-word confidence, so an unlikely transcription can be recognised
        # as one instead of being indistinguishable from a clear phrase.
        self._recognizer.SetWords(True)

    def accept(self, chunk: bytes):
        """(text, confidence) once a phrase ends, else None.

        Vosk decides where the phrase ended, using the acoustic model rather
        than a silence stopwatch — so trailing-off speech is not cut and a
        mid-sentence pause does not split a command in two.
        """
        if self._recognizer.AcceptWaveform(chunk):
            return self._read(self._recognizer.Result())
        return None

    def flush(self):
        """Whatever was still being said when listening stopped."""
        return self._read(self._recognizer.FinalResult())

    @staticmethod
    def _read(raw):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        text = (data.get("text") or "").strip()
        if not text:
            return None
        # The *weakest* word, not the average: one word guessed out of noise
        # is what turns a real phrase into a wrong command, and averaging it
        # against confident neighbours hides exactly that.
        words = data.get("result") or []
        confidence = min((w.get("conf", 1.0) for w in words), default=1.0)
        return text, float(confidence)
