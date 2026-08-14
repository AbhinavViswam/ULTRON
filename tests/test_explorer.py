"""Resolving "this folder" and "this file" from File Explorer.

Guessing a path is how an assistant opens the wrong folder, so these ask
Explorer directly. The COM tests matter more than they look: tools run on the
timeout watchdog's worker thread, and COM is per-thread.
"""

import os
import threading

import pytest

from ultron.plugins.explorer_plugin import (
    _folder_of, _is_explorer, explorer_state, get_current_explorer_folder,
    get_selected_file_in_explorer, list_current_explorer_folder
)


class _FakeWindow:
    """Stands in for an Explorer COM window."""

    def __init__(self, full_name="C:\\Windows\\explorer.exe", location=""):
        self.FullName = full_name
        self.LocationURL = location


class TestWindowIdentification:
    def test_explorer_is_recognised(self):
        assert _is_explorer(_FakeWindow()) is True

    def test_internet_explorer_is_excluded(self):
        """It shares the same COM collection as File Explorer."""
        assert _is_explorer(_FakeWindow("C:\\Program Files\\iexplore.exe")) is False

    def test_identification_does_not_depend_on_the_window_title(self):
        """The old check read window.Name, which Windows translates."""
        import ast
        import inspect
        import textwrap

        from ultron.plugins import explorer_plugin

        tree = ast.parse(textwrap.dedent(inspect.getsource(explorer_plugin._is_explorer)))
        read = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "Name" not in read, "window.Name is localised; use FullName"
        assert "FullName" in read

    def test_a_localised_window_title_still_identifies(self):
        """A German Windows calls it 'Datei-Explorer'."""
        window = _FakeWindow("C:\\Windows\\explorer.exe")
        window.Name = "Datei-Explorer"
        assert _is_explorer(window) is True

    def test_an_unreadable_window_is_not_explorer(self):
        class Broken:
            @property
            def FullName(self):
                raise OSError("gone")

        assert _is_explorer(Broken()) is False


class TestLocationParsing:
    def test_a_real_folder_is_returned(self, tmp_path):
        url = tmp_path.as_uri()
        assert _folder_of(_FakeWindow(location=url)) == str(tmp_path)

    def test_a_path_with_spaces_survives(self, tmp_path):
        spaced = tmp_path / "two words"
        spaced.mkdir()
        assert _folder_of(_FakeWindow(location=spaced.as_uri())) == str(spaced)

    def test_this_pc_has_no_path(self):
        """Special locations must come back empty, not as a fake path."""
        window = _FakeWindow(location="shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}")
        assert _folder_of(window) == ""

    def test_a_deleted_folder_is_not_returned(self, tmp_path):
        missing = tmp_path / "gone"
        url = missing.as_uri()
        assert _folder_of(_FakeWindow(location=url)) == ""

    def test_no_location_at_all(self):
        assert _folder_of(_FakeWindow(location="")) == ""


class TestNoExplorerOpen:
    """With nothing open the tools must explain, not return a bogus path."""

    def test_folder_lookup_explains_itself(self, monkeypatch):
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state", lambda: []
        )
        result = get_current_explorer_folder()
        assert not os.path.isdir(result)
        assert "No File Explorer window is open" in result

    def test_listing_passes_the_explanation_through(self, monkeypatch):
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state", lambda: []
        )
        assert "No File Explorer window is open" in list_current_explorer_folder()

    def test_selection_is_empty(self, monkeypatch):
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state", lambda: []
        )
        assert get_selected_file_in_explorer() == []


class TestChoosingBetweenWindows:
    def test_the_foreground_window_wins(self, monkeypatch, tmp_path):
        background = tmp_path / "background"
        foreground = tmp_path / "foreground"
        background.mkdir()
        foreground.mkdir()
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state",
            lambda: [
                {"folder": str(foreground), "selection": [], "in_front": True},
                {"folder": str(background), "selection": [], "in_front": False},
            ],
        )
        assert get_current_explorer_folder() == str(foreground)

    def test_a_window_with_no_path_is_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state",
            lambda: [
                {"folder": "", "selection": [], "in_front": True},
                {"folder": str(tmp_path), "selection": [], "in_front": False},
            ],
        )
        assert get_current_explorer_folder() == str(tmp_path)

    def test_only_special_locations_open(self, monkeypatch):
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state",
            lambda: [{"folder": "", "selection": [], "in_front": True}],
        )
        assert "not showing a folder on disk" in get_current_explorer_folder()


class TestListing:
    def test_contents_are_listed(self, monkeypatch, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        (tmp_path / "sub").mkdir()
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state",
            lambda: [{"folder": str(tmp_path), "selection": [], "in_front": True}],
        )
        listing = list_current_explorer_folder()
        assert "notes.txt" in listing
        assert "[folder] sub" in listing

    def test_an_empty_folder_says_so(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state",
            lambda: [{"folder": str(tmp_path), "selection": [], "in_front": True}],
        )
        assert "is empty" in list_current_explorer_folder()

    def test_long_listings_are_capped(self, monkeypatch, tmp_path):
        for i in range(130):
            (tmp_path / f"file{i:03}.txt").write_text("x")
        monkeypatch.setattr(
            "ultron.plugins.explorer_plugin.explorer_state",
            lambda: [{"folder": str(tmp_path), "selection": [], "in_front": True}],
        )
        listing = list_current_explorer_folder()
        assert "and 30 more" in listing


class TestComOnWorkerThreads:
    """Regression: tools run on the watchdog's thread, and COM is per-thread.

    Without CoInitialize these fail with "CoInitialize has not been called"
    while working perfectly when tried from the main thread.
    """

    def _on_worker(self, call):
        result = {}
        thread = threading.Thread(target=lambda: result.update(value=call()))
        thread.start()
        thread.join(timeout=30)
        return result.get("value")

    def test_enumeration_works_off_the_main_thread(self):
        assert isinstance(self._on_worker(explorer_state), list)

    def test_folder_lookup_works_off_the_main_thread(self):
        result = self._on_worker(get_current_explorer_folder)
        assert result is not None
        assert "CoInitialize" not in result

    def test_selection_works_off_the_main_thread(self):
        assert isinstance(self._on_worker(get_selected_file_in_explorer), list)

    def test_it_survives_repeated_calls(self):
        """Unbalanced CoUninitialize would break the second call."""
        for _ in range(3):
            assert "CoInitialize" not in self._on_worker(get_current_explorer_folder)

    def test_through_the_tool_layer(self, brain):
        """The path a real model takes, watchdog and all."""
        for tool in ("get_current_explorer_folder", "list_current_explorer_folder",
                     "get_selected_file_in_explorer"):
            assert "CoInitialize" not in str(brain._invoke_tool(tool, {}))


class TestRegistration:
    @pytest.mark.parametrize("tool", [
        "get_current_explorer_folder",
        "list_current_explorer_folder",
        "get_selected_file_in_explorer",
    ])
    def test_the_tool_is_available(self, brain, tool):
        assert tool in brain.tool_functions

    def test_reading_here_is_not_gated(self):
        """Looking at a folder changes nothing and must not prompt."""
        from ultron.brain import confirmation_question

        assert confirmation_question("get_current_explorer_folder", {}) is None
        assert confirmation_question("list_current_explorer_folder", {}) is None
