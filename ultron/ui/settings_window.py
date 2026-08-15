"""A small frameless window hosting the settings panel.

The orb has no panel to host settings inside, so the panel gets its own
window — with a drag handle, since frameless windows have no title bar.
"""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from ultron.ui import theme
from ultron.ui.settings_panel import SettingsPanel

WINDOW_SIZE = (470, 640)
CORNER_RADIUS = 14
HEADER_HEIGHT = 44


class SettingsWindow(QWidget):
    """Frameless settings window, draggable by its header."""

    settings_saved = Signal()

    def __init__(self):
        super().__init__()
        self._drag_offset = None

        self.setWindowTitle("Ultron Settings")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(theme.STYLESHEET)
        self.resize(*WINDOW_SIZE)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("ULTRON  ·  SETTINGS")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)

        close_button = QPushButton("✕")
        close_button.setObjectName("iconButton")
        close_button.clicked.connect(self.hide)
        header.addWidget(close_button)
        root.addLayout(header)

        self.panel = SettingsPanel()
        self.panel.closed.connect(self.hide)
        self.panel.settings_saved.connect(self.settings_saved.emit)
        root.addWidget(self.panel, 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)
        painter.fillPath(path, theme.panel_color())
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawPath(path)

    def show_panel(self):
        """Reloads from disk and centres on first open."""
        self.panel.reload()
        if not self.isVisible():
            screen = QApplication.primaryScreen()
            if screen:
                area = screen.availableGeometry()
                self.move(
                    area.center().x() - self.width() // 2,
                    area.center().y() - self.height() // 2,
                )
        self.show()
        self.raise_()
        self.activateWindow()

    # Frameless windows have no title bar, so the header doubles as one.
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= HEADER_HEIGHT:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
