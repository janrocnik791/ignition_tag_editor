"""Testi validatorja: kategorije ERROR/WARNING/INFO in semantika override."""

from __future__ import annotations

import json
import os

import pytest

from analyzer.validate import validate, check_json_parseable, CODE_SEVERITY
from analyzer.udt_resolver import build_registry, braces_balanced, param_tokens


def codes(findings):
    out = {}
    for f in findings:
        out.setdefault(f.code, []).append(f)
    return out


@pytest.fixture()
def findings(vconn, validate_db):
    return validate(vconn, raw_paths=None)


# --- Resolver / dedovanje -------------------------------------------------

def test_effective_members_inheritance(vconn):
    reg = build_registry(vconn)
    # Child deduje od Base: efektivni clani = A,B (Base) + C,D,E (Child)
    site = next(s for (s, k) in reg.canonical if k == "Child")
    eff = reg.effective_members(site, "Child")
    assert eff == {"A", "B", "C", "D", "E"}
    # parametri se dedujejo
    assert reg.effective_params(site, "Child") == {"Linija"}


# --- ERROR ----------------------------------------------------------------

def test_unknown_udt_type(findings):
    c = codes(findings)
    assert "UNKNOWN_UDT_TYPE" in c
    assert any(f.typeId == "Ghost" for f in c["UNKNOWN_UDT_TYPE"])
    assert all(f.severity == "ERROR" for f in c["UNKNOWN_UDT_TYPE"])


def test_missing_parent_udt(findings):
    c = codes(findings)
    assert "MISSING_PARENT_UDT" in c
    assert any(f.relatedPath == "NeobstojecParent" for f in c["MISSING_PARENT_UDT"])


def test_inheritance_cycle(findings):
    c = codes(findings)
    assert "UDT_INHERITANCE_CYCLE" in c


def test_duplicate_definition(findings):
    c = codes(findings)
    assert "DUPLICATE_UDT_DEFINITION" in c
    assert any(f.typeId == "Dup" for f in c["DUPLICATE_UDT_DEFINITION"])


def test_invalid_path_template(findings):
    c = codes(findings)
    assert "INVALID_PATH_TEMPLATE" in c
    assert any(f.fullPath.endswith("/E") for f in c["INVALID_PATH_TEMPLATE"])


def test_invalid_json_detection(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{ not valid json ", encoding="utf-8")
    out = check_json_parseable([str(bad)])
    assert len(out) == 1 and out[0].code == "INVALID_JSON"
    assert out[0].severity == "ERROR"


# --- WARNING --------------------------------------------------------------

def test_empty_type_id_nested(findings):
    c = codes(findings)
    assert "EMPTY_TYPE_ID" in c
    # Sub je gnezdena instanca brez typeId
    sub = [f for f in c["EMPTY_TYPE_ID"] if f.fullPath.endswith("/Sub")]
    assert sub and sub[0].severity == "WARNING"
    assert "Gnezden" in sub[0].explanation


def test_unresolved_parameter(findings):
    c = codes(findings)
    assert "UNRESOLVED_PARAMETER" in c
    assert any("Neznan" in f.evidence for f in c["UNRESOLVED_PARAMETER"])


def test_instance_member_not_in_definition(findings):
    c = codes(findings)
    assert "INSTANCE_MEMBER_NOT_IN_DEFINITION" in c
    assert any("X_ni_v_def" in f.evidence for f in c["INSTANCE_MEMBER_NOT_IN_DEFINITION"])


def test_type_id_tagtype_mismatch(findings):
    c = codes(findings)
    assert "TYPE_ID_TAGTYPE_MISMATCH" in c
    assert any(f.fullPath.endswith("BadTypeIdAtomic") for f in c["TYPE_ID_TAGTYPE_MISMATCH"])


# --- INFO -----------------------------------------------------------------

def test_instance_override_shape_not_error(findings):
    c = codes(findings)
    assert "INSTANCE_OVERRIDE_SHAPE" in c
    ov = [f for f in c["INSTANCE_OVERRIDE_SHAPE"] if f.fullPath.endswith("Inst_override")]
    assert ov and ov[0].severity == "INFO"


def test_shared_opc_item_path_is_info(findings):
    c = codes(findings)
    assert "SHARED_OPC_ITEM_PATH" in c
    assert all(f.severity == "INFO" for f in c["SHARED_OPC_ITEM_PATH"])


def test_external_provider_reference_is_info(findings):
    c = codes(findings)
    assert "EXTERNAL_PROVIDER_REFERENCE" in c
    assert any("EXTERNAL_PROV" in f.evidence for f in c["EXTERNAL_PROVIDER_REFERENCE"])
    assert all(f.severity == "INFO" for f in c["EXTERNAL_PROVIDER_REFERENCE"])


def test_optional_member_absent_only_with_rule(vconn):
    # Brez pravila: OPTIONAL_MEMBER_ABSENT se NE pojavi (validator ne ugiba).
    f_no_rule = validate(vconn, raw_paths=None)
    assert not any(f.code == "OPTIONAL_MEMBER_ABSENT" for f in f_no_rule)
    # S pravilom: Base.B je opcijski -> Inst_base_missing_B ga nima -> INFO.
    rules = {"Base": {"required": [], "optional": ["B"]}}
    f_rule = validate(vconn, rules=rules, raw_paths=None)
    oma = [f for f in f_rule if f.code == "OPTIONAL_MEMBER_ABSENT"]
    assert oma and all(f.severity == "INFO" for f in oma)
    assert any(f.fullPath.endswith("Inst_base_missing_B") for f in oma)


# --- Semantika: override oblike NISO anomalija ----------------------------

def test_override_shapes_are_subsets_not_anomaly(vconn):
    """Vse override oblike so podmnozice efektivne definicije (razen extra)."""
    reg = build_registry(vconn)
    site = next(s for (s, k) in reg.canonical if k == "Child")
    eff = reg.effective_members(site, "Child")
    # Inst_override serializira {A} -> podmnozica
    assert {"A"} <= eff


# --- Pomozne funkcije -----------------------------------------------------

def test_braces_balanced():
    assert braces_balanced("[P]a/{X}/b")
    assert not braces_balanced("[P]a/{X/b")
    assert not braces_balanced("[P]a/X}/b")


def test_param_tokens():
    assert param_tokens("[P]a/{X}/{Y}") == ["X", "Y"]


def test_all_codes_have_severity(findings):
    for f in findings:
        assert f.code in CODE_SEVERITY
        assert f.severity == CODE_SEVERITY[f.code]


# --- Read-only ------------------------------------------------------------

def test_validate_reports_written_to_tmp(vconn, tmp_path):
    from analyzer.reports import write_reports
    fs = validate(vconn, raw_paths=None)
    paths = write_reports(fs, str(tmp_path / "analysis"))
    for key in ("md", "csv", "json"):
        assert os.path.exists(paths[key])
    data = json.load(open(paths["json"], encoding="utf-8"))
    assert len(data) == len(fs)
