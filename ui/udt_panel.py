"""Read-only prikaz efektivnega UDT konteksta (C4)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class UdtPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._context: Optional[Dict[str, Any]] = None
        self.summary_label = QLabel("Izbrani tag nima UDT konteksta.", self)
        self.summary_label.setObjectName("udtSummary")
        self.summary_label.setWordWrap(True)

        self.chain_list = QListWidget(self)
        self.chain_list.setObjectName("udtInheritanceChain")

        self.members_table = QTableWidget(0, 2, self)
        self.members_table.setObjectName("udtMembers")
        self.members_table.setHorizontalHeaderLabels(["Član", "Izvor"])
        self.members_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.parameters_table = QTableWidget(0, 2, self)
        self.parameters_table.setObjectName("udtParameters")
        self.parameters_table.setHorizontalHeaderLabels(["Parameter", "Efektivna vrednost"])
        self.parameters_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(QLabel("Dedovanje", self))
        layout.addWidget(self.chain_list)
        layout.addWidget(QLabel("Efektivni člani", self))
        layout.addWidget(self.members_table, 1)
        layout.addWidget(QLabel("Efektivni parametri", self))
        layout.addWidget(self.parameters_table, 1)

    @property
    def context(self) -> Optional[Dict[str, Any]]:
        return self._context

    def set_context(self, context: Optional[Dict[str, Any]]) -> None:
        self._context = context
        self.chain_list.clear()
        self.members_table.setRowCount(0)
        self.parameters_table.setRowCount(0)
        if context is None:
            self.summary_label.setText("Izbrani tag nima UDT konteksta.")
            return

        state = "najdena" if context["definition_found"] else "NI najdena"
        member_path = context.get("member_path")
        suffix = f"\nČlan: {member_path}" if member_path else ""
        self.summary_label.setText(
            f"typeId: {context['type_id']}\n"
            f"site: {context['site']}\n"
            f"definicija: {state}{suffix}"
        )
        self.chain_list.addItems(context["inheritance_chain"])

        direct = set(context["direct_members"])
        inherited = set(context["inherited_members"])
        local = set(context["local_members"])
        members = context["effective_members"]
        self.members_table.setRowCount(len(members))
        for row, member in enumerate(members):
            if member in local:
                origin = "lokalno/override"
            elif member in direct:
                origin = "definicija"
            elif member in inherited:
                origin = "podedovano"
            else:
                origin = "efektivno"
            self.members_table.setItem(row, 0, QTableWidgetItem(member))
            self.members_table.setItem(row, 1, QTableWidgetItem(origin))

        parameters = context["effective_parameters"]
        names = sorted(parameters)
        self.parameters_table.setRowCount(len(names))
        for row, name in enumerate(names):
            value = json.dumps(
                parameters[name],
                ensure_ascii=False,
                sort_keys=True,
            )
            self.parameters_table.setItem(row, 0, QTableWidgetItem(name))
            self.parameters_table.setItem(row, 1, QTableWidgetItem(value))
