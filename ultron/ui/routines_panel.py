"""Managing routines without saying a word.

A routine is an instruction Ultron carries out on a schedule — unlike a
reminder, which only speaks a sentence, a routine does the work. Setting one
up by voice means dictating a paragraph of instruction and a schedule in one
breath, which is exactly the kind of thing a form is better at.

The schedule controls here do not build a schedule themselves. They assemble
the same short phrase the voice path uses — "weekdays", "every 3 days",
"monday and friday" — and hand it to routines.parse_schedule. One grammar,
one parser, one set of tests, whichever way the routine was created.
"""

import datetime

from PySide6.QtCore import QDate, QTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QTimeEdit, QVBoxLayout, QWidget
)

from ultron import routines as sched
from ultron.ui import theme

# Repeat choices, paired with how each is phrased for the parser. None means
# the phrase depends on other controls and is built in _when_text.
REPEAT_CHOICES = [
    ("Every day", "daily"),
    ("Weekdays (Mon–Fri)", "weekdays"),
    ("Weekends", "weekends"),
    ("Certain days…", None),
    ("Every N days…", None),
    ("Monthly, on a date…", None),
    ("Once, on a date…", None),
]

CUSTOM_DAYS_INDEX = 3
EVERY_N_INDEX = 4
MONTHLY_INDEX = 5
ONCE_INDEX = 6

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DELIVERY_CHOICES = [
    ("speak", "Say it aloud"),
    ("card", "Show a card"),
    ("toast", "Notification"),
    ("file", "Markdown log"),
]

# Enough to recognise the routine, not enough to swamp the row.
INSTRUCTION_PREVIEW = 90
RESULT_PREVIEW = 110


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("sectionHeader")
    return label


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.HLine)
    return line


def _shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_next_run(routine: dict, now: datetime.datetime = None) -> str:
    """When this routine runs next, in words worth reading at a glance."""
    if not routine["enabled"]:
        return "disabled"
    if not routine["next_run"]:
        return "not scheduled"

    try:
        moment = datetime.datetime.fromisoformat(routine["next_run"])
    except (TypeError, ValueError):
        return "not scheduled"

    now = now or datetime.datetime.now()
    if moment.date() == now.date():
        return f"next today at {moment:%H:%M}"
    if moment.date() == now.date() + datetime.timedelta(days=1):
        return f"next tomorrow at {moment:%H:%M}"
    return f"next {moment:%a %d %b} at {moment:%H:%M}"


class RoutineRow(QFrame):
    """One routine, with the buttons that act on it."""

    run_now = Signal(int)
    toggled = Signal(int, bool)
    edit = Signal(int)
    remove = Signal(int)

    def __init__(self, routine: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("routineRow")
        self.routine_id = routine["id"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(routine["name"])
        name.setObjectName("routineName")
        if not routine["enabled"]:
            name.setEnabled(False)
        top.addWidget(name, 1)

        delete_button = QPushButton("✕")
        delete_button.setObjectName("iconButton")
        delete_button.setToolTip("Delete this routine")
        delete_button.clicked.connect(lambda: self.remove.emit(self.routine_id))
        top.addWidget(delete_button)
        layout.addLayout(top)

        schedule = QLabel(f"{sched.describe(routine['schedule'])} · "
                          f"{describe_next_run(routine)}")
        schedule.setObjectName("hint")
        layout.addWidget(schedule)

        instruction = QLabel(_shorten(routine["instruction"], INSTRUCTION_PREVIEW))
        instruction.setObjectName("hint")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        if routine["last_result"]:
            last = QLabel("Last: " + _shorten(routine["last_result"], RESULT_PREVIEW))
            last.setObjectName("hint")
            last.setWordWrap(True)
            layout.addWidget(last)

        if routine["fail_count"]:
            failed = QLabel(f"⚠ failed {routine['fail_count']} time(s) in a row")
            failed.setObjectName("hint")
            layout.addWidget(failed)

        # Their own line. Sharing the title row meant a long routine name
        # pushed them off the edge of the window entirely.
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch(1)
        for label, slot in (
            ("Run now", lambda: self.run_now.emit(self.routine_id)),
            ("Disable" if routine["enabled"] else "Enable",
             lambda: self.toggled.emit(self.routine_id, not routine["enabled"])),
            ("Edit", lambda: self.edit.emit(self.routine_id)),
        ):
            button = QPushButton(label)
            button.setObjectName("smallButton")
            button.clicked.connect(slot)
            actions.addWidget(button)
        layout.addSpacing(2)
        layout.addLayout(actions)


class RoutinesPanel(QWidget):
    """The list of routines, plus the form that creates and edits them."""

    closed = Signal()

    def __init__(self, get_db, run_now, parent=None):
        """
        Args:
            get_db:  callable returning the live Database, or None before the
                     core has finished booting.
            run_now: callable taking a routine name, to run it immediately.
        """
        super().__init__(parent)
        self._get_db = get_db
        self._run_now = run_now
        self._editing_id = None
        self._build()
        self.reload()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(2, 2, 8, 2)
        self._body.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._body.addWidget(_section("Your routines"))
        self._list = QVBoxLayout()
        self._list.setSpacing(6)
        self._body.addLayout(self._list)

        self._empty = QLabel(
            "Nothing scheduled yet. A routine is a job Ultron does on its own — "
            "look something up, check something, write something down — not just "
            "a reminder to do it yourself."
        )
        self._empty.setObjectName("hint")
        self._empty.setWordWrap(True)
        self._body.addWidget(self._empty)

        self._body.addWidget(_separator())
        self._build_form()
        self._body.addStretch(1)

        footer = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)

        back_button = QPushButton("Close")
        back_button.clicked.connect(self.closed.emit)
        footer.addWidget(back_button)
        outer.addLayout(footer)

    def _build_form(self):
        self._form_header = _section("New routine")
        self._body.addWidget(self._form_header)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setSpacing(7)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Day in history")
        form.addRow("Name", self.name_edit)

        self.instruction_edit = QPlainTextEdit()
        self.instruction_edit.setPlaceholderText(
            "Search the web for what is notable about today — national days, "
            "festivals, historic events — and tell me the highlights."
        )
        self.instruction_edit.setFixedHeight(64)
        form.addRow("Do this", self.instruction_edit)

        self.repeat_combo = QComboBox()
        for label, _phrase in REPEAT_CHOICES:
            self.repeat_combo.addItem(label)
        self.repeat_combo.currentIndexChanged.connect(self._apply_repeat_visibility)
        form.addRow("Repeat", self.repeat_combo)

        # --- the controls each repeat mode needs --------------------------
        self.days_row = QWidget()
        days_layout = QHBoxLayout(self.days_row)
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setSpacing(2)
        self.day_checks = []
        for label in DAY_LABELS:
            check = QCheckBox(label)
            self.day_checks.append(check)
            days_layout.addWidget(check)
        days_layout.addStretch(1)
        self.days_label = QLabel("On")
        form.addRow(self.days_label, self.days_row)

        self.every_n_spin = QSpinBox()
        self.every_n_spin.setRange(1, 365)
        self.every_n_spin.setValue(3)
        self.every_n_spin.setSuffix(" days")
        self.every_n_label = QLabel("Every")
        form.addRow(self.every_n_label, self.every_n_spin)

        self.day_of_month_spin = QSpinBox()
        self.day_of_month_spin.setRange(1, 31)
        self.day_of_month_spin.setValue(1)
        self.day_of_month_label = QLabel("Day of month")
        form.addRow(self.day_of_month_label, self.day_of_month_spin)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate().addDays(1))
        self.date_label = QLabel("On date")
        form.addRow(self.date_label, self.date_edit)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime(8, 0))
        form.addRow("At", self.time_edit)

        # A grid, not a row: four checkboxes side by side set a minimum width
        # for the whole panel, and every routine row above then got clipped by
        # a window sized to something the user never sees.
        delivery_row = QWidget()
        delivery_layout = QGridLayout(delivery_row)
        delivery_layout.setContentsMargins(0, 0, 0, 0)
        delivery_layout.setSpacing(4)
        self.delivery_checks = {}
        for index, (value, label) in enumerate(DELIVERY_CHOICES):
            check = QCheckBox(label)
            check.setChecked(value in ("speak", "card"))
            self.delivery_checks[value] = check
            delivery_layout.addWidget(check, index // 2, index % 2)
        form.addRow("Deliver", delivery_row)
        self._body.addLayout(form)

        hint = QLabel(
            "A routine that is missed — because Ultron was not running — is not "
            "run late. It waits for its next turn."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self._body.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel edit")
        self.cancel_button.clicked.connect(self._clear_form)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)

        self.save_button = QPushButton("Create routine")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        self._body.addLayout(buttons)

        self._apply_repeat_visibility()

    # ------------------------------------------------------------------
    # The form
    # ------------------------------------------------------------------

    def _apply_repeat_visibility(self):
        """Only the controls the chosen repeat mode actually uses."""
        index = self.repeat_combo.currentIndex()
        for widget, wanted in (
            (self.days_row, index == CUSTOM_DAYS_INDEX),
            (self.days_label, index == CUSTOM_DAYS_INDEX),
            (self.every_n_spin, index == EVERY_N_INDEX),
            (self.every_n_label, index == EVERY_N_INDEX),
            (self.day_of_month_spin, index == MONTHLY_INDEX),
            (self.day_of_month_label, index == MONTHLY_INDEX),
            (self.date_edit, index == ONCE_INDEX),
            (self.date_label, index == ONCE_INDEX),
        ):
            widget.setVisible(wanted)

    def _when_text(self) -> str:
        """The schedule as the phrase parse_schedule already understands.

        Building the phrase rather than the schedule dict means the form and
        the voice path cannot drift apart: both are parsed by the same code.
        """
        index = self.repeat_combo.currentIndex()
        phrase = REPEAT_CHOICES[index][1]
        if phrase:
            return phrase
        if index == CUSTOM_DAYS_INDEX:
            chosen = [sched.WEEKDAY_NAMES[i].lower()
                      for i, check in enumerate(self.day_checks) if check.isChecked()]
            return ", ".join(chosen)
        if index == EVERY_N_INDEX:
            return f"every {self.every_n_spin.value()} days"
        if index == MONTHLY_INDEX:
            return f"monthly on the {self.day_of_month_spin.value()}"
        return self.date_edit.date().toString("yyyy-MM-dd")

    def _deliver_text(self) -> str:
        chosen = [value for value, check in self.delivery_checks.items()
                  if check.isChecked()]
        return ",".join(chosen)

    def _clear_form(self):
        self._editing_id = None
        self.name_edit.clear()
        self.instruction_edit.clear()
        self.repeat_combo.setCurrentIndex(0)
        for check in self.day_checks:
            check.setChecked(False)
        self.time_edit.setTime(QTime(8, 0))
        for value, check in self.delivery_checks.items():
            check.setChecked(value in ("speak", "card"))
        self._form_header.setText("NEW ROUTINE")
        self.save_button.setText("Create routine")
        self.cancel_button.hide()
        self._apply_repeat_visibility()

    def _load_into_form(self, routine: dict):
        self._editing_id = routine["id"]
        self.name_edit.setText(routine["name"])
        self.instruction_edit.setPlainText(routine["instruction"])

        schedule = routine["schedule"]
        kind, days = schedule["kind"], schedule["days"]
        if kind == "interval":
            self.repeat_combo.setCurrentIndex(EVERY_N_INDEX)
            self.every_n_spin.setValue(max(1, schedule["every_n_days"]))
        elif kind == "monthly":
            self.repeat_combo.setCurrentIndex(MONTHLY_INDEX)
            self.day_of_month_spin.setValue(max(1, schedule["day_of_month"]))
        elif kind == "once":
            self.repeat_combo.setCurrentIndex(ONCE_INDEX)
            if schedule["once_date"]:
                self.date_edit.setDate(QDate.fromString(schedule["once_date"], "yyyy-MM-dd"))
        elif days == sched.WEEKDAYS:
            self.repeat_combo.setCurrentIndex(1)
        elif days == sched.WEEKEND:
            self.repeat_combo.setCurrentIndex(2)
        elif kind == "weekly":
            self.repeat_combo.setCurrentIndex(CUSTOM_DAYS_INDEX)
            for index, check in enumerate(self.day_checks):
                check.setChecked(index in days)
        else:
            self.repeat_combo.setCurrentIndex(0)

        self.time_edit.setTime(QTime.fromString(schedule["at_time"], "HH:mm"))
        chosen = routine["deliver"].split(",")
        for value, check in self.delivery_checks.items():
            check.setChecked(value in chosen)

        self._form_header.setText(f"EDITING · {routine['name'].upper()}")
        self.save_button.setText("Save changes")
        self.cancel_button.show()
        self._apply_repeat_visibility()

    def _save(self):
        db = self._get_db()
        if db is None:
            self._flash("Ultron is still starting up — try again in a moment.")
            return

        name = self.name_edit.text().strip()
        instruction = self.instruction_edit.toPlainText().strip()
        if not name or not instruction:
            self._flash("A routine needs both a name and something to do.")
            return

        clash = [r for r in db.list_routines()
                 if r["name"].lower() == name.lower() and r["id"] != self._editing_id]
        if clash:
            self._flash(f"There is already a routine called '{name}'.")
            return

        when = self._when_text()
        if not when:
            self._flash("Pick at least one day.")
            return

        try:
            schedule = sched.parse_schedule(when, self.time_edit.time().toString("HH:mm"))
        except sched.ScheduleError as e:
            self._flash(str(e))
            return

        following = sched.next_run(schedule)
        if following is None:
            self._flash("That date has already passed.")
            return

        deliver = self._deliver_text()
        if not deliver:
            self._flash("Pick at least one way to deliver the result.")
            return

        if self._editing_id is None:
            db.add_routine(name, instruction, schedule, following.isoformat(), deliver)
            self._flash(f"'{name}' created — {sched.describe(schedule)}, "
                        f"first run {following:%a %d %b at %H:%M}.")
        else:
            db.update_routine(self._editing_id, name=name,
                              instruction=instruction, deliver=deliver)
            db.set_routine_schedule(self._editing_id, schedule, following.isoformat())
            self._flash(f"'{name}' updated — {sched.describe(schedule)}.")

        self._clear_form()
        self.reload()

    # ------------------------------------------------------------------
    # The list
    # ------------------------------------------------------------------

    def reload(self):
        """Rebuilds the list from the database."""
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget:
                # Unparented now, not merely queued: deleteLater leaves the old
                # rows on screen until the event loop gets to them, which shows
                # up as a duplicate row stacked over the new list.
                widget.setParent(None)
                widget.deleteLater()

        db = self._get_db()
        routines = db.list_routines() if db is not None else []
        self._empty.setVisible(not routines)

        for routine in routines:
            row = RoutineRow(routine)
            row.run_now.connect(self._on_run_now)
            row.toggled.connect(self._on_toggled)
            row.edit.connect(self._on_edit)
            row.remove.connect(self._on_remove)
            self._list.addWidget(row)

    def _routine(self, routine_id: int):
        db = self._get_db()
        return db.get_routine(routine_id) if db is not None else None

    def _on_run_now(self, routine_id: int):
        routine = self._routine(routine_id)
        if not routine:
            return
        self._run_now(routine["name"])
        self._flash(f"Running '{routine['name']}' now — the result will arrive "
                    "when it finishes.")

    def _on_toggled(self, routine_id: int, enabled: bool):
        db = self._get_db()
        routine = self._routine(routine_id)
        if db is None or not routine:
            return

        if enabled:
            # It has been sitting with a next_run in the past. Re-enabling must
            # move it forward, or the no-catch-up rule fires the moment it is
            # switched on and the routine appears to run by itself.
            following = sched.next_run(routine["schedule"])
            db.update_routine(routine_id, enabled=1, fail_count=0,
                              next_run=following.isoformat() if following else "")
        else:
            db.update_routine(routine_id, enabled=0)

        self._flash(f"'{routine['name']}' {'enabled' if enabled else 'disabled'}.")
        self.reload()

    def _on_edit(self, routine_id: int):
        routine = self._routine(routine_id)
        if routine:
            self._load_into_form(routine)

    def _on_remove(self, routine_id: int):
        db = self._get_db()
        routine = self._routine(routine_id)
        if db is None or not routine:
            return

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete routine")
        confirm.setText(f"Delete '{routine['name']}'?")
        confirm.setInformativeText("This cannot be undone.")
        confirm.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        confirm.setDefaultButton(QMessageBox.Cancel)
        confirm.setStyleSheet(theme.STYLESHEET)
        if confirm.exec() != QMessageBox.Yes:
            return

        db.delete_routine(routine_id)
        if self._editing_id == routine_id:
            self._clear_form()
        self._flash(f"'{routine['name']}' deleted.")
        self.reload()

    def _flash(self, message: str):
        self.status_label.setText(message)
