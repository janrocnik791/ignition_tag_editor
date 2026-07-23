"""SQLite shema in gradnja materializiranih statistik."""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE files (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL,
    site          TEXT,
    kind          TEXT,
    root_tag_type TEXT,
    size_bytes    INTEGER,
    sha256        TEXT,
    node_count    INTEGER,
    indexed_at    TEXT
);

CREATE TABLE tags (
    id               INTEGER PRIMARY KEY,
    file_id          INTEGER NOT NULL REFERENCES files(id),
    parent_id        INTEGER REFERENCES tags(id),
    depth            INTEGER,
    full_path        TEXT,
    name             TEXT,
    tag_type         TEXT,
    data_type        TEXT,
    value_source     TEXT,
    type_id          TEXT,
    opc_item_path    TEXT,
    opc_server       TEXT,
    source_tag_path  TEXT,
    documentation    TEXT,
    member_signature TEXT,
    raw_properties   TEXT NOT NULL
);

-- Indeksi za zahtevana iskalna polja.
CREATE INDEX idx_tags_full_path       ON tags(full_path);
CREATE INDEX idx_tags_name            ON tags(name);
CREATE INDEX idx_tags_opc_item_path   ON tags(opc_item_path);
CREATE INDEX idx_tags_source_tag_path ON tags(source_tag_path);
CREATE INDEX idx_tags_type_id         ON tags(type_id);
CREATE INDEX idx_tags_tag_type        ON tags(tag_type);
CREATE INDEX idx_tags_parent          ON tags(parent_id);
CREATE INDEX idx_tags_file            ON tags(file_id);
"""

# Materializirane statistike struktur; zgrajene po vstavljanju vseh tagov.
STATS_SQL = """
DROP TABLE IF EXISTS stat_tagtype;
CREATE TABLE stat_tagtype AS
    SELECT file_id, tag_type, COUNT(*) AS cnt
    FROM tags GROUP BY file_id, tag_type;

DROP TABLE IF EXISTS stat_datatype;
CREATE TABLE stat_datatype AS
    SELECT data_type, COUNT(*) AS cnt
    FROM tags WHERE data_type IS NOT NULL GROUP BY data_type;

-- Za vsak typeId in serializirano strukturo clanov INSTANCE: koliko instanc
-- jo deli + primer poti. Vec oblik za isti typeId NI napaka -- Ignition
-- instance pogosto serializirajo le lokalne override, ne celotne definicije.
-- (Locitev od UdtType definicij je namerna, sicer se stetja pomesajo.)
DROP TABLE IF EXISTS udt_structures;
CREATE TABLE udt_structures AS
    SELECT type_id,
           member_signature,
           COUNT(*)        AS instance_count,
           MIN(full_path)  AS example_full_path
    FROM tags
    WHERE tag_type = 'UdtInstance' AND type_id IS NOT NULL AND type_id <> ''
    GROUP BY type_id, member_signature;

-- En opcItemPath je lahko na vec tagih (potrjeno pravilo, ne napaka).
DROP TABLE IF EXISTS opc_multiplicity;
CREATE TABLE opc_multiplicity AS
    SELECT opc_item_path, COUNT(*) AS tag_count
    FROM tags
    WHERE opc_item_path IS NOT NULL
    GROUP BY opc_item_path
    HAVING COUNT(*) > 1;
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def build_stats(conn: sqlite3.Connection) -> None:
    conn.executescript(STATS_SQL)
