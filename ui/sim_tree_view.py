"""Lazy Qt pogled simuliranega G1 drevesa."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtWidgets import QPlainTextEdit, QSplitter, QTreeView, QVBoxLayout, QWidget

from editor import Project, list_providers, sim_children, sim_details


@dataclass
class _Item:
    data: Dict[str, Any]
    parent: Optional["_Item"]
    children: List["_Item"] = field(default_factory=list)
    exhausted: bool = False

    def row(self) -> int:
        return self.parent.children.index(self) if self.parent else 0


class SimTreeModel(QAbstractItemModel):
    NodeUidRole = Qt.ItemDataRole.UserRole + 1
    NodeDataRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, project: Project, parent=None, *, page_size: int = 200):
        super().__init__(parent)
        self.project = project
        self.page_size = page_size
        self.root = _Item({}, None, exhausted=True)
        self.reload()

    def reload(self) -> None:
        providers = {
            row["provider_uid"]: row for row in list_providers(self.project)
        }
        rows = sim_children(self.project, None, limit=500)["results"]
        self.beginResetModel()
        self.root.children = []
        for row in rows:
            row = dict(row)
            row.update(providers.get(row["provider_uid"], {}))
            self.root.children.append(
                _Item(row, self.root, exhausted=not row["has_children"])
            )
        self.endResetModel()

    def _item(self, index: QModelIndex) -> _Item:
        return index.internalPointer() if index.isValid() else self.root

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._item(parent).children)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 1

    def index(self, row, column, parent=QModelIndex()) -> QModelIndex:
        if column != 0 or not self.hasIndex(row, column, parent):
            return QModelIndex()
        return self.createIndex(row, column, self._item(parent).children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        item = self._item(index)
        parent = item.parent
        if parent is None or parent is self.root:
            return QModelIndex()
        return self.createIndex(parent.row(), 0, parent)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._item(index).data
        if role == Qt.ItemDataRole.DisplayRole:
            if self._item(index).parent is self.root:
                name = row.get("provider_name") or "Provider"
                return f"{name} ({row.get('site')})" if row.get("site") else name
            marker = " *" if row.get("has_operations") or row.get("is_new") else ""
            return (row.get("name") or "(brez imena)") + marker
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("effective_path")
        if role == self.NodeUidRole:
            return row.get("node_uid")
        if role == self.NodeDataRole:
            return dict(row)
        return None

    def hasChildren(self, parent=QModelIndex()) -> bool:
        item = self._item(parent)
        return bool(item.children) if item is self.root else bool(
            item.data.get("has_children")
        )

    def canFetchMore(self, parent: QModelIndex) -> bool:
        item = self._item(parent)
        return item is not self.root and not item.exhausted

    def fetchMore(self, parent: QModelIndex) -> None:
        item = self._item(parent)
        if not self.canFetchMore(parent):
            return
        result = sim_children(
            self.project,
            item.data["node_uid"],
            limit=self.page_size,
            offset=len(item.children),
        )
        rows = result["results"]
        if rows:
            first = len(item.children)
            self.beginInsertRows(parent, first, first + len(rows) - 1)
            item.children.extend(
                _Item(dict(row), item, exhausted=not row["has_children"])
                for row in rows
            )
            self.endInsertRows()
        item.exhausted = not result["has_next"]

    def flags(self, index):
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if index.isValid() else Qt.ItemFlag.NoItemFlags
        )


class SimTreeView(QWidget):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.model = SimTreeModel(project, self)
        self.tree = QTreeView(self)
        self.tree.setObjectName("simTree")
        self.tree.setModel(self.model)
        self.details = QPlainTextEdit(self)
        self.details.setObjectName("simDetails")
        self.details.setReadOnly(True)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.details)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self.tree.selectionModel().currentChanged.connect(self._selected)

    def refresh(self) -> None:
        self.details.clear()
        self.model.reload()

    def _selected(self, current, _previous) -> None:
        uid = self.model.data(current, self.model.NodeUidRole)
        if uid:
            self.details.setPlainText(json.dumps(
                sim_details(self.project, uid),
                ensure_ascii=False, indent=2, sort_keys=True,
            ))
