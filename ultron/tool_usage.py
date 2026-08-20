"""A tally of which tools actually get used.

Ultron carries 89 tools. Described to a local model that is 2,690 tokens of
prompt before the user has said anything; sent to Groq as JSON schemas it is
7,445. Every request pays that, and the bill does not depend on whether a
tool has ever been called once.

Deciding what to cut needs evidence rather than intuition, and intuition is
particularly bad here: the tools that feel important are the ones recently
written, not the ones used daily. So this counts.

Two design points worth stating.

Every registered tool is written with a count of zero the moment Ultron
starts. A file that only lists what has been called cannot answer the
question being asked -- "what do I never use" is exactly the set that would
be missing from it.

Failures are counted separately from calls. A tool invoked forty times that
errors on all forty is not a well-used tool; it is a broken one, and the two
look identical in a single number.

What counts as a failure is narrow and deliberately so: the tool raised, timed
out, or returned a string beginning "Error". It does not cover the 39 places
in automation.py that report trouble as "Failed to ...", "Could not ..." or
"File does not exist", so the error count is a floor rather than a total.

Widening it was the obvious fix and the wrong one. "No files matching 'x'" is
a successful search that found nothing, and "the file does not exist" is a
correct answer to a question about a missing file. Counting those as failures
would recommend deleting tools that work.
"""

import json
import os
import threading

from ultron.config import DATA_DIR

# Lives with the database and the logs: generated, personal, and already
# outside version control. usage.json sits at the repository root for
# historical reasons; new files do not need to repeat that.
USAGE_PATH = os.path.join(DATA_DIR, "tool_usage.json")

# Tools run on a worker thread, and routines run on another. Both land here.
_lock = threading.RLock()


def _read(path: str = None) -> dict:
    """The tally on disk, or an empty one.

    A corrupt file starts over rather than raising: losing counts is a
    nuisance, while failing to run a tool because its statistics could not be
    parsed would be absurd.
    """
    try:
        with open(path or USAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}


def _write(data: dict, path: str = None):
    """Replaces the file atomically, so a crash cannot truncate it."""
    path = path or USAGE_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        # Bookkeeping must never take the assistant down with it.
        print(f"[ToolUsage] could not write {path}: {e}")


def register(names, path: str = None):
    """Ensures every known tool has a row, even at zero.

    Called at startup with the whole tool table. Without this the file
    describes only what has been used, which is the opposite of the question
    it exists to answer.
    """
    with _lock:
        data = _read(path)
        changed = False
        for name in names:
            if name not in data:
                data[name] = {"calls": 0, "errors": 0, "last_used": None}
                changed = True
        if changed:
            _write(data, path)
        return data


def record(name: str, ok: bool = True, when: str = None, path: str = None):
    """Counts one invocation of *name*.

    `when` is injectable so tests do not depend on the clock.
    """
    if not name:
        return
    with _lock:
        data = _read(path)
        row = data.get(name)
        if not isinstance(row, dict):
            row = {"calls": 0, "errors": 0, "last_used": None}
        row["calls"] = int(row.get("calls", 0)) + 1
        if not ok:
            row["errors"] = int(row.get("errors", 0)) + 1
        if when is None:
            import datetime
            when = datetime.datetime.now().isoformat(timespec="seconds")
        row["last_used"] = when
        data[name] = row
        _write(data, path)


def report(path: str = None) -> dict:
    """Grouped for the decision this exists to support.

    `never_used` is the answer to the question; `failing` is the trap beside
    it, because a tool that is called often and errors every time reads as
    popular in a plain ranking.
    """
    data = _read(path)
    rows = [(name, row) for name, row in data.items() if isinstance(row, dict)]

    never = sorted(n for n, r in rows if not r.get("calls"))
    used = sorted(((n, r) for n, r in rows if r.get("calls")),
                  key=lambda item: -item[1].get("calls", 0))
    failing = sorted(
        ((n, r) for n, r in rows
         if r.get("calls") and r.get("errors", 0) >= r.get("calls", 0)),
        key=lambda item: -item[1].get("calls", 0))

    return {
        "total_tools": len(rows),
        "never_used": never,
        "used": used,
        "always_failing": failing,
        "total_calls": sum(r.get("calls", 0) for _n, r in rows),
    }
