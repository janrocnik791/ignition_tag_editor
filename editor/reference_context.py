"""Optional reference-index context exposed as reviewable relationship suggestions.

The existing :mod:`analyzer.reference` importer remains the authority for parsing and
validating reference CSV files.  This adapter only projects its normalized, provenance-
carrying records into the editor's relationship model.  It never creates a manual
confirmation and never treats a name match as exact evidence.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional

from analyzer.reference.importer import build_reference_index
from analyzer.reference.query import get_expected_state

from .project import Project, ProjectError
from .relationships import create_suggestion_relationship

REFERENCE_EVIDENCE_TYPE = "REFERENCE_EXPECTATION"


class ReferenceContextError(ProjectError):
    """The optional reference context could not be read or applied."""


def _candidate_nodes(
    project: Project, name: Optional[str], site: str
) -> List[Dict[str, Any]]:
    if not isinstance(name, str) or not name.strip():
        return []
    return [
        dict(row)
        for row in project.conn.execute(
            "SELECT b.node_uid, b.source_id, b.path_at_import, "
            "s.provider_name, s.sha256 FROM baseline_nodes b "
            "JOIN sources s ON s.id=b.source_id "
            "WHERE b.name=? AND s.site=? "
            "ORDER BY s.provider_name, b.path_at_import, b.node_uid",
            (name.strip(), site),
        ).fetchall()
    ]


def _upsert_suggestion(
    project: Project,
    *,
    source: Dict[str, Any],
    target: Optional[Dict[str, Any]],
    state: str,
    evidence: Dict[str, Any],
) -> str:
    result = create_suggestion_relationship(
        project,
        source["node_uid"],
        target["node_uid"] if target else None,
        "GENERIC",
        REFERENCE_EVIDENCE_TYPE,
        evidence,
        state=state,
        namespace="reference_context",
    )
    return result["relationship_uid"]


def _provenance_key(provenance: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: provenance.get(key)
        for key in ("file", "sheet", "sha256", "row", "col_index", "col_header")
        if provenance.get(key) is not None
    }


def apply_reference_index(
    project: Project,
    reference_db_path: str,
    *,
    site: str,
    line: str,
) -> Dict[str, Any]:
    """Create pending suggestions from one read-only reference index.

    Exact tag-name equality is used only to anchor reference records to visible
    project nodes.  Every resulting row has ``origin='SUGGESTION'`` and remains
    ``UNRESOLVED`` or ``AMBIGUOUS`` until a user explicitly confirms it.
    """
    db_path = os.path.abspath(reference_db_path)
    if not os.path.isfile(db_path):
        raise ReferenceContextError(
            f"Referencni indeks ne obstaja: {db_path}"
        )
    if not isinstance(site, str) or not site.strip():
        raise ReferenceContextError("site mora biti neprazen niz")
    if not isinstance(line, str) or not line.strip():
        raise ReferenceContextError("line mora biti neprazen niz")

    try:
        reference = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        expected = get_expected_state(reference, site.strip(), line.strip())
    except sqlite3.Error as exc:
        raise ReferenceContextError(
            f"Referencnega indeksa ni mogoce prebrati: {exc}"
        ) from exc
    finally:
        if "reference" in locals():
            reference.close()

    context = {
        "adapter": "analyzer.reference",
        "site": site.strip(),
        "line": line.strip(),
    }
    produced = set()
    matched_records = 0
    skipped_records = 0
    ambiguous = 0

    with project.conn:
        for group in expected["groups"]:
            for member in group["members"]:
                candidates = _candidate_nodes(
                    project, member["expected_name"], site.strip()
                )
                if not candidates:
                    skipped_records += 1
                    continue
                matched_records += 1
                state = "AMBIGUOUS" if len(candidates) > 1 else "UNRESOLVED"
                ambiguous += int(state == "AMBIGUOUS")
                for source in candidates:
                    evidence = {
                        **context,
                        "kind": "expected_member",
                        "expected_name": member["expected_name"],
                        "member_key": member["member_key"],
                        "tech_number": group["tech_number"],
                        "group_type": group["group_type"],
                        "prefix": group["prefix"],
                        "provenance": _provenance_key(member["provenance"]),
                        "candidate_count": len(candidates),
                    }
                    produced.add(
                        _upsert_suggestion(
                            project,
                            source=source,
                            target=None,
                            state=state,
                            evidence=evidence,
                        )
                    )

        for line_tag in expected["line_tags"]:
            sources = _candidate_nodes(
                project, line_tag["old_name"], site.strip()
            )
            targets = _candidate_nodes(
                project, line_tag["new_name"], site.strip()
            )
            if not sources:
                skipped_records += 1
                continue
            matched_records += 1
            state = (
                "UNRESOLVED"
                if len(sources) == 1 and len(targets) <= 1
                else "AMBIGUOUS"
            )
            ambiguous += int(state == "AMBIGUOUS")
            target = targets[0] if len(targets) == 1 else None
            for source in sources:
                target_for_source = target
                if (
                    target_for_source
                    and target_for_source["node_uid"] == source["node_uid"]
                ):
                    target_for_source = None
                evidence = {
                    **context,
                    "kind": "line_tag_rename",
                    "label": line_tag["label"],
                    "old_name": line_tag["old_name"],
                    "new_name": line_tag["new_name"],
                    "provenance": _provenance_key(line_tag["provenance"]),
                    "source_candidate_count": len(sources),
                    "target_candidate_count": len(targets),
                    "target_candidates": [
                        row["node_uid"] for row in targets[:100]
                    ],
                }
                produced.add(
                    _upsert_suggestion(
                        project,
                        source=source,
                        target=target_for_source,
                        state=state,
                        evidence=evidence,
                    )
                )

        existing = project.conn.execute(
            "SELECT relationship_uid, evidence_json FROM relationships "
            "WHERE origin='SUGGESTION' AND evidence_type=?",
            (REFERENCE_EVIDENCE_TYPE,),
        ).fetchall()
        stale = 0
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        for row in existing:
            evidence = json.loads(row["evidence_json"])
            same_context = all(
                evidence.get(key) == value for key, value in context.items()
            )
            if same_context and row["relationship_uid"] not in produced:
                project.conn.execute(
                    "UPDATE relationships SET state='STALE', updated_at=? "
                    "WHERE relationship_uid=?",
                    (now, row["relationship_uid"]),
                )
                stale += 1

    return {
        "site": site.strip(),
        "line": line.strip(),
        "created_or_refreshed": len(produced),
        "matched_records": matched_records,
        "skipped_records": skipped_records,
        "ambiguous_records": ambiguous,
        "stale": stale,
    }


def import_reference_context(
    project: Project,
    mappings_dir: str,
    *,
    site: str,
    line: str,
) -> Dict[str, Any]:
    """Build the existing reference index and apply it without persisting a side DB."""
    source_dir = os.path.abspath(mappings_dir)
    if not os.path.isdir(source_dir):
        raise ReferenceContextError(
            f"Mapa referenc ne obstaja: {source_dir}"
        )
    with tempfile.TemporaryDirectory(prefix="tag_editor_reference_") as temp_dir:
        db_path = os.path.join(temp_dir, "reference_index.sqlite")
        build = build_reference_index(source_dir, db_path, site.strip())
        applied = apply_reference_index(
            project, db_path, site=site, line=line
        )
    return {"index": build, **applied}
