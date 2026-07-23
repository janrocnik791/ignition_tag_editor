"""Zapis referencnih porocil (zavrnjene/konfliktne vrstice) v generated mapo.

Read-only nad viri. Vsaka zavrnjena vrstica ostane sledljiva (izvor + raw).
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List

_SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}
_FIELDS = ["severity", "code", "sheet", "source_row", "source_col_index",
           "canonical_key", "message", "raw_context_json"]


def _fetch_issues(conn) -> List[Dict]:
    rows = conn.execute(
        "SELECT i.severity, i.code, i.sheet, i.source_row, i.source_col_index, "
        "i.canonical_key, i.message, i.raw_context_json, s.filename "
        "FROM import_issues i LEFT JOIN reference_sources s ON s.id = i.source_id "
        "ORDER BY i.severity, i.code, i.source_row, i.source_col_index"
    ).fetchall()
    cols = _FIELDS + ["filename"]
    out = [dict(zip(cols, r)) for r in rows]
    out.sort(key=lambda d: (_SEVERITY_ORDER.get(d["severity"], 9), d["code"],
                            d["source_row"] or 0, d["source_col_index"] or 0))
    return out


def write_reference_reports(conn, out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    issues = _fetch_issues(conn)
    json_path = os.path.join(out_dir, "reference_issues.json")
    csv_path = os.path.join(out_dir, "reference_issues.csv")
    md_path = os.path.join(out_dir, "reference_issues.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS + ["filename"])
        w.writeheader()
        for d in issues:
            w.writerow(d)

    _write_md(issues, md_path)
    return {"json": json_path, "csv": csv_path, "md": md_path}


def _write_md(issues: List[Dict], path: str) -> None:
    by_sev = Counter(i["severity"] for i in issues)
    by_code = Counter(i["code"] for i in issues)
    examples: Dict[str, List[Dict]] = defaultdict(list)
    for i in issues:
        if len(examples[i["code"]]) < 10:
            examples[i["code"]].append(i)

    now = datetime.now(timezone.utc).isoformat()
    lines = ["# Referencno uvozno porocilo", "", f"Ustvarjeno: {now}", "",
             "> Read-only. Nic v virih (data/mappings) ni bilo spremenjeno.", "",
             "## Povzetek po resnosti", "", "| Severity | Stevilo |", "|---|---|"]
    for sev in ("ERROR", "WARNING", "INFO"):
        lines.append(f"| {sev} | {by_sev.get(sev, 0)} |")
    lines += ["", "## Povzetek po kodi", "", "| Code | Stevilo |", "|---|---|"]
    for code, cnt in sorted(by_code.items(),
                            key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {code} | {cnt} |")
    lines += ["", "## Primeri (do 10 na kodo)", ""]
    for code, _c in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"### {code}")
        lines.append("")
        for i in examples[code]:
            loc = f"row {i['source_row']}" if i["source_row"] else "(vir)"
            col = f" col {i['source_col_index']}" if i["source_col_index"] is not None else ""
            key = f" | key: `{i['canonical_key']}`" if i["canonical_key"] else ""
            lines.append(f"- `{i['filename']}` {loc}{col}{key}  \n  {i['message']}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
