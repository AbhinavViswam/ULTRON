"""Checking that the local model can actually hold what Ultron sends it.

Ollama picks a context window when it loads a model, sized against whatever
memory happens to be free — not against the prompt it is about to receive. On
this machine it chose 4,096 tokens for a system prompt of 6,736. Everything
past the limit was silently discarded, which meant Ultron sent the whole
conversation on every turn and the model never saw a word of it.

Nothing reported an error. The model simply answered as though it had no
history, and looked forgetful rather than truncated. That is the failure this
module exists to make loud: not to fix the window, but to refuse to let it go
unnoticed.
"""

import json
import urllib.request

# Long enough for a slow first load, short enough not to hold up startup.
FETCH_TIMEOUT_SECONDS = 5

# Room to leave beyond the system prompt for the conversation and the reply.
# Below this there is no space to hold a conversation at all, which is the
# state that reads to a user as "it doesn't remember anything".
HEADROOM_TOKENS = 2000


def base_url(api_url: str) -> str:
    """Ollama's own API root, from the OpenAI-compatible URL in settings."""
    return (api_url or "").rstrip("/").removesuffix("/v1")


def parse_num_ctx(parameters: str):
    """The num_ctx a Modelfile pins, or None if it leaves the choice to Ollama.

    /api/show returns parameters as one flat string of "name value" lines.
    """
    for line in (parameters or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "num_ctx":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def _get(url: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def fetch_context_length(api_url: str, model: str, get=_get):
    """The context window this model will actually run with, or None.

    A loaded model is asked directly, because that is the real number. An
    unloaded one can only be judged by whether its Modelfile pins num_ctx —
    without that, Ollama decides at load time based on free memory, and the
    honest answer is that it cannot be known yet.
    """
    root = base_url(api_url)
    if not root or not model:
        return None

    try:
        running = get(f"{root}/api/ps")
        for entry in running.get("models", []):
            if entry.get("name") == model or entry.get("model") == model:
                length = entry.get("context_length")
                if length:
                    return int(length)
    except Exception as e:
        print(f"[Model] could not ask Ollama what is loaded: {e}")
        return None

    try:
        return parse_num_ctx(get(f"{root}/api/show", {"model": model}).get("parameters"))
    except Exception as e:
        print(f"[Model] could not read {model}'s parameters: {e}")
        return None


def diagnose(system_tokens: int, context_length, model: str = "the model"):
    """A warning when the prompt cannot fit, or None when all is well.

    None is also the answer when the window is simply unknown — inventing a
    warning from a guess would train the user to ignore real ones.
    """
    if not context_length or system_tokens <= 0:
        return None

    if system_tokens >= context_length:
        return (f"[Model] {model} has a {context_length:,} token context but "
                f"Ultron's instructions alone are ~{system_tokens:,}. The "
                f"instructions are being cut off and no conversation can fit, "
                f"so it will not remember anything you say. Raise the window: "
                f"create a model with 'PARAMETER num_ctx 16384'.")

    if system_tokens + HEADROOM_TOKENS > context_length:
        spare = context_length - system_tokens
        return (f"[Model] {model} has a {context_length:,} token context and "
                f"Ultron's instructions take ~{system_tokens:,}, leaving only "
                f"~{spare:,} for the conversation. It will forget earlier "
                f"messages quickly. Consider a larger num_ctx.")

    return None
