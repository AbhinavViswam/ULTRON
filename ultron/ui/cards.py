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
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
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

# --- Session transcript, shown inside the input card -------------------------

# Tall enough to hold a few exchanges; past this the transcript scrolls rather
# than growing into a full-height window.
TRANSCRIPT_MAX_HEIGHT = 300
# A long session must not grow the widget tree without bound.
TRANSCRIPT_MAX_ROWS = 120
BUBBLE_RADIUS = 10
# Within this many pixels of the bottom still counts as "reading the newest",
# so a stray wheel notch does not stop the view following the conversation.
SCROLL_FOLLOW_SLACK = 6

# Per role: fill, text colour, caption, caption colour, right-aligned.
# Alignment carries who spoke, so ordinary turns need no caption at all.
TRANSCRIPT_LOOKS = {
    "user": (theme.USER_BUBBLE, theme.TEXT, "", theme.TEXT_DIM, True),
    "user_voice": (theme.USER_BUBBLE, theme.TEXT, "SPOKEN", theme.ACCENT, True),
    "user_queued": (theme.PANEL_LIGHT, theme.TEXT, "QUEUED", theme.TEXT_DIM, True),
    "user_voice_queued": (theme.PANEL_LIGHT, theme.TEXT, "SPOKEN · QUEUED",
                          theme.TEXT_DIM, True),
    "ultron": (theme.PANEL_LIGHT, theme.TEXT, "", theme.ACCENT, False),
    "reminder": (theme.PANEL_LIGHT, theme.TEXT, "REMINDER", theme.WARNING, False),
    "scheduled": (theme.PANEL_LIGHT, theme.TEXT, "SCHEDULED", theme.ACCENT, False),
    "system": (theme.PANEL, theme.TEXT_DIM, "", theme.TEXT_DIM, False),
}

# Roles whose caption colour is worth drawing around the whole bubble.
BORDERED_ROLES = ("reminder", "scheduled")


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


class ConfirmCard(_FloatingWindow):
    """Asks the user to approve a destructive action, and will not assume yes.

    Deliberately modal-ish in feel — it takes focus and stays until answered
    or until the core's timeout expires — because everything it guards is
    something the user cannot easily take back.
    """

    answered = Signal(bool)

    def __init__(self, question: str):
        super().__init__(accept_focus=True)
        self._fill = QColor(theme.PANEL)
        self._fill.setAlpha(252)
        self._stroke = QColor(theme.WARNING)
        self.setStyleSheet(theme.STYLESHEET)
        self._done = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 13)
        layout.setSpacing(9)

        tag = QLabel("CONFIRM")
        tag.setStyleSheet(
            f"color: {theme.WARNING}; font-size: 9px;"
            "font-weight: 600; letter-spacing: 1.6px;"
        )
        layout.addWidget(tag)

        body = QLabel(f"Ultron wants to {question}.")
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        body.setFixedWidth(CARD_WIDTH - 30)
        body.setStyleSheet(f"color: {theme.TEXT}; font-size: 12.5px;")
        layout.addWidget(body)
        self._body = body

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        no = QPushButton("No")
        no.setCursor(Qt.PointingHandCursor)
        no.clicked.connect(lambda: self._answer(False))
        buttons.addWidget(no)

        yes = QPushButton("Yes, do it")
        yes.setObjectName("primary")
        yes.setCursor(Qt.PointingHandCursor)
        yes.clicked.connect(lambda: self._answer(True))
        buttons.addWidget(yes)
        layout.addLayout(buttons)

        # No is focused, so a stray Enter or Space refuses rather than approves.
        no.setDefault(True)
        no.setFocus()

        self.setFixedWidth(CARD_WIDTH)
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self._body.setFixedHeight(self._body.heightForWidth(self._body.width()))
        self.adjustSize()

    def _answer(self, approved: bool):
        if self._done:
            return
        self._done = True
        self.answered.emit(approved)
        self.hide()
        self.deleteLater()

    def expire(self):
        """Called when the core stopped waiting; the answer is already 'no'."""
        if self._done:
            return
        self._done = True
        self.hide()
        self.deleteLater()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._answer(False)
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        # Dismissing the question is not consent.
        self._answer(False)
        super().closeEvent(event)


class _TranscriptRow(QWidget):
    """One turn in the session transcript: a captioned bubble, left or right."""

    def __init__(self, text: str, role: str, max_bubble_width: int):
        super().__init__()
        fill, text_color, caption, caption_color, on_right = TRANSCRIPT_LOOKS.get(
            role, TRANSCRIPT_LOOKS["system"]
        )

        bubble = QFrame()
        bubble.setStyleSheet(
            f"QFrame {{ background: {fill}; border: 1px solid {caption_color if role in BORDERED_ROLES else theme.BORDER};"
            f"border-radius: {BUBBLE_RADIUS}px; }}"
        )
        bubble.setMaximumWidth(max_bubble_width)

        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(10, 7, 10, 8)
        inner.setSpacing(2)

        if caption:
            tag = QLabel(caption)
            tag.setStyleSheet(
                f"color: {caption_color}; background: transparent; border: none;"
                "font-size: 8.5px; font-weight: 600; letter-spacing: 1.2px;"
            )
            inner.addWidget(tag)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        # Selectable so a reply can be copied out of the transcript.
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet(
            f"color: {text_color}; background: transparent; border: none;"
            "font-size: 12px;"
        )
        inner.addWidget(body)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        if on_right:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)


class TranscriptView(QScrollArea):
    """The running record of this session, scrolled to the newest turn.

    Cards are deliberately transient, so once one fades there is no way to
    re-read what was said. This keeps the same messages available for as long
    as the process lives, without putting a permanent window on the desktop.
    """

    def __init__(self, width: int):
        super().__init__()
        self._bubble_width = int(width * 0.86)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)

        self._holder = QWidget()
        self._holder.setAutoFillBackground(False)
        self._rows = QVBoxLayout(self._holder)
        self._rows.setContentsMargins(0, 0, 4, 0)
        self._rows.setSpacing(6)
        self._rows.addStretch(1)
        self.setWidget(self._holder)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setFixedHeight(0)
        self.hide()

        # Wrapped labels report their real height several layout passes later,
        # so the scroll range keeps growing after a message is added. Following
        # the range rather than setting a position once is what actually keeps
        # the newest turn in view — and it stops following the moment the user
        # scrolls up to re-read something.
        self._follow = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)

    def _on_range_changed(self, _minimum, maximum):
        if self._follow:
            self.verticalScrollBar().setValue(maximum)

    def _on_value_changed(self, value):
        bar = self.verticalScrollBar()
        self._follow = value >= bar.maximum() - SCROLL_FOLLOW_SLACK

    @property
    def is_empty(self) -> bool:
        # The trailing stretch is always present, so it is not a row.
        return self._rows.count() <= 1

    def append(self, text: str, role: str):
        row = _TranscriptRow(text, role, self._bubble_width)
        # Insert before the trailing stretch that keeps short sessions top-aligned.
        self._rows.insertWidget(self._rows.count() - 1, row)

        while self._rows.count() - 1 > TRANSCRIPT_MAX_ROWS:
            stale = self._rows.takeAt(0)
            if stale.widget():
                stale.widget().deleteLater()

        if not self.isVisible():
            self.show()
        self._resize_to_content()

    def clear(self):
        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.setFixedHeight(0)
        self.hide()

    def _resize_to_content(self):
        """Grows with the conversation up to a ceiling, then scrolls instead."""
        wanted = self._holder.sizeHint().height()
        self.setFixedHeight(min(wanted, TRANSCRIPT_MAX_HEIGHT))
        # Wrapped labels only report their real height after the next layout
        # pass, so settle the height and the scroll position once that lands.
        QTimer.singleShot(0, self.settle)

    def settle(self):
        """Re-measures after layout and jumps back to the newest turn."""
        wanted = self._holder.sizeHint().height()
        self.setFixedHeight(min(wanted, TRANSCRIPT_MAX_HEIGHT))
        self._follow = True
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class InputCard(_FloatingWindow):
    """The prompt box, opened by clicking the orb.

    Above the entry sits this session's transcript, so the conversation can be
    re-read after the floating cards have faded.
    """

    submitted = Signal(str)

    def __init__(self):
        super().__init__(accept_focus=True)
        self._fill = QColor(theme.PANEL)
        self._fill.setAlpha(250)
        self._stroke = QColor(theme.ACCENT_DIM)
        self._anchor = None
        self._screen = None
        # Theme rather than inline styling, so the scrollbar matches the rest.
        self.setStyleSheet(theme.STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(7)

        self._header = self._build_header()
        self._header.hide()
        layout.addWidget(self._header)

        self.transcript = TranscriptView(CARD_WIDTH - 24)
        layout.addWidget(self.transcript)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Ask Ultron…")
        self.edit.setStyleSheet("font-size: 12.5px;")
        self.edit.returnPressed.connect(self._submit)
        layout.addWidget(self.edit)

        self.setFixedWidth(CARD_WIDTH)
        self.adjustSize()

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(2, 0, 2, 0)

        title = QLabel("THIS SESSION")
        title.setStyleSheet(
            f"color: {theme.TEXT_DIM}; font-size: 8.5px;"
            "font-weight: 600; letter-spacing: 1.4px;"
        )
        row.addWidget(title)
        row.addStretch(1)

        clear = QPushButton("clear")
        clear.setObjectName("iconButton")
        clear.setStyleSheet("font-size: 10px;")
        clear.setCursor(Qt.PointingHandCursor)
        clear.setFocusPolicy(Qt.NoFocus)
        clear.clicked.connect(self._clear_transcript)
        row.addWidget(clear)
        return header

    def append(self, text: str, role: str):
        """Adds a turn to the transcript, whether or not the card is open."""
        self.transcript.append(text, role)
        self._header.show()
        self._refit()

    def _clear_transcript(self):
        """Clears the on-screen record only — the database keeps the history."""
        self.transcript.clear()
        self._header.hide()
        self._refit()
        self.edit.setFocus()

    def _refit(self):
        self.adjustSize()
        self._reposition()
        # The transcript settles its height one event loop later, so follow it.
        QTimer.singleShot(0, self._after_settle)

    def _after_settle(self):
        self.adjustSize()
        self._reposition()
        # Resizing the card changes the scrollable extent, so pin to the newest
        # turn last of all — otherwise a long reply lands mid-scroll.
        self.transcript.settle()

    def _submit(self):
        text = self.edit.text().strip()
        if text:
            self.edit.clear()
            self.submitted.emit(text)

    def open_at(self, bottom_right: QPoint, screen=None):
        """Opens with its bottom-right corner pinned near the orb.

        Anchoring the bottom means the box grows upward as the transcript
        fills, keeping the entry line under the pointer instead of walking it
        down the screen.
        """
        self._anchor = bottom_right
        self._screen = screen
        self._reposition()
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        QTimer.singleShot(0, self.transcript.settle)

    def _reposition(self):
        if self._anchor is None:
            return
        x = self._anchor.x() - self.width()
        y = self._anchor.y() - self.height()
        if self._screen is not None:
            x = max(self._screen.left() + CARD_GAP,
                    min(x, self._screen.right() - self.width() - CARD_GAP))
            y = max(self._screen.top() + CARD_GAP,
                    min(y, self._screen.bottom() - self.height() - CARD_GAP))
        self.move(x, y)

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
