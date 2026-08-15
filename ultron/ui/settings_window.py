"""A small frameless window hosting the settings panel."""

from PySide6.QtCore import Signal

from ultron.ui.panel_window import PanelWindow
from ultron.ui.settings_panel import SettingsPanel

WINDOW_SIZE = (470, 640)


class SettingsWindow(PanelWindow):
    settings_saved = Signal()

    def __init__(self):
        super().__init__("settings", WINDOW_SIZE)
        panel = SettingsPanel()
        panel.closed.connect(self.hide)
        panel.settings_saved.connect(self.settings_saved.emit)
        self.set_panel(panel)

    def show_panel(self):
        """Reloads from disk and centres on first open."""
        self.panel.reload()
        self.centre_and_show()
