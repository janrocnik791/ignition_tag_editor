"""F1: trajni dnevnik, validacija, apply-in-sim in inverzi operacij."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from editor import (
    OPERATION_TYPES,
    OperationError,
    apply_operation_to_state,
    build_simulation_state,
    create_operation,
    create_project,
    import_source,
    invert_operation,
    list_operations,
    load_baseline_state,
    open_project,
    ordered_operations,
    reorder_operation,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
C4_FIX = os.path.join(ROOT, "tests", "fixtures", "editor_c4", "site_a")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="F1 operations")
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


def _baseline_digest(project) -> str:
    digest = hashlib.sha256()
    for row in project.conn.execute(
        "SELECT * FROM baseline_nodes ORDER BY node_uid"
    ).fetchall():
        digest.update(repr(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def _semantic_state(state):
    fields = (
        "node_uid",
        "provider_uid",
        "parent_uid",
        "sibling_index",
        "depth",
        "path_at_import",
        "name",
        "tag_type",
        "data_type",
        "value_source",
        "type_id",
        "opc_item_path",
        "opc_server",
        "source_tag_path",
        "source_id",
        "properties",
        "_baseline",
    )
    return {
        uid: {field: node.get(field) for field in fields}
        for uid, node in state.items()
    }


def test_schema_and_all_public_operation_types_are_declared(project):
    assert set(OPERATION_TYPES) == {
        "CREATE_TAG",
        "CREATE_FOLDER",
        "CREATE_UDT_INSTANCE",
        "RENAME_TAG",
        "MOVE_TAG",
        "UPDATE_PROPERTY",
        "UPDATE_SOURCE_PATH",
        "UPDATE_PARAMETERS",
        "DELETE_TAG",
    }
    columns = {
        row["name"]
        for row in project.conn.execute(
            "PRAGMA table_info(operations)"
        ).fetchall()
    }
    assert {
        "operation_uid",
        "seq",
        "op_type",
        "target_node_uid",
        "payload_json",
        "original_json",
        "status",
        "reason",
        "created_by",
        "created_at",
        "depends_on_json",
        "conflict_info",
    } == columns


def test_create_types_dependencies_order_and_effective_tree(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    uns_root = _uid(project, "UNS_SITEA", "")
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
    tag = create_operation(
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
    )
    instance = create_operation(
        project,
        "CREATE_UDT_INSTANCE",
        "new:instance",
        {
            "parent_uid": uns_root,
            "name": "Line2",
            "tagType": "UdtInstance",
            "props": {"typeId": "MotorUDT"},
        },
        "operator",
    )

    assert [row["operation_uid"] for row in ordered_operations(project)] == [
        folder["operation_uid"],
        tag["operation_uid"],
        instance["operation_uid"],
    ]
    assert tag["depends_on"] == [folder["operation_uid"]]
    state = build_simulation_state(project)
    assert state["new:folder"]["path_at_import"] == "Area1/Generated"
    assert state["new:tag"]["path_at_import"] == (
        "Area1/Generated/Temperature"
    )
    assert state["new:tag"]["data_type"] == "Float4"
    assert state["new:instance"]["path_at_import"] == "Line2"
    assert state["new:instance"]["type_id"] == "MotorUDT"

    with pytest.raises(OperationError, match="pred njeno odvisnost"):
        reorder_operation(project, tag["operation_uid"], 0)


def test_rename_move_and_descendant_paths(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    area2 = _uid(project, "IO_TESTSITE_SIE", "Area2")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    speed = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Speed")

    rename = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunFeedback"},
        "operator",
    )
    move = create_operation(
        project,
        "MOVE_TAG",
        speed,
        {"new_parent_uid": area2, "new_sibling_index": 1},
        "operator",
    )
    state = build_simulation_state(project)

    assert rename["original"] == {"name": "Motor1_Run"}
    assert state[run]["path_at_import"] == "Area1/RunFeedback"
    assert move["original"] == {
        "parent_uid": area1,
        "sibling_index": 1,
    }
    assert state[speed]["path_at_import"] == "Area2/Motor1_Speed"


def test_update_property_source_path_and_udt_parameters(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    ref = _uid(project, "IO_TESTSITE_SIE", "Area2/Ref")
    line = _uid(project, "UNS_SITEA", "Line1")
    create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"key": "documentation", "new_value": "Run feedback"},
        "engineer",
    )
    create_operation(
        project,
        "UPDATE_SOURCE_PATH",
        ref,
        {"new_value": "[IO_TESTSITE_SIE]Area1/Motor1_Run"},
        "engineer",
    )
    create_operation(
        project,
        "UPDATE_PARAMETERS",
        line,
        {
            "params": {
                "Area": {"dataType": "String", "value": "Utilities"},
                "SpeedScale": {"dataType": "Float4", "value": 3.0},
            }
        },
        "engineer",
    )

    state = build_simulation_state(project)
    assert state[run]["properties"]["documentation"] == "Run feedback"
    assert state[ref]["source_tag_path"] == (
        "[IO_TESTSITE_SIE]Area1/Motor1_Run"
    )
    assert state[line]["properties"]["parameters"]["Area"]["value"] == (
        "Utilities"
    )


def test_every_executable_operation_round_trips_through_inverse(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    area2 = _uid(project, "IO_TESTSITE_SIE", "Area2")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    speed = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Speed")
    ref = _uid(project, "IO_TESTSITE_SIE", "Area2/Ref")
    line = _uid(project, "UNS_SITEA", "Line1")
    operations = [
        create_operation(
            project,
            "CREATE_FOLDER",
            "new:roundtrip",
            {
                "parent_uid": area1,
                "name": "RoundTrip",
                "tagType": "Folder",
                "props": {},
            },
            "tester",
        ),
        create_operation(
            project,
            "RENAME_TAG",
            run,
            {"new_name": "RunState"},
            "tester",
        ),
        create_operation(
            project,
            "MOVE_TAG",
            speed,
            {"new_parent_uid": area2, "new_sibling_index": 1},
            "tester",
        ),
        create_operation(
            project,
            "UPDATE_PROPERTY",
            run,
            {"pointer": "/documentation", "new_value": "Changed"},
            "tester",
        ),
        create_operation(
            project,
            "UPDATE_SOURCE_PATH",
            ref,
            {"new_value": "[~]Area1/RunState"},
            "tester",
        ),
        create_operation(
            project,
            "UPDATE_PARAMETERS",
            line,
            {
                "params": {
                    "Area": {"dataType": "String", "value": "Changed"}
                }
            },
            "tester",
        ),
    ]
    baseline = load_baseline_state(project)
    simulated = build_simulation_state(project)

    for operation in reversed(operations):
        apply_operation_to_state(simulated, invert_operation(operation))

    assert _semantic_state(simulated) == _semantic_state(baseline)


@pytest.mark.parametrize(
    ("op_type", "target_path", "payload", "message"),
    (
        (
            "RENAME_TAG",
            "Area1/Motor1_Run",
            {"new_name": "bad/name"},
            "nedovoljen znak",
        ),
        (
            "RENAME_TAG",
            "Area1/Motor1_Run",
            {"new_name": "Motor1_Speed"},
            "ze obstaja",
        ),
        (
            "UPDATE_PROPERTY",
            "Area1/Motor1_Run",
            {"key": "unknownProperty", "new_value": 1},
            "Neznana lastnost",
        ),
        (
            "UPDATE_PROPERTY",
            "Area1/Motor1_Run",
            {"key": "enabled", "new_value": "yes"},
            "zahteva tip bool",
        ),
        (
            "UPDATE_PROPERTY",
            "Area2/Ref",
            {"key": "sourceTagPath", "new_value": "[~]Area1/Motor1_Run"},
            "UPDATE_SOURCE_PATH",
        ),
        (
            "UPDATE_SOURCE_PATH",
            "Area2/Ref",
            {"new_value": "[~]Area1/{Broken"},
            "neuravnotezene",
        ),
        (
            "UPDATE_SOURCE_PATH",
            "Area2/Ref",
            {"new_value": "Area1/Motor1_Run"},
            "provider",
        ),
    ),
)
def test_invalid_operations_are_rejected(
    project,
    op_type,
    target_path,
    payload,
    message,
):
    target = _uid(project, "IO_TESTSITE_SIE", target_path)
    with pytest.raises(OperationError, match=message):
        create_operation(
            project,
            op_type,
            target,
            payload,
            "tester",
        )
    assert list_operations(project) == []


def test_move_cycle_and_invalid_udt_parameters_are_rejected(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    line = _uid(project, "UNS_SITEA", "Line1")
    with pytest.raises(OperationError, match="lastnega potomca"):
        create_operation(
            project,
            "MOVE_TAG",
            area1,
            {"new_parent_uid": run, "new_sibling_index": 0},
            "tester",
        )
    with pytest.raises(OperationError, match="Nedeklarirani"):
        create_operation(
            project,
            "UPDATE_PARAMETERS",
            line,
            {"params": {"NotDeclared": {"value": 1}}},
            "tester",
        )
    with pytest.raises(OperationError, match="zahteva String"):
        create_operation(
            project,
            "UPDATE_PARAMETERS",
            line,
            {"params": {"Area": {"dataType": "String", "value": 5}}},
            "tester",
        )
    with pytest.raises(OperationError, match="samo za UdtInstance"):
        create_operation(
            project,
            "UPDATE_PARAMETERS",
            run,
            {"params": {}},
            "tester",
        )


def test_delete_is_persisted_as_deferred_and_not_applied(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    baseline = load_baseline_state(project)

    operation = create_operation(
        project,
        "DELETE_TAG",
        run,
        {},
        "operator",
    )

    assert operation["status"] == "DEFERRED"
    assert "odlozena" in operation["reason"]
    assert _semantic_state(build_simulation_state(project)) == (
        _semantic_state(baseline)
    )


def test_conflicting_renames_are_explicit_and_not_applied(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    first = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "FirstName"},
        "one",
    )
    second = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "SecondName"},
        "two",
    )
    third = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "ThirdName"},
        "three",
    )

    rows = list_operations(project)
    assert {row["status"] for row in rows} == {"CONFLICT"}
    assert rows[0]["conflict"] == rows[1]["conflict"]
    assert set(rows[0]["conflict"]["operation_uids"]) == {
        first["operation_uid"],
        second["operation_uid"],
        third["operation_uid"],
    }
    assert build_simulation_state(project)[run]["name"] == "Motor1_Run"


def test_operations_persist_reorder_and_never_mutate_baseline(project):
    area1 = _uid(project, "IO_TESTSITE_SIE", "Area1")
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    before = _baseline_digest(project)
    rename = create_operation(
        project,
        "RENAME_TAG",
        run,
        {"new_name": "RunState"},
        "tester",
    )
    folder = create_operation(
        project,
        "CREATE_FOLDER",
        "new:persistent",
        {
            "parent_uid": area1,
            "name": "Persistent",
            "tagType": "Folder",
            "props": {},
        },
        "tester",
    )
    reorder_operation(project, folder["operation_uid"], 0)
    assert _baseline_digest(project) == before
    db_path = project.db_path
    project.close()

    reopened = open_project(db_path)
    try:
        rows = list_operations(reopened)
        assert [row["operation_uid"] for row in rows] == [
            folder["operation_uid"],
            rename["operation_uid"],
        ]
        assert rows[1]["original"] == {"name": "Motor1_Run"}
        assert _baseline_digest(reopened) == before
        assert build_simulation_state(reopened)[run]["name"] == "RunState"
    finally:
        reopened.close()


def test_input_and_dependency_validation(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    with pytest.raises(OperationError, match="Neznan op_type"):
        create_operation(project, "GUESS", run, {}, "tester")
    with pytest.raises(OperationError, match="auditnega"):
        create_operation(
            project,
            "RENAME_TAG",
            run,
            {"new_name": "RunState"},
            " ",
        )
    with pytest.raises(OperationError, match="Neznane odvisnosti"):
        create_operation(
            project,
            "RENAME_TAG",
            run,
            {"new_name": "RunState"},
            "tester",
            depends_on=["missing"],
        )
    with pytest.raises(OperationError, match="payload"):
        create_operation(
            project,
            "RENAME_TAG",
            run,
            ["not", "object"],
            "tester",
        )


def test_json_storage_is_canonical_and_reader_facing_values_are_decoded(project):
    run = _uid(project, "IO_TESTSITE_SIE", "Area1/Motor1_Run")
    operation = create_operation(
        project,
        "UPDATE_PROPERTY",
        run,
        {"new_value": "abc", "key": "documentation"},
        "tester",
    )
    raw = project.conn.execute(
        "SELECT payload_json, depends_on_json FROM operations "
        "WHERE operation_uid = ?",
        (operation["operation_uid"],),
    ).fetchone()
    assert raw["payload_json"] == (
        '{"key":"documentation","new_value":"abc"}'
    )
    assert json.loads(raw["depends_on_json"]) == []
    assert operation["payload"] == {
        "key": "documentation",
        "new_value": "abc",
    }
