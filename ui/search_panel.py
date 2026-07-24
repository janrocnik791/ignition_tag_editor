"""Paginirano iskanje in filtri baseline vozlisc (mejnik C3)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from editor import (
    Project,
    SEARCH_FIELDS,
    SEARCH_MODES,
    get_search_filters,
    search_nodes,
)


class SearchResultsModel(QAbstractTableModel):
    COLUMNS = (
        ("Ime", "name"),
        ("Pot", "path_at_import"),
        ("Tip taga", "tag_type"),
        ("Provider", "provider_name"),
        ("Lokacija", "site"),
        ("typeId", "type_id"),
    )

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
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            value = row.get(self.COLUMNS[index.column()][1])
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("path_at_import")
        if role == Qt.ItemDataRole.UserRole:
            return dict(row)
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


class SearchPanel(QWidget):
    FIELD_LABELS = {
        "fullPath": "Pot",
        "name": "Ime",
        "opcItemPath": "OPC Item Path",
        "sourceTagPath": "Source Tag Path",
        "typeId": "typeId",
    }

    def __init__(self, project: Project, parent=None, *, page_size: int = 50) -> None:
        super().__init__(parent)
        self._project = project
        self._page_size = page_size
        self._offset = 0
        self._last_result: Optional[Dict[str, Any]] = None

        self.query_edit = QLineEdit(self)
        self.query_edit.setObjectName("searchQuery")
        self.query_edit.setPlaceholderText("Iskalna vrednost …")
        self.field_combo = QComboBox(self)
        self.field_combo.setObjectName("searchField")
        for field in SEARCH_FIELDS:
            self.field_combo.addItem(self.FIELD_LABELS.get(field, field), field)
        self.field_combo.setCurrentIndex(self.field_combo.findData("name"))

        self.mode_combo = QComboBox(self)
        self.mode_combo.setObjectName("searchMode")
        for mode in SEARCH_MODES:
            self.mode_combo.addItem(mode, mode)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("contains"))

        self.site_combo = QComboBox(self)
        self.site_combo.setObjectName("siteFilter")
        self.provider_combo = QComboBox(self)
        self.provider_combo.setObjectName("providerFilter")
        self.tag_type_combo = QComboBox(self)
        self.tag_type_combo.setObjectName("tagTypeFilter")
        self._populate_filters()

        self.search_button = QPushButton("Išči", self)
        self.search_button.setObjectName("searchButton")
        self.result_label = QLabel("Še brez iskanja.", self)
        self.result_label.setObjectName("searchResultCount")

        self.results_model = SearchResultsModel(self)
        self.results_view = QTableView(self)
        self.results_view.setObjectName("searchResults")
        self.results_view.setModel(self.results_model)
        self.results_view.setAlternatingRowColors(True)
        self.results_view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self.results_view.setSortingEnabled(False)

        self.previous_button = QPushButton("Prejšnja", self)
        self.previous_button.setObjectName("previousPage")
        self.next_button = QPushButton("Naslednja", self)
        self.next_button.setObjectName("nextPage")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)

        form = QFormLayout()
        form.addRow("Vrednost", self.query_edit)
        form.addRow("Polje", self.field_combo)
        form.addRow("Način", self.mode_combo)
        form.addRow("Lokacija", self.site_combo)
        form.addRow("Provider", self.provider_combo)
        form.addRow("Tip taga", self.tag_type_combo)

        pager = QHBoxLayout()
        pager.addWidget(self.previous_button)
        pager.addWidget(self.next_button)
        pager.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.search_button)
        layout.addWidget(self.result_label)
        layout.addWidget(self.results_view, 1)
        layout.addLayout(pager)

        self.search_button.clicked.connect(lambda: self.execute_search(reset=True))
        self.query_edit.returnPressed.connect(
            lambda: self.execute_search(reset=True)
        )
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        return self._last_result

    def _populate_filters(self) -> None:
        options = get_search_filters(self._project)
        self.site_combo.addItem("Vse lokacije", None)
        for site in options["sites"]:
            self.site_combo.addItem(site, site)

        self.provider_combo.addItem("Vsi providerji", None)
        for provider in options["providers"]:
            label = f"{provider['provider_name']} ({provider['site']})"
            self.provider_combo.addItem(label, provider["provider_uid"])

        self.tag_type_combo.addItem("Vsi tipi", None)
        for tag_type in options["tag_types"]:
            self.tag_type_combo.addItem(tag_type, tag_type)

    def execute_search(self, *, reset: bool = False) -> None:
        if reset:
            self._offset = 0
        result = search_nodes(
            self._project,
            self.field_combo.currentData(),
            self.query_edit.text(),
            mode=self.mode_combo.currentData(),
            provider_uid=self.provider_combo.currentData(),
            site=self.site_combo.currentData(),
            tag_type=self.tag_type_combo.currentData(),
            limit=self._page_size,
            offset=self._offset,
        )
        self._last_result = result
        self.results_model.set_rows(result["results"])
        self.previous_button.setEnabled(result["has_previous"])
        self.next_button.setEnabled(result["has_next"])

        if result["total"] == 0:
            self.result_label.setText("Zadetki: 0")
        else:
            first = result["offset"] + 1
            last = result["offset"] + len(result["results"])
            self.result_label.setText(
                f"Zadetki: {result['total']} (prikaz {first}–{last})"
            )

    def previous_page(self) -> None:
        if self._offset == 0:
            return
        self._offset = max(0, self._offset - self._page_size)
        self.execute_search()

    def next_page(self) -> None:
        if not self._last_result or not self._last_result["has_next"]:
            return
        self._offset += self._page_size
        self.execute_search()
