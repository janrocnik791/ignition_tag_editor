"""Mejnik C1: read-only repozitorij za lazy navigacijo drevesa in podrobnosti.

Bere samo baseline (``baseline_nodes``); nic ne spreminja. Otroci se pridobivajo
na zahtevo po ``parent_uid`` (paginirano), zato noben klic ne nalozi celega drevesa.
Uporablja indeksa iz sheme v1 (``parent_uid``, ``provider_uid``).

Obseg C1: navigacija + podrobnosti vozlisca (surove + izlusceni stolpci). Iskanje
je v C3; razresevanje efektivnih UDT clanov/parametrov je v C4. Ker so operacije
(preimenovanja/premiki) sele v F+, je efektivna pot vozlisca tu enaka
``path_at_import``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .import_service import list_providers  # re-export enotnega read API  # noqa: F401
from .project import Project, ProjectError


class RepositoryError(ProjectError):
    """Napaka poizvedbe (npr. neznan node_uid)."""


# Lahki stolpci za vrstice drevesa (brez raw_json zaradi zmogljivosti).
_TREE_COLS = (
    "node_uid, provider_uid, parent_uid, sibling_index, depth, path_at_import, "
    "name, tag_type, type_id"
)


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
        "SELECT provider_name, site, kind FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    return dict(row) if row else None


def node_details(project: Project, node_uid: str) -> Dict[str, Any]:
    """Podrobnosti vozlisca: izlusceni stolpci + surove lastnosti + kontekst.

    ``properties`` je celoten originalni Ignition objekt (brez otrok). Neznan uid
    vrze RepositoryError. Efektivni UDT clani/parametri pridejo v C4.
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
    return {
        **node,
        "properties": properties,
        "parent": parent,
        "child_count": cc,
        "has_children": cc > 0,
        "provider": _provider_info(project, node.get("source_id")),
    }
