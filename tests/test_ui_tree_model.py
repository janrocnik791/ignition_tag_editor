"""Headless testi lazy Qt modela (mejnik C2)."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester

from editor import create_project, import_source
from ui.models.tree_model import TreeModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="Qt model test")
    for filename in (
        "tags_IO_TESTSITE_SIE.json",
        "tags_UNS_TESTSITE.json",
        "UDT_Definitions.json",
    ):
        import_source(project, os.path.join(FIX, filename), site="testsite")
    yield project
    project.close()


def _provider_index(model: TreeModel, prefix: str):
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        if str(model.data(index)).startswith(prefix):
            return index
    raise AssertionError(f"Ni providerja s predpono {prefix!r}")


def test_provider_roots_are_loaded_but_descendants_are_lazy(qtbot, project):
    model = TreeModel(project, page_size=1)
    assert model.rowCount(QModelIndex()) == 3

    io = _provider_index(model, "IO_TESTSITE_SIE")
    assert model.rowCount(io) == 0
    assert model.hasChildren(io) is True
    assert model.canFetchMore(io) is True


def test_fetch_more_uses_pages_and_preserves_order(qtbot, project):
    model = TreeModel(project, page_size=1)
    io = _provider_index(model, "IO_TESTSITE_SIE")

    model.fetchMore(io)
    assert model.rowCount(io) == 1
    assert model.data(model.index(0, 0, io)) == "Area1"
    assert model.canFetchMore(io) is True

    model.fetchMore(io)
    assert model.rowCount(io) == 2
    assert model.data(model.index(1, 0, io)) == "Area2"

    # Pri strani, ki je tocno polna, naslednji prazen fetch oznaci konec.
    model.fetchMore(io)
    assert model.canFetchMore(io) is False


def test_index_parent_roles_and_leaf_state(qtbot, project):
    model = TreeModel(project, page_size=20)
    io = _provider_index(model, "IO_TESTSITE_SIE")
    model.fetchMore(io)
    area1 = model.index(0, 0, io)
    assert model.parent(area1) == io
    assert model.data(area1, TreeModel.NodeUidRole)
    assert model.data(area1, TreeModel.NodeDataRole)["name"] == "Area1"

    model.fetchMore(area1)
    run = model.index(0, 0, area1)
    assert model.data(run, Qt.ItemDataRole.DisplayRole) == "Motor1_Run"
    assert model.hasChildren(run) is False
    assert model.canFetchMore(run) is False


def test_invalid_page_size_rejected(qtbot, project):
    with pytest.raises(ValueError):
        TreeModel(project, page_size=0)


def test_model_obeys_qt_model_contract(qtbot, project):
    model = TreeModel(project, page_size=1)
    tester = QAbstractItemModelTester(
        model,
        QAbstractItemModelTester.FailureReportingMode.Fatal,
    )
    io = _provider_index(model, "IO_TESTSITE_SIE")
    model.fetchMore(io)
    assert tester is not None
