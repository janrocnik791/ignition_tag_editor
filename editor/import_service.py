"""Mejnik B2: uvoz IO/UNS/UDT JSON v nespremenljiv baseline projekta.

Ponovna uporaba parserja Faze 0 (``analyzer.model.TagRow`` za ekstrakcijo polj in
``raw_json``, ``analyzer.build.sha256_file`` za prstni odtis, ``classify_file`` za
kind). Doda stabilno identiteto (``node_uid``, ``provider_uid``), vrstni red
sorojencev (``sibling_index``) in izvor (``sources``).

Identiteta (glej roadmap §9):
    provider_uid = sha1(site + "/" + provider_name + "/" + kind)
    node_uid     = sha1(provider_uid + "\\x00" + original_full_path)
``node_uid`` je deterministicen, zato ponovni uvoz nespremenjenih vozlisc da iste
identitete (osnova za stale-detekcijo v poznejsih mejnikih). Vhodne datoteke se
odpirajo samo za branje in ostanejo nespremenjene.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from analyzer.build import sha256_file
from analyzer.model import TagRow, classify_file

from .project import Project, ProjectError

_TAGS_RE = re.compile(r"(?i)^tags[_-](.+)$")


class ImportSourceError(ProjectError):
    """Napaka pri uvozu vira (neveljaven JSON, neznan provider, podvojitev ...)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_provider_uid(site: str, provider_name: str, kind: str) -> str:
    return hashlib.sha1(
        f"{site}/{provider_name}/{kind}".encode("utf-8")
    ).hexdigest()


def compute_node_uid(provider_uid: str, full_path: str) -> str:
    return hashlib.sha1(
        f"{provider_uid}\x00{full_path}".encode("utf-8")
    ).hexdigest()


def parse_provider_name(path: str, site: str, kind: str) -> Optional[str]:
    """Razberi ime providerja iz imena datoteke.

    ``tags_<PROVIDER>.json`` -> ``<PROVIDER>`` (npr. ``IO_ST_SIE``, ``UNS_ST``).
    UDT datoteke nimajo tokena providerja -> ``UDT_<site>``. Sicer None (neznan
    vzorec).
    """
    base = os.path.splitext(os.path.basename(path))[0]
    m = _TAGS_RE.match(base)
    if m:
        return m.group(1)
    if kind == "udt":
        return f"UDT_{site}"
    return None


def discover_sources(root_dir: str, site: Optional[str] = None) -> List[Dict[str, Any]]:
    """Poisci .json vire pod ``root_dir`` in jih klasificiraj (brez branja JSON).

    ``site`` prevlada nad imenom mape. Ne odpira vsebine datotek.
    """
    out: List[Dict[str, Any]] = []
    for dirpath, _dirs, files in os.walk(root_dir):
        for name in sorted(files):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            info = classify_file(path)
            s = site or info["site"]
            out.append({
                "path": path, "site": s, "kind": info["kind"],
                "provider_name": parse_provider_name(path, s, info["kind"]),
            })
    return sorted(out, key=lambda d: d["path"])


def validate_source(path: str, site: str) -> Dict[str, Any]:
    """Preverjanje pred uvozom: parsabilnost + prepoznan provider. Odpre datoteko."""
    info = classify_file(path)
    kind = info["kind"]
    provider_name = parse_provider_name(path, site, kind)
    issues: List[str] = []
    root_tag_type = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            root = json.load(f)
        if not isinstance(root, dict):
            issues.append("koren JSON ni objekt")
        else:
            root_tag_type = root.get("tagType")
    except (OSError, json.JSONDecodeError) as e:
        issues.append(f"neveljaven JSON: {e}")
    if provider_name is None:
        issues.append("neznan provider vzorec (ni tags_* in ni UDT)")
    return {
        "ok": not issues, "path": path, "site": site, "kind": kind,
        "provider_name": provider_name, "root_tag_type": root_tag_type,
        "issues": issues,
    }


def _collect_nodes(
    root: Dict[str, Any], provider_uid: str, source_id: int
) -> List[Tuple]:
    """Pre-order obhod: vrne vrstice za baseline_nodes (starsi pred otroki)."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    rows: List[Tuple] = []

    def walk(node, parent_uid, depth, parent_full_path, sibling_index):
        tr = TagRow.from_node(node, source_id, None, depth, parent_full_path)
        full_path = tr.full_path
        nuid = compute_node_uid(provider_uid, full_path)
        rows.append((
            nuid, provider_uid, parent_uid, sibling_index, depth, full_path,
            tr.name, tr.tag_type, tr.data_type, tr.value_source, tr.type_id,
            tr.opc_item_path, tr.opc_server, tr.source_tag_path,
            tr.raw_properties, source_id,
        ))
        children = node.get("tags")
        if isinstance(children, list):
            for i, child in enumerate(children):
                if isinstance(child, dict):
                    walk(child, nuid, depth + 1, full_path, i)

    walk(root, None, 0, "", 0)
    return rows


_INSERT_SQL = (
    "INSERT INTO baseline_nodes (node_uid, provider_uid, parent_uid, "
    "sibling_index, depth, path_at_import, name, tag_type, data_type, "
    "value_source, type_id, opc_item_path, opc_server, source_tag_path, "
    "raw_json, source_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def import_source(project: Project, path: str, site: str) -> Dict[str, Any]:
    """Uvozi en JSON vir v baseline projekta.

    Idempotentno po (provider, sha256): enak vir -> ``unchanged``; spremenjen vir
    z isto potjo -> ``reimported`` (zamenjava); ista identiteta providerja z druge
    poti -> napaka (podvojitev). Vhodna datoteka se ne spreminja.
    """
    info = classify_file(path)
    kind = info["kind"]
    provider_name = parse_provider_name(path, site, kind)
    if provider_name is None:
        raise ImportSourceError(
            f"neznan provider vzorec: {os.path.basename(path)}"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            root = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ImportSourceError(f"neveljaven JSON: {path} ({e})")
    if not isinstance(root, dict):
        raise ImportSourceError(f"koren JSON ni objekt: {path}")

    puid = compute_provider_uid(site, provider_name, kind)
    sha = sha256_file(path)
    conn = project.conn
    now = _now()
    session = uuid.uuid4().hex

    existing = conn.execute(
        "SELECT id, path, sha256 FROM sources "
        "WHERE provider_name=? AND site=? AND kind=?",
        (provider_name, site, kind),
    ).fetchone()

    if existing is not None:
        if existing["path"] == path and existing["sha256"] == sha:
            n = conn.execute(
                "SELECT COUNT(*) FROM baseline_nodes WHERE source_id=?",
                (existing["id"],),
            ).fetchone()[0]
            return {"status": "unchanged", "source_id": existing["id"],
                    "provider_uid": puid, "provider_name": provider_name,
                    "nodes": n}
        if existing["path"] != path:
            raise ImportSourceError(
                f"provider {provider_name}@{site} je ze uvozen iz "
                f"{existing['path']}"
            )
        # ista pot, spremenjena vsebina -> zamenjava
        source_id = existing["id"]
        conn.execute("DELETE FROM baseline_nodes WHERE source_id=?", (source_id,))
        conn.execute(
            "UPDATE sources SET sha256=?, import_session=?, imported_at=? "
            "WHERE id=?", (sha, session, now, source_id),
        )
        status = "reimported"
    else:
        cur = conn.execute(
            "INSERT INTO sources (path, sha256, provider_name, site, kind, "
            "import_session, imported_at) VALUES (?,?,?,?,?,?,?)",
            (path, sha, provider_name, site, kind, session, now),
        )
        source_id = cur.lastrowid
        status = "imported"

    rows = _collect_nodes(root, puid, source_id)
    try:
        conn.executemany(_INSERT_SQL, rows)
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ImportSourceError(
            f"trk identitete vozlisca (podvojena pot?) v {path}: {e}"
        )
    conn.commit()
    return {"status": status, "source_id": source_id, "provider_uid": puid,
            "provider_name": provider_name, "nodes": len(rows)}


def list_providers(project: Project) -> List[Dict[str, Any]]:
    """Uvozeni providerji s steviljem vozlisc (osnova za lazy drevo v C1)."""
    rows = project.conn.execute(
        "SELECT b.provider_uid, s.provider_name, s.site, s.kind, "
        "COUNT(*) AS node_count "
        "FROM baseline_nodes b JOIN sources s ON s.id = b.source_id "
        "GROUP BY b.provider_uid, s.provider_name, s.site, s.kind "
        "ORDER BY s.site, s.provider_name"
    ).fetchall()
    return [dict(r) for r in rows]
