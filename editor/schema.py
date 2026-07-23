"""Shema projektne baze (``project.sqlite``) in deterministicni migracijski tekac.

Verzija sheme se vodi prek ``PRAGMA user_version`` (avtoritativno) in se zrcali v
stolpec ``project_meta.schema_version`` zaradi berljivosti. Migracije so naprej
usmerjene in oznacene z zaporedno verzijo; odpiranje starejsega projekta pozene
cakajoce migracije. Baseline se s poznejsimi migracijami nikoli ne prepise.

Mejnik B1 ustvari v1 shemo: ``project_meta``, ``sources``, ``baseline_nodes``.
Tabeli ``relationships`` (mejnik D1) in ``operations`` (mejnik F1) dodata svoji
migraciji pozneje -- tu jih namerno se ne ustvarimo (brez scaffoldinga za kasnejse
mejnike).
"""

from __future__ import annotations

import sqlite3
from typing import Callable, List, Tuple

# Trenutna ciljna verzija sheme.
SCHEMA_VERSION = 1

# Identifikator aplikacije, shranjen v project_meta (locevanje tujih .sqlite).
APP_ID = "ignition_tag_editor"


class ProjectSchemaError(Exception):
    """Nezdruzljiva verzija sheme (projekt je novejsi od podprte)."""


# ---- v1 shema ------------------------------------------------------------

_V1_SQL = """
-- Enovrsticni metapodatki projekta (id vedno 1).
CREATE TABLE project_meta (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    project_uid    TEXT NOT NULL,
    name           TEXT NOT NULL,
    app_id         TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- Registrirane uvozne datoteke (napolni B2).
CREATE TABLE sources (
    id             INTEGER PRIMARY KEY,
    path           TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    provider_name  TEXT,
    site           TEXT,
    kind           TEXT,
    import_session TEXT,
    imported_at    TEXT
);

-- Nespremenljiv baseline uvozenih vozlisc (napolni B2).
CREATE TABLE baseline_nodes (
    node_uid        TEXT PRIMARY KEY,
    provider_uid    TEXT NOT NULL,
    parent_uid      TEXT REFERENCES baseline_nodes(node_uid),
    sibling_index   INTEGER NOT NULL,
    depth           INTEGER NOT NULL,
    path_at_import  TEXT NOT NULL,
    name            TEXT,
    tag_type        TEXT,
    data_type       TEXT,
    value_source    TEXT,
    type_id         TEXT,
    opc_item_path   TEXT,
    opc_server      TEXT,
    source_tag_path TEXT,
    raw_json        TEXT NOT NULL,
    source_id       INTEGER REFERENCES sources(id)
);

-- Jedrni indeksi za identiteto in obhod drevesa; iskalni indeksi pridejo z C1/C3.
CREATE INDEX idx_baseline_parent   ON baseline_nodes(parent_uid);
CREATE INDEX idx_baseline_provider ON baseline_nodes(provider_uid);
"""


def _migration_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(_V1_SQL)


# Urejen seznam (verzija, funkcija). Dodaj nove migracije na konec.
MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_v1),
]


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Uporabi cakajoce migracije. Vrne koncno verzijo sheme.

    Nova baza ima ``user_version = 0``. Verzija, novejsa od ``SCHEMA_VERSION``,
    je napaka (baza je bila ustvarjena z novejso aplikacijo).
    """
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise ProjectSchemaError(
            f"Projektna shema v{version} je novejsa od podprte v{SCHEMA_VERSION}."
        )
    for target, fn in MIGRATIONS:
        if target > version:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {target}")
            version = target
    # Zrcali verzijo v project_meta, ce vrstica ze obstaja (na sveze bazi je se ni).
    conn.execute("UPDATE project_meta SET schema_version = ?", (version,))
    conn.commit()
    return version
