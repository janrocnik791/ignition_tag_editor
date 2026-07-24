"""Headless UI testi F2 urejevalnika in stage-anega dnevnika."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from PySide6.QtCore import Qt

from editor import (
    create_operation,
    create_project,
    import_source,
    list_operations,
)
from ui.main_window import MainWindow
from ui.operation_editor import OperationEditor
from ui.staged_changes_panel import StagedChangesPanel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
C4_FIX = os.path.join(ROOT, "tests", "fixtures", "editor_c4", "site_a")


def _populate(path):
    project = create_project(str(path), name="F2 UI test")
    import_source(
        project,
        os.path.join(EDITOR_FIX, "tags_IO_TESTSITE_SIE.json"),
        site="testsite",
    )
    for filename in ("UDT_Definitions.json", "tags_UNS_SITEA.json"):
        import_source(project, os.path.join(C4_FIX, filename), site="site_a")
    return project


@pytest.fixture()
def project(tmp_path):
    project = _populate(tmp_path / "proj")
    yield project
    project.close()


@pytest.fixture()
def project_path(tmp_path):
    project = _populate(tmp_path / "proj")
    db_path = project.db_path
    project.close()
    return db_path


def _uid(project, provider: str, path: str) -> str:
    row = project.conn.execute(
        "SELECT b.node_uid FROM baseline_nodes b "
        "JOIN sources s ON s.id = b.source_id "
        "WHERE s.provider_name = ? AND b.path_at_import = ?",
        (provider, path),
    ).fetchone()
    assert row is not None
    return row["node_uid"]


def _digest(project) -> str:
    digest = hashlib.sha256()
    for row in project.conn.execute(
        "SELECT * FROM baseline_nodes ORDER BY node_uid"
    ).fetchall():
        digest.update(repr(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def _set_type(editor: OperationEditor, op_type: str) -> None:
    index = editor.op_type_combo.findData(op_type)
    assert index >= 0
    editor.op_type_combo.setCurrentIndex(index)


def test_editor_stages_rename_and_panel_shows_full_details(qtbot, project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    before = _digest(project)
    editor = OperationEditor(project)
    panel = StagedChangesPanel(project)
    qtbot.addWidget(editor)
    qtbot.addWidget(panel)
    editor.operationCreated.connect(
        lambda operation_uid: panel.refresh(select_uid=operation_uid)
    )
    editor.set_node(run, "Area1/Motor1_Run")
    _set_type(editor, "RENAME_TAG")
    editor.actor_edit.setText("operator")
    editor.name_edit.setText("RunFeedback")

    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)

    operations = list_operations(project)
    assert len(operations) == 1
    assert operations[0]["payload"] == {"new_name": "RunFeedback"}
    assert operations[0]["original"] == {"name": "Motor1_Run"}
    assert panel.model.rowCount() == 1
    assert "VALID: 1" in panel.summary_label.text()
    details = json.loads(panel.details.toPlainText())
    assert details["created_by"] == "operator"
    assert details["payload"]["new_name"] == "RunFeedback"
    assert panel.details.isReadOnly()
    assert _digest(project) == before


def test_editor_create_move_property_parameters_and_validation(
    qtbot,
    project,
):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    line = _uid(project, "UNS_SITEA", "Line1")
    editor = OperationEditor(project)
    qtbot.addWidget(editor)
    editor.actor_edit.setText("engineer")

    editor.set_node(area1, "Area1")
    _set_type(editor, "CREATE_FOLDER")
    editor.name_edit.setText("Generated")
    editor.props_edit.setPlainText("{}")
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)

    editor.set_node(run, "Area1/Motor1_Run")
    _set_type(editor, "MOVE_TAG")
    editor.destination_query.setText("Area2")
    editor.search_destinations()
    assert editor.destination_model.rowCount() == 1
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)

    _set_type(editor, "UPDATE_PROPERTY")
    editor.property_edit.setText("documentation")
    editor.value_edit.setText('"UI staged"')
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)

    editor.set_node(line, "Line1")
    _set_type(editor, "UPDATE_PARAMETERS")
    editor.parameters_edit.setPlainText(
        '{"Area":{"dataType":"String","value":"Utilities"}}'
    )
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)

    assert [row["op_type"] for row in list_operations(project)] == [
        "CREATE_FOLDER",
        "MOVE_TAG",
        "UPDATE_PROPERTY",
        "UPDATE_PARAMETERS",
    ]
    assert all(
        row["status"] == "VALID"
        for row in list_operations(project)
    )

    editor.value_edit.setText("not-json")
    _set_type(editor, "UPDATE_PROPERTY")
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)
    assert "ni veljaven JSON" in editor.status_label.text()
    assert len(list_operations(project)) == 4


def test_panel_surfaces_conflict_deferred_and_resolves_after_remove(
    qtbot,
    project,
):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    first = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "First"},
        "one",
    )
    second = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "Second"},
        "two",
    )
    create_operation(project, "DELETE_TAG", run, {}, "three")
    panel = StagedChangesPanel(project)
    qtbot.addWidget(panel)

    assert panel.model.rowCount() == 3
    assert "CONFLICT: 2" in panel.summary_label.text()
    assert "DEFERRED: 1" in panel.summary_label.text()
    conflict_row = next(
        row
        for row in range(panel.model.rowCount())
        if panel.model.data(panel.model.index(row, 1)) == "CONFLICT"
    )
    assert panel.model.data(
        panel.model.index(conflict_row, 1),
        Qt.ItemDataRole.ForegroundRole,
    ) is not None

    first_row = next(
        row
        for row in range(panel.model.rowCount())
        if panel.model.data(
            panel.model.index(row, 0),
            panel.model.OperationRole,
        )["operation_uid"] == first["operation_uid"]
    )
    panel.table.setCurrentIndex(panel.model.index(first_row, 0))
    qtbot.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)

    remaining = list_operations(project)
    renamed = next(
        row
        for row in remaining
        if row["operation_uid"] == second["operation_uid"]
    )
    assert renamed["status"] == "VALID"
    assert renamed["original"] == {"name": "Motor1_Run"}
    assert "VALID: 1" in panel.summary_label.text()


def test_panel_preserves_dependencies_during_reorder_and_remove(
    qtbot,
    project,
):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    folder = create_operation(
        project,
        "CREATE_FOLDER",
        "new:folder",
        {
            "parent_uid": area1,
            "name": "Folder",
            "tagType": "Folder",
            "props": {},
        },
        "operator",
    )
    child = create_operation(
        project,
        "CREATE_TAG",
        "new:child",
        {
            "parent_uid": "new:folder",
            "name": "Child",
            "tagType": "AtomicTag",
            "props": {},
        },
        "operator",
    )
    panel = StagedChangesPanel(project)
    qtbot.addWidget(panel)
    panel.table.setCurrentIndex(panel.model.index(1, 0))

    qtbot.mouseClick(panel.up_button, Qt.MouseButton.LeftButton)
    assert "pred njeno odvisnost" in panel.status_label.text()
    assert [row["operation_uid"] for row in list_operations(project)] == [
        folder["operation_uid"],
        child["operation_uid"],
    ]

    panel.table.setCurrentIndex(panel.model.index(0, 0))
    qtbot.mouseClick(panel.remove_button, Qt.MouseButton.LeftButton)
    assert "odvisne operacije" in panel.status_label.text()
    assert panel.model.rowCount() == 2


def test_main_window_selection_syncs_editor_and_refreshes_stage_panel(
    qtbot,
    project_path,
):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(project_path)
    search = window.search_panel
    search.query_edit.setText("Motor1_Run")
    search.execute_search(reset=True)
    index = search.results_model.index(0, 0)
    uid = search.results_model.data(
        index,
        Qt.ItemDataRole.UserRole,
    )["node_uid"]
    search.results_view.setCurrentIndex(index)

    editor = window.operation_editor
    assert editor.node_uid == uid
    _set_type(editor, "RENAME_TAG")
    editor.actor_edit.setText("operator")
    editor.name_edit.setText("RunFromMainWindow")
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)

    panel = window.staged_changes_panel
    assert panel.model.rowCount() == 1
    assert panel.selected_operation()["target_node_uid"] == uid
    assert "RunFromMainWindow" in panel.details.toPlainText()

    window.close()
    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert reopened.open_project_path(project_path)
    assert reopened.staged_changes_panel.model.rowCount() == 1
    assert "RunFromMainWindow" in (
        reopened.staged_changes_panel.details.toPlainText()
    )


def test_empty_actor_and_invalid_create_do_not_write(qtbot, project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    editor = OperationEditor(project)
    qtbot.addWidget(editor)
    editor.set_node(area1, "Area1")
    _set_type(editor, "CREATE_FOLDER")
    editor.name_edit.setText("NewFolder")
    before = project.conn.total_changes

    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)
    assert "Auditni uporabnik je obvezen" in editor.status_label.text()
    assert project.conn.total_changes == before

    editor.actor_edit.setText("operator")
    editor.name_edit.setText("bad/name")
    qtbot.mouseClick(editor.stage_button, Qt.MouseButton.LeftButton)
    assert "nedovoljen znak" in editor.status_label.text()
    assert list_operations(project) == []
