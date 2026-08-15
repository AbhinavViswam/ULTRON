# Tests

```
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest
```

Roughly 40 seconds. Run it before committing anything that touches the brain,
the tool layer, or scheduling.

## Safety

**Nothing here touches your real data.** `conftest.py` redirects `Database` to
a temporary SQLite file and ChromaDB directory for every test, so running the
suite cannot read or alter what Ultron actually remembers. Files are only ever
deleted inside pytest's `tmp_path`.

The LLM client is stubbed out, so no test makes a network call or spends
tokens.

## What each file covers

| File | Guards against |
| --- | --- |
| `test_reminders.py` | Recurring reminders drifting later every day, monthly walking backwards through the calendar, a week away producing seven alarms |
| `test_confirmation.py` | Destructive tools running without a human agreeing — including the model granting itself permission, and saved workflows going around the gate |
| `test_memory.py` | Deleting the wrong memory, listings whose numbers shift under the user, ambiguous requests being guessed at |
| `test_timeouts.py` | A hung tool or stalled LLM freezing the single worker thread forever |
| `test_tool_calls.py` | Malformed calls from small models: invented argument names, missing arguments, unparsable JSON |
| `test_error_reporting.py` | Failures vanishing silently — most importantly speech-to-text, where a network drop looked identical to saying nothing |
| `test_folders.py` | "open ultron folder" failing, or a bare name resolving against the working directory |
| `test_ui.py` | Clipped message cards, a transcript that stops following the conversation, a confirmation card that can be dismissed into a yes |

`test_ui.py` builds real Qt widgets and needs a desktop session. It is skipped
automatically if PySide6 is missing.

## Writing more

Prefer testing through `brain._invoke_tool(name, args)` rather than calling
tool functions directly — that is the path a real model takes, and it is where
argument coercion, the confirmation gate and the timeout watchdog all live.

Use the `brain`, `approve_all` and `refuse_all` fixtures rather than building
your own; they keep the isolation guarantees above.
