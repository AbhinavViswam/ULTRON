from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget, QPushButton
)
from ultron.ui import theme

class MemoryRow(QFrame):
    def __init__(self, memory: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("routineRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        
        title = QLabel(f"[{memory.get('category', 'None')}] {memory.get('key', 'None')}")
        title.setObjectName("routineName")
        layout.addWidget(title)

        value = QLabel(str(memory.get('value', '')))
        value.setObjectName("hint")
        value.setWordWrap(True)
        layout.addWidget(value)

        meta = QLabel(f"Importance: {memory.get('importance', 0)} · Saved: {memory.get('saved_at', '')[:19]}")
        meta.setObjectName("hint")
        layout.addWidget(meta)

class MemoriesPanel(QWidget):
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
        title = QLabel("MEMORIES")
        title.setObjectName("sectionHeader")
        top.addWidget(title, 1)

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

    def reload(self):
        while self._body.count() > 1:
            item = self._body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        db = self._get_db()
        if not db:
            return
            
        memories = db.list_memories()
        if not memories:
            empty = QLabel("No memories have been recorded yet.")
            empty.setObjectName("hint")
            empty.setAlignment(Qt.AlignCenter)
            self._body.insertWidget(0, empty)
            return

        for i, memory in enumerate(memories):
            self._body.insertWidget(i, MemoryRow(memory))
