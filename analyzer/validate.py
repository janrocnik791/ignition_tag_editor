"""Read-only validator Ignition izvozov.

Ne spreminja nicesar; bere SQLite indeks (in po potrebi surove datoteke za
INVALID_JSON) ter vrne ugotovitve po kategorijah ERROR / WARNING / INFO.

Pomembna semantika:
- Razlicne serializirane oblike instanc istega typeId so override oblike
  (INFO INSTANCE_OVERRIDE_SHAPE), NE napake.
- Prazen typeId na gnezdeni instanci NI napaka (tip doloca nadrejena
  definicija) -- WARNING EMPTY_TYPE_ID z razlago.
- Odsotnost clana je najvec INFO, dokler ni pravila v rules/member_requirements.
- Vec tagov z istim opcItemPath je INFO, ne napaka.
- Zunanji (neuvozeni) provider je INFO, ne poskodovana referenca.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .rules import load_member_requirements
from .udt_resolver import (
    BUILTIN_UDT_PARAMS,
    braces_balanced,
    build_registry,
    param_tokens,
    provider_token,
    type_key_from_full_path,
)

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

_SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

# Kategorija -> severity (za validacijo in filtriranje).
CODE_SEVERITY = {
    # ERROR
    "INVALID_JSON": SEVERITY_ERROR,
    "UNKNOWN_UDT_TYPE": SEVERITY_ERROR,
    "DUPLICATE_UDT_DEFINITION": SEVERITY_ERROR,
    "MISSING_PARENT_UDT": SEVERITY_ERROR,
    "UDT_INHERITANCE_CYCLE": SEVERITY_ERROR,
    "INVALID_PATH_TEMPLATE": SEVERITY_ERROR,
    # WARNING
    "EMPTY_TYPE_ID": SEVERITY_WARNING,
    "UNRESOLVED_PARAMETER": SEVERITY_WARNING,
    "UNRESOLVED_INTERNAL_REFERENCE": SEVERITY_WARNING,
    "INSTANCE_MEMBER_NOT_IN_DEFINITION": SEVERITY_WARNING,
    "TYPE_ID_TAGTYPE_MISMATCH": SEVERITY_WARNING,
    # INFO
    "INSTANCE_OVERRIDE_SHAPE": SEVERITY_INFO,
    "SHARED_OPC_ITEM_PATH": SEVERITY_INFO,
    "EXTERNAL_PROVIDER_REFERENCE": SEVERITY_INFO,
    "OPTIONAL_MEMBER_ABSENT": SEVERITY_INFO,
}


@dataclass
class Finding:
    severity: str
    code: str
    internalId: Optional[int]
    provider: Optional[str]
    fullPath: Optional[str]
    typeId: Optional[str]
    relatedPath: Optional[str]
    explanation: str
    evidence: str
    suggestedAction: str

    def as_dict(self) -> Dict:
        return asdict(self)


class Validator:
    def __init__(self, conn: sqlite3.Connection, rules_dir: Optional[str] = None):
        self.conn = conn
        self.reg = build_registry(conn)
        self.rules = load_member_requirements(rules_dir) if rules_dir else {}
        self.findings: List[Finding] = []

    def add(self, code: str, **kw) -> None:
        self.findings.append(
            Finding(severity=CODE_SEVERITY[code], code=code, **kw)
        )

    # ---- posamezni pregledi ------------------------------------------

    def check_duplicate_definitions(self) -> None:
        for (site, key), recs in self.reg.copies.items():
            if len(recs) < 2:
                continue
            # Konflikt le, ce se efektivna vsebina kopij razlikuje.
            sigs = {(frozenset(r.direct_members), r.parent or "") for r in recs}
            same_file = len({r.file_id for r in recs}) < len(recs)
            if len(sigs) > 1 or same_file:
                rec = recs[0]
                self.add(
                    "DUPLICATE_UDT_DEFINITION",
                    internalId=rec.id, provider=site, fullPath=rec.full_path,
                    typeId=key, relatedPath=None,
                    explanation=(
                        "Vec kopij UDT definicije z isto potjo se razlikuje po "
                        "clanih ali starsu (ali se pojavi vec v isti datoteki)."
                    ),
                    evidence=(
                        f"kopij={len(recs)}, razlicnih struktur={len(sigs)}, "
                        f"datoteke={sorted({r.file_id for r in recs})}"
                    ),
                    suggestedAction=(
                        "Rocno preveri, katera definicija je pravilna; ne "
                        "spreminjaj samodejno."
                    ),
                )

    def check_inheritance(self) -> None:
        for (site, key), rec in self.reg.canonical.items():
            parent = rec.parent
            if not parent:
                continue
            if (site, parent) not in self.reg.canonical:
                self.add(
                    "MISSING_PARENT_UDT",
                    internalId=rec.id, provider=site, fullPath=rec.full_path,
                    typeId=key, relatedPath=parent,
                    explanation="Podedovani parent UDT tip ne obstaja v tem providerju.",
                    evidence=f"parent typeId={parent!r}",
                    suggestedAction="Preveri manjkajoco definicijo ali napacen typeId.",
                )
                continue
            # cikel: parent veriga se vrne na key
            chain = self.reg.inheritance_chain(site, key)
            # ce zadnji clen kaze nazaj v verigo -> cikel
            last = self.reg.canonical.get((site, chain[-1]))
            if last and last.parent and last.parent in chain:
                self.add(
                    "UDT_INHERITANCE_CYCLE",
                    internalId=rec.id, provider=site, fullPath=rec.full_path,
                    typeId=key, relatedPath=" -> ".join(chain),
                    explanation="Veriga dedovanja UDT tipov tvori cikel.",
                    evidence=f"veriga={' -> '.join(chain)} -> {last.parent}",
                    suggestedAction="Odpravi krozno referenco parent typeId.",
                )

    def check_definition_bindings(self) -> None:
        """Parametri in sablone poti v clanih UDT definicij."""
        rows = self.conn.execute(
            "SELECT t.id, f.site, t.full_path, t.source_tag_path, t.parent_id "
            "FROM tags t JOIN files f ON f.id = t.file_id "
            "WHERE t.source_tag_path IS NOT NULL"
        ).fetchall()
        for tid, site, full_path, binding, parent_id in rows:
            # Doloci lastniski UDT tip (najblizji UdtType prednik).
            owner_key = self._owning_type_key(parent_id)
            if not braces_balanced(binding):
                self.add(
                    "INVALID_PATH_TEMPLATE",
                    internalId=tid, provider=site, fullPath=full_path,
                    typeId=owner_key, relatedPath=binding,
                    explanation="Neuravnotezene zavite oklepaje v sablonski poti.",
                    evidence=f"binding={binding!r}",
                    suggestedAction="Popravi sablono parametra {..}.",
                )
                continue
            # Neznan parameter (le ce lahko dolocimo lastniski tip).
            # Vgrajeni Ignition parametri (InstanceName, ...) so vedno veljavni.
            if owner_key is not None:
                eff_params = self.reg.effective_params(site, owner_key)
                for p in param_tokens(binding):
                    if p in BUILTIN_UDT_PARAMS or p in eff_params:
                        continue
                    self.add(
                        "UNRESOLVED_PARAMETER",
                        internalId=tid, provider=site, fullPath=full_path,
                        typeId=owner_key, relatedPath=binding,
                        explanation=(
                            "Sablona uporablja parameter, ki ni deklariran "
                            "v UDT tipu ali podedovanih tipih (in ni vgrajen)."
                        ),
                        evidence=f"parameter={p!r}, deklarirani={sorted(eff_params)}",
                        suggestedAction="Preveri ime parametra ali deklaracijo.",
                    )
            # Zunanji provider (INFO). Parametrizirani providerji ([{x}]) niso
            # zunanji -- so predloge in jih preskocimo.
            tok = provider_token(binding)
            if (tok and "{" not in tok and tok != "provider"
                    and self.reg.internal_providers
                    and tok not in self.reg.internal_providers):
                self.add(
                    "EXTERNAL_PROVIDER_REFERENCE",
                    internalId=tid, provider=site, fullPath=full_path,
                    typeId=owner_key, relatedPath=binding,
                    explanation=(
                        "Referenca kaze na provider, ki ni med uvozenimi. "
                        "To je informacija, ne poskodovana referenca."
                    ),
                    evidence=f"provider={tok!r}, interni={sorted(self.reg.internal_providers)}",
                    suggestedAction="Po potrebi uvozi zunanji provider za popolno razresitev.",
                )

    def _owning_type_key(self, node_id: Optional[int]) -> Optional[str]:
        """Poisci najblizji UdtType prednik in vrni njegov typeId kljuc."""
        cur = node_id
        guard = 0
        while cur is not None and guard < 100:
            guard += 1
            row = self.conn.execute(
                "SELECT tag_type, full_path, parent_id FROM tags WHERE id = ?",
                (cur,),
            ).fetchone()
            if row is None:
                return None
            tag_type, full_path, parent_id = row
            if tag_type == "UdtType":
                return type_key_from_full_path(full_path)
            cur = parent_id
        return None

    def check_instances(self) -> None:
        rows = self.conn.execute(
            "SELECT t.id, f.site, t.full_path, t.type_id, t.parent_id "
            "FROM tags t JOIN files f ON f.id = t.file_id "
            "WHERE t.tag_type = 'UdtInstance'"
        ).fetchall()
        for tid, site, full_path, type_id, parent_id in rows:
            members = {
                m[0] for m in self.conn.execute(
                    "SELECT name FROM tags WHERE parent_id = ?", (tid,)
                ).fetchall()
            }

            if not type_id:
                parent_type = None
                if parent_id is not None:
                    prow = self.conn.execute(
                        "SELECT tag_type FROM tags WHERE id = ?", (parent_id,)
                    ).fetchone()
                    parent_type = prow[0] if prow else None
                nested = parent_type in ("UdtInstance", "UdtType")
                self.add(
                    "EMPTY_TYPE_ID",
                    internalId=tid, provider=site, fullPath=full_path,
                    typeId=None, relatedPath=None,
                    explanation=(
                        "Instanca brez typeId. " + (
                            "Gnezden clan -- tip doloca nadrejena definicija (obicajno)."
                            if nested else
                            "Vrhnja instanca brez tipa -- preveri izvor."
                        )
                    ),
                    evidence=f"parent_tagType={parent_type!r}, #clanov={len(members)}",
                    suggestedAction=(
                        "Obicajno ni potrebno ukrepanje pri gnezdenih clanih."
                    ),
                )
                continue

            rec = self.reg.canonical_get(site, type_id)
            if rec is None:
                self.add(
                    "UNKNOWN_UDT_TYPE",
                    internalId=tid, provider=site, fullPath=full_path,
                    typeId=type_id, relatedPath=None,
                    explanation="typeId instance nima definicije v tem providerju.",
                    evidence=f"typeId={type_id!r}",
                    suggestedAction="Preveri manjkajoco UDT definicijo ali napacen typeId.",
                )
                continue

            eff = self.reg.effective_members(site, type_id)
            extra = members - eff
            absent = eff - members

            if extra:
                self.add(
                    "INSTANCE_MEMBER_NOT_IN_DEFINITION",
                    internalId=tid, provider=site, fullPath=full_path,
                    typeId=type_id, relatedPath=None,
                    explanation=(
                        "Instanca vsebuje clane, ki jih ni v razreseni definiciji "
                        "(vkljucno z dedovanjem). To je opozorilo, ne napaka."
                    ),
                    evidence=f"clani_ne_v_def={sorted(extra)}",
                    suggestedAction="Preveri, ali gre za zastarel clan ali napacen typeId.",
                )

            if members and members < eff:
                self.add(
                    "INSTANCE_OVERRIDE_SHAPE",
                    internalId=tid, provider=site, fullPath=full_path,
                    typeId=type_id, relatedPath=None,
                    explanation=(
                        "Instanca serializira le podmnozico clanov definicije "
                        "(lokalni override). To je pricakovano, ne napaka."
                    ),
                    evidence=(
                        f"#serializiranih={len(members)}, #v_definiciji={len(eff)}, "
                        f"odsotni={sorted(absent)[:10]}"
                    ),
                    suggestedAction="Ni ukrepanja; zgolj informacija.",
                )

            # Pravila optional (samo ce obstajajo). Required NAMERNO ne emitira
            # kode izven fiksnega seznama kategorij -- validator ne izumlja
            # napak. Odsotnost clana ostane INFO (OPTIONAL_MEMBER_ABSENT) le,
            # ko je clan eksplicitno oznacen kot opcijski v pravilih.
            spec = self.rules.get(type_id)
            if spec:
                for opt in spec.get("optional", []):
                    if opt not in members and opt in eff:
                        self.add(
                            "OPTIONAL_MEMBER_ABSENT",
                            internalId=tid, provider=site, fullPath=full_path,
                            typeId=type_id, relatedPath=None,
                            explanation="Opcijski clan (po pravilu) je odsoten.",
                            evidence=f"clan={opt!r}",
                            suggestedAction="Ni nujno ukrepanje; odvisno od namena.",
                        )

    def check_type_id_tagtype(self) -> None:
        rows = self.conn.execute(
            "SELECT t.id, f.site, t.full_path, t.type_id, t.tag_type "
            "FROM tags t JOIN files f ON f.id = t.file_id "
            "WHERE t.type_id IS NOT NULL AND t.type_id <> '' "
            "AND t.tag_type NOT IN ('UdtInstance', 'UdtType')"
        ).fetchall()
        for tid, site, full_path, type_id, tag_type in rows:
            self.add(
                "TYPE_ID_TAGTYPE_MISMATCH",
                internalId=tid, provider=site, fullPath=full_path,
                typeId=type_id, relatedPath=None,
                explanation="Vozlisce ima typeId, a ni UdtInstance/UdtType.",
                evidence=f"tagType={tag_type!r}, typeId={type_id!r}",
                suggestedAction="Preveri, ali je typeId na pravem vozliscu.",
            )

    def check_shared_opc(self) -> None:
        rows = self.conn.execute(
            "SELECT opc_item_path, tag_count FROM opc_multiplicity "
            "ORDER BY tag_count DESC"
        ).fetchall()
        for opc, cnt in rows:
            sample = self.conn.execute(
                "SELECT t.id, f.site, t.full_path FROM tags t "
                "JOIN files f ON f.id = t.file_id "
                "WHERE t.opc_item_path = ? ORDER BY t.full_path LIMIT 5",
                (opc,),
            ).fetchall()
            first = sample[0]
            others = "; ".join(s[2] for s in sample[1:])
            self.add(
                "SHARED_OPC_ITEM_PATH",
                internalId=first[0], provider=first[1], fullPath=first[2],
                typeId=None, relatedPath=others,
                explanation=(
                    "Vec tagov deli isti opcItemPath. To je informacija "
                    "(en OPC naslov je lahko na vec tagih), ne napaka."
                ),
                evidence=f"opcItemPath={opc!r}, #tagov={cnt}",
                suggestedAction="Ni ukrepanja; zgolj informacija.",
            )

    def run(self) -> List[Finding]:
        self.check_duplicate_definitions()
        self.check_inheritance()
        self.check_definition_bindings()
        self.check_instances()
        self.check_type_id_tagtype()
        self.check_shared_opc()
        self.findings.sort(
            key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.code,
                           f.fullPath or "")
        )
        return self.findings


def check_json_parseable(paths: List[str]) -> List[Finding]:
    """Preveri, ali se surove datoteke parsajo (INVALID_JSON). Read-only."""
    out: List[Finding] = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception as e:  # noqa: BLE001
            out.append(Finding(
                severity=SEVERITY_ERROR, code="INVALID_JSON",
                internalId=None, provider=os.path.basename(os.path.dirname(p)),
                fullPath=p, typeId=None, relatedPath=None,
                explanation="Datoteke ni mogoce parsati kot JSON.",
                evidence=str(e)[:300],
                suggestedAction="Preveri izvoz; datoteka je morda okvarjena.",
            ))
    return out


def validate(
    conn: sqlite3.Connection,
    rules_dir: Optional[str] = None,
    rules: Optional[Dict] = None,
    raw_paths: Optional[List[str]] = None,
) -> List[Finding]:
    """Zagon validacije. ``rules`` (dict) prevlada nad ``rules_dir``."""
    v = Validator(conn, rules_dir=rules_dir)
    if rules is not None:
        v.rules = rules
    findings: List[Finding] = []
    if raw_paths:
        findings.extend(check_json_parseable(raw_paths))
    findings.extend(v.run())
    return findings
