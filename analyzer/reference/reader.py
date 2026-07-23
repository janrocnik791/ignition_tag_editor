"""Branje in normalizacija referencnih CSV listov (CP1250, podpicje).

Bloki se dolocijo iz glave po IMENIH (ne indeksih), zato so odporni na razlicen
vrstni red/stevilo stolpcev med listi. Vsak zapis ohrani izvor in raw vrednosti.
"""

from __future__ import annotations

import csv
import json
import re
from typing import Dict, List, Optional, Tuple

# Glava tipa sklopa: "<Ime> (prefix)".
_PREFIX_HEADER_RE = re.compile(r"^(.*\S)\s*\(prefix\)$")

from .model import (
    ExpectedGroup,
    ExpectedLineTag,
    ExpectedMember,
    ImportIssue,
    LegendEntry,
    MemberTemplate,
    ParsedSheet,
    ReferenceSource,
)
from .profiles import (
    CSV_DELIMITER,
    CSV_ENCODING,
    CUSTOM_ANCHOR,
    CUSTOM_NEW,
    CUSTOM_OLD,
    GROUP_ANCHORS,
    IDENTITY_HEADERS,
    NOTE_HEADER,
    PROFILE_TEMPLATE,
)


def read_rows(path: str) -> List[List[str]]:
    """Preberi CSV kot seznam vrstic. Lahko vrze UnicodeDecodeError."""
    with open(path, encoding=CSV_ENCODING, newline="") as f:
        return [row for row in csv.reader(f, delimiter=CSV_DELIMITER)]


def _blank(v: Optional[str]) -> bool:
    return v is None or v.strip() == ""


def _resolve_identity(header: List[str]) -> Dict[str, int]:
    idx: Dict[str, int] = {}
    for field, names in IDENTITY_HEADERS.items():
        for nm in names:
            if nm in header:
                idx[field] = header.index(nm)
                break
    return idx


def _detect_blocks(
    header: List[str],
) -> Tuple[List[Dict], Optional[int]]:
    """Vrni (bloki, custom_idx). Blok = {group_type, prefix_idx, member_cols, note_idx}.

    VSAKA glava "<X> (prefix)" je meja bloka -- tudi neznan tip. Tako se stolpci
    neznanega bloka ne vsrkajo v prejsnji znani blok (neznan tip se poroca posebej).
    """
    prefix_anchors: List[Tuple[int, Optional[str]]] = []
    custom_idx: Optional[int] = None
    for i, h in enumerate(header):
        hs = h.strip()
        if _PREFIX_HEADER_RE.match(hs):
            prefix_anchors.append((i, GROUP_ANCHORS.get(hs)))  # None = neznan tip
        elif hs == CUSTOM_ANCHOR and custom_idx is None:
            custom_idx = i

    boundaries = sorted(
        [a[0] for a in prefix_anchors]
        + ([custom_idx] if custom_idx is not None else [len(header)])
    )
    blocks: List[Dict] = []
    for idx, gtype in prefix_anchors:
        if gtype is None:
            continue  # neznan tip sklopa -> ne gradi bloka (poroca se posebej)
        nexts = [b for b in boundaries if b > idx]
        end = min(nexts) if nexts else len(header)
        member_cols: List[Tuple[int, str]] = []
        note_idx: Optional[int] = None
        for c in range(idx + 1, end):
            hc = header[c].strip()
            if hc == NOTE_HEADER:
                note_idx = c
            elif hc:
                member_cols.append((c, hc))
        blocks.append(
            {
                "group_type": gtype,
                "prefix_idx": idx,
                "member_cols": member_cols,
                "note_idx": note_idx,
            }
        )
    return blocks, custom_idx


def _detect_custom(header: List[str], custom_idx: Optional[int]) -> Optional[Dict]:
    if custom_idx is None:
        return None
    old_idx = header.index(CUSTOM_OLD) if CUSTOM_OLD in header else None
    new_idx = header.index(CUSTOM_NEW) if CUSTOM_NEW in header else None
    note_idx = None
    for c in range(custom_idx + 1, len(header)):
        if header[c].strip() == NOTE_HEADER:
            note_idx = c
    return {"label_idx": custom_idx, "old_idx": old_idx,
            "new_idx": new_idx, "note_idx": note_idx}


def _row_get(row: List[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _raw_row_json(header: List[str], row: List[str]) -> str:
    """Celotna originalna vrstica kot urejen slovar glava->celica.

    Dodatne celice brez glave se ohranijo pod kljucem 'col<idx>'.
    """
    obj: Dict[str, str] = {}
    for i, cell in enumerate(row):
        key = header[i] if i < len(header) and header[i].strip() else f"col{i}"
        # ohrani vse; ob podvojenih glavah dodaj indeks
        if key in obj:
            key = f"{key}#{i}"
        obj[key] = cell
    return json.dumps(obj, ensure_ascii=False, sort_keys=False)


def parse_line_sheet(rows: List[List[str]], source: ReferenceSource) -> ParsedSheet:
    """Normaliziraj list druzine 'linija' (L*, tudi predloga 'Novo poimenovanje').

    Za predlogo (PROFILE_TEMPLATE) namesto expected_groups izpolnimo member_templates.
    """
    ps = ParsedSheet(source=source)
    if not rows:
        return ps
    header = rows[0]
    is_template = source.profile_id == PROFILE_TEMPLATE
    ident = _resolve_identity(header)
    blocks, custom_idx = _detect_blocks(header)
    custom = _detect_custom(header, custom_idx)

    # Neznani stolpci: glave, ki jih profil ne porabi (INFO, enkrat na vir).
    consumed = set(ident.values())
    for b in blocks:
        consumed.add(b["prefix_idx"])
        if b["note_idx"] is not None:
            consumed.add(b["note_idx"])
        for c, _h in b["member_cols"]:
            consumed.add(c)
    if custom:
        for k in ("label_idx", "old_idx", "new_idx", "note_idx"):
            if custom[k] is not None:
                consumed.add(custom[k])
    for i, h in enumerate(header):
        hs = h.strip()
        if not hs or i in consumed:
            continue
        m = _PREFIX_HEADER_RE.match(hs)
        if m:
            # Neprepoznan tip sklopa ("<X> (prefix)", X ni znan).
            ps.issues.append(ImportIssue(
                severity="ERROR", code="REF_UNKNOWN_GROUP_TYPE", sheet=source.sheet_name,
                source_row=1, source_col_index=i, canonical_key=m.group(1).strip(),
                message=f"Neznan tip sklopa '{m.group(1).strip()}'.",
                raw_context_json=json.dumps({"header": hs}, ensure_ascii=False),
            ))
        else:
            ps.issues.append(ImportIssue(
                severity="INFO", code="REF_UNKNOWN_COLUMN", sheet=source.sheet_name,
                source_row=1, source_col_index=i, canonical_key=None,
                message=f"Stolpec '{hs}' ni del profila; ohranjen v raw.",
                raw_context_json=json.dumps({"header": hs}, ensure_ascii=False),
            ))

    # Za predlogo zberemo kanonicni nabor clanov iz glave (enkrat).
    if is_template:
        for b in blocks:
            for ordinal, (_c, hdr) in enumerate(b["member_cols"]):
                ps.templates.append(MemberTemplate(
                    group_type=b["group_type"], member_key=hdr, ordinal=ordinal,
                ))

    last = {"tech_number": None, "maximo_id": None, "description": None}
    header_norm = [h.strip() for h in header]

    for i in range(1, len(rows)):
        row = rows[i]
        source_row = i + 1  # 1-osnovana vrstica datoteke (glava = 1)
        if all(_blank(c) for c in row):
            ps.issues.append(ImportIssue(
                severity="INFO", code="REF_BLANK_ROW", sheet=source.sheet_name,
                source_row=source_row, source_col_index=None, canonical_key=None,
                message="Prazna locilna vrstica (preskocena).", raw_context_json=None,
            ))
            continue
        if [c.strip() for c in row] == header_norm:
            ps.issues.append(ImportIssue(
                severity="INFO", code="REF_REPEATED_HEADER", sheet=source.sheet_name,
                source_row=source_row, source_col_index=None, canonical_key=None,
                message="Ponovljena glava (preskocena).", raw_context_json=None,
            ))
            continue

        raw_json = _raw_row_json(header, row)

        # Identiteta z dedovanjem (prazen Stroj ID = nadaljevanje prejsnjega).
        tech_cell = _row_get(row, ident.get("tech_number"))
        maximo_cell = _row_get(row, ident.get("maximo_id"))
        desc_cell = _row_get(row, ident.get("description"))
        if not _blank(tech_cell):
            last["tech_number"] = tech_cell.strip()
        if not _blank(maximo_cell):
            last["maximo_id"] = maximo_cell.strip()
        if not _blank(desc_cell):
            last["description"] = desc_cell.strip()
        tech = last["tech_number"]
        maximo = last["maximo_id"]
        desc = last["description"]

        # Sklopi v tej vrstici.
        for b in blocks:
            prefix = _row_get(row, b["prefix_idx"]).strip()
            members: List[ExpectedMember] = []
            for c, hdr in b["member_cols"]:
                val = _row_get(row, c)
                if _blank(val):
                    continue
                members.append(ExpectedMember(
                    member_key=hdr, expected_name=val.strip(), required=None,
                    note=_row_get(row, b["note_idx"]).strip() or None,
                    source_row=source_row, source_col_index=c,
                    source_col_header=hdr, raw_value=val,
                ))
            if not prefix and not members:
                continue  # blok ni prisoten v tej vrstici
            if is_template:
                continue  # predloga: strukturo smo ze zajeli iz glave
            if tech is None:
                ps.issues.append(ImportIssue(
                    severity="ERROR", code="REF_MISSING_REQUIRED_FIELD",
                    sheet=source.sheet_name, source_row=source_row,
                    source_col_index=b["prefix_idx"],
                    canonical_key=f"{source.site}/{source.line}/{b['group_type']}/{prefix}",
                    message=("Zaseden blok brez tehnoloske stevilke "
                             "(Stroj ID). Vrstica ni normalizirana."),
                    raw_context_json=raw_json,
                ))
                continue
            ps.groups.append(ExpectedGroup(
                site=source.site, line=source.line, tech_number=tech,
                maximo_id=maximo, description=desc, group_type=b["group_type"],
                prefix=prefix or None, source_row=source_row, raw_row_json=raw_json,
                status="OK", members=members,
            ))

        # Linijski custom tagi (samo dejanske preslikave star->nov).
        if custom and not is_template:
            label = _row_get(row, custom["label_idx"]).strip()
            old = _row_get(row, custom["old_idx"]).strip()
            new = _row_get(row, custom["new_idx"]).strip()
            note = _row_get(row, custom["note_idx"]).strip()
            if old or new:
                ps.line_tags.append(ExpectedLineTag(
                    site=source.site, line=source.line, label=label or None,
                    old_name=old or None, new_name=new or None, note=note or None,
                    source_row=source_row, raw_row_json=raw_json,
                ))

    return ps


def parse_legend_sheet(rows: List[List[str]], source: ReferenceSource) -> ParsedSheet:
    """Normaliziraj legendo: 3 glave (tip / Prefix|Suffix / Staro|Novo|Pomen).

    Zdruzena polja Excela se v CSV izvozijo kot vrednost v prvem stolpcu +
    prazni sledeci; zato tip in kind naprej-zapolnimo (forward-fill).
    """
    ps = ParsedSheet(source=source)
    if len(rows) < 3:
        ps.issues.append(ImportIssue(
            severity="ERROR", code="REF_HEADER_MISMATCH", sheet=source.sheet_name,
            source_row=1, source_col_index=None, canonical_key=None,
            message="Legenda nima pricakovanih treh glav.", raw_context_json=None,
        ))
        return ps

    r_type, r_kind, r_role = rows[0], rows[1], rows[2]
    ncols = max(len(r_type), len(r_kind), len(r_role))

    def ff(row: List[str], n: int) -> List[str]:
        out: List[str] = []
        cur = ""
        for i in range(n):
            v = row[i].strip() if i < len(row) else ""
            if v:
                cur = v
            out.append(cur)
        return out

    types = ff(r_type, ncols)
    kinds_raw = [(r_kind[i].strip() if i < len(r_kind) else "") for i in range(ncols)]
    # kind se zapolni naprej znotraj istega tipa
    kinds: List[str] = []
    cur_kind = ""
    prev_type = None
    for i in range(ncols):
        if types[i] != prev_type:
            cur_kind = ""
            prev_type = types[i]
        if kinds_raw[i]:
            cur_kind = kinds_raw[i]
        kinds.append(cur_kind)
    roles = [(r_role[i].strip().lower() if i < len(r_role) else "") for i in range(ncols)]

    # Zgradi bloke (trojcke): nov blok ob spremembi tipa/kind ali ponovitvi vloge.
    blocks: List[Dict] = []  # {gtype, kind, start_col, cols:{role:idx}}
    cur: Optional[Dict] = None
    for i in range(ncols):
        role = roles[i]
        if role not in ("staro", "novo", "pomen"):
            continue
        key = (types[i], kinds[i])
        if cur is None or key != (cur["gtype"], cur["kind"]) or role in cur["cols"]:
            cur = {"gtype": types[i], "kind": kinds[i], "start_col": i, "cols": {}}
            blocks.append(cur)
        cur["cols"][role] = i

    # Preberi podatkovne vrstice (od 4. naprej) v vsak blok.
    for ri in range(3, len(rows)):
        row = rows[ri]
        source_row = ri + 1
        if all(_blank(c) for c in row):
            continue
        for blk in blocks:
            cols = blk["cols"]
            old = _row_get(row, cols.get("staro"))
            new = _row_get(row, cols.get("novo"))
            mean = _row_get(row, cols.get("pomen"))
            if _blank(old) and _blank(new) and _blank(mean):
                continue
            kind = blk["kind"].lower() if blk["kind"] else None
            ps.legend.append(LegendEntry(
                group_type=blk["gtype"] or None, kind=kind,
                old_token=old.strip() or None, new_token=new.strip() or None,
                meaning=mean.strip() or None, source_row=source_row,
                source_col_index=blk["start_col"],
            ))
    return ps
