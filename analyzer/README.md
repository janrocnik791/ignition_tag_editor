# Ignition Tag Analizator (read-only)

Indeksira vse Ignition izvoze iz `data/raw` v SQLite (`data/generated/tag_index.sqlite`).
**Nikoli ne spreminja `data/raw`** – datoteke odpira le za branje, indeks piše samo v
`data/generated`. Gradnjo z DB potjo pod `data/raw` zavrne.

Brez zunanjih odvisnosti (samo standardna knjižnica: `json`, `sqlite3`).
Za teste je potreben `pytest`.

## Uporaba

```bash
python -m analyzer build
python -m analyzer search --field opcItemPath --value DB2318 --mode contains
python -m analyzer search --field typeId --value Meritev --mode exact
python -m analyzer stats
python -m analyzer raw --id 42
python -m analyzer validate
python -m analyzer validate --severity error
python -m analyzer validate --code EMPTY_TYPE_ID
python -m analyzer inspect-udt --type-id "Siemens/Meritev_alarm_SP"
```

Iskalna polja: `fullPath`, `name`, `opcItemPath`, `sourceTagPath`, `typeId`.
Načini: `exact`, `prefix`, `contains`. Iskanje izpiše skupno število zadetkov + vzorec.

## Validator (read-only)

`validate` razreši UDT definicije, dedovanje (`typeId` na `UdtType`), parametre in
instance override, nato razvrsti ugotovitve:

- **ERROR:** `INVALID_JSON`, `UNKNOWN_UDT_TYPE`, `DUPLICATE_UDT_DEFINITION`,
  `MISSING_PARENT_UDT`, `UDT_INHERITANCE_CYCLE`, `INVALID_PATH_TEMPLATE`
- **WARNING:** `EMPTY_TYPE_ID`, `UNRESOLVED_PARAMETER`,
  `UNRESOLVED_INTERNAL_REFERENCE`, `INSTANCE_MEMBER_NOT_IN_DEFINITION`,
  `TYPE_ID_TAGTYPE_MISMATCH`
- **INFO:** `INSTANCE_OVERRIDE_SHAPE`, `SHARED_OPC_ITEM_PATH`,
  `EXTERNAL_PROVIDER_REFERENCE`, `OPTIONAL_MEMBER_ABSENT`

Semantika: različne serializirane oblike instanc istega `typeId` **niso napaka**
(override / različne definicije med providerji). Prazen `typeId` na gnezdeni
instanci **ni napaka**. Odsotnost člana je največ INFO, dokler pravila niso
zapisana v `rules/member_requirements.yaml` (validator ne ugiba required članov).
Vgrajeni Ignition parametri (`InstanceName`, `ParentInstanceName`, …) so vedno
veljavni. Zunanji (neuvožen) provider je INFO, ne poškodovana referenca.

Poročila: `data/generated/analysis/validation_summary.md`, `validation_issues.csv`,
`validation_issues.json`.

## Podatkovni model

- `files` – ena vrstica na izvorno datoteko (pot, site, kind, sha256, node_count).
- `tags` – eno vozlišče na vrstico; drevo prek `parent_id`; `full_path`, `name`,
  `tag_type`, `data_type`, `value_source`, `type_id`, `opc_item_path`, `opc_server`,
  `source_tag_path` (sploščen `binding`), `documentation`, `member_signature` in
  `raw_properties` (celoten originalni objekt vozlišča **brez** otrok).
- Statistike (materializirane): `stat_tagtype`, `stat_datatype`, `udt_structures`
  (nekonsistentne instance = več signatur na `type_id`), `opc_multiplicity`
  (en `opcItemPath` na več tagov).

## Testi

```bash
python -m pytest tests -q
```
