"""Checkpoint L validation, full export, Ignition re-export, and packaging."""

from __future__ import annotations

import json
import os

import pytest

from editor import (
    ExportError,
    canonical_export_bytes,
    compute_export_scope,
    create_operation,
    create_project,
    import_source,
    serialize_ignition_json,
    validate_project,
    verify_ignition_reexport,
    write_production_package,
)
from ui.app import build_parser, main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")


def _uid(project, provider, path):
    row = project.conn.execute(
        "SELECT b.node_uid FROM baseline_nodes b JOIN sources s ON s.id=b.source_id "
        "WHERE s.provider_name=? AND b.path_at_import=?",
        (provider, path),
    ).fetchone()
    assert row
    return row["node_uid"]


@pytest.fixture()
def project(tmp_path):
    opened = create_project(str(tmp_path / "project"), name="Production")
    for name in (
        "tags_IO_D1.json",
        "tags_UNS_D1.json",
        "UDT_Definitions.json",
    ):
        import_source(
            opened,
            os.path.join(FIX, "editor_d1", name),
            site="factory",
        )
    yield opened
    opened.close()


def test_advanced_validation_blocks_conflicting_production_export(
    project, tmp_path
):
    target = _uid(project, "IO_D1", "Raw/Temp")
    create_operation(
        project, "RENAME_TAG", target, {"new_name": "One"}, "one"
    )
    create_operation(
        project, "RENAME_TAG", target, {"new_name": "Two"}, "two"
    )
    validation = validate_project(project)
    assert validation["status"] == "INVALID"
    assert validation["counts"]["ERROR"] == 2
    assert {
        row["code"] for row in validation["findings"]
    } == {"OPERATION_CONFLICT"}
    with pytest.raises(ExportError, match="validacijskih napak"):
        write_production_package(project, str(tmp_path / "blocked"))
    assert not (tmp_path / "blocked").exists()


def test_full_production_export_writes_every_provider_without_overwrite(
    project, tmp_path
):
    output = tmp_path / "full"
    result = write_production_package(project, str(output))
    assert result["mode"] == "full"
    assert len(result["files"]) == 3
    assert all(os.path.isfile(path) for path in result["files"])
    manifest = json.load(open(result["manifest_path"], encoding="utf-8"))
    assert manifest["mode"] == "full"
    assert manifest["validation"]["status"] == "VALID"
    assert {
        row["round_trip_status"] for row in manifest["exports"]
    } == {"EXPORT_VERIFIED"}
    with pytest.raises(ExportError, match="ne prepisuje"):
        write_production_package(project, str(output))


def test_supplied_ignition_reexport_reports_exact_differences(
    project, tmp_path
):
    selection = _uid(project, "IO_D1", "Organized")
    scope = compute_export_scope(project, selection)
    payload = serialize_ignition_json(project, scope)
    path = tmp_path / "ignition-reexport.json"
    path.write_bytes(canonical_export_bytes(payload))
    verified = verify_ignition_reexport(project, selection, str(path))
    assert verified["status"] == "IGNITION_REEXPORT_VERIFIED"
    assert verified["matches"] is True

    payload["tags"][0]["tags"][0]["documentation"] = "Changed in Gateway"
    path.write_bytes(canonical_export_bytes(payload))
    mismatch = verify_ignition_reexport(project, selection, str(path))
    assert mismatch["status"] == "IGNITION_REEXPORT_MISMATCH"
    assert mismatch["changed_paths"] == ["Organized/FromRef"]

    payload = serialize_ignition_json(project, scope)
    payload["tags"].append(payload["tags"][0])
    path.write_bytes(canonical_export_bytes(payload))
    duplicate = verify_ignition_reexport(project, selection, str(path))
    assert duplicate["matches"] is False
    assert duplicate["duplicate_actual_paths"] == [
        "Organized",
        "Organized/FromRef",
        "Organized/MissingRef",
        "Organized/SameNameOnly",
    ]


def test_export_paginates_more_than_500_siblings(tmp_path):
    source = tmp_path / "tags_WIDE.json"
    source.write_text(
        json.dumps(
            {
                "name": "",
                "tagType": "Provider",
                "tags": [
                    {"name": f"T{index:03}", "tagType": "AtomicTag"}
                    for index in range(501)
                ],
            }
        ),
        encoding="utf-8",
    )
    project = create_project(str(tmp_path / "wide"), name="Wide")
    try:
        import_source(project, str(source), site="factory")
        root = _uid(project, "WIDE", "")
        scope = compute_export_scope(project, root)
        payload = serialize_ignition_json(project, scope)
        assert scope["node_count"] == 502
        assert len(payload["tags"]) == 501
    finally:
        project.close()


def test_packaging_entry_supports_noninteractive_smoke_test(qapp):
    assert build_parser().parse_args(["--smoke-test"]).smoke_test is True
    assert main(["--smoke-test"]) == 0
    assert os.path.isfile(
        os.path.join(ROOT, "packaging", "ignition_tag_editor.spec")
    )
    assert os.path.isfile(os.path.join(ROOT, "packaging", "build.ps1"))
