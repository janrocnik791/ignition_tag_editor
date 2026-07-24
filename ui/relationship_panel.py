"""Read-only veriga exact relacij in njihovih dokazov (mejnik D2)."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from editor import Project, query_relationships

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_RELATIONSHIPS = 200
_QUERY_PAGE_SIZE = 500

_ROLE_LABELS = {
    "RAW_TO_ORGANIZED": "Raw IO → organiziran tag",
    "ORGANIZED_TO_MEMBER": "Organiziran tag → UDT član",
    "MEMBER_TO_UNS_INSTANCE": "UDT član → UNS instanca",
    "GENERIC": "Strukturna povezava",
}
_STATE_LABELS = {
    "EXACT": "EXACT",
    "UNRESOLVED": "NEREŠENO",
    "AMBIGUOUS": "DVOUMNO",
    "MANUAL_CONFIRMED": "ROČNO POTRJENO",
    "MANUAL_REJECTED": "ROČNO ZAVRNJENO",
    "STALE": "ZASTARELO",
    "CONFLICT": "KONFLIKT",
}
_ROLE_ORDER = {
    "RAW_TO_ORGANIZED": 0,
    "ORGANIZED_TO_MEMBER": 1,
    "GENERIC": 2,
    "MEMBER_TO_UNS_INSTANCE": 3,
}
_STATE_ORDER = {
    "UNRESOLVED": 0,
    "AMBIGUOUS": 1,
    "CONFLICT": 2,
    "STALE": 3,
    "EXACT": 4,
    "MANUAL_CONFIRMED": 5,
    "MANUAL_REJECTED": 6,
}


def _node_label(row: Dict[str, Any], prefix: str) -> str:
    path = row.get(f"{prefix}_path")
    provider = row.get(f"{prefix}_provider")
    site = row.get(f"{prefix}_site")
    if path is None:
        return ""
    context = "/".join(value for value in (site, provider) if value)
    return f"[{context}] {path}" if context else path


def _target_label(row: Dict[str, Any]) -> str:
    if row.get("target_node_uid"):
        return _node_label(row, "target")
    if row["state"] == "AMBIGUOUS":
        count = row.get("evidence", {}).get("candidate_count", 0)
        return f"— DVOUMNO: {count} kandidatov —"
    return "— NEREŠENO —"


def load_relationship_chain(
    project: Project,
    node_uid: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_relationships: int = DEFAULT_MAX_RELATIONSHIPS,
) -> Dict[str, Any]:
    """Nalozi omejeno povezano komponento relacij okrog izbranega taga.

    Obhod sledi samo ze shranjenim D1 robovom. Omejitvi preprečita, da bi en
    pogosto uporabljen UDT član v UI naložil tisoče instanc.
    """
    if max_depth < 0:
        raise ValueError("max_depth ne sme biti negativen")
    if max_relationships < 1:
        raise ValueError("max_relationships mora biti vsaj 1")

    queue = deque([(node_uid, 0)])
    visited_nodes = set()
    rows: Dict[str, Dict[str, Any]] = {}
    truncated = False

    while queue and len(rows) < max_relationships:
        current_uid, depth = queue.popleft()
        if current_uid in visited_nodes:
            continue
        visited_nodes.add(current_uid)
        page = query_relationships(
            project,
            node_uid=current_uid,
            limit=_QUERY_PAGE_SIZE,
        )
        if page["has_next"]:
            truncated = True

        for relation in page["results"]:
            relationship_uid = relation["relationship_uid"]
            if relationship_uid not in rows:
                if len(rows) >= max_relationships:
                    truncated = True
                    break
                rows[relationship_uid] = relation
            if depth >= max_depth:
                continue
            for related_uid in (
                relation.get("source_node_uid"),
                relation.get("target_node_uid"),
            ):
                if related_uid and related_uid not in visited_nodes:
                    queue.append((related_uid, depth + 1))
            if len(rows) >= max_relationships:
                break

    if queue:
        truncated = True

    ordered = sorted(
        rows.values(),
        key=lambda row: (
            _ROLE_ORDER.get(row["role"], 99),
            _STATE_ORDER.get(row["state"], 99),
            row.get("source_path") or "",
            row.get("target_path") or "",
            row["relationship_uid"],
        ),
    )
    counts: Dict[str, int] = {}
    for row in ordered:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "node_uid": node_uid,
        "results": ordered,
        "total": len(ordered),
        "by_state": dict(sorted(counts.items())),
        "truncated": truncated,
        "visited_nodes": len(visited_nodes),
    }


class RelationshipChainModel(QAbstractTableModel):
    COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("Stanje", "state"),
        ("Korak verige", "role"),
        ("Izvor", "source"),
        ("Cilj / vrzel", "target"),
        ("Dokaz", "evidence_type"),
    )
    RelationshipRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []
        self._load_result: Dict[str, Any] = {
            "node_uid": None,
            "results": [],
            "total": 0,
            "by_state": {},
            "truncated": False,
            "visited_nodes": 0,
        }

    @property
    def load_result(self) -> Dict[str, Any]:
        return self._load_result

    def load_node(
        self,
        project: Project,
        node_uid: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_relationships: int = DEFAULT_MAX_RELATIONSHIPS,
    ) -> Dict[str, Any]:
        result = load_relationship_chain(
            project,
            node_uid,
            max_depth=max_depth,
            max_relationships=max_relationships,
        )
        self.beginResetModel()
        self._rows = result["results"]
        self._load_result = result
        self.endResetModel()
        return result

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._load_result = {
            "node_uid": None,
            "results": [],
            "total": 0,
            "by_state": {},
            "truncated": False,
            "visited_nodes": 0,
        }
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        field = self.COLUMNS[index.column()][1]
        if role == Qt.ItemDataRole.DisplayRole:
            if field == "state":
                return _STATE_LABELS.get(row["state"], row["state"])
            if field == "role":
                return _ROLE_LABELS.get(row["role"], row["role"])
            if field == "source":
                return _node_label(row, "source")
            if field == "target":
                return _target_label(row)
            return row.get(field) or ""
        if role == Qt.ItemDataRole.ToolTipRole:
            return json.dumps(
                row.get("evidence") or {},
                ensure_ascii=False,
                sort_keys=True,
            )
        if role == Qt.ItemDataRole.ForegroundRole:
            if row["state"] == "UNRESOLVED":
                return QBrush(QColor("#b42318"))
            if row["state"] == "AMBIGUOUS":
                return QBrush(QColor("#b54708"))
        if role == self.RelationshipRole:
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


class RelationshipPanel(QWidget):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self._node_uid: Optional[str] = None

        self.title_label = QLabel("Izberi tag za pregled relacij.", self)
        self.title_label.setObjectName("relationshipTitle")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.summary_label = QLabel(
            "Relacije so read-only in uporabljajo shranjene D1 dokaze.",
            self,
        )
        self.summary_label.setObjectName("relationshipSummary")
        self.summary_label.setWordWrap(True)

        self.model = RelationshipChainModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("relationshipChain")
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.verticalHeader().setVisible(False)

        self.evidence_view = QPlainTextEdit(self)
        self.evidence_view.setObjectName("relationshipEvidence")
        self.evidence_view.setReadOnly(True)
        self.evidence_view.setPlaceholderText(
            "Izberi povezavo za prikaz dokaza."
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel("Dokaz izbrane povezave", self))
        layout.addWidget(self.evidence_view, 1)

        self.table.selectionModel().currentChanged.connect(
            self._relationship_selected
        )

    @property
    def node_uid(self) -> Optional[str]:
        return self._node_uid

    def set_node(self, node_uid: str, path: Optional[str] = None) -> None:
        self._node_uid = node_uid
        self.title_label.setText(path or node_uid)
        self.evidence_view.clear()
        result = self.model.load_node(self._project, node_uid)
        counts = result["by_state"]
        if result["total"] == 0:
            self.summary_label.setText(
                "Za izbrani tag ni shranjenih relacij. "
                "Exact discovery za ta projekt morda še ni bil zagnan."
            )
            return

        pieces = [
            f"Relacije v omejeni verigi: {result['total']}",
            f"EXACT: {counts.get('EXACT', 0)}",
            f"NEREŠENO: {counts.get('UNRESOLVED', 0)}",
            f"DVOUMNO: {counts.get('AMBIGUOUS', 0)}",
        ]
        if result["truncated"]:
            pieces.append(
                "Prikaz je omejen; pogosto uporabljena veja ima dodatne relacije."
            )
        self.summary_label.setText(" · ".join(pieces))
        first = self.model.index(0, 0)
        self.table.setCurrentIndex(first)
        self.table.resizeColumnsToContents()

    def _relationship_selected(
        self,
        current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        if not current.isValid():
            self.evidence_view.clear()
            return
        relation = self.model.data(
            self.model.index(current.row(), 0),
            self.model.RelationshipRole,
        )
        if not relation:
            self.evidence_view.clear()
            return
        payload = {
            "relationship_uid": relation["relationship_uid"],
            "state": relation["state"],
            "role": relation["role"],
            "evidence_type": relation["evidence_type"],
            "origin": relation["origin"],
            "confidence": relation["confidence"],
            "is_effective": relation.get("is_effective"),
            "removed": relation.get("removed", False),
            "manual_override_uid": relation.get("manual_override_uid"),
            "confirmed_by": relation.get("confirmed_by"),
            "confirmed_at": relation.get("confirmed_at"),
            "validity": relation.get("validity"),
            "evidence": relation["evidence"],
            "source_hashes": relation["source_hashes"],
        }
        self.evidence_view.setPlainText(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
