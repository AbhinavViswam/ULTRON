from ultron.ui.panel_window import PanelWindow
from ultron.ui.memories_panel import MemoriesPanel

WINDOW_SIZE = (470, 640)

class MemoriesWindow(PanelWindow):
    def __init__(self, get_db):
        super().__init__("memories_history", WINDOW_SIZE)
        panel = MemoriesPanel(get_db)
        panel.closed.connect(self.hide)
        self.set_panel(panel)

    def show_panel(self):
        """Reloads from disk and centres on first open."""
        self.panel.reload()
        self.centre_and_show()
