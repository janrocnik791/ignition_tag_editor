# Ignition Tag Editor – razvojna smer in implementacijski načrt

**Status dokumenta:** delovna produktna usmeritev (editor-first ponovni zagon smeri)
**Ciljno okolje:** Calcit, Ignition 8.3
**Primarni lokaciji:** Stahovica in Gospić
**Datum posodobitve:** 23. julij 2026

> Ta dokument je bil v celoti prepisan. Vsebuje **eno samo** nepротislovno smer:
> najprej uporaben vizualni urejevalnik, šele nato referenčne tabele, samodejno
> grupiranje in približno ujemanje. Prejšnji vrstni red (najprej referenčni uvoz in
> hevristike) je opuščen. Zgodovina že opravljenega dela (Faza 0 in referenčni
> uvoznik) je ohranjena in prekvalificirana kot podporna infrastruktura.

---

## 1. Namen programa

Namizni program, ki iz obstoječih Ignition izvozov omogoča:

1. prebrati in ohraniti celotno strukturo IO, UNS in UDT tagov;
2. jih vizualno pregledovati in navigirati po velikih drevesih;
3. prikazati **samo dokazljive (exact)** relacije med plastmi tagov;
4. pustiti nerešene relacije **vidno nerešene**;
5. omogočiti uporabniku ročno ustvarjanje in potrditev relacij;
6. varno voditi spremembe v ločeni delovni kopiji (operacije, ne mutacije);
7. simulirati končno drevo, prikazati diff, undo/redo;
8. izdelati varen, omejen Ignition 8.3 JSON izvoz z round-trip preverjanjem;
9. eno ročno dokončano linijo uporabiti kot golden dataset;
10. **šele nato** dodati referenčne tabele, pravila grupiranja in približno ujemanje.

Program ni neposredni urejevalnik aktivnega Gatewaya. Izvorni JSON ostane
nespremenjen; vse spremembe se vodijo v ločenem delovnem modelu, v Ignition pa
prenesejo šele prek pregledanega izvoza.

## 2. Ciljno okolje

- Ignition **8.3**
- Python **3.13**
- Calcit **Stahovica** in **Gospić**
- vhod: IO, UNS in UDT **JSON izvozi**
- offline namizna aplikacija
- **brez** neposrednega pisanja v aktivni Gateway ali PLC

## 3. Trenutno implementirano stanje (baseline)

Testi zeleni: `python -m pytest -q` → **64 uspešnih** (43 Faza 0 + 21 referenčni uvoz).

**Faza 0 – analitična osnova (zaključeno, ostaja):** `analyzer/`
- JSON parser + SQLite indeks tagov (`build.py`, `model.py`, `schema.py`);
- iskalni sloj (`query.py`);
- razreševanje UDT definicij, dedovanja, parametrov, override (`udt_resolver.py`);
- read-only validator z razlago (`validate.py`), poročila (`reports.py`);
- CLI (`__main__.py`): `build | search | stats | raw | validate | inspect-udt`.
- V realni rabi ~277.000 vozlišč; izvorne datoteke ostanejo nespremenjene.

**Referenčni uvoznik (zaključeno, prekvalificirano v podporno infrastrukturo):**
`analyzer/reference/`
- ločen `reference_index.sqlite`, uvoz CP1250 CSV izvozov (`ref-build | ref-sources |
  ref-validate | ref-query`), model pričakovanega stanja, navzkrizna validacija.
- **Ostaja uporaben, a NE določa več takojšnjega razvojnega vrstnega reda.** Priključi
  se pozneje (mejnik J) kot neobvezen kontekst in vir predlogov.

**Ugotovljene omejitve obstoječega indeksa za urejanje/izvoz** (dejansko preverjeno):
- tabela `tags` nima stolpca za **vrstni red sorojencev** (red je le implicitno po rowid);
- `id` je rowid, ki se ob vsaki gradnji **na novo dodeli** → ni stabilna identiteta;
- **provider ni eksplicit** (izpeljan iz imena datoteke, koren JSON ima `name:""`);
- `raw_properties` = originalni objekt **brez otrok** → za brezizgubno rekonstrukcijo
  je treba otroke ponovno vgnezditi v izvirnem vrstnem redu.

**Še ne obstaja:** grafični vmesnik, trajni model ročnih relacij, model operacij,
simulirano drevo, undo/redo, izvoz.

## 4. Produktna strategija in razlog za spremenjen vrstni red

Prejšnja smer je postavljala referenčni uvoz, samodejno mapiranje, prepoznavo sklopov
in približno poimenovanje **pred** kakršenkoli vizualni urejevalnik. Glavna težava je
bila **manjkajoča povratna zanka**: parserji in hevristike lahko poljubno rastejo, a
uporabnik njihovega rezultata ne vidi, ne more ročno popraviti relacije, urediti
strukture ali preizkusiti majhne end-to-end spremembe.

Nova strategija je **editor-first**: najprej najpreprostejši uporaben urejevalnik,
vidna in navigabilna struktura, samo dokazljive relacije, ročne popravke, varen model
delovne kopije, omejen Ignition 8.3 izvoz z round-trip preverjanjem — in šele nato
avtomatizacija, ki se **ocenjuje** proti eni ročno dokončani liniji (golden dataset).
Urejevalnik je temelj, na katerem se pozneje meri vsaka avtomatika. Ročni popravki in
dokončane linije postanejo testni podatki za prihodnje hevristike.

## 5. Celoten uporabniški tok (end-to-end)

```text
Ustvari ali odpri delovni projekt
→ uvozi IO, UNS in UDT JSON iz več providerjev in lokacij (nespremenljiv baseline)
→ izberi provider/lokacijo/vejo in navigiraj veliko drevo (lazy)
→ išči in preglej tag (raw + efektivne lastnosti, UDT kontekst, provenance)
→ preglej SAMO dokazljive relacije; nerešeno ostane vidno nerešeno
→ ročno poveži/razveži, potrdi ali zavrni relacijo
→ stage-aj operacije (create/rename/move/update) ločeno od baseline
→ simuliraj končno drevo, preglej diff, undo/redo
→ shrani/ponovno odpri projekt brez izgube relacij in operacij
→ izberi omejen obseg in izdelaj Ignition 8.3 JSON izvoz
→ ponovno uvozi izvoz v aplikacijo in preveri enakost načrtovanega stanja
```

## 6. Arhitekturne plasti in pretok podatkov

Stroga ločitev; urejanje **nikoli** ne mutira baseline vrstic.

1. **Katalog virov** – registrirane uvozne datoteke (path, sha256, provider, site, kind,
   import_session, imported_at).
2. **Baseline (nespremenljiv)** – uvožena vozlišča: `node_uid`, `provider_uid`,
   `parent_uid`, `sibling_index`, `depth`, `path_at_import`, `tag_type`, izvlečena iskalna
   polja, `raw_json` (celoten originalni objekt brez otrok).
3. **Relacije** – exact + ročne povezave z dokazom/stanjem/revizijo.
4. **Operacije** – stage-ane spremembe (delovna kopija).
5. **Metapodatki projekta** – ime, schema_version, obseg izbora/izvoza, undo kazalec.
6. **Referenčni podatki (neobvezno, pozneje)** – obstoječi `reference_index` priključen
   read-only kot kontekst/predlogi.
7. **Generirani izhod** – izvozni paketi (datoteke na disku, izven projekta).

Pretok: `import_service` (parse + provider identiteta) → **baseline** →
`repository` (lazy drevo + podrobnosti) → `relationships` (exact + ročno) →
`operations` (delovna kopija) → `simulation` (efektivno drevo + diff) →
`export` (Ignition 8.3 + round-trip). Referenčni uvoznik piše **predloge** v model
relacij (mejnik J).

## 7. Temeljne invariante

- Uvožene JSON datoteke so nespremenljive.
- Originalni objekti tagov in **neznane** Ignition lastnosti se ohranijo.
- Baseline, ročne relacije, operacije, simulirano stanje in izvoz so **ločene plasti**.
- Urejanje nikoli ne posodobi baseline vrstic neposredno.
- Relacija ohrani dokaz, izvor, stanje in revizijske podatke.
- Exact dokaz in ročna potrditev sta **različna** vira relacije; ročna potrditev
  prevlada nad prihodnjimi hevristikami; noben fuzzy zadetek ne postane tiho odobren.
- Nerešena relacija je veljavno stanje produkta.
- Vsaka identiteta nosi dovolj site/provider konteksta, da ni trkov.
- Preimenovanje/premik ne uničita stabilne interne identitete vozlišča.
- Velika drevesa se nalagajo **lazy**/paginirano; program ostane odziven pri realnem
  obsegu Calcita.
- Izvoz je **deterministicen** in omejen; brez pisanja v aktivni Gateway.
- Resnični Calcit izvozi in iz njih izpeljani zaupni fixture se nikoli ne commit-ajo.

## 8. Model projekta / podatkovne baze

Projekt = mapa z **enim samostojnim `project.sqlite`**, ki vsebuje nespremenljiv
posnetek baseline-a **in** ločene tabele za relacije, operacije in metapodatke.
Prenosljiv, single-writer, enostavna varnostna kopija/obnovitev. Baseline se napolni z
**ponovno uporabo** logike Faze 0 (`build._walk`, `model.TagRow`, `sha256_file`).

Tabele (uvedene po mejnikih, glej §15):
- `project_meta(schema_version, name, created_at, undo_cursor, export_scope_json, ...)`
- `sources(id, path, sha256, provider_name, site, kind, import_session, imported_at)`
- `baseline_nodes(node_uid PK, provider_uid, parent_uid, sibling_index, depth,
  path_at_import, name, tag_type, data_type, value_source, type_id, opc_item_path,
  opc_server, source_tag_path, raw_json, source_id)`
- `relationships(...)` (§10)
- `operations(...)` (§11)

## 9. Model identitete tagov (preživi preimenovanje in premik)

- `node_uid` dodeljen ob uvozu, **nespremenljiv**: deterministično
  `hash(provider_uid + "\x00" + original_full_path)` (full_path je enolična znotraj
  providerja). Preimenovanje/premik sta **operaciji**, ne mutaciji, zato se `node_uid`
  nikoli ne spremeni; efektivna pot se izračuna z zlaganjem operacij nad baseline.
- Ustvarjena vozlišča dobijo sintetični uid (`new:<uuid4>`), shranjen v operaciji, ne v
  baseline.
- `provider_uid = hash(site + "/" + provider_name + "/" + kind)`; `provider_name` se
  razbere iz imena datoteke (koren JSON ima prazno ime).
- Ponovni uvoz spremenjenega vira se ujema po `(provider_uid, original_full_path)` →
  isti `node_uid`, kjer sidro še obstaja; dodano/odstranjeno se zazna; odvisne
  relacije/operacije brez sidra se označijo **STALE** (poznejši mejnik).

## 10. Model relacij in dokazov (`relationships`)

Stolpci: `source_node_uid, target_node_uid, role, state, evidence_type, evidence_json,
origin, confidence, confirmed_by, confirmed_at, created_at, updated_at, source_hashes_json`.

- `role`: `RAW_TO_ORGANIZED | ORGANIZED_TO_MEMBER | MEMBER_TO_UNS_INSTANCE | GENERIC`
- `state`: `EXACT | MANUAL_CONFIRMED | MANUAL_REJECTED | UNRESOLVED | AMBIGUOUS | STALE | CONFLICT`
- `evidence_type`: `OPC_ITEM_PATH_EXACT | SOURCE_TAG_PATH_RESOLVED |
  UDT_DEFINITION_MEMBERSHIP | INSTANCE_TYPE | MANUAL`
- `origin`: `AUTO_EXACT | MANUAL | SUGGESTION`

Prihodnje hevristike pišejo **`SUGGESTION`** vrstice v ta model; nikoli ga ne obidejo in
nikoli samodejno ne odobrijo. `MANUAL_CONFIRMED` prevlada nad predlogi. Veljavnost se
vodi proti trenutnim hash-om virov.

**Meje dokaznih virov** (dokumentirano): enako **ime samo po sebi NI** exact dokaz;
deljen `opcItemPath` je legitimno ne-enolичен; `sourceTagPath` zahteva razrešitev
providerja; članstvo v UDT zahteva predhodno razrešeno dedovanje.

## 11. Model operacij delovne kopije (`operations`)

Stolpci: `seq, op_type, target_node_uid, payload_json, original_json, status, reason,
created_by, created_at, depends_on_json, conflict_info`.

Za vsako operacijo (ciljna identiteta / originalno stanje / zahtevano / validacija /
prizadeti / inverz):

| op_type | payload | validacija | prizadeti | inverz |
|---|---|---|---|---|
| `CREATE_TAG` / `CREATE_FOLDER` / `CREATE_UDT_INSTANCE` | `{parent_uid,name,tagType,props}` (target `new:uid`) | starš obstaja v sim; ime enolično med efektivnimi sorojenci | – | odstrani ustvarjeno vozlišče |
| `RENAME_TAG` | `{new_name}` | veljavni znaki; enolično ime | efektivne poti potomcev; poti-osnovane reference (→ stale) | preimenuj nazaj |
| `MOVE_TAG` | `{new_parent_uid,new_sibling_index}` | brez premika v lastnega potomca; enolично ime v cilju | potomci | premakni nazaj |
| `UPDATE_PROPERTY` | `{key/pointer,new_value}` | znana lastnost + tip | – | obnovi original |
| `UPDATE_SOURCE_PATH` | `{new_value}` za `sourceTagPath` | uravnoteženi `{}` + provider token (`udt_resolver.braces_balanced`, `provider_token`) | reference | obnovi original |
| `UPDATE_PARAMETERS` | `{params}` UDT instance | proti efektivnim parametrom tipa (`udt_resolver.effective_params`) | člani | obnovi original |
| `DELETE_TAG` | – | **modeliran, a odložen iz prve izdaje**; tombstone; opozori na potomce/reference | potomci/reference | undelete |

Vrstni red po `seq`; odvisnosti v `depends_on_json`; undo/redo = urejen dnevnik operacij
+ kazalec v `project_meta`; konflikt (npr. dva preimenovanja enega vozlišča) →
`status=CONFLICT`.

## 12. Arhitektura uporabniškega vmesnika (UI)

**PySide6 / Qt.** `QTreeView` + lasten **lazy** `QAbstractItemModel`, ki otroke pridobiva
na zahtevo (`get_children(parent_uid, limit, offset)`) in virtualizira izris; primerno za
277k vozlišč. Branja DB izven UI niti (ali dovolj hitre omejene poizvedbe). GUI je
izoliran v paketu `ui/`, tako da storitve ostanejo uvozljive brez Qt (headless testi z
`pytest-qt`). Paneli se uvajajo po mejnikih (§13, §ui-scope). Prvi explorer prikaže le
minimum, ki dokaže arhitekturo.

## 13. Podrobni urejeni mejniki (A–L)

Vsak mejnik vsebuje: **Cilj · Uporabniku viden rezultat · Zakaj zdaj · Odvisnosti ·
Ponovna uporaba · Datoteke · Shema · Storitve/API · UI · Napake · Zmogljivost · Testi ·
Kriteriji · Ne-cilji · Rollback · Meje commit-ov · Ročno preverjanje.** Za oddaljene
mejnike (I–L) je opis lažji in se pred izvedbo ponovno načrtuje.

### A. Ponovni zagon smeri in arhitektura (ta dokument)
- **Status:** zaključeno.
- **Cilj:** uskladiti roadmap, CLAUDE.md, veje, odvisnosti in sheme z editor-first smerjo.
- **Viden rezultat:** en nepротisloven roadmap; jasen naslednji mejnik (B1).
- **Zakaj zdaj:** brez usklajene smeri se implementacija razhaja.
- **Odvisnosti:** –. **Ponovna uporaba:** obstoječi roadmap in CLAUDE.md.
- **Datoteke:** `IGNITION_TAG_EDITOR_ROADMAP.md`, `CLAUDE.md` (samo Current checkpoint).
- **Ne-cilji:** koda; migracija main. **Meja commit-a:** en docs commit na
  `roadmap-editor-first`. **Ročno preverjanje:** roadmap se bere kot ena smer, z
  dosledno ciljno verzijo Ignition 8.3 in brez avtomatike pred urejevalnikom.

### B1. Model projekta + shema + življenjski cikel
- **Status:** zaključeno. Implementirano v `editor/schema.py` (v1 shema + `migrate` prek
  `PRAGMA user_version`), `editor/project.py` (`create_project`/`open_project`/`save`/
  `close`/`recover`), testi `tests/test_project.py`. Shema v1 ustvari `project_meta`,
  `sources`, `baseline_nodes`; tabeli `relationships`/`operations` prideta s svojima
  migracijama v D1/F1 (brez scaffoldinga v B1).
- **Cilj:** samostojen `project.sqlite` + migracijski tekač; create/open/save/close/recover.
- **Viden rezultat:** ustvari/odpri/shrani prazen projekt; preživi prekinjeno sejo.
- **Zakaj zdaj:** temelj za vse; brez UI. **Odvisnosti:** A.
- **Ponovna uporaba:** vzorec `schema.create_schema`; `tests/conftest.py` vzorci.
- **Datoteke:** `editor/__init__.py`, `editor/schema.py`, `editor/project.py`,
  `tests/test_project.py`.
- **Shema:** `project_meta`, `sources`, `baseline_nodes`, prazni `relationships`/
  `operations`; `schema_version`.
- **Storitve:** `create_project(path,name)`, `open_project(path)`, `save`, `close`,
  `recover(path)`.
- **UI:** –. **Napake:** poškodovan/zaklenjen projekt, nezdružljiva verzija sheme →
  jasna napaka. **Zmogljivost:** odpiranje < ~200 ms.
- **Testi:** shema/migracija naprej, create/reopen, obnovitev po prekinitvi.
- **Kriteriji:** projekt se ustvari, zapre in znova odpre brez izgube metapodatkov.
- **Ne-cilji:** uvoz tagov (B2). **Rollback:** projekt je ena datoteka; brisanje = čist
  rollback. **Meja commit-a:** en commit. **Ročno:** ustvari projekt, znova odpri.

### B2. Uvoz virov v baseline
- **Status:** zaključeno. Implementirano v `editor/import_service.py`
  (`discover_sources`, `validate_source`, `import_source`, `list_providers`,
  `compute_provider_uid`/`compute_node_uid`, `parse_provider_name`), testi
  `tests/test_import.py`, sintetični fixture `tests/fixtures/editor/*`. Ponovno
  uporabljeni `analyzer.model.TagRow`, `analyzer.build.sha256_file`, `classify_file`.
  Preverjeno na realnih izvozih: 277.607 vozlišč, 0 trkov `node_uid`, viri
  nespremenjeni. Idempotentno/zamenjava po (provider, sha256).
- **Cilj:** uvoz IO/UNS/UDT JSON v nespremenljiv baseline z identiteto in provenance.
- **Viden rezultat:** projekt vsebuje označene baseline-e več providerjev/lokacij.
- **Zakaj zdaj:** brez baseline ni kaj prikazati. **Odvisnosti:** B1.
- **Ponovna uporaba:** `build._walk`, `build.sha256_file`, `model.TagRow`,
  `model.classify_file`, `model._flatten_binding`.
- **Datoteke:** `editor/import_service.py`, `tests/test_import.py`,
  `tests/fixtures/editor/*` (sintetični mini JSON providerji).
- **Shema:** polni `baseline_nodes` (+ `node_uid`, `provider_uid`, `sibling_index`,
  `raw_json`), `sources`.
- **Storitve:** `discover_sources`, `validate_source`, `import_source(project, path,
  site)`, `list_providers`, `reimport` + stale osnova.
- **UI:** –. **Napake:** neveljaven JSON, neznan provider vzorec, podvojen uvoz.
  **Zmogljivost:** uvoz velike datoteke omejen v pomnilniku (kot Faza 0).
- **Testi:** provenance, ohranjen vrstni red, provider identiteta, nespremenljivost virov
  (sha pred==po), brezizgubnost `raw_json`.
- **Kriteriji:** baseline enolično identificiran; viri nespremenjeni.
- **Ne-cilji:** urejanje. **Rollback:** ponovni uvoz zgradi nov baseline; stari projekt
  ostane. **Meja commit-a:** en commit. **Ročno:** uvozi sintetični provider, preglej štetja.

### C1. Repozitorij lazy drevesa + podrobnosti (headless)
- **Status:** zaključeno. Implementirano v `editor/repository.py` (`get_provider_root`,
  `get_children(parent_uid, limit, offset)`, `child_count`, `get_node`, `get_parent`,
  `breadcrumbs`, `full_path`, `node_details`; `list_providers` re-export), testi
  `tests/test_repository.py`. Read-only nad `baseline_nodes`; brez migracije (indeksa
  `parent_uid`/`provider_uid` sta že v v1). Zmogljivost na realnih podatkih:
  `get_children(limit=200)` nad vozliščem s 6701 otroki ~10 ms. Iskanje (C3) in efektivni
  UDT clani/parametri (C4) tu namerno še niso vključeni.
- **Cilj:** API za lazy navigacijo in podrobnosti. **Viden rezultat:** (posredno prek UI).
- **Zakaj zdaj:** UI potrebuje hitre omejene poizvedbe. **Odvisnosti:** B2.
- **Ponovna uporaba:** `query.search`, `query.SEARCH_FIELDS`, `udt_resolver`.
- **Datoteke:** `editor/repository.py`, `tests/test_repository.py`.
- **Shema:** indeksi na `baseline_nodes(parent_uid)`, `(provider_uid)`, iskalna polja.
- **Storitve:** `list_providers`, `get_children(parent_uid,limit,offset)`, `get_parent`,
  `breadcrumbs`, `full_path`, `node_details(raw+effective)`, `child_count`.
- **UI:** –. **Napake:** neznan uid. **Zmogljivost:** `get_children` < ~150 ms.
- **Testi:** lazy paging, breadcrumbs, štetja, deterministicen vrstni red.
- **Kriteriji:** noben klic ne naloži celega drevesa. **Ne-cilji:** izris.
- **Meja commit-a:** en commit. **Ročno:** poženi poizvedbe iz Python REPL.

### C2. PySide6 lupina + lazy provider drevo
- **Status:** zaključeno. Implementirano v izoliranem paketu `ui/`: zagonska točka
  `ui/app.py`, open-project lupina in `QTreeView` v `ui/main_window.py`, paginiran
  `TreeModel(QAbstractItemModel)` v `ui/models/tree_model.py`. Odvisnosti so ločene v
  `requirements.txt` (runtime) in `requirements-dev.txt` (pytest/pytest-qt). Headless testi
  modela in okna so v `tests/test_ui_tree_model.py` ter `tests/test_ui_main_window.py`;
  celotna zbirka ima 113 zelenih testov. Preverjeno na realnem projektu z 277.607 vozlišči:
  inicializacija šestih provider korenov ~534 ms, najpočasnejši fetch prve strani 200
  otrok ~13 ms; celotno drevo se ne naloži v pomnilnik.
- **Cilj:** minimalni UI, ki dokaže arhitekturo. **Viden rezultat:** odpri projekt, razširi
  velika drevesa brez zmrznitve.
- **Zakaj zdaj:** prva povratna zanka. **Odvisnosti:** C1.
- **Ponovna uporaba:** `editor/repository.py`.
- **Datoteke:** `ui/__init__.py`, `ui/app.py`, `ui/main_window.py`,
  `ui/models/tree_model.py`, `tests/test_ui_tree_model.py`.
- **Odvisnost:** dodaj **PySide6** (runtime), **pytest-qt** (dev).
- **Storitve:** `TreeModel(QAbstractItemModel)` nad `repository`.
- **UI:** zagonski zaslon (open project) + provider drevo. **Napake:** manjkajoč projekt.
  **Zmogljivost:** prvi nivo < ~1 s; razširitev < ~150 ms.
- **Testi:** model headless (pytest-qt): rowCount/index/lazy fetch.
- **Kriteriji:** navigacija realnega 277k providerja brez nalaganja vsega. **Ne-cilji:**
  iskanje/inspektor. **Rollback:** GUI izoliran; storitve nedotaknjene. **Meja commit-a:**
  en commit. **Ročno:** odpri projekt, razširi globoko vejo.

### C3. Iskanje in filtri
- **Status:** zaključeno. `editor/repository.py` vsebuje `search_nodes` in
  `get_search_filters`: polja `fullPath`/`name`/`opcItemPath`/`sourceTagPath`/`typeId`,
  načini exact/prefix/contains, presečni filtri provider/site/tag_type, determinističen
  `limit`/`offset`, skupno štetje ter največ 500 lahkih vrstic brez `raw_json`.
  `ui/search_panel.py` doda kontrole, tabelo rezultatov in prejšnja/naslednja; glavno okno
  ga prikaže ob lazy drevesu. Schema v2 doda verzionirane iskalne indekse in testirano
  migracijo iz v1 brez spremembe baseline vrstic. Celotna zbirka: 126 zelenih testov.
  Realni projekt (277.607 vozlišč): reprezentativni count + prva stran ~0,1–0,18 s,
  možnosti filtrov ~0,11 s, odprtje okna z drevesom + search panelom ~0,11 s.
- **Cilj:** iskanje po polju + provider/site/tag_type + štetja. **Odvisnosti:** C2.
- **Ponovna uporaba:** `query.search`, `SEARCH_FIELDS`.
- **Datoteke:** `ui/search_panel.py`, razširi `editor/repository.py`, testi.
- **UI:** iskalno polje + filtri + rezultati (paginirano). **Zmogljivost:** count + prva
  stran < ~500 ms na 277k. **Testi:** filtrirano štetje, mode exact/prefix/contains.
- **Kriteriji:** iskanje vrne štetje in vzorec brez izpisa tisočev. **Meja commit-a:** en commit.

### C4. Tag inspektor + UDT kontekst  → **Explorer MVP zaključen**
- **Status:** zaključeno. `editor/udt_context.py` prilagodi projektni baseline
  obstoječemu site-aware `UdtRegistry` ter izračuna efektivne lastnosti, člane,
  parametre in dedovalno verigo brez spremembe baseline. `node_details` vrača raw in
  efektivne lastnosti ter razširjen provenance. `ui/inspector_panel.py` in
  `ui/udt_panel.py` sta read-only ter se osvežita ob izboru v lazy drevesu ali rezultatih
  iskanja. Celotna zbirka: 135 zelenih testov. Realni projekt (277.607 vozlišč):
  gradnja resolverja ~0,059 s, podrobnosti izbrane UDT instance ~0,0028 s, odprtje
  celotnega okna ~0,841 s; C4 operacije niso zapisale sprememb.
- **Cilj:** prikaz raw+efektivnih lastnosti, OPC, sourceTagPath, typeId, parametrov,
  provenance; UDT efektivni člani/parametri. **Odvisnosti:** C3.
- **Ponovna uporaba:** `udt_resolver.effective_members/params/inheritance_chain`,
  `query.get_raw`.
- **Datoteke:** `ui/inspector_panel.py`, `ui/udt_panel.py`, razširi `repository`, testi.
- **UI:** inspektor + UDT panel, sinhroniziran z izborom v drevesu. **Testi:** node_details,
  efektivna vs uvožena konfiguracija. **Kriteriji:** izbran tag pokaže vse zahtevane
  atribute + izvor. **Ne-cilji:** urejanje. **Meja commit-a:** en commit. **Ročno:**
  klikni UDT instanco, preveri efektivne člane.

### D1. Odkrivanje exact relacij
- **Status:** zaključeno. Schema v3 doda tabelo `relationships` in indekse brez
  spremembe baseline vrstic. `editor/relationships.py` deterministično in idempotentno
  odkriva štiri dovoljene avtomatske dokaze: enolični IO kandidat za `opcItemPath`,
  statično razrešen `sourceTagPath` z lokacijo/providerjem, efektivno članstvo UDT z
  dedovanjem ter `typeId` instance. Referencirani tag je pri razrešenem
  `sourceTagPath` usmerjen proti tagu z bindingom; več kandidatov se ne izbira, ampak
  zapiše kot `AMBIGUOUS`, manjkajoč ali dinamičen cilj pa kot `UNRESOLVED`. Enako ime
  ni dokaz. `query_relationships` podpira filtre, kontekst obeh vozlišč in strani do
  500 vrstic. Celotna zbirka: 148 zelenih testov. Realni projekt (277.607 vozlišč):
  65.125 relacij (57.059 exact, 8.048 unresolved, 18 ambiguous), discovery ~19–27 s,
  filtrirana stran 100 vrstic ~0,03 s; baseline digest pred/po je enak.
- **Cilj:** samo dokazljive relacije + eksplicitno UNRESOLVED. **Odvisnosti:** C4.
- **Ponovna uporaba:** `udt_resolver`, `opc_multiplicity` vzorci iz Faze 0.
- **Datoteke:** `editor/relationships.py`, `tests/test_relationships.py`.
- **Shema:** `relationships` (§10). **Storitve:** `discover_exact(project)`,
  `query_relationships`, evidenca po `evidence_type`.
- **Napake:** dvoumni kandidati → `AMBIGUOUS`, ne ugibaj. **Testi:** vsak dokazni tip +
  meje (ime samo ni dokaz; deljen opcItemPath). **Kriteriji:** nič hevristik; nerešeno je
  vidno. **Ne-cilji:** predlogi. **Meja commit-a:** en commit.

### D2. Panel verige relacij
- **Status:** zaključeno. `ui/relationship_panel.py` doda read-only tabelarni model in
  panel, ki ob izboru v drevesu ali iskanju sledi shranjenim D1 robovom ter jih uredi
  kot korake `raw IO → organized IO → UDT član → UNS instanca`. Vsaka vrstica pokaže
  stanje, smer, provider/site kontekst in vrsto dokaza; izbor vrstice pokaže celoten
  evidence/audit JSON. `UNRESOLVED` in `AMBIGUOUS` cilji so izpisani kot vidne vrzeli.
  Obhod je omejen na globino 3 in 200 relacij, omejitev pa je jasno označena. Panel ne
  zaganja počasnega discoveryja in ne piše v projekt. Celotna zbirka: 155 zelenih
  testov. Realni projekt s 65.125 relacijami: omejena 200-vrstična veriga ~0,04 s,
  nerešena vrzel ~0,004 s, odprtje celotnega okna ~1,05 s; nič zapisov panela.
- **Cilj:** prikaz `raw IO → organized IO → UDT član → UNS instanca` + dokaz + UNRESOLVED.
  **Odvisnosti:** D1. **Datoteke:** `ui/relationship_panel.py`, testi modela. **Kriteriji:**
  za izbran tag se pokaže veriga in dokazi; vrzeli so vidne. **Meja commit-a:** en commit.

### E1. Storitev ročnih relacij
- **Status:** zaključeno. `editor/relationships.py` podpira neposredno ustvarjanje ter
  potrditev, zavrnitev in logično odstranitev relacij. Vsaka ročna odločitev je ločena
  `MANUAL` vrstica z akterjem, časom, opombo in naraščajočo auditno zgodovino, zato
  avtomatski dokaz ostane nedotaknjen. Aktivna ročna odločitev ima prednost pred
  avtomatskimi relacijami in prihodnjimi sugestijami; odstranitev ohrani audit ter vrne
  prednost osnovni relaciji. Shranjeni hashi virov omogočajo preverjanje veljavnosti,
  označitev `STALE` in obnovitev prejšnjega stanja, ko se vsebina povrne. Celotna zbirka:
  163 zelenih testov.
- **Cilj:** create/confirm/reject/remove; `MANUAL_CONFIRMED` prevlada; trajno; veljavnost
  proti hash-om. **Odvisnosti:** D1. **Datoteke:** razširi `editor/relationships.py`, testi.
  **Testi:** trajnost + prednost pred (bodočimi) predlogi. **Kriteriji:** ročne relacije
  preživijo ponovno odprtje. **Meja commit-a:** en commit.

### E2. UI urejevalnik ročnih povezav
- **Status:** zaključeno. Nova stran `Ročne povezave` je sinhronizirana z izborom v
  drevesu in iskanju. Uporabnik eksplicitno poišče ter izbere drugi tag, določi smer in
  vlogo povezave, vnese auditnega uporabnika in opombo ter nato ustvari, potrdi, zavrne
  ali logično odstrani odločitev. Nerešena relacija zahteva izbranega kandidata.
  Urejevalnik po zapisu osveži tudi read-only verigo, ki zdaj pokaže veljavnost,
  efektivnost in auditna polja. Odločitve so vidne po ponovnem odprtju projekta.
  Celotna zbirka: 168 zelenih testov.
- **Cilj:** ročno poveži/razveži iz UI; ponovno odprtje ohrani. **Odvisnosti:** E1, D2.
  **Datoteke:** `ui/manual_link_editor.py`, testi. **Meja commit-a:** en commit.

### F1. Model in storitve operacij
- **Status:** zaključeno. Schema v4 doda trajni, auditirani in urejeni dnevnik
  `operations`. `editor/operations.py` validira in in-memory uporablja `CREATE_TAG`,
  `CREATE_FOLDER`, `CREATE_UDT_INSTANCE`, `RENAME_TAG`, `MOVE_TAG`,
  `UPDATE_PROPERTY`, `UPDATE_SOURCE_PATH` in `UPDATE_PARAMETERS`; za vsak izvedljiv
  tip sestavi inverz. `DELETE_TAG` se shrani kot jasno `DEFERRED` in se ne uporabi.
  Odvisnosti se topološko uredijo, nove tarče samodejno dobijo CREATE odvisnost,
  nasprotujoče spremembe istega polja so `CONFLICT`. Interaktivna validacija naloži le
  relevanten overlay; na kopiji realnega projekta z 277.607 vozlišči je trajni rename
  ~0,0027 s (prej polna materializacija ~9,6 s), baseline ostane nespremenjen. Celotna
  zbirka: 187 zelenih testov.
- **Cilj:** `CREATE_*`/`RENAME`/`MOVE`/`UPDATE_PROPERTY`/`UPDATE_SOURCE_PATH`/
  `UPDATE_PARAMETERS` (+ `DELETE` modeliran, odložen); validate/order/apply-in-sim/invert.
  **Odvisnosti:** B2 (identiteta), C1. **Ponovna uporaba:** `udt_resolver.braces_balanced`,
  `provider_token`, `effective_params`.
- **Datoteke:** `editor/operations.py`, `tests/test_operations.py`.
- **Shema:** `operations` (§11). **Testi:** validacija po tipu + round-trip inverz; brez
  mutacije baseline. **Kriteriji:** vsaka operacija ima validacijo in inverz. **Ne-cilji:**
  izvedba DELETE. **Meja commit-a:** en commit (ali dva: create/rename/move, nato update*).

### F2. Panel stage-anih sprememb + urejevalnik operacij
- **Cilj:** operacije vidno ločene od baseline; ustvarjanje operacij iz UI. **Odvisnosti:**
  F1. **Datoteke:** `ui/staged_changes_panel.py`, `ui/operation_editor.py`, testi.
  **Meja commit-a:** en commit.

### G1. Storitvi SimTree + diff
- **Cilj:** efektivno drevo (lazy) iz baseline+operacij; strukturiran diff. **Odvisnosti:**
  F1. **Datoteke:** `editor/simulation.py`, `tests/test_simulation.py`.
- **Storitve:** `sim_children(node_uid)`, `sim_details`, `diff(project)` (added/renamed/
  moved/property-changed/reference-changed/deleted). **Testi:** pravilnost sim + kategorije
  diff-a; baseline nespremenjen. **Kriteriji:** sim ne mutira baseline. **Meja commit-a:**
  en commit.

### G2. Undo/redo + trajnost
- **Cilj:** kazalec dnevnika operacij; save/reopen obnovi baseline+relacije+operacije+kazalec.
  **Odvisnosti:** G1. **Datoteke:** razširi `operations.py`, `project.py`, testi.
  **Testi:** undo/redo, zvestoba ponovnega odprtja. **Meja commit-a:** en commit.

### G3. Pogled simuliranega drevesa + diff UI + validacija  → **Editor MVP zaključen**
- **Cilj:** vizualni sim + before/after diff + validacijske ugotovitve nad sim.
  **Odvisnosti:** G2. **Ponovna uporaba:** `validate.validate` (prilagojen na sim).
  **Datoteke:** `ui/sim_tree_view.py`, `ui/diff_panel.py`, `ui/validation_panel.py`, testi.
  **Meja commit-a:** en commit.

### H1. Omejen Ignition 8.3 izvoz
- **Cilj:** izračun obsega + deterministicna serializacija + manifest. **Odvisnosti:** G1.
- **Datoteke:** `editor/export.py`, `tests/test_export.py`.
- **Storitve:** `compute_export_scope(selection)`, `serialize_ignition_json(scope)`,
  `write_package(...)`. **Napake:** UDT definicijski izvoz strožji (Overwrite lahko
  odstrani člane). **Testi:** deterministicnost + **brezizgubna no-op rekonstrukcija** na
  omejenih realnih podatkih. **Kriteriji:** izvoz je deterministicen in omejen. **Meja
  commit-a:** en commit.

### H2. Round-trip preverjanje + izvozni UI  → **Prvi celoten navpični rez**
- **Cilj:** ponovni uvoz izvoza v parser in primerjava z načrtovanim sim poddrevesom →
  `EXPORT_VERIFIED`. **Odvisnosti:** H1. **Datoteke:** razširi `export.py`,
  `ui/export_panel.py`, testi. **Testi:** round-trip vozlišče-za-vozlišče (brez naših
  metapodatkov). **Kriteriji:** izvoz round-trip-a natančno. **Meja commit-a:** en commit.

### I. Prva ročno dokončana linija
- **Cilj:** eno realno linijo pelji end-to-end skozi urejevalnik; ustvari **nezaupno,
  sintetizirano golden** vedenjsko specifikacijo kot regresijske fixture. **Odvisnosti:** H2.
  **Ne-cilji:** avtomatika. **Meja commit-a:** golden fixtures + test.

### J. Integracija referenčnih podatkov
- **Cilj:** obstoječi `analyzer/reference` priključi kot neobvezen kontekst; piše
  **`SUGGESTION`** vrstice v model relacij (nikoli samodejno odobreno). **Odvisnosti:** I.
  **Ponovna uporaba:** celoten `analyzer/reference`.

### K. Samodejno grupiranje in mapiranje
- **Cilj:** najprej deterministicna pravila (predlogi), nato **omejeno** približno ujemanje
  — šele po golden datasetu. **Odvisnosti:** J.

### L. Napredna validacija + produkcijski izvoz + pakiranje
- **Cilj:** polni/omejeni izvoz, post-import preverjanje iz Ignitiona, PyInstaller pakiranje.
  **Odvisnosti:** K.

**UI paneli po mejnikih:** zagon/open (B1/C2), import/source manager (B2), provider/site
izbirnik (C2), lazy drevo (C2), povezana IO/UNS/UDT navigacija (C2–C4), iskanje/filtri
(C3), inspektor (C4), veriga relacij (D2), urejevalnik ročnih povezav (E2), panel
stage-anih sprememb + urejevalnik operacij (F2), before/after diff + simulirano drevo +
validacija (G3), izbor obsega izvoza + rezultat/round-trip (H1/H2).

## 14. Inventar funkcij/storitev

Modul · vhod → izhod · meja trajnosti.

- **Project** (`editor/project.py`): create/open/save/close/recover · path/name → ročica
  projekta · lasti `project.sqlite`.
- **Import** (`editor/import_service.py`): discover/validate/fingerprint/import, reimport +
  stale · datoteke/site → `sources` + `baseline_nodes` · piše baseline (po uvozu
  nespremenljiv).
- **Repository** (`editor/repository.py`): list_providers/roots, get_children, get_parent,
  breadcrumbs, full_path, search + filtrirano štetje, node_details, raw/effective · uid/
  filtri → vrstice · **read-only** nad baseline+ops.
- **UDT** (ponovna uporaba `analyzer/udt_resolver.py`): efektivni člani/parametri, dedovanje
  · site/typeId → množice · read-only.
- **Relationships** (`editor/relationships.py`): discover_exact, query, confirm, reject,
  remove · uid/dokaz → `relationships` · piše relacije.
- **Operations** (`editor/operations.py`): create, validate, order, apply-in-sim, invert ·
  payload → `operations` + validacija · piše operacije.
- **Simulation** (`editor/simulation.py`): sim_children/details, diff, undo/redo ·
  baseline+ops → efektivni pogled/diff · bere baseline+ops, piše le undo kazalec.
- **Validation** (ponovna uporaba `analyzer/validate.py`, prilagojen na sim) · sim →
  ugotovitve · read-only.
- **Export** (`editor/export.py`): scope calc, Ignition 8.3 serialize, round-trip compare ·
  izbor → JSON paket + preverjanje · piše samo izhodne datoteke.

## 15. Zaporedje shem in migracij (`project.sqlite`, `schema_version` + tekač)

- **B1:** `project_meta`, `sources`, `baseline_nodes`, `schema_version` (+ prazni
  `relationships`/`operations`).
- **B2:** napolni `baseline_nodes` (+ `node_uid`, `provider_uid`, `sibling_index`,
  `raw_json`).
- **C3 / v2:** read-only iskalni indeksi nad baseline polji, `source_id` in `tag_type`;
  baseline vrstice ostanejo nespremenjene.
- **D1:** `relationships`. **F1:** `operations`. **G2:** undo kazalec v `project_meta`.

Vsaka migracija je naprej-usmerjena, verzionirana, testirana; odpiranje starejšega
projekta požene čakajoče migracije; baseline se s poznejšimi migracijami **nikoli** ne
prepiše.

## 16. Testna strategija (sintetično commit-ano; realni Calcit ignoriran)

Enotski (identiteta/pot/lastnost/pravila operacij) · shema & migracije · regresija parserja
· lazy poizvedbe · dokazi relacij · trajnost ročnih relacij · delovna kopija + inverz ·
simulacija & diff · deterministicna serializacija · **Ignition 8.3 round-trip** · UI-model
(`pytest-qt`, headless) · omejeni **zmogljivostni** testi z velikim sintetičnim drevesom ·
ročne provere na ignoriranih realnih vhodih · **vseh 64 obstoječih testov ostane zelenih**.
Ponovno uporabi vzorce iz `tests/conftest.py` in `tests/fixtures/`. Sintetični fixture se
commit-ajo; realni Calcit podatki ostanejo ignorirani.

## 17. Cilji zmogljivosti

- Odpiranje projekta in izris prvega nivoja drevesa < ~1 s.
- Razširitev poljubnega vozlišča < ~150 ms prek paginiranega `get_children`.
- Iskanje vrne štetje + prvo stran < ~500 ms na 277k vozliščih.
- UI nikoli ne naloži celotnega drevesa.
- Izvoz omejenega poddrevesa deterministicen in omejen.

## 18. Varnost in ravnanje z zaupnimi podatki

Brez pisanja v `data/raw`; brez neposrednega pisanja v Gateway/PLC; resnični Calcit izvozi
in iz njih izpeljani zaupni fixture ostanejo git-ignorirani; projektne datoteke iz realnih
podatkov so zaupne (se ne commit-ajo); commit-ajo se samo sintetični fixture.

## 19. Tveganja in blaženja

- **Brezizgubnost rekonstrukcije nepreverjena** → round-trip test na omejenih realnih
  podatkih pred zanašanjem na izvoz (H1).
- **UI zatikanje pri 277k** → obvezen lazy model + paging + zmogljivostni testi.
- **Qt odvisnost/pakiranje** → GUI izoliran v `ui/`, storitve headless, pakiranje odloženo.
- **Stabilna identiteta pri ponovnem uvozu** → deterministicen `node_uid`, operacije ga ne
  spreminjajo, STALE označevanje.
- **Zdrs nazaj v hevristike** → avtomatika je za navpičnim rezom in golden datasetom.

## 20. Definicija MVP

- **Explorer MVP** (konec C): odpri projekt · uvozi/odpri indeksirane vire · navigiraj
  velika provider drevesa · išči · preglej tage + UDT kontekst.
- **Editor MVP** (konec G): exact + ročne relacije · stage-ane ročne spremembe · simulirano
  drevo · diff · undo/redo · save/reopen.

## 21. Definicija prvega celotnega navpičnega reza

Konec **H**: Editor MVP + omejen Ignition 8.3 JSON izvoz + deterministicno round-trip
preverjanje + en majhen, ročno preverjen realni primer.

## 22. Odložena funkcionalnost

Izvedba `DELETE_TAG`; referenčni predlogi (J); grupiranje/fuzzy ujemanje (K); polni
produkcijski izvoz + post-import preverjanje iz Ignitiona + pakiranje (L); večuporabniško/
cloud.

## 23. Merljivi kriteriji sprejemljivosti

- Explorer MVP navigira realne 277k-node providerje znotraj ciljev zmogljivosti.
- Baseline dokazljivo nespremenljiv (hash pred==po) in ločen od operacij.
- Exact relacije prikažejo dokaz in nerešene vrzeli.
- Ročne povezave in operacije preživijo ponovno odprtje.
- Sim + diff nikoli ne mutirata baseline.
- Omejen izvoz je deterministicen in round-trip-a vozlišče-za-vozlišče.
- Vseh 64 obstoječih + novi testi so zeleni; čist klon požene testno zbirko s commit-animi
  sintetičnimi fixturi.

## 24. Takojšnji naslednji implementacijski mejnik

**F2 – Panel stage-anih sprememb in urejevalnik operacij.** Dodaj
`ui/staged_changes_panel.py` ter `ui/operation_editor.py`. Uporabnik mora iz izbranega
baseline vozlišča ustvariti validirano F1 operacijo, pregledati njen payload/original,
videti `VALID`/`CONFLICT`/`DEFERRED` stanje in urediti dovoljeni vrstni red. Baseline in
simulirani pogled morata ostati jasno ločena. (B1, B2, C1–C4, D1–D2, E1–E2 in F1 so
zaključeni.)

## 25. Kontrolni seznam po mejnikih za Claude Code

Za vsak mejnik: (1) Plan Mode – preglej repo + relevantne teste; (2) potrdi obseg = točno
en mejnik; (3) implementiraj s ponovno uporabo obstoječih komponent (§14); (4) dodaj/posodobi
teste; (5) poženi fokusirane in nato celotne relevantne teste; (6) preglej diff; (7) commit
po eni koherentni meji (trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`);
(8) push trenutne ne-main veje; (9) poročaj hash, vejo, teste, naslednji mejnik; (10) ne
začenjaj naslednjega mejnika brez ločene instrukcije. Nikoli force-push; nikoli commit
realnih Calcit podatkov.

## 26. Referenčna dokumentacija

- [Ignition 8.3 – Exporting and Importing Tags](https://www.docs.inductiveautomation.com/docs/8.3/platform/tags/exporting-and-importing-tags)
- [Ignition 8.3 – system.tag scripting](https://www.docs.inductiveautomation.com/docs/8.3/appendix/scripting-functions/system-tag)
