"""Omejen deterministicni Ignition 8.3 JSON izvoz (mejnik H1)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List

from .project import Project, ProjectError
from .simulation import SimTree
from .import_service import import_source
from .project import create_project


class ExportError(ProjectError):
    """Neveljaven obseg ali cilj izvoza."""


def compute_export_scope(project: Project, selection_uid: str) -> Dict[str, Any]:
    tree = SimTree(project)
    root = tree.details(selection_uid)
    node_uids: List[str] = []
    stack = [selection_uid]
    while stack:
        uid = stack.pop()
        node_uids.append(uid)
        children = tree.children(uid, limit=500)
        if children["has_next"]:
            raise ExportError(
                "Vozlisce ima vec kot 500 neposrednih otrok; razdeli izvoz"
            )
        stack.extend(
            row["node_uid"] for row in reversed(children["results"])
        )
    kinds = {
        tree.details(uid).get("tag_type") for uid in node_uids
    }
    return {
        "selection_uid": selection_uid,
        "selection_path": root["effective_path"],
        "provider_uid": root["provider_uid"],
        "provider_name": (
            root.get("provider") or {}
        ).get("provider_name"),
        "node_uids": node_uids,
        "node_count": len(node_uids),
        "selection_is_provider": root["tag_type"] == "Provider",
        "contains_udt_definition": "UdtType" in kinds,
        "contains_udt_instance": "UdtInstance" in kinds,
    }


def _serialize_node(tree: SimTree, node_uid: str) -> Dict[str, Any]:
    details = tree.details(node_uid)
    result = deepcopy(details["properties"])
    result["name"] = details["name"]
    result["tagType"] = details["tag_type"]
    if details.get("source_tag_path") is not None:
        existing = result.get("sourceTagPath")
        if isinstance(existing, dict) and "binding" in existing:
            existing["binding"] = details["source_tag_path"]
        elif "sourceTagPath" in result:
            result["sourceTagPath"] = details["source_tag_path"]
    children = tree.children(node_uid, limit=500)
    if children["has_next"]:
        raise ExportError("Izvoz podpira najvec 500 otrok na vozlisce")
    if children["results"]:
        result["tags"] = [
            _serialize_node(tree, child["node_uid"])
            for child in children["results"]
        ]
    else:
        result.pop("tags", None)
    return result


def serialize_ignition_json(
    project: Project,
    scope: Dict[str, Any],
) -> Dict[str, Any]:
    tree = SimTree(project)
    root_uid = scope["selection_uid"]
    root = tree.details(root_uid)
    if root["tag_type"] == "Provider":
        children = tree.children(root_uid, limit=500)
        tags = [
            _serialize_node(tree, row["node_uid"])
            for row in children["results"]
        ]
    else:
        tags = [_serialize_node(tree, root_uid)]
    return {"tags": tags}


def canonical_export_bytes(payload: Dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_package(
    project: Project,
    selection_uid: str,
    output_dir: str,
) -> Dict[str, Any]:
    scope = compute_export_scope(project, selection_uid)
    payload = serialize_ignition_json(project, scope)
    data = canonical_export_bytes(payload)
    os.makedirs(output_dir, exist_ok=True)
    tags_path = os.path.join(output_dir, "tags.json")
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(tags_path, "wb") as handle:
        handle.write(data)
    manifest = {
        "format": "ignition-tag-editor-package",
        "format_version": 1,
        "target_ignition_version": "8.3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_uid": project.project_uid,
        "scope": {
            key: value for key, value in scope.items()
            if key != "node_uids"
        },
        "tags_file": "tags.json",
        "tags_sha256": hashlib.sha256(data).hexdigest(),
        "warnings": (
            [
                "UDT instances do not include their definitions automatically; "
                "import definitions first."
            ]
            if scope["contains_udt_instance"]
            and not scope["contains_udt_definition"]
            else []
        ),
    }
    manifest_data = canonical_export_bytes(manifest)
    with open(manifest_path, "wb") as handle:
        handle.write(manifest_data)
    return {
        "scope": scope,
        "tags_path": tags_path,
        "manifest_path": manifest_path,
        "tags_sha256": manifest["tags_sha256"],
    }


def _flatten_export_tags(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any], parent_path: str, sibling_index: int) -> None:
        name = node.get("name") or ""
        path = f"{parent_path}/{name}" if parent_path else name
        properties = deepcopy(node)
        children = properties.pop("tags", None)
        rows.append({
            "path": path,
            "sibling_index": sibling_index,
            "properties": properties,
        })
        if isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, dict):
                    walk(child, path, index)

    for index, tag in enumerate(payload.get("tags") or []):
        if isinstance(tag, dict):
            walk(tag, "", index)
    return rows


def verify_round_trip(
    project: Project,
    selection_uid: str,
) -> Dict[str, Any]:
    """Ponovno uvozi serializiran JSON in primerja semantiko vozlisce-za-vozlisce."""
    scope = compute_export_scope(project, selection_uid)
    payload = serialize_ignition_json(project, scope)
    expected = _flatten_export_tags(payload)
    with tempfile.TemporaryDirectory(prefix="ignition-tag-roundtrip-") as tmp:
        source_path = os.path.join(tmp, "tags_ROUNDTRIP.json")
        with open(source_path, "wb") as handle:
            handle.write(canonical_export_bytes(payload))
        roundtrip = create_project(
            os.path.join(tmp, "project"),
            name="Export round-trip",
        )
        try:
            import_source(roundtrip, source_path, site="roundtrip")
            actual = []
            for row in roundtrip.conn.execute(
                "SELECT path_at_import, sibling_index, raw_json "
                "FROM baseline_nodes WHERE parent_uid IS NOT NULL "
                "ORDER BY depth, path_at_import, sibling_index, node_uid"
            ).fetchall():
                actual.append({
                    "path": row["path_at_import"],
                    "sibling_index": row["sibling_index"],
                    "properties": json.loads(row["raw_json"]),
                })
        finally:
            roundtrip.close()
    expected_sorted = sorted(
        expected,
        key=lambda row: (
            row["path"].count("/"),
            row["path"],
            row["sibling_index"],
        ),
    )
    matches = expected_sorted == actual
    return {
        "status": "EXPORT_VERIFIED" if matches else "EXPORT_MISMATCH",
        "matches": matches,
        "expected_count": len(expected_sorted),
        "actual_count": len(actual),
        "scope": scope,
        "expected": expected_sorted if not matches else None,
        "actual": actual if not matches else None,
    }
