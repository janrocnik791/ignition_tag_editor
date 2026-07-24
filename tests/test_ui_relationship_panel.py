"""Headless UI/model testi D2 verige relacij."""

from __future__ import annotations

import json
import os

import pytest
from PySide6.QtCore import Qt

from editor import (
    create_project,
    discover_exact,
    import_source,
)
from ui.main_window import MainWindow
from ui.relationship_panel import (
    RelationshipChainModel,
    RelationshipPanel,
    load_relationship_chain,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor_d1")


def _populate(path):
    project = create_project(str(path), name="D2 UI test")
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


def test_chain_loader_connects_all_visible_pipeline_steps(project):
    motor = _uid(project, "UNS_D1", "Motor1")

    result = load_relationship_chain(project, motor)

    roles = {row["role"] for row in result["results"]}
    assert {
        "RAW_TO_ORGANIZED",
        "ORGANIZED_TO_MEMBER",
        "GENERIC",
        "MEMBER_TO_UNS_INSTANCE",
    } <= roles
    assert result["total"] > 3
    assert result["truncated"] is False
    assert result["by_state"] == {"EXACT": result["total"]}


def test_chain_loader_is_bounded(project):
    motor = _uid(project, "UNS_D1", "Motor1")

    result = load_relationship_chain(
        project,
        motor,
        max_relationships=2,
    )

    assert result["total"] == 2
    assert result["truncated"] is True


@pytest.mark.parametrize(
    ("path", "expected_state", "target_fragment"),
    (
        ("MissingOpc", "UNRESOLVED", "NEREŠENO"),
        ("AmbiguousOpc", "AMBIGUOUS", "2 kandidatov"),
    ),
)
def test_panel_keeps_gaps_visible(
    qtbot,
    project,
    path,
    expected_state,
    target_fragment,
):
    node_uid = _uid(project, "UNS_D1", path)
    panel = RelationshipPanel(project)
    qtbot.addWidget(panel)

    before = project.conn.total_changes
    panel.set_node(node_uid, path)

    assert panel.node_uid == node_uid
    assert project.conn.total_changes == before
    assert panel.evidence_view.isReadOnly()
    assert panel.model.rowCount() == 1
    relation = panel.model.data(
        panel.model.index(0, 0),
        panel.model.RelationshipRole,
    )
    assert relation["state"] == expected_state
    assert target_fragment in panel.model.data(panel.model.index(0, 3))
    summary_state = "DVOUMNO" if expected_state == "AMBIGUOUS" else "NEREŠENO"
    assert f"{summary_state}: 1" in panel.summary_label.text()
    evidence = json.loads(panel.evidence_view.toPlainText())
    assert evidence["state"] == expected_state
    assert evidence["evidence"]["candidate_count"] in (0, 2)


def test_model_exposes_read_only_rows_and_evidence(qtbot, project):
    motor = _uid(project, "UNS_D1", "Motor1")
    model = RelationshipChainModel()
    result = model.load_node(project, motor)
    first = model.index(0, 0)

    assert model.rowCount() == result["total"]
    assert model.columnCount() == 5
    assert model.headerData(
        4,
        Qt.Orientation.Horizontal,
        Qt.ItemDataRole.DisplayRole,
    ) == "Dokaz"
    assert not (model.flags(first) & Qt.ItemFlag.ItemIsEditable)
    relation = model.data(first, model.RelationshipRole)
    assert relation["evidence"]
    assert relation["source_hashes"]


def test_panel_explains_when_discovery_has_not_run(qtbot, tmp_path):
    project = create_project(str(tmp_path / "proj"), name="No discovery")
    import_source(
        project,
        os.path.join(FIX, "tags_UNS_D1.json"),
        site="d1",
    )
    node_uid = _uid(project, "UNS_D1", "MissingOpc")
    panel = RelationshipPanel(project)
    qtbot.addWidget(panel)

    panel.set_node(node_uid, "MissingOpc")

    assert panel.model.rowCount() == 0
    assert "ni shranjenih relacij" in panel.summary_label.text()
    project.close()


def test_main_window_search_selection_updates_relationship_panel(
    qtbot,
    project_path,
):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(project_path)
    search = window.search_panel
    search.query_edit.setText("MissingOpc")
    search.execute_search(reset=True)
    index = search.results_model.index(0, 0)
    node_uid = search.results_model.data(
        index,
        Qt.ItemDataRole.UserRole,
    )["node_uid"]

    search.results_view.setCurrentIndex(index)

    panel = window.relationship_panel
    assert panel.node_uid == node_uid
    assert panel.model.rowCount() == 1
    assert "NEREŠENO: 1" in panel.summary_label.text()
