"""Read-only repozitorij za drevo, podrobnosti in paginirano iskanje.

Bere samo baseline (``baseline_nodes``); nic ne spreminja. Otroci se pridobivajo
na zahtevo po ``parent_uid`` (paginirano), zato noben klic ne nalozi celega drevesa.
Uporablja indeksa iz sheme v1 (``parent_uid``, ``provider_uid``).

C1 pokriva navigacijo + podrobnosti vozlisca, C3 pa iskanje in filtre.
Razresevanje efektivnih UDT clanov/parametrov je v C4. Ker so operacije
(preimenovanja/premiki) sele v F+, je efektivna pot vozlisca tu enaka
``path_at_import``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from analyzer.query import SEARCH_FIELDS as ANALYZER_SEARCH_FIELDS

from .import_service import (  # noqa: F401
    compute_provider_uid,
    list_providers,
)
from .project import Project, ProjectError
from .udt_context import ProjectUdtContext


class RepositoryError(ProjectError):
    """Napaka poizvedbe (npr. neznan node_uid)."""


# Lahki stolpci za vrstice drevesa (brez raw_json zaradi zmogljivosti).
_TREE_COLS = (
    "node_uid, provider_uid, parent_uid, sibling_index, depth, path_at_import, "
    "name, tag_type, type_id"
)

# C3 ohrani javna imena polj in semantiko obstojecega analyzer.query.search,
# vendar jih preslika na stolpce projektnega baselinea.
_ANALYZER_TO_BASELINE = {
    "full_path": "path_at_import",
    "name": "name",
    "opc_item_path": "opc_item_path",
    "source_tag_path": "source_tag_path",
    "type_id": "type_id",
}
SEARCH_FIELDS = {
    field: _ANALYZER_TO_BASELINE[column]
    for field, column in ANALYZER_SEARCH_FIELDS.items()
}
SEARCH_MODES = ("exact", "prefix", "contains")
MAX_SEARCH_PAGE_SIZE = 500
_SEARCH_INDEXES = {
    "fullPath": "idx_baseline_search_path",
    "name": "idx_baseline_search_name",
    "opcItemPath": "idx_baseline_search_opc_item",
    "sourceTagPath": "idx_baseline_search_source_tag",
    "typeId": "idx_baseline_search_type_id",
}


def _tree_row(row) -> Dict[str, Any]:
    d = dict(row)
    d["has_children"] = bool(d.pop("has_children_flag"))
    return d


def get_provider_root(project: Project, provider_uid: str) -> Optional[Dict[str, Any]]:
    """Korensko vozlisce providerja (``parent_uid IS NULL``) ali None."""
    row = project.conn.execute(
        f"SELECT {_TREE_COLS}, "
        "EXISTS(SELECT 1 FROM baseline_nodes c WHERE c.parent_uid = b.node_uid) "
        "AS has_children_flag "
        "FROM baseline_nodes b "
        "WHERE b.provider_uid = ? AND b.parent_uid IS NULL", (provider_uid,)
    ).fetchone()
    return _tree_row(row) if row else None


def get_children(
    project: Project,
    parent_uid: Optional[str],
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Otroci danega vozlisca, urejeni po ``sibling_index`` (deterministicno).

    ``parent_uid=None`` vrne korene providerjev (po ``provider_uid``). Paginirano
    prek ``limit``/``offset``.
    """
    base = (
        f"SELECT {_TREE_COLS}, "
        "EXISTS(SELECT 1 FROM baseline_nodes c WHERE c.parent_uid = b.node_uid) "
        "AS has_children_flag FROM baseline_nodes b "
    )
    params: List[Any] = []
    if parent_uid is None:
        base += "WHERE b.parent_uid IS NULL ORDER BY b.provider_uid, b.sibling_index, b.node_uid"
    else:
        base += "WHERE b.parent_uid = ? ORDER BY b.sibling_index, b.node_uid"
        params.append(parent_uid)
    if limit is not None:
        base += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return [_tree_row(r) for r in project.conn.execute(base, params).fetchall()]


def child_count(project: Project, parent_uid: Optional[str]) -> int:
    if parent_uid is None:
        return project.conn.execute(
            "SELECT COUNT(*) FROM baseline_nodes WHERE parent_uid IS NULL"
        ).fetchone()[0]
    return project.conn.execute(
        "SELECT COUNT(*) FROM baseline_nodes WHERE parent_uid = ?", (parent_uid,)
    ).fetchone()[0]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_term(field: str, value: str, mode: str) -> tuple[str, List[Any]]:
    if field not in SEARCH_FIELDS:
        raise ValueError(
            f"Nepoznano polje '{field}'. Dovoljena: {', '.join(SEARCH_FIELDS)}"
        )
    if mode not in SEARCH_MODES:
        raise ValueError(
            f"Nepoznan mode '{mode}'. Dovoljeni: {', '.join(SEARCH_MODES)}"
        )
    column = f"b.{SEARCH_FIELDS[field]}"
    if mode == "exact":
        return f"{column} = ?", [value]
    if value == "":
        return f"{column} IS NOT NULL", []
    escaped = _escape_like(value)
    if mode == "prefix":
        return (
            f"({column} IS NOT NULL AND {column} LIKE ? ESCAPE '\\')",
            [escaped + "%"],
        )
    return (
        f"({column} IS NOT NULL AND {column} LIKE ? ESCAPE '\\')",
        ["%" + escaped + "%"],
    )


def search_nodes(
    project: Project,
    field: str,
    value: str,
    *,
    mode: str = "contains",
    provider_uid: Optional[str] = None,
    site: Optional[str] = None,
    tag_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Poisci baseline vozlisca z agregatom, filtri in omejeno stranjo.

    Iskalna polja in nacini so enaki ``analyzer.query.search``. Filtri se
    kombinirajo z AND. Rezultat nikoli ne vrne vec kot ``limit`` lahkih vrstic
    in ne vsebuje ``raw_json``.
    """
    if not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_PAGE_SIZE:
        raise ValueError(
            f"limit mora biti med 1 in {MAX_SEARCH_PAGE_SIZE}"
        )
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset mora biti nenegativno celo stevilo")

    term, params = _search_term(field, value, mode)
    clauses = [term]
    if provider_uid is not None:
        clauses.append("b.provider_uid = ?")
        params.append(provider_uid)
    if site is not None:
        clauses.append(
            "b.source_id IN (SELECT id FROM sources WHERE site = ?)"
        )
        params.append(site)
    if tag_type is not None:
        clauses.append("b.tag_type = ?")
        params.append(tag_type)
    where = " AND ".join(clauses)
    order_column = f"b.{SEARCH_FIELDS[field]}"
    search_index = _SEARCH_INDEXES[field]

    total = project.conn.execute(
        f"SELECT COUNT(*) FROM baseline_nodes b INDEXED BY {search_index} "
        f"WHERE {where}",
        params,
    ).fetchone()[0]

    rows = project.conn.execute(
        "WITH page AS ("
        "SELECT b.node_uid, b.provider_uid, b.parent_uid, b.path_at_import, "
        "b.name, b.tag_type, b.data_type, b.type_id, b.opc_item_path, "
        "b.source_tag_path, b.source_id "
        f"FROM baseline_nodes b INDEXED BY {search_index} WHERE {where} "
        f"ORDER BY {order_column}, b.provider_uid, b.node_uid LIMIT ? OFFSET ?"
        ") "
        "SELECT p.node_uid, p.provider_uid, p.parent_uid, p.path_at_import, "
        "p.name, p.tag_type, p.data_type, p.type_id, p.opc_item_path, "
        "p.source_tag_path, s.provider_name, s.site, s.kind "
        "FROM page p JOIN sources s ON s.id = p.source_id "
        f"ORDER BY p.{SEARCH_FIELDS[field]}, p.provider_uid, p.node_uid",
        [*params, limit, offset],
    ).fetchall()
    results = [dict(row) for row in rows]
    return {
        "field": field,
        "mode": mode,
        "value": value,
        "filters": {
            "provider_uid": provider_uid,
            "site": site,
            "tag_type": tag_type,
        },
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_previous": offset > 0,
        "has_next": offset + len(results) < total,
        "results": results,
    }


def get_search_filters(project: Project) -> Dict[str, List[Any]]:
    """Vrni deterministicne moznosti filtrov, ki obstajajo v baselineu."""
    source_rows = project.conn.execute(
        "SELECT provider_name, site, kind FROM sources "
        "WHERE provider_name IS NOT NULL AND site IS NOT NULL AND kind IS NOT NULL "
        "ORDER BY site, provider_name, kind"
    ).fetchall()
    providers = [
        {
            "provider_uid": compute_provider_uid(
                row["site"], row["provider_name"], row["kind"]
            ),
            **dict(row),
        }
        for row in source_rows
    ]
    sites = sorted({row["site"] for row in providers if row["site"] is not None})
    tag_types = [
        row[0]
        for row in project.conn.execute(
            "SELECT DISTINCT tag_type FROM baseline_nodes "
            "WHERE tag_type IS NOT NULL AND tag_type <> '' ORDER BY tag_type"
        ).fetchall()
    ]
    return {
        "providers": providers,
        "sites": sites,
        "tag_types": tag_types,
    }


def get_node(project: Project, node_uid: str) -> Optional[Dict[str, Any]]:
    """Celotna vrstica vozlisca (brez razclenjenega raw_json) ali None."""
    row = project.conn.execute(
        "SELECT * FROM baseline_nodes WHERE node_uid = ?", (node_uid,)
    ).fetchone()
    return dict(row) if row else None


def get_parent(project: Project, node_uid: str) -> Optional[Dict[str, Any]]:
    """Stars vozlisca ali None (koren nima starsa). Neznan uid -> RepositoryError."""
    node = get_node(project, node_uid)
    if node is None:
        raise RepositoryError(f"Neznan node_uid: {node_uid}")
    if node["parent_uid"] is None:
        return None
    return get_node(project, node["parent_uid"])


def breadcrumbs(project: Project, node_uid: str) -> List[Dict[str, Any]]:
    """Pot od korena do vozlisca kot seznam ``{node_uid, name, path_at_import}``."""
    chain: List[Dict[str, Any]] = []
    cur: Optional[str] = node_uid
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        row = project.conn.execute(
            "SELECT node_uid, name, path_at_import, parent_uid "
            "FROM baseline_nodes WHERE node_uid = ?", (cur,)
        ).fetchone()
        if row is None:
            if not chain:
                raise RepositoryError(f"Neznan node_uid: {node_uid}")
            break
        chain.append({"node_uid": row["node_uid"], "name": row["name"],
                      "path_at_import": row["path_at_import"]})
        cur = row["parent_uid"]
    chain.reverse()
    return chain


def full_path(project: Project, node_uid: str) -> Optional[str]:
    """Efektivna pot vozlisca. V C1 (brez operacij) je enaka ``path_at_import``."""
    node = get_node(project, node_uid)
    return node["path_at_import"] if node else None


def _provider_info(project: Project, source_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if source_id is None:
        return None
    row = project.conn.execute(
        "SELECT id AS source_id, path, sha256, provider_name, site, kind, "
        "import_session, imported_at FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def node_details(
    project: Project,
    node_uid: str,
    udt_context: Optional[ProjectUdtContext] = None,
) -> Dict[str, Any]:
    """Podrobnosti vozlisca: izlusceni stolpci + surove lastnosti + kontekst.

    ``properties`` je celoten originalni Ignition objekt (brez otrok). Neznan uid
    vrze RepositoryError. ``effective_properties`` in ``udt_context`` sta
    izracunana read-only pogleda; baseline ostane nespremenjen.
    """
    node = get_node(project, node_uid)
    if node is None:
        raise RepositoryError(f"Neznan node_uid: {node_uid}")
    raw = node.pop("raw_json")
    try:
        properties = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        properties = {}
    parent = None
    if node["parent_uid"] is not None:
        p = get_node(project, node["parent_uid"])
        if p:
            parent = {"node_uid": p["node_uid"], "name": p["name"],
                      "path_at_import": p["path_at_import"]}
    cc = child_count(project, node_uid)
    resolver = udt_context or ProjectUdtContext(project)
    resolved = resolver.resolve(node_uid)
    return {
        **node,
        "properties": properties,
        "effective_properties": resolved["effective_properties"],
        "udt_context": resolved["udt_context"],
        "parent": parent,
        "child_count": cc,
        "has_children": cc > 0,
        "provider": _provider_info(project, node.get("source_id")),
    }
