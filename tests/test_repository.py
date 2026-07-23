"""Testi mejnika C1: read-only repozitorij za lazy drevo in podrobnosti."""

from __future__ import annotations

import os

import pytest

from editor import (
    RepositoryError,
    breadcrumbs,
    child_count,
    create_project,
    full_path,
    get_children,
    get_parent,
    get_provider_root,
    import_source,
    list_providers,
    node_details,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")
IO = os.path.join(FIX, "tags_IO_TESTSITE_SIE.json")
UNS = os.path.join(FIX, "tags_UNS_TESTSITE.json")
UDT = os.path.join(FIX, "UDT_Definitions.json")


@pytest.fixture()
def project(tmp_path):
    p = create_project(str(tmp_path / "proj"), name="Repo test")
    import_source(p, IO, site="testsite")
    import_source(p, UNS, site="testsite")
    import_source(p, UDT, site="testsite")
    yield p
    p.close()


def _io_root(project):
    io = next(p for p in list_providers(project)
              if p["provider_name"] == "IO_TESTSITE_SIE")
    return get_provider_root(project, io["provider_uid"])


# ---- providerji / koreni -------------------------------------------------

def test_provider_roots_via_children_none(project):
    roots = get_children(project, None)
    assert len(roots) == 3  # trije providerji
    assert all(r["parent_uid"] is None for r in roots)


def test_get_provider_root(project):
    root = _io_root(project)
    assert root["parent_uid"] is None
    assert root["tag_type"] == "Provider"
    assert root["path_at_import"] == ""
    assert root["has_children"] is True


# ---- lazy otroci / vrstni red / paging -----------------------------------

def test_children_ordered_by_sibling_index(project):
    root = _io_root(project)
    kids = get_children(project, root["node_uid"])
    assert [k["name"] for k in kids] == ["Area1", "Area2"]
    assert [k["sibling_index"] for k in kids] == [0, 1]
    assert kids[0]["has_children"] is True

    area1 = kids[0]
    leaves = get_children(project, area1["node_uid"])
    assert [l["name"] for l in leaves] == ["Motor1_Run", "Motor1_Speed"]
    assert all(l["has_children"] is False for l in leaves)


def test_lazy_paging(project):
    root = _io_root(project)
    total = child_count(project, root["node_uid"])
    assert total == 2
    page0 = get_children(project, root["node_uid"], limit=1, offset=0)
    page1 = get_children(project, root["node_uid"], limit=1, offset=1)
    assert len(page0) == 1 and len(page1) == 1
    # brez prekrivanja; skupaj == cel nabor v istem vrstnem redu
    assert page0[0]["name"] == "Area1"
    assert page1[0]["name"] == "Area2"
    assert page0[0]["node_uid"] != page1[0]["node_uid"]


def test_limit_never_loads_whole_tree(project):
    root = _io_root(project)
    page = get_children(project, root["node_uid"], limit=1)
    assert len(page) <= 1


def test_deterministic_order(project):
    root = _io_root(project)
    a = [k["node_uid"] for k in get_children(project, root["node_uid"])]
    b = [k["node_uid"] for k in get_children(project, root["node_uid"])]
    assert a == b


def test_children_of_unknown_parent_is_empty(project):
    assert get_children(project, "ni-tak-uid") == []


# ---- starsi / breadcrumbs / pot ------------------------------------------

def _run_node(project):
    root = _io_root(project)
    area1 = get_children(project, root["node_uid"])[0]
    return get_children(project, area1["node_uid"])[0]  # Motor1_Run


def test_get_parent(project):
    root = _io_root(project)
    area1 = get_children(project, root["node_uid"])[0]
    run = get_children(project, area1["node_uid"])[0]
    assert get_parent(project, run["node_uid"])["node_uid"] == area1["node_uid"]
    assert get_parent(project, root["node_uid"]) is None  # koren nima starsa


def test_get_parent_unknown_raises(project):
    with pytest.raises(RepositoryError):
        get_parent(project, "ni-tak-uid")


def test_breadcrumbs(project):
    run = _run_node(project)
    crumbs = breadcrumbs(project, run["node_uid"])
    assert [c["name"] for c in crumbs] == ["", "Area1", "Motor1_Run"]
    # zadnji clen je iskano vozlisce
    assert crumbs[-1]["node_uid"] == run["node_uid"]


def test_full_path(project):
    run = _run_node(project)
    assert full_path(project, run["node_uid"]) == "Area1/Motor1_Run"


# ---- podrobnosti ---------------------------------------------------------

def test_node_details_raw_and_context(project):
    run = _run_node(project)
    d = node_details(project, run["node_uid"])
    assert d["tag_type"] == "AtomicTag"
    assert d["opc_item_path"] == "ns=1;s=[PLC]M1.Run"
    # surove lastnosti = celoten originalni objekt
    assert d["properties"]["opcItemPath"] == "ns=1;s=[PLC]M1.Run"
    assert d["properties"]["dataType"] == "Boolean"
    assert "raw_json" not in d
    assert d["child_count"] == 0 and d["has_children"] is False
    assert d["parent"]["name"] == "Area1"
    assert d["provider"] == {"provider_name": "IO_TESTSITE_SIE",
                             "site": "testsite", "kind": "io"}


def test_node_details_unknown_raises(project):
    with pytest.raises(RepositoryError):
        node_details(project, "ni-tak-uid")


def test_udt_instance_details(project):
    uns = next(p for p in list_providers(project)
               if p["provider_name"] == "UNS_TESTSITE")
    root = get_provider_root(project, uns["provider_uid"])
    site = get_children(project, root["node_uid"])[0]      # Site
    line1 = get_children(project, site["node_uid"])[0]      # Line1
    d = node_details(project, line1["node_uid"])
    assert d["tag_type"] == "UdtInstance"
    assert d["type_id"] == "MotorUDT"
    assert d["child_count"] == 2  # Run, Speed
