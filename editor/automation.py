"""Bounded, review-only grouping and mapping proposals (checkpoint K)."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .project import Project, ProjectError
from .relationships import create_suggestion_relationship

AUTOMATION_EVIDENCE_TYPES = (
    "DETERMINISTIC_NAME_PATTERN",
    "DETERMINISTIC_GROUP_PATTERN",
    "FUZZY_NAME_SIMILARITY",
)
_LEAF_EXCLUSIONS = ("Provider", "Folder", "UdtType")
_GROUP_PATTERN = re.compile(
    r"^(.+?)[_-](PV|SP|RUN|RDY|CMD|STATE|ALARM|FAULT)$", re.IGNORECASE
)


class AutomationError(ProjectError):
    """Invalid or unsafe automation request."""


def _nodes(project: Project, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses = [
        "b.name IS NOT NULL",
        "b.name <> ''",
        "b.tag_type NOT IN (?, ?, ?)",
    ]
    params: List[Any] = list(_LEAF_EXCLUSIONS)
    if kind is not None:
        clauses.append("s.kind=?")
        params.append(kind)
    return [
        dict(row)
        for row in project.conn.execute(
            "SELECT b.node_uid, b.parent_uid, b.name, b.path_at_import, "
            "b.provider_uid, s.provider_name, s.site, s.kind "
            "FROM baseline_nodes b JOIN sources s ON s.id=b.source_id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY s.site, s.provider_name, b.path_at_import, b.node_uid",
            params,
        ).fetchall()
    ]


def _normalized(name: str) -> str:
    return "".join(ch.casefold() for ch in name if ch.isalnum())


def _mark_previous_stale(
    project: Project, produced: Set[str], evidence_types: Sequence[str]
) -> int:
    placeholders = ",".join("?" for _ in evidence_types)
    rows = project.conn.execute(
        "SELECT relationship_uid, evidence_json FROM relationships "
        "WHERE origin='SUGGESTION' AND evidence_type IN "
        f"({placeholders})",
        list(evidence_types),
    ).fetchall()
    stale = 0
    for row in rows:
        evidence = json.loads(row["evidence_json"])
        if (
            evidence.get("suggestion_namespace") == "automation"
            and row["relationship_uid"] not in produced
        ):
            project.conn.execute(
                "UPDATE relationships SET state='STALE' "
                "WHERE relationship_uid=?",
                (row["relationship_uid"],),
            )
            stale += 1
    return stale


def _add(
    project: Project,
    produced: Set[str],
    source_uid: str,
    target_uid: Optional[str],
    evidence_type: str,
    evidence: Dict[str, Any],
    *,
    state: str = "UNRESOLVED",
    confidence: Optional[float] = None,
) -> None:
    row = create_suggestion_relationship(
        project,
        source_uid,
        target_uid,
        "GENERIC",
        evidence_type,
        evidence,
        state=state,
        confidence=confidence,
        namespace="automation",
    )
    produced.add(row["relationship_uid"])


def propose_automation(
    project: Project,
    *,
    include_fuzzy: bool = True,
    fuzzy_threshold: float = 0.86,
    fuzzy_margin: float = 0.08,
    max_fuzzy_sources: int = 1000,
    max_candidates_per_source: int = 100,
    max_fuzzy_suggestions: int = 500,
) -> Dict[str, Any]:
    """Generate deterministic proposals, then optional strictly bounded fuzzy ones."""
    if not 0.5 <= fuzzy_threshold <= 1.0:
        raise AutomationError("fuzzy_threshold mora biti med 0.5 in 1.0")
    if not 0.0 <= fuzzy_margin <= 0.5:
        raise AutomationError("fuzzy_margin mora biti med 0 in 0.5")
    for value, label in (
        (max_fuzzy_sources, "max_fuzzy_sources"),
        (max_candidates_per_source, "max_candidates_per_source"),
        (max_fuzzy_suggestions, "max_fuzzy_suggestions"),
    ):
        if not isinstance(value, int) or value < 1:
            raise AutomationError(f"{label} mora biti pozitivno celo stevilo")

    io_nodes = _nodes(project, "io")
    uns_nodes = _nodes(project, "uns")
    all_nodes = _nodes(project)
    produced: Set[str] = set()
    deterministic_sources: Set[str] = set()
    deterministic_targets: Set[str] = set()
    name_count = 0
    group_count = 0

    uns_by_site_name: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for target in uns_nodes:
        uns_by_site_name.setdefault(
            (target["site"], target["name"].casefold()), []
        ).append(target)
    for source in io_nodes:
        candidates = uns_by_site_name.get(
            (source["site"], source["name"].casefold()), []
        )
        if not candidates:
            continue
        deterministic_sources.add(source["node_uid"])
        deterministic_targets.update(row["node_uid"] for row in candidates)
        state = "UNRESOLVED" if len(candidates) == 1 else "AMBIGUOUS"
        _add(
            project,
            produced,
            source["node_uid"],
            candidates[0]["node_uid"] if len(candidates) == 1 else None,
            "DETERMINISTIC_NAME_PATTERN",
            {
                "rule": "same_name_io_to_uns",
                "name": source["name"],
                "site": source["site"],
                "candidate_count": len(candidates),
                "candidate_uids": [
                    row["node_uid"] for row in candidates[:100]
                ],
            },
            state=state,
        )
        name_count += 1

    groups: Dict[Tuple[str, Optional[str], str], List[Dict[str, Any]]] = {}
    for node in all_nodes:
        match = _GROUP_PATTERN.match(node["name"])
        if match:
            groups.setdefault(
                (node["provider_uid"], node["parent_uid"], match.group(1).casefold()),
                [],
            ).append(node)
    for (_provider, _parent, key), members in sorted(groups.items()):
        members.sort(key=lambda row: (row["name"].casefold(), row["node_uid"]))
        if len(members) < 2:
            continue
        anchor = members[0]
        for member in members[1:]:
            _add(
                project,
                produced,
                anchor["node_uid"],
                member["node_uid"],
                "DETERMINISTIC_GROUP_PATTERN",
                {
                    "rule": "shared_base_known_suffix",
                    "group_key": key,
                    "member_names": [row["name"] for row in members],
                },
            )
            group_count += 1

    fuzzy_count = 0
    evaluated_sources = 0
    candidate_truncations = 0
    if include_fuzzy:
        available_targets = [
            row
            for row in uns_nodes
            if row["node_uid"] not in deterministic_targets
        ]
        for source in io_nodes:
            if fuzzy_count >= max_fuzzy_suggestions:
                break
            if source["node_uid"] in deterministic_sources:
                continue
            if evaluated_sources >= max_fuzzy_sources:
                break
            source_key = _normalized(source["name"])
            if len(source_key) < 4:
                continue
            pool = [
                row
                for row in available_targets
                if row["site"] == source["site"]
                and _normalized(row["name"])[:1] == source_key[:1]
            ]
            pool.sort(key=lambda row: (row["name"].casefold(), row["node_uid"]))
            evaluated_sources += 1
            if len(pool) > max_candidates_per_source:
                candidate_truncations += 1
                pool = pool[:max_candidates_per_source]
            scored = sorted(
                (
                    (
                        SequenceMatcher(
                            None, source_key, _normalized(target["name"])
                        ).ratio(),
                        target,
                    )
                    for target in pool
                ),
                key=lambda item: (-item[0], item[1]["node_uid"]),
            )
            if not scored or scored[0][0] < fuzzy_threshold:
                continue
            second_score = scored[1][0] if len(scored) > 1 else 0.0
            if scored[0][0] - second_score < fuzzy_margin:
                continue
            score, target = scored[0]
            _add(
                project,
                produced,
                source["node_uid"],
                target["node_uid"],
                "FUZZY_NAME_SIMILARITY",
                {
                    "algorithm": "difflib.SequenceMatcher",
                    "source_normalized": source_key,
                    "target_normalized": _normalized(target["name"]),
                    "score": score,
                    "runner_up_score": second_score,
                    "threshold": fuzzy_threshold,
                    "required_margin": fuzzy_margin,
                    "candidate_pool_size": len(pool),
                },
                confidence=score,
            )
            fuzzy_count += 1

    with project.conn:
        stale = _mark_previous_stale(
            project, produced, AUTOMATION_EVIDENCE_TYPES
        )
    return {
        "created_or_refreshed": len(produced),
        "deterministic_name": name_count,
        "deterministic_group": group_count,
        "fuzzy": fuzzy_count,
        "fuzzy_enabled": include_fuzzy,
        "evaluated_fuzzy_sources": evaluated_sources,
        "candidate_truncations": candidate_truncations,
        "stale": stale,
        "limits": {
            "max_fuzzy_sources": max_fuzzy_sources,
            "max_candidates_per_source": max_candidates_per_source,
            "max_fuzzy_suggestions": max_fuzzy_suggestions,
        },
    }
