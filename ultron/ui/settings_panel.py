"""Settings page for the overlay.

Covers every piece of configuration the assistant needs, so a fresh install
can be brought online without touching a JSON file: provider and model, API
keys, microphone, truth mode, the agent monitor, and Gmail authorization.
"""

import os
import shutil
import threading

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget
)

from ultron.config import config, CREDENTIALS_PATH
from ultron.ui import theme

PROVIDER_CHOICES = [
    ("OpenRouter", "openrouterapi"),
    ("Gemini", "geminiapi"),
    ("Local (Ollama)", "localapi"),
]

# Which settings key holds the model name for each provider.
MODEL_SETTING = {
    "openrouterapi": "openrouter_model",
    "geminiapi": "gemini_model",
    "localapi": "local_model",
}

ALERT_MODES = ["both", "toast", "voice"]


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("sectionHeader")
    return label


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.HLine)
    return line


class _GmailWorker(QObject):
    """Runs the Google consent flow off the UI thread.

    `run_local_server` spins a browser and blocks until the user consents,
    which would freeze the window if called inline.
    """

    finished = Signal(bool, str)

    def authorize(self):
        def work():
            try:
                from ultron.plugins.gmail_plugin import authenticate_gmail
                authenticate_gmail()
                self.finished.emit(True, "Gmail connected.")
            except Exception as e:
                self.finished.emit(False, str(e))

        threading.Thread(target=work, daemon=True).start()


class SettingsPanel(QWidget):
    """Editable view over every configuration file."""

    closed = Signal()
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gmail_worker = _GmailWorker(self)
        self._gmail_worker.finished.connect(self._on_gmail_result)
        self._build()
        self.reload()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.hide()
        outer.addWidget(self.banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(2, 2, 8, 2)
        form.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # --- Provider ---------------------------------------------------
        form.addWidget(_section("AI Provider"))
        provider_form = QFormLayout()
        provider_form.setLabelAlignment(Qt.AlignLeft)
        provider_form.setSpacing(7)

        self.provider_combo = QComboBox()
        for label, value in PROVIDER_CHOICES:
            self.provider_combo.addItem(label, value)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_form.addRow("Provider", self.provider_combo)

        self.model_edit = QLineEdit()
        provider_form.addRow("Model", self.model_edit)

        self.local_url_edit = QLineEdit()
        self.local_url_row_label = QLabel("Local API URL")
        provider_form.addRow(self.local_url_row_label, self.local_url_edit)

        form.addLayout(provider_form)
        form.addWidget(_separator())

        # --- API keys ---------------------------------------------------
        form.addWidget(_section("API Keys"))
        keys_form = QFormLayout()
        keys_form.setSpacing(7)

        self.openrouter_key = QLineEdit()
        self.openrouter_key.setEchoMode(QLineEdit.Password)
        self.openrouter_key.setPlaceholderText("sk-or-...")
        keys_form.addRow("OpenRouter", self.openrouter_key)

        self.google_key = QLineEdit()
        self.google_key.setEchoMode(QLineEdit.Password)
        self.google_key.setPlaceholderText("AIza...")
        keys_form.addRow("Google", self.google_key)

        form.addLayout(keys_form)

        self.reveal_keys = QCheckBox("Show keys")
        self.reveal_keys.toggled.connect(self._on_reveal_toggled)
        form.addWidget(self.reveal_keys)

        keys_hint = QLabel("Stored in keys.json, which is gitignored.")
        keys_hint.setObjectName("hint")
        form.addWidget(keys_hint)
        form.addWidget(_separator())

        # --- Assistant behaviour ---------------------------------------
        form.addWidget(_section("Assistant"))
        self.mic_check = QCheckBox("Continuous background microphone")
        form.addWidget(self.mic_check)
        self.self_hearing_check = QCheckBox(
            "Ignore my own voice coming back through the speakers")
        self.self_hearing_check.setToolTip(
            "Only matters on speakers. Turn it off if you use headphones.")
        form.addWidget(self.self_hearing_check)
        self.truth_check = QCheckBox("Truth mode (never guess or fabricate)")
        form.addWidget(self.truth_check)

        self.startup_check = QCheckBox("Start Ultron when Windows starts")
        # Applied immediately rather than on Save: it writes a shortcut to the
        # Startup folder, not a setting, so it has nothing to do with the rest
        # of this form.
        self.startup_check.toggled.connect(self._on_startup_toggled)
        form.addWidget(self.startup_check)
        form.addWidget(_separator())

        # --- Idle chat ----------------------------------------------------
        form.addWidget(_section("Idle Chat"))
        self.idle_check = QCheckBox("Speak up when nothing has happened for a while")
        form.addWidget(self.idle_check)

        idle_form = QFormLayout()
        idle_form.setSpacing(7)

        self.idle_after_spin = QSpinBox()
        self.idle_after_spin.setRange(1, 240)
        self.idle_after_spin.setSuffix(" min")
        idle_form.addRow("Speak after", self.idle_after_spin)

        quiet_row = QHBoxLayout()
        self.quiet_start_spin = QSpinBox()
        self.quiet_start_spin.setRange(0, 23)
        self.quiet_start_spin.setSuffix(":00")
        self.quiet_end_spin = QSpinBox()
        self.quiet_end_spin.setRange(0, 23)
        self.quiet_end_spin.setSuffix(":00")
        quiet_row.addWidget(self.quiet_start_spin)
        quiet_row.addWidget(QLabel("to"))
        quiet_row.addWidget(self.quiet_end_spin)
        quiet_row.addStretch(1)
        idle_form.addRow("Stay silent", quiet_row)

        self.give_up_spin = QSpinBox()
        self.give_up_spin.setRange(0, 20)
        # 0 is the "never stop" case, which needs saying rather than showing
        # as a bare zero.
        self.give_up_spin.setSpecialValueText("never give up")
        self.give_up_spin.setSuffix(" unanswered")
        idle_form.addRow("Stop after", self.give_up_spin)
        form.addLayout(idle_form)

        self.idle_mic_check = QCheckBox("Stay silent while the microphone is off")
        form.addWidget(self.idle_mic_check)
        self.idle_fullscreen_check = QCheckBox(
            "Stay silent during games, video and screen shares"
        )
        form.addWidget(self.idle_fullscreen_check)

        idle_hint = QLabel(
            "Uses what it knows about you, so it can offer something relevant "
            "rather than just asking if you are still there."
        )
        idle_hint.setObjectName("hint")
        idle_hint.setWordWrap(True)
        form.addWidget(idle_hint)
        form.addWidget(_separator())

        # --- Agent monitor ----------------------------------------------
        form.addWidget(_section("Agent Monitor"))
        self.monitor_check = QCheckBox("Watch coding agents and alert me")
        form.addWidget(self.monitor_check)

        monitor_form = QFormLayout()
        monitor_form.setSpacing(7)
        self.alert_combo = QComboBox()
        self.alert_combo.addItems(ALERT_MODES)
        monitor_form.addRow("Alert mode", self.alert_combo)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        monitor_form.addRow("Listener port", self.port_spin)
        form.addLayout(monitor_form)

        monitor_hint = QLabel("Port changes take effect on restart.")
        monitor_hint.setObjectName("hint")
        form.addWidget(monitor_hint)
        form.addWidget(_separator())

        # --- Gmail --------------------------------------------------------
        form.addWidget(_section("Gmail"))
        self.gmail_status = QLabel()
        self.gmail_status.setWordWrap(True)
        form.addWidget(self.gmail_status)

        gmail_row = QHBoxLayout()
        self.creds_button = QPushButton("Choose credentials.json…")
        self.creds_button.clicked.connect(self._pick_credentials)
        gmail_row.addWidget(self.creds_button)

        self.gmail_button = QPushButton("Connect")
        self.gmail_button.clicked.connect(self._toggle_gmail)
        gmail_row.addWidget(self.gmail_button)
        gmail_row.addStretch(1)
        form.addLayout(gmail_row)

        form.addStretch(1)

        # --- Footer -------------------------------------------------------
        footer = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        footer.addWidget(self.status_label, 1)

        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.closed.emit)
        footer.addWidget(self.back_button)

        save_button = QPushButton("Save")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self.save)
        footer.addWidget(save_button)
        outer.addLayout(footer)

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------

    def reload(self):
        """Populates every field from the current configuration."""
        provider = config.active_provider()
        index = self.provider_combo.findData(provider)
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(max(index, 0))
        self.provider_combo.blockSignals(False)

        self.model_edit.setText(config.model_for(provider))
        self.local_url_edit.setText(config.get("local_api_url", "http://localhost:11434/v1"))
        self._apply_provider_visibility(provider)

        self.openrouter_key.setText(config.get_key("openrouter"))
        self.google_key.setText(config.get_key("google"))

        self.mic_check.setChecked(bool(config.get("microphone_active", True)))
        self.truth_check.setChecked(bool(config.get("truth_mode", False)))
        self.self_hearing_check.setChecked(
            bool(config.get("self_hearing_guard", True)))

        # Reflects the Startup folder, not settings.json, so read it from disk
        # without letting the signal fire back and rewrite the shortcut.
        from ultron.launcher import startup_enabled

        self.startup_check.blockSignals(True)
        self.startup_check.setChecked(startup_enabled())
        self.startup_check.blockSignals(False)

        self.idle_check.setChecked(bool(config.get("idle_chat.enabled", True)))
        self.idle_after_spin.setValue(int(config.get("idle_chat.after_minutes", 25)))
        self.quiet_start_spin.setValue(int(config.get("idle_chat.quiet_start_hour", 22)))
        self.quiet_end_spin.setValue(int(config.get("idle_chat.quiet_end_hour", 8)))
        self.give_up_spin.setValue(int(config.get("idle_chat.give_up_after", 0)))
        self.idle_mic_check.setChecked(
            bool(config.get("idle_chat.silent_when_mic_off", False)))
        self.idle_fullscreen_check.setChecked(
            bool(config.get("idle_chat.silent_in_fullscreen", False)))

        self.monitor_check.setChecked(bool(config.get("agent_monitor.enabled", True)))
        mode = config.get("agent_monitor.alert_mode", "both")
        self.alert_combo.setCurrentIndex(ALERT_MODES.index(mode) if mode in ALERT_MODES else 0)
        self.port_spin.setValue(int(config.get("agent_monitor.port", 8787)))

        self._refresh_gmail_status()
        self._refresh_banner()

    def save(self):
        """Writes every field back to settings.json and keys.json."""
        provider = self.provider_combo.currentData()

        updates = {name: (name == provider) for _label, name in PROVIDER_CHOICES}
        updates[MODEL_SETTING[provider]] = self.model_edit.text().strip()
        updates["local_api_url"] = self.local_url_edit.text().strip() or "http://localhost:11434/v1"
        updates["microphone_active"] = self.mic_check.isChecked()
        updates["truth_mode"] = self.truth_check.isChecked()
        updates["self_hearing_guard"] = self.self_hearing_check.isChecked()
        updates["idle_chat.enabled"] = self.idle_check.isChecked()
        updates["idle_chat.after_minutes"] = self.idle_after_spin.value()
        updates["idle_chat.quiet_start_hour"] = self.quiet_start_spin.value()
        updates["idle_chat.quiet_end_hour"] = self.quiet_end_spin.value()
        updates["idle_chat.give_up_after"] = self.give_up_spin.value()
        updates["idle_chat.silent_when_mic_off"] = self.idle_mic_check.isChecked()
        updates["idle_chat.silent_in_fullscreen"] = self.idle_fullscreen_check.isChecked()
        updates["agent_monitor.enabled"] = self.monitor_check.isChecked()
        updates["agent_monitor.alert_mode"] = self.alert_combo.currentText()
        updates["agent_monitor.port"] = self.port_spin.value()

        config.update(updates)
        config.set_key("openrouter", self.openrouter_key.text())
        config.set_key("google", self.google_key.text())

        self._refresh_banner()
        self._flash("Saved.")
        self.settings_saved.emit()

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    def _on_provider_changed(self):
        provider = self.provider_combo.currentData()
        # Show the model already configured for the newly selected provider.
        self.model_edit.setText(config.model_for(provider))
        self._apply_provider_visibility(provider)

    def _apply_provider_visibility(self, provider: str):
        is_local = provider == "localapi"
        self.local_url_edit.setVisible(is_local)
        self.local_url_row_label.setVisible(is_local)

    def _on_startup_toggled(self, enabled: bool):
        from ultron.launcher import set_startup

        try:
            self._flash(set_startup(enabled))
        except Exception as e:
            self._flash(f"Could not change startup: {e}")
            # Put the box back to what is actually true on disk.
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(not enabled)
            self.startup_check.blockSignals(False)

    def _on_reveal_toggled(self, shown: bool):
        mode = QLineEdit.Normal if shown else QLineEdit.Password
        self.openrouter_key.setEchoMode(mode)
        self.google_key.setEchoMode(mode)

    def _refresh_banner(self):
        problems = config.missing_requirements()
        if not problems:
            self.banner.hide()
            return
        self.banner.setText("⚠  " + "\n".join(problems))
        self.banner.setStyleSheet(
            f"background: #2a1f14; border: 1px solid {theme.WARNING};"
            f"border-radius: 8px; padding: 9px 12px; color: {theme.WARNING};"
        )
        self.banner.show()

    def _flash(self, message: str):
        self.status_label.setText(message)

    # ------------------------------------------------------------------
    # Gmail
    # ------------------------------------------------------------------

    def _refresh_gmail_status(self):
        from ultron.plugins.gmail_plugin import gmail_connection_status

        status = gmail_connection_status()
        colors = {
            "ready": theme.SUCCESS,
            "needs_authorization": theme.WARNING,
            "needs_credentials": theme.TEXT_DIM,
        }
        self.gmail_status.setText(status["message"])
        self.gmail_status.setStyleSheet(f"color: {colors.get(status['state'], theme.TEXT_DIM)};")

        self.gmail_button.setText("Disconnect" if status["state"] == "ready" else "Connect")
        self.gmail_button.setEnabled(status["state"] != "needs_credentials")

    def _pick_credentials(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Google OAuth credentials", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            if os.path.abspath(path) != os.path.abspath(CREDENTIALS_PATH):
                shutil.copyfile(path, CREDENTIALS_PATH)
            self._flash("Credentials installed.")
        except OSError as e:
            self._flash(f"Could not copy credentials: {e}")
        self._refresh_gmail_status()

    def _toggle_gmail(self):
        from ultron.plugins.gmail_plugin import disconnect_gmail, gmail_connection_status

        if gmail_connection_status()["state"] == "ready":
            disconnect_gmail()
            self._flash("Gmail disconnected.")
            self._refresh_gmail_status()
            return

        self.gmail_button.setEnabled(False)
        self._flash("Waiting for Google consent in your browser…")
        self._gmail_worker.authorize()

    def _on_gmail_result(self, ok: bool, message: str):
        self._flash(message if ok else f"Gmail error: {message}")
        self.gmail_button.setEnabled(True)
        self._refresh_gmail_status()
