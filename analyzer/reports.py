"""Zapis validacijskih porocil v data/generated/analysis (read-only nad raw)."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List

from .validate import CODE_SEVERITY, Finding

_FIELDS = [
    "severity", "code", "internalId", "provider", "fullPath", "typeId",
    "relatedPath", "explanation", "evidence", "suggestedAction",
]


def summarize(findings: List[Finding]) -> Dict:
    by_sev = Counter(f.severity for f in findings)
    by_code = Counter(f.code for f in findings)
    return {"by_severity": dict(by_sev), "by_code": dict(by_code),
            "total": len(findings)}


def write_reports(findings: List[Finding], out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "validation_issues.json")
    csv_path = os.path.join(out_dir, "validation_issues.csv")
    md_path = os.path.join(out_dir, "validation_summary.md")

    dicts = [f.as_dict() for f in findings]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dicts, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for d in dicts:
            w.writerow(d)

    _write_summary_md(findings, md_path)
    return {"json": json_path, "csv": csv_path, "md": md_path}


def _write_summary_md(findings: List[Finding], path: str) -> None:
    summ = summarize(findings)
    by_code_examples: Dict[str, List[Finding]] = defaultdict(list)
    for f in findings:
        if len(by_code_examples[f.code]) < 10:
            by_code_examples[f.code].append(f)

    now = datetime.now(timezone.utc).isoformat()
    lines: List[str] = []
    lines.append("# Validacijsko porocilo")
    lines.append("")
    lines.append(f"Ustvarjeno: {now}")
    lines.append("")
    lines.append("> Read-only analiza. Nic v `data/raw` ni bilo spremenjeno. "
                 "Popravki, preimenovanja in izvoz NISO implementirani.")
    lines.append("")
    lines.append("## Povzetek po resnosti")
    lines.append("")
    lines.append("| Severity | Stevilo |")
    lines.append("|---|---|")
    for sev in ("ERROR", "WARNING", "INFO"):
        lines.append(f"| {sev} | {summ['by_severity'].get(sev, 0)} |")
    lines.append(f"| **Skupaj** | **{summ['total']}** |")
    lines.append("")
    lines.append("## Povzetek po kodi")
    lines.append("")
    lines.append("| Severity | Code | Stevilo |")
    lines.append("|---|---|---|")
    ordered = sorted(
        summ["by_code"].items(),
        key=lambda kv: ({"ERROR": 0, "WARNING": 1, "INFO": 2}.get(
            CODE_SEVERITY.get(kv[0], "INFO"), 3), -kv[1]),
    )
    for code, cnt in ordered:
        lines.append(f"| {CODE_SEVERITY.get(code, '?')} | {code} | {cnt} |")
    lines.append("")
    lines.append("## Reprezentativni primeri (do 10 na kodo)")
    lines.append("")
    for code, _cnt in ordered:
        lines.append(f"### {code} ({CODE_SEVERITY.get(code, '?')})")
        lines.append("")
        for f in by_code_examples[code]:
            rp = f" | related: `{f.relatedPath}`" if f.relatedPath else ""
            tid = f" | typeId: `{f.typeId}`" if f.typeId else ""
            lines.append(
                f"- `{f.fullPath}` (id={f.internalId}, {f.provider}){tid}{rp}  \n"
                f"  {f.explanation}  \n"
                f"  _evidence:_ {f.evidence}"
            )
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
