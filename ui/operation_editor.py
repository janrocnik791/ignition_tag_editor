"""Urejevalnik za ustvarjanje validiranih F1 operacij (mejnik F2)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from editor import (
    OPERATION_TYPES,
    OperationError,
    Project,
    create_operation,
    search_nodes,
)

from .manual_link_editor import CandidateModel

_OPERATION_LABELS = {
    "CREATE_TAG": "Ustvari tag",
    "CREATE_FOLDER": "Ustvari mapo",
    "CREATE_UDT_INSTANCE": "Ustvari UDT instanco",
    "RENAME_TAG": "Preimenuj tag",
    "MOVE_TAG": "Premakni tag",
    "UPDATE_PROPERTY": "Posodobi lastnost",
    "UPDATE_SOURCE_PATH": "Posodobi sourceTagPath",
    "UPDATE_PARAMETERS": "Posodobi UDT parametre",
    "DELETE_TAG": "Izbriši tag (odloženo)",
}


class OperationEditor(QWidget):
    operationCreated = Signal(str)

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self._node_uid: Optional[str] = None
        self._node_path: Optional[str] = None

        self.node_label = QLabel("Izberi tag za pripravo operacije.", self)
        self.node_label.setObjectName("operationNode")
        self.node_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.op_type_combo = QComboBox(self)
        self.op_type_combo.setObjectName("operationType")
        for op_type in OPERATION_TYPES:
            self.op_type_combo.addItem(
                _OPERATION_LABELS.get(op_type, op_type),
                op_type,
            )
        self.actor_edit = QLineEdit(self)
        self.actor_edit.setObjectName("operationActor")
        self.actor_edit.setPlaceholderText("Auditni uporabnik")

        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("operationName")
        self.name_edit.setPlaceholderText("Novo ime")
        self.tag_type_combo = QComboBox(self)
        self.tag_type_combo.setObjectName("operationTagType")
        for tag_type in ("AtomicTag", "MemoryTag"):
            self.tag_type_combo.addItem(tag_type, tag_type)
        self.props_edit = QPlainTextEdit(self)
        self.props_edit.setObjectName("operationProps")
        self.props_edit.setMaximumHeight(90)

        self.property_edit = QLineEdit(self)
        self.property_edit.setObjectName("operationProperty")
        self.property_edit.setPlaceholderText("key ali /JSON/pointer")
        self.value_edit = QLineEdit(self)
        self.value_edit.setObjectName("operationValue")
        self.value_edit.setPlaceholderText(
            'JSON vrednost, npr. true, 12.5 ali "tekst"'
        )
        self.source_path_edit = QLineEdit(self)
        self.source_path_edit.setObjectName("operationSourcePath")
        self.source_path_edit.setPlaceholderText("[provider]pot/do/taga")
        self.parameters_edit = QPlainTextEdit(self)
        self.parameters_edit.setObjectName("operationParameters")
        self.parameters_edit.setMaximumHeight(110)
        self.parameters_edit.setPlaceholderText(
            '{"Parameter":{"dataType":"String","value":"vrednost"}}'
        )

        self.destination_query = QLineEdit(self)
        self.destination_query.setObjectName("operationDestinationQuery")
        self.destination_query.setPlaceholderText("Del poti ciljnega starša …")
        self.destination_button = QPushButton("Poišči cilj", self)
        self.destination_button.setObjectName("operationDestinationSearch")
        destination_search = QHBoxLayout()
        destination_search.addWidget(self.destination_query, 1)
        destination_search.addWidget(self.destination_button)
        self.destination_container = QWidget(self)
        self.destination_container.setLayout(destination_search)

        self.destination_model = CandidateModel(self)
        self.destination_view = QTableView(self)
        self.destination_view.setObjectName("operationDestinations")
        self.destination_view.setModel(self.destination_model)
        self.destination_view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self.destination_view.setSelectionMode(
            QTableView.SelectionMode.SingleSelection
        )
        self.destination_view.setEditTriggers(
            QTableView.EditTrigger.NoEditTriggers
        )
        self.destination_view.setMaximumHeight(130)
        self.sibling_spin = QSpinBox(self)
        self.sibling_spin.setObjectName("operationSiblingIndex")
        self.sibling_spin.setRange(0, 1_000_000)

        self.preview = QPlainTextEdit(self)
        self.preview.setObjectName("operationPayloadPreview")
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(100)
        self.stage_button = QPushButton("Dodaj med stage-ane spremembe", self)
        self.stage_button.setObjectName("stageOperation")
        self.status_label = QLabel(
            "Baseline se ne spremeni; zapiše se samo operacija.",
            self,
        )
        self.status_label.setObjectName("operationStatus")
        self.status_label.setWordWrap(True)

        self.form = QFormLayout()
        self.form.addRow("Vrsta operacije", self.op_type_combo)
        self.form.addRow("Auditni uporabnik", self.actor_edit)
        self.form.addRow("Ime", self.name_edit)
        self.form.addRow("Tip taga", self.tag_type_combo)
        self.form.addRow("Lastnosti (JSON)", self.props_edit)
        self.form.addRow("Lastnost / pointer", self.property_edit)
        self.form.addRow("Nova vrednost (JSON)", self.value_edit)
        self.form.addRow("sourceTagPath", self.source_path_edit)
        self.form.addRow("Parametri (JSON)", self.parameters_edit)
        self.form.addRow("Iskanje ciljnega starša", self.destination_container)
        self.form.addRow("Ciljni starš", self.destination_view)
        self.form.addRow("Indeks med sorojenci", self.sibling_spin)
        self.form.addRow("Payload predogled", self.preview)

        layout = QVBoxLayout(self)
        layout.addWidget(self.node_label)
        layout.addLayout(self.form)
        layout.addWidget(self.stage_button)
        layout.addWidget(self.status_label)

        self.op_type_combo.currentIndexChanged.connect(
            self._operation_type_changed
        )
        self.destination_button.clicked.connect(self.search_destinations)
        self.destination_query.returnPressed.connect(
            self.search_destinations
        )
        self.stage_button.clicked.connect(self.stage_operation)
        self._set_enabled(False)
        self._operation_type_changed()

    @property
    def node_uid(self) -> Optional[str]:
        return self._node_uid

    def set_node(self, node_uid: str, path: Optional[str] = None) -> None:
        self._node_uid = node_uid
        self._node_path = path
        self.node_label.setText(path or node_uid)
        self.destination_model.set_rows([])
        self.destination_view.clearSelection()
        self._set_enabled(True)
        self.status_label.setStyleSheet("")
        self.status_label.setText(
            "Baseline se ne spremeni; zapiše se samo operacija."
        )
        self._operation_type_changed()

    def search_destinations(self) -> None:
        query = self.destination_query.text().strip()
        if not query:
            self._show_error("Vnesi del poti ciljnega starša.")
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
            if row["tag_type"] in (
                "Provider",
                "Folder",
                "UdtType",
                "UdtInstance",
            )
        ]
        self.destination_model.set_rows(rows)
        if rows:
            self.destination_view.setCurrentIndex(
                self.destination_model.index(0, 0)
            )
            self.destination_view.resizeColumnsToContents()
        self.status_label.setText(
            f"Najdenih ciljnih staršev: {len(rows)}. "
            "Pred stage-anjem preveri izbrano vrstico."
        )

    def selected_destination(self) -> Optional[Dict[str, Any]]:
        current = self.destination_view.currentIndex()
        if not current.isValid():
            return None
        return self.destination_model.data(
            self.destination_model.index(current.row(), 0),
            self.destination_model.CandidateRole,
        )

    def build_request(self) -> Dict[str, Any]:
        if self._node_uid is None:
            raise OperationError("Najprej izberi tag")
        op_type = self.op_type_combo.currentData()
        target_uid: Optional[str] = self._node_uid
        if op_type == "CREATE_TAG":
            target_uid = None
            payload = {
                "parent_uid": self._node_uid,
                "name": self.name_edit.text(),
                "tagType": self.tag_type_combo.currentData(),
                "props": self._parse_object(
                    self.props_edit.toPlainText(), "Lastnosti"
                ),
            }
        elif op_type == "CREATE_FOLDER":
            target_uid = None
            payload = {
                "parent_uid": self._node_uid,
                "name": self.name_edit.text(),
                "tagType": "Folder",
                "props": self._parse_object(
                    self.props_edit.toPlainText(), "Lastnosti"
                ),
            }
        elif op_type == "CREATE_UDT_INSTANCE":
            target_uid = None
            payload = {
                "parent_uid": self._node_uid,
                "name": self.name_edit.text(),
                "tagType": "UdtInstance",
                "props": self._parse_object(
                    self.props_edit.toPlainText(), "Lastnosti"
                ),
            }
        elif op_type == "RENAME_TAG":
            payload = {"new_name": self.name_edit.text()}
        elif op_type == "MOVE_TAG":
            destination = self.selected_destination()
            if destination is None:
                raise OperationError("Izberi ciljnega starša")
            payload = {
                "new_parent_uid": destination["node_uid"],
                "new_sibling_index": self.sibling_spin.value(),
            }
        elif op_type == "UPDATE_PROPERTY":
            key = self.property_edit.text().strip()
            selector = (
                {"pointer": key} if key.startswith("/") else {"key": key}
            )
            payload = {
                **selector,
                "new_value": self._parse_json(
                    self.value_edit.text(), "Nova vrednost"
                ),
            }
        elif op_type == "UPDATE_SOURCE_PATH":
            payload = {"new_value": self.source_path_edit.text()}
        elif op_type == "UPDATE_PARAMETERS":
            payload = {
                "params": self._parse_object(
                    self.parameters_edit.toPlainText(), "Parametri"
                )
            }
        else:
            payload = {}
        return {
            "op_type": op_type,
            "target_node_uid": target_uid,
            "payload": payload,
        }

    def stage_operation(self) -> None:
        actor = self.actor_edit.text().strip()
        if not actor:
            self._show_error("Auditni uporabnik je obvezen.")
            return
        try:
            request = self.build_request()
            self.preview.setPlainText(
                json.dumps(
                    request["payload"],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            operation = create_operation(
                self._project,
                request["op_type"],
                request["target_node_uid"],
                request["payload"],
                actor,
            )
        except OperationError as exc:
            self._show_error(str(exc))
            return
        self.status_label.setStyleSheet("color: #067647;")
        self.status_label.setText(
            f"Operacija #{operation['seq']} je stage-ana "
            f"({operation['status']})."
        )
        self.operationCreated.emit(operation["operation_uid"])

    def _operation_type_changed(self) -> None:
        op_type = self.op_type_combo.currentData()
        create = op_type in (
            "CREATE_TAG",
            "CREATE_FOLDER",
            "CREATE_UDT_INSTANCE",
        )
        self.form.setRowVisible(
            self.name_edit,
            create or op_type == "RENAME_TAG",
        )
        self.form.setRowVisible(
            self.tag_type_combo,
            op_type == "CREATE_TAG",
        )
        self.form.setRowVisible(self.props_edit, create)
        property_update = op_type == "UPDATE_PROPERTY"
        self.form.setRowVisible(self.property_edit, property_update)
        self.form.setRowVisible(self.value_edit, property_update)
        self.form.setRowVisible(
            self.source_path_edit,
            op_type == "UPDATE_SOURCE_PATH",
        )
        self.form.setRowVisible(
            self.parameters_edit,
            op_type == "UPDATE_PARAMETERS",
        )
        move = op_type == "MOVE_TAG"
        self.form.setRowVisible(self.destination_container, move)
        self.form.setRowVisible(self.destination_view, move)
        self.form.setRowVisible(self.sibling_spin, move)
        if create and not self.props_edit.toPlainText().strip():
            self.props_edit.setPlainText(
                '{"typeId":"MotorUDT"}'
                if op_type == "CREATE_UDT_INSTANCE"
                else "{}"
            )
        try:
            request = self.build_request()
        except OperationError:
            self.preview.clear()
        else:
            self.preview.setPlainText(
                json.dumps(
                    request["payload"],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )

    @staticmethod
    def _parse_json(raw: str, label: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OperationError(
                f"{label} ni veljaven JSON: {exc.msg}"
            ) from exc

    @classmethod
    def _parse_object(cls, raw: str, label: str) -> Dict[str, Any]:
        value = cls._parse_json(raw or "{}", label)
        if not isinstance(value, dict):
            raise OperationError(f"{label} mora biti JSON objekt")
        return value

    def _show_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b42318;")
        self.status_label.setText(message)

    def _set_enabled(self, enabled: bool) -> None:
        self.stage_button.setEnabled(enabled)
        self.op_type_combo.setEnabled(enabled)
        self.actor_edit.setEnabled(enabled)
