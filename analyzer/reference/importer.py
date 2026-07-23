"""Orkestracija uvoza referencnih CSV v locen SQLite indeks (read-only nad viri).

Gradnja je deterministicna. Posamezni vir je idempotenten po sha256:
  - enak path + enak sha256  -> preskoci (nespremenjeno);
  - enak path + drug sha256  -> zamenjava (izbrisi odvisne vrstice, previous_sha256);
  - nov path                 -> vstavi.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..build import sha256_file
from .model import ParsedSheet, ReferenceSource
from .profiles import (
    PROFILE_LEGEND,
    classify_sheet,
    sheet_name_from_filename,
)
from .reader import parse_legend_sheet, parse_line_sheet, read_rows
from .schema import create_reference_schema


def find_reference_csvs(mappings_dir: str) -> List[str]:
    out: List[str] = []
    for dirpath, _dirs, files in os.walk(mappings_dir):
        for name in files:
            if name.lower().endswith(".csv"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _assert_read_only(db_path: str, mappings_dir: str) -> None:
    db_abs = os.path.abspath(db_path)
    src_abs = os.path.abspath(mappings_dir)
    try:
        common = os.path.commonpath([db_abs, src_abs])
    except ValueError:
        return
    if common == src_abs:
        raise ValueError(
            f"Izhodna DB ({db_abs}) je pod viri ({src_abs}); pisanje prepovedano."
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(path: str, site: Optional[str], sha: str, size: int) -> ParsedSheet:
    """Klasificiraj, preberi in normaliziraj en list. Ohrani izvor v ReferenceSource."""
    sheet = sheet_name_from_filename(path)
    cls = classify_sheet(sheet)
    src = ReferenceSource(
        path=path, filename=os.path.basename(path), sheet_name=sheet,
        site=site, line=cls["line"], profile_id=cls["profile_id"] or "unknown",
        sha256=sha, size_bytes=size, header_json="[]", imported_at=_now(),
    )
    if cls["profile_id"] is None:
        ps = ParsedSheet(source=src)
        from .model import ImportIssue
        ps.issues.append(ImportIssue(
            severity="ERROR", code="REF_UNKNOWN_SHEET_PROFILE", sheet=sheet,
            source_row=None, source_col_index=None, canonical_key=sheet,
            message=f"List '{sheet}' nima znanega layout profila.",
            raw_context_json=None,
        ))
        return ps

    try:
        rows = read_rows(path)
    except UnicodeDecodeError as e:
        ps = ParsedSheet(source=src)
        from .model import ImportIssue
        ps.issues.append(ImportIssue(
            severity="ERROR", code="REF_INVALID_ENCODING", sheet=sheet,
            source_row=None, source_col_index=None, canonical_key=None,
            message=f"Datoteke ni mogoce dekodirati kot CP1250: {e}",
            raw_context_json=None,
        ))
        return ps

    src.header_json = json.dumps(rows[0] if rows else [], ensure_ascii=False)
    src.row_count = max(0, len(rows) - 1)
    if cls["profile_id"] == PROFILE_LEGEND:
        ps = parse_legend_sheet(rows, src)
    else:  # PROFILE_LINE ali PROFILE_TEMPLATE
        ps = parse_line_sheet(rows, src)
    src.group_count = len(ps.groups)
    src.member_count = sum(len(g.members) for g in ps.groups)
    src.issue_count = len(ps.issues)
    return ps


def _delete_source_rows(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute(
        "DELETE FROM expected_members WHERE group_id IN "
        "(SELECT id FROM expected_groups WHERE source_id=?)", (source_id,))
    for tbl in ("expected_groups", "expected_line_tags", "legend_entries",
                "member_templates", "import_issues"):
        conn.execute(f"DELETE FROM {tbl} WHERE source_id=?", (source_id,))


def _insert_parsed(conn: sqlite3.Connection, ps: ParsedSheet, source_id: int) -> None:
    src = ps.source
    for g in ps.groups:
        cur = conn.execute(
            "INSERT INTO expected_groups (source_id, site, line, tech_number, "
            "maximo_id, description, group_type, prefix, source_row, raw_row_json, "
            "status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, g.site, g.line, g.tech_number, g.maximo_id, g.description,
             g.group_type, g.prefix, g.source_row, g.raw_row_json, g.status),
        )
        gid = cur.lastrowid
        for m in g.members:
            conn.execute(
                "INSERT INTO expected_members (group_id, source_id, member_key, "
                "expected_name, required, note, source_row, source_col_index, "
                "source_col_header, raw_value) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (gid, source_id, m.member_key, m.expected_name,
                 None if m.required is None else int(m.required), m.note,
                 m.source_row, m.source_col_index, m.source_col_header, m.raw_value),
            )
    for t in ps.line_tags:
        conn.execute(
            "INSERT INTO expected_line_tags (source_id, site, line, label, "
            "old_name, new_name, note, source_row, raw_row_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (source_id, t.site, t.line, t.label, t.old_name, t.new_name, t.note,
             t.source_row, t.raw_row_json),
        )
    for le in ps.legend:
        conn.execute(
            "INSERT INTO legend_entries (source_id, group_type, kind, old_token, "
            "new_token, meaning, source_row, source_col_index) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (source_id, le.group_type, le.kind, le.old_token, le.new_token,
             le.meaning, le.source_row, le.source_col_index),
        )
    for mt in ps.templates:
        conn.execute(
            "INSERT INTO member_templates (source_id, group_type, member_key, ordinal) "
            "VALUES (?,?,?,?)",
            (source_id, mt.group_type, mt.member_key, mt.ordinal),
        )
    for iss in sorted(ps.issues, key=lambda x: (
            x.source_row or 0, x.source_col_index or 0, x.code)):
        conn.execute(
            "INSERT INTO import_issues (source_id, severity, code, sheet, "
            "source_row, source_col_index, canonical_key, message, raw_context_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (source_id, iss.severity, iss.code, iss.sheet, iss.source_row,
             iss.source_col_index, iss.canonical_key, iss.message,
             iss.raw_context_json),
        )


def import_source(
    conn: sqlite3.Connection, path: str, site: Optional[str]
) -> Dict[str, object]:
    """Uvozi en vir; idempotenten po sha256. Vrne {status, source_id, ...}."""
    sha = sha256_file(path)
    size = os.path.getsize(path)
    existing = conn.execute(
        "SELECT id, sha256 FROM reference_sources WHERE path=?", (path,)
    ).fetchone()

    if existing and existing[1] == sha:
        return {"status": "unchanged", "source_id": existing[0], "sha256": sha}

    ps = _parse(path, site, sha, size)
    src = ps.source

    if existing:  # spremenjen vir -> zamenjava
        source_id = existing[0]
        prev = existing[1]
        _delete_source_rows(conn, source_id)
        conn.execute(
            "UPDATE reference_sources SET sha256=?, size_bytes=?, sheet_name=?, "
            "site=?, line=?, profile_id=?, header_json=?, row_count=?, "
            "group_count=?, member_count=?, issue_count=?, previous_sha256=?, "
            "imported_at=? WHERE id=?",
            (src.sha256, src.size_bytes, src.sheet_name, src.site, src.line,
             src.profile_id, src.header_json, src.row_count, src.group_count,
             src.member_count, src.issue_count, prev, src.imported_at, source_id),
        )
        _insert_parsed(conn, ps, source_id)
        conn.commit()
        return {"status": "replaced", "source_id": source_id, "sha256": sha,
                "previous_sha256": prev}

    cur = conn.execute(
        "INSERT INTO reference_sources (path, filename, sheet_name, site, line, "
        "profile_id, sha256, size_bytes, header_json, row_count, group_count, "
        "member_count, issue_count, previous_sha256, imported_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (src.path, src.filename, src.sheet_name, src.site, src.line,
         src.profile_id, src.sha256, src.size_bytes, src.header_json,
         src.row_count, src.group_count, src.member_count, src.issue_count,
         None, src.imported_at),
    )
    source_id = cur.lastrowid
    _insert_parsed(conn, ps, source_id)
    conn.commit()
    return {"status": "imported", "source_id": source_id, "sha256": sha}


def build_reference_index(
    mappings_dir: str, db_path: str, site: Optional[str], verbose: bool = False
) -> Dict[str, object]:
    """Zgradi referencni indeks iz vseh CSV pod mappings_dir. Prepise DB."""
    _assert_read_only(db_path, mappings_dir)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    try:
        create_reference_schema(conn)
        csvs = find_reference_csvs(mappings_dir)
        results = []
        for path in csvs:
            r = import_source(conn, path, site)
            results.append({"path": path, **r})
            if verbose:
                print(f"  {os.path.basename(path)}: {r['status']}")
        # navzkrizne provere po vstavljanju vseh virov
        from .validate import validate_reference
        n_issues = validate_reference(conn)
        conn.commit()
        return {
            "sources": len(csvs),
            "results": results,
            "cross_row_issues": n_issues,
        }
    finally:
        conn.close()
