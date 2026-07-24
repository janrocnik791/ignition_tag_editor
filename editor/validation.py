"""Read-only validation of the complete simulated editor state."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set

from analyzer.build import sha256_file
from analyzer.udt_resolver import braces_balanced

from .operations import build_simulation_state
from .project import Project
from .relationships import query_relationships
from .simulation import diff

_SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}


def _finding(
    severity: str,
    code: str,
    target: Optional[str],
    message: str,
    **evidence: Any,
) -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "target": target,
        "message": message,
        "evidence": evidence,
    }


def validate_project(
    project: Project,
    *,
    node_uids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Validate export-relevant invariants without mutating baseline or operations."""
    state = build_simulation_state(project)
    selected = set(state) if node_uids is None else set(node_uids) & set(state)
    findings: List[Dict[str, Any]] = []
    if not selected:
        findings.append(
            _finding(
                "ERROR",
                "EMPTY_EXPORT_SCOPE",
                None,
                "Izbrani obseg ne vsebuje nobenega simuliranega vozlisca.",
            )
        )

    operation_diff = diff(project)
    for item in operation_diff["skipped"]:
        findings.append(
            _finding(
                "ERROR" if item["status"] == "CONFLICT" else "WARNING",
                f"OPERATION_{item['status']}",
                item["operation_uid"],
                item["reason"],
                operation_uid=item["operation_uid"],
            )
        )

    siblings: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for uid in selected:
        node = state[uid]
        siblings[
            (
                node.get("provider_uid"),
                node.get("parent_uid"),
                (node.get("name") or "").casefold(),
            )
        ].append(node)
        binding = node.get("source_tag_path")
        if isinstance(binding, str) and not braces_balanced(binding):
            findings.append(
                _finding(
                    "ERROR",
                    "INVALID_PATH_TEMPLATE",
                    uid,
                    "sourceTagPath ima neuravnotezene zavite oklepaje.",
                    binding=binding,
                )
            )
        if (
            node.get("tag_type") == "UdtInstance"
            and not node.get("type_id")
        ):
            parent = state.get(node.get("parent_uid"))
            if parent is None or parent.get("tag_type") not in (
                "UdtType",
                "UdtInstance",
            ):
                findings.append(
                    _finding(
                        "WARNING",
                        "EMPTY_TOP_LEVEL_TYPE_ID",
                        uid,
                        "Samostojna UDT instanca nima typeId.",
                    )
                )
    for (_provider, _parent, name), rows in siblings.items():
        if name and len(rows) > 1:
            findings.append(
                _finding(
                    "ERROR",
                    "DUPLICATE_SIBLING_NAME",
                    rows[0].get("parent_uid"),
                    "Simulirani sorojenci imajo podvojeno ime.",
                    name=rows[0].get("name"),
                    node_uids=sorted(row["node_uid"] for row in rows),
                )
            )

    page = query_relationships(project, limit=500)
    relationship_rows = page["results"]
    offset = len(relationship_rows)
    while page["has_next"]:
        page = query_relationships(project, limit=500, offset=offset)
        relationship_rows.extend(page["results"])
        offset += len(page["results"])
    for row in relationship_rows:
        if row["source_node_uid"] not in selected:
            continue
        if row["state"] in ("STALE", "CONFLICT"):
            findings.append(
                _finding(
                    "ERROR",
                    f"RELATIONSHIP_{row['state']}",
                    row["relationship_uid"],
                    "Relacija ni veljavna za produkcijski izvoz.",
                    origin=row["origin"],
                )
            )
        elif row["origin"] == "SUGGESTION":
            findings.append(
                _finding(
                    "INFO",
                    "UNREVIEWED_SUGGESTION",
                    row["relationship_uid"],
                    "Predlog ni odobren in ne spreminja izvoza.",
                    evidence_type=row["evidence_type"],
                )
            )
        elif row["state"] in ("UNRESOLVED", "AMBIGUOUS"):
            findings.append(
                _finding(
                    "WARNING",
                    f"RELATIONSHIP_{row['state']}",
                    row["relationship_uid"],
                    "Exact odkrivanje je pustilo odprto relacijo.",
                    evidence_type=row["evidence_type"],
                )
            )

    for source in project.conn.execute(
        "SELECT id, path, sha256, provider_name FROM sources ORDER BY id"
    ).fetchall():
        if not os.path.isfile(source["path"]):
            findings.append(
                _finding(
                    "INFO",
                    "SOURCE_FILE_UNAVAILABLE",
                    str(source["id"]),
                    "Izvorna datoteka ni vec dosegljiva; baseline ostaja v projektu.",
                    provider=source["provider_name"],
                )
            )
        elif sha256_file(source["path"]) != source["sha256"]:
            findings.append(
                _finding(
                    "WARNING",
                    "SOURCE_FILE_CHANGED",
                    str(source["id"]),
                    "Izvorna datoteka na disku se razlikuje od uvozenega baselinea.",
                    provider=source["provider_name"],
                )
            )

    findings.sort(
        key=lambda row: (
            _SEVERITY_ORDER[row["severity"]],
            row["code"],
            row["target"] or "",
        )
    )
    counts = Counter(row["severity"] for row in findings)
    return {
        "status": "VALID" if not counts["ERROR"] else "INVALID",
        "checked_nodes": len(selected),
        "counts": {
            severity: counts[severity]
            for severity in ("ERROR", "WARNING", "INFO")
        },
        "findings": findings,
    }
