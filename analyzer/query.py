"""Iskanje po indeksu in izpis statistik struktur (samo branje DB)."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

# Preslikava zahtevanih iskalnih polj v stolpce tabele tags.
SEARCH_FIELDS = {
    "fullPath": "full_path",
    "name": "name",
    "opcItemPath": "opc_item_path",
    "sourceTagPath": "source_tag_path",
    "typeId": "type_id",
}

_MODES = ("exact", "prefix", "contains")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(
    conn: sqlite3.Connection,
    field: str,
    value: str,
    mode: str = "contains",
    limit: int = 20,
) -> Dict[str, Any]:
    """Poisci tage po enem polju. Vrne skupno stevilo + vzorec vrstic.

    ``mode``: exact | prefix | contains. Vrstic ne izpisemo tisoce -- samo
    ``limit`` vzorec plus agregat.
    """
    if field not in SEARCH_FIELDS:
        raise ValueError(
            f"Nepoznano polje '{field}'. Dovoljena: {', '.join(SEARCH_FIELDS)}"
        )
    if mode not in _MODES:
        raise ValueError(f"Nepoznan mode '{mode}'. Dovoljeni: {', '.join(_MODES)}")

    col = SEARCH_FIELDS[field]
    if mode == "exact":
        where = f"{col} = ?"
        params: List[Any] = [value]
    elif mode == "prefix":
        where = f"{col} LIKE ? ESCAPE '\\'"
        params = [_escape_like(value) + "%"]
    else:
        where = f"{col} LIKE ? ESCAPE '\\'"
        params = ["%" + _escape_like(value) + "%"]

    total = conn.execute(
        f"SELECT COUNT(*) FROM tags WHERE {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT id, file_id, full_path, name, tag_type, data_type, "
        f"type_id, opc_item_path, source_tag_path "
        f"FROM tags WHERE {where} ORDER BY full_path LIMIT ?",
        params + [limit],
    ).fetchall()

    cols = [
        "id", "file_id", "full_path", "name", "tag_type", "data_type",
        "type_id", "opc_item_path", "source_tag_path",
    ]
    sample = [dict(zip(cols, r)) for r in rows]
    return {"field": field, "mode": mode, "value": value,
            "total": total, "sample": sample}


def get_raw(conn: sqlite3.Connection, tag_id: int) -> Optional[str]:
    """Vrni ohranjeni celoten originalni objekt (raw_properties) taga."""
    row = conn.execute(
        "SELECT raw_properties FROM tags WHERE id = ?", (tag_id,)
    ).fetchone()
    return row[0] if row else None


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Zberi agregatne statistike struktur iz materializiranih tabel."""
    files = conn.execute(
        "SELECT id, site, kind, node_count, path FROM files ORDER BY id"
    ).fetchall()

    tagtype = conn.execute(
        "SELECT tag_type, SUM(cnt) FROM stat_tagtype "
        "GROUP BY tag_type ORDER BY 2 DESC"
    ).fetchall()

    datatype = conn.execute(
        "SELECT data_type, cnt FROM stat_datatype ORDER BY cnt DESC"
    ).fetchall()

    # typeId z vec serializiranimi oblikami INSTANCE. To NI nekonsistentnost:
    # instance pogosto zapisejo le lokalne override, ne celotne definicije.
    override_diversity = conn.execute(
        "SELECT type_id, COUNT(*) AS shapes, SUM(instance_count) AS instances "
        "FROM udt_structures GROUP BY type_id HAVING COUNT(*) > 1 "
        "ORDER BY shapes DESC"
    ).fetchall()

    type_usage = conn.execute(
        "SELECT type_id, SUM(instance_count) AS instances "
        "FROM udt_structures GROUP BY type_id ORDER BY instances DESC LIMIT 20"
    ).fetchall()

    opc_multi = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(tag_count), 0) FROM opc_multiplicity"
    ).fetchone()

    return {
        "files": [
            {"id": f[0], "site": f[1], "kind": f[2], "nodes": f[3], "path": f[4]}
            for f in files
        ],
        "tag_types": [{"tag_type": t[0], "count": t[1]} for t in tagtype],
        "data_types": [{"data_type": d[0], "count": d[1]} for d in datatype],
        "override_shape_diversity": [
            {"type_id": i[0], "shapes": i[1], "instances": i[2]}
            for i in override_diversity
        ],
        "top_type_usage": [
            {"type_id": u[0], "instances": u[1]} for u in type_usage
        ],
        "opc_shared_paths": opc_multi[0],
        "opc_max_sharing": opc_multi[1],
    }
