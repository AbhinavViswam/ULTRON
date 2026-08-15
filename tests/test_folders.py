"""Turning a spoken folder name into a real path.

"open ultron folder" used to fail with "please specify the path", and a bare
name used to resolve against the working directory — which, launched from its
own shortcut, is Ultron's project, so "ultron" opened the package nested
inside it rather than the folder meant.
"""

import os

import pytest

from ultron.automation import _clean_folder_name, _looks_like_path, _prune, known_folder


class TestSpokenNames:
    @pytest.mark.parametrize("spoken,expected", [
        ("ultron", "ultron"),
        ("ultron folder", "ultron"),
        ("the ultron folder", "ultron"),
        ("my downloads folder", "downloads"),
        ("the documents directory", "documents"),
        ("  spaced  ", "spaced"),
        ('"quoted"', "quoted"),
    ])
    def test_filler_words_are_stripped(self, spoken, expected):
        assert _clean_folder_name(spoken) == expected

    def test_an_empty_phrase_survives(self):
        assert _clean_folder_name("") == ""
        assert _clean_folder_name(None) == ""

    def test_a_name_that_is_only_filler_does_not_loop_forever(self):
        assert _clean_folder_name("the folder") == ""


class TestPathDetection:
    @pytest.mark.parametrize("text", [
        r"C:\Users\dilsh",
        "~/Documents",
        "some/relative/path",
        r"some\relative\path",
    ])
    def test_real_paths_are_recognised(self, text):
        assert _looks_like_path(text) is True

    @pytest.mark.parametrize("text", ["ultron", "downloads", "my project"])
    def test_bare_names_are_not_paths(self, text):
        """Otherwise they resolve against the working directory."""
        assert _looks_like_path(text) is False


class TestPruning:
    def test_a_nested_duplicate_is_dropped(self):
        outer = os.path.normpath("C:/DILSHA/ABHI/ultron")
        inner = os.path.normpath("C:/DILSHA/ABHI/ultron/ultron")
        assert _prune([inner, outer]) == [outer]

    def test_unrelated_matches_are_all_kept(self):
        a = os.path.normpath("C:/one/ultron")
        b = os.path.normpath("D:/two/ultron")
        assert set(_prune([a, b])) == {a, b}

    def test_shallowest_comes_first(self):
        deep = os.path.normpath("C:/a/b/c/ultron")
        shallow = os.path.normpath("C:/ultron")
        assert _prune([deep, shallow])[0] == shallow

    def test_duplicates_collapse(self):
        path = os.path.normpath("C:/a/ultron")
        assert _prune([path, path]) == [path]


class TestKnownFolders:
    def test_desktop_resolves_to_somewhere_real(self):
        """Must survive OneDrive's known-folder redirection."""
        desktop = known_folder("desktop")
        assert desktop == "" or os.path.isdir(desktop)

    def test_an_unknown_name_returns_empty(self):
        assert known_folder("nonsense") == ""

    @pytest.mark.parametrize("name", ["desktop", "documents", "downloads"])
    def test_the_usual_folders_are_mapped(self, name):
        from ultron.automation import _KNOWN_FOLDER_KEYS

        assert name in _KNOWN_FOLDER_KEYS
