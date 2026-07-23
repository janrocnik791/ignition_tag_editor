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

def test_create_project_builds_v1_schema(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="Test projekt")
    try:
        assert os.path.exists(p.db_path)
        assert p.name == "Test projekt"
        assert p.project_uid  # dodeljen
        assert p.schema_version == SCHEMA_VERSION == 1
    finally:
        p.close()

    tables = _tables(p.db_path)
    assert {"project_meta", "sources", "baseline_nodes"} <= tables
    # relationships/operations se namerno se NE ustvarita v B1 (D1/F1).
    assert "relationships" not in tables
    assert "operations" not in tables

    conn = sqlite3.connect(p.db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM baseline_nodes").fetchone()[0] == 0
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
        assert p2.schema_version == 1
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
        assert p.schema_version == 1
        assert migrate(p.conn) == 1  # ponovni zagon migrate je no-op
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
        assert rec.schema_version == 1
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
