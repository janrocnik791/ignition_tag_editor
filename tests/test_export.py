"""H1: omejen deterministicni Ignition 8.3 JSON izvoz."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from editor import (
    canonical_export_bytes,
    compute_export_scope,
    create_operation,
    create_project,
    import_source,
    serialize_ignition_json,
    write_package,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
C4 = os.path.join(ROOT, "tests", "fixtures", "editor_c4", "site_a")


@pytest.fixture()
def project(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="H1 export")
    source = os.path.join(FIX, "tags_IO_TESTSITE_SIE.json")
    import_source(p, source, site="testsite")
    yield p
    p.close()


def _uid(project, path):
    row = project.conn.execute(
        "SELECT node_uid FROM baseline_nodes WHERE path_at_import=?",
        (path,),
    ).fetchone()
    assert row
    return row["node_uid"]


def test_noop_provider_export_reconstructs_fixture_losslessly(project):
    root = _uid(project, "")
    scope = compute_export_scope(project, root)
    payload = serialize_ignition_json(project, scope)
    original = json.load(
        open(os.path.join(FIX, "tags_IO_TESTSITE_SIE.json"), encoding="utf-8")
    )
    assert payload == {"tags": original["tags"]}
    assert scope["node_count"] == 6


def test_subtree_scope_is_limited_and_operations_are_applied(project):
    area1 = _uid(project, "Area1")
    run = _uid(project, "Area1/Motor1_Run")
    create_operation(
        project, "RENAME_TAG", run, {"new_name": "RunState"}, "operator"
    )
    scope = compute_export_scope(project, area1)
    payload = serialize_ignition_json(project, scope)
    assert scope["node_count"] == 3
    assert payload["tags"][0]["name"] == "Area1"
    assert [row["name"] for row in payload["tags"][0]["tags"]] == [
        "RunState", "Motor1_Speed"
    ]
    assert "Area2" not in json.dumps(payload)


def test_export_bytes_are_deterministic(project):
    area1 = _uid(project, "Area1")
    scope = compute_export_scope(project, area1)
    first = canonical_export_bytes(serialize_ignition_json(project, scope))
    second = canonical_export_bytes(serialize_ignition_json(project, scope))
    assert first == second
    assert first.endswith(b"\n")


def test_package_manifest_hash_and_udt_warning(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="UDT export")
    try:
        import_source(
            p, os.path.join(C4, "UDT_Definitions.json"), site="site_a"
        )
        import_source(
            p, os.path.join(C4, "tags_UNS_SITEA.json"), site="site_a"
        )
        instance = next(
            row["node_uid"]
            for row in p.conn.execute(
                "SELECT node_uid FROM baseline_nodes "
                "WHERE path_at_import='Line1'"
            ).fetchall()
        )
        result = write_package(p, instance, str(tmp_path / "out"))
        data = open(result["tags_path"], "rb").read()
        manifest = json.load(open(result["manifest_path"], encoding="utf-8"))
        assert manifest["target_ignition_version"] == "8.3"
        assert manifest["tags_sha256"] == hashlib.sha256(data).hexdigest()
        assert manifest["warnings"]
        assert manifest["scope"]["node_count"] == 2
    finally:
        p.close()
