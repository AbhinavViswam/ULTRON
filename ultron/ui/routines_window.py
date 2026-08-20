"""A frameless window hosting the routines panel."""

from ultron.ui.panel_window import PanelWindow
from ultron.ui.routines_panel import RoutinesPanel

WINDOW_SIZE = (560, 700)


class RoutinesWindow(PanelWindow):
    def __init__(self, get_db, run_now):
        super().__init__("routines", WINDOW_SIZE)
        panel = RoutinesPanel(get_db, run_now)
        panel.closed.connect(self.hide)
        self.set_panel(panel)

    def show_panel(self):
        """Rebuilds the list from the database and centres on first open."""
        self.panel.reload()
        self.centre_and_show()
