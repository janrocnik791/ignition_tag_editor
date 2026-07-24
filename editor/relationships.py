"""Exact relacije nad projektnim baselineom (mejnik D1).

Modul uporablja samo eksplicitne Ignition dokaze iz roadmapa: enolicno enak
``opcItemPath``, razresljiv ``sourceTagPath``, efektivno clananje UDT definicije
in ``typeId`` instance. Imena sama niso dokaz. Neenolicni kandidati ostanejo
``AMBIGUOUS``, manjkajoci ali dinamicni cilji pa ``UNRESOLVED``.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from analyzer.udt_resolver import param_tokens

from .project import Project, ProjectError
from .udt_context import ProjectUdtContext

RELATIONSHIP_ROLES = (
    "RAW_TO_ORGANIZED",
    "ORGANIZED_TO_MEMBER",
    "MEMBER_TO_UNS_INSTANCE",
    "GENERIC",
)
RELATIONSHIP_STATES = (
    "EXACT",
    "MANUAL_CONFIRMED",
    "MANUAL_REJECTED",
    "UNRESOLVED",
    "AMBIGUOUS",
    "STALE",
    "CONFLICT",
)
EVIDENCE_TYPES = (
    "OPC_ITEM_PATH_EXACT",
    "SOURCE_TAG_PATH_RESOLVED",
    "UDT_DEFINITION_MEMBERSHIP",
    "INSTANCE_TYPE",
    "MANUAL",
)
ORIGINS = ("AUTO_EXACT", "MANUAL", "SUGGESTION")

MAX_RELATIONSHIP_PAGE_SIZE = 500
_AUTO_ORIGIN = "AUTO_EXACT"
_PROVIDER_PREFIX = re.compile(r"^\[([^\]]+)\](.*)$", re.DOTALL)
_MAX_EVIDENCE_CANDIDATES = 100


class RelationshipError(ProjectError):
    """Neveljaven filter ali zahteva storitve relacij."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class _Relation:
    relationship_uid: str
    source_node_uid: str
    target_node_uid: Optional[str]
    role: str
    state: str
    evidence_type: str
    evidence_json: str
    origin: str
    confidence: Optional[float]
    source_hashes_json: str


class _Discovery:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.conn = project.conn
        self.udt = ProjectUdtContext(project)
        self.relations: Dict[str, _Relation] = {}
        self.sources = {
            row["id"]: dict(row)
            for row in self.conn.execute(
                "SELECT id, sha256, provider_name, site, kind "
                "FROM sources ORDER BY id"
            ).fetchall()
        }
        self._node_source_ids: Dict[str, Optional[int]] = {}

    def _node_source_id(self, node_uid: Optional[str]) -> Optional[int]:
        if node_uid is None:
            return None
        if node_uid not in self._node_source_ids:
            row = self.conn.execute(
                "SELECT source_id FROM baseline_nodes WHERE node_uid = ?",
                (node_uid,),
            ).fetchone()
            self._node_source_ids[node_uid] = (
                row["source_id"] if row else None
            )
        return self._node_source_ids[node_uid]

    def _source_hashes(
        self,
        node_uids: Iterable[Optional[str]],
        extra_source_ids: Iterable[Optional[int]] = (),
    ) -> str:
        ids = {
            source_id
            for source_id in extra_source_ids
            if source_id is not None
        }
        ids.update(
            source_id
            for source_id in (
                self._node_source_id(node_uid) for node_uid in node_uids
            )
            if source_id is not None
        )
        values = [
            {
                "source_id": source_id,
                "sha256": self.sources[source_id]["sha256"],
            }
            for source_id in sorted(ids)
            if source_id in self.sources
        ]
        return _canonical_json(values)

    def add(
        self,
        *,
        anchor_uid: str,
        source_uid: str,
        target_uid: Optional[str],
        role: str,
        state: str,
        evidence_type: str,
        evidence: Dict[str, Any],
        candidate_source_ids: Iterable[Optional[int]] = (),
    ) -> None:
        identity = "\x00".join(
            (
                _AUTO_ORIGIN,
                evidence_type,
                anchor_uid,
                source_uid,
                target_uid or "",
                role,
            )
        )
        relationship_uid = hashlib.sha1(
            identity.encode("utf-8")
        ).hexdigest()
        relation = _Relation(
            relationship_uid=relationship_uid,
            source_node_uid=source_uid,
            target_node_uid=target_uid,
            role=role,
            state=state,
            evidence_type=evidence_type,
            evidence_json=_canonical_json(evidence),
            origin=_AUTO_ORIGIN,
            confidence=1.0 if state == "EXACT" else None,
            source_hashes_json=self._source_hashes(
                (source_uid, target_uid),
                candidate_source_ids,
            ),
        )
        self.relations[relationship_uid] = relation

    @staticmethod
    def _candidate_evidence(candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        ordered = sorted(candidate["node_uid"] for candidate in candidates)
        shown = ordered[:_MAX_EVIDENCE_CANDIDATES]
        return {
            "candidate_count": len(ordered),
            "candidate_uids": shown,
            "candidates_truncated": len(shown) < len(ordered),
        }

    @staticmethod
    def _source_path_role(source_kind: str) -> str:
        if source_kind == "io":
            return "RAW_TO_ORGANIZED"
        if source_kind in ("udt", "uns"):
            return "ORGANIZED_TO_MEMBER"
        return "GENERIC"

    def _resolve_source_path(
        self,
        row: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[str], str]:
        binding = row["source_tag_path"]
        if param_tokens(binding):
            return None, None, "dynamic_parameters"

        match = _PROVIDER_PREFIX.match(binding)
        if match:
            provider_token = match.group(1)
            path_part = match.group(2)
            if provider_token in ("~", ""):
                provider_name = row["provider_name"]
                target_path = path_part.lstrip("/")
            elif provider_token == ".":
                provider_name = row["provider_name"]
                parent_path = posixpath.dirname(row["path_at_import"])
                target_path = posixpath.normpath(
                    posixpath.join(parent_path, path_part.lstrip("/"))
                )
            else:
                provider_name = provider_token
                target_path = path_part.lstrip("/")
        else:
            provider_name = row["provider_name"]
            target_path = binding.lstrip("/")

        if not provider_name or not target_path or target_path == ".":
            return provider_name, target_path, "invalid_static_path"
        return provider_name, target_path, "static_path"

    def discover_source_tag_paths(self) -> None:
        rows = self.conn.execute(
            "SELECT b.node_uid, b.source_id, b.path_at_import, "
            "b.source_tag_path, s.site, s.provider_name, s.kind "
            "FROM baseline_nodes b JOIN sources s ON s.id = b.source_id "
            "WHERE b.source_tag_path IS NOT NULL "
            "ORDER BY b.node_uid"
        ).fetchall()
        for sqlite_row in rows:
            row = dict(sqlite_row)
            provider_name, target_path, reason = self._resolve_source_path(row)
            role = self._source_path_role(row["kind"])
            candidates: List[Dict[str, Any]] = []
            if reason == "static_path":
                candidates = [
                    dict(candidate)
                    for candidate in self.conn.execute(
                        "SELECT b.node_uid, b.source_id, s.kind "
                        "FROM baseline_nodes b "
                        "JOIN sources s ON s.id = b.source_id "
                        "WHERE s.site = ? AND s.provider_name = ? "
                        "AND b.path_at_import = ? "
                        "ORDER BY b.node_uid",
                        (row["site"], provider_name, target_path),
                    ).fetchall()
                ]

            evidence = {
                "binding": row["source_tag_path"],
                "provider": provider_name,
                "resolved_path": target_path,
                "resolution": reason,
                **self._candidate_evidence(candidates),
            }
            candidate_sources = [
                candidate["source_id"] for candidate in candidates
            ]
            if len(candidates) == 1:
                target = candidates[0]
                # Veriga je usmerjena od referenciranega taga proti tagu,
                # ki vsebuje binding (npr. raw IO -> organizirani tag).
                self.add(
                    anchor_uid=row["node_uid"],
                    source_uid=target["node_uid"],
                    target_uid=row["node_uid"],
                    role=role,
                    state="EXACT",
                    evidence_type="SOURCE_TAG_PATH_RESOLVED",
                    evidence=evidence,
                    candidate_source_ids=candidate_sources,
                )
            else:
                self.add(
                    anchor_uid=row["node_uid"],
                    source_uid=row["node_uid"],
                    target_uid=None,
                    role=role,
                    state="AMBIGUOUS" if candidates else "UNRESOLVED",
                    evidence_type="SOURCE_TAG_PATH_RESOLVED",
                    evidence=evidence,
                    candidate_source_ids=candidate_sources,
                )

    def discover_opc_paths(self) -> None:
        anchors = self.conn.execute(
            "SELECT b.node_uid, b.source_id, b.opc_item_path, s.site, s.kind "
            "FROM baseline_nodes b JOIN sources s ON s.id = b.source_id "
            "WHERE b.opc_item_path IS NOT NULL AND s.kind <> 'io' "
            "ORDER BY b.node_uid"
        ).fetchall()
        for anchor in anchors:
            candidates = [
                dict(row)
                for row in self.conn.execute(
                    "SELECT b.node_uid, b.source_id "
                    "FROM baseline_nodes b "
                    "JOIN sources s ON s.id = b.source_id "
                    "WHERE s.site = ? AND s.kind = 'io' "
                    "AND b.value_source = 'opc' AND b.opc_item_path = ? "
                    "ORDER BY b.node_uid",
                    (anchor["site"], anchor["opc_item_path"]),
                ).fetchall()
            ]
            evidence = {
                "opc_item_path": anchor["opc_item_path"],
                "site": anchor["site"],
                **self._candidate_evidence(candidates),
            }
            candidate_sources = [
                candidate["source_id"] for candidate in candidates
            ]
            if len(candidates) == 1:
                self.add(
                    anchor_uid=anchor["node_uid"],
                    source_uid=candidates[0]["node_uid"],
                    target_uid=anchor["node_uid"],
                    role="RAW_TO_ORGANIZED",
                    state="EXACT",
                    evidence_type="OPC_ITEM_PATH_EXACT",
                    evidence=evidence,
                    candidate_source_ids=candidate_sources,
                )
            else:
                self.add(
                    anchor_uid=anchor["node_uid"],
                    source_uid=anchor["node_uid"],
                    target_uid=None,
                    role="RAW_TO_ORGANIZED",
                    state="AMBIGUOUS" if candidates else "UNRESOLVED",
                    evidence_type="OPC_ITEM_PATH_EXACT",
                    evidence=evidence,
                    candidate_source_ids=candidate_sources,
                )

    def discover_udt_membership(self) -> None:
        for (site, type_key), definition in sorted(
            self.udt.registry.canonical.items()
        ):
            chain = self.udt.registry.inheritance_chain(site, type_key)
            direct = set(definition.direct_members)
            for member_name in sorted(
                self.udt.registry.effective_members(site, type_key)
            ):
                member_uid = self.udt.effective_member_uid(
                    site, type_key, (member_name,)
                )
                if member_uid is None:
                    continue
                self.add(
                    anchor_uid=f"{site}:{type_key}:{member_name}",
                    source_uid=str(definition.id),
                    target_uid=member_uid,
                    role="GENERIC",
                    state="EXACT",
                    evidence_type="UDT_DEFINITION_MEMBERSHIP",
                    evidence={
                        "site": site,
                        "type_id": type_key,
                        "member_path": member_name,
                        "inheritance_chain": chain,
                        "inherited": member_name not in direct,
                    },
                    candidate_source_ids=(definition.file_id,),
                )

    def discover_instance_types(self) -> None:
        instances = self.conn.execute(
            "SELECT b.node_uid, b.source_id, b.type_id, b.path_at_import, "
            "s.site, s.provider_name "
            "FROM baseline_nodes b JOIN sources s ON s.id = b.source_id "
            "WHERE b.tag_type = 'UdtInstance' "
            "AND b.type_id IS NOT NULL AND b.type_id <> '' "
            "ORDER BY b.node_uid"
        ).fetchall()
        for instance in instances:
            site = instance["site"]
            type_key = instance["type_id"]
            definition = self.udt.registry.canonical_get(site, type_key)
            if definition is None:
                self.add(
                    anchor_uid=instance["node_uid"],
                    source_uid=instance["node_uid"],
                    target_uid=None,
                    role="MEMBER_TO_UNS_INSTANCE",
                    state="UNRESOLVED",
                    evidence_type="INSTANCE_TYPE",
                    evidence={
                        "site": site,
                        "type_id": type_key,
                        "definition_found": False,
                    },
                )
                continue

            members = sorted(
                self.udt.registry.effective_members(site, type_key)
            )
            if not members:
                self.add(
                    anchor_uid=instance["node_uid"],
                    source_uid=str(definition.id),
                    target_uid=instance["node_uid"],
                    role="GENERIC",
                    state="EXACT",
                    evidence_type="INSTANCE_TYPE",
                    evidence={
                        "site": site,
                        "type_id": type_key,
                        "definition_found": True,
                        "member_path": None,
                    },
                    candidate_source_ids=(
                        definition.file_id,
                        instance["source_id"],
                    ),
                )
                continue

            for member_name in members:
                member_uid = self.udt.effective_member_uid(
                    site, type_key, (member_name,)
                )
                if member_uid is None:
                    continue
                self.add(
                    anchor_uid=f"{instance['node_uid']}:{member_name}",
                    source_uid=member_uid,
                    target_uid=instance["node_uid"],
                    role="MEMBER_TO_UNS_INSTANCE",
                    state="EXACT",
                    evidence_type="INSTANCE_TYPE",
                    evidence={
                        "site": site,
                        "type_id": type_key,
                        "definition_node_uid": str(definition.id),
                        "member_path": member_name,
                        "inheritance_chain": (
                            self.udt.registry.inheritance_chain(site, type_key)
                        ),
                    },
                    candidate_source_ids=(
                        definition.file_id,
                        instance["source_id"],
                    ),
                )

    def run(self) -> Dict[str, _Relation]:
        self.discover_source_tag_paths()
        self.discover_opc_paths()
        self.discover_udt_membership()
        self.discover_instance_types()
        return self.relations


_UPSERT_SQL = """
INSERT INTO relationships (
    relationship_uid, source_node_uid, target_node_uid, role, state,
    evidence_type, evidence_json, origin, confidence, confirmed_by,
    confirmed_at, created_at, updated_at, source_hashes_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
ON CONFLICT(relationship_uid) DO UPDATE SET
    source_node_uid = excluded.source_node_uid,
    target_node_uid = excluded.target_node_uid,
    role = excluded.role,
    state = excluded.state,
    evidence_type = excluded.evidence_type,
    evidence_json = excluded.evidence_json,
    origin = excluded.origin,
    confidence = excluded.confidence,
    updated_at = CASE
        WHEN relationships.source_node_uid = excluded.source_node_uid
         AND relationships.target_node_uid IS excluded.target_node_uid
         AND relationships.role = excluded.role
         AND relationships.state = excluded.state
         AND relationships.evidence_type = excluded.evidence_type
         AND relationships.evidence_json = excluded.evidence_json
         AND relationships.origin = excluded.origin
         AND relationships.confidence IS excluded.confidence
         AND relationships.source_hashes_json = excluded.source_hashes_json
        THEN relationships.updated_at
        ELSE excluded.updated_at
    END,
    source_hashes_json = excluded.source_hashes_json
"""


def discover_exact(project: Project) -> Dict[str, Any]:
    """Ponovno izracunaj vse ``AUTO_EXACT`` relacije projekta.

    Rocnih oziroma prihodnjih suggestion vrstic ne spreminja. Ponovni zagon nad
    nespremenjenim baselineom ohrani identitete in casovne oznake.
    """
    relations = _Discovery(project).run()
    now = _now()
    existing = {
        row["relationship_uid"]
        for row in project.conn.execute(
            "SELECT relationship_uid FROM relationships WHERE origin = ?",
            (_AUTO_ORIGIN,),
        ).fetchall()
    }
    current = set(relations)
    stale = sorted(existing - current)

    with project.conn:
        if stale:
            project.conn.executemany(
                "DELETE FROM relationships WHERE relationship_uid = ? "
                "AND origin = ?",
                ((relationship_uid, _AUTO_ORIGIN) for relationship_uid in stale),
            )
        project.conn.executemany(
            _UPSERT_SQL,
            (
                (
                    relation.relationship_uid,
                    relation.source_node_uid,
                    relation.target_node_uid,
                    relation.role,
                    relation.state,
                    relation.evidence_type,
                    relation.evidence_json,
                    relation.origin,
                    relation.confidence,
                    now,
                    now,
                    relation.source_hashes_json,
                )
                for relation in sorted(
                    relations.values(),
                    key=lambda item: item.relationship_uid,
                )
            ),
        )

    by_state: Dict[str, int] = {}
    by_evidence_type: Dict[str, int] = {}
    for relation in relations.values():
        by_state[relation.state] = by_state.get(relation.state, 0) + 1
        by_evidence_type[relation.evidence_type] = (
            by_evidence_type.get(relation.evidence_type, 0) + 1
        )
    return {
        "total": len(relations),
        "inserted_or_updated": len(relations),
        "removed": len(stale),
        "by_state": dict(sorted(by_state.items())),
        "by_evidence_type": dict(sorted(by_evidence_type.items())),
    }


def _validate_filter(value: Optional[str], allowed: Sequence[str], name: str) -> None:
    if value is not None and value not in allowed:
        raise RelationshipError(
            f"Neveljaven {name} {value!r}; dovoljeno: {', '.join(allowed)}"
        )


def query_relationships(
    project: Project,
    *,
    node_uid: Optional[str] = None,
    role: Optional[str] = None,
    state: Optional[str] = None,
    evidence_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """Paginirano vrni relacije z dokazom in kontekstom obeh vozlisc."""
    _validate_filter(role, RELATIONSHIP_ROLES, "role")
    _validate_filter(state, RELATIONSHIP_STATES, "state")
    _validate_filter(evidence_type, EVIDENCE_TYPES, "evidence_type")
    if limit < 1 or limit > MAX_RELATIONSHIP_PAGE_SIZE:
        raise RelationshipError(
            f"limit mora biti med 1 in {MAX_RELATIONSHIP_PAGE_SIZE}"
        )
    if offset < 0:
        raise RelationshipError("offset ne sme biti negativen")

    clauses: List[str] = []
    params: List[Any] = []
    if node_uid is not None:
        clauses.append(
            "(r.source_node_uid = ? OR r.target_node_uid = ?)"
        )
        params.extend((node_uid, node_uid))
    for column, value in (
        ("role", role),
        ("state", state),
        ("evidence_type", evidence_type),
    ):
        if value is not None:
            clauses.append(f"r.{column} = ?")
            params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""

    total = project.conn.execute(
        "SELECT COUNT(*) FROM relationships r" + where,
        params,
    ).fetchone()[0]
    rows = project.conn.execute(
        "SELECT r.*, "
        "src.name AS source_name, src.path_at_import AS source_path, "
        "ss.provider_name AS source_provider, ss.site AS source_site, "
        "ss.kind AS source_kind, "
        "dst.name AS target_name, dst.path_at_import AS target_path, "
        "ts.provider_name AS target_provider, ts.site AS target_site, "
        "ts.kind AS target_kind "
        "FROM relationships r "
        "LEFT JOIN baseline_nodes src "
        "ON src.node_uid = r.source_node_uid "
        "LEFT JOIN sources ss ON ss.id = src.source_id "
        "LEFT JOIN baseline_nodes dst "
        "ON dst.node_uid = r.target_node_uid "
        "LEFT JOIN sources ts ON ts.id = dst.source_id "
        + where
        + " ORDER BY r.relationship_uid LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item["source_hashes"] = json.loads(
            item.pop("source_hashes_json")
        )
        results.append(item)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_previous": offset > 0,
        "has_next": offset + len(results) < total,
        "results": results,
    }
