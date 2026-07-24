"""Napredna read-only validacija aktivne simulacije (mejnika G3/L)."""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from editor import Project, diff, sim_details, validate_project


def validate_simulation(project: Project) -> List[Dict[str, Any]]:
    result = diff(project)
    findings = []
    for item in result["skipped"]:
        findings.append({
            "severity": "ERROR" if item["status"] == "CONFLICT" else "WARNING",
            "code": item["status"],
            "target": item["operation_uid"],
            "message": item["reason"],
        })
    targets = {
        item["target_node_uid"]
        for items in result["categories"].values() for item in items
    }
    for uid in sorted(targets):
        details = sim_details(project, uid)
        if details.get("tag_type") == "UdtInstance" and not details.get("type_id"):
            findings.append({
                "severity": "WARNING", "code": "EMPTY_TYPE_ID",
                "target": uid, "message": "UDT instanca nima typeId.",
            })
    return findings


class ValidationPanel(QWidget):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.summary = QLabel(self)
        self.advanced_button = QPushButton(
            "Zaženi celovito validacijo", self
        )
        self.advanced_button.setObjectName("runAdvancedValidation")
        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.advanced_button)
        layout.addWidget(self.view)
        self.advanced_button.clicked.connect(self.run_advanced)
        self.refresh()

    def refresh(self):
        findings = validate_simulation(self.project)
        self.summary.setText(f"Validacijske ugotovitve: {len(findings)}")
        self.view.setPlainText("\n".join(
            f"{row['severity']} · {row['code']} · {row['target']} · {row['message']}"
            for row in findings
        ) or "Ni ugotovitev.")

    def run_advanced(self):
        result = validate_project(self.project)
        self.summary.setText(
            "Celovita validacija: "
            f"{result['status']} · {result['checked_nodes']} vozlišč · "
            f"{result['counts']['ERROR']} napak · "
            f"{result['counts']['WARNING']} opozoril · "
            f"{result['counts']['INFO']} informacij"
        )
        self.view.setPlainText("\n".join(
            f"{row['severity']} · {row['code']} · "
            f"{row['target'] or '-'} · {row['message']}"
            for row in result["findings"]
        ) or "Ni ugotovitev.")
