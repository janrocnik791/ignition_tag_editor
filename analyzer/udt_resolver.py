"""Razresevanje UDT definicij, dedovanja, parametrov in providerjev.

Kljucne semanticne odlocitve (glej tudi CLAUDE.md):
- typeId instance se razresi na definicijo po poti ``_types_/<typeId>`` znotraj
  ISTEGA providerja (site).
- Definicije so pogosto izvozene dvakrat (v UDT in UNS datoteki istega site) --
  to je redundanca, ne konflikt, razen ce se vsebina razlikuje.
- Efektivna struktura clana = lastni clani zdruzeni z dedovanimi (po verigi
  parent typeId). Instance serializirajo le podmnozico (override) -- to NI
  nekonsistentnost.
"""

from __future__ import annotations

import json as _json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

_PROVIDER_TOKEN = re.compile(r"\[([^\]]+)\]")
_PARAM_TOKEN = re.compile(r"\{([^}]+)\}")

_TYPES_PREFIX = "_types_/"

# Vgrajeni Ignition UDT parametri -- vedno veljavni, ceprav niso deklarirani.
BUILTIN_UDT_PARAMS = frozenset({
    "InstanceName", "ParentInstanceName", "RootInstanceName",
    "PathToParentFolder", "TagName",
})

# Vgrajeni/posebni provider tokeni, ki niso "zunanji".
#   '.'      -> relativna referenca na trenutni provider
#   'System' -> Ignition sistemski provider
BUILTIN_PROVIDERS = frozenset({".", "System"})


def type_key_from_full_path(full_path: str) -> str:
    """'_types_/Siemens/Meritev' -> 'Siemens/Meritev'."""
    if full_path.startswith(_TYPES_PREFIX):
        return full_path[len(_TYPES_PREFIX):]
    return full_path


def provider_token(binding: Optional[str]) -> Optional[str]:
    """Prvi ``[PROVIDER]`` token iz vezave/opcItemPath."""
    if not binding:
        return None
    m = _PROVIDER_TOKEN.search(binding)
    return m.group(1) if m else None


def param_tokens(binding: Optional[str]) -> List[str]:
    if not binding:
        return []
    return _PARAM_TOKEN.findall(binding)


def braces_balanced(binding: Optional[str]) -> bool:
    if not binding:
        return True
    depth = 0
    for ch in binding:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


@dataclass
class DefRecord:
    """Ena kopija UDT definicije (lahko jih je vec kopij na (site, key))."""

    id: int
    site: str
    file_id: int
    kind: str
    full_path: str
    key: str
    parent: Optional[str]
    params: Set[str]
    direct_members: Set[str]


@dataclass
class UdtRegistry:
    conn: sqlite3.Connection
    io_filenames: List[str] = field(default_factory=list)
    # (site, key) -> kanonicni DefRecord
    canonical: Dict[Tuple[str, str], DefRecord] = field(default_factory=dict)
    # (site, key) -> vse kopije
    copies: Dict[Tuple[str, str], List[DefRecord]] = field(default_factory=dict)
    internal_providers: Set[str] = field(default_factory=set)
    _eff_members: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)
    _eff_params: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)

    def canonical_get(self, site: str, key: str) -> Optional[DefRecord]:
        return self.canonical.get((site, key))

    def effective_members(
        self, site: str, key: str, _seen: Optional[Set[str]] = None
    ) -> Set[str]:
        ck = (site, key)
        if ck in self._eff_members:
            return self._eff_members[ck]
        _seen = _seen or set()
        rec = self.canonical.get(ck)
        if rec is None or key in _seen:
            return set()
        _seen = _seen | {key}
        members = set(rec.direct_members)
        if rec.parent:
            members |= self.effective_members(site, rec.parent, _seen)
        self._eff_members[ck] = members
        return members

    def effective_params(
        self, site: str, key: str, _seen: Optional[Set[str]] = None
    ) -> Set[str]:
        ck = (site, key)
        if ck in self._eff_params:
            return self._eff_params[ck]
        _seen = _seen or set()
        rec = self.canonical.get(ck)
        if rec is None or key in _seen:
            return set()
        _seen = _seen | {key}
        params = set(rec.params)
        if rec.parent:
            params |= self.effective_params(site, rec.parent, _seen)
        self._eff_params[ck] = params
        return params

    def inheritance_chain(self, site: str, key: str) -> List[str]:
        """Veriga typeId od tipa navzgor; ustavi se pri ciklu ali manjkajocem."""
        chain: List[str] = []
        seen: Set[str] = set()
        cur: Optional[str] = key
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            rec = self.canonical.get((site, cur))
            cur = rec.parent if rec else None
        return chain


def build_registry(conn: sqlite3.Connection) -> UdtRegistry:
    io_filenames = [
        r[0] for r in conn.execute(
            "SELECT path FROM files WHERE kind = 'io'"
        ).fetchall()
    ]
    reg = UdtRegistry(conn=conn, io_filenames=io_filenames)

    # Zberi vse UdtType definicije s pripadajocimi clani in parametri.
    rows = conn.execute(
        "SELECT t.id, f.site, t.file_id, f.kind, t.full_path, t.type_id, "
        "       t.raw_properties "
        "FROM tags t JOIN files f ON f.id = t.file_id "
        "WHERE t.tag_type = 'UdtType'"
    ).fetchall()

    for tid, site, file_id, kind, full_path, parent, raw in rows:
        key = type_key_from_full_path(full_path)
        obj = _json.loads(raw)
        params = set((obj.get("parameters") or {}).keys())
        members = {
            m[0] for m in conn.execute(
                "SELECT name FROM tags WHERE parent_id = ?", (tid,)
            ).fetchall()
        }
        rec = DefRecord(
            id=tid, site=site, file_id=file_id, kind=kind,
            full_path=full_path, key=key,
            parent=(parent if parent else None),
            params=params, direct_members=members,
        )
        reg.copies.setdefault((site, key), []).append(rec)

    # Kanonicna izbira: prednost 'udt' datoteki, sicer prva.
    for ck, recs in reg.copies.items():
        canonical = next((r for r in recs if r.kind == "udt"), recs[0])
        reg.canonical[ck] = canonical

    # Interni providerji: tokeni, ki se ujemajo z imenom KATERE KOLI uvozene
    # datoteke (IO in UNS providerji so vsi uvozeni). Poleg tega so interni
    # vgrajeni tokeni (relativni '.', 'System'). Predloge ([{provider}]) niso
    # zunanji -- so parametri in jih ne stejemo med providerje.
    all_names = [
        os.path.basename(p).lower()
        for (p,) in conn.execute("SELECT path FROM files").fetchall()
    ]
    tokens = set()
    for r in conn.execute(
        "SELECT DISTINCT source_tag_path FROM tags "
        "WHERE source_tag_path IS NOT NULL"
    ).fetchall():
        tok = provider_token(r[0])
        if tok:
            tokens.add(tok)
    reg.internal_providers |= BUILTIN_PROVIDERS
    for tok in tokens:
        if "{" in tok:
            continue  # parametriziran provider -- ne klasificiramo
        low = tok.lower()
        if low in ("provider",):
            continue
        if any(low in name for name in all_names):
            reg.internal_providers.add(tok)

    return reg
