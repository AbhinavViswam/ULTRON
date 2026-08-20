"""Shared fixtures.

The single most important thing here is isolation: nothing in this suite may
read or write the real `data/ultron.db` or `data/chroma`. Every test that
needs storage gets a throwaway copy, so running the tests can never disturb
what Ultron actually remembers.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """A Database backed by a temporary SQLite file and ChromaDB directory."""
    import chromadb

    from ultron.database import Database

    def fake_init(self, db_path="data/ultron.db"):
        self.db_path = str(tmp_path / "ultron.db")
        self._init_db()
        self.chroma_client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        self.memories_col = self.chroma_client.get_or_create_collection("memories")
        self.chat_history_col = self.chroma_client.get_or_create_collection("chat_history")

    monkeypatch.setattr(Database, "__init__", fake_init)
    return Database()


@pytest.fixture
def brain(scratch_db, monkeypatch):
    """A Brain whose storage is disposable and whose LLM is never called.

    Building a real Brain is the honest way to test the tool layer — it wires
    up the actual tool table, the argument coercion and the confirmation gate.
    Only the network is kept out of reach.
    """
    from ultron.brain import Brain

    instance = Brain()
    monkeypatch.setattr(
        instance, "client", None, raising=False
    )  # any accidental API call fails loudly rather than going out
    return instance


@pytest.fixture
def approve_all(brain):
    """Confirmation handler that says yes, recording what it was asked."""
    asked = []

    def handler(tool_name, args, question):
        asked.append(question)
        return True

    brain.set_confirm_handler(handler)
    return asked


@pytest.fixture
def refuse_all(brain):
    """Confirmation handler that says no, recording what it was asked."""
    asked = []

    def handler(tool_name, args, question):
        asked.append(question)
        return False

    brain.set_confirm_handler(handler)
    return asked


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole session; Qt allows only one."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_tool_usage(tmp_path, monkeypatch):
    """Keeps the tool tally out of the user's real data directory.

    Brain seeds every tool into the tally as it starts, so every test that
    builds a Brain was writing to data/tool_usage.json - the same file that
    is supposed to record what the *user* actually does. Zeros are harmless,
    but any test that runs a tool would have inflated the real counts, and
    the whole point of that file is to be trustworthy enough to delete tools
    on the strength of it.
    """
    from ultron import tool_usage

    monkeypatch.setattr(tool_usage, "USAGE_PATH",
                        str(tmp_path / "tool_usage.json"))
