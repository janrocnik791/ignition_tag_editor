"""G1: lazy SimTree in strukturiran diff brez mutacije baseline-a."""

from __future__ import annotations

import hashlib
import os

import pytest

from editor import (
    SimulationError,
    create_operation,
    create_project,
    diff,
    import_source,
    sim_children,
    sim_details,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
C4_FIX = os.path.join(ROOT, "tests", "fixtures", "editor_c4", "site_a")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="G1 simulation")
    import_source(
        project,
        os.path.join(EDITOR_FIX, "tags_IO_TESTSITE_SIE.json"),
        site="testsite",
    )
    for filename in ("UDT_Definitions.json", "tags_UNS_SITEA.json"):
        import_source(project, os.path.join(C4_FIX, filename), site="site_a")
    yield project
    project.close()


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
    result = hashlib.sha256()
    for row in project.conn.execute(
        "SELECT * FROM baseline_nodes ORDER BY node_uid"
    ).fetchall():
        result.update(repr(tuple(row)).encode())
    return result.hexdigest()


def test_lazy_children_apply_create_move_and_pagination(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    area2 = _uid(project, "IO_TESTSITE_SIE", "Area2")
    speed = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Speed")
    folder = create_operation(
        project,
        "CREATE_FOLDER",
        "new:folder",
        {
            "parent_uid": area1,
            "name": "Generated",
            "tagType": "Folder",
            "props": {},
        },
        "operator",
    )
    create_operation(
        project,
        "CREATE_TAG",
        "new:tag",
        {
            "parent_uid": "new:folder",
            "name": "Temperature",
            "tagType": "AtomicTag",
            "props": {"dataType": "Float4"},
        },
        "operator",
        depends_on=[folder["operation_uid"]],
    )
    create_operation(
        project,
        "MOVE_TAG",
        speed,
        {"new_parent_uid": area2, "new_sibling_index": 1},
        "operator",
    )

    children = sim_children(project, area1, limit=1)
    assert children["total"] == 2
    assert children["has_next"] is True
    second = sim_children(project, area1, limit=1, offset=1)
    assert second["has_previous"] is True
    all_names = {
        row["name"]
        for row in sim_children(project, area1)["results"]
    }
    assert all_names == {"Motor1_Run", "Generated"}
    assert {
        row["name"]
        for row in sim_children(project, area2)["results"]
    } == {"Ref", "Motor1_Speed"}
    assert sim_children(project, "new:folder")["results"][0][
        "effective_path"
    ] == "Area1/Generated/Temperature"


def test_rename_updates_effective_descendant_paths(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    create_operation(
        project,
        "RENAME_TAG",
        area1,
        {"new_name": "Production"},
        "operator",
    )

    folder = sim_details(project, area1)
    child = sim_details(project, run)
    baseline_path = project.conn.execute(
        "SELECT path_at_import FROM baseline_nodes WHERE node_uid = ?",
        (run,),
    ).fetchone()["path_at_import"]

    assert folder["effective_path"] == "Production"
    assert child["effective_path"] == "Production/Motor1_Run"
    assert baseline_path == "Area1/Motor1_Run"


def test_details_apply_property_reference_and_parameter_changes(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    ref = _uid(project, "IO_TESTSITE_SIE", "Area2/Ref")
    line = _uid(project, "UNS_SITEA", "Line1")
    create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Simulated"},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_SOURCE_PATH",
        ref,
        {"new_value": "[~]Area1/Motor1_Run"},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_PARAMETERS",
        line,
        {
            "params": {
                "Area": {"dataType": "String", "value": "Utilities"}
            }
        },
        "operator",
    )

    assert sim_details(project, run)["properties"]["documentation"] == (
        "Simulated"
    )
    assert sim_details(project, ref)["source_tag_path"] == (
        "[~]Area1/Motor1_Run"
    )
    line_details = sim_details(project, line)
    assert line_details["properties"]["parameters"]["Area"]["value"] == (
        "Utilities"
    )
    assert line_details["operations"][0]["op_type"] == "UPDATE_PARAMETERS"


def test_structured_diff_has_all_executable_categories(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    area2 = _uid(project, "IO_TESTSITE_SIE", "Area2")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    speed = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Speed")
    ref = _uid(project, "IO_TESTSITE_SIE", "Area2/Ref")
    line = _uid(project, "UNS_SITEA", "Line1")
    create_operation(
        project,
        "CREATE_FOLDER",
        "new:folder",
        {
            "parent_uid": area1,
            "name": "New",
            "tagType": "Folder",
            "props": {},
        },
        "operator",
    )
    create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunState"},
        "operator",
    )
    create_operation(
        project,
        "MOVE_TAG",
        speed,
        {"new_parent_uid": area2, "new_sibling_index": 1},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Changed"},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_SOURCE_PATH",
        ref,
        {"new_value": "[~]Area1/RunState"},
        "operator",
    )
    create_operation(
        project,
        "UPDATE_PARAMETERS",
        line,
        {"params": {"Area": {"dataType": "String", "value": "Changed"}}},
        "operator",
    )

    result = diff(project)

    assert result["counts"] == {
        "added": 1,
        "renamed": 1,
        "moved": 1,
        "property_changed": 2,
        "reference_changed": 1,
        "deleted": 0,
    }
    assert result["total"] == 6
    assert result["categories"]["renamed"][0]["before"] == "Motor1_Run"
    assert result["categories"]["renamed"][0]["after"] == "RunState"
    assert result["categories"]["moved"][0]["after"]["path"] == (
        "Area2/Motor1_Speed"
    )


def test_conflicts_and_deferred_delete_are_skipped(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    create_operation(
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
    create_operation(project, "DELETE_TAG", run, {}, "three")

    result = diff(project)

    assert result["total"] == 0
    assert {item["status"] for item in result["skipped"]} == {
        "CONFLICT",
        "DEFERRED",
    }
    assert sim_details(project, run)["name"] == "Motor1_Run"


def test_simulation_reads_never_mutate_project(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    create_operation(
        project,
        "CREATE_FOLDER",
        "new:folder",
        {
            "parent_uid": area1,
            "name": "Generated",
            "tagType": "Folder",
            "props": {},
        },
        "operator",
    )
    before_digest = _digest(project)
    before_changes = project.conn.total_changes

    sim_children(project, area1)
    sim_details(project, "new:folder")
    diff(project)

    assert _digest(project) == before_digest
    assert project.conn.total_changes == before_changes


def test_simulation_validates_queries(project):
    with pytest.raises(SimulationError, match="Neznan"):
        sim_details(project, "missing")
    with pytest.raises(SimulationError, match="limit"):
        sim_children(project, None, limit=0)
    with pytest.raises(SimulationError, match="offset"):
        sim_children(project, None, offset=-1)
