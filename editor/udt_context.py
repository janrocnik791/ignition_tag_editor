"""Projektni adapter za efektivni UDT kontekst (mejnik C4).

Obstojeci ``analyzer.udt_resolver.UdtRegistry`` vsebuje avtoritativno logiko za
dedovanje clanov, parametrov in inheritance chain. Ta adapter zgradi isti registry
iz projektnih tabel ``baseline_nodes``/``sources`` in nato doda vrednosti parametrov
ter efektivne lastnosti iz ohranjenega ``raw_json``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analyzer.udt_resolver import DefRecord, UdtRegistry, type_key_from_full_path

from .project import Project


def _json_object(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


class ProjectUdtContext:
    """Predpomnjen, site-aware UDT resolver za en odprt projekt."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.registry = UdtRegistry(conn=project.conn)
        self._definition_raw: Dict[str, Dict[str, Any]] = {}
        self._build_registry()

    def _build_registry(self) -> None:
        definitions = self.project.conn.execute(
            "SELECT b.node_uid, b.source_id, b.path_at_import, b.type_id, "
            "b.raw_json, s.site, s.kind "
            "FROM baseline_nodes b JOIN sources s ON s.id = b.source_id "
            "WHERE b.tag_type = 'UdtType' "
            "ORDER BY s.site, b.path_at_import, s.kind, b.node_uid"
        ).fetchall()
        members: Dict[str, set[str]] = {}
        for row in self.project.conn.execute(
            "SELECT c.parent_uid, c.name "
            "FROM baseline_nodes c JOIN baseline_nodes p ON p.node_uid = c.parent_uid "
            "WHERE p.tag_type = 'UdtType'"
        ).fetchall():
            members.setdefault(row["parent_uid"], set()).add(row["name"])

        for row in definitions:
            node_uid = row["node_uid"]
            raw = _json_object(row["raw_json"])
            self._definition_raw[node_uid] = raw
            key = type_key_from_full_path(row["path_at_import"])
            params = raw.get("parameters")
            rec = DefRecord(
                id=node_uid,
                site=row["site"],
                file_id=row["source_id"],
                kind=row["kind"],
                full_path=row["path_at_import"],
                key=key,
                parent=row["type_id"] or None,
                params=set(params) if isinstance(params, dict) else set(),
                direct_members=members.get(node_uid, set()),
            )
            self.registry.copies.setdefault((row["site"], key), []).append(rec)

        for context_key, records in self.registry.copies.items():
            canonical = next(
                (record for record in records if record.kind == "udt"),
                records[0],
            )
            self.registry.canonical[context_key] = canonical

    def _node(self, node_uid: str) -> Optional[Dict[str, Any]]:
        row = self.project.conn.execute(
            "SELECT * FROM baseline_nodes WHERE node_uid = ?",
            (node_uid,),
        ).fetchone()
        return dict(row) if row else None

    def _ancestors(self, node_uid: str) -> List[Dict[str, Any]]:
        chain: List[Dict[str, Any]] = []
        current: Optional[str] = node_uid
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            node = self._node(current)
            if node is None:
                break
            chain.append(node)
            current = node["parent_uid"]
        return chain

    def _raw_for_definition(self, record: DefRecord) -> Dict[str, Any]:
        return self._definition_raw.get(str(record.id), {})

    def _definition_member_uid(
        self,
        root_uid: str,
        relative_path: Sequence[str],
    ) -> Optional[str]:
        current = root_uid
        for name in relative_path:
            row = self.project.conn.execute(
                "SELECT node_uid FROM baseline_nodes "
                "WHERE parent_uid = ? AND name = ? "
                "ORDER BY sibling_index, node_uid LIMIT 1",
                (current, name),
            ).fetchone()
            if row is None:
                return None
            current = row["node_uid"]
        return current

    def _definition_properties(
        self,
        site: str,
        type_key: str,
        relative_path: Sequence[str],
    ) -> Dict[str, Any]:
        effective: Dict[str, Any] = {}
        for key in reversed(self.registry.inheritance_chain(site, type_key)):
            record = self.registry.canonical_get(site, key)
            if record is None:
                continue
            if relative_path:
                member_uid = self._definition_member_uid(
                    str(record.id), relative_path
                )
                if member_uid is None:
                    continue
                member = self._node(member_uid)
                raw = _json_object(member["raw_json"]) if member else {}
            else:
                raw = self._raw_for_definition(record)
            effective = _deep_merge(effective, raw)
        return effective

    def _definition_parameters(
        self,
        site: str,
        type_key: str,
    ) -> Dict[str, Any]:
        effective: Dict[str, Any] = {}
        for key in reversed(self.registry.inheritance_chain(site, type_key)):
            record = self.registry.canonical_get(site, key)
            if record is None:
                continue
            params = self._raw_for_definition(record).get("parameters")
            if isinstance(params, dict):
                effective = _deep_merge(effective, params)
        return effective

    def _children_names(self, node_uid: str) -> List[str]:
        return [
            row["name"]
            for row in self.project.conn.execute(
                "SELECT name FROM baseline_nodes WHERE parent_uid = ? "
                "ORDER BY sibling_index, node_uid",
                (node_uid,),
            ).fetchall()
        ]

    def _subject(
        self,
        ancestors: List[Dict[str, Any]],
    ) -> Optional[Tuple[Dict[str, Any], str, str]]:
        # Najblizja instanca z eksplicitnim typeId ima prednost. Prazna typeId
        # gnezdena instanca podeduje kontekst prvega tipiziranega prednika.
        for node in ancestors:
            if node["tag_type"] == "UdtInstance" and node["type_id"]:
                return node, "instance", node["type_id"]
            if node["tag_type"] == "UdtType":
                return (
                    node,
                    "definition",
                    type_key_from_full_path(node["path_at_import"]),
                )
        return None

    def resolve(self, node_uid: str) -> Dict[str, Any]:
        """Vrni ``effective_properties`` in reader-facing ``udt_context``."""
        ancestors = self._ancestors(node_uid)
        if not ancestors:
            return {"effective_properties": {}, "udt_context": None}
        selected = ancestors[0]
        selected_raw = _json_object(selected["raw_json"])
        subject_info = self._subject(ancestors)
        if subject_info is None:
            return {
                "effective_properties": selected_raw,
                "udt_context": None,
            }

        subject, subject_kind, type_key = subject_info
        subject_index = next(
            index for index, node in enumerate(ancestors)
            if node["node_uid"] == subject["node_uid"]
        )
        relative_path = [
            node["name"] for node in reversed(ancestors[:subject_index])
        ]
        source = self.project.conn.execute(
            "SELECT site, provider_name, kind FROM sources WHERE id = ?",
            (selected["source_id"],),
        ).fetchone()
        site = source["site"] if source else ""
        definition = self.registry.canonical_get(site, type_key)
        definition_found = definition is not None

        effective_properties = self._definition_properties(
            site, type_key, relative_path
        )
        effective_properties = _deep_merge(effective_properties, selected_raw)

        subject_raw = _json_object(subject["raw_json"])
        local_params = subject_raw.get("parameters")
        local_parameters = local_params if isinstance(local_params, dict) else {}
        effective_parameters = self._definition_parameters(site, type_key)
        effective_parameters = _deep_merge(
            effective_parameters, local_parameters
        )

        definition_members = (
            self.registry.effective_members(site, type_key)
            if definition_found else set()
        )
        direct_members = set(definition.direct_members) if definition else set()
        local_members = set(self._children_names(subject["node_uid"]))
        effective_members = definition_members | local_members

        context = {
            "subject_kind": subject_kind,
            "selected_role": (
                subject_kind if not relative_path else f"{subject_kind}_member"
            ),
            "site": site,
            "provider_name": source["provider_name"] if source else None,
            "type_id": type_key,
            "definition_found": definition_found,
            "definition_node_uid": str(definition.id) if definition else None,
            "definition_kind": definition.kind if definition else None,
            "parent_type_id": definition.parent if definition else None,
            "inheritance_chain": self.registry.inheritance_chain(site, type_key),
            "direct_members": sorted(direct_members),
            "inherited_members": sorted(definition_members - direct_members),
            "local_members": sorted(local_members),
            "effective_members": sorted(effective_members),
            "declared_parameter_names": sorted(
                self.registry.effective_params(site, type_key)
            ),
            "local_parameters": deepcopy(local_parameters),
            "effective_parameters": effective_parameters,
            "subject_node_uid": subject["node_uid"],
            "member_path": "/".join(relative_path),
        }
        return {
            "effective_properties": effective_properties,
            "udt_context": context,
        }
