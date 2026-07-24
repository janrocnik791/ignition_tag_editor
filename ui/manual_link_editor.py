"""UI za eksplicitno urejanje rocnih relacij (mejnik E2)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    RELATIONSHIP_ROLES,
    RelationshipError,
    confirm_relationship,
    create_manual_relationship,
    query_relationships,
    reject_relationship,
    remove_manual_relationship,
    search_nodes,
)

_RELATION_DATA_ROLE = Qt.ItemDataRole.UserRole + 1

_ROLE_LABELS = {
    "RAW_TO_ORGANIZED": "Raw IO → organiziran tag",
    "ORGANIZED_TO_MEMBER": "Organiziran tag → UDT član",
    "MEMBER_TO_UNS_INSTANCE": "UDT član → UNS instanca",
    "GENERIC": "Splošna povezava",
}


class CandidateModel(QAbstractTableModel):
    COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("Pot", "path_at_import"),
        ("Provider", "provider_name"),
        ("Lokacija", "site"),
        ("Tip", "tag_type"),
    )
    CandidateRole = Qt.ItemDataRole.UserRole + 1

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
            return row.get("node_uid")
        if role == self.CandidateRole:
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


class ManualLinkEditor(QWidget):
    """Ureja E1 relacije brez spreminjanja baseline vozlisc."""

    relationshipsChanged = Signal()

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self._node_uid: Optional[str] = None
        self._node_path: Optional[str] = None

        self.node_label = QLabel("Izberi tag za urejanje povezav.", self)
        self.node_label.setObjectName("manualLinkNode")
        self.node_label.setStyleSheet("font-size: 18px; font-weight: 600;")

        self.actor_edit = QLineEdit(self)
        self.actor_edit.setObjectName("manualLinkActor")
        self.actor_edit.setPlaceholderText("Uporabnik ali identifikator revizorja")
        self.note_edit = QLineEdit(self)
        self.note_edit.setObjectName("manualLinkNote")
        self.note_edit.setPlaceholderText("Razlog ali referenca (neobvezno)")

        self.relationship_combo = QComboBox(self)
        self.relationship_combo.setObjectName("manualRelationship")
        self.relationship_combo.setMinimumContentsLength(45)

        self.role_combo = QComboBox(self)
        self.role_combo.setObjectName("manualLinkRole")
        for role in RELATIONSHIP_ROLES:
            self.role_combo.addItem(_ROLE_LABELS.get(role, role), role)
        self.direction_combo = QComboBox(self)
        self.direction_combo.setObjectName("manualLinkDirection")
        self.direction_combo.addItem("Izbrani tag → kandidat", "selected_source")
        self.direction_combo.addItem("Kandidat → izbrani tag", "selected_target")

        self.candidate_query = QLineEdit(self)
        self.candidate_query.setObjectName("manualCandidateQuery")
        self.candidate_query.setPlaceholderText("Del poti drugega taga …")
        self.candidate_button = QPushButton("Poišči kandidata", self)
        self.candidate_button.setObjectName("manualCandidateSearch")
        candidate_search = QHBoxLayout()
        candidate_search.addWidget(self.candidate_query, 1)
        candidate_search.addWidget(self.candidate_button)

        self.candidate_model = CandidateModel(self)
        self.candidate_view = QTableView(self)
        self.candidate_view.setObjectName("manualCandidates")
        self.candidate_view.setModel(self.candidate_model)
        self.candidate_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.candidate_view.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.candidate_view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.candidate_view.setAlternatingRowColors(True)
        self.candidate_view.verticalHeader().setVisible(False)

        self.create_button = QPushButton("Ustvari ročno povezavo", self)
        self.create_button.setObjectName("manualCreate")
        self.confirm_button = QPushButton("Potrdi izbrano relacijo", self)
        self.confirm_button.setObjectName("manualConfirm")
        self.reject_button = QPushButton("Zavrni izbrano relacijo", self)
        self.reject_button.setObjectName("manualReject")
        self.remove_button = QPushButton("Odstrani ročno odločitev", self)
        self.remove_button.setObjectName("manualRemove")
        actions = QHBoxLayout()
        actions.addWidget(self.create_button)
        actions.addWidget(self.confirm_button)
        actions.addWidget(self.reject_button)
        actions.addWidget(self.remove_button)

        self.status_label = QLabel(
            "Ročne odločitve se trajno zapišejo z auditno zgodovino.",
            self,
        )
        self.status_label.setObjectName("manualLinkStatus")
        self.status_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Auditni uporabnik", self.actor_edit)
        form.addRow("Opomba", self.note_edit)
        form.addRow("Obstoječa relacija", self.relationship_combo)
        form.addRow("Vloga nove povezave", self.role_combo)
        form.addRow("Smer nove povezave", self.direction_combo)

        layout = QVBoxLayout(self)
        layout.addWidget(self.node_label)
        layout.addLayout(form)
        layout.addLayout(candidate_search)
        layout.addWidget(self.candidate_view, 1)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)

        self.candidate_button.clicked.connect(self.search_candidates)
        self.candidate_query.returnPressed.connect(self.search_candidates)
        self.create_button.clicked.connect(self.create_link)
        self.confirm_button.clicked.connect(self.confirm_selected)
        self.reject_button.clicked.connect(self.reject_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self._set_actions_enabled(False)

    @property
    def node_uid(self) -> Optional[str]:
        return self._node_uid

    @property
    def node_path(self) -> Optional[str]:
        return self._node_path

    def set_node(self, node_uid: str, path: Optional[str] = None) -> None:
        self._node_uid = node_uid
        self._node_path = path
        self.node_label.setText(path or node_uid)
        self.candidate_model.set_rows([])
        self.candidate_view.clearSelection()
        self.refresh_relationships()
        self._set_actions_enabled(True)
        self.status_label.setText(
            "Izberi obstoječo relacijo ali poišči drug tag za novo povezavo."
        )

    def refresh_relationships(
        self,
        *,
        select_uid: Optional[str] = None,
    ) -> None:
        self.relationship_combo.clear()
        if self._node_uid is None:
            return
        result = query_relationships(
            self._project,
            node_uid=self._node_uid,
            limit=500,
        )
        for relation in result["results"]:
            source = relation.get("source_path") or relation["source_node_uid"]
            target = (
                relation.get("target_path")
                or relation.get("target_node_uid")
                or "— nerešeno —"
            )
            removed = " · odstranjeno" if relation.get("removed") else ""
            validity = relation.get("validity") or {}
            validity_label = (
                "veljavna" if validity.get("valid", True) else "neveljavna"
            )
            label = (
                f"{relation['state']} · {relation['role']} · "
                f"{source} → {target} · {validity_label}{removed}"
            )
            self.relationship_combo.addItem(
                label,
                relation["relationship_uid"],
            )
            index = self.relationship_combo.count() - 1
            self.relationship_combo.setItemData(
                index,
                relation,
                _RELATION_DATA_ROLE,
            )
            if relation["relationship_uid"] == select_uid:
                self.relationship_combo.setCurrentIndex(index)

    def search_candidates(self) -> None:
        if self._node_uid is None:
            self._show_error("Najprej izberi osnovni tag.")
            return
        query = self.candidate_query.text().strip()
        if not query:
            self._show_error("Vnesi del poti kandidata.")
            return
        result = search_nodes(
            self._project,
            "fullPath",
            query,
            mode="contains",
            limit=100,
        )
        rows = [
            row
            for row in result["results"]
            if row["node_uid"] != self._node_uid
        ]
        self.candidate_model.set_rows(rows)
        if rows:
            self.candidate_view.setCurrentIndex(
                self.candidate_model.index(0, 0)
            )
            self.candidate_view.resizeColumnsToContents()
        suffix = " (prvih 100)" if result["has_next"] else ""
        self.status_label.setText(
            f"Najdenih kandidatov: {len(rows)}{suffix}. "
            "Pred zapisom preveri izbrano vrstico."
        )

    def selected_candidate(self) -> Optional[Dict[str, Any]]:
        current = self.candidate_view.currentIndex()
        if not current.isValid():
            return None
        return self.candidate_model.data(
            self.candidate_model.index(current.row(), 0),
            self.candidate_model.CandidateRole,
        )

    def selected_relationship(self) -> Optional[Dict[str, Any]]:
        index = self.relationship_combo.currentIndex()
        if index < 0:
            return None
        value = self.relationship_combo.itemData(index, _RELATION_DATA_ROLE)
        return dict(value) if isinstance(value, dict) else None

    def create_link(self) -> None:
        if self._node_uid is None:
            self._show_error("Najprej izberi osnovni tag.")
            return
        candidate = self.selected_candidate()
        if candidate is None:
            self._show_error("Izberi kandidata za novo povezavo.")
            return
        if self.direction_combo.currentData() == "selected_source":
            source_uid = self._node_uid
            target_uid = candidate["node_uid"]
        else:
            source_uid = candidate["node_uid"]
            target_uid = self._node_uid
        self._run_action(
            lambda actor, note: create_manual_relationship(
                self._project,
                source_uid,
                target_uid,
                self.role_combo.currentData(),
                actor,
                note=note,
                evidence={
                    "ui": "manual_link_editor",
                    "selected_node_uid": self._node_uid,
                },
            ),
            "Ročna povezava je ustvarjena.",
        )

    def confirm_selected(self) -> None:
        relation = self.selected_relationship()
        if relation is None:
            self._show_error("Izberi relacijo za potrditev.")
            return
        candidate_uid = None
        if relation.get("target_node_uid") is None:
            candidate = self.selected_candidate()
            if candidate is None:
                self._show_error(
                    "Nerešena relacija zahteva izbranega kandidata."
                )
                return
            candidate_uid = candidate["node_uid"]
        self._run_action(
            lambda actor, note: confirm_relationship(
                self._project,
                relation["relationship_uid"],
                actor,
                candidate_node_uid=candidate_uid,
                note=note,
            ),
            "Relacija je ročno potrjena.",
        )

    def reject_selected(self) -> None:
        relation = self.selected_relationship()
        if relation is None:
            self._show_error("Izberi relacijo za zavrnitev.")
            return
        self._run_action(
            lambda actor, note: reject_relationship(
                self._project,
                relation["relationship_uid"],
                actor,
                note=note,
            ),
            "Relacija je ročno zavrnjena.",
        )

    def remove_selected(self) -> None:
        relation = self.selected_relationship()
        if relation is None:
            self._show_error("Izberi ročno odločitev za odstranitev.")
            return
        self._run_action(
            lambda actor, note: remove_manual_relationship(
                self._project,
                relation["relationship_uid"],
                actor,
                note=note,
            ),
            "Ročna odločitev je odstranjena; audit je ohranjen.",
        )

    def _run_action(self, action, success_message: str) -> None:
        actor = self.actor_edit.text().strip()
        if not actor:
            self._show_error("Auditni uporabnik je obvezen.")
            return
        note = self.note_edit.text().strip() or None
        try:
            relation = action(actor, note)
        except RelationshipError as exc:
            self._show_error(str(exc))
            return
        self.refresh_relationships(select_uid=relation["relationship_uid"])
        self.status_label.setStyleSheet("color: #067647;")
        self.status_label.setText(success_message)
        self.relationshipsChanged.emit()

    def _show_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b42318;")
        self.status_label.setText(message)

    def _set_actions_enabled(self, enabled: bool) -> None:
        for widget in (
            self.candidate_query,
            self.candidate_button,
            self.relationship_combo,
            self.role_combo,
            self.direction_combo,
            self.create_button,
            self.confirm_button,
            self.reject_button,
            self.remove_button,
        ):
            widget.setEnabled(enabled)
