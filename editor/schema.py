"""Shema projektne baze (``project.sqlite``) in deterministicni migracijski tekac.

Verzija sheme se vodi prek ``PRAGMA user_version`` (avtoritativno) in se zrcali v
stolpec ``project_meta.schema_version`` zaradi berljivosti. Migracije so naprej
usmerjene in oznacene z zaporedno verzijo; odpiranje starejsega projekta pozene
cakajoce migracije. Baseline se s poznejsimi migracijami nikoli ne prepise.

Mejnik B1 ustvari v1 shemo: ``project_meta``, ``sources``, ``baseline_nodes``.
C3 doda v2 iskalne indekse brez spremembe baseline vrstic.
D1 doda v3 tabelo ``relationships`` brez spremembe baseline vrstic. F1 doda
v4 tabelo ``operations``, prav tako brez spremembe baseline vrstic. G2 doda
v5 trajni kazalec undo/redo v ``project_meta``.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, List, Tuple

# Trenutna ciljna verzija sheme.
SCHEMA_VERSION = 5

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


# ---- v2: C3 iskalni indeksi ---------------------------------------------

_V2_SQL = """
CREATE INDEX idx_baseline_search_path
    ON baseline_nodes(
        path_at_import, provider_uid, node_uid, source_id, tag_type
    );
CREATE INDEX idx_baseline_search_name
    ON baseline_nodes(name, provider_uid, node_uid, source_id, tag_type)
    WHERE name IS NOT NULL;
CREATE INDEX idx_baseline_search_opc_item
    ON baseline_nodes(
        opc_item_path, provider_uid, node_uid, source_id, tag_type
    )
    WHERE opc_item_path IS NOT NULL;
CREATE INDEX idx_baseline_search_source_tag
    ON baseline_nodes(
        source_tag_path, provider_uid, node_uid, source_id, tag_type
    )
    WHERE source_tag_path IS NOT NULL;
CREATE INDEX idx_baseline_search_type_id
    ON baseline_nodes(type_id, provider_uid, node_uid, source_id, tag_type)
    WHERE type_id IS NOT NULL;
CREATE INDEX idx_baseline_search_tag_type
    ON baseline_nodes(tag_type, provider_uid, node_uid, source_id)
    WHERE tag_type IS NOT NULL;
CREATE INDEX idx_baseline_search_source
    ON baseline_nodes(source_id) WHERE source_id IS NOT NULL;
"""


def _migration_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(_V2_SQL)


# ---- v3: D1 exact relacije ----------------------------------------------

_V3_SQL = """
CREATE TABLE relationships (
    relationship_uid    TEXT PRIMARY KEY,
    source_node_uid     TEXT NOT NULL,
    target_node_uid     TEXT,
    role                TEXT NOT NULL,
    state               TEXT NOT NULL,
    evidence_type       TEXT NOT NULL,
    evidence_json       TEXT NOT NULL,
    origin              TEXT NOT NULL,
    confidence          REAL,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    source_hashes_json  TEXT NOT NULL
);

CREATE INDEX idx_relationships_source
    ON relationships(source_node_uid, role, state, evidence_type);
CREATE INDEX idx_relationships_target
    ON relationships(target_node_uid, role, state, evidence_type)
    WHERE target_node_uid IS NOT NULL;
CREATE INDEX idx_relationships_state
    ON relationships(state, evidence_type, origin, relationship_uid);
CREATE INDEX idx_relationships_filter_state
    ON relationships(state, relationship_uid);
CREATE INDEX idx_relationships_filter_evidence
    ON relationships(evidence_type, relationship_uid);
CREATE INDEX idx_relationships_filter_role
    ON relationships(role, relationship_uid);
"""


def _migration_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(_V3_SQL)


# ---- v4: F1 dnevnik operacij delovne kopije -----------------------------

_V4_SQL = """
CREATE TABLE operations (
    operation_uid    TEXT PRIMARY KEY,
    seq              INTEGER NOT NULL,
    op_type          TEXT NOT NULL,
    target_node_uid  TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    original_json    TEXT NOT NULL,
    status           TEXT NOT NULL,
    reason           TEXT,
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    depends_on_json  TEXT NOT NULL,
    conflict_info    TEXT
);

CREATE INDEX idx_operations_seq
    ON operations(seq, operation_uid);
CREATE INDEX idx_operations_target
    ON operations(target_node_uid, op_type, status, seq);
CREATE INDEX idx_operations_status
    ON operations(status, seq, operation_uid);
"""


def _migration_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(_V4_SQL)


# ---- v5: G2 trajni kazalec aktivnega prefiksa operacij ------------------

def _migration_v5(conn: sqlite3.Connection) -> None:
    conn.execute(
        "ALTER TABLE project_meta ADD COLUMN operation_cursor "
        "INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "UPDATE project_meta SET operation_cursor = "
        "(SELECT COALESCE(MAX(seq), 0) FROM operations) WHERE id = 1"
    )


# Urejen seznam (verzija, funkcija). Dodaj nove migracije na konec.
MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_v1),
    (2, _migration_v2),
    (3, _migration_v3),
    (4, _migration_v4),
    (5, _migration_v5),
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
