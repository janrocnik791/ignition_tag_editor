"""Headless UI testi G3 simulacije, diff-a in validacije."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt

from editor import create_operation, create_project, import_source, operation_cursor
from ui.diff_panel import DiffPanel
from ui.main_window import MainWindow
from ui.sim_tree_view import SimTreeModel, SimTreeView
from ui.validation_panel import ValidationPanel, validate_simulation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")


@pytest.fixture()
def project(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="G3 UI")
    import_source(
        p, os.path.join(FIX, "tags_IO_TESTSITE_SIE.json"), site="testsite"
    )
    yield p
    p.close()


def _uid(project, path):
    row = project.conn.execute(
        "SELECT b.node_uid FROM baseline_nodes b JOIN sources s ON s.id=b.source_id "
        "WHERE s.provider_name='IO_TESTSITE_SIE' AND b.path_at_import=?",
        (path,),
    ).fetchone()
    return row["node_uid"]


def test_sim_tree_model_is_lazy_and_marks_changes(qtbot, project):
    area1 = _uid(project, "Area1")
    create_operation(
        project, "CREATE_FOLDER", "new:folder",
        {"parent_uid": area1, "name": "Generated", "tagType": "Folder", "props": {}},
        "operator",
    )
    view = SimTreeView(project)
    qtbot.addWidget(view)
    model = view.model
    root = model.index(0, 0)
    model.fetchMore(root)
    area = next(
        model.index(row, 0, root)
        for row in range(model.rowCount(root))
        if model.data(model.index(row, 0, root)) == "Area1"
    )
    model.fetchMore(area)
    assert any(
        model.data(model.index(row, 0, area)) == "Generated *"
        for row in range(model.rowCount(area))
    )


def test_diff_panel_undo_redo_updates_active_diff(qtbot, project):
    run = _uid(project, "Area1/Motor1_Run")
    create_operation(
        project, "RENAME_TAG", run, {"new_name": "RunState"}, "operator"
    )
    panel = DiffPanel(project)
    qtbot.addWidget(panel)
    assert panel.model.rowCount() == 1
    assert panel.cursor_label.text() == "Aktivne operacije: 1/1"
    qtbot.mouseClick(panel.undo_button, Qt.MouseButton.LeftButton)
    assert operation_cursor(project) == 0
    assert panel.model.rowCount() == 0
    qtbot.mouseClick(panel.redo_button, Qt.MouseButton.LeftButton)
    assert operation_cursor(project) == 1
    assert panel.model.rowCount() == 1


def test_validation_surfaces_conflicts(qtbot, project):
    run = _uid(project, "Area1/Motor1_Run")
    create_operation(project, "RENAME_TAG", run, {"new_name": "One"}, "one")
    create_operation(project, "RENAME_TAG", run, {"new_name": "Two"}, "two")
    findings = validate_simulation(project)
    assert [row["code"] for row in findings] == ["CONFLICT", "CONFLICT"]
    panel = ValidationPanel(project)
    qtbot.addWidget(panel)
    assert "Validacijske ugotovitve: 2" == panel.summary.text()
    assert "ERROR · CONFLICT" in panel.view.toPlainText()
    qtbot.mouseClick(
        panel.advanced_button, Qt.MouseButton.LeftButton
    )
    assert "Celovita validacija: INVALID" in panel.summary.text()
    assert "OPERATION_CONFLICT" in panel.view.toPlainText()


def test_main_window_exposes_and_refreshes_simulation(qtbot, tmp_path):
    p = create_project(str(tmp_path / "proj"), name="G3 main")
    import_source(
        p, os.path.join(FIX, "tags_IO_TESTSITE_SIE.json"), site="testsite"
    )
    db = p.db_path
    p.close()
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(db)
    assert window.sim_tree_view is not None
    assert window.diff_panel is not None
    assert window.validation_panel is not None
    search = window.search_panel
    search.query_edit.setText("Motor1_Run")
    search.execute_search(reset=True)
    search.results_view.setCurrentIndex(search.results_model.index(0, 0))
    editor = window.operation_editor
    editor.op_type_combo.setCurrentIndex(
        editor.op_type_combo.findData("RENAME_TAG")
    )
    editor.actor_edit.setText("operator")
    editor.name_edit.setText("RunState")
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)
    assert window.diff_panel.model.rowCount() == 1
