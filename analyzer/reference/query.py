"""Read-only poizvedbe pricakovanega stanja z izvorom (provenance).

Ne spreminja nicesar. Za izbran site/line (in po zelji tech/group_type/member)
vrne pricakovane sklope, clane, imena in njihov izvor.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


def list_sources(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, filename, sheet_name, site, line, profile_id, sha256, "
        "row_count, group_count, member_count, issue_count, previous_sha256 "
        "FROM reference_sources ORDER BY filename"
    ).fetchall()
    cols = ["id", "filename", "sheet_name", "site", "line", "profile_id",
            "sha256", "row_count", "group_count", "member_count", "issue_count",
            "previous_sha256"]
    return [dict(zip(cols, r)) for r in rows]


def _source_meta(conn: sqlite3.Connection, source_id: int) -> Dict[str, Any]:
    r = conn.execute(
        "SELECT filename, sheet_name, sha256 FROM reference_sources WHERE id=?",
        (source_id,)
    ).fetchone()
    if not r:
        return {}
    return {"filename": r[0], "sheet_name": r[1], "sha256": r[2]}


def get_expected_state(
    conn: sqlite3.Connection,
    site: str,
    line: str,
    tech: Optional[str] = None,
    group_type: Optional[str] = None,
    member: Optional[str] = None,
) -> Dict[str, Any]:
    """Vrni pricakovane sklope + clane + izvor za izbran obseg.

    ``member`` filtrira po pricakovanem imenu clana (expected_name, tocno).
    """
    where = ["g.site = ?", "g.line = ?"]
    params: List[Any] = [site, line]
    if tech:
        where.append("g.tech_number = ?")
        params.append(tech)
    if group_type:
        where.append("g.group_type = ?")
        params.append(group_type)

    sql = (
        "SELECT g.id, g.source_id, g.tech_number, g.maximo_id, g.description, "
        "g.group_type, g.prefix, g.source_row "
        "FROM expected_groups g WHERE " + " AND ".join(where) +
        " ORDER BY g.tech_number, g.group_type, g.prefix, g.source_row"
    )
    groups: List[Dict[str, Any]] = []
    for gr in conn.execute(sql, params).fetchall():
        gid, source_id, tech_number, maximo, desc, gt, prefix, srow = gr
        src = _source_meta(conn, source_id)
        mrows = conn.execute(
            "SELECT member_key, expected_name, required, note, source_row, "
            "source_col_index, source_col_header FROM expected_members "
            "WHERE group_id=? ORDER BY source_col_index, member_key", (gid,)
        ).fetchall()
        members = []
        for m in mrows:
            if member and m[1] != member:
                continue
            members.append({
                "member_key": m[0], "expected_name": m[1],
                "required": None if m[2] is None else bool(m[2]),
                "note": m[3],
                "provenance": {
                    "file": src.get("filename"), "sheet": src.get("sheet_name"),
                    "sha256": src.get("sha256"), "row": m[4],
                    "col_index": m[5], "col_header": m[6],
                },
            })
        if member and not members:
            continue
        groups.append({
            "tech_number": tech_number, "maximo_id": maximo, "description": desc,
            "group_type": gt, "prefix": prefix,
            "provenance": {"file": src.get("filename"),
                           "sheet": src.get("sheet_name"),
                           "sha256": src.get("sha256"), "row": srow},
            "members": members,
        })

    line_tags: List[Dict[str, Any]] = []
    if not (tech or group_type or member):
        for lt in conn.execute(
            "SELECT t.label, t.old_name, t.new_name, t.note, t.source_row, t.source_id "
            "FROM expected_line_tags t WHERE t.site=? AND t.line=? "
            "ORDER BY t.source_row", (site, line)
        ).fetchall():
            src = _source_meta(conn, lt[5])
            line_tags.append({
                "label": lt[0], "old_name": lt[1], "new_name": lt[2], "note": lt[3],
                "provenance": {"file": src.get("filename"),
                               "sheet": src.get("sheet_name"),
                               "sha256": src.get("sha256"), "row": lt[4]},
            })

    return {"site": site, "line": line, "groups": groups, "line_tags": line_tags}


def get_issues(
    conn: sqlite3.Connection, severity: Optional[str] = None,
    code: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if severity:
        where.append("severity = ?")
        params.append(severity.upper())
    if code:
        where.append("code = ?")
        params.append(code)
    sql = "SELECT severity, code, sheet, source_row, source_col_index, " \
          "canonical_key, message FROM import_issues"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY severity, code, source_row, source_col_index"
    cols = ["severity", "code", "sheet", "source_row", "source_col_index",
            "canonical_key", "message"]
    return [dict(zip(cols, r)) for r in conn.execute(sql, params).fetchall()]
