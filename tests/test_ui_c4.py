"""Headless UI testi C4 inspektorja in UDT panela."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt

from editor import create_project, import_source
from ui.inspector_panel import InspectorPanel
from ui.main_window import MainWindow
from ui.udt_panel import UdtPanel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor_c4", "site_a")


@pytest.fixture()
def project_path(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="C4 UI test")
    for filename in ("UDT_Definitions.json", "tags_UNS_SITEA.json"):
        import_source(project, os.path.join(FIX, filename), site="site_a")
    db_path = project.db_path
    project.close()
    return db_path


def test_read_only_panels_render_details(qtbot):
    inspector = InspectorPanel()
    udt = UdtPanel()
    qtbot.addWidget(inspector)
    qtbot.addWidget(udt)
    details = {
        "node_uid": "n1",
        "name": "Line1",
        "path_at_import": "Line1",
        "tag_type": "UdtInstance",
        "data_type": None,
        "value_source": None,
        "type_id": "MotorUDT",
        "opc_server": None,
        "opc_item_path": None,
        "source_tag_path": None,
        "properties": {"typeId": "MotorUDT"},
        "effective_properties": {"typeId": "MotorUDT"},
        "provider": {
            "site": "site_a",
            "provider_name": "UNS_SITEA",
            "kind": "uns",
            "path": "tags_UNS_SITEA.json",
            "sha256": "abc",
            "imported_at": "now",
        },
    }
    context = {
        "definition_found": True,
        "site": "site_a",
        "type_id": "MotorUDT",
        "member_path": "",
        "inheritance_chain": ["MotorUDT", "BaseMotor"],
        "direct_members": ["Speed"],
        "inherited_members": ["Run"],
        "local_members": [],
        "effective_members": ["Run", "Speed"],
        "effective_parameters": {"Area": {"value": "Packing"}},
    }

    inspector.set_details(details)
    udt.set_context(context)

    assert inspector.node_uid == "n1"
    assert inspector.raw_json.isReadOnly()
    assert inspector.effective_json.isReadOnly()
    assert inspector.value_labels["type_id"].text() == "MotorUDT"
    assert "UNS_SITEA" in inspector.provenance_label.text()
    assert udt.context == context
    assert udt.chain_list.count() == 2
    assert udt.members_table.rowCount() == 2
    assert udt.parameters_table.rowCount() == 1


def test_search_selection_updates_inspector_and_udt_context(qtbot, project_path):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(project_path)

    panel = window.search_panel
    panel.query_edit.setText("Line1")
    panel.execute_search(reset=True)
    uid = panel.results_model.data(
        panel.results_model.index(0, 0),
        Qt.ItemDataRole.UserRole,
    )["node_uid"]
    panel.results_view.setCurrentIndex(panel.results_model.index(0, 0))

    assert window.inspector_panel.node_uid == uid
    assert window.udt_panel.context["type_id"] == "MotorUDT"
    assert window.udt_panel.context["effective_members"] == [
        "Alarm",
        "Run",
        "Speed",
    ]


def test_tree_selection_updates_inspector(qtbot, project_path):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_project_path(project_path)
    model = window.tree_model

    provider = next(
        model.index(row, 0)
        for row in range(model.rowCount())
        if str(model.data(model.index(row, 0))).startswith("UNS_SITEA")
    )
    model.fetchMore(provider)
    line = model.index(0, 0, provider)
    window.tree_view.setCurrentIndex(line)

    assert model.data(line) == "Line1"
    assert window.inspector_panel.node_uid == model.data(
        line, model.NodeUidRole
    )
    assert window.udt_panel.context["selected_role"] == "instance"
