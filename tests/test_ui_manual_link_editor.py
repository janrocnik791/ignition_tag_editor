"""Headless UI testi E2 urejevalnika rocnih povezav."""

from __future__ import annotations

import json
import os

import pytest
from PySide6.QtCore import Qt

from editor import (
    create_project,
    discover_exact,
    import_source,
    open_project,
    query_relationships,
)
from ui.main_window import MainWindow
from ui.manual_link_editor import ManualLinkEditor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor_d1")


def _populate(path):
    project = create_project(str(path), name="E2 UI test")
    for filename in (
        "tags_IO_D1.json",
        "UDT_Definitions.json",
        "tags_UNS_D1.json",
    ):
        import_source(project, os.path.join(FIX, filename), site="d1")
    discover_exact(project)
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


def _select_candidate(editor: ManualLinkEditor, query: str) -> dict:
    editor.candidate_query.setText(query)
    editor.search_candidates()
    assert editor.candidate_model.rowCount() >= 1
    index = editor.candidate_model.index(0, 0)
    editor.candidate_view.setCurrentIndex(index)
    return editor.candidate_model.data(
        index,
        editor.candidate_model.CandidateRole,
    )


def _manual_rows(project):
    return [
        row
        for row in query_relationships(project, limit=500)["results"]
        if row["origin"] == "MANUAL"
    ]


def test_create_link_from_explicit_candidate_and_reopen(qtbot, project):
    raw = _uid(project, "IO_D1", "Raw/Temp")
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    editor = ManualLinkEditor(project)
    qtbot.addWidget(editor)
    editor.set_node(raw, "Raw/Temp")
    candidate = _select_candidate(editor, "Organized/FromRef")
    assert candidate["node_uid"] == organized
    editor.actor_edit.setText("operator")
    editor.note_edit.setText("UI verification")
    editor.role_combo.setCurrentIndex(
        editor.role_combo.findData("RAW_TO_ORGANIZED")
    )

    qtbot.mouseClick(editor.create_button, Qt.MouseButton.LeftButton)

    rows = _manual_rows(project)
    assert len(rows) == 1
    assert rows[0]["source_node_uid"] == raw
    assert rows[0]["target_node_uid"] == organized
    assert rows[0]["is_effective"] is True
    assert rows[0]["evidence"]["details"]["ui"] == "manual_link_editor"
    assert "ustvarjena" in editor.status_label.text()
    assert "veljavna" in editor.relationship_combo.currentText()

    db_path = project.db_path
    project.close()
    reopened = open_project(db_path)
    try:
        reopened_editor = ManualLinkEditor(reopened)
        qtbot.addWidget(reopened_editor)
        reopened_editor.set_node(raw, "Raw/Temp")
        assert reopened_editor.relationship_combo.count() >= 2
        assert any(
            reopened_editor.relationship_combo.itemData(index)
            == rows[0]["relationship_uid"]
            for index in range(reopened_editor.relationship_combo.count())
        )
    finally:
        reopened.close()


def test_confirm_unresolved_requires_explicit_candidate(qtbot, project):
    missing = _uid(project, "UNS_D1", "MissingOpc")
    raw = _uid(project, "IO_D1", "Raw/Temp")
    editor = ManualLinkEditor(project)
    qtbot.addWidget(editor)
    editor.set_node(missing, "MissingOpc")
    editor.actor_edit.setText("reviewer")

    qtbot.mouseClick(editor.confirm_button, Qt.MouseButton.LeftButton)
    assert "zahteva izbranega kandidata" in editor.status_label.text()
    assert _manual_rows(project) == []

    candidate = _select_candidate(editor, "Raw/Temp")
    assert candidate["node_uid"] == raw
    qtbot.mouseClick(editor.confirm_button, Qt.MouseButton.LeftButton)

    manual = _manual_rows(project)[0]
    assert manual["source_node_uid"] == raw
    assert manual["target_node_uid"] == missing
    assert manual["state"] == "MANUAL_CONFIRMED"
    assert "potrjena" in editor.status_label.text()


def test_reject_then_logically_remove_from_ui(qtbot, project):
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    editor = ManualLinkEditor(project)
    qtbot.addWidget(editor)
    editor.set_node(organized, "Organized/FromRef")
    editor.actor_edit.setText("reviewer")
    editor.note_edit.setText("Incorrect mapping")

    qtbot.mouseClick(editor.reject_button, Qt.MouseButton.LeftButton)

    manual = _manual_rows(project)[0]
    assert manual["state"] == "MANUAL_REJECTED"
    assert manual["is_effective"] is True
    assert editor.selected_relationship()["relationship_uid"] == manual[
        "relationship_uid"
    ]

    editor.actor_edit.setText("administrator")
    qtbot.mouseClick(editor.remove_button, Qt.MouseButton.LeftButton)

    removed = _manual_rows(project)[0]
    assert removed["removed"] is True
    assert removed["is_effective"] is False
    assert [
        event["action"] for event in removed["evidence"]["history"]
    ] == ["reject", "remove"]
    assert "audit je ohranjen" in editor.status_label.text()


def test_actor_and_candidate_validation_prevent_writes(qtbot, project):
    raw = _uid(project, "IO_D1", "Raw/Temp")
    editor = ManualLinkEditor(project)
    qtbot.addWidget(editor)
    editor.set_node(raw, "Raw/Temp")
    _select_candidate(editor, "Organized/FromRef")
    before = project.conn.total_changes

    qtbot.mouseClick(editor.create_button, Qt.MouseButton.LeftButton)

    assert "Auditni uporabnik je obvezen" in editor.status_label.text()
    assert project.conn.total_changes == before
    assert _manual_rows(project) == []


def test_main_window_synchronizes_editor_and_relationship_chain(
    qtbot,
    project_path,
):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(project_path)
    search = window.search_panel
    search.query_edit.setText("Raw/Temp")
    search.field_combo.setCurrentIndex(
        search.field_combo.findData("fullPath")
    )
    search.execute_search(reset=True)
    index = search.results_model.index(0, 0)
    raw = search.results_model.data(
        index,
        Qt.ItemDataRole.UserRole,
    )["node_uid"]
    search.results_view.setCurrentIndex(index)

    editor = window.manual_link_editor
    assert editor.node_uid == raw
    _select_candidate(editor, "Organized/FromRef")
    editor.actor_edit.setText("operator")
    editor.role_combo.setCurrentIndex(
        editor.role_combo.findData("RAW_TO_ORGANIZED")
    )
    qtbot.mouseClick(editor.create_button, Qt.MouseButton.LeftButton)

    assert window.relationship_panel.node_uid == raw
    manual_row = next(
        row
        for row, relation in enumerate(
            window.relationship_panel.model.load_result["results"]
        )
        if relation["origin"] == "MANUAL"
    )
    window.relationship_panel.table.setCurrentIndex(
        window.relationship_panel.model.index(manual_row, 0)
    )
    evidence = json.loads(
        window.relationship_panel.evidence_view.toPlainText()
    )
    assert evidence["origin"] == "MANUAL"
    assert evidence["confirmed_by"] == "operator"
    assert evidence["validity"]["valid"] is True
    assert evidence["evidence"]["history"][0]["action"] == "create"
