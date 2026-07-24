"""Testi minimalne C2 lupine in open-project toka."""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QPushButton, QSplitter, QTreeView

from editor import create_project, import_source
from ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
IO = os.path.join(FIX, "tags_IO_TESTSITE_SIE.json")


@pytest.fixture()
def project_path(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="Okenski test")
    import_source(project, IO, site="testsite")
    db_path = project.db_path
    project.close()
    return db_path


def test_initial_page_offers_project_open(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    buttons = window.findChildren(QPushButton)
    assert any("Odpri projekt" in button.text() for button in buttons)
    assert window.project is None
    assert window.tree_model is None


def test_open_project_shows_lazy_tree(qtbot, project_path):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.open_project_path(project_path) is True
    assert window.project is not None
    assert window.project.name == "Okenski test"
    assert window.tree_model is not None
    assert window.tree_model.rowCount() == 1
    assert isinstance(window.centralWidget(), QSplitter)
    assert isinstance(window.tree_view, QTreeView)
    assert window.search_panel is not None
    assert "Okenski test" in window.windowTitle()
