"""The orb: a single painted widget that shows what Ultron is doing.

Everything it draws is driven by real signals from UltronCore — the loudness
of Ultron's own voice while it speaks, the microphone while it listens, and
the live tool name while a tool runs. Nothing here is a canned animation.

States and how they read:
    idle       slow breathing, dim
    listening  concentric rings pushing outward on your voice
    thinking   a dot orbiting the rim
    tool       an arc sweeping the rim, with a label underneath
    speaking   the core swells with the voice and radiates a soft glow
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QPainter, QPen, QRadialGradient
)
from PySide6.QtWidgets import QWidget

from ultron.core import (
    STATE_IDLE, STATE_LISTENING, STATE_SPEAKING, STATE_THINKING, STATE_TOOL
)
from ultron.ui import theme

# Frame pacing. 33ms is smooth enough for this motion and cheap enough to leave
# running all day next to a voice pipeline.
FRAME_MS = 33

# How fast the displayed level chases the real one. Audio levels are spiky;
# without smoothing the orb jitters instead of pulsing.
LEVEL_ATTACK = 0.45   # rising: fast, so speech onset feels immediate
LEVEL_RELEASE = 0.12  # falling: slow, so it settles rather than snapping

# Accent per state.
STATE_COLORS = {
    STATE_IDLE: QColor(theme.ACCENT_DIM),
    STATE_LISTENING: QColor(theme.ACCENT),
    STATE_THINKING: QColor("#fbbf24"),
    STATE_TOOL: QColor("#ea580c"),
    STATE_SPEAKING: QColor(theme.ACCENT),
}


class Orb(QWidget):
    """Animated status orb. Emits `clicked` for taps that were not drags."""

    clicked = Signal()

    def __init__(self, diameter: int = 88, parent=None):
        super().__init__(parent)
        self.diameter = diameter
        # Room around the core for the glow and listening rings to breathe.
        # At full speaking level the glow reaches ~2x the core radius, so a
        # tighter box than this clips it into a visible square.
        self.setFixedSize(int(diameter * 2.1), int(diameter * 2.1))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)

        self.state = STATE_IDLE
        self.tool_label = None
        self.muted = False
        self._level = 0.0
        self._smoothed = 0.0
        self._phase = 0.0
        # Expanding rings emitted while listening, each (radius, opacity).
        self._rings = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def set_state(self, state: str, detail: str = None):
        self.state = state
        self.tool_label = detail
        if state != STATE_LISTENING:
            self._rings.clear()
        self.update()

    def set_level(self, level: float):
        self._level = max(0.0, min(1.0, float(level)))

    def set_muted(self, muted: bool):
        """Marks the microphone as off, badging the orb accordingly."""
        if muted == self.muted:
            return
        self.muted = muted
        if muted:
            self._rings.clear()
        self.update()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start(FRAME_MS)

    def hideEvent(self, event):
        # Nothing to animate while hidden to the tray, and this runs all day.
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self):
        rate = LEVEL_ATTACK if self._level > self._smoothed else LEVEL_RELEASE
        self._smoothed += (self._level - self._smoothed) * rate
        self._phase = (self._phase + 0.03) % (math.pi * 2)

        # Loud speech while listening throws off a ring, so the orb visibly
        # reacts to *your* voice rather than just glowing.
        if self.state == STATE_LISTENING and self._smoothed > 0.22 and len(self._rings) < 4:
            if not self._rings or self._rings[-1][0] > 0.25:
                self._rings.append([0.0, 0.55])

        for ring in self._rings:
            ring[0] += 0.022
            ring[1] -= 0.013
        self._rings = [r for r in self._rings if r[1] > 0]

        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        center = QPointF(self.width() / 2, self.height() / 2)
        color = STATE_COLORS.get(self.state, STATE_COLORS[STATE_IDLE])

        breath = 0.5 + 0.5 * math.sin(self._phase * 0.8)
        # Speech drives size directly; otherwise the orb just breathes.
        swell = self._smoothed * 0.22 if self.state == STATE_SPEAKING else breath * 0.04
        radius = (self.diameter / 2) * (1 + swell)

        self._draw_rings(painter, center, color)
        self._draw_glow(painter, center, radius, color, breath)
        self._draw_core(painter, center, radius, color)

        if self.state == STATE_TOOL:
            self._draw_sweep(painter, center, radius, color)
        elif self.state == STATE_THINKING:
            self._draw_orbit(painter, center, radius, color)

        if self.muted:
            self._draw_mute_badge(painter, center, radius)

    def _draw_mute_badge(self, painter, center, radius):
        """A struck-through mic badge on the rim while the microphone is off.

        The idle and listening states differ only in tint, which is too subtle
        to answer "is it hearing me?" at a glance.
        """
        badge_radius = radius * 0.29
        badge_center = QPointF(
            center.x() + radius * 0.70, center.y() + radius * 0.70
        )

        painter.setPen(QPen(QColor(theme.BG), 2))
        painter.setBrush(QBrush(QColor(theme.PANEL_LIGHT)))
        painter.drawEllipse(badge_center, badge_radius, badge_radius)

        # Mic capsule and stand.
        body = QColor(theme.TEXT_DIM)
        painter.setPen(QPen(body, max(1.4, badge_radius * 0.17), Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(QBrush(body))
        capsule_w = badge_radius * 0.38
        capsule_h = badge_radius * 0.62
        painter.drawRoundedRect(
            QRectF(
                badge_center.x() - capsule_w / 2,
                badge_center.y() - capsule_h * 0.75,
                capsule_w,
                capsule_h,
            ),
            capsule_w / 2,
            capsule_w / 2,
        )
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(
            QPointF(badge_center.x(), badge_center.y() + badge_radius * 0.18),
            QPointF(badge_center.x(), badge_center.y() + badge_radius * 0.48),
        )

        painter.setPen(QPen(QColor(theme.DANGER), max(1.6, badge_radius * 0.2),
                            Qt.SolidLine, Qt.RoundCap))
        offset = badge_radius * 0.66
        painter.drawLine(
            QPointF(badge_center.x() - offset, badge_center.y() + offset),
            QPointF(badge_center.x() + offset, badge_center.y() - offset),
        )

    def _draw_rings(self, painter, center, color):
        """Expanding echoes of your voice, drawn behind everything else."""
        span = self.width() / 2
        for progress, opacity in self._rings:
            ring_color = QColor(color)
            ring_color.setAlphaF(max(0.0, opacity))
            painter.setPen(QPen(ring_color, 2))
            painter.setBrush(Qt.NoBrush)
            r = self.diameter / 2 + progress * (span - self.diameter / 2)
            painter.drawEllipse(center, r, r)

    def _draw_glow(self, painter, center, radius, color, breath):
        # Never exceed the circular mask the window is clipped to, or the glow
        # ends in a hard edge.
        glow_radius = min(radius * (1.55 + self._smoothed * 0.5), self.width() / 2)
        gradient = QRadialGradient(center, glow_radius)
        inner = QColor(color)
        # Idle glow is faint; speaking makes it bloom with the voice.
        strength = 0.16 + breath * 0.06 + self._smoothed * 0.42
        inner.setAlphaF(min(0.75, strength))
        edge = QColor(color)
        edge.setAlphaF(0.0)
        gradient.setColorAt(0.35, inner)
        gradient.setColorAt(1.0, edge)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(center, glow_radius, glow_radius)

    def _draw_core(self, painter, center, radius, color):
        gradient = QRadialGradient(
            QPointF(center.x() - radius * 0.3, center.y() - radius * 0.35), radius * 1.6
        )
        top = QColor(color).lighter(135)
        top.setAlphaF(0.95)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(0.55, QColor(theme.PANEL_LIGHT))
        gradient.setColorAt(1.0, QColor(theme.BG))

        painter.setBrush(QBrush(gradient))
        rim = QColor(color)
        rim.setAlphaF(0.85)
        painter.setPen(QPen(rim, 1.6))
        painter.drawEllipse(center, radius, radius)

    def _draw_sweep(self, painter, center, radius, color):
        """Arc sweeping the rim: a tool is running."""
        sweep_radius = radius + 7
        gradient = QConicalGradient(center, -self._phase * 180 / math.pi * 2)
        bright = QColor(color)
        faded = QColor(color)
        faded.setAlphaF(0.0)
        gradient.setColorAt(0.0, bright)
        gradient.setColorAt(0.35, faded)
        gradient.setColorAt(1.0, faded)

        pen = QPen(QBrush(gradient), 3)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, sweep_radius, sweep_radius)

    def _draw_orbit(self, painter, center, radius, color):
        """A single dot circling the rim: thinking, no tool yet."""
        orbit_radius = radius + 7
        angle = self._phase * 2.2
        dot = QPointF(
            center.x() + math.cos(angle) * orbit_radius,
            center.y() + math.sin(angle) * orbit_radius,
        )
        faint = QColor(color)
        faint.setAlphaF(0.22)
        painter.setPen(QPen(faint, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, orbit_radius, orbit_radius)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(dot, 3.5, 3.5)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        # The window handles dragging; the orb only reports genuine clicks.
        event.ignore()

    def hit_test(self, point) -> bool:
        """True if a point in widget coordinates lands on the orb itself."""
        center = QPointF(self.width() / 2, self.height() / 2)
        offset = QPointF(point) - center
        return (offset.x() ** 2 + offset.y() ** 2) ** 0.5 <= self.diameter / 2 + 6


class OrbLabel(QWidget):
    """The caption under the orb — the current tool, or nothing at all."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.setFixedHeight(20)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_text(self, text: str):
        self.text = text or ""
        self.setVisible(bool(self.text))
        self.update()

    def paintEvent(self, event):
        if not self.text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        font = painter.font()
        font.setPointSize(8)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        text = metrics.elidedText(self.text, Qt.ElideRight, self.width() - 16)
        width = metrics.horizontalAdvance(text) + 16
        pill = QRectF((self.width() - width) / 2, 1, width, 17)

        background = QColor(theme.BG)
        background.setAlphaF(0.82)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(pill, 8.5, 8.5)

        painter.setPen(QColor(theme.WARNING))
        painter.drawText(pill, Qt.AlignCenter, text)
