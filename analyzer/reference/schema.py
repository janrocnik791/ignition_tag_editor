"""SQLite shema za referencni model pricakovanega stanja (Faza 1).

Locena baza od Phase 0 tag indeksa. Gradnja je deterministicna: staro bazo
odstranimo in ustvarimo znova iz staticnega SCHEMA niza.
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
-- Ena vrstica na uvozeno CSV datoteko (list delovnega zvezka).
CREATE TABLE reference_sources (
    id             INTEGER PRIMARY KEY,
    path           TEXT NOT NULL,
    filename       TEXT NOT NULL,
    sheet_name     TEXT,
    site           TEXT,
    line           TEXT,
    profile_id     TEXT,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER,
    header_json    TEXT,          -- originalni glave, dobesedno
    row_count      INTEGER DEFAULT 0,
    group_count    INTEGER DEFAULT 0,
    member_count   INTEGER DEFAULT 0,
    issue_count    INTEGER DEFAULT 0,
    previous_sha256 TEXT,         -- <> NULL => zamenjava spremenjenega vira
    imported_at    TEXT
);

-- Pricakovana naprava/sklop v liniji (zrno: en tehnoloski blok v vrstici).
CREATE TABLE expected_groups (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES reference_sources(id),
    site         TEXT,
    line         TEXT,
    tech_number  TEXT,            -- Stroj ID (nov)
    maximo_id    TEXT,            -- Stroj ID (Maximo/star)
    description  TEXT,            -- Stroj opis
    group_type   TEXT,            -- Meritev|Stikala|Ventili|Motorji|Regulatorji|Filtri
    prefix       TEXT,            -- vrednost prefix celice (npr. TOK, M, V)
    source_row   INTEGER,         -- 1-osnovana vrstica v CSV
    raw_row_json TEXT,            -- celotna originalna vrstica (glava -> celica)
    status       TEXT
);

-- Pricakovani clan sklopa (ime taga v celici).
CREATE TABLE expected_members (
    id                INTEGER PRIMARY KEY,
    group_id          INTEGER NOT NULL REFERENCES expected_groups(id),
    source_id         INTEGER NOT NULL REFERENCES reference_sources(id),
    member_key        TEXT,       -- glava stolpca (PV, RUN, ...)
    expected_name     TEXT,       -- vrednost celice (pricakovano ime taga)
    required          INTEGER,    -- NULL = neznano (obveznost ni v tabeli)
    note              TEXT,
    source_row        INTEGER,
    source_col_index  INTEGER,
    source_col_header TEXT,
    raw_value         TEXT
);

-- Linijski custom tagi (blok Custom + Star tag + Nov tag).
CREATE TABLE expected_line_tags (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES reference_sources(id),
    site         TEXT,
    line         TEXT,
    label        TEXT,            -- stolpec Custom (npr. "START SEKVENCE")
    old_name     TEXT,            -- Star tag
    new_name     TEXT,            -- Nov tag
    note         TEXT,
    source_row   INTEGER,
    raw_row_json TEXT
);

-- Legenda: preslikava prefiksov/sufiksov (staro -> novo + pomen).
CREATE TABLE legend_entries (
    id               INTEGER PRIMARY KEY,
    source_id        INTEGER NOT NULL REFERENCES reference_sources(id),
    group_type       TEXT,
    kind             TEXT,        -- prefix|suffix
    old_token        TEXT,
    new_token        TEXT,
    meaning          TEXT,
    source_row       INTEGER,
    source_col_index INTEGER
);

-- Kanonicni nabor clanov na tip sklopa (iz lista "Novo poimenovanje").
CREATE TABLE member_templates (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES reference_sources(id),
    group_type  TEXT,
    member_key  TEXT,
    ordinal     INTEGER
);

-- Zavrnjene/konfliktne vrstice; nic ne izgine tiho.
CREATE TABLE import_issues (
    id               INTEGER PRIMARY KEY,
    source_id        INTEGER REFERENCES reference_sources(id),
    severity         TEXT,
    code             TEXT,
    sheet            TEXT,
    source_row       INTEGER,
    source_col_index INTEGER,
    canonical_key    TEXT,
    message          TEXT,
    raw_context_json TEXT
);

CREATE INDEX idx_eg_site_line_tech ON expected_groups(site, line, tech_number);
CREATE INDEX idx_eg_group_type     ON expected_groups(group_type);
CREATE INDEX idx_eg_source         ON expected_groups(source_id);
CREATE INDEX idx_em_expected_name  ON expected_members(expected_name);
CREATE INDEX idx_em_group          ON expected_members(group_id);
CREATE INDEX idx_lt_site_line      ON expected_line_tags(site, line);
CREATE INDEX idx_leg_source        ON legend_entries(source_id);
CREATE INDEX idx_mt_group_type     ON member_templates(group_type);
CREATE INDEX idx_issue_code        ON import_issues(code);
CREATE INDEX idx_issue_source      ON import_issues(source_id);
"""


def create_reference_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
