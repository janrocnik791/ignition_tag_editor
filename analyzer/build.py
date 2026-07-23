"""Gradnja SQLite indeksa iz Ignition izvozov (read-only nad data/raw)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .model import TagRow, classify_file
from .schema import build_stats, create_schema

# Najvecji izvoz je ~40 MB; json.load ga brez tezav prebere v pomnilnik.
# (Streaming z 'ijson' je mozna nadgradnja, a ni potreben in ni odvisnost.)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_exports(raw_root: str) -> List[str]:
    """Poisci vse .json izvoze pod data/raw (read-only)."""
    out: List[str] = []
    for dirpath, _dirs, files in os.walk(raw_root):
        for name in files:
            if name.lower().endswith(".json"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _iter_roots(path: str) -> Iterator[Tuple[Optional[str], Dict[str, Any]]]:
    """Vrni (root_tag_type, root_node). Datoteka se odpre samo za branje."""
    with open(path, "r", encoding="utf-8") as f:
        root = json.load(f)
    yield root.get("tagType"), root


def _walk(
    node: Dict[str, Any],
    file_id: int,
    parent_id: Optional[int],
    depth: int,
    parent_full_path: str,
    emit,
) -> int:
    """Rekurzivno obidi drevo; ``emit(row) -> new_id`` vstavi in vrne id.

    Vrne stevilo obiskanih vozlisc.
    """
    row = TagRow.from_node(node, file_id, parent_id, depth, parent_full_path)
    my_id = emit(row)
    count = 1
    children = node.get("tags")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                count += _walk(
                    child, file_id, my_id, depth + 1, row.full_path, emit
                )
    return count


def _assert_read_only(db_path: str, raw_root: str) -> None:
    """Zavrni gradnjo, ce bi DB pisali pod data/raw."""
    db_abs = os.path.abspath(db_path)
    raw_abs = os.path.abspath(raw_root)
    try:
        common = os.path.commonpath([db_abs, raw_abs])
    except ValueError:
        return  # razlicna diska -> zagotovo ne pod raw
    if common == raw_abs:
        raise ValueError(
            f"Izhodna DB ({db_abs}) je pod data/raw ({raw_abs}); "
            "pisanje v raw je prepovedano."
        )


def build_index(
    raw_root: str,
    db_path: str,
    verbose: bool = False,
) -> Dict[str, int]:
    """Zgradi SQLite indeks. Prepise obstojeco DB. Vrne povzetek.

    ``raw_root`` se odpira izkljucno za branje; zapisujemo samo v ``db_path``.
    """
    _assert_read_only(db_path, raw_root)
    # Ignition drevesa so lahko globoka; dvignemo limit za rekurzivni obhod.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        create_schema(conn)

        exports = find_exports(raw_root)
        total_nodes = 0
        now = datetime.now(timezone.utc).isoformat()

        for path in exports:
            meta = classify_file(path)
            size = os.path.getsize(path)
            digest = sha256_file(path)

            cur = conn.execute(
                "INSERT INTO files "
                "(path, site, kind, root_tag_type, size_bytes, sha256, "
                " node_count, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                (path, meta["site"], meta["kind"], None, size, digest, 0, now),
            )
            file_id = cur.lastrowid

            def emit(row: TagRow) -> int:
                cur2 = conn.execute(
                    "INSERT INTO tags "
                    "(file_id, parent_id, depth, full_path, name, tag_type, "
                    " data_type, value_source, type_id, opc_item_path, "
                    " opc_server, source_tag_path, documentation, "
                    " member_signature, raw_properties) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row.as_tuple(),
                )
                return cur2.lastrowid

            root_tag_type = None
            file_nodes = 0
            for rtt, root in _iter_roots(path):
                root_tag_type = rtt
                file_nodes += _walk(root, file_id, None, 0, "", emit)

            conn.execute(
                "UPDATE files SET node_count = ?, root_tag_type = ? WHERE id = ?",
                (file_nodes, root_tag_type, file_id),
            )
            total_nodes += file_nodes
            if verbose:
                print(f"  {path}: {file_nodes} vozlisc ({meta['kind']})")

        build_stats(conn)
        conn.commit()

        summary = {
            "files": len(exports),
            "nodes": total_nodes,
        }
        return summary
    finally:
        conn.close()
