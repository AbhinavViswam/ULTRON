"""Commands that never need to ask the model.

"Pause" does not require reasoning. On this machine it required fifty seconds
of it, because every utterance went to a local model running mostly on the CPU
— and by the time the music stopped the moment had passed.

These are the phrases whose meaning is fixed. Matching one dispatches the tool
directly: no prompt, no tokens, no wait.

Two rules keep this safe rather than merely fast:

1. **Exact match after normalisation, never fuzzy.** A wrong alias fires an
   action the user did not ask for, which is far worse than a slow one. If a
   phrase is not in the table verbatim, it goes to the model as before.
2. **Nothing destructive, ever.** Aliases skip the model, which means they also
   skip the reasoning that would question a bad instruction. Anything that
   deletes, sends, spends or powers off stays on the slow path where the
   confirmation gate can see it.
"""

import re

# Words that carry no meaning here, so their presence or absence must not
# decide whether a phrase is recognised. "ultron" is included because the
# microphone hears the wake word as part of the sentence.
FILLER = {
    "please", "can", "could", "would", "you", "u", "hey", "ok", "okay",
    "ultron", "just", "now", "the", "a", "an", "my", "for", "me", "to",
    "some", "it", "that", "this", "and", "then", "go", "ahead",
}

# phrase -> (tool name, arguments). Only phrases with exactly one plausible
# meaning belong here; anything a reasonable person could read two ways is
# left to the model.
ALIASES = {}


def normalise(text: str) -> str:
    """Reduces a phrase to the words that carry its meaning.

    Punctuation and filler are dropped so that "Ultron, could you pause that
    please?" and "pause" land on the same key. What survives is matched whole:
    a longer sentence that merely contains "pause" reduces to something else
    and falls through to the model, which is the intent.
    """
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    kept = [word for word in words if word not in FILLER]
    return " ".join(kept)


def _add(tool: str, args: dict, *phrases: str):
    """Registers phrases under the same form a lookup will produce.

    Storing them as written was a silent bug: "my reminders" normalises to
    "reminders", so the raw key could never be hit by any input. Normalising
    here means a phrase can be written naturally and still match.
    """
    for phrase in phrases:
        ALIASES[normalise(phrase)] = (tool, args)


# --- media ---------------------------------------------------------------
# Deliberately absent: "stop". It reads as "stop talking" at least as often as
# "stop the music", and interrupting speech is already handled elsewhere.
_add("system_media_control", {"action": "pause"},
     "pause", "pause music", "pause song", "pause playback")
_add("system_media_control", {"action": "play"},
     "resume", "resume music", "resume song", "unpause", "continue playing")
_add("system_media_control", {"action": "next"},
     "next", "next song", "next track", "skip", "skip song", "skip track")
_add("system_media_control", {"action": "prev"},
     "previous", "previous song", "previous track", "back song", "last song")

# --- volume --------------------------------------------------------------
_add("adjust_volume", {"action": "volume_up"},
     "volume up", "turn up volume", "turn up", "louder", "increase volume")
_add("adjust_volume", {"action": "volume_down"},
     "volume down", "turn down volume", "turn down", "quieter", "lower volume",
     "decrease volume")
_add("adjust_volume", {"action": "mute"},
     "mute", "unmute", "mute volume", "silence")

# --- things Ultron simply knows -----------------------------------------
_add("list_reminders", {},
     "list reminders", "show reminders", "what are reminders",
     "what reminders do i have", "my reminders")
_add("list_routines", {},
     "list routines", "show routines", "what are routines", "my routines")
_add("get_system_health", {},
     "system health", "battery", "battery level", "how is battery",
     "check battery", "disk space", "cpu usage")
_add("read_clipboard", {},
     "read clipboard", "what is in clipboard", "clipboard")
_add("release_stuck_keys", {},
     "release stuck keys", "unstick keys", "keys are stuck", "stuck keys")
_add("list_memories", {},
     "list memories", "what do you remember", "what do you remember about me", "show memories",
     "what do you know about me")
_add("get_current_explorer_folder", {},
     "current folder", "what folder am i in", "which folder is open")


def resolve(text: str):
    """The (tool, arguments) this phrase means outright, or None.

    None is the common case and the safe one — it simply means the model
    handles this the way it always has.
    """
    key = normalise(text)
    if not key:
        return None
    match = ALIASES.get(key)
    if match is None:
        return None
    tool, args = match
    return tool, dict(args)


def phrases_for(tool: str) -> list:
    """Every phrase that dispatches a given tool. Used by the tests."""
    return sorted(phrase for phrase, (name, _args) in ALIASES.items()
                  if name == tool)
