"""Reading text out of an image.

The Windows Tesseract installer does not put the executable on PATH, and
pytesseract only looks there — so a perfectly good installation reported
itself as missing, and every image handed to read_document came back as an
error telling the user to install something they already had.
"""

import os

import pytest

from ultron.plugins import document_plugin
from ultron.plugins.document_plugin import find_tesseract, read_document

PIL = pytest.importorskip("PIL")


@pytest.fixture
def image(tmp_path):
    """An image with text in it, drawn rather than shipped as a fixture."""
    from PIL import Image, ImageDraw, ImageFont

    picture = Image.new("RGB", (720, 140), "white")
    draw = ImageDraw.Draw(picture)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    draw.text((25, 45), "Rateup meeting at 10:25", fill="black", font=font)

    path = tmp_path / "note.png"
    picture.save(path)
    return str(path)


class TestFindingTesseract:
    def test_path_wins_over_the_usual_locations(self, monkeypatch):
        """A deliberate choice of build must not be overridden by a guess."""
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: r"D:\custom\tesseract.exe")

        assert find_tesseract() == r"D:\custom\tesseract.exe"

    def test_the_install_location_is_used_when_path_has_nothing(self, monkeypatch, tmp_path):
        import shutil

        pretend = tmp_path / "tesseract.exe"
        pretend.write_text("")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.setattr(document_plugin, "TESSERACT_LOCATIONS", [str(pretend)])

        assert find_tesseract() == str(pretend)

    def test_nothing_found_is_reported_as_nothing(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.setattr(document_plugin, "TESSERACT_LOCATIONS", [])

        assert find_tesseract() == ""

    def test_a_missing_install_is_explained_with_the_command_to_fix_it(
            self, monkeypatch, image):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: None)
        monkeypatch.setattr(document_plugin, "TESSERACT_LOCATIONS", [])

        result = read_document(image)
        assert "not installed" in result
        assert "winget install UB-Mannheim.TesseractOCR" in result


@pytest.mark.skipif(not find_tesseract(), reason="Tesseract is not installed")
class TestReadingRealImages:
    def test_text_is_read_back_out_of_an_image(self, image):
        result = read_document(image)

        assert "Rateup meeting at 10:25" in result
        assert os.path.basename(image) in result

    def test_a_blank_image_says_so_rather_than_returning_nothing(self, tmp_path):
        from PIL import Image

        path = tmp_path / "blank.png"
        Image.new("RGB", (300, 120), "white").save(path)

        assert "No text detected" in read_document(str(path))

    def test_it_works_without_tesseract_being_on_path(self, image, monkeypatch):
        """The case that was broken: installed, but PATH knows nothing of it."""
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: None)

        assert "Rateup meeting at 10:25" in read_document(image)
