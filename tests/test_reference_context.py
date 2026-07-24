"""Checkpoint J: optional analyzer/reference context creates pending suggestions."""

from __future__ import annotations

import os

import pytest

from editor import (
    confirm_relationship,
    create_project,
    import_reference_context,
    import_source,
    query_relationships,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")


@pytest.fixture()
def project(tmp_path):
    opened = create_project(str(tmp_path / "project"), name="Reference context")
    yield opened
    opened.close()


def _import_d1(project):
    for name in ("tags_IO_D1.json", "tags_UNS_D1.json"):
        import_source(
            project,
            os.path.join(FIX, "editor_d1", name),
            site="factory",
        )


def test_reference_context_is_optional(project):
    _import_d1(project)
    assert query_relationships(project)["total"] == 0


def test_reference_context_creates_only_reviewable_suggestions(project):
    _import_d1(project)
    result = import_reference_context(
        project,
        os.path.join(FIX, "reference_context"),
        site="factory",
        line="L1",
    )
    assert result["index"]["sources"] == 1
    assert result["created_or_refreshed"] == 4
    assert result["matched_records"] == 3
    assert result["skipped_records"] == 0

    page = query_relationships(
        project, evidence_type="REFERENCE_EXPECTATION", limit=100
    )
    assert page["total"] == 4
    assert {row["origin"] for row in page["results"]} == {"SUGGESTION"}
    assert {row["state"] for row in page["results"]} <= {
        "UNRESOLVED",
        "AMBIGUOUS",
    }
    assert all(row["confirmed_by"] is None for row in page["results"])
    assert all(
        row["evidence"]["adapter"] == "analyzer.reference"
        and row["evidence"]["provenance"]["sha256"]
        for row in page["results"]
    )


def test_reference_suggestions_are_idempotent_and_manually_confirmable(project):
    _import_d1(project)
    args = (
        project,
        os.path.join(FIX, "reference_context"),
    )
    first = import_reference_context(
        *args, site="factory", line="L1"
    )
    second = import_reference_context(
        *args, site="factory", line="L1"
    )
    assert first["created_or_refreshed"] == second["created_or_refreshed"] == 4
    assert query_relationships(
        project, evidence_type="REFERENCE_EXPECTATION", limit=100
    )["total"] == 4

    rename = next(
        row
        for row in query_relationships(
            project, evidence_type="REFERENCE_EXPECTATION", limit=100
        )["results"]
        if row["evidence"]["kind"] == "line_tag_rename"
        and row["target_node_uid"] is not None
    )
    manual = confirm_relationship(
        project, rename["relationship_uid"], "checkpoint-j-reviewer"
    )
    assert manual["origin"] == "MANUAL"
    assert manual["state"] == "MANUAL_CONFIRMED"
    base = next(
        row
        for row in query_relationships(
            project, evidence_type="REFERENCE_EXPECTATION", limit=100
        )["results"]
        if row["relationship_uid"] == rename["relationship_uid"]
    )
    assert base["origin"] == "SUGGESTION"
    assert base["state"] != "MANUAL_CONFIRMED"
    assert base["manual_override_uid"] == manual["relationship_uid"]
