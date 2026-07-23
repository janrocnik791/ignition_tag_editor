"""Testi za read-only Ignition tag analizator."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from analyzer.build import build_index
from analyzer.query import search, get_raw, stats


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


# --- 1. Read-only jamstva -------------------------------------------------

def test_raw_files_unchanged(built_db, fixtures_raw, raw_hashes):
    """Gradnja ne sme spremeniti nobene fixture datoteke."""
    after = {n: _sha(os.path.join(fixtures_raw, n)) for n in raw_hashes}
    assert after == raw_hashes


def test_refuses_db_under_raw(tmp_path, fixtures_raw):
    """Zapis DB pod raw mapo mora biti zavrnjen."""
    bad_db = os.path.join(fixtures_raw, "sub", "index.sqlite")
    with pytest.raises(ValueError):
        build_index(fixtures_raw, bad_db)
    assert not os.path.exists(bad_db)


# --- 2. Drevo in poti -----------------------------------------------------

def test_full_path_and_tree(conn):
    row = conn.execute(
        "SELECT id, parent_id, depth, full_path FROM tags "
        "WHERE name = 'M_T001_NAVOR'"
    ).fetchone()
    assert row is not None
    _id, parent_id, depth, full_path = row
    assert full_path == "SIE_TEST/M_T001_NAVOR"
    assert depth == 2  # Provider(0) -> SIE_TEST(1) -> tag(2)
    parent_name = conn.execute(
        "SELECT name FROM tags WHERE id = ?", (parent_id,)
    ).fetchone()[0]
    assert parent_name == "SIE_TEST"


def test_root_path_equals_name(conn):
    """Koren nima prednika; full_path je enak lastnemu imenu.

    IO in UNS izvoza imata koren s praznim imenom (""), UDT izvoz pa '_types_'.
    """
    roots = conn.execute(
        "SELECT name, full_path FROM tags WHERE parent_id IS NULL"
    ).fetchall()
    assert len(roots) == 3
    assert all(name == full_path for name, full_path in roots)
    assert {r[1] for r in roots} == {"", "_types_"}


# --- 3. Ohranitev surovih lastnosti --------------------------------------

def test_raw_properties_preserved(conn):
    raw = conn.execute(
        "SELECT raw_properties FROM tags WHERE name = 'M_T001_NAVOR'"
    ).fetchone()[0]
    obj = json.loads(raw)
    assert obj["customUnknownProp"] == "ohrani_me"  # nepoznana lastnost ostane
    assert "tags" not in obj  # otroci so loceni zapisi
    assert obj["opcItemPath"] == "ns=1;s=[SIE_TEST]DB1,REAL0"


def test_unicode_preserved(conn):
    raw = conn.execute(
        "SELECT documentation FROM tags WHERE name = 'M_T001_NAVOR'"
    ).fetchone()[0]
    assert "čžš" in raw


# --- 4. Ekstrakcija polj --------------------------------------------------

def test_io_extraction(conn):
    row = conn.execute(
        "SELECT data_type, value_source, opc_item_path, opc_server "
        "FROM tags WHERE name = 'M_T001_NAVOR'"
    ).fetchone()
    assert row == (
        "Float4", "opc", "ns=1;s=[SIE_TEST]DB1,REAL0", "Ignition OPC UA Server"
    )


def test_udt_instance_typeid(conn):
    tid = conn.execute(
        "SELECT type_id FROM tags WHERE name = 'DIC2030'"
    ).fetchone()[0]
    assert tid == "Meritev"


def test_source_tag_path_flattened(conn):
    """sourceTagPath objekt mora biti splosen na 'binding' niz."""
    stp = conn.execute(
        "SELECT source_tag_path FROM tags WHERE name = 'Sifra'"
    ).fetchone()[0]
    assert stp == "[IO_TEST_SIE]urejeni_tagi/Porocilni_tagi/SIF_L{Linija}"
    # celoten objekt ostane v raw_properties
    raw = json.loads(conn.execute(
        "SELECT raw_properties FROM tags WHERE name = 'Sifra'"
    ).fetchone()[0])
    assert raw["sourceTagPath"]["bindType"] == "parameter"


# --- 5. Iskanje po vseh zahtevanih poljih --------------------------------

def test_search_fullpath(conn):
    res = search(conn, "fullPath", "SIE_TEST/M_T001", mode="prefix")
    assert res["total"] == 1
    assert res["sample"][0]["name"] == "M_T001_NAVOR"


def test_search_name_exact(conn):
    res = search(conn, "name", "DIC2040", mode="exact")
    assert res["total"] == 1


def test_search_opcitempath_contains(conn):
    res = search(conn, "opcItemPath", "DB1,REAL0", mode="contains")
    assert res["total"] == 2  # deljen med dvema tagoma


def test_search_sourcetagpath(conn):
    res = search(conn, "sourceTagPath", "DRUG_PROVIDER", mode="contains")
    assert res["total"] == 1
    assert res["sample"][0]["name"] == "Zunanji"


def test_search_typeid(conn):
    res = search(conn, "typeId", "Meritev", mode="exact")
    assert res["total"] == 2


def test_search_invalid_field(conn):
    with pytest.raises(ValueError):
        search(conn, "nekaj", "x")


# --- 6. Statistike struktur ----------------------------------------------

def test_stat_tagtype_counts(conn):
    s = stats(conn)
    by = {t["tag_type"]: t["count"] for t in s["tag_types"]}
    assert by.get("UdtInstance") == 2
    assert by.get("Provider") == 2  # IO in UNS koren
    assert by.get("AtomicTag", 0) >= 7


def test_override_shape_diversity_not_flagged_as_error(conn):
    """typeId 'Meritev' ima dve serializirani obliki instanc (override), NI napaka."""
    s = stats(conn)
    div = {i["type_id"]: i for i in s["override_shape_diversity"]}
    assert "Meritev" in div
    assert div["Meritev"]["shapes"] == 2
    assert div["Meritev"]["instances"] == 2


def test_opc_multiplicity(conn):
    s = stats(conn)
    assert s["opc_shared_paths"] == 1
    assert s["opc_max_sharing"] == 2


def test_missing_member_not_error(conn):
    """DIC2040 brez SetPoint je veljaven zapis, ne napaka."""
    members = conn.execute(
        "SELECT name FROM tags WHERE parent_id = "
        "(SELECT id FROM tags WHERE name = 'DIC2040')"
    ).fetchall()
    names = {m[0] for m in members}
    assert names == {"Meritev", "Alarm_H"}


# --- 7. Robni primeri -----------------------------------------------------

def test_folder_without_tags(conn):
    row = conn.execute(
        "SELECT tag_type FROM tags WHERE name = 'Prazna'"
    ).fetchone()
    assert row[0] == "Folder"
    children = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE parent_id = "
        "(SELECT id FROM tags WHERE name = 'Prazna')"
    ).fetchone()[0]
    assert children == 0


def test_external_provider_is_data_not_error(conn):
    """Zunanji provider [DRUG_PROVIDER] je zgolj podatek, ne poskodovana ref."""
    stp = conn.execute(
        "SELECT source_tag_path FROM tags WHERE name = 'Zunanji'"
    ).fetchone()[0]
    assert stp.startswith("[DRUG_PROVIDER]")


def test_get_raw_roundtrip(conn):
    tid = conn.execute(
        "SELECT id FROM tags WHERE name = 'M_T001_NAVOR'"
    ).fetchone()[0]
    raw = json.loads(get_raw(conn, tid))
    assert raw["name"] == "M_T001_NAVOR"


# --- 8. Idempotentnost ----------------------------------------------------

def test_rebuild_idempotent(tmp_path, fixtures_raw):
    db1 = str(tmp_path / "a.sqlite")
    db2 = str(tmp_path / "b.sqlite")
    s1 = build_index(fixtures_raw, db1)
    s2 = build_index(fixtures_raw, db2)
    assert s1 == s2
