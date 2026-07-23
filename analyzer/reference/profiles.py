"""Deklarativni layout profili referencnih listov (konfiguracija, ne koda).

Vsi trije listi iz druzine "linija" (L400, L1600, Novo poimenovanje) delijo isto
strukturo blokov. Bralnik veze clane po IMENU glave znotraj bloka, ne po fiksnem
indeksu -- zato so odporni na razlicen vrstni red/stevilo stolpcev med listi.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

# CSV je izvozen iz Excela s podpicjem in kodno stranjo Windows Central Europe.
CSV_ENCODING = "cp1250"
CSV_DELIMITER = ";"

# Prepoznani tipi sklopov (prefix bloki v listih linij).
KNOWN_GROUP_TYPES = (
    "Meritev",
    "Stikala",
    "Ventili",
    "Motorji",
    "Regulatorji",
    "Filtri",
)

# Glava "<Ime> (prefix)" -> tip sklopa.
GROUP_ANCHORS: Dict[str, str] = {
    "Meritev (prefix)": "Meritev",
    "Stikala (prefix)": "Stikala",
    "Ventili (prefix)": "Ventili",
    "Motorji (prefix)": "Motorji",
    "Regulatorji (prefix)": "Regulatorji",
    "Filtri (prefix)": "Filtri",
}

# Glave identitete (vec moznih zapisov -- Maximo/star).
IDENTITY_HEADERS = {
    "tech_number": ("Stroj ID (nov)",),
    "maximo_id": ("Stroj ID (Maximo)", "Stroj ID (star)"),
    "description": ("Stroj opis",),
}

# Zakljucni custom blok.
CUSTOM_ANCHOR = "Custom"
CUSTOM_OLD = "Star tag"
CUSTOM_NEW = "Nov tag"
NOTE_HEADER = "Opomba"

# Profili: line (L*), template (Novo poimenovanje), legend (Legenda).
PROFILE_LINE = "line_v1"
PROFILE_TEMPLATE = "template_v1"
PROFILE_LEGEND = "legend_v1"


def sheet_name_from_filename(path: str) -> str:
    """Iz "struktura_preimenovanje_tagov(L400).csv" vrne "L400".

    Ce ni oklepajev, vrne ime datoteke brez koncnice.
    """
    base = os.path.basename(path)
    m = re.search(r"\(([^)]+)\)", base)
    if m:
        return m.group(1).strip()
    stem, _ext = os.path.splitext(base)
    return stem.strip()


_LINE_RE = re.compile(r"^L\s*\d+", re.IGNORECASE)


def classify_sheet(sheet_name: str) -> Dict[str, Optional[str]]:
    """Doloci profil, tip in (ce obstaja) oznako linije iz imena lista.

    Vrne {"profile_id", "line"} ali {"profile_id": None} ce ni prepoznan.
    """
    name = sheet_name.strip()
    low = name.lower()
    if _LINE_RE.match(name):
        return {"profile_id": PROFILE_LINE, "line": name}
    if low.startswith("novo"):
        return {"profile_id": PROFILE_TEMPLATE, "line": None}
    if low.startswith("legenda"):
        return {"profile_id": PROFILE_LEGEND, "line": None}
    return {"profile_id": None, "line": None}
