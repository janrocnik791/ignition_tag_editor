"""Testi Faze 1: uvoz referencnih CSV in model pricakovanega stanja.

Uporablja majhne sinteticne CP1250 fixture pod tests/fixtures/reference/
(brez zaupnih realnih podatkov).
"""

from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

from analyzer.build import sha256_file
from analyzer.reference.importer import (
    build_reference_index,
    import_source,
)
from analyzer.reference.query import get_expected_state, get_issues, list_sources
from analyzer.reference.schema import create_reference_schema
from analyzer.reference.validate import validate_reference

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "reference")


def _fix(name: str) -> str:
    return os.path.join(FIX, name)


def _toplevel_csvs():
    return sorted(
        os.path.join(FIX, n) for n in os.listdir(FIX)
        if n.lower().endswith(".csv")
    )


def _codes(conn):
    return {r[0] for r in conn.execute("SELECT DISTINCT code FROM import_issues")}


@pytest.fixture()
def ref_db(tmp_path):
    """Svez indeks iz vrhnjih fixture datotek (site=stahovica) + navzkrizne provere."""
    db = str(tmp_path / "ref.sqlite")
    conn = sqlite3.connect(db)
    create_reference_schema(conn)
    for path in _toplevel_csvs():
        import_source(conn, path, site="stahovica")
    validate_reference(conn)
    yield conn
    conn.close()


# --- Viri / fingerprint ---------------------------------------------------

def test_sources_fingerprinted(ref_db):
    sources = list_sources(ref_db)
    assert len(sources) == len(_toplevel_csvs())
    by_name = {s["filename"]: s for s in sources}
    for path in _toplevel_csvs():
        name = os.path.basename(path)
        assert by_name[name]["sha256"] == sha256_file(path)


def test_source_files_immutable(ref_db):
    # branje/uvoz ne sme spremeniti izvornih datotek
    for path in _toplevel_csvs():
        assert os.path.exists(path)
        # sha256 se ujema s tem, kar je v bazi (torej datoteka nespremenjena)
    src = {s["filename"]: s["sha256"] for s in list_sources(ref_db)}
    for path in _toplevel_csvs():
        assert src[os.path.basename(path)] == sha256_file(path)


# --- Veljaven uvoz / poizvedba -------------------------------------------

def test_valid_import_groups_members(ref_db):
    res = get_expected_state(ref_db, "stahovica", "L900", tech="10")
    types = {g["group_type"] for g in res["groups"]}
    assert "Meritev" in types and "Motorji" in types
    # najdi Meritev/T
    mer = [g for g in res["groups"]
           if g["group_type"] == "Meritev" and g["prefix"] == "T"][0]
    names = {m["member_key"]: m["expected_name"] for m in mer["members"]}
    assert names["PV"] == "T10_PV" and names["SP"] == "T10_SP"
    mot = [g for g in res["groups"] if g["group_type"] == "Motorji"][0]
    mnames = {m["member_key"]: m["expected_name"] for m in mot["members"]}
    assert mnames["RUN"] == "M10_RUN" and mnames["RDY"] == "M10_RDY"


def test_unicode_slovenian_preserved(ref_db):
    res = get_expected_state(ref_db, "stahovica", "L900", tech="10")
    assert res["groups"][0]["description"] == "POLŽASTI TRANSPORTER"
    res11 = get_expected_state(ref_db, "stahovica", "L900", tech="11")
    assert res11["groups"][0]["description"] == "MOTOR Š"


def test_continuation_row_inherits_tech(ref_db):
    # Meritev/NAVOR izhaja iz nadaljevalne vrstice (prazen Stroj ID) stroja 10.
    res = get_expected_state(ref_db, "stahovica", "L900", tech="10")
    navor = [g for g in res["groups"] if g["prefix"] == "NAVOR"]
    assert navor and navor[0]["group_type"] == "Meritev"
    assert navor[0]["members"][0]["expected_name"] == "M10_NAVOR"
    # in podeduje opis stroja
    assert navor[0]["description"] == "POLŽASTI TRANSPORTER"


def test_provenance_accuracy(ref_db):
    res = get_expected_state(ref_db, "stahovica", "L900", tech="10")
    mer = [g for g in res["groups"]
           if g["group_type"] == "Meritev" and g["prefix"] == "T"][0]
    pv = [m for m in mer["members"] if m["member_key"] == "PV"][0]
    prov = pv["provenance"]
    assert prov["file"] == "ref(L900).csv"
    assert prov["col_header"] == "PV"
    assert prov["row"] == 2  # 1=glava, 2=prva podatkovna vrstica
    assert prov["sha256"] == sha256_file(_fix("ref(L900).csv"))


# --- Preskoceni tipi vrstic (sledljivo) -----------------------------------

def test_blank_and_repeated_header_skipped(ref_db):
    codes = _codes(ref_db)
    assert "REF_BLANK_ROW" in codes
    assert "REF_REPEATED_HEADER" in codes
    # nobena skupina ne sme nastati iz ponovljene glave/prazne vrstice:
    # stroj 10 ima natanko 3 Meritev sklope (T, TOK? ne -> T + NAVOR) + Motor
    res = get_expected_state(ref_db, "stahovica", "L900", tech="10")
    prefixes = sorted(g["prefix"] for g in res["groups"])
    assert prefixes == ["M", "NAVOR", "T"]


# --- Napake / neznano -----------------------------------------------------

def test_missing_required_field(ref_db):
    iss = get_issues(ref_db, code="REF_MISSING_REQUIRED_FIELD")
    assert iss and all(i["severity"] == "ERROR" for i in iss)
    # zavrnjena vrstica ostane sledljiva -> ne ustvari skupine brez tech
    res = get_expected_state(ref_db, "stahovica", "L901")
    assert all(g["tech_number"] for g in res["groups"])


def test_unknown_group_type(ref_db):
    iss = get_issues(ref_db, code="REF_UNKNOWN_GROUP_TYPE")
    assert iss and any(i["canonical_key"] == "Grelci" for i in iss)
    assert all(i["severity"] == "ERROR" for i in iss)


def test_unknown_column_preserved(ref_db):
    iss = get_issues(ref_db, code="REF_UNKNOWN_COLUMN")
    assert iss and all(i["severity"] == "INFO" for i in iss)
    # neznani stolpec ostane v raw_row_json skupine
    row = ref_db.execute(
        "SELECT raw_row_json FROM expected_groups g "
        "JOIN reference_sources s ON s.id=g.source_id "
        "WHERE s.line='L901' LIMIT 1"
    ).fetchone()
    assert row and "RandomCol" in row[0]


# --- Podvojitve / konflikti / lokacije ------------------------------------

def test_duplicate_row(ref_db):
    iss = get_issues(ref_db, code="REF_DUPLICATE_ROW")
    assert iss and all(i["severity"] == "WARNING" for i in iss)


def test_conflict_row(ref_db):
    iss = get_issues(ref_db, code="REF_CONFLICT")
    assert iss and all(i["severity"] == "ERROR" for i in iss)
    assert any("L902" in (i["canonical_key"] or "") for i in iss)


def test_location_difference(tmp_path):
    db = str(tmp_path / "loc.sqlite")
    conn = sqlite3.connect(db)
    create_reference_schema(conn)
    import_source(conn, _fix(os.path.join("stahovica", "ref(L950).csv")), site="stahovica")
    import_source(conn, _fix(os.path.join("gospic", "ref(L950).csv")), site="gospic")
    validate_reference(conn)
    iss = get_issues(conn, code="REF_LOCATION_DIFFERENCE")
    conn.close()
    assert iss and all(i["severity"] == "INFO" for i in iss)
    # razlike med lokacijami ostanejo eksplicitne (obe lokaciji imenovani)
    assert any("gospic" in i["message"] and "stahovica" in i["message"] for i in iss)


def test_shared_expected_name_is_info(tmp_path):
    # dve razlicni vlogi z istim pricakovanim imenom -> INFO (deljena referenca)
    p = str(tmp_path / "ref(L960).csv")
    with open(p, "w", encoding="cp1250", newline="") as f:
        f.write("Stroj ID (Maximo);Stroj ID (nov);Stroj opis;"
                "Meritev (prefix);PV;SP;Opomba;Regulatorji (prefix);SP;PV;Opomba\n")
        f.write("50;50;X;T;T50_PV;SHARED_NAME;;R;R50_SP;SHARED_NAME;\n")
    conn = sqlite3.connect(str(tmp_path / "s.sqlite"))
    create_reference_schema(conn)
    import_source(conn, p, site="stahovica")
    validate_reference(conn)
    iss = get_issues(conn, code="REF_SHARED_EXPECTED_NAME")
    hard = get_issues(conn, code="REF_CONFLICT")
    conn.close()
    assert iss and all(i["severity"] == "INFO" for i in iss)
    assert not hard  # deljeno ime ni trd konflikt


# --- Predloga (Novo poimenovanje) -----------------------------------------

def test_member_template_parsed(ref_db):
    rows = ref_db.execute(
        "SELECT group_type, member_key FROM member_templates ORDER BY ordinal"
    ).fetchall()
    pairs = {(r[0], r[1]) for r in rows}
    assert ("Meritev", "PV") in pairs and ("Meritev", "SP") in pairs
    # predloga ne sme ustvariti expected_groups
    tmpl_groups = ref_db.execute(
        "SELECT COUNT(*) FROM expected_groups g JOIN reference_sources s "
        "ON s.id=g.source_id WHERE s.profile_id='template_v1'"
    ).fetchone()[0]
    assert tmpl_groups == 0


# --- Legenda --------------------------------------------------------------

def test_legend_parsed(ref_db):
    rows = ref_db.execute(
        "SELECT group_type, kind, old_token, new_token, meaning "
        "FROM legend_entries ORDER BY source_col_index"
    ).fetchall()
    tuples = {(r[0], r[1], r[2], r[3]) for r in rows}
    assert ("Meritve", "prefix", "DP", "DIC") in tuples
    assert ("Meritve", "suffix", "PLL", "PV") in tuples
    assert ("Stikala", "prefix", "DI", "DIN") in tuples


# --- Neznan profil --------------------------------------------------------

def test_unknown_sheet_profile(tmp_path):
    p = str(tmp_path / "ref(NekajCudnega).csv")
    with open(p, "w", encoding="cp1250", newline="") as f:
        f.write("a;b;c\n1;2;3\n")
    conn = sqlite3.connect(str(tmp_path / "u.sqlite"))
    create_reference_schema(conn)
    import_source(conn, p, site="stahovica")
    codes = _codes(conn)
    conn.close()
    assert "REF_UNKNOWN_SHEET_PROFILE" in codes


# --- Idempotentnost / zamenjava -------------------------------------------

def test_unchanged_reimport_is_idempotent(tmp_path):
    db = str(tmp_path / "i.sqlite")
    conn = sqlite3.connect(db)
    create_reference_schema(conn)
    r1 = import_source(conn, _fix("ref(L900).csv"), site="stahovica")
    assert r1["status"] == "imported"
    n_groups_1 = conn.execute("SELECT COUNT(*) FROM expected_groups").fetchone()[0]
    r2 = import_source(conn, _fix("ref(L900).csv"), site="stahovica")
    assert r2["status"] == "unchanged"
    n_groups_2 = conn.execute("SELECT COUNT(*) FROM expected_groups").fetchone()[0]
    assert n_groups_1 == n_groups_2
    n_sources = conn.execute("SELECT COUNT(*) FROM reference_sources").fetchone()[0]
    conn.close()
    assert n_sources == 1


def test_changed_reimport_replaces(tmp_path):
    src = str(tmp_path / "ref(L900).csv")
    shutil.copyfile(_fix("ref(L900).csv"), src)
    db = str(tmp_path / "c.sqlite")
    conn = sqlite3.connect(db)
    create_reference_schema(conn)
    r1 = import_source(conn, src, site="stahovica")
    sha1 = r1["sha256"]
    n1 = conn.execute("SELECT COUNT(*) FROM expected_groups").fetchone()[0]
    # spremeni vir (dodaj stroj)
    with open(src, "a", encoding="cp1250", newline="") as f:
        f.write("99;99;NOVI;;;;;M;M99_RUN;M99_RDY;;;;;\n")
    r2 = import_source(conn, src, site="stahovica")
    assert r2["status"] == "replaced"
    assert r2["previous_sha256"] == sha1
    assert r2["sha256"] != sha1
    n2 = conn.execute("SELECT COUNT(*) FROM expected_groups").fetchone()[0]
    n_sources = conn.execute("SELECT COUNT(*) FROM reference_sources").fetchone()[0]
    prev = conn.execute(
        "SELECT previous_sha256 FROM reference_sources").fetchone()[0]
    conn.close()
    assert n2 == n1 + 1  # dodan stroj 99
    assert n_sources == 1  # zamenjava, ne nov vir
    assert prev == sha1


# --- Determinizem ---------------------------------------------------------

def _dump(db_path):
    conn = sqlite3.connect(db_path)
    out = []
    for tbl, cols in [
        ("expected_groups", "site,line,tech_number,group_type,prefix,source_row"),
        ("expected_members", "group_id,member_key,expected_name,source_row,source_col_index"),
        ("import_issues", "severity,code,source_row,source_col_index,canonical_key"),
    ]:
        rows = conn.execute(
            f"SELECT {cols} FROM {tbl} ORDER BY {cols}").fetchall()
        out.append((tbl, rows))
    conn.close()
    return out


def test_deterministic_output(tmp_path):
    # zgradi dvakrat iz iste kurirane mape -> identicen izpis
    curated = tmp_path / "mappings"
    curated.mkdir()
    for path in _toplevel_csvs():
        shutil.copyfile(path, curated / os.path.basename(path))
    db1 = str(tmp_path / "d1.sqlite")
    db2 = str(tmp_path / "d2.sqlite")
    build_reference_index(str(curated), db1, site="stahovica")
    build_reference_index(str(curated), db2, site="stahovica")
    assert _dump(db1) == _dump(db2)


# --- build_reference_index (CLI pot) --------------------------------------

def test_build_reference_index_read_only(tmp_path):
    curated = tmp_path / "mappings"
    curated.mkdir()
    before = {}
    for path in _toplevel_csvs():
        dst = curated / os.path.basename(path)
        shutil.copyfile(path, dst)
        before[str(dst)] = sha256_file(str(dst))
    db = str(tmp_path / "b.sqlite")
    summary = build_reference_index(str(curated), db, site="stahovica")
    assert summary["sources"] == len(before)
    # viri nespremenjeni
    for p, sha in before.items():
        assert sha256_file(p) == sha
