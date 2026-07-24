"""Glavno okno: open-project zaslon in lazy provider drevo (C2)."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from editor import (
    Project,
    ProjectError,
    ProjectSchemaError,
    ProjectUdtContext,
    RepositoryError,
    node_details,
    open_project,
)

from .inspector_panel import InspectorPanel
from .manual_link_editor import ManualLinkEditor
from .models.tree_model import TreeModel
from .operation_editor import OperationEditor
from .relationship_panel import RelationshipPanel
from .search_panel import SearchPanel
from .staged_changes_panel import StagedChangesPanel
from .udt_panel import UdtPanel


class MainWindow(QMainWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._tree_model: Optional[TreeModel] = None
        self._tree_view: Optional[QTreeView] = None
        self._search_panel: Optional[SearchPanel] = None
        self._inspector_panel: Optional[InspectorPanel] = None
        self._udt_panel: Optional[UdtPanel] = None
        self._relationship_panel: Optional[RelationshipPanel] = None
        self._manual_link_editor: Optional[ManualLinkEditor] = None
        self._operation_editor: Optional[OperationEditor] = None
        self._staged_changes_panel: Optional[StagedChangesPanel] = None
        self._udt_context: Optional[ProjectUdtContext] = None
        self.setWindowTitle("Ignition Tag Editor")
        self.resize(1200, 720)
        self.show_open_project_page()

    @property
    def project(self) -> Optional[Project]:
        return self._project

    @property
    def tree_model(self) -> Optional[TreeModel]:
        return self._tree_model

    @property
    def tree_view(self) -> Optional[QTreeView]:
        return self._tree_view

    @property
    def search_panel(self) -> Optional[SearchPanel]:
        return self._search_panel

    @property
    def inspector_panel(self) -> Optional[InspectorPanel]:
        return self._inspector_panel

    @property
    def udt_panel(self) -> Optional[UdtPanel]:
        return self._udt_panel

    @property
    def relationship_panel(self) -> Optional[RelationshipPanel]:
        return self._relationship_panel

    @property
    def manual_link_editor(self) -> Optional[ManualLinkEditor]:
        return self._manual_link_editor

    @property
    def operation_editor(self) -> Optional[OperationEditor]:
        return self._operation_editor

    @property
    def staged_changes_panel(self) -> Optional[StagedChangesPanel]:
        return self._staged_changes_panel

    def show_open_project_page(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Ignition Tag Editor", page)
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(
            "Odprite mapo obstoječega delovnega projekta.",
            page,
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("Odpri projekt …", page)
        button.clicked.connect(self.choose_project)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(12)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(page)

    def choose_project(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Odpri Ignition Tag Editor projekt",
            "",
        )
        if path:
            self.open_project_path(path)

    def open_project_path(self, path: str) -> bool:
        try:
            project = open_project(path)
        except (ProjectError, ProjectSchemaError) as exc:
            QMessageBox.critical(self, "Projekta ni mogoče odpreti", str(exc))
            return False

        old_project = self._project
        self._project = project
        self._tree_model = TreeModel(project, self)
        self._udt_context = ProjectUdtContext(project)

        tree = QTreeView(self)
        tree.setModel(self._tree_model)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(True)
        tree.setHeaderHidden(False)
        search_panel = SearchPanel(project, self)
        inspector_panel = InspectorPanel(self)
        udt_panel = UdtPanel(self)
        relationship_panel = RelationshipPanel(project, self)
        manual_link_editor = ManualLinkEditor(project, self)
        operation_editor = OperationEditor(project, self)
        staged_changes_panel = StagedChangesPanel(project, self)
        operation_workspace = QSplitter(Qt.Orientation.Vertical, self)
        operation_workspace.addWidget(operation_editor)
        operation_workspace.addWidget(staged_changes_panel)
        operation_workspace.setStretchFactor(0, 1)
        operation_workspace.setStretchFactor(1, 1)
        details_tabs = QTabWidget(self)
        details_tabs.addTab(inspector_panel, "Inspektor")
        details_tabs.addTab(udt_panel, "UDT kontekst")
        details_tabs.addTab(relationship_panel, "Relacije")
        details_tabs.addTab(manual_link_editor, "Ročne povezave")
        details_tabs.addTab(operation_workspace, "Stage-ane spremembe")
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(tree)
        splitter.addWidget(search_panel)
        splitter.addWidget(details_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([280, 420, 500])

        self._tree_view = tree
        self._search_panel = search_panel
        self._inspector_panel = inspector_panel
        self._udt_panel = udt_panel
        self._relationship_panel = relationship_panel
        self._manual_link_editor = manual_link_editor
        self._operation_editor = operation_editor
        self._staged_changes_panel = staged_changes_panel
        self.setCentralWidget(splitter)
        self.setWindowTitle(f"{project.name} — Ignition Tag Editor")
        self.statusBar().showMessage(os.path.abspath(project.db_path))
        tree.selectionModel().currentChanged.connect(
            self._tree_selection_changed
        )
        search_panel.nodeSelected.connect(self._show_node)
        manual_link_editor.relationshipsChanged.connect(
            self._refresh_relationship_views
        )
        operation_editor.operationCreated.connect(
            lambda operation_uid: staged_changes_panel.refresh(
                select_uid=operation_uid
            )
        )

        if old_project is not None:
            old_project.close()
        return True

    def _tree_selection_changed(self, current, _previous) -> None:
        if not current.isValid() or self._tree_model is None:
            return
        node_uid = self._tree_model.data(
            current, self._tree_model.NodeUidRole
        )
        if node_uid:
            self._show_node(node_uid)

    def _show_node(self, node_uid: str) -> None:
        if (
            self._project is None
            or self._udt_context is None
            or self._inspector_panel is None
            or self._udt_panel is None
            or self._relationship_panel is None
            or self._manual_link_editor is None
            or self._operation_editor is None
        ):
            return
        try:
            details = node_details(
                self._project,
                node_uid,
                udt_context=self._udt_context,
            )
        except RepositoryError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._inspector_panel.set_details(details)
        self._udt_panel.set_context(details["udt_context"])
        self._relationship_panel.set_node(
            node_uid,
            details.get("path_at_import"),
        )
        self._manual_link_editor.set_node(
            node_uid,
            details.get("path_at_import"),
        )
        self._operation_editor.set_node(
            node_uid,
            details.get("path_at_import"),
        )

    def _refresh_relationship_views(self) -> None:
        if (
            self._relationship_panel is None
            or self._manual_link_editor is None
            or self._manual_link_editor.node_uid is None
        ):
            return
        node_uid = self._manual_link_editor.node_uid
        self._relationship_panel.set_node(
            node_uid,
            self._manual_link_editor.node_path,
        )

    def closeEvent(self, event) -> None:
        if self._project is not None:
            self._project.close()
            self._project = None
            self._udt_context = None
        super().closeEvent(event)
