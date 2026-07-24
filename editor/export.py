"""Omejen deterministicni Ignition 8.3 JSON izvoz (mejnik H1)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .project import Project, ProjectError
from .simulation import SimTree
from .import_service import import_source
from .project import create_project
from .validation import validate_project


class ExportError(ProjectError):
    """Neveljaven obseg ali cilj izvoza."""


def _all_children(tree: SimTree, parent_uid: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = tree.children(parent_uid, limit=500, offset=offset)
        rows.extend(page["results"])
        if not page["has_next"]:
            return rows
        offset += len(page["results"])


def compute_export_scope(project: Project, selection_uid: str) -> Dict[str, Any]:
    tree = SimTree(project)
    root = tree.details(selection_uid)
    node_uids: List[str] = []
    stack = [selection_uid]
    while stack:
        uid = stack.pop()
        node_uids.append(uid)
        children = _all_children(tree, uid)
        stack.extend(
            row["node_uid"] for row in reversed(children)
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
    children = _all_children(tree, node_uid)
    if children:
        result["tags"] = [
            _serialize_node(tree, child["node_uid"])
            for child in children
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
        children = _all_children(tree, root_uid)
        tags = [
            _serialize_node(tree, row["node_uid"])
            for row in children
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


def compute_full_export_scopes(project: Project) -> List[Dict[str, Any]]:
    """Return one deterministic scope per imported provider/source root."""
    roots = project.conn.execute(
        "SELECT b.node_uid FROM baseline_nodes b "
        "JOIN sources s ON s.id=b.source_id "
        "WHERE b.parent_uid IS NULL "
        "ORDER BY s.site, s.provider_name, s.kind, b.node_uid"
    ).fetchall()
    if not roots:
        raise ExportError("Projekt nima uvozenih providerjev")
    return [compute_export_scope(project, row["node_uid"]) for row in roots]


def _safe_filename(value: Optional[str]) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "provider").strip("._")
    return safe or "provider"


def verify_ignition_reexport(
    project: Project,
    selection_uid: str,
    reexport_path: str,
) -> Dict[str, Any]:
    """Compare a user-supplied Ignition re-export with the planned simulated scope."""
    path = os.path.abspath(reexport_path)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            actual_payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"Ignition re-exporta ni mogoce prebrati: {exc}") from exc
    if not isinstance(actual_payload, dict):
        raise ExportError("Ignition re-export mora imeti JSON objekt v korenu")
    if actual_payload.get("tagType") == "Provider":
        actual_payload = {"tags": actual_payload.get("tags") or []}
    if not isinstance(actual_payload.get("tags"), list):
        raise ExportError("Ignition re-export nima korenskega seznama 'tags'")

    scope = compute_export_scope(project, selection_uid)
    expected_payload = serialize_ignition_json(project, scope)
    expected = _flatten_export_tags(expected_payload)
    actual = _flatten_export_tags(actual_payload)
    expected_by_path = {row["path"]: row for row in expected}
    actual_by_path = {row["path"]: row for row in actual}
    expected_path_counts = Counter(row["path"] for row in expected)
    actual_path_counts = Counter(row["path"] for row in actual)
    duplicate_expected = sorted(
        path for path, count in expected_path_counts.items() if count > 1
    )
    duplicate_actual = sorted(
        path for path, count in actual_path_counts.items() if count > 1
    )
    missing = sorted(set(expected_by_path) - set(actual_by_path))
    extra = sorted(set(actual_by_path) - set(expected_by_path))
    changed = sorted(
        path
        for path in set(expected_by_path) & set(actual_by_path)
        if expected_by_path[path] != actual_by_path[path]
    )
    matches = not (
        missing
        or extra
        or changed
        or duplicate_expected
        or duplicate_actual
        or len(expected) != len(actual)
    )
    return {
        "status": (
            "IGNITION_REEXPORT_VERIFIED"
            if matches
            else "IGNITION_REEXPORT_MISMATCH"
        ),
        "matches": matches,
        "path": path,
        "sha256": hashlib.sha256(
            canonical_export_bytes(actual_payload)
        ).hexdigest(),
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing_paths": missing,
        "extra_paths": extra,
        "changed_paths": changed,
        "duplicate_expected_paths": duplicate_expected,
        "duplicate_actual_paths": duplicate_actual,
        "scope": scope,
    }


def write_production_package(
    project: Project,
    output_dir: str,
    *,
    selection_uid: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a validated limited or full multi-provider production package."""
    scopes = (
        [compute_export_scope(project, selection_uid)]
        if selection_uid is not None
        else compute_full_export_scopes(project)
    )
    selected_uids = {
        uid for scope in scopes for uid in scope["node_uids"]
    }
    validation = validate_project(project, node_uids=selected_uids)
    if validation["status"] != "VALID":
        raise ExportError(
            "Produkcijski izvoz blokira "
            f"{validation['counts']['ERROR']} validacijskih napak"
        )
    mode = "limited" if selection_uid is not None else "full"
    output = os.path.abspath(output_dir)
    entries = []
    used_names = set()
    for index, scope in enumerate(scopes):
        if mode == "limited":
            filename = "tags.json"
        else:
            source = project.conn.execute(
                "SELECT s.site, s.provider_name FROM baseline_nodes b "
                "JOIN sources s ON s.id=b.source_id WHERE b.node_uid=?",
                (scope["selection_uid"],),
            ).fetchone()
            stem = _safe_filename(
                f"{source['site']}_{source['provider_name']}"
            )
            filename = f"tags_{stem}.json"
            if filename in used_names:
                filename = f"tags_{stem}_{index + 1}.json"
        used_names.add(filename)
        payload = serialize_ignition_json(project, scope)
        data = canonical_export_bytes(payload)
        verified = verify_round_trip(project, scope["selection_uid"])
        if not verified["matches"]:
            raise ExportError(
                f"Notranji round-trip se ne ujema za {scope['provider_name']}"
            )
        entries.append(
            {
                "filename": filename,
                "scope": scope,
                "data": data,
                "sha256": hashlib.sha256(data).hexdigest(),
                "round_trip_status": verified["status"],
            }
        )

    targets = [os.path.join(output, row["filename"]) for row in entries]
    targets.append(os.path.join(output, "manifest.json"))
    existing = [path for path in targets if os.path.exists(path)]
    if existing:
        raise ExportError(
            "Produkcijski paket ne prepisuje obstojecih datotek: "
            + ", ".join(existing)
        )
    os.makedirs(output, exist_ok=True)
    for entry in entries:
        with open(os.path.join(output, entry["filename"]), "wb") as handle:
            handle.write(entry["data"])
    manifest = {
        "format": "ignition-tag-editor-production-package",
        "format_version": 2,
        "target_ignition_version": "8.3",
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_uid": project.project_uid,
        "validation": {
            "status": validation["status"],
            "counts": validation["counts"],
            "checked_nodes": validation["checked_nodes"],
        },
        "exports": [
            {
                "filename": row["filename"],
                "sha256": row["sha256"],
                "round_trip_status": row["round_trip_status"],
                "scope": {
                    key: value
                    for key, value in row["scope"].items()
                    if key != "node_uids"
                },
            }
            for row in entries
        ],
        "post_import_verification": {
            "required": True,
            "method": "Re-export imported scope from Ignition and compare in app.",
        },
    }
    manifest_path = os.path.join(output, "manifest.json")
    with open(manifest_path, "wb") as handle:
        handle.write(canonical_export_bytes(manifest))
    return {
        "mode": mode,
        "output_dir": output,
        "manifest_path": manifest_path,
        "files": [os.path.join(output, row["filename"]) for row in entries],
        "validation": validation,
        "exports": [
            {key: value for key, value in row.items() if key != "data"}
            for row in entries
        ],
    }
