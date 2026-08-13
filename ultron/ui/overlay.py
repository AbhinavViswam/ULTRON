"""The Ultron overlay: a floating orb with message cards.

The orb is the whole interface. It shows state, it takes clicks for typed
input, and messages appear beside it and fade. There is no chat window — the
transcript lives in the database, not on screen.
"""

import threading

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QRegion
from PySide6.QtWidgets import (
    QApplication, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget
)

from ultron.config import config
from ultron.core import STATE_IDLE, STATE_TOOL
from ultron.ui import theme
from ultron.ui.cards import CardStack, InputCard
from ultron.ui.orb import Orb, OrbLabel
from ultron.ui.settings_window import SettingsWindow

SCREEN_MARGIN = 34
# Space between the orb and the nearest card.
CARD_ANCHOR_GAP = 6

# How an OutputManager source becomes a card kind. "user" is the answer to a
# request; "reminder" and "cron" are things Ultron raised on its own and must
# not look like the loading phrases that share the "system" source.
SOURCE_ROLES = {
    "user": "ultron",
    "reminder": "reminder",
    "cron": "scheduled",
    "system": "system",
    "status": "system",
}
# A press that moves further than this is a drag, not a click.
DRAG_THRESHOLD = 5


class OrbOverlay(QWidget):
    """Frameless always-on-top window containing just the orb and its caption."""

    # Core callbacks arrive on worker threads; signals hop them to the UI thread.
    assistant_message = Signal(str, str)
    user_message = Signal(str, str, bool)
    state_changed = Signal(str, object)
    level_changed = Signal(float)
    core_ready = Signal()
    core_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.core = None
        self._press_pos = None
        self._drag_offset = None
        self._dragging = False

        self._build_window()
        self._build_tray()
        # The microphone is not live until the core finishes booting, so start
        # from the honest state rather than assuming the saved setting.
        self._sync_mic_ui(False)

        self.cards = CardStack()
        self.input_card = InputCard()
        self.input_card.submitted.connect(self._on_submitted)
        self.settings_window = SettingsWindow()
        self.settings_window.settings_saved.connect(self._on_settings_saved)

        self._connect_signals()
        self._restore_position()

        if config.missing_requirements():
            self._show_settings()
            self.cards.add(
                "Setup needed before I can start. " + config.missing_requirements()[0],
                role="system",
            )
        else:
            self._boot_core()

    # ------------------------------------------------------------------
    # Window
    # ------------------------------------------------------------------

    def _build_window(self):
        self.setWindowTitle("Ultron")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(theme.STYLESHEET)

        self.orb = Orb()
        self.label = OrbLabel()
        self.label.setFixedWidth(self.orb.width())
        self.label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.orb, 0, Qt.AlignHCenter)
        layout.addWidget(self.label, 0, Qt.AlignHCenter)

        self.setFixedSize(self.orb.width(), self.orb.height() + self.label.height())
        self.setToolTip("Click to type · drag to move · right-click for menu")
        self._apply_mask()

    def _apply_mask(self):
        """Clips the window to the orb (plus its caption).

        A translucent window still swallows clicks across its whole rectangle,
        which would make the empty space around the orb a dead zone over
        whatever app is underneath. Masking to the visible shape lets those
        clicks reach the window below.
        """
        region = QRegion(self.orb.geometry(), QRegion.Ellipse)
        if self.label.isVisible():
            region = region.united(QRegion(self.label.geometry()))
        self.setMask(region)

    def _restore_position(self):
        """Puts the orb back where it was left, or bottom-right on first run."""
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None

        saved = config.get("overlay_position")
        if isinstance(saved, dict) and "x" in saved and "y" in saved:
            point = QPoint(int(saved["x"]), int(saved["y"]))
            # A saved position from a monitor that is no longer attached would
            # strand the orb offscreen.
            if area is None or area.contains(point):
                self.move(point)
                self._sync_anchor()
                return

        if area:
            self.move(
                area.right() - self.width() - SCREEN_MARGIN,
                area.bottom() - self.height() - SCREEN_MARGIN,
            )
        self._sync_anchor()

    def _save_position(self):
        position = self.pos()
        config.set("overlay_position", {"x": position.x(), "y": position.y()})

    def _sync_anchor(self):
        """Points the card stack at the orb's current screen position."""
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = self.frameGeometry()
        self.cards.set_anchor(
            center_x=geometry.center().x(),
            below_y=geometry.bottom() + CARD_ANCHOR_GAP,
            above_y=geometry.top() - CARD_ANCHOR_GAP,
            screen=screen.availableGeometry(),
        )

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_anchor()

    def showEvent(self, event):
        super().showEvent(event)
        # Child geometry is only final once laid out, so re-mask on show.
        self._apply_mask()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._dragging = False
        elif event.button() == Qt.RightButton:
            self.menu.exec(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        current = event.globalPosition().toPoint()
        if not self._dragging:
            moved = (current - self._press_pos).manhattanLength()
            if moved <= DRAG_THRESHOLD:
                return
            self._dragging = True
        self.move(current - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._dragging:
            self._save_position()
        elif self.orb.hit_test(event.position().toPoint()):
            self._toggle_input()
        self._press_pos = None
        self._dragging = False

    def _toggle_input(self):
        if self.input_card.isVisible():
            self.input_card.hide()
            return
        position = self.pos()
        self.input_card.open_at(
            QPoint(position.x() - self.input_card.width() - 10, position.y() + 10)
        )

    def _on_submitted(self, text: str):
        if not self.core:
            self.cards.add("I am not online yet, sir.", role="system")
            return
        self.input_card.hide()
        # No card here: submit() announces the input itself, and only it knows
        # whether the request went straight through or had to queue.
        self.core.submit(text, origin="keyboard")

    # ------------------------------------------------------------------
    # Tray
    # ------------------------------------------------------------------

    def _build_tray(self):
        self.tray = QSystemTrayIcon(theme.make_app_icon(), self)
        self.tray.setToolTip("Ultron")

        self.menu = QMenu()
        self.menu.setStyleSheet(theme.STYLESHEET)

        self.mic_action = QAction("Microphone", self)
        self.mic_action.setCheckable(True)
        self.mic_action.triggered.connect(self._toggle_mic)
        self.menu.addAction(self.mic_action)

        self.quiet_action = QAction("Stop talking", self)
        self.quiet_action.triggered.connect(self._silence)
        self.menu.addAction(self.quiet_action)

        self.menu.addSeparator()

        show_action = QAction("Show orb", self)
        show_action.triggered.connect(self._show_orb)
        self.menu.addAction(show_action)

        hide_action = QAction("Hide orb", self)
        hide_action.triggered.connect(self.hide)
        self.menu.addAction(hide_action)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._show_settings)
        self.menu.addAction(settings_action)

        self.menu.addSeparator()
        quit_action = QAction("Quit Ultron", self)
        quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason != QSystemTrayIcon.Trigger:
            return
        if self.isVisible():
            self.hide()
        else:
            self._show_orb()

    def _show_orb(self):
        self.show()
        self.raise_()
        self._sync_anchor()

    def summon(self):
        """Brings the orb back — used when a second launch is attempted."""
        self._show_orb()
        self.cards.add("Already running, sir.", role="system")

    def _toggle_mic(self):
        if not self.core:
            self._sync_mic_ui(False)
            return
        active = self.core.set_microphone(self.mic_action.isChecked())
        self._sync_mic_ui(active)
        self.cards.add(
            "Microphone on, sir." if active else "Microphone off.", role="system"
        )

    def _sync_mic_ui(self, active: bool):
        """Reflects microphone state everywhere it is visible at once.

        The tray icon, the menu entry and the orb each need updating; leaving
        any one of them stale makes the state ambiguous.
        """
        self.mic_action.setChecked(active)
        self.mic_action.setText("Microphone  ·  on" if active else "Microphone  ·  off")
        self.orb.set_muted(not active)
        self.tray.setIcon(theme.make_app_icon(muted=not active))
        self.tray.setToolTip("Ultron — listening" if active else "Ultron — microphone off")

    def _silence(self):
        if self.core:
            self.core.output_manager.interrupt()

    def _show_settings(self):
        self.settings_window.show_panel()

    # ------------------------------------------------------------------
    # Core wiring
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.assistant_message.connect(self._on_assistant_message)
        self.user_message.connect(self._on_user_message)
        self.state_changed.connect(self._on_state_changed)
        self.level_changed.connect(self.orb.set_level)
        self.core_ready.connect(self._on_core_ready)
        self.core_failed.connect(self._on_core_failed)

    def _boot_core(self):
        """Builds the core off the UI thread so the orb appears immediately."""

        def work():
            try:
                from ultron.core import UltronCore

                core = UltronCore(echo_to_console=False)
                core.on_assistant_message(
                    lambda text, source: self.assistant_message.emit(text, source)
                )
                core.on_user_message(
                    lambda text, origin, queued: self.user_message.emit(text, origin, queued)
                )
                core.on_state_changed(
                    lambda state, detail: self.state_changed.emit(state, detail)
                )
                core.on_level(lambda level: self.level_changed.emit(level))
                core.on_status(lambda text: self.assistant_message.emit(text, "status"))
                self.core = core
                core.start()
                self.core_ready.emit()
            except Exception as e:
                self.core_failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_core_ready(self):
        self._sync_mic_ui(self.core.microphone_active)

    def _on_core_failed(self, message: str):
        self.cards.add(f"I could not start: {message}", role="system")
        self._show_settings()

    def _on_state_changed(self, state: str, detail):
        self.orb.set_state(state, detail)
        self.label.set_text(detail if state == STATE_TOOL else "")
        # The caption appearing changes the window's visible shape.
        self._apply_mask()

    def _on_assistant_message(self, text: str, source: str):
        self.cards.add(text, role=SOURCE_ROLES.get(source, "system"))

    def _on_user_message(self, text: str, origin: str, queued: bool):
        """Cards every accepted input, typed or spoken, queued or immediate.

        Voice is marked as spoken so you can see what was actually heard, and
        anything waiting behind a turn in progress is marked as queued rather
        than silently vanishing until its turn comes.
        """
        role = "user_voice" if origin == "voice" else "user"
        if queued:
            role += "_queued"
        self.cards.add(text, role=role)

    def _on_settings_saved(self):
        if not self.core:
            if not config.missing_requirements():
                self._boot_core()
            return
        self.core.set_microphone(bool(config.get("microphone_active", True)))
        self._sync_mic_ui(self.core.microphone_active)
        self.cards.add(f"Settings updated. Using {config.model_for()}.", role="system")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """Closing hides the orb; quitting is an explicit menu choice."""
        event.ignore()
        self.hide()

    def quit_app(self):
        self.orb.set_state(STATE_IDLE, None)
        self.cards.close_all()
        self.input_card.close()
        self.settings_window.close()
        if self.core:
            self.core.shutdown()
        self.tray.hide()
        QTimer.singleShot(0, QApplication.quit)
