"""Lazy efektivno drevo in strukturiran diff (mejnik G1)."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from .operations import (
    CREATE_OPERATION_TYPES,
    OperationError,
    list_operations,
)
from .project import Project
from .udt_context import ProjectUdtContext

MAX_SIM_PAGE_SIZE = 500


class SimulationError(OperationError):
    """Neveljavna poizvedba simuliranega drevesa."""


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


class SimTree:
    """Lahki overlay veljavnih operacij nad baseline poizvedbami."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.all_operations = list_operations(project)
        self.operations = [
            operation
            for operation in self.all_operations
            if operation["status"] == "VALID"
        ]
        self.by_target: Dict[str, List[Dict[str, Any]]] = {}
        self.creates: Dict[str, Dict[str, Any]] = {}
        self.moves: Dict[str, Dict[str, Any]] = {}
        for operation in self.operations:
            self.by_target.setdefault(
                operation["target_node_uid"], []
            ).append(operation)
            if operation["op_type"] in CREATE_OPERATION_TYPES:
                self.creates[operation["target_node_uid"]] = operation
            elif operation["op_type"] == "MOVE_TAG":
                self.moves[operation["target_node_uid"]] = operation
        self._node_cache: Dict[str, Dict[str, Any]] = {}
        self._path_cache: Dict[str, Tuple[str, int]] = {}

    def _baseline_node(self, node_uid: str) -> Optional[Dict[str, Any]]:
        row = self.project.conn.execute(
            "SELECT * FROM baseline_nodes WHERE node_uid = ?",
            (node_uid,),
        ).fetchone()
        if row is None:
            return None
        node = dict(row)
        node["properties"] = _json_object(node["raw_json"])
        node["is_new"] = False
        return node

    def _created_node(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        payload = operation["payload"]
        properties = deepcopy(payload["props"])
        properties["name"] = payload["name"]
        properties["tagType"] = payload["tagType"]
        parent = self._node(payload["parent_uid"])
        return {
            "node_uid": operation["target_node_uid"],
            "provider_uid": parent.get("provider_uid"),
            "parent_uid": payload["parent_uid"],
            "sibling_index": 1_000_000 + operation["seq"],
            "depth": 0,
            "path_at_import": "",
            "name": payload["name"],
            "tag_type": payload["tagType"],
            "data_type": properties.get("dataType"),
            "value_source": properties.get("valueSource"),
            "type_id": properties.get("typeId"),
            "opc_item_path": properties.get("opcItemPath"),
            "opc_server": properties.get("opcServer"),
            "source_tag_path": properties.get("sourceTagPath"),
            "raw_json": json.dumps(
                properties,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "source_id": parent.get("source_id"),
            "properties": properties,
            "is_new": True,
        }

    def _node(self, node_uid: str) -> Dict[str, Any]:
        cached = self._node_cache.get(node_uid)
        if cached is not None:
            return cached
        create = self.creates.get(node_uid)
        node = (
            self._created_node(create)
            if create is not None
            else self._baseline_node(node_uid)
        )
        if node is None:
            raise SimulationError(f"Neznan simuliran node_uid: {node_uid}")
        for operation in self.by_target.get(node_uid, []):
            payload = operation["payload"]
            op_type = operation["op_type"]
            if op_type in CREATE_OPERATION_TYPES:
                continue
            if op_type == "RENAME_TAG":
                node["name"] = payload["new_name"]
                node["properties"]["name"] = payload["new_name"]
            elif op_type == "MOVE_TAG":
                node["parent_uid"] = payload["new_parent_uid"]
                node["sibling_index"] = payload["new_sibling_index"]
            elif op_type == "UPDATE_PROPERTY":
                selector = payload.get("pointer")
                parts = (
                    [
                        item.replace("~1", "/").replace("~0", "~")
                        for item in selector[1:].split("/")
                    ]
                    if selector
                    else [payload["key"]]
                )
                current = node["properties"]
                for part in parts[:-1]:
                    current = current.setdefault(part, {})
                current[parts[-1]] = deepcopy(payload["new_value"])
                column = {
                    "dataType": "data_type",
                    "valueSource": "value_source",
                    "typeId": "type_id",
                    "opcItemPath": "opc_item_path",
                    "opcServer": "opc_server",
                }.get(parts[0])
                if column and len(parts) == 1:
                    node[column] = payload["new_value"]
            elif op_type == "UPDATE_SOURCE_PATH":
                node["source_tag_path"] = payload["new_value"]
                node["properties"]["sourceTagPath"] = payload["new_value"]
            elif op_type == "UPDATE_PARAMETERS":
                node["properties"]["parameters"] = deepcopy(payload["params"])
        path, depth = self._effective_path(node_uid)
        node["effective_path"] = path
        node["effective_depth"] = depth
        node["has_operations"] = node_uid in self.by_target
        self._node_cache[node_uid] = node
        return node

    def _effective_path(
        self,
        node_uid: str,
        seen: Optional[set[str]] = None,
    ) -> Tuple[str, int]:
        cached = self._path_cache.get(node_uid)
        if cached is not None:
            return cached
        seen = seen or set()
        if node_uid in seen:
            raise SimulationError("Cikel v simuliranem drevesu")
        seen.add(node_uid)
        create = self.creates.get(node_uid)
        node = (
            self._created_node(create)
            if create is not None
            else self._baseline_node(node_uid)
        )
        if node is None:
            raise SimulationError(f"Neznan simuliran node_uid: {node_uid}")
        for operation in self.by_target.get(node_uid, []):
            if operation["op_type"] == "RENAME_TAG":
                node["name"] = operation["payload"]["new_name"]
            elif operation["op_type"] == "MOVE_TAG":
                node["parent_uid"] = operation["payload"]["new_parent_uid"]
        parent_uid = node.get("parent_uid")
        if parent_uid is None:
            result = (node.get("name") or "", 0)
        else:
            parent_path, parent_depth = self._effective_path(
                parent_uid, seen
            )
            name = node.get("name") or ""
            result = (
                f"{parent_path}/{name}" if parent_path else name,
                parent_depth + 1,
            )
        self._path_cache[node_uid] = result
        return result

    def children(
        self,
        parent_uid: Optional[str],
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        if not isinstance(limit, int) or not 1 <= limit <= MAX_SIM_PAGE_SIZE:
            raise SimulationError(
                f"limit mora biti med 1 in {MAX_SIM_PAGE_SIZE}"
            )
        if not isinstance(offset, int) or offset < 0:
            raise SimulationError("offset mora biti nenegativno celo stevilo")
        if parent_uid is not None:
            self._node(parent_uid)
        if parent_uid is None:
            baseline_rows = self.project.conn.execute(
                "SELECT node_uid FROM baseline_nodes WHERE parent_uid IS NULL"
            ).fetchall()
        else:
            baseline_rows = self.project.conn.execute(
                "SELECT node_uid FROM baseline_nodes WHERE parent_uid = ?",
                (parent_uid,),
            ).fetchall()
        candidate_uids = {row["node_uid"] for row in baseline_rows}
        candidate_uids.update(
            uid
            for uid, operation in self.creates.items()
            if operation["payload"]["parent_uid"] == parent_uid
        )
        candidate_uids.update(
            uid
            for uid, operation in self.moves.items()
            if operation["payload"]["new_parent_uid"] == parent_uid
        )
        rows = []
        for uid in candidate_uids:
            node = self._node(uid)
            if node.get("parent_uid") != parent_uid:
                continue
            item = {
                key: node.get(key)
                for key in (
                    "node_uid",
                    "provider_uid",
                    "parent_uid",
                    "sibling_index",
                    "name",
                    "tag_type",
                    "type_id",
                    "effective_path",
                    "effective_depth",
                    "is_new",
                    "has_operations",
                )
            }
            item["has_children"] = self._has_children(uid)
            rows.append(item)
        rows.sort(
            key=lambda row: (
                row["sibling_index"],
                row["node_uid"],
            )
        )
        page = rows[offset:offset + limit]
        return {
            "parent_uid": parent_uid,
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "has_previous": offset > 0,
            "has_next": offset + len(page) < len(rows),
            "results": page,
        }

    def _has_children(self, node_uid: str) -> bool:
        baseline = self.project.conn.execute(
            "SELECT node_uid FROM baseline_nodes WHERE parent_uid = ?",
            (node_uid,),
        ).fetchall()
        if any(
            self._node(row["node_uid"]).get("parent_uid") == node_uid
            for row in baseline
        ):
            return True
        return any(
            operation["payload"].get("parent_uid") == node_uid
            for operation in self.creates.values()
        ) or any(
            operation["payload"].get("new_parent_uid") == node_uid
            for operation in self.moves.values()
        )

    def details(self, node_uid: str) -> Dict[str, Any]:
        node = deepcopy(self._node(node_uid))
        children = self.children(node_uid, limit=1)
        parent = (
            deepcopy(self._node(node["parent_uid"]))
            if node.get("parent_uid")
            else None
        )
        baseline_effective: Dict[str, Any] = {}
        udt_context = None
        if not node["is_new"]:
            resolved = ProjectUdtContext(self.project).resolve(node_uid)
            baseline_effective = resolved["effective_properties"]
            udt_context = resolved["udt_context"]
        provider = None
        if node.get("source_id") is not None:
            row = self.project.conn.execute(
                "SELECT id AS source_id, path, sha256, provider_name, site, "
                "kind, import_session, imported_at FROM sources WHERE id = ?",
                (node["source_id"],),
            ).fetchone()
            provider = dict(row) if row else None
        return {
            **node,
            "path_at_import": node.get("path_at_import"),
            "effective_properties": _deep_merge(
                baseline_effective, node["properties"]
            ),
            "udt_context": udt_context,
            "parent": (
                {
                    "node_uid": parent["node_uid"],
                    "name": parent["name"],
                    "effective_path": parent["effective_path"],
                }
                if parent else None
            ),
            "child_count": children["total"],
            "has_children": children["total"] > 0,
            "provider": provider,
            "operations": deepcopy(self.by_target.get(node_uid, [])),
        }


def sim_children(
    project: Project,
    parent_uid: Optional[str],
    *,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    return SimTree(project).children(
        parent_uid,
        limit=limit,
        offset=offset,
    )


def sim_details(project: Project, node_uid: str) -> Dict[str, Any]:
    return SimTree(project).details(node_uid)


def diff(project: Project) -> Dict[str, Any]:
    """Vrni strukturiran diff iz veljavnega dnevnika in efektivnega overlayja."""
    tree = SimTree(project)
    categories: Dict[str, List[Dict[str, Any]]] = {
        "added": [],
        "renamed": [],
        "moved": [],
        "property_changed": [],
        "reference_changed": [],
        "deleted": [],
    }
    for operation in tree.operations:
        op_type = operation["op_type"]
        uid = operation["target_node_uid"]
        after = tree._node(uid)
        common = {
            "operation_uid": operation["operation_uid"],
            "seq": operation["seq"],
            "target_node_uid": uid,
            "op_type": op_type,
        }
        if op_type in CREATE_OPERATION_TYPES:
            categories["added"].append(
                {
                    **common,
                    "before": None,
                    "after": {
                        "name": after["name"],
                        "parent_uid": after["parent_uid"],
                        "path": after["effective_path"],
                        "tag_type": after["tag_type"],
                        "properties": deepcopy(after["properties"]),
                    },
                }
            )
        elif op_type == "RENAME_TAG":
            categories["renamed"].append(
                {
                    **common,
                    "before": operation["original"]["name"],
                    "after": after["name"],
                    "path_after": after["effective_path"],
                }
            )
        elif op_type == "MOVE_TAG":
            categories["moved"].append(
                {
                    **common,
                    "before": deepcopy(operation["original"]),
                    "after": {
                        "parent_uid": after["parent_uid"],
                        "sibling_index": after["sibling_index"],
                        "path": after["effective_path"],
                    },
                }
            )
        elif op_type == "UPDATE_PROPERTY":
            categories["property_changed"].append(
                {
                    **common,
                    "selector": (
                        operation["payload"].get("pointer")
                        or operation["payload"].get("key")
                    ),
                    "before": operation["original"].get("value"),
                    "after": operation["payload"]["new_value"],
                }
            )
        elif op_type == "UPDATE_SOURCE_PATH":
            categories["reference_changed"].append(
                {
                    **common,
                    "before": operation["original"].get("flattened"),
                    "after": operation["payload"]["new_value"],
                }
            )
        elif op_type == "UPDATE_PARAMETERS":
            categories["property_changed"].append(
                {
                    **common,
                    "selector": "parameters",
                    "before": operation["original"].get("params"),
                    "after": deepcopy(operation["payload"]["params"]),
                }
            )
        elif op_type == "DELETE_TAG":
            categories["deleted"].append(
                {
                    **common,
                    "before": operation["original"].get("node"),
                    "after": None,
                }
            )
    counts = {
        category: len(items)
        for category, items in categories.items()
    }
    skipped = [
        {
            "operation_uid": operation["operation_uid"],
            "status": operation["status"],
            "reason": operation["reason"],
        }
        for operation in tree.all_operations
        if operation["status"] != "VALID"
    ]
    return {
        "categories": categories,
        "counts": counts,
        "total": sum(counts.values()),
        "skipped": skipped,
    }
