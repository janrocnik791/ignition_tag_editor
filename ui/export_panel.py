"""Izbira obsega, zapis paketa in H2 round-trip rezultat."""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from editor import (
    ExportError,
    Project,
    compute_export_scope,
    verify_round_trip,
    write_package,
)


class ExportPanel(QWidget):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.node_uid: Optional[str] = None
        self.selection_label = QLabel("Izberi simulirano vejo za izvoz.", self)
        self.selection_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.output_edit = QLineEdit(self)
        self.output_edit.setObjectName("exportOutputDirectory")
        self.browse_button = QPushButton("Izberi mapo …", self)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.browse_button)
        self.preview = QPlainTextEdit(self)
        self.preview.setObjectName("exportScopePreview")
        self.preview.setReadOnly(True)
        self.export_button = QPushButton("Preveri round-trip in izvozi", self)
        self.export_button.setObjectName("exportPackage")
        self.export_button.setEnabled(False)
        self.status_label = QLabel(
            "Izvoz ne piše v Gateway; ustvari le lokalni JSON paket.",
            self,
        )
        self.status_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.selection_label)
        layout.addLayout(output_row)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.export_button)
        layout.addWidget(self.status_label)
        self.browse_button.clicked.connect(self.choose_directory)
        self.export_button.clicked.connect(self.export_package)

    def set_node(self, node_uid: str, path: Optional[str] = None) -> None:
        self.node_uid = node_uid
        self.selection_label.setText(path or node_uid)
        try:
            scope = compute_export_scope(self.project, node_uid)
        except ExportError as exc:
            self.preview.clear()
            self.export_button.setEnabled(False)
            self._error(str(exc))
            return
        self.preview.setPlainText(
            json.dumps(scope, ensure_ascii=False, indent=2, sort_keys=True)
        )
        self.export_button.setEnabled(True)
        self.status_label.setStyleSheet("")
        self.status_label.setText(
            f"Obseg vsebuje {scope['node_count']} vozlišč."
        )

    def choose_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Ciljna mapa izvoza", "")
        if path:
            self.output_edit.setText(path)

    def export_package(self) -> None:
        if self.node_uid is None:
            self._error("Najprej izberi vejo.")
            return
        output = self.output_edit.text().strip()
        if not output:
            self._error("Izberi ciljno mapo.")
            return
        try:
            verified = verify_round_trip(self.project, self.node_uid)
            if not verified["matches"]:
                raise ExportError("Round-trip primerjava se ne ujema")
            package = write_package(self.project, self.node_uid, output)
        except (ExportError, OSError) as exc:
            self._error(str(exc))
            return
        self.status_label.setStyleSheet("color: #067647;")
        self.status_label.setText(
            "EXPORT_VERIFIED · "
            f"{verified['actual_count']} vozlišč · {package['tags_path']}"
        )

    def _error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b42318;")
        self.status_label.setText(message)
