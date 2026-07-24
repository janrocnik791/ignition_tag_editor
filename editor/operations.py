"""Model in storitve dnevnika operacij delovne kopije (mejnik F1).

Operacije so trajne, urejene in ločene od nespremenljivega baselinea. Ta modul
zna validirati ter zapisati operacijo, zgraditi in-memory simulacijsko stanje,
uporabiti posamezen korak in sestaviti njegov inverz. Lazy SimTree in diff sta
namerno predmet G1.
"""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Sequence, Tuple

from analyzer.udt_resolver import braces_balanced, provider_token

from .project import Project, ProjectError
from .udt_context import ProjectUdtContext

OPERATION_TYPES = (
    "CREATE_TAG",
    "CREATE_FOLDER",
    "CREATE_UDT_INSTANCE",
    "RENAME_TAG",
    "MOVE_TAG",
    "UPDATE_PROPERTY",
    "UPDATE_SOURCE_PATH",
    "UPDATE_PARAMETERS",
    "DELETE_TAG",
)
OPERATION_STATUSES = (
    "VALID",
    "CONFLICT",
    "STALE",
    "DEFERRED",
)
CREATE_OPERATION_TYPES = frozenset(
    {"CREATE_TAG", "CREATE_FOLDER", "CREATE_UDT_INSTANCE"}
)
_INTERNAL_OPERATION_TYPES = frozenset({"RESTORE_TAG"})
_NAME_INVALID = re.compile(r"[\x00-\x1f\[\]/]")
_PROPERTY_TYPES: Dict[str, Tuple[type, ...]] = {
    "dataType": (str,),
    "valueSource": (str,),
    "typeId": (str,),
    "opcItemPath": (str,),
    "opcServer": (str,),
    "sourceTagPath": (str, dict),
    "documentation": (str,),
    "enabled": (bool,),
    "historyEnabled": (bool,),
    "engUnit": (str,),
    "deadband": (int, float),
    "deadbandMode": (str,),
    "scaleMode": (str,),
    "formatString": (str,),
    "tooltip": (str,),
}
_COLUMN_BY_PROPERTY = {
    "dataType": "data_type",
    "valueSource": "value_source",
    "typeId": "type_id",
    "opcItemPath": "opc_item_path",
    "opcServer": "opc_server",
    "sourceTagPath": "source_tag_path",
}
_CONTAINER_TAG_TYPES = frozenset(
    {"Provider", "Folder", "UdtType", "UdtInstance"}
)
_INTEGER_DATA_TYPES = frozenset(
    {"Int1", "Int2", "Int4", "Int8", "Byte", "Short", "Integer", "Long"}
)
_FLOAT_DATA_TYPES = frozenset({"Float4", "Float8", "Float", "Double"})
_SPECIALIZED_PROPERTIES = {
    "name": "RENAME_TAG",
    "tags": "CREATE/MOVE operacije",
    "parameters": "UPDATE_PARAMETERS",
    "sourceTagPath": "UPDATE_SOURCE_PATH",
    "tagType": "namensko CREATE operacijo",
}


class OperationError(ProjectError):
    """Neveljavna operacija ali poskodovan dnevnik operacij."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise OperationError(f"Vrednost ni veljaven JSON: {exc}") from exc


def _json_value(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return deepcopy(default)


def _decode_operation(row: Any) -> Dict[str, Any]:
    operation = dict(row)
    operation["payload"] = _json_value(
        operation.pop("payload_json", "{}"), {}
    )
    operation["original"] = _json_value(
        operation.pop("original_json", "{}"), {}
    )
    operation["depends_on"] = _json_value(
        operation.pop("depends_on_json", "[]"), []
    )
    raw_conflict = operation.pop("conflict_info", None)
    operation["conflict"] = (
        _json_value(raw_conflict, {}) if raw_conflict else None
    )
    return operation


def _require_actor(actor: str) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise OperationError("created_by mora vsebovati auditnega uporabnika")
    return actor.strip()


def _require_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise OperationError("payload mora biti JSON objekt")
    _canonical_json(payload)
    return deepcopy(payload)


def _valid_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise OperationError("Ime taga ne sme biti prazno")
    if name in (".", "..") or _NAME_INVALID.search(name):
        raise OperationError(
            "Ime taga vsebuje nedovoljen znak (/, [, ], ali kontrolni znak)"
        )
    return name


def _raw_properties(node: Dict[str, Any]) -> Dict[str, Any]:
    properties = node.get("properties")
    if isinstance(properties, dict):
        return properties
    raw = _json_value(node.get("raw_json"), {})
    properties = raw if isinstance(raw, dict) else {}
    node["properties"] = properties
    return properties


def load_baseline_state(project: Project) -> Dict[str, Dict[str, Any]]:
    """Nalozi kopijo baseline vozlisc za cisto in-memory uporabo."""
    state: Dict[str, Dict[str, Any]] = {}
    for row in project.conn.execute(
        "SELECT * FROM baseline_nodes ORDER BY depth, sibling_index, node_uid"
    ).fetchall():
        node = dict(row)
        node["properties"] = _json_value(node["raw_json"], {})
        node["_baseline"] = True
        state[node["node_uid"]] = node
    return state


def _state_row(row: Any) -> Dict[str, Any]:
    node = dict(row)
    node["properties"] = _json_value(node["raw_json"], {})
    node["_baseline"] = True
    return node


def _load_validation_state(
    project: Project,
    target_node_uid: Optional[str],
    payload: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Nalozi le kontekst, potreben za interaktivno validacijo.

    Semena so zahtevani target/starsi ter targeti vseh obstojecih operacij.
    Za njih nalozimo potomce (pot/cikel), prednike (provider/site) in neposredne
    otroke relevantnih starsev (enolicnost imen). Tako prva operacija nad velikim
    projektom ne materializira celotnega baselinea.
    """
    existing = ordered_operations(project)
    seed_uids = set()
    relevant_parents = set()

    def add_uid(value: Any) -> None:
        if isinstance(value, str) and value and not value.startswith("new:"):
            seed_uids.add(value)

    add_uid(target_node_uid)
    for field in ("parent_uid", "new_parent_uid"):
        value = payload.get(field)
        add_uid(value)
        if isinstance(value, str):
            relevant_parents.add(value)
    for operation in existing:
        add_uid(operation["target_node_uid"])
        for field in ("parent_uid", "new_parent_uid"):
            value = operation["payload"].get(field)
            add_uid(value)
            if isinstance(value, str):
                relevant_parents.add(value)

    state: Dict[str, Dict[str, Any]] = {}
    if seed_uids:
        placeholders = ",".join("?" for _ in seed_uids)
        descendants = project.conn.execute(
            "WITH RECURSIVE branch(node_uid) AS ("
            "SELECT node_uid FROM baseline_nodes WHERE node_uid IN "
            f"({placeholders}) "
            "UNION "
            "SELECT child.node_uid FROM baseline_nodes child "
            "JOIN branch parent ON child.parent_uid = parent.node_uid"
            ") SELECT b.* FROM baseline_nodes b JOIN branch r "
            "ON r.node_uid = b.node_uid",
            sorted(seed_uids),
        ).fetchall()
        ancestors = project.conn.execute(
            "WITH RECURSIVE chain(node_uid, parent_uid) AS ("
            "SELECT node_uid, parent_uid FROM baseline_nodes "
            f"WHERE node_uid IN ({placeholders}) "
            "UNION "
            "SELECT parent.node_uid, parent.parent_uid "
            "FROM baseline_nodes parent JOIN chain child "
            "ON parent.node_uid = child.parent_uid"
            ") SELECT b.* FROM baseline_nodes b JOIN chain r "
            "ON r.node_uid = b.node_uid",
            sorted(seed_uids),
        ).fetchall()
        for row in (*descendants, *ancestors):
            state[row["node_uid"]] = _state_row(row)

    for uid in seed_uids:
        node = state.get(uid)
        if node and node.get("parent_uid"):
            relevant_parents.add(node["parent_uid"])
    baseline_parents = sorted(
        uid for uid in relevant_parents
        if uid and not uid.startswith("new:")
    )
    if baseline_parents:
        placeholders = ",".join("?" for _ in baseline_parents)
        for row in project.conn.execute(
            "SELECT * FROM baseline_nodes WHERE parent_uid IN "
            f"({placeholders})",
            baseline_parents,
        ).fetchall():
            state[row["node_uid"]] = _state_row(row)

    for operation in existing:
        op_type = operation["op_type"]
        if op_type in CREATE_OPERATION_TYPES:
            if operation["payload"]["parent_uid"] in state:
                apply_operation_to_state(state, operation)
        elif operation["target_node_uid"] in state:
            new_parent = operation["payload"].get("new_parent_uid")
            if new_parent is None or new_parent in state:
                apply_operation_to_state(state, operation)
    return state


def _children(
    state: MutableMapping[str, Dict[str, Any]],
    parent_uid: Optional[str],
) -> List[Dict[str, Any]]:
    return sorted(
        (
            node
            for node in state.values()
            if node.get("parent_uid") == parent_uid
        ),
        key=lambda node: (
            node.get("sibling_index", 0),
            node["node_uid"],
        ),
    )


def _ensure_unique_name(
    state: MutableMapping[str, Dict[str, Any]],
    parent_uid: Optional[str],
    name: str,
    *,
    exclude_uid: Optional[str] = None,
) -> None:
    for sibling in _children(state, parent_uid):
        if (
            sibling["node_uid"] != exclude_uid
            and sibling.get("name") == name
        ):
            raise OperationError(
                f"Ime {name!r} ze obstaja med efektivnimi sorojenci"
            )


def _is_descendant(
    state: MutableMapping[str, Dict[str, Any]],
    possible_descendant_uid: str,
    ancestor_uid: str,
) -> bool:
    current: Optional[str] = possible_descendant_uid
    seen = set()
    while current is not None and current not in seen:
        if current == ancestor_uid:
            return True
        seen.add(current)
        node = state.get(current)
        current = node.get("parent_uid") if node else None
    return False


def _source_context(
    project: Project,
    state: MutableMapping[str, Dict[str, Any]],
    node_uid: str,
) -> Tuple[Optional[int], Optional[str]]:
    current = state.get(node_uid)
    seen = set()
    while current is not None and current["node_uid"] not in seen:
        seen.add(current["node_uid"])
        source_id = current.get("source_id")
        if source_id is not None:
            row = project.conn.execute(
                "SELECT site FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            return source_id, row["site"] if row else None
        parent_uid = current.get("parent_uid")
        current = state.get(parent_uid) if parent_uid else None
    return None, None


def _pointer_parts(payload: Dict[str, Any]) -> List[str]:
    has_key = "key" in payload
    has_pointer = "pointer" in payload
    if has_key == has_pointer:
        raise OperationError(
            "UPDATE_PROPERTY zahteva natanko eno polje key ali pointer"
        )
    value = payload["key"] if has_key else payload["pointer"]
    if not isinstance(value, str) or not value:
        raise OperationError("key/pointer mora biti neprazen niz")
    if has_key:
        if "/" in value:
            raise OperationError("key ne sme vsebovati /; uporabi JSON pointer")
        return [value]
    if not value.startswith("/"):
        raise OperationError("JSON pointer se mora zaceti z /")
    parts = [
        item.replace("~1", "/").replace("~0", "~")
        for item in value[1:].split("/")
    ]
    if any(not item for item in parts):
        raise OperationError("JSON pointer ne sme vsebovati praznega segmenta")
    return parts


def _get_pointer(
    properties: Dict[str, Any],
    parts: Sequence[str],
) -> Tuple[bool, Any]:
    current: Any = properties
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, deepcopy(current)


def _set_pointer(
    properties: Dict[str, Any],
    parts: Sequence[str],
    value: Any,
    *,
    remove: bool = False,
) -> None:
    current = properties
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if remove:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = deepcopy(value)


def _validate_property(
    node: Dict[str, Any],
    payload: Dict[str, Any],
) -> Tuple[List[str], bool, Any]:
    if "new_value" not in payload:
        raise OperationError("UPDATE_PROPERTY zahteva new_value")
    parts = _pointer_parts(payload)
    properties = _raw_properties(node)
    exists, original = _get_pointer(properties, parts)
    root = parts[0]
    if root in _SPECIALIZED_PROPERTIES:
        raise OperationError(
            f"Lastnost {root} zahteva {_SPECIALIZED_PROPERTIES[root]}"
        )
    if root not in _PROPERTY_TYPES and root not in properties:
        raise OperationError(f"Neznana lastnost: {root}")
    value = payload["new_value"]
    expected = _PROPERTY_TYPES.get(root)
    if expected is None and exists and original is not None:
        expected = (type(original),)
    if (
        expected is not None
        and value is not None
        and (
            not isinstance(value, expected)
            or (
                bool not in expected
                and isinstance(value, bool)
                and any(item in expected for item in (int, float))
            )
        )
    ):
        expected_names = "/".join(item.__name__ for item in expected)
        raise OperationError(
            f"Lastnost {root} zahteva tip {expected_names}"
        )
    return parts, exists, original


def _validate_parameters(
    project: Project,
    state: MutableMapping[str, Dict[str, Any]],
    target: Dict[str, Any],
    params: Any,
) -> None:
    if target.get("tag_type") != "UdtInstance":
        raise OperationError(
            "UPDATE_PARAMETERS je dovoljen samo za UdtInstance"
        )
    if not isinstance(params, dict):
        raise OperationError("params mora biti JSON objekt")
    type_id = target.get("type_id") or _raw_properties(target).get("typeId")
    if not isinstance(type_id, str) or not type_id:
        raise OperationError("UDT instanca nima typeId")
    _, site = _source_context(project, state, target["node_uid"])
    context = ProjectUdtContext(project)
    declared = context.registry.effective_params(site or "", type_id)
    unknown = sorted(set(params) - declared)
    if unknown:
        raise OperationError(
            "Nedeklarirani UDT parametri: " + ", ".join(unknown)
        )
    if target.get("_baseline"):
        resolved = context.resolve(target["node_uid"]).get("udt_context")
        definitions = (
            resolved.get("effective_parameters", {})
            if isinstance(resolved, dict)
            else {}
        )
        for name, update in params.items():
            definition = definitions.get(name)
            if not isinstance(update, dict):
                raise OperationError(
                    f"Parameter {name} mora biti JSON objekt"
                )
            if not isinstance(definition, dict):
                continue
            expected_type = definition.get("dataType")
            supplied_type = update.get("dataType")
            if (
                supplied_type is not None
                and expected_type is not None
                and supplied_type != expected_type
            ):
                raise OperationError(
                    f"Parameter {name} zahteva dataType {expected_type}"
                )
            value = update.get("value")
            if expected_type == "Boolean" and not isinstance(value, bool):
                raise OperationError(
                    f"Parameter {name} zahteva Boolean vrednost"
                )
            if (
                expected_type in _INTEGER_DATA_TYPES
                and (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                )
            ):
                raise OperationError(
                    f"Parameter {name} zahteva celo stevilo"
                )
            if (
                expected_type in _FLOAT_DATA_TYPES
                and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                )
            ):
                raise OperationError(
                    f"Parameter {name} zahteva stevilsko vrednost"
                )
            if expected_type == "String" and not isinstance(value, str):
                raise OperationError(
                    f"Parameter {name} zahteva String vrednost"
                )


def validate_operation(
    project: Project,
    op_type: str,
    target_node_uid: Optional[str],
    payload: Dict[str, Any],
    *,
    state: Optional[MutableMapping[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Validiraj operacijo proti efektivnemu stanju in vrni normaliziran opis."""
    if op_type not in OPERATION_TYPES:
        raise OperationError(
            f"Neznan op_type {op_type!r}; dovoljeno: {', '.join(OPERATION_TYPES)}"
        )
    payload = _require_payload(payload)
    state = (
        state
        if state is not None
        else _load_validation_state(project, target_node_uid, payload)
    )

    if op_type in CREATE_OPERATION_TYPES:
        if target_node_uid is None:
            target_node_uid = "new:" + uuid.uuid4().hex
        if not isinstance(target_node_uid, str) or not target_node_uid.startswith(
            "new:"
        ):
            raise OperationError("CREATE operacija zahteva target new:<uid>")
        if target_node_uid in state:
            raise OperationError(f"Target ze obstaja: {target_node_uid}")
        required = {"parent_uid", "name", "tagType", "props"}
        missing = sorted(required - set(payload))
        if missing:
            raise OperationError(
                "CREATE payload manjka: " + ", ".join(missing)
            )
        parent_uid = payload["parent_uid"]
        if parent_uid not in state:
            raise OperationError(f"Neznan efektivni stars: {parent_uid}")
        if state[parent_uid].get("tag_type") not in _CONTAINER_TAG_TYPES:
            raise OperationError("CREATE stars ne more vsebovati otrok")
        name = _valid_name(payload["name"])
        _ensure_unique_name(state, parent_uid, name)
        if not isinstance(payload["props"], dict):
            raise OperationError("CREATE props mora biti JSON objekt")
        if "tags" in payload["props"]:
            raise OperationError(
                "CREATE props ne sme vsebovati tags; otroke dodaj z operacijami"
            )
        if not isinstance(payload["tagType"], str) or not payload["tagType"]:
            raise OperationError("CREATE tagType mora biti neprazen niz")
        expected_tag_type = {
            "CREATE_FOLDER": "Folder",
            "CREATE_UDT_INSTANCE": "UdtInstance",
        }.get(op_type)
        if expected_tag_type and payload["tagType"] != expected_tag_type:
            raise OperationError(
                f"{op_type} zahteva tagType={expected_tag_type}"
            )
        if op_type == "CREATE_TAG" and payload["tagType"] in (
            "Folder",
            "UdtInstance",
        ):
            raise OperationError(
                "Za Folder/UdtInstance uporabi namensko CREATE operacijo"
            )
        if op_type == "CREATE_UDT_INSTANCE":
            type_id = payload["props"].get("typeId")
            if not isinstance(type_id, str) or not type_id:
                raise OperationError(
                    "CREATE_UDT_INSTANCE zahteva props.typeId"
                )
            _, site = _source_context(project, state, parent_uid)
            context = ProjectUdtContext(project)
            if context.registry.canonical_get(site or "", type_id) is None:
                raise OperationError(
                    f"UDT definicija {type_id!r} ne obstaja na site {site!r}"
                )
        return {
            "op_type": op_type,
            "target_node_uid": target_node_uid,
            "payload": payload,
            "original": {"exists": False},
            "status": "VALID",
            "reason": None,
        }

    if not isinstance(target_node_uid, str) or target_node_uid not in state:
        raise OperationError(f"Neznan efektivni target: {target_node_uid}")
    target = state[target_node_uid]

    if op_type == "RENAME_TAG":
        if target.get("parent_uid") is None:
            raise OperationError("Provider korena ni mogoce preimenovati")
        if set(payload) != {"new_name"}:
            raise OperationError("RENAME_TAG zahteva samo new_name")
        name = _valid_name(payload["new_name"])
        _ensure_unique_name(
            state,
            target.get("parent_uid"),
            name,
            exclude_uid=target_node_uid,
        )
        original = {"name": target.get("name")}
    elif op_type == "MOVE_TAG":
        required = {"new_parent_uid", "new_sibling_index"}
        if set(payload) != required:
            raise OperationError(
                "MOVE_TAG zahteva new_parent_uid in new_sibling_index"
            )
        parent_uid = payload["new_parent_uid"]
        if parent_uid not in state:
            raise OperationError(f"Neznan ciljni stars: {parent_uid}")
        if target.get("parent_uid") is None:
            raise OperationError("Provider korena ni mogoce premakniti")
        if _is_descendant(state, parent_uid, target_node_uid):
            raise OperationError(
                "Vozlisca ni mogoce premakniti vase ali v lastnega potomca"
            )
        if state[parent_uid].get("tag_type") not in _CONTAINER_TAG_TYPES:
            raise OperationError("Ciljni stars ne more vsebovati otrok")
        if (
            target.get("provider_uid")
            != state[parent_uid].get("provider_uid")
        ):
            raise OperationError(
                "MOVE_TAG med razlicnimi providerji ni dovoljen"
            )
        sibling_index = payload["new_sibling_index"]
        if (
            not isinstance(sibling_index, int)
            or isinstance(sibling_index, bool)
            or sibling_index < 0
        ):
            raise OperationError(
                "new_sibling_index mora biti nenegativno celo stevilo"
            )
        _ensure_unique_name(
            state,
            parent_uid,
            target.get("name"),
            exclude_uid=target_node_uid,
        )
        original = {
            "parent_uid": target.get("parent_uid"),
            "sibling_index": target.get("sibling_index"),
        }
    elif op_type == "UPDATE_PROPERTY":
        parts, exists, value = _validate_property(target, payload)
        original = {
            "pointer": "/" + "/".join(
                item.replace("~", "~0").replace("/", "~1")
                for item in parts
            ),
            "exists": exists,
            "value": value,
        }
    elif op_type == "UPDATE_SOURCE_PATH":
        if set(payload) != {"new_value"}:
            raise OperationError(
                "UPDATE_SOURCE_PATH zahteva samo new_value"
            )
        value = payload["new_value"]
        if not isinstance(value, str) or not value:
            raise OperationError("sourceTagPath mora biti neprazen niz")
        if not braces_balanced(value):
            raise OperationError("sourceTagPath ima neuravnotezene zavite oklepaje")
        if provider_token(value) is None:
            raise OperationError(
                "sourceTagPath mora vsebovati ekspliciten [provider] token"
            )
        properties = _raw_properties(target)
        original = {
            "exists": "sourceTagPath" in properties,
            "value": deepcopy(properties.get("sourceTagPath")),
            "flattened": target.get("source_tag_path"),
        }
    elif op_type == "UPDATE_PARAMETERS":
        if set(payload) != {"params"}:
            raise OperationError("UPDATE_PARAMETERS zahteva samo params")
        _validate_parameters(project, state, target, payload["params"])
        properties = _raw_properties(target)
        original = {
            "exists": "parameters" in properties,
            "params": deepcopy(properties.get("parameters")),
        }
    else:
        if payload:
            raise OperationError("DELETE_TAG ne sprejme payloada")
        original = {"node": deepcopy(target)}
        return {
            "op_type": op_type,
            "target_node_uid": target_node_uid,
            "payload": payload,
            "original": original,
            "status": "DEFERRED",
            "reason": "Izvedba DELETE_TAG je odlozena do checkpointa L",
        }

    return {
        "op_type": op_type,
        "target_node_uid": target_node_uid,
        "payload": payload,
        "original": original,
        "status": "VALID",
        "reason": None,
    }


def _operation_conflict_key(operation: Dict[str, Any]) -> Optional[str]:
    op_type = operation["op_type"]
    if op_type in CREATE_OPERATION_TYPES:
        return None
    if op_type == "UPDATE_PROPERTY":
        parts = _pointer_parts(operation["payload"])
        return "property:/" + "/".join(parts)
    if op_type == "RENAME_TAG":
        return "name"
    if op_type == "MOVE_TAG":
        return "parent"
    if op_type == "UPDATE_SOURCE_PATH":
        return "sourceTagPath"
    if op_type == "UPDATE_PARAMETERS":
        return "parameters"
    if op_type == "DELETE_TAG":
        return "node"
    return None


def create_operation(
    project: Project,
    op_type: str,
    target_node_uid: Optional[str],
    payload: Dict[str, Any],
    created_by: str,
    *,
    depends_on: Sequence[str] = (),
) -> Dict[str, Any]:
    """Validiraj in trajno dodaj eno operacijo na konec dnevnika."""
    actor = _require_actor(created_by)
    if isinstance(depends_on, (str, bytes)) or not isinstance(
        depends_on, Sequence
    ):
        raise OperationError("depends_on mora biti zaporedje operation_uid")
    dependency_uids = list(dict.fromkeys(depends_on))
    if not all(
        isinstance(item, str) and item for item in dependency_uids
    ):
        raise OperationError("depends_on vsebuje neveljaven operation_uid")
    cursor = operation_cursor(project)
    known_dependencies = {
        row["operation_uid"]
        for row in project.conn.execute(
            "SELECT operation_uid FROM operations WHERE seq <= ?",
            (cursor,),
        ).fetchall()
    }
    unknown = sorted(set(dependency_uids) - known_dependencies)
    if unknown:
        raise OperationError(
            "Neznane odvisnosti: " + ", ".join(unknown)
        )

    validated = validate_operation(
        project,
        op_type,
        target_node_uid,
        payload,
    )
    with project.conn:
        project.conn.execute(
            "DELETE FROM operations WHERE seq > ?",
            (cursor,),
        )
    _refresh_conflict_groups(project)
    referenced_new_uids = []
    if (
        validated["op_type"] not in CREATE_OPERATION_TYPES
        and validated["target_node_uid"].startswith("new:")
    ):
        referenced_new_uids.append(validated["target_node_uid"])
    for field in ("parent_uid", "new_parent_uid"):
        value = validated["payload"].get(field)
        if isinstance(value, str) and value.startswith("new:"):
            referenced_new_uids.append(value)
    for new_uid in referenced_new_uids:
        creator = project.conn.execute(
            "SELECT operation_uid FROM operations "
            "WHERE target_node_uid = ? AND op_type IN "
            "('CREATE_TAG','CREATE_FOLDER','CREATE_UDT_INSTANCE') "
            "AND seq <= ? ORDER BY seq DESC LIMIT 1",
            (new_uid, cursor),
        ).fetchone()
        if creator is None:
            raise OperationError(
                f"Manjka CREATE odvisnost za {new_uid}"
            )
        if creator["operation_uid"] not in dependency_uids:
            dependency_uids.append(creator["operation_uid"])
    operation_uid = uuid.uuid4().hex
    operation = {
        "operation_uid": operation_uid,
        **validated,
    }
    conflict_key = _operation_conflict_key(operation)
    conflicting_uids: List[str] = []
    if conflict_key is not None:
        for existing in active_operations(project):
            if (
                existing["status"] in ("VALID", "CONFLICT")
                and existing["target_node_uid"]
                == operation["target_node_uid"]
                and _operation_conflict_key(existing) == conflict_key
            ):
                conflicting_uids.append(existing["operation_uid"])
    conflict = None
    if conflicting_uids:
        conflict = {
            "key": conflict_key,
            "operation_uids": sorted(
                [operation_uid, *conflicting_uids]
            ),
        }
        operation["status"] = "CONFLICT"
        operation["reason"] = (
            f"Vec operacij spreminja {conflict_key} istega vozlisca"
        )

    seq = cursor + 1
    created_at = _now()
    with project.conn:
        if conflicting_uids:
            placeholders = ",".join("?" for _ in conflicting_uids)
            project.conn.execute(
                "UPDATE operations SET status='CONFLICT', reason=?, "
                "conflict_info=? WHERE operation_uid IN "
                f"({placeholders})",
                (
                    operation["reason"],
                    _canonical_json(conflict),
                    *conflicting_uids,
                ),
            )
            revalidated = validate_operation(
                project,
                operation["op_type"],
                operation["target_node_uid"],
                operation["payload"],
            )
            operation["original"] = revalidated["original"]
        project.conn.execute(
            "INSERT INTO operations ("
            "operation_uid, seq, op_type, target_node_uid, payload_json, "
            "original_json, status, reason, created_by, created_at, "
            "depends_on_json, conflict_info"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_uid,
                seq,
                operation["op_type"],
                operation["target_node_uid"],
                _canonical_json(operation["payload"]),
                _canonical_json(operation["original"]),
                operation["status"],
                operation["reason"],
                actor,
                created_at,
                _canonical_json(dependency_uids),
                _canonical_json(conflict) if conflict else None,
            ),
        )
        project.conn.execute(
            "UPDATE project_meta SET operation_cursor = ? WHERE id = 1",
            (seq,),
        )
    return get_operation(project, operation_uid)


def get_operation(project: Project, operation_uid: str) -> Dict[str, Any]:
    row = project.conn.execute(
        "SELECT * FROM operations WHERE operation_uid = ?",
        (operation_uid,),
    ).fetchone()
    if row is None:
        raise OperationError(f"Neznan operation_uid: {operation_uid}")
    return _decode_operation(row)


def list_operations(
    project: Project,
    *,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if status is not None and status not in OPERATION_STATUSES:
        raise OperationError(f"Neveljaven status: {status}")
    sql = "SELECT * FROM operations"
    params: List[Any] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY seq, operation_uid"
    return [
        _decode_operation(row)
        for row in project.conn.execute(sql, params).fetchall()
    ]


def operation_cursor(project: Project) -> int:
    row = project.conn.execute(
        "SELECT operation_cursor FROM project_meta WHERE id = 1"
    ).fetchone()
    if row is None:
        raise OperationError("Projekt nima project_meta")
    cursor = int(row["operation_cursor"])
    maximum = project.conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM operations"
    ).fetchone()[0]
    if not 0 <= cursor <= maximum:
        raise OperationError(
            f"Neveljaven operation_cursor {cursor}; maksimum je {maximum}"
        )
    return cursor


def active_operations(project: Project) -> List[Dict[str, Any]]:
    cursor = operation_cursor(project)
    return [
        operation
        for operation in list_operations(project)
        if operation["seq"] <= cursor
    ]


def undo(project: Project, steps: int = 1) -> int:
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
        raise OperationError("steps mora biti pozitivno celo stevilo")
    cursor = max(0, operation_cursor(project) - steps)
    with project.conn:
        project.conn.execute(
            "UPDATE project_meta SET operation_cursor = ? WHERE id = 1",
            (cursor,),
        )
    _refresh_conflict_groups(project)
    return cursor


def redo(project: Project, steps: int = 1) -> int:
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
        raise OperationError("steps mora biti pozitivno celo stevilo")
    maximum = project.conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM operations"
    ).fetchone()[0]
    cursor = min(maximum, operation_cursor(project) + steps)
    with project.conn:
        project.conn.execute(
            "UPDATE project_meta SET operation_cursor = ? WHERE id = 1",
            (cursor,),
        )
    _refresh_conflict_groups(project)
    return cursor


def ordered_operations(
    project: Project,
    *,
    statuses: Iterable[str] = ("VALID",),
) -> List[Dict[str, Any]]:
    """Topolosko uredi dnevnik; seq je deterministicni tie-breaker."""
    allowed = set(statuses)
    unknown_statuses = allowed - set(OPERATION_STATUSES)
    if unknown_statuses:
        raise OperationError(
            "Neveljavni statusi: " + ", ".join(sorted(unknown_statuses))
        )
    all_rows = list_operations(project)
    selected = {
        operation["operation_uid"]: operation
        for operation in active_operations(project)
        if operation["status"] in allowed
    }
    indegree = {uid: 0 for uid in selected}
    followers: Dict[str, List[str]] = {uid: [] for uid in selected}
    for uid, operation in selected.items():
        for dependency in operation["depends_on"]:
            if dependency in selected:
                indegree[uid] += 1
                followers[dependency].append(uid)
            elif dependency not in {
                row["operation_uid"] for row in all_rows
            }:
                raise OperationError(
                    f"Operacija {uid} ima manjkajoco odvisnost {dependency}"
                )
    ready = sorted(
        (selected[uid]["seq"], uid)
        for uid, degree in indegree.items()
        if degree == 0
    )
    result: List[Dict[str, Any]] = []
    while ready:
        _, uid = ready.pop(0)
        result.append(selected[uid])
        for follower in followers[uid]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                ready.append((selected[follower]["seq"], follower))
                ready.sort()
    if len(result) != len(selected):
        raise OperationError("Dnevnik operacij vsebuje cikel odvisnosti")
    return result


def reorder_operation(
    project: Project,
    operation_uid: str,
    new_index: int,
) -> List[Dict[str, Any]]:
    """Premakni operacijo na zero-based indeks in normaliziraj seq."""
    operations = list_operations(project)
    if operation_cursor(project) < len(operations):
        raise OperationError(
            "Vrstnega reda ni mogoce spreminjati, dokler obstaja redo veja"
        )
    if not isinstance(new_index, int) or isinstance(new_index, bool):
        raise OperationError("new_index mora biti celo stevilo")
    if not 0 <= new_index < len(operations):
        raise OperationError(
            f"new_index mora biti med 0 in {max(0, len(operations) - 1)}"
        )
    current = next(
        (
            index
            for index, operation in enumerate(operations)
            if operation["operation_uid"] == operation_uid
        ),
        None,
    )
    if current is None:
        raise OperationError(f"Neznan operation_uid: {operation_uid}")
    operation = operations.pop(current)
    operations.insert(new_index, operation)
    position = {
        item["operation_uid"]: index
        for index, item in enumerate(operations)
    }
    for item in operations:
        for dependency in item["depends_on"]:
            if dependency in position and position[dependency] > position[
                item["operation_uid"]
            ]:
                raise OperationError(
                    "Operacije ni mogoce postaviti pred njeno odvisnost"
                )
    with project.conn:
        for seq, item in enumerate(operations, 1):
            project.conn.execute(
                "UPDATE operations SET seq = ? WHERE operation_uid = ?",
                (seq, item["operation_uid"]),
            )
    return list_operations(project)


def _refresh_conflict_groups(project: Project) -> None:
    cursor = operation_cursor(project)
    operations = list_operations(project)
    candidates = [
        operation
        for operation in operations
        if (
            operation["seq"] <= cursor
            and operation["status"] in ("VALID", "CONFLICT")
        )
    ]
    groups: Dict[Tuple[str, str], List[str]] = {}
    for operation in candidates:
        key = _operation_conflict_key(operation)
        if key is not None:
            groups.setdefault(
                (operation["target_node_uid"], key), []
            ).append(operation["operation_uid"])
    with project.conn:
        project.conn.execute(
            "UPDATE operations SET status='VALID', reason=NULL, "
            "conflict_info=NULL WHERE status='CONFLICT'"
        )
        for (target_uid, key), operation_uids in groups.items():
            if len(operation_uids) < 2:
                continue
            conflict = {
                "key": key,
                "operation_uids": sorted(operation_uids),
            }
            placeholders = ",".join("?" for _ in operation_uids)
            project.conn.execute(
                "UPDATE operations SET status='CONFLICT', reason=?, "
                "conflict_info=? WHERE operation_uid IN "
                f"({placeholders})",
                (
                    f"Vec operacij spreminja {key} istega vozlisca",
                    _canonical_json(conflict),
                    *operation_uids,
                ),
            )


def remove_operation(
    project: Project,
    operation_uid: str,
) -> List[Dict[str, Any]]:
    """Odstrani stage-an korak, ce nobena druga operacija ni odvisna od njega."""
    selected = get_operation(project, operation_uid)
    dependents = [
        operation["operation_uid"]
        for operation in list_operations(project)
        if operation_uid in operation["depends_on"]
    ]
    if dependents:
        raise OperationError(
            "Operacije ni mogoce odstraniti; odvisne operacije: "
            + ", ".join(dependents)
        )
    cursor = operation_cursor(project)
    with project.conn:
        project.conn.execute(
            "DELETE FROM operations WHERE operation_uid = ?",
            (operation_uid,),
        )
        if selected["seq"] <= cursor:
            cursor -= 1
        project.conn.execute(
            "UPDATE project_meta SET operation_cursor = ? WHERE id = 1",
            (cursor,),
        )
        operations = project.conn.execute(
            "SELECT operation_uid FROM operations "
            "ORDER BY seq, operation_uid"
        ).fetchall()
        for seq, operation in enumerate(operations, 1):
            project.conn.execute(
                "UPDATE operations SET seq = ? WHERE operation_uid = ?",
                (seq, operation["operation_uid"]),
            )
    _refresh_conflict_groups(project)
    return list_operations(project)


def _recompute_subtree(
    state: MutableMapping[str, Dict[str, Any]],
    node_uid: str,
) -> None:
    node = state[node_uid]
    parent = state.get(node.get("parent_uid"))
    if parent is None:
        node["depth"] = 0
        node["path_at_import"] = node.get("name") or ""
    else:
        node["depth"] = int(parent.get("depth", 0)) + 1
        prefix = parent.get("path_at_import") or ""
        node["path_at_import"] = (
            f"{prefix}/{node.get('name')}" if prefix else node.get("name") or ""
        )
    for child in _children(state, node_uid):
        _recompute_subtree(state, child["node_uid"])


def apply_operation_to_state(
    state: MutableMapping[str, Dict[str, Any]],
    operation: Dict[str, Any],
) -> None:
    """Uporabi dekodiran forward ali interni inverse opis na in-memory stanje."""
    op_type = operation["op_type"]
    target_uid = operation["target_node_uid"]
    payload = operation.get("payload") or {}
    original = operation.get("original") or {}

    if op_type in CREATE_OPERATION_TYPES:
        parent = state[payload["parent_uid"]]
        properties = deepcopy(payload["props"])
        properties.update(
            {
                "name": payload["name"],
                "tagType": payload["tagType"],
            }
        )
        state[target_uid] = {
            "node_uid": target_uid,
            "provider_uid": parent.get("provider_uid"),
            "parent_uid": payload["parent_uid"],
            "sibling_index": len(_children(state, payload["parent_uid"])),
            "depth": int(parent.get("depth", 0)) + 1,
            "path_at_import": "",
            "name": payload["name"],
            "tag_type": payload["tagType"],
            "data_type": properties.get("dataType"),
            "value_source": properties.get("valueSource"),
            "type_id": properties.get("typeId"),
            "opc_item_path": properties.get("opcItemPath"),
            "opc_server": properties.get("opcServer"),
            "source_tag_path": properties.get("sourceTagPath"),
            "raw_json": _canonical_json(properties),
            "source_id": parent.get("source_id"),
            "properties": properties,
            "_baseline": False,
        }
        _recompute_subtree(state, target_uid)
        return
    if op_type == "DELETE_TAG":
        if payload.get("created_only"):
            to_remove = [
                uid
                for uid in list(state)
                if _is_descendant(state, uid, target_uid)
            ]
            for uid in to_remove:
                state.pop(uid, None)
        return
    if op_type == "RESTORE_TAG":
        state[target_uid] = deepcopy(original["node"])
        return

    target = state[target_uid]
    properties = _raw_properties(target)
    if op_type == "RENAME_TAG":
        target["name"] = payload["new_name"]
        properties["name"] = payload["new_name"]
        _recompute_subtree(state, target_uid)
    elif op_type == "MOVE_TAG":
        target["parent_uid"] = payload["new_parent_uid"]
        target["sibling_index"] = payload["new_sibling_index"]
        _recompute_subtree(state, target_uid)
    elif op_type == "UPDATE_PROPERTY":
        parts = _pointer_parts(payload)
        _set_pointer(
            properties,
            parts,
            payload.get("new_value"),
            remove=bool(payload.get("_remove")),
        )
        if len(parts) == 1 and parts[0] in _COLUMN_BY_PROPERTY:
            target[_COLUMN_BY_PROPERTY[parts[0]]] = (
                None if payload.get("_remove") else payload.get("new_value")
            )
    elif op_type == "UPDATE_SOURCE_PATH":
        if payload.get("_remove"):
            properties.pop("sourceTagPath", None)
            target["source_tag_path"] = None
        else:
            properties["sourceTagPath"] = deepcopy(
                payload.get("_property_value", payload["new_value"])
            )
            target["source_tag_path"] = payload["new_value"]
    elif op_type == "UPDATE_PARAMETERS":
        if payload.get("_remove"):
            properties.pop("parameters", None)
        else:
            properties["parameters"] = deepcopy(payload["params"])
    else:
        raise OperationError(f"Operacije ni mogoce uporabiti v simulaciji: {op_type}")
    target["raw_json"] = _canonical_json(properties)


def build_simulation_state(
    project: Project,
    *,
    operations: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Zgradi efektivno kopijo; baseline tabele se nikoli ne zapisujejo."""
    state = load_baseline_state(project)
    ordered = (
        list(operations)
        if operations is not None
        else ordered_operations(project)
    )
    for operation in ordered:
        if operation.get("status", "VALID") != "VALID":
            continue
        apply_operation_to_state(state, operation)
    return state


def invert_operation(operation: Dict[str, Any]) -> Dict[str, Any]:
    """Sestavi cisti in-memory inverz za eno ze validirano operacijo."""
    op_type = operation["op_type"]
    target_uid = operation["target_node_uid"]
    payload = operation.get("payload") or {}
    original = operation.get("original") or {}
    inverse_type = op_type
    inverse_payload: Dict[str, Any]
    inverse_original: Dict[str, Any] = {}

    if op_type in CREATE_OPERATION_TYPES:
        inverse_type = "DELETE_TAG"
        inverse_payload = {"created_only": True}
    elif op_type == "RENAME_TAG":
        inverse_payload = {"new_name": original["name"]}
    elif op_type == "MOVE_TAG":
        inverse_payload = {
            "new_parent_uid": original["parent_uid"],
            "new_sibling_index": original["sibling_index"],
        }
    elif op_type == "UPDATE_PROPERTY":
        inverse_payload = {
            "pointer": original["pointer"],
            "new_value": original.get("value"),
            "_remove": not original.get("exists", False),
        }
    elif op_type == "UPDATE_SOURCE_PATH":
        inverse_payload = {
            "new_value": original.get("flattened"),
            "_property_value": original.get("value"),
            "_remove": not original.get("exists", False),
        }
    elif op_type == "UPDATE_PARAMETERS":
        inverse_payload = {
            "params": original.get("params"),
            "_remove": not original.get("exists", False),
        }
    elif op_type == "DELETE_TAG":
        inverse_type = "RESTORE_TAG"
        inverse_payload = {}
        inverse_original = original
    else:
        raise OperationError(f"Operacija nima inverza: {op_type}")

    return {
        "operation_uid": "inverse:" + operation.get("operation_uid", uuid.uuid4().hex),
        "seq": operation.get("seq", 0),
        "op_type": inverse_type,
        "target_node_uid": target_uid,
        "payload": inverse_payload,
        "original": inverse_original,
        "status": "VALID",
        "reason": None,
        "created_by": operation.get("created_by", ""),
        "created_at": operation.get("created_at", ""),
        "depends_on": [],
        "conflict": None,
    }
