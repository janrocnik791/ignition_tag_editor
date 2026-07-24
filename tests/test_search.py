"""Testi C3 repository iskanja in filtrov."""

from __future__ import annotations

import os

import pytest

from editor import (
    create_project,
    get_search_filters,
    import_source,
    list_providers,
    search_nodes,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="Search test")
    for filename in (
        "tags_IO_TESTSITE_SIE.json",
        "tags_UNS_TESTSITE.json",
        "UDT_Definitions.json",
    ):
        import_source(project, os.path.join(FIX, filename), site="testsite")
    yield project
    project.close()


def test_exact_prefix_and_contains_modes(project):
    exact = search_nodes(project, "name", "Motor1_Run", mode="exact")
    assert exact["total"] == 1
    assert exact["results"][0]["path_at_import"] == "Area1/Motor1_Run"

    prefix = search_nodes(
        project, "fullPath", "Area1/Motor1", mode="prefix"
    )
    assert prefix["total"] == 2

    contains = search_nodes(
        project, "opcItemPath", "M1.", mode="contains"
    )
    assert contains["total"] == 2


def test_literal_like_characters_are_escaped(project):
    result = search_nodes(project, "name", "Motor1_", mode="contains")
    assert {row["name"] for row in result["results"]} == {
        "Motor1_Run",
        "Motor1_Speed",
    }


def test_provider_site_and_tag_type_filters_are_combined(project):
    io = next(
        provider for provider in list_providers(project)
        if provider["provider_name"] == "IO_TESTSITE_SIE"
    )
    result = search_nodes(
        project,
        "name",
        "",
        provider_uid=io["provider_uid"],
        site="testsite",
        tag_type="Folder",
    )
    assert result["total"] == 2
    assert {row["name"] for row in result["results"]} == {"Area1", "Area2"}


def test_count_and_offset_paging_are_deterministic(project):
    first = search_nodes(project, "name", "", limit=2, offset=0)
    second = search_nodes(project, "name", "", limit=2, offset=2)
    again = search_nodes(project, "name", "", limit=2, offset=0)

    assert first["total"] == 15
    assert len(first["results"]) == len(second["results"]) == 2
    assert first["has_previous"] is False and first["has_next"] is True
    assert second["has_previous"] is True
    assert [row["node_uid"] for row in first["results"]] == [
        row["node_uid"] for row in again["results"]
    ]
    assert {
        row["node_uid"] for row in first["results"]
    }.isdisjoint(row["node_uid"] for row in second["results"])
    assert all("raw_json" not in row for row in first["results"])


def test_search_filter_options(project):
    filters = get_search_filters(project)
    assert filters["sites"] == ["testsite"]
    assert {row["provider_name"] for row in filters["providers"]} == {
        "IO_TESTSITE_SIE",
        "UNS_TESTSITE",
        "UDT_testsite",
    }
    assert {"Provider", "Folder", "AtomicTag", "UdtInstance", "UdtType"} <= set(
        filters["tag_types"]
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"field": "unknown", "value": "x"}, "Nepoznano polje"),
        ({"field": "name", "value": "x", "mode": "fuzzy"}, "Nepoznan mode"),
        ({"field": "name", "value": "x", "limit": 0}, "limit"),
        ({"field": "name", "value": "x", "offset": -1}, "offset"),
    ],
)
def test_invalid_search_arguments(project, kwargs, message):
    with pytest.raises(ValueError, match=message):
        search_nodes(project, **kwargs)
