"""E1: persistentne in auditirane rocne relacije ter njihova veljavnost."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from editor import (
    RelationshipError,
    confirm_relationship,
    create_manual_relationship,
    create_project,
    discover_exact,
    import_source,
    open_project,
    query_relationships,
    refresh_relationship_validity,
    reject_relationship,
    relationship_validity,
    remove_manual_relationship,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor_d1")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="E1 manual relations")
    for filename in (
        "tags_IO_D1.json",
        "UDT_Definitions.json",
        "tags_UNS_D1.json",
    ):
        import_source(project, os.path.join(FIX, filename), site="d1")
    discover_exact(project)
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


def _auto_relation(project, *, node_uid: str, evidence_type: str, state: str):
    rows = query_relationships(
        project,
        node_uid=node_uid,
        evidence_type=evidence_type,
        state=state,
    )["results"]
    return next(row for row in rows if row["origin"] == "AUTO_EXACT")


def _query_one(project, relationship_uid: str):
    rows = query_relationships(project, limit=500)["results"]
    return next(
        row for row in rows
        if row["relationship_uid"] == relationship_uid
    )


def test_create_manual_relationship_is_audited_and_overrides_exact(project):
    raw = _uid(project, "IO_D1", "Raw/Temp")
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    exact = _auto_relation(
        project,
        node_uid=organized,
        evidence_type="SOURCE_TAG_PATH_RESOLVED",
        state="EXACT",
    )

    manual = create_manual_relationship(
        project,
        raw,
        organized,
        "RAW_TO_ORGANIZED",
        "operator@example.test",
        evidence={"ticket": "E1-1"},
        note="Verified against wiring drawing",
    )

    assert manual["origin"] == "MANUAL"
    assert manual["evidence_type"] == "MANUAL"
    assert manual["state"] == "MANUAL_CONFIRMED"
    assert manual["confirmed_by"] == "operator@example.test"
    assert manual["confirmed_at"]
    assert json.loads(manual["source_hashes_json"])
    evidence = json.loads(manual["evidence_json"])
    assert evidence["details"] == {"ticket": "E1-1"}
    assert evidence["history"][-1] == {
        "action": "create",
        "actor": "operator@example.test",
        "at": manual["confirmed_at"],
        "state": "MANUAL_CONFIRMED",
        "note": "Verified against wiring drawing",
    }

    queried_manual = _query_one(project, manual["relationship_uid"])
    queried_exact = _query_one(project, exact["relationship_uid"])
    assert queried_manual["is_effective"] is True
    assert queried_manual["validity"] == {"valid": True, "reasons": []}
    assert queried_exact["is_effective"] is False
    assert queried_exact["manual_override_uid"] == manual["relationship_uid"]


def test_confirm_and_reject_preserve_auto_evidence_and_manual_history(project):
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    exact = _auto_relation(
        project,
        node_uid=organized,
        evidence_type="SOURCE_TAG_PATH_RESOLVED",
        state="EXACT",
    )

    confirmed = confirm_relationship(
        project,
        exact["relationship_uid"],
        "reviewer",
        note="Confirmed",
    )
    rejected = reject_relationship(
        project,
        exact["relationship_uid"],
        "senior-reviewer",
        note="Superseded after review",
    )

    assert rejected["relationship_uid"] == confirmed["relationship_uid"]
    assert rejected["state"] == "MANUAL_REJECTED"
    assert [
        item["action"]
        for item in json.loads(rejected["evidence_json"])["history"]
    ] == ["confirm", "reject"]
    untouched = project.conn.execute(
        "SELECT state, origin, evidence_type, confirmed_by "
        "FROM relationships WHERE relationship_uid = ?",
        (exact["relationship_uid"],),
    ).fetchone()
    assert tuple(untouched) == (
        "EXACT",
        "AUTO_EXACT",
        "SOURCE_TAG_PATH_RESOLVED",
        None,
    )
    assert _query_one(project, rejected["relationship_uid"])[
        "is_effective"
    ] is True
    assert _query_one(project, exact["relationship_uid"])[
        "is_effective"
    ] is False


def test_remove_is_logical_and_restores_automatic_precedence(project):
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    exact = _auto_relation(
        project,
        node_uid=organized,
        evidence_type="SOURCE_TAG_PATH_RESOLVED",
        state="EXACT",
    )
    manual = confirm_relationship(
        project,
        exact["relationship_uid"],
        "reviewer",
    )

    removed = remove_manual_relationship(
        project,
        manual["relationship_uid"],
        "administrator",
        note="Return to discovery result",
    )

    evidence = json.loads(removed["evidence_json"])
    assert evidence["removed"] is True
    assert [entry["action"] for entry in evidence["history"]] == [
        "confirm",
        "remove",
    ]
    assert removed["confirmed_by"] == "administrator"
    assert _query_one(project, manual["relationship_uid"])[
        "is_effective"
    ] is False
    assert _query_one(project, exact["relationship_uid"])[
        "is_effective"
    ] is True
    with pytest.raises(RelationshipError, match="samo MANUAL"):
        remove_manual_relationship(project, exact["relationship_uid"], "admin")


def test_unresolved_confirmation_requires_and_uses_candidate(project):
    missing = _uid(project, "UNS_D1", "MissingOpc")
    raw = _uid(project, "IO_D1", "Raw/Temp")
    unresolved = _auto_relation(
        project,
        node_uid=missing,
        evidence_type="OPC_ITEM_PATH_EXACT",
        state="UNRESOLVED",
    )

    with pytest.raises(RelationshipError, match="candidate_node_uid"):
        confirm_relationship(project, unresolved["relationship_uid"], "reviewer")

    manual = confirm_relationship(
        project,
        unresolved["relationship_uid"],
        "reviewer",
        candidate_node_uid=raw,
    )
    assert manual["source_node_uid"] == raw
    assert manual["target_node_uid"] == missing
    assert json.loads(manual["evidence_json"])[
        "based_on_relationship_uid"
    ] == unresolved["relationship_uid"]
    assert _query_one(project, unresolved["relationship_uid"])[
        "is_effective"
    ] is False


def test_manual_input_validation(project):
    raw = _uid(project, "IO_D1", "Raw/Temp")
    organized = _uid(project, "IO_D1", "Organized/FromRef")

    invalid_calls = (
        lambda: create_manual_relationship(
            project, "missing", organized, "GENERIC", "actor"
        ),
        lambda: create_manual_relationship(
            project, raw, "missing", "GENERIC", "actor"
        ),
        lambda: create_manual_relationship(
            project, raw, raw, "GENERIC", "actor"
        ),
        lambda: create_manual_relationship(
            project, raw, organized, "GUESSED", "actor"
        ),
        lambda: create_manual_relationship(
            project, raw, organized, "GENERIC", " "
        ),
        lambda: create_manual_relationship(
            project, raw, None, "GENERIC", "actor"
        ),
        lambda: create_manual_relationship(
            project, raw, organized, "GENERIC", None
        ),
        lambda: create_manual_relationship(
            project,
            raw,
            organized,
            "GENERIC",
            "actor",
            evidence=["not", "an", "object"],
        ),
    )
    for call in invalid_calls:
        with pytest.raises(RelationshipError):
            call()


def test_manual_relationship_persists_across_reopen(project):
    raw = _uid(project, "IO_D1", "Raw/Temp")
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    manual = create_manual_relationship(
        project,
        raw,
        organized,
        "RAW_TO_ORGANIZED",
        "operator",
    )
    db_path = project.db_path
    project.close()

    reopened = open_project(db_path)
    try:
        row = _query_one(reopened, manual["relationship_uid"])
        assert row["state"] == "MANUAL_CONFIRMED"
        assert row["evidence"]["history"][0]["actor"] == "operator"
        assert row["is_effective"] is True
    finally:
        reopened.close()


def test_changed_source_marks_manual_stale_and_restores_it(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    source = source_dir / "tags_IO_D1.json"
    shutil.copy2(os.path.join(FIX, "tags_IO_D1.json"), source)
    original = source.read_bytes()
    project = create_project(str(tmp_path / "proj"), name="E1 validity")
    try:
        import_source(project, str(source), site="d1")
        raw = _uid(project, "IO_D1", "Raw/Temp")
        organized = _uid(project, "IO_D1", "Organized/FromRef")
        manual = create_manual_relationship(
            project,
            raw,
            organized,
            "RAW_TO_ORGANIZED",
            "operator",
        )
        assert relationship_validity(
            project, manual["relationship_uid"]
        )["valid"] is True

        content = json.loads(source.read_text(encoding="utf-8"))
        content["tags"][0]["tags"][0]["documentation"] = "changed"
        source.write_text(
            json.dumps(content, ensure_ascii=False),
            encoding="utf-8",
        )
        assert import_source(project, str(source), site="d1")[
            "status"
        ] == "reimported"

        validity = relationship_validity(project, manual["relationship_uid"])
        assert validity["valid"] is False
        assert any(
            reason.startswith("source_hash_changed:")
            for reason in validity["reasons"]
        )
        summary = refresh_relationship_validity(project)
        assert summary["stale"] >= 1
        stale = _query_one(project, manual["relationship_uid"])
        assert stale["state"] == "STALE"
        assert stale["is_effective"] is False

        source.write_bytes(original)
        import_source(project, str(source), site="d1")
        summary = refresh_relationship_validity(project)
        assert summary["restored"] >= 1
        restored = _query_one(project, manual["relationship_uid"])
        assert restored["state"] == "MANUAL_CONFIRMED"
        assert restored["validity"]["valid"] is True
    finally:
        project.close()


def test_manual_decision_precedes_future_suggestion_and_survives_discovery(
    project,
):
    raw = _uid(project, "IO_D1", "Raw/Temp")
    organized = _uid(project, "IO_D1", "Organized/FromRef")
    manual = create_manual_relationship(
        project,
        raw,
        organized,
        "RAW_TO_ORGANIZED",
        "operator",
    )
    exact_before = project.conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE origin = 'AUTO_EXACT'"
    ).fetchone()[0]

    project.conn.execute(
        "INSERT INTO relationships ("
        "relationship_uid, source_node_uid, target_node_uid, role, state, "
        "evidence_type, evidence_json, origin, confidence, confirmed_by, "
        "confirmed_at, created_at, updated_at, source_hashes_json"
        ") SELECT 'future-suggestion', ?, ?, ?, 'UNRESOLVED', "
        "'MANUAL', '{}', 'SUGGESTION', 0.5, NULL, NULL, "
        "created_at, updated_at, source_hashes_json "
        "FROM relationships WHERE relationship_uid = ?",
        (
            raw,
            organized,
            "RAW_TO_ORGANIZED",
            manual["relationship_uid"],
        ),
    )
    project.conn.commit()

    assert _query_one(project, "future-suggestion")["is_effective"] is False
    discover_exact(project)
    assert project.conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE origin = 'AUTO_EXACT'"
    ).fetchone()[0] == exact_before
    assert _query_one(project, manual["relationship_uid"])[
        "is_effective"
    ] is True
