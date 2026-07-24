"""Checkpoint K deterministic and bounded approximate suggestions."""

from __future__ import annotations

import os

import pytest

from editor import (
    AutomationError,
    create_project,
    import_source,
    propose_automation,
    query_relationships,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "automation")


@pytest.fixture()
def project(tmp_path):
    opened = create_project(str(tmp_path / "project"), name="Automation")
    for name in ("tags_IO_K.json", "tags_UNS_K.json"):
        import_source(opened, os.path.join(FIX, name), site="factory")
    yield opened
    opened.close()


def _suggestions(project, evidence_type):
    return query_relationships(
        project, evidence_type=evidence_type, limit=100
    )["results"]


def test_deterministic_rules_run_before_fuzzy_and_never_approve(project):
    result = propose_automation(project, include_fuzzy=False)
    assert result["deterministic_name"] == 1
    assert result["deterministic_group"] == 1
    assert result["fuzzy"] == 0

    rows = _suggestions(project, "DETERMINISTIC_NAME_PATTERN")
    assert len(rows) == 1
    assert rows[0]["source_name"] == rows[0]["target_name"] == "SameExact"
    assert rows[0]["origin"] == "SUGGESTION"
    assert rows[0]["state"] == "UNRESOLVED"
    assert rows[0]["confirmed_by"] is None

    groups = _suggestions(project, "DETERMINISTIC_GROUP_PATTERN")
    assert len(groups) == 1
    assert {groups[0]["source_name"], groups[0]["target_name"]} == {
        "M10_RUN",
        "M10_RDY",
    }


def test_fuzzy_matching_is_thresholded_margin_checked_and_bounded(project):
    result = propose_automation(
        project,
        fuzzy_threshold=0.86,
        fuzzy_margin=0.08,
        max_fuzzy_sources=10,
        max_candidates_per_source=10,
        max_fuzzy_suggestions=1,
    )
    assert result["fuzzy"] == 1
    assert result["limits"]["max_fuzzy_suggestions"] == 1
    row = _suggestions(project, "FUZZY_NAME_SIMILARITY")[0]
    assert row["source_name"] == "Pump01_Runing"
    assert row["target_name"] == "Pump01_Running"
    assert row["origin"] == "SUGGESTION"
    assert row["state"] == "UNRESOLVED"
    assert row["confidence"] >= 0.86
    assert (
        row["evidence"]["score"] - row["evidence"]["runner_up_score"]
        >= row["evidence"]["required_margin"]
    )


def test_automation_is_idempotent_and_validates_safety_limits(project):
    first = propose_automation(project)
    second = propose_automation(project)
    assert first["created_or_refreshed"] == second["created_or_refreshed"]
    assert query_relationships(project, limit=100)["total"] == (
        first["created_or_refreshed"]
    )
    with pytest.raises(AutomationError):
        propose_automation(project, fuzzy_threshold=0.2)
    with pytest.raises(AutomationError):
        propose_automation(project, max_candidates_per_source=0)
