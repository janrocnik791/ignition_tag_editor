"""Pregled in urejanje vrstnega reda stage-anih operacij (mejnik F2)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from editor import (
    OperationError,
    Project,
    list_operations,
    remove_operation,
    reorder_operation,
)


class StagedChangesModel(QAbstractTableModel):
    COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("#", "seq"),
        ("Stanje", "status"),
        ("Operacija", "op_type"),
        ("Cilj", "target"),
        ("Payload", "payload"),
        ("Uporabnik", "created_by"),
    )
    OperationRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        operation = self._rows[index.row()]
        field = self.COLUMNS[index.column()][1]
        if role == Qt.ItemDataRole.DisplayRole:
            if field == "target":
                return operation["target_label"]
            if field == "payload":
                return json.dumps(
                    operation["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            return str(operation.get(field) or "")
        if role == Qt.ItemDataRole.ToolTipRole:
            return operation.get("reason") or operation["operation_uid"]
        if role == Qt.ItemDataRole.ForegroundRole:
            if operation["status"] == "CONFLICT":
                return QBrush(QColor("#b42318"))
            if operation["status"] == "DEFERRED":
                return QBrush(QColor("#b54708"))
        if role == self.OperationRole:
            return dict(operation)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section][0]
        return None


class StagedChangesPanel(QWidget):
    operationsChanged = Signal()

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self.summary_label = QLabel("Stage-ane spremembe: 0", self)
        self.summary_label.setObjectName("stagedSummary")
        self.summary_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.model = StagedChangesModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("stagedChanges")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)

        self.details = QPlainTextEdit(self)
        self.details.setObjectName("stagedOperationDetails")
        self.details.setReadOnly(True)
        self.details.setPlaceholderText(
            "Izberi operacijo za payload, original, odvisnosti in konflikt."
        )
        self.up_button = QPushButton("Premakni gor", self)
        self.up_button.setObjectName("operationUp")
        self.down_button = QPushButton("Premakni dol", self)
        self.down_button.setObjectName("operationDown")
        self.remove_button = QPushButton("Odstrani stage-an korak", self)
        self.remove_button.setObjectName("operationRemove")
        actions = QHBoxLayout()
        actions.addWidget(self.up_button)
        actions.addWidget(self.down_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        self.status_label = QLabel(
            "Spremembe so ločene od baseline-a.",
            self,
        )
        self.status_label.setObjectName("stagedStatus")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table, 2)
        layout.addLayout(actions)
        layout.addWidget(self.details, 1)
        layout.addWidget(self.status_label)

        self.table.selectionModel().currentChanged.connect(
            self._selection_changed
        )
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.remove_button.clicked.connect(self.remove_selected)
        self.refresh()

    def refresh(self, *, select_uid: Optional[str] = None) -> None:
        rows = list_operations(self._project)
        for operation in rows:
            target = self._project.conn.execute(
                "SELECT path_at_import FROM baseline_nodes "
                "WHERE node_uid = ?",
                (operation["target_node_uid"],),
            ).fetchone()
            operation["target_label"] = (
                target["path_at_import"]
                if target is not None
                else operation["payload"].get("name")
                or operation["target_node_uid"]
            )
        self.model.set_rows(rows)
        counts: Dict[str, int] = {}
        for operation in rows:
            counts[operation["status"]] = (
                counts.get(operation["status"], 0) + 1
            )
        parts = [f"Stage-ane spremembe: {len(rows)}"]
        parts.extend(
            f"{status}: {count}"
            for status, count in sorted(counts.items())
        )
        self.summary_label.setText(" · ".join(parts))
        if not rows:
            self.details.clear()
            self._update_buttons()
            return
        selected_row = 0
        if select_uid is not None:
            selected_row = next(
                (
                    row
                    for row, operation in enumerate(rows)
                    if operation["operation_uid"] == select_uid
                ),
                0,
            )
        self.table.setCurrentIndex(self.model.index(selected_row, 0))
        self.table.resizeColumnsToContents()
        self._update_buttons()

    def selected_operation(self) -> Optional[Dict[str, Any]]:
        current = self.table.currentIndex()
        if not current.isValid():
            return None
        return self.model.data(
            self.model.index(current.row(), 0),
            self.model.OperationRole,
        )

    def move_selected(self, offset: int) -> None:
        current = self.table.currentIndex()
        operation = self.selected_operation()
        if operation is None:
            self._show_error("Izberi operacijo za premik.")
            return
        new_index = current.row() + offset
        if not 0 <= new_index < self.model.rowCount():
            return
        try:
            reorder_operation(
                self._project,
                operation["operation_uid"],
                new_index,
            )
        except OperationError as exc:
            self._show_error(str(exc))
            return
        self.status_label.setStyleSheet("color: #067647;")
        self.status_label.setText("Vrstni red operacij je posodobljen.")
        self.refresh(select_uid=operation["operation_uid"])
        self.operationsChanged.emit()

    def remove_selected(self) -> None:
        operation = self.selected_operation()
        if operation is None:
            self._show_error("Izberi operacijo za odstranitev.")
            return
        try:
            remove_operation(
                self._project,
                operation["operation_uid"],
            )
        except OperationError as exc:
            self._show_error(str(exc))
            return
        self.status_label.setStyleSheet("color: #067647;")
        self.status_label.setText(
            "Stage-an korak je odstranjen; baseline ni bil spremenjen."
        )
        self.refresh()
        self.operationsChanged.emit()

    def _selection_changed(
        self,
        _current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        operation = self.selected_operation()
        if operation is None:
            self.details.clear()
        else:
            self.details.setPlainText(
                json.dumps(
                    operation,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        self._update_buttons()

    def _update_buttons(self) -> None:
        current = self.table.currentIndex()
        row = current.row() if current.isValid() else -1
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(
            0 <= row < self.model.rowCount() - 1
        )
        self.remove_button.setEnabled(row >= 0)

    def _show_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b42318;")
        self.status_label.setText(message)
