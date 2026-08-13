"""Floating message cards that appear beside the orb and fade away.

Each card is its own frameless window rather than a widget inside a panel, so
messages can sit over whatever is on screen without the assistant needing a
window of its own. Cards never take focus — typing in another app while Ultron
answers must keep working.
"""

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, Signal
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget
)

from ultron.ui import theme

CARD_WIDTH = 330
CARD_RADIUS = 13
CARD_GAP = 8
CARD_MARGIN_H = 13

# Dwell time scales with reading length, within bounds.
MIN_DWELL_MS = 3500
MAX_DWELL_MS = 14000
MS_PER_CHARACTER = 45

# Reminders and scheduled results are the whole point of having been told, so
# they stay up far longer than conversational replies.
SCHEDULED_DWELL_MS = 26000

FADE_IN_MS = 220
FADE_OUT_MS = 420

# Per card kind: fill, border, body colour, caption, caption colour.
# The caption is what separates a reminder from a passing loading phrase.
CARD_LOOKS = {
    "user": (theme.USER_BUBBLE, theme.ACCENT_DIM, theme.TEXT, "YOU", theme.TEXT_DIM),
    "user_voice": (theme.USER_BUBBLE, theme.ACCENT, theme.TEXT,
                   "YOU  ·  SPOKEN", theme.ACCENT),
    # Accepted but waiting behind the turn in progress.
    "user_queued": (theme.PANEL_LIGHT, theme.BORDER, theme.TEXT,
                    "YOU  ·  QUEUED", theme.TEXT_DIM),
    "user_voice_queued": (theme.PANEL_LIGHT, theme.BORDER, theme.TEXT,
                          "YOU  ·  SPOKEN  ·  QUEUED", theme.TEXT_DIM),
    "ultron": (theme.PANEL, theme.BORDER, theme.TEXT, "ULTRON", theme.ACCENT),
    "reminder": (theme.PANEL, theme.WARNING, theme.TEXT,
                 "REMINDER", theme.WARNING),
    "scheduled": (theme.PANEL, theme.ACCENT_DIM, theme.TEXT,
                  "SCHEDULED", theme.ACCENT),
    "system": (theme.PANEL, theme.BORDER, theme.TEXT_DIM, "", theme.TEXT_DIM),
}

# Kinds that must not be missed, so they linger.
LONG_LIVED_ROLES = ("reminder", "scheduled")


class _FloatingWindow(QWidget):
    """Shared chrome: frameless, on top, focus-free, with a soft shadow."""

    def __init__(self, accept_focus: bool = False):
        super().__init__()
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if not accept_focus:
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        if not accept_focus:
            self.setAttribute(Qt.WA_ShowWithoutActivating)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.setGraphicsEffect(shadow)

        self._fill = QColor(theme.PANEL)
        self._stroke = QColor(theme.BORDER)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            self.rect().adjusted(1, 1, -1, -1), CARD_RADIUS, CARD_RADIUS
        )
        painter.fillPath(path, self._fill)
        painter.setPen(QPen(self._stroke, 1))
        painter.drawPath(path)


class MessageCard(_FloatingWindow):
    """One message, shown briefly beside the orb."""

    dismissed = Signal(object)

    def __init__(self, text: str, role: str = "ultron"):
        super().__init__()
        self.role = role
        fill, stroke, text_color, caption, caption_color = CARD_LOOKS.get(
            role, CARD_LOOKS["ultron"]
        )
        self._fill = QColor(fill)
        self._fill.setAlpha(247)
        self._stroke = QColor(stroke)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGIN_H, 10, CARD_MARGIN_H, 11)
        layout.setSpacing(3)

        if caption:
            speaker = QLabel(caption)
            speaker.setStyleSheet(
                f"color: {caption_color};"
                "font-size: 9px; font-weight: 600; letter-spacing: 1.5px;"
            )
            layout.addWidget(speaker)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        body.setStyleSheet(f"color: {text_color}; font-size: 12.5px;")
        # A wrapping label reports a height only once its width is known.
        # Without this the card sizes to a single line and clips the rest.
        body.setFixedWidth(CARD_WIDTH - CARD_MARGIN_H * 2)
        layout.addWidget(body)
        self._body = body

        self.setFixedWidth(CARD_WIDTH)
        self._fit_height()

        self._closing = False
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

        if role in LONG_LIVED_ROLES:
            self._dwell_ms = SCHEDULED_DWELL_MS
        else:
            dwell = len(text) * MS_PER_CHARACTER
            self._dwell_ms = max(MIN_DWELL_MS, min(MAX_DWELL_MS, dwell))

    def _fit_height(self):
        """Sizes the card to its wrapped text at the fixed card width."""
        self._body.setFixedHeight(self._body.heightForWidth(self._body.width()))
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        # Fonts come from the stylesheet, which is only applied once the
        # widget is polished, so the first measurement can be short.
        self._fit_height()

    def appear(self):
        self.setWindowOpacity(0.0)
        self.show()
        self._animate_to(1.0, FADE_IN_MS)
        self._dismiss_timer.start(self._dwell_ms)

    def dismiss(self):
        # Clicking a card that is already fading would connect _finish a
        # second time and delete the widget twice.
        if self._closing:
            return
        self._closing = True
        self._dismiss_timer.stop()
        self._animate_to(0.0, FADE_OUT_MS, on_done=self._finish)

    def hold(self):
        """Keeps the card up — used while the pointer is over it."""
        self._dismiss_timer.stop()

    def resume(self):
        self._dismiss_timer.start(max(1200, self._dwell_ms // 2))

    def enterEvent(self, event):
        self.hold()

    def leaveEvent(self, event):
        self.resume()

    def mousePressEvent(self, event):
        """Click to acknowledge — mainly so a reminder can be cleared early."""
        if event.button() == Qt.LeftButton:
            self.dismiss()

    def _animate_to(self, target: float, duration: int, on_done=None):
        self._fade.stop()
        self._fade.setDuration(duration)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(target)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        if on_done:
            self._fade.finished.connect(on_done)
        self._fade.start()

    def _finish(self):
        self.hide()
        self.dismissed.emit(self)


class InputCard(_FloatingWindow):
    """A one-line prompt box, opened by clicking the orb."""

    submitted = Signal(str)

    def __init__(self):
        super().__init__(accept_focus=True)
        self._fill = QColor(theme.PANEL)
        self._fill.setAlpha(250)
        self._stroke = QColor(theme.ACCENT_DIM)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Ask Ultron…")
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: {theme.PANEL_LIGHT}; color: {theme.TEXT};"
            f"border: 1px solid {theme.BORDER}; border-radius: 8px; padding: 7px 10px;"
            f"selection-background-color: {theme.ACCENT_DIM}; font-size: 12.5px; }}"
            f"QLineEdit:focus {{ border: 1px solid {theme.ACCENT_DIM}; }}"
        )
        self.edit.returnPressed.connect(self._submit)
        layout.addWidget(self.edit)

        self.setFixedWidth(CARD_WIDTH)
        self.adjustSize()

    def _submit(self):
        text = self.edit.text().strip()
        if text:
            self.edit.clear()
            self.submitted.emit(text)

    def open_at(self, position: QPoint):
        self.move(position)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class CardStack:
    """Keeps live cards directly under the orb, newest nearest to it.

    Cards hang below the orb and grow downward, so whatever Ultron is saying
    right now sits immediately beneath it. When there is not enough room below
    — the orb's default home is the bottom-right corner — the stack flips and
    grows upward instead.
    """

    def __init__(self, max_cards: int = 4):
        self.max_cards = max_cards
        self._cards = []
        self._center_x = 0
        self._below_y = 0
        self._above_y = 0
        self._screen = None

    def set_anchor(self, center_x: int, below_y: int, above_y: int, screen):
        """Positions the stack relative to the orb.

        Args:
            center_x: Horizontal centre of the orb; cards centre on it.
            below_y:  First y the stack may occupy under the orb.
            above_y:  Bottom edge the stack may occupy above the orb.
            screen:   Usable screen rect, used to decide which way to grow
                      and to keep cards on screen near an edge.
        """
        self._center_x = center_x
        self._below_y = below_y
        self._above_y = above_y
        self._screen = screen
        self._relayout()

    def add(self, text: str, role: str = "ultron"):
        card = MessageCard(text, role)
        card.dismissed.connect(self._remove)
        self._cards.append(card)

        # When the stack is full, drop the oldest chatter first. Reminders are
        # evicted only if there is nothing else to give up, otherwise a burst
        # of replies would push a reminder off before it was read.
        if len(self._cards) > self.max_cards:
            expendable = [c for c in self._cards if c.role not in LONG_LIVED_ROLES]
            (expendable or self._cards)[0].dismiss()

        card.appear()
        self._relayout()
        return card

    def _remove(self, card):
        if card in self._cards:
            self._cards.remove(card)
        card.deleteLater()
        self._relayout()

    def _relayout(self):
        if not self._cards or self._screen is None:
            return

        needed = sum(c.height() + CARD_GAP for c in self._cards)
        grow_down = self._below_y + needed <= self._screen.bottom()

        # Newest first either way, so the current message hugs the orb.
        if grow_down:
            y = self._below_y
            for card in reversed(self._cards):
                card.move(self._x_for(card), y)
                y += card.height() + CARD_GAP
        else:
            y = self._above_y
            for card in reversed(self._cards):
                y -= card.height() + CARD_GAP
                card.move(self._x_for(card), max(y, self._screen.top()))

    def _x_for(self, card) -> int:
        """Centres a card on the orb, kept clear of the screen edges."""
        x = int(self._center_x - card.width() / 2)
        highest = self._screen.right() - card.width() - CARD_GAP
        return max(self._screen.left() + CARD_GAP, min(x, highest))

    def clear(self):
        for card in list(self._cards):
            card.dismiss()

    def close_all(self):
        for card in list(self._cards):
            card.hide()
            card.deleteLater()
        self._cards.clear()
