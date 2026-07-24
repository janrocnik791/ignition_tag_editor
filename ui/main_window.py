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
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from editor import Project, ProjectError, ProjectSchemaError, open_project

from .models.tree_model import TreeModel
from .search_panel import SearchPanel


class MainWindow(QMainWindow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._tree_model: Optional[TreeModel] = None
        self._tree_view: Optional[QTreeView] = None
        self._search_panel: Optional[SearchPanel] = None
        self.setWindowTitle("Ignition Tag Editor")
        self.resize(900, 650)
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

        tree = QTreeView(self)
        tree.setModel(self._tree_model)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(True)
        tree.setHeaderHidden(False)
        search_panel = SearchPanel(project, self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(tree)
        splitter.addWidget(search_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([320, 580])

        self._tree_view = tree
        self._search_panel = search_panel
        self.setCentralWidget(splitter)
        self.setWindowTitle(f"{project.name} — Ignition Tag Editor")
        self.statusBar().showMessage(os.path.abspath(project.db_path))

        if old_project is not None:
            old_project.close()
        return True

    def closeEvent(self, event) -> None:
        if self._project is not None:
            self._project.close()
            self._project = None
        super().closeEvent(event)
