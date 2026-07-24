"""G2: trajni kazalec aktivnega prefiksa ter undo/redo."""

from __future__ import annotations

import os

import pytest

from editor import (
    OperationError,
    active_operations,
    create_operation,
    create_project,
    diff,
    import_source,
    list_operations,
    open_project,
    operation_cursor,
    redo,
    remove_operation,
    sim_details,
    undo,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="G2 undo")
    import_source(
        project,
        os.path.join(FIX, "tags_IO_TESTSITE_SIE.json"),
        site="testsite",
    )
    yield project
    project.close()


def _uid(project, path: str) -> str:
    row = project.conn.execute(
        "SELECT b.node_uid FROM baseline_nodes b "
        "JOIN sources s ON s.id = b.source_id "
        "WHERE s.provider_name = 'IO_TESTSITE_SIE' "
        "AND b.path_at_import = ?",
        (path,),
    ).fetchone()
    assert row is not None
    return row["node_uid"]


def test_undo_redo_changes_only_active_prefix(project):
    run = _uid(project, "Area1/Motor1_Run")
    create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunState"},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Changed"},
        "operator",
    )
    assert operation_cursor(project) == 2
    assert len(active_operations(project)) == 2

    assert undo(project) == 1
    details = sim_details(project, run)
    assert details["name"] == "RunState"
    assert "documentation" not in details["properties"]
    assert len(list_operations(project)) == 2

    assert undo(project, 10) == 0
    assert sim_details(project, run)["name"] == "Motor1_Run"
    assert diff(project)["total"] == 0
    assert redo(project, 10) == 2
    assert sim_details(project, run)["properties"]["documentation"] == (
        "Changed"
    )


def test_new_operation_after_undo_discards_redo_branch(project):
    run = _uid(project, "Area1/Motor1_Run")
    first = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunState"},
        "operator",
    )
    discarded = create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Discard me"},
        "operator",
    )
    undo(project)

    replacement = create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "tooltip", "new_value": "Replacement"},
        "operator",
    )

    assert operation_cursor(project) == 2
    assert [row["operation_uid"] for row in list_operations(project)] == [
        first["operation_uid"],
        replacement["operation_uid"],
    ]
    assert all(
        row["operation_uid"] != discarded["operation_uid"]
        for row in list_operations(project)
    )
    assert redo(project) == 2


def test_conflicts_follow_active_cursor(project):
    run = _uid(project, "Area1/Motor1_Run")
    first = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "One"},
        "one",
    )
    create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "Two"},
        "two",
    )
    assert {row["status"] for row in list_operations(project)} == {
        "CONFLICT"
    }
    assert sim_details(project, run)["name"] == "Motor1_Run"

    undo(project)

    rows = list_operations(project)
    assert rows[0]["operation_uid"] == first["operation_uid"]
    assert rows[0]["status"] == "VALID"
    assert sim_details(project, run)["name"] == "One"

    redo(project)
    assert {row["status"] for row in list_operations(project)} == {
        "CONFLICT"
    }
    assert sim_details(project, run)["name"] == "Motor1_Run"


def test_cursor_and_complete_project_state_persist_on_reopen(project):
    run = _uid(project, "Area1/Motor1_Run")
    create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunState"},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Later"},
        "operator",
    )
    undo(project)
    db_path = project.db_path
    baseline_count = project.conn.execute(
        "SELECT COUNT(*) FROM baseline_nodes"
    ).fetchone()[0]
    project.close()

    reopened = open_project(db_path)
    try:
        assert operation_cursor(reopened) == 1
        assert len(list_operations(reopened)) == 2
        assert reopened.conn.execute(
            "SELECT COUNT(*) FROM baseline_nodes"
        ).fetchone()[0] == baseline_count
        assert sim_details(reopened, run)["name"] == "RunState"
        assert redo(reopened) == 2
        assert sim_details(reopened, run)["properties"][
            "documentation"
        ] == "Later"
    finally:
        reopened.close()


def test_remove_updates_cursor_for_active_and_undone_rows(project):
    run = _uid(project, "Area1/Motor1_Run")
    first = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunState"},
        "operator",
    )
    second = create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Later"},
        "operator",
    )
    undo(project)
    remove_operation(project, second["operation_uid"])
    assert operation_cursor(project) == 1
    remove_operation(project, first["operation_uid"])
    assert operation_cursor(project) == 0
    assert list_operations(project) == []


def test_invalid_undo_redo_steps_are_rejected(project):
    for function in (undo, redo):
        with pytest.raises(OperationError, match="pozitivno"):
            function(project, 0)
        with pytest.raises(OperationError, match="pozitivno"):
            function(project, True)
