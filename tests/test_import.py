"""Testi mejnika B2: uvoz IO/UNS/UDT JSON v nespremenljiv baseline."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from analyzer.build import sha256_file
from editor import (
    ImportSourceError,
    compute_node_uid,
    compute_provider_uid,
    create_project,
    discover_sources,
    import_source,
    list_providers,
    validate_source,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
IO = os.path.join(FIX, "tags_IO_TESTSITE_SIE.json")
UNS = os.path.join(FIX, "tags_UNS_TESTSITE.json")
UDT = os.path.join(FIX, "UDT_Definitions.json")


@pytest.fixture()
def project(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="Import test")
    yield p
    p.close()


def _row(project, path_at_import):
    return project.conn.execute(
        "SELECT * FROM baseline_nodes WHERE path_at_import=?", (path_at_import,)
    ).fetchone()


# ---- osnovni uvoz --------------------------------------------------------

def test_import_creates_baseline_and_source(project):
    res = import_source(project, IO, site="testsite")
    assert res["status"] == "imported"
    assert res["nodes"] == 6
    assert res["provider_name"] == "IO_TESTSITE_SIE"

    src = project.conn.execute("SELECT * FROM sources").fetchone()
    assert src["provider_name"] == "IO_TESTSITE_SIE"
    assert src["site"] == "testsite"
    assert src["kind"] == "io"
    assert src["sha256"] == sha256_file(IO)

    n = project.conn.execute("SELECT COUNT(*) FROM baseline_nodes").fetchone()[0]
    assert n == 6


def test_provider_and_node_identity(project):
    res = import_source(project, IO, site="testsite")
    puid = compute_provider_uid("testsite", "IO_TESTSITE_SIE", "io")
    assert res["provider_uid"] == puid

    run = _row(project, "Area1/Motor1_Run")
    assert run["node_uid"] == compute_node_uid(puid, "Area1/Motor1_Run")
    assert run["provider_uid"] == puid
    # root ima prazno pot in nima starsa
    root = _row(project, "")
    assert root["parent_uid"] is None
    assert root["tag_type"] == "Provider"
    # otrok kaze na starsa prek node_uid
    area1 = _row(project, "Area1")
    assert run["parent_uid"] == area1["node_uid"]


def test_sibling_order_preserved(project):
    import_source(project, IO, site="testsite")
    assert _row(project, "Area1")["sibling_index"] == 0
    assert _row(project, "Area2")["sibling_index"] == 1
    assert _row(project, "Area1/Motor1_Run")["sibling_index"] == 0
    assert _row(project, "Area1/Motor1_Speed")["sibling_index"] == 1


def test_raw_json_lossless(project):
    import_source(project, IO, site="testsite")
    with open(IO, encoding="utf-8") as f:
        original = json.load(f)
    motor_run = original["tags"][0]["tags"][0]  # Area1 -> Motor1_Run (leaf)
    stored = json.loads(_row(project, "Area1/Motor1_Run")["raw_json"])
    assert stored == motor_run  # brez otrok; leaf je enak

    # folder: raw_json ohrani objekt brez 'tags'
    area1_stored = json.loads(_row(project, "Area1")["raw_json"])
    expected = {k: v for k, v in original["tags"][0].items() if k != "tags"}
    assert area1_stored == expected
    assert "tags" not in area1_stored


def test_source_tag_path_flattened_and_preserved(project):
    import_source(project, IO, site="testsite")
    ref = _row(project, "Area2/Ref")
    assert ref["source_tag_path"] == "[~]Area1/Motor1_Speed"
    # celoten binding objekt ostane v raw_json
    raw = json.loads(ref["raw_json"])
    assert raw["sourceTagPath"] == {"bindType": "Tag", "binding": "[~]Area1/Motor1_Speed"}


def test_opc_fields_extracted(project):
    import_source(project, IO, site="testsite")
    run = _row(project, "Area1/Motor1_Run")
    assert run["opc_item_path"] == "ns=1;s=[PLC]M1.Run"
    assert run["opc_server"] == "Ignition OPC UA Server"
    assert run["data_type"] == "Boolean"


# ---- nespremenljivost virov ----------------------------------------------

def test_source_file_immutable(project):
    before = sha256_file(IO)
    import_source(project, IO, site="testsite")
    assert sha256_file(IO) == before


# ---- vec providerjev -----------------------------------------------------

def test_import_multiple_providers_no_collision(project):
    import_source(project, IO, site="testsite")
    import_source(project, UNS, site="testsite")
    import_source(project, UDT, site="testsite")

    provs = list_providers(project)
    names = {p["provider_name"]: p for p in provs}
    assert set(names) == {"IO_TESTSITE_SIE", "UNS_TESTSITE", "UDT_testsite"}
    assert names["IO_TESTSITE_SIE"]["node_count"] == 6
    assert names["UNS_TESTSITE"]["node_count"] == 5
    assert names["UDT_testsite"]["node_count"] == 4

    total = project.conn.execute("SELECT COUNT(*) FROM baseline_nodes").fetchone()[0]
    distinct = project.conn.execute(
        "SELECT COUNT(DISTINCT node_uid) FROM baseline_nodes"
    ).fetchone()[0]
    assert total == distinct == 15  # brez trkov node_uid med providerji


def test_udt_instance_type_id_captured(project):
    import_source(project, UNS, site="testsite")
    line1 = _row(project, "Site/Line1")
    assert line1["tag_type"] == "UdtInstance"
    assert line1["type_id"] == "MotorUDT"


# ---- idempotentnost / zamenjava / podvojitve -----------------------------

def test_unchanged_reimport_is_idempotent(project):
    r1 = import_source(project, IO, site="testsite")
    assert r1["status"] == "imported"
    r2 = import_source(project, IO, site="testsite")
    assert r2["status"] == "unchanged"
    assert project.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    assert project.conn.execute(
        "SELECT COUNT(*) FROM baseline_nodes").fetchone()[0] == 6


def test_changed_reimport_replaces(project, tmp_path):
    src = str(tmp_path / "tags_IO_TESTSITE_SIE.json")
    shutil.copyfile(IO, src)
    r1 = import_source(project, src, site="testsite")
    assert r1["nodes"] == 6
    # spremeni vir: dodaj tag v Area2
    with open(src, encoding="utf-8") as f:
        obj = json.load(f)
    obj["tags"][1]["tags"].append(
        {"name": "Extra", "tagType": "AtomicTag", "dataType": "Int4"}
    )
    with open(src, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    r2 = import_source(project, src, site="testsite")
    assert r2["status"] == "reimported"
    assert r2["nodes"] == 7
    assert project.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    assert project.conn.execute(
        "SELECT COUNT(*) FROM baseline_nodes").fetchone()[0] == 7


def test_duplicate_provider_from_different_path_raises(project, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pa = str(a / "tags_IO_TESTSITE_SIE.json")
    pb = str(b / "tags_IO_TESTSITE_SIE.json")
    shutil.copyfile(IO, pa)
    shutil.copyfile(IO, pb)
    import_source(project, pa, site="testsite")
    with pytest.raises(ImportSourceError):
        import_source(project, pb, site="testsite")


# ---- napake --------------------------------------------------------------

def test_invalid_json_raises(project, tmp_path):
    bad = str(tmp_path / "tags_IO_BAD.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{ not valid json ")
    with pytest.raises(ImportSourceError):
        import_source(project, bad, site="testsite")


def test_unknown_provider_pattern_raises(project, tmp_path):
    weird = str(tmp_path / "nekaj.json")
    with open(weird, "w", encoding="utf-8") as f:
        json.dump({"name": "", "tagType": "Provider", "tags": []}, f)
    with pytest.raises(ImportSourceError):
        import_source(project, weird, site="testsite")


# ---- odkrivanje / validacija ---------------------------------------------

def test_discover_sources_classifies(tmp_path):
    found = discover_sources(FIX, site="testsite")
    by_name = {d["provider_name"]: d for d in found}
    assert "IO_TESTSITE_SIE" in by_name and by_name["IO_TESTSITE_SIE"]["kind"] == "io"
    assert "UNS_TESTSITE" in by_name and by_name["UNS_TESTSITE"]["kind"] == "uns"
    assert "UDT_testsite" in by_name and by_name["UDT_testsite"]["kind"] == "udt"


def test_validate_source_ok_and_bad(tmp_path):
    ok = validate_source(IO, site="testsite")
    assert ok["ok"] and ok["provider_name"] == "IO_TESTSITE_SIE"
    assert ok["root_tag_type"] == "Provider"

    bad = str(tmp_path / "tags_IO_BAD.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{ broken ")
    res = validate_source(bad, site="testsite")
    assert not res["ok"] and res["issues"]
