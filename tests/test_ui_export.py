"""Headless UI testi H2 izvoza in round-trip rezultata."""

from __future__ import annotations

import json
import os

import pytest
from PySide6.QtCore import Qt

from editor import (
    canonical_export_bytes,
    compute_export_scope,
    create_project,
    import_source,
    serialize_ignition_json,
)
from ui.export_panel import ExportPanel
from ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
SOURCE = os.path.join(FIX, "tags_IO_TESTSITE_SIE.json")


@pytest.fixture()
def project(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="H2 UI")
    import_source(p, SOURCE, site="testsite")
    yield p
    p.close()


def _uid(project, path):
    return project.conn.execute(
        "SELECT node_uid FROM baseline_nodes WHERE path_at_import=?",
        (path,),
    ).fetchone()["node_uid"]


def test_export_panel_previews_scope_and_writes_verified_package(
    qtbot, project, tmp_path
):
    panel = ExportPanel(project)
    qtbot.addWidget(panel)
    panel.set_node(_uid(project, "Area1"), "Area1")
    scope = json.loads(panel.preview.toPlainText())
    assert scope["node_count"] == 3
    panel.output_edit.setText(str(tmp_path / "package"))
    qtbot.mouseClick(panel.export_button, Qt.MouseButton.LeftButton)
    assert "EXPORT_VERIFIED" in panel.status_label.text()
    assert (tmp_path / "package" / "tags.json").exists()
    assert (tmp_path / "package" / "manifest.json").exists()


def test_export_panel_requires_output_directory(qtbot, project):
    panel = ExportPanel(project)
    qtbot.addWidget(panel)
    panel.set_node(_uid(project, "Area1"), "Area1")
    qtbot.mouseClick(panel.export_button, Qt.MouseButton.LeftButton)
    assert "Izberi ciljno mapo" in panel.status_label.text()


def test_export_panel_full_mode_and_ignition_reexport(
    qtbot, project, tmp_path
):
    panel = ExportPanel(project)
    qtbot.addWidget(panel)
    root = _uid(project, "")
    panel.set_node(root, "IO_TESTSITE_SIE")
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("full"))
    panel.output_edit.setText(str(tmp_path / "full"))
    qtbot.mouseClick(panel.export_button, Qt.MouseButton.LeftButton)
    assert "EXPORT_VERIFIED" in panel.status_label.text()
    assert len(list((tmp_path / "full").glob("tags_*.json"))) == 1

    payload = serialize_ignition_json(
        project, compute_export_scope(project, root)
    )
    reexport = tmp_path / "reexport.json"
    reexport.write_bytes(canonical_export_bytes(payload))
    panel.reexport_edit.setText(str(reexport))
    qtbot.mouseClick(panel.reexport_verify, Qt.MouseButton.LeftButton)
    assert "IGNITION_REEXPORT_VERIFIED" in panel.status_label.text()


def test_main_window_selection_updates_export_panel(qtbot, tmp_path):
    p = create_project(str(tmp_path / "proj"), name="H2 main")
    import_source(p, SOURCE, site="testsite")
    db = p.db_path
    p.close()
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(db)
    search = window.search_panel
    search.query_edit.setText("Area1")
    search.execute_search(reset=True)
    search.results_view.setCurrentIndex(search.results_model.index(0, 0))
    assert window.export_panel.node_uid is not None
    assert json.loads(window.export_panel.preview.toPlainText())[
        "selection_path"
    ] == "Area1"
