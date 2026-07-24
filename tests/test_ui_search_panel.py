"""Headless testi C3 iskalnega panela."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt

from editor import create_project, import_source
from ui.search_panel import SearchPanel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="UI search test")
    for filename in (
        "tags_IO_TESTSITE_SIE.json",
        "tags_UNS_TESTSITE.json",
        "UDT_Definitions.json",
    ):
        import_source(project, os.path.join(FIX, filename), site="testsite")
    yield project
    project.close()


def test_search_panel_populates_filters_and_results(qtbot, project):
    panel = SearchPanel(project)
    qtbot.addWidget(panel)
    panel.query_edit.setText("Motor1_")

    qtbot.mouseClick(panel.search_button, Qt.MouseButton.LeftButton)

    assert panel.last_result["total"] == 2
    assert panel.results_model.rowCount() == 2
    assert panel.result_label.text() == "Zadetki: 2 (prikaz 1–2)"
    assert panel.site_combo.findData("testsite") >= 0
    assert panel.tag_type_combo.findData("AtomicTag") >= 0


def test_search_panel_applies_filter_and_zero_state(qtbot, project):
    panel = SearchPanel(project)
    qtbot.addWidget(panel)
    panel.query_edit.setText("Motor1")
    panel.tag_type_combo.setCurrentIndex(
        panel.tag_type_combo.findData("UdtInstance")
    )

    qtbot.mouseClick(panel.search_button, Qt.MouseButton.LeftButton)

    assert panel.last_result["total"] == 0
    assert panel.results_model.rowCount() == 0
    assert panel.result_label.text() == "Zadetki: 0"


def test_search_panel_pages_without_loading_all_results(qtbot, project):
    panel = SearchPanel(project, page_size=1)
    qtbot.addWidget(panel)
    panel.query_edit.setText("Motor")

    qtbot.mouseClick(panel.search_button, Qt.MouseButton.LeftButton)
    first_uid = panel.results_model.data(
        panel.results_model.index(0, 0),
        Qt.ItemDataRole.UserRole,
    )["node_uid"]
    assert panel.last_result["total"] == 3
    assert panel.results_model.rowCount() == 1
    assert panel.next_button.isEnabled()

    qtbot.mouseClick(panel.next_button, Qt.MouseButton.LeftButton)
    second_uid = panel.results_model.data(
        panel.results_model.index(0, 0),
        Qt.ItemDataRole.UserRole,
    )["node_uid"]
    assert panel.last_result["offset"] == 1
    assert panel.previous_button.isEnabled()
    assert first_uid != second_uid
