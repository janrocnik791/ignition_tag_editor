"""Testi mejnika B1: model projekta, shema, migracijski tekac, zivljenjski cikel."""

from __future__ import annotations

import os
import sqlite3

import pytest

from editor import (
    Project,
    ProjectError,
    ProjectSchemaError,
    SCHEMA_VERSION,
    create_project,
    open_project,
    recover,
)
from editor.schema import APP_ID, migrate


def _tables(db_path: str) -> set:
    conn = sqlite3.connect(db_path)
    try:
        return {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()


# ---- ustvarjanje ---------------------------------------------------------

def test_create_project_builds_current_schema(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="Test projekt")
    try:
        assert os.path.exists(p.db_path)
        assert p.name == "Test projekt"
        assert p.project_uid  # dodeljen
        assert p.schema_version == SCHEMA_VERSION == 3
    finally:
        p.close()

    tables = _tables(p.db_path)
    assert {"project_meta", "sources", "baseline_nodes", "relationships"} <= tables
    # operations se namerno se NE ustvari pred F1.
    assert "operations" not in tables

    conn = sqlite3.connect(p.db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM baseline_nodes").fetchone()[0] == 0
        relationship_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(relationships)"
            ).fetchall()
        }
        assert {
            "relationship_uid",
            "source_node_uid",
            "target_node_uid",
            "role",
            "state",
            "evidence_type",
            "evidence_json",
            "origin",
            "confidence",
            "confirmed_by",
            "confirmed_at",
            "created_at",
            "updated_at",
            "source_hashes_json",
        } == relationship_columns
        relationship_indexes = {
            row[1]
            for row in conn.execute(
                "PRAGMA index_list(relationships)"
            ).fetchall()
        }
        assert {
            "idx_relationships_source",
            "idx_relationships_target",
            "idx_relationships_filter_state",
            "idx_relationships_filter_evidence",
            "idx_relationships_filter_role",
        } <= relationship_indexes
        appid = conn.execute("SELECT app_id FROM project_meta WHERE id=1").fetchone()[0]
        assert appid == APP_ID
    finally:
        conn.close()


def test_create_refuses_existing(tmp_path):
    d = str(tmp_path / "proj")
    p = create_project(d, name="A")
    p.close()
    with pytest.raises(ProjectError):
        create_project(d, name="B")


def test_create_uses_directory_layout(tmp_path):
    d = tmp_path / "myproject"
    p = create_project(str(d), name="X")
    try:
        assert p.db_path == str(d / "project.sqlite")
        assert p.directory == str(d.resolve()) or os.path.abspath(str(d))
    finally:
        p.close()


# ---- odpiranje / trajnost ------------------------------------------------

def test_reopen_preserves_metadata(tmp_path):
    d = str(tmp_path / "proj")
    p1 = create_project(d, name="Trajni")
    uid = p1.project_uid
    created = p1.meta["created_at"]
    p1.close()

    p2 = open_project(d)
    try:
        assert p2.name == "Trajni"
        assert p2.project_uid == uid
        assert p2.meta["created_at"] == created
        assert p2.schema_version == SCHEMA_VERSION
    finally:
        p2.close()


def test_open_missing_raises(tmp_path):
    with pytest.raises(ProjectError):
        open_project(str(tmp_path / "ni_projekta"))


def test_open_foreign_sqlite_raises_and_does_not_pollute(tmp_path):
    foreign = str(tmp_path / "tuja.sqlite")
    conn = sqlite3.connect(foreign)
    conn.execute("CREATE TABLE nekaj (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(ProjectError):
        open_project(foreign)
    # ne sme ustvariti nasih tabel v tuji bazi
    assert "project_meta" not in _tables(foreign)


def test_save_updates_timestamp(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="X")
    try:
        before = p.meta["updated_at"]
        p.save()
        after = p.meta["updated_at"]
        assert after >= before
    finally:
        p.close()


def test_context_manager_closes(tmp_path):
    with create_project(str(tmp_path / "proj"), name="X") as p:
        assert not p._closed
    assert p._closed
    # ponovno odpiranje deluje po zaprtju prek context managerja
    open_project(str(tmp_path / "proj")).close()


# ---- migracije -----------------------------------------------------------

def test_migrate_is_idempotent(tmp_path):
    d = str(tmp_path / "proj")
    create_project(d, name="X").close()
    # ponovni open ne sme premakniti verzije ali vreci
    p = open_project(d)
    try:
        assert p.schema_version == SCHEMA_VERSION
        assert migrate(p.conn) == SCHEMA_VERSION  # ponovni zagon migrate je no-op
    finally:
        p.close()


def test_schema_newer_than_supported_raises(tmp_path):
    d = str(tmp_path / "proj")
    p = create_project(d, name="X")
    db = p.db_path
    p.close()
    # simuliraj projekt, ustvarjen z novejso aplikacijo
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(ProjectSchemaError):
        open_project(d)


def test_v1_project_migrates_through_v3(tmp_path):
    d = str(tmp_path / "proj")
    p = create_project(d, name="Starejsi")
    db = p.db_path
    p.close()

    conn = sqlite3.connect(db)
    search_indexes = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name LIKE 'idx_baseline_search_%'"
        ).fetchall()
    ]
    for index_name in search_indexes:
        conn.execute(f'DROP INDEX "{index_name}"')
    conn.execute("DROP TABLE relationships")
    conn.execute("PRAGMA user_version = 1")
    conn.execute("UPDATE project_meta SET schema_version = 1 WHERE id = 1")
    conn.commit()
    conn.close()

    migrated = open_project(d)
    try:
        assert migrated.schema_version == 3
        indexes = {
            row[0]
            for row in migrated.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name LIKE 'idx_baseline_search_%'"
            ).fetchall()
        }
        assert {
            "idx_baseline_search_path",
            "idx_baseline_search_name",
            "idx_baseline_search_tag_type",
        } <= indexes
        assert "relationships" in _tables(db)
    finally:
        migrated.close()


def test_v2_project_migrates_relationships_without_rewriting_baseline(tmp_path):
    d = str(tmp_path / "proj")
    p = create_project(d, name="V2")
    p.conn.execute(
        "INSERT INTO sources "
        "(path, sha256, provider_name, site, kind) "
        "VALUES ('synthetic.json', 'abc', 'P', 's', 'io')"
    )
    source_id = p.conn.execute(
        "SELECT id FROM sources WHERE provider_name = 'P'"
    ).fetchone()["id"]
    p.conn.execute(
        "INSERT INTO baseline_nodes "
        "(node_uid, provider_uid, parent_uid, sibling_index, depth, "
        "path_at_import, name, tag_type, raw_json, source_id) "
        "VALUES ('node-1', 'provider-1', NULL, 0, 0, '', '', "
        "'Provider', '{\"name\":\"\"}', ?)",
        (source_id,),
    )
    before = tuple(
        p.conn.execute(
            "SELECT node_uid, raw_json FROM baseline_nodes"
        ).fetchone()
    )
    p.conn.execute("DROP TABLE relationships")
    p.conn.execute("PRAGMA user_version = 2")
    p.conn.execute(
        "UPDATE project_meta SET schema_version = 2 WHERE id = 1"
    )
    p.conn.commit()
    db = p.db_path
    p.close()

    migrated = open_project(db)
    try:
        after = tuple(
            migrated.conn.execute(
                "SELECT node_uid, raw_json FROM baseline_nodes"
            ).fetchone()
        )
        assert migrated.schema_version == 3
        assert before == after
        assert "relationships" in _tables(db)
    finally:
        migrated.close()


# ---- obnovitev -----------------------------------------------------------

def test_recover_after_interrupted_session(tmp_path):
    d = str(tmp_path / "proj")
    p = create_project(d, name="Original")
    db = p.db_path
    p.save()
    p.close()

    # simuliraj prekinitev sredi urejanja: nepotrjen zapis, trd zapor brez commita
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("UPDATE project_meta SET name = 'UMAZANO' WHERE id = 1")
    raw.close()  # brez commit -> rollback

    rec = recover(d)
    try:
        assert rec.name == "Original"  # zadnje potrjeno stanje ostane
        assert rec.schema_version == SCHEMA_VERSION
    finally:
        rec.close()


def test_recover_detects_corruption(tmp_path):
    d = tmp_path / "proj"
    p = create_project(str(d), name="X")
    db = p.db_path
    p.close()
    # odstrani WAL/SHM ostanke in prepisi glavno datoteko s smetmi
    for suffix in ("-wal", "-shm"):
        side = db + suffix
        if os.path.exists(side):
            os.remove(side)
    with open(db, "wb") as f:
        f.write(b"to ni sqlite baza" * 8)
    with pytest.raises(ProjectError):
        recover(str(d))
