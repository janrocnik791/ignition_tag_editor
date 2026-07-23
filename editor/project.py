"""Model delovnega projekta in zivljenjski cikel (mejnik B1).

Projekt = mapa z enim samostojnim ``project.sqlite``. Ta modul pokriva
create/open/save/close/recover; uvoz tagov v baseline pride v B2. Brez Qt.

Trajnost/obnovitev: baza tece v WAL nacinu, spremembe se potrdijo ob ``save``/
``close``. Prekinjena seja izgubi le nepotrjeno delo; zadnje potrjeno stanje ostane.
``recover`` odpre projekt, preveri integriteto in poravna morebitni ostanek WAL.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .schema import APP_ID, SCHEMA_VERSION, ProjectSchemaError, migrate

DB_FILENAME = "project.sqlite"


class ProjectError(Exception):
    """Napaka pri delu s projektom (manjka, poskodovan, ni nas projekt ...)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(path: str) -> str:
    """Razresi pot do ``project.sqlite``. ``path`` je mapa projekta ali datoteka."""
    if path.endswith(".sqlite") or os.path.isfile(path):
        return path
    return os.path.join(path, DB_FILENAME)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.DatabaseError as e:  # poskodovana / ni sqlite datoteka
        conn.close()
        raise ProjectError(f"Datoteka ni veljavna projektna baza: {db_path} ({e})")
    return conn


def _verify_is_project(conn: sqlite3.Connection, db_path: str) -> None:
    """Preveri, da je to nas projekt (obstaja project_meta z nasim app_id)."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='project_meta'"
        ).fetchone()
    except sqlite3.DatabaseError as e:  # ni veljavna sqlite baza
        raise ProjectError(f"Datoteka ni veljavna projektna baza: {db_path} ({e})")
    if row is None:
        raise ProjectError(f"Ni Ignition Tag Editor projekt (ni project_meta): {db_path}")
    meta = conn.execute("SELECT app_id FROM project_meta WHERE id = 1").fetchone()
    if meta is None or meta["app_id"] != APP_ID:
        raise ProjectError(f"Tuja ali poskodovana projektna baza: {db_path}")


def _load_meta(conn: sqlite3.Connection) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM project_meta WHERE id = 1").fetchone()
    if row is None:
        raise ProjectError("Manjka vrstica project_meta.")
    return dict(row)


@dataclass
class Project:
    """Odprt delovni projekt. Lasti povezavo na ``project.sqlite``."""

    db_path: str
    conn: sqlite3.Connection
    meta: Dict[str, Any] = field(default_factory=dict)
    _closed: bool = False

    # ---- lastnosti ----
    @property
    def name(self) -> str:
        return self.meta.get("name", "")

    @property
    def project_uid(self) -> str:
        return self.meta.get("project_uid", "")

    @property
    def schema_version(self) -> int:
        return int(self.meta.get("schema_version", 0))

    @property
    def directory(self) -> str:
        return os.path.dirname(os.path.abspath(self.db_path))

    # ---- zivljenjski cikel ----
    def save(self) -> "Project":
        """Potrdi stanje in osvezi ``updated_at``."""
        if self._closed:
            raise ProjectError("Projekt je zaprt.")
        self.conn.execute(
            "UPDATE project_meta SET updated_at = ? WHERE id = 1", (_now(),)
        )
        self.conn.commit()
        self.meta = _load_meta(self.conn)
        return self

    def close(self) -> None:
        """Potrdi in zapri povezavo (idempotentno)."""
        if self._closed:
            return
        try:
            self.conn.commit()
        finally:
            self.conn.close()
            self._closed = True

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def create_project(path: str, name: str) -> Project:
    """Ustvari nov projekt v mapi ``path``. Zavrne obstojeco projektno bazo."""
    db = _db_path(path)
    if os.path.exists(db):
        raise ProjectError(f"Projekt ze obstaja: {db}")
    parent = os.path.dirname(os.path.abspath(db))
    os.makedirs(parent, exist_ok=True)

    conn = _connect(db)
    try:
        migrate(conn)  # ustvari v1 shemo, user_version = SCHEMA_VERSION
        now = _now()
        conn.execute(
            "INSERT INTO project_meta "
            "(id, project_uid, name, app_id, schema_version, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, name, APP_ID, SCHEMA_VERSION, now, now),
        )
        conn.commit()
        return Project(db_path=db, conn=conn, meta=_load_meta(conn))
    except Exception:
        conn.close()
        raise


def open_project(path: str) -> Project:
    """Odpri obstojec projekt in uporabi cakajoce migracije."""
    db = _db_path(path)
    if not os.path.exists(db):
        raise ProjectError(f"Projekt ne obstaja: {db}")
    conn = _connect(db)
    try:
        _verify_is_project(conn, db)   # pred migrate, da ne pisemo v tujo bazo
        migrate(conn)                  # lahko vrze ProjectSchemaError (novejsa shema)
        return Project(db_path=db, conn=conn, meta=_load_meta(conn))
    except Exception:
        conn.close()
        raise


def recover(path: str) -> Project:
    """Obnovi projekt po prekinjeni seji: preveri integriteto, poravnaj WAL, odpri.

    Vrne odprt ``Project`` na zadnjem potrjenem stanju. Ob poskodbi vrze
    ``ProjectError``.
    """
    db = _db_path(path)
    if not os.path.exists(db):
        raise ProjectError(f"Projekt ne obstaja: {db}")
    conn = _connect(db)
    try:
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as e:
            raise ProjectError(f"Poskodovana projektna baza: {db} ({e})")
        if not check or check[0] != "ok":
            raise ProjectError(f"Preverjanje integritete ni uspelo: {db}")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # poravnaj ostanek WAL
        _verify_is_project(conn, db)
        migrate(conn)
        return Project(db_path=db, conn=conn, meta=_load_meta(conn))
    except Exception:
        conn.close()
        raise
