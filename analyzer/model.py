"""Podatkovni model in ekstrakcija lastnosti iz Ignition tag vozlisc.

Vsako vozlisce v izvozu je slovar z ``name``, ``tagType`` in po zelji ``tags``
(otroci). Iz posameznega vozlisca ekstrahiramo iskalna polja, celoten
originalni objekt (brez otrok) pa ohranimo v ``raw_properties``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Prepoznane vrste izvoznih datotek.
KIND_IO = "io"
KIND_UNS = "uns"
KIND_UDT = "udt"


def classify_file(path: str) -> Dict[str, str]:
    """Iz poti razberi ``site`` (mapa) in ``kind`` (vrsta izvoza).

    Ne ugiba iz vsebine -- samo iz imen. Vrne ``kind == "unknown"``, ce ga ne
    prepozna (tako datoteko vseeno indeksiramo, brez posebne obravnave).
    """
    import os

    lower = os.path.basename(path).lower()
    site = os.path.basename(os.path.dirname(path))
    if "udt" in lower or "definition" in lower:
        kind = KIND_UDT
    elif "uns" in lower:
        kind = KIND_UNS
    elif "io" in lower:
        kind = KIND_IO
    else:
        kind = "unknown"
    return {"site": site, "kind": kind}


def _flatten_binding(value: Any) -> Optional[str]:
    """Splosci polje, ki je lahko niz ali objekt ``{bindType, binding}``.

    ``opcItemPath`` je obicajno niz, ``sourceTagPath`` pa gnezden objekt. Za
    iskanje potrebujemo niz; celoten objekt ostane v ``raw_properties``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Ignition binding objekt: uporabi 'binding', sicer serializiraj.
        if "binding" in value and isinstance(value["binding"], str):
            return value["binding"]
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _member_signature(node: Dict[str, Any]) -> Optional[str]:
    """Za UDT instance/tipe: urejen seznam imen neposrednih otrok.

    Sluzi grupiranju enakih struktur. Manjkajoc clan zgolj ustvari drugo
    signaturo -- ni napaka.
    """
    children = node.get("tags")
    if not isinstance(children, list):
        return None
    names = sorted(
        str(c.get("name", "")) for c in children if isinstance(c, dict)
    )
    return json.dumps(names, ensure_ascii=False)


@dataclass
class TagRow:
    """En zapis v tabeli ``tags`` -- eno vozlisce drevesa."""

    file_id: int
    parent_id: Optional[int]
    depth: int
    full_path: str
    name: str
    tag_type: Optional[str]
    data_type: Optional[str]
    value_source: Optional[str]
    type_id: Optional[str]
    opc_item_path: Optional[str]
    opc_server: Optional[str]
    source_tag_path: Optional[str]
    documentation: Optional[str]
    member_signature: Optional[str]
    raw_properties: str

    @classmethod
    def from_node(
        cls,
        node: Dict[str, Any],
        file_id: int,
        parent_id: Optional[int],
        depth: int,
        parent_full_path: str,
    ) -> "TagRow":
        name = str(node.get("name", ""))
        # Poti ne normaliziramo; koren ima prazno ime.
        if parent_full_path:
            full_path = parent_full_path + "/" + name
        else:
            full_path = name

        # Celoten originalni objekt vozlisca BREZ otrok (ti so loceni zapisi).
        raw = {k: v for k, v in node.items() if k != "tags"}

        return cls(
            file_id=file_id,
            parent_id=parent_id,
            depth=depth,
            full_path=full_path,
            name=name,
            tag_type=node.get("tagType"),
            data_type=node.get("dataType"),
            value_source=node.get("valueSource"),
            type_id=node.get("typeId"),
            opc_item_path=_flatten_binding(node.get("opcItemPath")),
            opc_server=node.get("opcServer"),
            source_tag_path=_flatten_binding(node.get("sourceTagPath")),
            documentation=node.get("documentation"),
            member_signature=_member_signature(node),
            raw_properties=json.dumps(raw, ensure_ascii=False, sort_keys=True),
        )

    def as_tuple(self) -> tuple:
        return (
            self.file_id,
            self.parent_id,
            self.depth,
            self.full_path,
            self.name,
            self.tag_type,
            self.data_type,
            self.value_source,
            self.type_id,
            self.opc_item_path,
            self.opc_server,
            self.source_tag_path,
            self.documentation,
            self.member_signature,
            self.raw_properties,
        )


@dataclass
class IndexedFile:
    """Metapodatki o eni indeksirani izvorni datoteki."""

    path: str
    site: str
    kind: str
    root_tag_type: Optional[str]
    size_bytes: int
    sha256: str
    node_count: int = 0
    id: Optional[int] = None
