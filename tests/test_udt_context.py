"""C4: site-aware efektivni UDT kontekst in lastnosti."""

from __future__ import annotations

import os

import pytest

from editor import (
    ProjectUdtContext,
    create_project,
    import_source,
    node_details,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "editor_c4")


@pytest.fixture()
def project(tmp_path):
    project = create_project(str(tmp_path / "proj"), name="C4 UDT test")
    source_names = {
        "site_a": "tags_UNS_SITEA.json",
        "site_b": "tags_UNS_SITEB.json",
    }
    for site, source_name in source_names.items():
        for filename in ("UDT_Definitions.json", source_name):
            import_source(
                project,
                os.path.join(FIX, site, filename),
                site=site,
            )
    yield project
    project.close()


def _uid(project, site: str, path: str) -> str:
    row = project.conn.execute(
        "SELECT b.node_uid "
        "FROM baseline_nodes b JOIN sources s ON s.id = b.source_id "
        "WHERE s.site = ? AND b.path_at_import = ? "
        "ORDER BY CASE s.kind WHEN 'udt' THEN 0 ELSE 1 END LIMIT 1",
        (site, path),
    ).fetchone()
    assert row is not None
    return row["node_uid"]


def test_instance_exposes_inheritance_members_and_parameters(project):
    resolver = ProjectUdtContext(project)
    details = node_details(
        project,
        _uid(project, "site_a", "Line1"),
        udt_context=resolver,
    )
    context = details["udt_context"]

    assert context["subject_kind"] == "instance"
    assert context["selected_role"] == "instance"
    assert context["definition_found"] is True
    assert context["definition_kind"] == "udt"
    assert context["inheritance_chain"] == ["MotorUDT", "BaseMotor"]
    assert context["direct_members"] == ["Alarm", "Speed"]
    assert context["inherited_members"] == ["Run"]
    assert context["local_members"] == ["Speed"]
    assert context["effective_members"] == ["Alarm", "Run", "Speed"]
    assert context["declared_parameter_names"] == ["Area", "SpeedScale"]
    assert context["effective_parameters"]["Area"]["value"] == "Packing"
    assert context["effective_parameters"]["SpeedScale"]["value"] == 2.0
    assert details["properties"]["parameters"]["Area"]["value"] == "Packing"


def test_member_properties_merge_base_definition_derived_and_instance(project):
    resolver = ProjectUdtContext(project)
    details = node_details(
        project,
        _uid(project, "site_a", "Line1/Speed"),
        udt_context=resolver,
    )

    assert details["properties"] == {
        "name": "Speed",
        "tagType": "AtomicTag",
        "documentation": "Instance speed override",
    }
    assert details["effective_properties"]["dataType"] == "Float4"
    assert (
        details["effective_properties"]["documentation"]
        == "Instance speed override"
    )
    assert details["udt_context"]["selected_role"] == "instance_member"
    assert details["udt_context"]["member_path"] == "Speed"


def test_same_type_id_is_resolved_within_selected_site(project):
    details = node_details(project, _uid(project, "site_b", "LineB"))
    context = details["udt_context"]

    assert context["site"] == "site_b"
    assert context["inheritance_chain"] == ["MotorUDT"]
    assert context["effective_members"] == ["Temperature"]
    assert "Alarm" not in context["effective_members"]
    assert context["effective_parameters"]["Zone"]["value"] == "B"


def test_non_udt_node_keeps_raw_properties_as_effective(project):
    uid = _uid(project, "site_a", "")
    before = project.conn.execute(
        "SELECT raw_json FROM baseline_nodes WHERE node_uid = ?", (uid,)
    ).fetchone()["raw_json"]

    details = node_details(project, uid)

    after = project.conn.execute(
        "SELECT raw_json FROM baseline_nodes WHERE node_uid = ?", (uid,)
    ).fetchone()["raw_json"]
    assert details["udt_context"] is None
    assert details["effective_properties"] == details["properties"]
    assert after == before


def test_details_include_source_provenance(project):
    details = node_details(project, _uid(project, "site_a", "Line1"))
    provider = details["provider"]

    assert provider["site"] == "site_a"
    assert provider["provider_name"] == "UNS_SITEA"
    assert provider["kind"] == "uns"
    assert provider["path"].endswith("tags_UNS_SITEA.json")
    assert len(provider["sha256"]) == 64
    assert provider["import_session"]
    assert provider["imported_at"]
