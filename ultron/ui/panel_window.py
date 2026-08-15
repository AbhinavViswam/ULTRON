"""The frameless window shell that hosts a full-page panel.

The orb has nowhere to put a page inside itself, so anything larger than a
card gets its own window. Frameless windows have no title bar, so the header
doubles as the drag handle and carries its own close button.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
)

from ultron.ui import theme

CORNER_RADIUS = 14
HEADER_HEIGHT = 44


class PanelWindow(QWidget):
    """A rounded, translucent, draggable window with a title and one panel."""

    def __init__(self, title: str, size=(470, 640)):
        super().__init__()
        self._drag_offset = None

        self.setWindowTitle(title.title())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(theme.STYLESHEET)
        self.resize(*size)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(14, 12, 14, 14)
        self._root.setSpacing(10)

        header = QHBoxLayout()
        heading = QLabel(f"ULTRON  ·  {title.upper()}")
        heading.setObjectName("title")
        header.addWidget(heading)
        header.addStretch(1)

        close_button = QPushButton("✕")
        close_button.setObjectName("iconButton")
        close_button.clicked.connect(self.hide)
        header.addWidget(close_button)
        self._root.addLayout(header)

    def set_panel(self, panel: QWidget):
        self.panel = panel
        self._root.addWidget(panel, 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)
        painter.fillPath(path, theme.panel_color())
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawPath(path)

    def centre_and_show(self):
        """Centres on first open, then keeps wherever it was dragged to."""
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
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
