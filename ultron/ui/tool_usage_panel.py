from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget, QPushButton
)
from ultron.ui import theme

class ToolUsageRow(QFrame):
    def __init__(self, stat: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("routineRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        name = QLabel(stat["tool_name"])
        name.setObjectName("routineName")
        layout.addWidget(name)

        stats = QLabel(f"{stat['successes']} successes · {stat['failures']} failures")
        stats.setObjectName("hint")
        layout.addWidget(stats)

        if stat["last_used"]:
            last = QLabel(f"Last used: {stat['last_used']}")
            last.setObjectName("hint")
            layout.addWidget(last)

class ToolUsagePanel(QWidget):
    closed = Signal()

    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self._get_db = get_db
        self._build()
        self.reload()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("TOOL HISTORY")
        title.setObjectName("sectionHeader")
        top.addWidget(title, 1)

        clear = QPushButton("Clear")
        clear.setObjectName("smallButton")
        clear.clicked.connect(self.clear_history)
        top.addWidget(clear)

        close = QPushButton("Done")
        close.setObjectName("smallButton")
        close.clicked.connect(self.closed.emit)
        top.addWidget(close)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(8)
        self._body.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def clear_history(self):
        db = self._get_db()
        if db and hasattr(db, 'clear_tool_usage'):
            db.clear_tool_usage()
            self.reload()

    def reload(self):
        while self._body.count() > 1:
            item = self._body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        db = self._get_db()
        if not db:
            return
            
        stats = db.get_tool_statistics()
        if not stats:
            empty = QLabel("No tools have been used yet.")
            empty.setObjectName("hint")
            empty.setAlignment(Qt.AlignCenter)
            self._body.insertWidget(0, empty)
            return

        for i, stat in enumerate(stats):
            self._body.insertWidget(i, ToolUsageRow(stat))
