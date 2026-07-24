"""Read-only prikaz uvozenih in efektivnih lastnosti taga (C4)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


class InspectorPanel(QWidget):
    FIELD_NAMES = (
        ("Pot", "path_at_import"),
        ("Tag type", "tag_type"),
        ("Data type", "data_type"),
        ("Value source", "value_source"),
        ("typeId", "type_id"),
        ("OPC server", "opc_server"),
        ("OPC Item Path", "opc_item_path"),
        ("Source Tag Path", "source_tag_path"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._node_uid: Optional[str] = None
        self.title_label = QLabel("Izberi tag za pregled.", self)
        self.title_label.setObjectName("inspectorTitle")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 600;")

        self.value_labels: Dict[str, QLabel] = {}
        form_widget = QWidget(self)
        form = QFormLayout(form_widget)
        for title, field in self.FIELD_NAMES:
            label = QLabel("—", form_widget)
            label.setTextInteractionFlags(
                label.textInteractionFlags()
                | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            label.setWordWrap(True)
            label.setObjectName(f"inspector_{field}")
            self.value_labels[field] = label
            form.addRow(title, label)

        self.provenance_label = QLabel("—", form_widget)
        self.provenance_label.setObjectName("inspectorProvenance")
        self.provenance_label.setWordWrap(True)
        self.provenance_label.setTextInteractionFlags(
            self.provenance_label.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("Provenance", self.provenance_label)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)

        self.raw_json = QPlainTextEdit(self)
        self.raw_json.setObjectName("rawProperties")
        self.raw_json.setReadOnly(True)
        self.raw_json.setPlaceholderText("Uvožene lastnosti")
        self.effective_json = QPlainTextEdit(self)
        self.effective_json.setObjectName("effectiveProperties")
        self.effective_json.setReadOnly(True)
        self.effective_json.setPlaceholderText("Efektivne lastnosti")

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(scroll)
        layout.addWidget(QLabel("Uvožene (raw) lastnosti", self))
        layout.addWidget(self.raw_json, 1)
        layout.addWidget(QLabel("Efektivne lastnosti", self))
        layout.addWidget(self.effective_json, 1)

    @property
    def node_uid(self) -> Optional[str]:
        return self._node_uid

    def set_details(self, details: Dict[str, Any]) -> None:
        self._node_uid = details["node_uid"]
        self.title_label.setText(details.get("name") or "(brez imena)")
        for _title, field in self.FIELD_NAMES:
            self.value_labels[field].setText(_display(details.get(field)))

        provider = details.get("provider") or {}
        provenance = [
            f"site={_display(provider.get('site'))}",
            f"provider={_display(provider.get('provider_name'))}",
            f"kind={_display(provider.get('kind'))}",
            f"source={_display(provider.get('path'))}",
            f"sha256={_display(provider.get('sha256'))}",
            f"imported_at={_display(provider.get('imported_at'))}",
        ]
        self.provenance_label.setText("\n".join(provenance))
        self.raw_json.setPlainText(
            json.dumps(
                details.get("properties") or {},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        self.effective_json.setPlainText(
            json.dumps(
                details.get("effective_properties") or {},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
