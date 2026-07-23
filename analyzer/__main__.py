"""CLI vmesnik.

Phase 0 (tag indeks): build | search | stats | raw | validate | inspect-udt
Phase 1 (referencni model): ref-build | ref-sources | ref-validate | ref-query

Primeri:
    python -m analyzer build
    python -m analyzer search --field opcItemPath --value DB2318 --mode contains
    python -m analyzer stats
    python -m analyzer validate --severity error
    python -m analyzer inspect-udt --type-id "Siemens/Meritev_alarm_SP"
    python -m analyzer ref-build --mappings data/mappings --site stahovica
    python -m analyzer ref-sources
    python -m analyzer ref-validate
    python -m analyzer ref-query --site stahovica --line L400 --tech 410
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from .build import build_index
from .query import SEARCH_FIELDS, get_raw, search, stats
from .reports import summarize, write_reports
from .validate import CODE_SEVERITY, validate
from .udt_resolver import build_registry

# Privzete poti glede na koren repozitorija (mapa nad paketom analyzer).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RAW = os.path.join(_ROOT, "data", "raw")
DEFAULT_DB = os.path.join(_ROOT, "data", "generated", "tag_index.sqlite")
DEFAULT_RULES = os.path.join(_ROOT, "rules")
DEFAULT_ANALYSIS = os.path.join(_ROOT, "data", "generated", "analysis")
DEFAULT_MAPPINGS = os.path.join(_ROOT, "data", "mappings")
DEFAULT_REF_DB = os.path.join(_ROOT, "data", "generated", "reference_index.sqlite")
DEFAULT_REF_OUT = os.path.join(_ROOT, "data", "generated", "reference")


def _connect_ro(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        sys.exit(f"DB ne obstaja: {db_path}. Najprej zazeni 'build'.")
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def cmd_build(args: argparse.Namespace) -> None:
    summary = build_index(args.raw, args.db, verbose=True)
    print(
        f"Zgrajeno: {summary['files']} datotek, {summary['nodes']} vozlisc "
        f"-> {args.db}"
    )


def cmd_search(args: argparse.Namespace) -> None:
    conn = _connect_ro(args.db)
    try:
        res = search(conn, args.field, args.value, args.mode, args.limit)
    finally:
        conn.close()
    print(
        f"Polje={res['field']} mode={res['mode']} value={res['value']!r} "
        f"-> zadetkov: {res['total']} (vzorec {len(res['sample'])})"
    )
    for r in res["sample"]:
        extra = r.get("opc_item_path") or r.get("source_tag_path") or ""
        tid = f" typeId={r['type_id']}" if r["type_id"] else ""
        print(f"  [{r['id']}] {r['full_path']}  ({r['tag_type']}){tid}  {extra}")


def cmd_stats(args: argparse.Namespace) -> None:
    conn = _connect_ro(args.db)
    try:
        s = stats(conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print("== Datoteke ==")
    for f in s["files"]:
        print(f"  {f['site']}/{f['kind']}: {f['nodes']} vozlisc")
    print("\n== Tipi tagov ==")
    for t in s["tag_types"]:
        print(f"  {t['tag_type']}: {t['count']}")
    print("\n== Podatkovni tipi (top 10) ==")
    for d in s["data_types"][:10]:
        print(f"  {d['data_type']}: {d['count']}")
    print("\n== Najveckrat uporabljeni UDT tipi (top 10) ==")
    for u in s["top_type_usage"][:10]:
        print(f"  {u['type_id']}: {u['instances']} instanc")
    print("\n== Raznolikost override oblik instanc (NI napaka; vec oblik na typeId) ==")
    if not s["override_shape_diversity"]:
        print("  (ni je)")
    for i in s["override_shape_diversity"][:20]:
        print(f"  {i['type_id']}: {i['shapes']} serializiranih oblik "
              f"({i['instances']} instanc)")
    print(f"\n== opcItemPath ==\n  deljenih poti (>1 tag): {s['opc_shared_paths']}"
          f", najvec tagov na eni poti: {s['opc_max_sharing']}")


def cmd_raw(args: argparse.Namespace) -> None:
    conn = _connect_ro(args.db)
    try:
        raw = get_raw(conn, args.id)
    finally:
        conn.close()
    if raw is None:
        sys.exit(f"Ni taga z id={args.id}")
    print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))


def _raw_paths(raw_root):
    out = []
    for dp, _d, files in os.walk(raw_root):
        for n in files:
            if n.lower().endswith(".json"):
                out.append(os.path.join(dp, n))
    return sorted(out)


def cmd_validate(args: argparse.Namespace) -> None:
    conn = _connect_ro(args.db)
    try:
        findings = validate(
            conn, rules_dir=args.rules, raw_paths=_raw_paths(args.raw)
        )
    finally:
        conn.close()

    # Filtri (na izpis; porocila vsebujejo vse ugotovitve).
    shown = findings
    if args.severity:
        sev = args.severity.upper()
        shown = [f for f in shown if f.severity == sev]
    if args.code:
        shown = [f for f in shown if f.code == args.code]

    paths = write_reports(findings, args.out)
    summ = summarize(findings)
    print("== Ugotovitve po resnosti ==")
    for s in ("ERROR", "WARNING", "INFO"):
        print(f"  {s}: {summ['by_severity'].get(s, 0)}")
    print(f"  Skupaj: {summ['total']}")
    print("\n== Po kodi ==")
    for code, cnt in sorted(
        summ["by_code"].items(),
        key=lambda kv: ({'ERROR': 0, 'WARNING': 1, 'INFO': 2}.get(
            CODE_SEVERITY.get(kv[0], 'INFO'), 3), -kv[1])):
        print(f"  {CODE_SEVERITY.get(code, '?'):7s} {code}: {cnt}")
    print(f"\nPorocila: {paths['md']}\n          {paths['csv']}\n          {paths['json']}")

    if args.severity or args.code:
        print(f"\n== Filtriran izpis ({len(shown)}) ==")
        for f in shown[:args.limit]:
            print(f"  [{f.severity}] {f.code} | {f.fullPath} "
                  f"(id={f.internalId}) | {f.evidence}")


def cmd_inspect_udt(args: argparse.Namespace) -> None:
    conn = _connect_ro(args.db)
    try:
        reg = build_registry(conn)
        key = args.type_id
        found = False
        for (site, k), rec in reg.canonical.items():
            if k != key:
                continue
            found = True
            eff_m = sorted(reg.effective_members(site, k))
            eff_p = sorted(reg.effective_params(site, k))
            chain = reg.inheritance_chain(site, k)
            n_copies = len(reg.copies[(site, k)])
            print(f"== {key} @ {site} ==")
            print(f"  definicija full_path: {rec.full_path}  (kopij={n_copies})")
            print(f"  parent typeId: {rec.parent}")
            print(f"  veriga dedovanja: {' -> '.join(chain)}")
            print(f"  lastni clani ({len(rec.direct_members)}): {sorted(rec.direct_members)}")
            print(f"  EFEKTIVNI clani ({len(eff_m)}): {eff_m}")
            print(f"  efektivni parametri ({len(eff_p)}): {eff_p}")
            # serializirane oblike instanc
            shapes = conn.execute(
                "SELECT member_signature, COUNT(*) FROM tags "
                "WHERE tag_type='UdtInstance' AND type_id=? "
                "GROUP BY member_signature ORDER BY 2 DESC", (key,)
            ).fetchall()
            print(f"  serializirane oblike instanc ({len(shapes)}):")
            for sig, cnt in shapes[:8]:
                names = json.loads(sig) if sig else []
                extra = sorted(set(names) - set(eff_m))
                print(f"    #{cnt:5d}  clanov={len(names):2d}  "
                      f"podmnozica_def={set(names) <= set(eff_m)}  "
                      f"clani_ne_v_def={extra}")
            print()
        if not found:
            print(f"Tip '{key}' ni najden med definicijami.")
    finally:
        conn.close()


# ---- Faza 1: referencni model -----------------------------------------

def cmd_ref_build(args: argparse.Namespace) -> None:
    from .reference.importer import build_reference_index

    summary = build_reference_index(
        args.mappings, args.db, site=args.site, verbose=True
    )
    print(f"Uvozenih virov: {summary['sources']} -> {args.db}")
    for r in summary["results"]:
        print(f"  {os.path.basename(r['path'])}: {r['status']}")
    print(f"Navzkriznih ugotovitev: {summary['cross_row_issues']}")


def cmd_ref_sources(args: argparse.Namespace) -> None:
    from .reference.query import list_sources

    conn = _connect_ro(args.db)
    try:
        rows = list_sources(conn)
    finally:
        conn.close()
    for s in rows:
        print(f"  [{s['id']}] {s['filename']} ({s['profile_id']}) "
              f"site={s['site']} line={s['line']}")
        print(f"       sha256={s['sha256'][:16]}... rows={s['row_count']} "
              f"groups={s['group_count']} members={s['member_count']} "
              f"issues={s['issue_count']}")


def cmd_ref_validate(args: argparse.Namespace) -> None:
    import sqlite3

    from .reference.reports import write_reference_reports
    from .reference.validate import validate_reference

    conn = sqlite3.connect(args.db)
    try:
        n = validate_reference(conn)
        paths = write_reference_reports(conn, args.out)
        counts = dict(conn.execute(
            "SELECT severity, COUNT(*) FROM import_issues GROUP BY severity"
        ).fetchall())
    finally:
        conn.close()
    print("== Referencne ugotovitve po resnosti ==")
    for sev in ("ERROR", "WARNING", "INFO"):
        print(f"  {sev}: {counts.get(sev, 0)}")
    print(f"Navzkriznih (na novo izracunanih): {n}")
    print(f"Porocila: {paths['md']}\n          {paths['csv']}\n          {paths['json']}")


def cmd_ref_query(args: argparse.Namespace) -> None:
    from .reference.query import get_expected_state

    conn = _connect_ro(args.db)
    try:
        res = get_expected_state(
            conn, site=args.site, line=args.line, tech=args.tech,
            group_type=args.group_type, member=args.member,
        )
    finally:
        conn.close()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print(f"== Pricakovano stanje: {res['site']}/{res['line']} "
          f"({len(res['groups'])} sklopov) ==")
    for g in res["groups"]:
        pr = g["provenance"]
        print(f"\nSklop {g['tech_number']} \"{g['description'] or ''}\" "
              f"[{g['group_type']} prefix={g['prefix']}]  "
              f"(vrstica {pr['row']}, {pr['file']})")
        for m in g["members"]:
            mp = m["provenance"]
            print(f"    {m['member_key']:12s} = {m['expected_name']:24s} "
                  f"(row {mp['row']} col \"{mp['col_header']}\")")
    if res["line_tags"]:
        print(f"\n== Linijski custom tagi ({len(res['line_tags'])}) ==")
        for t in res["line_tags"]:
            print(f"    {t['label'] or '':22s} {t['old_name']} -> {t['new_name']}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="analyzer", description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB, help="pot do SQLite indeksa")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="zgradi indeks iz data/raw")
    b.add_argument("--raw", default=DEFAULT_RAW, help="mapa data/raw")
    b.set_defaults(func=cmd_build)

    se = sub.add_parser("search", help="iskanje po polju")
    se.add_argument("--field", required=True, choices=list(SEARCH_FIELDS))
    se.add_argument("--value", required=True)
    se.add_argument("--mode", default="contains",
                    choices=["exact", "prefix", "contains"])
    se.add_argument("--limit", type=int, default=20)
    se.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="agregatne statistike")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_stats)

    r = sub.add_parser("raw", help="izpisi raw_properties taga")
    r.add_argument("--id", type=int, required=True)
    r.set_defaults(func=cmd_raw)

    va = sub.add_parser("validate", help="read-only validacija + porocila")
    va.add_argument("--raw", default=DEFAULT_RAW, help="mapa data/raw (za INVALID_JSON)")
    va.add_argument("--rules", default=DEFAULT_RULES, help="mapa s pravili")
    va.add_argument("--out", default=DEFAULT_ANALYSIS, help="izhodna mapa porocil")
    va.add_argument("--severity", choices=["error", "warning", "info"],
                    help="filtriraj izpis po resnosti")
    va.add_argument("--code", help="filtriraj izpis po kodi")
    va.add_argument("--limit", type=int, default=30)
    va.set_defaults(func=cmd_validate)

    iu = sub.add_parser("inspect-udt", help="podroben pregled UDT tipa")
    iu.add_argument("--type-id", required=True, dest="type_id")
    iu.set_defaults(func=cmd_inspect_udt)

    # --- Faza 1: referencni model ---
    rb = sub.add_parser("ref-build", help="uvozi referencne CSV v locen indeks")
    rb.add_argument("--mappings", default=DEFAULT_MAPPINGS, help="mapa z referencnimi CSV")
    rb.add_argument("--db", default=DEFAULT_REF_DB, help="izhodni referencni indeks")
    rb.add_argument("--site", default=None, help="lokacija virov (npr. stahovica)")
    rb.set_defaults(func=cmd_ref_build)

    rs = sub.add_parser("ref-sources", help="izpisi uvozene referencne vire")
    rs.add_argument("--db", default=DEFAULT_REF_DB, help="referencni indeks")
    rs.set_defaults(func=cmd_ref_sources)

    rv = sub.add_parser("ref-validate", help="navzkrizne provere + porocila")
    rv.add_argument("--db", default=DEFAULT_REF_DB, help="referencni indeks")
    rv.add_argument("--out", default=DEFAULT_REF_OUT, help="izhodna mapa porocil")
    rv.set_defaults(func=cmd_ref_validate)

    rq = sub.add_parser("ref-query", help="poizvedba pricakovanega stanja")
    rq.add_argument("--db", default=DEFAULT_REF_DB, help="referencni indeks")
    rq.add_argument("--site", required=True)
    rq.add_argument("--line", required=True)
    rq.add_argument("--tech", default=None, help="tehnoloska stevilka (Stroj ID nov)")
    rq.add_argument("--group-type", default=None, dest="group_type")
    rq.add_argument("--member", default=None, help="tocno pricakovano ime clana")
    rq.add_argument("--json", action="store_true")
    rq.set_defaults(func=cmd_ref_query)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
