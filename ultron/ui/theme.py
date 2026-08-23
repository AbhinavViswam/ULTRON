"""Visual language for the overlay.

One dark, semi-transparent panel with a cyan accent. Colors live here rather
than inline so the whole window can be retuned from a single place.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

# Core palette - Crimson & Carbon Theme
BG = "#121212"
PANEL = "#1a1a1a"
PANEL_LIGHT = "#242424"
BORDER = "#333333"
ACCENT = "#FF2A2A"
ACCENT_DIM = "#CC0000"
TEXT = "#e0e0e0"
TEXT_DIM = "#888888"
USER_BUBBLE = "#3a1c1c"
DANGER = "#f87171"
SUCCESS = "#4ade80"
WARNING = "#fbbf24"

# Window transparency for the main panel (0-255).
PANEL_ALPHA = 242


def panel_color() -> QColor:
    color = QColor(BG)
    color.setAlpha(PANEL_ALPHA)
    return color


STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}}

QLabel#title {{
    color: {ACCENT};
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 3px;
}}

QLabel#subtitle {{
    color: {TEXT_DIM};
    font-size: 11px;
}}

QLabel#sectionHeader {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
    padding-top: 6px;
}}

QLabel#hint {{
    color: {TEXT_DIM};
    font-size: 11px;
}}

QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0px;
    background: transparent;
}}

QLineEdit, QSpinBox, QComboBox {{
    background: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT_DIM};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {TEXT_DIM};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM};
    outline: none;
}}

QPushButton {{
    background: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover {{
    border: 1px solid {ACCENT_DIM};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background: {BORDER};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
}}

QPushButton#primary {{
    background: {ACCENT_DIM};
    border: 1px solid {ACCENT_DIM};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {ACCENT};
    color: #ffffff;
}}

QPushButton#iconButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    color: {TEXT_DIM};
    font-size: 15px;
}}
QPushButton#iconButton:hover {{
    background: {PANEL_LIGHT};
    color: {ACCENT};
}}
QPushButton#closeButton:hover {{
    background: {DANGER};
    color: #0d1117;
}}

QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: {PANEL_LIGHT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_DIM};
    border: 1px solid {ACCENT};
}}

QMenu {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 26px 6px 26px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {PANEL_LIGHT};
    color: {ACCENT};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 8px;
}}
/* Without this the checked state of a checkable action is invisible. */
QMenu::indicator {{
    width: 13px;
    height: 13px;
    margin-left: 8px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: {PANEL_LIGHT};
}}
QMenu::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
}}

QFrame#separator {{
    background: {BORDER};
    max-height: 1px;
    border: none;
}}

QFrame#routineRow {{
    background: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#routineName {{
    color: {TEXT};
    font-weight: 600;
}}
QLabel#routineName:disabled {{
    color: {TEXT_DIM};
}}
QPushButton#smallButton {{
    padding: 3px 9px;
    font-size: 11px;
}}
"""


def make_app_icon(size: int = 64, muted: bool = False) -> QIcon:
    """Draws the tray/window icon so no binary asset has to ship with the repo.

    `muted` dims the face and strikes it through, so the tray icon alone says
    whether the microphone is listening — the icon is often the only part of
    Ultron on screen.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    accent = QColor(TEXT_DIM) if muted else QColor(ACCENT)
    margin = size * 0.08
    body = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

    painter.setBrush(QBrush(QColor(BG)))
    painter.setPen(QPen(accent, size * 0.06))
    painter.drawEllipse(body)

    # Two glowing eyes — unmistakable at 16px in the system tray.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(accent))
    eye_w = size * 0.20
    eye_h = size * 0.11
    eye_y = size * 0.42
    painter.drawEllipse(QRectF(size * 0.26, eye_y, eye_w, eye_h))
    painter.drawEllipse(QRectF(size * 0.54, eye_y, eye_w, eye_h))

    if muted:
        # A slash reads as "off" at any size, where a colour change alone does
        # not — especially on a light taskbar.
        painter.setPen(QPen(QColor(DANGER), size * 0.09, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            int(size * 0.22), int(size * 0.78), int(size * 0.78), int(size * 0.22)
        )

    painter.end()
    return QIcon(pixmap)
