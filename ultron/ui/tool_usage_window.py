from ultron.ui.panel_window import PanelWindow
from ultron.ui.tool_usage_panel import ToolUsagePanel

WINDOW_SIZE = (470, 640)

class ToolUsageWindow(PanelWindow):
    def __init__(self, get_db):
        super().__init__("tool_history", WINDOW_SIZE)
        panel = ToolUsagePanel(get_db)
        panel.closed.connect(self.hide)
        self.set_panel(panel)

    def show_panel(self):
        """Reloads from disk and centres on first open."""
        self.panel.reload()
        self.centre_and_show()
