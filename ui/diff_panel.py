"""Before/after diff ter G2 undo/redo kontrole."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTableView, QVBoxLayout, QWidget,
)

from editor import Project, diff, list_operations, operation_cursor, redo, undo


class DiffModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: List[Dict[str, Any]] = []

    def set_result(self, result):
        self.beginResetModel()
        self.rows = [
            {"category": category, **item}
            for category, items in result["categories"].items() for item in items
        ]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                row["seq"], row["category"], row["op_type"], row["target_node_uid"]
            )[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return dict(row)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("#", "Kategorija", "Operacija", "Target")[section]
        return None


class DiffPanel(QWidget):
    stateChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.cursor_label = QLabel(self)
        self.undo_button = QPushButton("Undo", self)
        self.redo_button = QPushButton("Redo", self)
        bar = QHBoxLayout()
        bar.addWidget(self.cursor_label)
        bar.addStretch(1)
        bar.addWidget(self.undo_button)
        bar.addWidget(self.redo_button)
        self.model = DiffModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("simDiff")
        self.table.setModel(self.model)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.table, 2)
        layout.addWidget(self.details, 1)
        self.undo_button.clicked.connect(lambda: self._move(False))
        self.redo_button.clicked.connect(lambda: self._move(True))
        self.table.selectionModel().currentChanged.connect(self._selected)
        self.refresh()

    def refresh(self):
        result = diff(self.project)
        self.model.set_result(result)
        cursor = operation_cursor(self.project)
        total = len(list_operations(self.project))
        self.cursor_label.setText(f"Aktivne operacije: {cursor}/{total}")
        self.undo_button.setEnabled(cursor > 0)
        self.redo_button.setEnabled(cursor < total)
        self.details.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

    def _move(self, forward):
        redo(self.project) if forward else undo(self.project)
        self.refresh()
        self.stateChanged.emit()

    def _selected(self, current, _previous):
        if current.isValid():
            self.details.setPlainText(json.dumps(
                self.model.data(self.model.index(current.row(), 0), Qt.ItemDataRole.UserRole),
                ensure_ascii=False, indent=2, sort_keys=True,
            ))
