"""Navzkrizne provere referencnega modela (podvojitve, konflikti, razlike lokacij).

Provere so ponovljive: pred racunanjem izbrisemo lastne kode in jih izracunamo
znova iz baze. Zapise ob-vrsticne kode (encoding, manjkajoca polja ...) ustvari
importer; tu obravnavamo le identitete cez vec vrstic/virov.

Kanonicna identiteta:
- sklop:  (site, line, group_type, prefix, tech_number)
- clan:   (sklop, member_key)
- ime:    (site, line, expected_name)  -- za zaznavo trkov imen
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

CROSS_ROW_CODES = ("REF_DUPLICATE_ROW", "REF_CONFLICT", "REF_LOCATION_DIFFERENCE",
                   "REF_SHARED_EXPECTED_NAME")


def _group_key(site, line, gt, prefix, tech) -> str:
    return f"{site}/{line}/{gt}/{prefix}/{tech}"


def _member_map(conn: sqlite3.Connection, group_id: int) -> Tuple[Tuple[str, str], ...]:
    rows = conn.execute(
        "SELECT member_key, expected_name FROM expected_members "
        "WHERE group_id=? ORDER BY member_key, expected_name", (group_id,)
    ).fetchall()
    return tuple((r[0], r[1]) for r in rows)


def _add(conn, source_id, severity, code, sheet, source_row, key, message, ctx):
    conn.execute(
        "INSERT INTO import_issues (source_id, severity, code, sheet, source_row, "
        "source_col_index, canonical_key, message, raw_context_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (source_id, severity, code, sheet, source_row, None, key, message,
         json.dumps(ctx, ensure_ascii=False) if ctx is not None else None),
    )


def _sheet_of(conn, source_id) -> Optional[str]:
    r = conn.execute(
        "SELECT sheet_name FROM reference_sources WHERE id=?", (source_id,)
    ).fetchone()
    return r[0] if r else None


def _check_group_collisions(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id, site, line, group_type, prefix, tech_number, source_id, source_row "
        "FROM expected_groups ORDER BY id"
    ).fetchall()
    by_key: Dict[Tuple, List] = defaultdict(list)
    for r in rows:
        by_key[(r[1], r[2], r[3], r[4], r[5])].append(r)

    n = 0
    for key, gs in sorted(by_key.items(), key=lambda kv: str(kv[0])):
        if len(gs) < 2:
            continue
        baseline_map = _member_map(conn, gs[0][0])
        base_row = gs[0][7]
        for g in gs[1:]:
            gid, site, line, gt, prefix, tech, source_id, source_row = g
            m = _member_map(conn, gid)
            ckey = _group_key(site, line, gt, prefix, tech)
            if m == baseline_map:
                _add(conn, source_id, "WARNING", "REF_DUPLICATE_ROW",
                     _sheet_of(conn, source_id), source_row, ckey,
                     f"Tocen dvojnik sklopa (ista identiteta in vrednosti) "
                     f"kot vrstica {base_row}.",
                     {"canonical_key": ckey, "duplicate_of_row": base_row})
            else:
                _add(conn, source_id, "ERROR", "REF_CONFLICT",
                     _sheet_of(conn, source_id), source_row, ckey,
                     f"Nasprotujoc sklop: ista identiteta kot vrstica {base_row}, "
                     f"a razlicni clani/vrednosti.",
                     {"canonical_key": ckey, "conflicts_with_row": base_row,
                      "this": list(m), "other": list(baseline_map)})
            n += 1
    return n


def _check_name_collisions(conn: sqlite3.Connection) -> int:
    """Isto pricakovano ime v vec vlogah = INFO (deljena referenca), NE napaka.

    V realnih tabelah je npr. SP meritve isto ime kot PV regulatorja -- to je
    veljavna referenca, ne dvojnik. Zato je INFO (podobno kot deljen opcItemPath).
    Trd konflikt ostane le pri isti identiteti sklopa z razlicnimi vrednostmi.
    """
    rows = conn.execute(
        "SELECT g.site, g.line, m.expected_name, m.group_id, m.member_key, "
        "m.source_id, m.source_row "
        "FROM expected_members m JOIN expected_groups g ON g.id = m.group_id "
        "WHERE m.expected_name IS NOT NULL AND m.expected_name <> '' "
        "ORDER BY g.site, g.line, m.expected_name, m.group_id, m.member_key"
    ).fetchall()
    by_name: Dict[Tuple, List] = defaultdict(list)
    for r in rows:
        by_name[(r[0], r[1], r[2])].append(r)

    n = 0
    for (site, line, name), occ in sorted(by_name.items(), key=lambda kv: str(kv[0])):
        roles = {(o[3], o[4]) for o in occ}  # (group_id, member_key)
        if len(roles) < 2:
            continue
        base = occ[0]
        for o in occ[1:]:
            if (o[3], o[4]) == (base[3], base[4]):
                continue
            _add(conn, o[5], "INFO", "REF_SHARED_EXPECTED_NAME", _sheet_of(conn, o[5]),
                 o[6], f"{site}/{line}/{name}",
                 f"Pricakovano ime '{name}' se pojavi v vec vlogah v "
                 f"{site}/{line} (deljena referenca, ne napaka).",
                 {"expected_name": name, "first_row": base[6]})
            n += 1
    return n


def _check_location_differences(conn: sqlite3.Connection) -> int:
    """Isti logicni sklop (brez site) v vec lokacijah z razlicnimi clani -> INFO."""
    rows = conn.execute(
        "SELECT id, site, line, group_type, prefix, tech_number, source_id, source_row "
        "FROM expected_groups ORDER BY id"
    ).fetchall()
    by_logical: Dict[Tuple, List] = defaultdict(list)
    for r in rows:
        by_logical[(r[2], r[3], r[4], r[5])].append(r)  # brez site

    n = 0
    for key, gs in sorted(by_logical.items(), key=lambda kv: str(kv[0])):
        sites = {g[1] for g in gs}
        if len(sites) < 2:
            continue
        # primerjaj clane po lokacijah; emitiraj le ce se razlikujejo
        per_site_map: Dict[str, Tuple] = {}
        for g in gs:
            per_site_map.setdefault(g[1], _member_map(conn, g[0]))
        distinct = {v for v in per_site_map.values()}
        if len(distinct) < 2:
            continue
        line, gt, prefix, tech = key
        for g in gs:
            _add(conn, g[6], "INFO", "REF_LOCATION_DIFFERENCE",
                 _sheet_of(conn, g[6]), g[7], f"{line}/{gt}/{prefix}/{tech}",
                 f"Sklop {tech} ({gt}/{prefix}) v liniji {line} se med "
                 f"lokacijami razlikuje: {sorted(sites)}.",
                 {"sites": sorted(sites)})
            n += 1
    return n


def validate_reference(conn: sqlite3.Connection) -> int:
    """Izbrisi in ponovno izracunaj navzkrizne kode. Vrne stevilo ugotovitev."""
    qmarks = ",".join("?" * len(CROSS_ROW_CODES))
    conn.execute(f"DELETE FROM import_issues WHERE code IN ({qmarks})", CROSS_ROW_CODES)
    n = 0
    n += _check_group_collisions(conn)
    n += _check_name_collisions(conn)
    n += _check_location_differences(conn)
    conn.commit()
    return n
