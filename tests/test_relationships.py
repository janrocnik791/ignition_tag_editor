"""D1: exact relacije, dokaz, dvoumnost in nerešene vrzeli."""

from __future__ import annotations

import os

import pytest

from editor import (
    EVIDENCE_TYPES,
    RelationshipError,
    create_project,
    discover_exact,
    import_source,
    query_relationships,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor_d1")
C4_SITE_B = os.path.join(ROOT, "tests", "fixtures", "editor_c4", "site_b")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="D1 relations")
    for filename in (
        "tags_IO_D1.json",
        "UDT_Definitions.json",
        "tags_UNS_D1.json",
    ):
        import_source(project, os.path.join(FIX, filename), site="d1")
    for filename in ("UDT_Definitions.json", "tags_UNS_SITEB.json"):
        import_source(project, os.path.join(C4_SITE_B, filename), site="site_b")
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


def _one(project, *, node_uid: str, evidence_type: str, state: str):
    result = query_relationships(
        project,
        node_uid=node_uid,
        evidence_type=evidence_type,
        state=state,
    )
    assert result["total"] == 1
    return result["results"][0]


def test_discover_writes_all_exact_evidence_types_and_preserves_baseline(project):
    before = project.conn.execute(
        "SELECT node_uid, raw_json FROM baseline_nodes ORDER BY node_uid"
    ).fetchall()

    summary = discover_exact(project)

    after = project.conn.execute(
        "SELECT node_uid, raw_json FROM baseline_nodes ORDER BY node_uid"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert set(summary["by_evidence_type"]) == set(EVIDENCE_TYPES) - {"MANUAL"}
    assert {"EXACT", "UNRESOLVED", "AMBIGUOUS"} <= set(summary["by_state"])
    assert summary["total"] == project.conn.execute(
        "SELECT COUNT(*) FROM relationships"
    ).fetchone()[0]


def test_source_tag_path_resolves_provider_and_direction(project):
    discover_exact(project)
    raw = _uid(project, "IO_D1", "Raw/Temp")
    organized = _uid(project, "IO_D1", "Organized/FromRef")

    relation = _one(
        project,
        node_uid=organized,
        evidence_type="SOURCE_TAG_PATH_RESOLVED",
        state="EXACT",
    )
    assert relation["source_node_uid"] == raw
    assert relation["target_node_uid"] == organized
    assert relation["role"] == "RAW_TO_ORGANIZED"
    assert relation["evidence"]["provider"] == "IO_D1"
    assert relation["evidence"]["resolved_path"] == "Raw/Temp"
    assert relation["confidence"] == 1.0
    assert relation["source_hashes"]


def test_missing_source_tag_path_is_explicitly_unresolved(project):
    discover_exact(project)
    missing = _uid(project, "IO_D1", "Organized/MissingRef")

    relation = _one(
        project,
        node_uid=missing,
        evidence_type="SOURCE_TAG_PATH_RESOLVED",
        state="UNRESOLVED",
    )
    assert relation["target_node_uid"] is None
    assert relation["evidence"]["candidate_count"] == 0


def test_unique_and_shared_opc_paths_are_not_conflated(project):
    discover_exact(project)
    raw = _uid(project, "IO_D1", "Raw/Temp")
    direct = _uid(project, "UNS_D1", "DirectOpc")
    ambiguous = _uid(project, "UNS_D1", "AmbiguousOpc")

    exact = _one(
        project,
        node_uid=direct,
        evidence_type="OPC_ITEM_PATH_EXACT",
        state="EXACT",
    )
    assert exact["source_node_uid"] == raw
    assert exact["target_node_uid"] == direct

    shared = _one(
        project,
        node_uid=ambiguous,
        evidence_type="OPC_ITEM_PATH_EXACT",
        state="AMBIGUOUS",
    )
    assert shared["target_node_uid"] is None
    assert shared["evidence"]["candidate_count"] == 2
    assert len(shared["evidence"]["candidate_uids"]) == 2


def test_missing_opc_candidate_is_unresolved(project):
    discover_exact(project)
    missing = _uid(project, "UNS_D1", "MissingOpc")

    relation = _one(
        project,
        node_uid=missing,
        evidence_type="OPC_ITEM_PATH_EXACT",
        state="UNRESOLVED",
    )
    assert relation["target_node_uid"] is None


def test_udt_membership_uses_effective_inheritance(project):
    discover_exact(project)
    motor_type = _uid(project, "UDT_d1", "_types_/MotorUDT")
    inherited = _uid(project, "UDT_d1", "_types_/BaseUDT/BaseMember")
    rows = query_relationships(
        project,
        node_uid=motor_type,
        evidence_type="UDT_DEFINITION_MEMBERSHIP",
        state="EXACT",
    )["results"]

    relation = next(row for row in rows if row["target_node_uid"] == inherited)
    assert relation["evidence"]["member_path"] == "BaseMember"
    assert relation["evidence"]["inherited"] is True
    assert relation["evidence"]["inheritance_chain"] == [
        "MotorUDT",
        "BaseUDT",
    ]


def test_instance_type_links_effective_members_to_instance(project):
    discover_exact(project)
    instance = _uid(project, "UNS_D1", "Motor1")
    inherited = _uid(project, "UDT_d1", "_types_/BaseUDT/BaseMember")
    rows = query_relationships(
        project,
        node_uid=instance,
        evidence_type="INSTANCE_TYPE",
        state="EXACT",
    )["results"]

    assert {row["evidence"]["member_path"] for row in rows} == {
        "BaseMember",
        "SameNameOnly",
        "Speed",
    }
    assert any(row["source_node_uid"] == inherited for row in rows)
    assert all(row["target_node_uid"] == instance for row in rows)
    assert all(row["role"] == "MEMBER_TO_UNS_INSTANCE" for row in rows)


def test_unknown_instance_type_is_unresolved(project):
    discover_exact(project)
    unknown = _uid(project, "UNS_D1", "Unknown1")

    relation = _one(
        project,
        node_uid=unknown,
        evidence_type="INSTANCE_TYPE",
        state="UNRESOLVED",
    )
    assert relation["evidence"]["type_id"] == "UnknownUDT"
    assert relation["evidence"]["definition_found"] is False


def test_same_type_id_is_resolved_per_site(project):
    discover_exact(project)
    instance = _uid(project, "UNS_SITEB", "LineB")
    rows = query_relationships(
        project,
        node_uid=instance,
        evidence_type="INSTANCE_TYPE",
        state="EXACT",
    )["results"]

    assert [row["evidence"]["member_path"] for row in rows] == ["Temperature"]
    assert all(row["source_site"] == "site_b" for row in rows)
    assert all(row["target_site"] == "site_b" for row in rows)


def test_equal_name_alone_never_creates_relationship(project):
    discover_exact(project)
    raw = _uid(project, "IO_D1", "Raw/SameNameOnly")
    organized = _uid(project, "IO_D1", "Organized/SameNameOnly")
    count = project.conn.execute(
        "SELECT COUNT(*) FROM relationships "
        "WHERE (source_node_uid = ? AND target_node_uid = ?) "
        "OR (source_node_uid = ? AND target_node_uid = ?)",
        (raw, organized, organized, raw),
    ).fetchone()[0]
    assert count == 0


def test_discovery_is_idempotent(project):
    first_summary = discover_exact(project)
    before = [
        tuple(row)
        for row in project.conn.execute(
            "SELECT relationship_uid, created_at, updated_at, evidence_json "
            "FROM relationships ORDER BY relationship_uid"
        ).fetchall()
    ]

    second_summary = discover_exact(project)
    after = [
        tuple(row)
        for row in project.conn.execute(
            "SELECT relationship_uid, created_at, updated_at, evidence_json "
            "FROM relationships ORDER BY relationship_uid"
        ).fetchall()
    ]

    assert second_summary == first_summary
    assert before == after


def test_query_filters_pages_and_validates(project):
    discover_exact(project)
    first = query_relationships(project, state="EXACT", limit=2)
    second = query_relationships(
        project,
        state="EXACT",
        limit=2,
        offset=2,
    )
    assert first["total"] > 2
    assert first["has_next"] is True
    assert second["has_previous"] is True
    assert {
        row["relationship_uid"] for row in first["results"]
    }.isdisjoint(
        row["relationship_uid"] for row in second["results"]
    )

    with pytest.raises(RelationshipError):
        query_relationships(project, state="GUESSED")
    with pytest.raises(RelationshipError):
        query_relationships(project, limit=501)
