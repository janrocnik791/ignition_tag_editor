"""Checkpoint I: celoten vertikalni rez nad nezaupno sinteticno golden linijo."""

from __future__ import annotations

import json
import os

from editor import (
    confirm_relationship,
    create_operation,
    create_project,
    diff,
    discover_exact,
    import_source,
    propose_automation,
    query_relationships,
    verify_round_trip,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "tests", "fixtures", "golden_line")


def _uid(project, provider, path):
    row = project.conn.execute(
        "SELECT b.node_uid FROM baseline_nodes b JOIN sources s ON s.id=b.source_id "
        "WHERE s.provider_name=? AND b.path_at_import=?",
        (provider, path),
    ).fetchone()
    assert row
    return row["node_uid"]


def test_synthetic_golden_line_end_to_end(tmp_path):
    spec = json.load(open(os.path.join(GOLDEN, "spec.json"), encoding="utf-8"))
    project = create_project(str(tmp_path / "project"), name=spec["name"])
    try:
        for relative in spec["sources"]:
            import_source(
                project,
                os.path.normpath(os.path.join(GOLDEN, relative)),
                site=spec["site"],
            )
        discovery = discover_exact(project)
        assert discovery["total"] == spec["expected"]["relationship_total"]
        assert discovery["by_state"] == spec["expected"]["relationship_states"]

        proposals = propose_automation(project, include_fuzzy=False)
        assert {
            key: proposals[key]
            for key in ("deterministic_name", "deterministic_group", "fuzzy")
        } == spec["expected"]["automation"]
        name_only = query_relationships(
            project,
            evidence_type="DETERMINISTIC_NAME_PATTERN",
            limit=100,
        )["results"]
        # Identical names inside one provider are not cross-provider mappings.
        assert not name_only
        assert all(row["state"] != "EXACT" for row in name_only)

        manual_spec = spec["manual_confirmation"]
        anchor = _uid(project, manual_spec["provider"], manual_spec["path"])
        base = query_relationships(
            project,
            node_uid=anchor,
            evidence_type=manual_spec["evidence_type"],
            state="EXACT",
        )["results"][0]
        manual = confirm_relationship(
            project, base["relationship_uid"], "golden-reviewer"
        )
        assert manual["state"] == "MANUAL_CONFIRMED"

        for operation in spec["operations"]:
            create_operation(
                project,
                operation["op_type"],
                _uid(project, operation["provider"], operation["path"]),
                operation["payload"],
                "golden-editor",
            )
        assert diff(project)["counts"] == spec["expected"]["diff_counts"]

        selection = spec["export_selection"]
        verified = verify_round_trip(
            project,
            _uid(project, selection["provider"], selection["path"]),
        )
        assert verified["status"] == "EXPORT_VERIFIED"
        assert verified["scope"]["node_count"] == (
            spec["expected"]["export_scope_nodes"]
        )
        assert verified["actual_count"] == spec["expected"]["roundtrip_nodes"]
        # Successful verification intentionally omits verbose rows; inspect export.
        from editor import compute_export_scope, serialize_ignition_json

        scope = compute_export_scope(
            project, _uid(project, selection["provider"], selection["path"])
        )
        payload = serialize_ignition_json(project, scope)
        organized = next(
            row for row in payload["tags"] if row["name"] == "Organized"
        )
        assert any(
            f"Organized/{row['name']}"
            == spec["expected"]["renamed_export_path"]
            for row in organized["tags"]
        )
    finally:
        project.close()
