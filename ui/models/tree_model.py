"""Lazy Qt model provider drevesa nad ``editor.repository``.

Koreni providerjev se nalozijo ob ustvarjanju modela. Njihovi potomci se berejo
stran po stran sele, ko jih ``QTreeView`` potrebuje. Model zato v pomnilniku nikoli
ne zgradi celotnega drevesa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt

from editor import Project, get_children, list_providers


@dataclass
class _TreeItem:
    data: Dict[str, Any]
    parent: Optional["_TreeItem"]
    children: List["_TreeItem"] = field(default_factory=list)
    exhausted: bool = False
    fetching: bool = False

    @property
    def node_uid(self) -> Optional[str]:
        return self.data.get("node_uid")

    @property
    def has_children(self) -> bool:
        return bool(self.data.get("has_children"))

    def row(self) -> int:
        if self.parent is None:
            return 0
        for row, child in enumerate(self.parent.children):
            if child is self:
                return row
        return 0


class TreeModel(QAbstractItemModel):
    """Enostolpcni, paginiran model baseline drevesa."""

    NodeUidRole = Qt.ItemDataRole.UserRole + 1
    NodeDataRole = Qt.ItemDataRole.UserRole + 2

    def __init__(
        self,
        project: Project,
        parent=None,
        *,
        page_size: int = 200,
    ) -> None:
        super().__init__(parent)
        if page_size < 1:
            raise ValueError("page_size mora biti vsaj 1")
        self._project = project
        self._page_size = page_size
        self._fetching = False
        self._root = _TreeItem({}, None, exhausted=True)
        self._load_provider_roots()

    @property
    def project(self) -> Project:
        return self._project

    @property
    def page_size(self) -> int:
        return self._page_size

    def _load_provider_roots(self) -> None:
        providers = {
            provider["provider_uid"]: provider
            for provider in list_providers(self._project)
        }
        for row in get_children(self._project, None):
            info = providers.get(row["provider_uid"], {})
            row = dict(row)
            row["provider_name"] = info.get("provider_name")
            row["site"] = info.get("site")
            row["kind"] = info.get("kind")
            self._root.children.append(
                _TreeItem(row, self._root, exhausted=not row["has_children"])
            )

    def _item(self, index: QModelIndex) -> _TreeItem:
        if index.isValid():
            item = index.internalPointer()
            if isinstance(item, _TreeItem):
                return item
        return self._root

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self._item(parent).children)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if row < 0 or column != 0 or not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_item = self._item(parent)
        return self.createIndex(row, column, parent_item.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        item = self._item(index)
        parent_item = item.parent
        if parent_item is None or parent_item is self._root:
            return QModelIndex()
        return self.createIndex(parent_item.row(), 0, parent_item)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._item(index)
        row = item.data
        if role == Qt.ItemDataRole.DisplayRole:
            if item.parent is self._root:
                provider = row.get("provider_name") or row.get("name") or "Provider"
                site = row.get("site")
                return f"{provider} ({site})" if site else provider
            return row.get("name") or "(brez imena)"
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("path_at_import") or row.get("provider_name")
        if role == self.NodeUidRole:
            return row.get("node_uid")
        if role == self.NodeDataRole:
            return dict(row)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            section == 0
            and orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return "Providerji in tagi"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        item = self._item(parent)
        if item is self._root:
            return bool(item.children)
        return item.has_children

    def canFetchMore(self, parent: QModelIndex) -> bool:
        item = self._item(parent)
        return (
            item is not self._root
            and item.has_children
            and not item.exhausted
            and not item.fetching
            and not self._fetching
        )

    def fetchMore(self, parent: QModelIndex) -> None:
        if not self.canFetchMore(parent):
            return
        item = self._item(parent)
        rows = get_children(
            self._project,
            item.node_uid,
            limit=self._page_size,
            offset=len(item.children),
        )
        if not rows:
            item.exhausted = True
            return

        first = len(item.children)
        last = first + len(rows) - 1
        item.fetching = True
        self._fetching = True
        try:
            self.beginInsertRows(parent, first, last)
            for row in rows:
                item.children.append(
                    _TreeItem(
                        dict(row),
                        item,
                        exhausted=not row["has_children"],
                    )
                )
            self.endInsertRows()
        finally:
            item.fetching = False
            self._fetching = False
        if len(rows) < self._page_size:
            item.exhausted = True
