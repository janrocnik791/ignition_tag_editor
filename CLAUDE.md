# Ignition Tag Editor — Claude Code operating manual

This file is the permanent operating manual for every Claude Code session in this
repository. Read it in full at the start of a task. It defines the rules and working
protocol; it does not repeat the product design, which lives in the roadmap.

## 1. Project purpose and authoritative documents

Build a Calcit-first, offline desktop tool for analysing, mapping, restructuring,
validating, and later exporting Ignition tag structures. The product direction is
**editor-first**: a usable visual editor with exact relationships, manual corrections,
a safe working-copy/operation model, and scoped export come before reference tables,
grouping rules, and fuzzy matching.

Authority, in order:

1. **`main`** is the authoritative stable baseline. Start every task from an
   up-to-date `main`.
2. **`CLAUDE.md`** (this file) defines permanent repository rules and the working
   protocol.
3. **`IGNITION_TAG_EDITOR_ROADMAP.md`** is the authoritative product architecture,
   implementation sequence, checkpoint scope, and acceptance criteria. Do **not**
   duplicate the roadmap here; reference it by section/checkpoint.

For an individual task, resolve questions in this order: the current user instruction
and the plan approved in the session → the actual repository, tests, schemas, and input
data → the roadmap → existing generated reports (only as evidence from a particular run,
never as permanent truth).

Inspect the repository before proposing changes. Do not assume a module, field, command,
or path exists because it appeared in earlier discussion. Read only the parts of the
roadmap and codebase relevant to the current task.

## 2. Target environment and safety boundaries

- Ignition **8.3**; Python **3.13**; Calcit **Stahovica** and **Gospić**; input is IO,
  UNS, and UDT **JSON exports**.
- The application is an **offline** editor that works from exported files. It must
  **never** write directly to an active Ignition Gateway or PLC.
- Imported inputs are immutable. Never overwrite files in `data/raw`, `data/mappings`,
  or any equivalent import location.

## 3. Repository and architecture invariants

- **Layer separation.** Imported baseline, manual relationships, staged operations,
  simulated state, and export are separate layers. Editing never mutates baseline rows
  directly; planned changes are stored as operations, not applied to imported nodes.
- **Losslessness.** Preserve the original tag object and all unknown Ignition properties
  so future lossless export remains possible. Keep the original configuration object
  available alongside any normalized representation.
- **Identity.** A stable internal node identity survives rename and move (those are
  operations, not baseline mutations). Every identity and lookup carries enough
  site/provider context to prevent collisions.
- **Relationships.** A relationship must retain its evidence, source, state, and audit
  data. The valid states and evidence types are defined authoritatively in the roadmap
  relationship/evidence model (§10) — do not hard-code a competing list here. Manual
  confirmation outranks later heuristic suggestions but must remain auditable. An
  unresolved relationship is valid product state. No fuzzy match may silently become an
  approved relationship, and no ambiguous inferred relationship is auto-approved.
- **Determinism.** Prefer deterministic, explainable rules over opaque similarity
  guesses; program output must be deterministic. Never silently delete, rename, move, or
  overwrite a tag.

**Ignition domain rules** (stable facts about the data):

- Resolve UDT definitions per site/provider; the same `typeId` can validly differ
  between Stahovica and Gospić.
- Resolve UDT inheritance and instance overrides before comparing an instance with its
  effective definition; do not expand inherited members into local instance overrides.
- `{InstanceName}` is an Ignition-provided parameter and is not unresolved merely because
  it is absent from user parameters.
- A nested `UdtInstance` may validly have an empty `typeId` (the parent definition
  supplies the type).
- Multiple tags sharing an `opcItemPath` are not automatically an error.
- References to known external providers are informational unless a task-specific rule
  says otherwise.

## 4. Roadmap execution protocol

Work **checkpoint by checkpoint**; the roadmap (checkpoints A–L) defines scope. Plan Mode
is the default entry point for an implementation task: make no changes, inspect the repo
and relevant tests/schemas/fixtures (and a bounded sample of real data), reuse existing
components, resolve unknowns from evidence, and produce one concrete plan for exactly the
active checkpoint, separating confirmed facts from assumptions.

For each implementation request:

1. Read this file and the roadmap sections relevant to the active checkpoint.
2. Inspect the actual repository state rather than relying only on checkpoint status text.
3. Identify the first incomplete checkpoint and verify its prerequisites.
4. Prepare a concise internal implementation plan.
5. Implement exactly that checkpoint; work autonomously on ordinary technical decisions
   already constrained by the roadmap.
6. Do not expand into later checkpoints because related work would be convenient.
7. Run focused tests and then the complete relevant test suite.
8. Inspect the final diff for correctness, scope creep, unrelated changes, generated
   files, secrets, and confidential data.
9. Update the roadmap checkpoint status and the mutable **Current checkpoint** section
   (§9) below.
10. Create meaningful commits for coherent completed work and push the working branch
    (see §7).
11. Report: completed checkpoint; delivered user-visible and architectural result; key
    decisions or roadmap deviations; changed files; test commands and results; branch and
    commit hash; a short manual verification procedure; and the exact next checkpoint.
12. Stop and wait for explicit user approval before beginning the next checkpoint.

The Slovenian instruction **“Potrjujem, nadaljuj z naslednjim checkpointom.”** authorizes
implementing exactly **one** next incomplete checkpoint. It never authorizes implementing
several checkpoints in sequence.

**Roadmap integrity.** The editor-first order is authoritative: manual inspection, exact
relationships, manual corrections, working-copy operations, simulation, diff, and safe
export precede fuzzy mapping and predictive automation. Do not silently reorder, merge,
skip, or broaden checkpoints. Small implementation-driven clarifications may be documented
in the roadmap. Any material architectural or product-direction change requires user
approval. Keep historically completed work (Phase 0 and the reference importer) accurately
represented.

## 5. Decision and approval boundaries

Do not ask for confirmation about ordinary implementation choices already determined by
the roadmap; decide and proceed. Stop and ask the user only when:

- repository evidence materially contradicts the roadmap;
- a decision would change the architecture, data model, product scope, MVP, or export
  safety;
- the work required belongs to a later checkpoint;
- required information, access, or source material is missing;
- delivering it would break existing accepted behaviour;
- tests expose a fundamental defect in an already accepted checkpoint;
- a destructive or history-rewriting Git operation appears necessary.

When stopping, explain the evidence, recommend one default, and describe its consequences.

## 6. Testing and acceptance requirements

- Add or update tests with every behavioural change. Run the focused tests first, then the
  full relevant suite; both must pass. All existing tests must keep passing.
- A checkpoint is complete only when its roadmap acceptance criteria and its tests
  demonstrably pass, output is deterministic, source provenance and provider/site context
  are retained, and no original data was modified. Do **not** silently weaken tests or
  roadmap acceptance criteria to finish a checkpoint. Do not mark a whole roadmap phase
  complete because one task within it passes.
- Update documentation when a contract, schema, checkpoint status, or product decision
  changes.
- **Large data.** Analyse large JSON/CSV/SQLite with scripts, queries, indexes, and
  bounded samples. Do not print complete exports, large node collections, or full report
  files into the model context; summarize counts and representative examples and keep
  complete findings in report files. Use small synthetic fixtures for unit tests and
  bounded fixtures for regression tests. Run full-dataset validation only when an
  acceptance criterion requires it or as a final integration check. Reuse existing indexes
  and generated analysis when their input hashes still match.

## 7. Git workflow

- Begin from an up-to-date authoritative `main`. Inspect the branch and working-tree state
  before editing, and preserve unrelated user changes.
- Implement a checkpoint on a focused branch; keep `main` stable. Never force-push and
  never rewrite shared history — if a destructive or history-rewriting operation seems
  necessary, stop and ask (§5).
- Create one meaningful commit per coherent completed unit of work. End each commit
  message with the trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Push the current working branch to origin. Report the commit hash, branch, and test
  results.
- Do not commit unrelated changes, and do not mark a checkpoint complete until its
  acceptance criteria and tests pass.

## 8. Confidential data and repository hygiene

Never commit:

- real Calcit JSON or CSV exports;
- confidential rows derived from real Calcit data;
- credentials or secrets;
- generated SQLite project/index databases;
- generated reports or application exports;
- temporary files, caches, build output, or unrelated workspace changes.

Ignored locations: `data/raw`, `data/mappings`, and everything under `data/generated`.
Tracked test data: only **synthetic, non-confidential** fixtures under `data/fixtures` and
`tests/fixtures`. Committed fixtures must be synthetic and contain only the minimum
structure needed to reproduce behaviour. Write generated databases, reports, and exports
only to their designated generated locations.

## 9. Current checkpoint

Mutable status only. Durable history lives in Git and the roadmap — do not turn this into
a changelog.

- **Last completed:** Checkpoint C3 — read-only paginated search in
  `editor/repository.py` (`search_nodes`, `get_search_filters`) plus `ui/search_panel.py`
  with field/mode/provider/site/tag-type controls, total counts, bounded result pages, and
  previous/next navigation. Schema v2 adds search indexes; v1 projects migrate forward
  without rewriting baseline rows. Real-data check over 277,607 nodes: representative
  count + first-page queries ~0.1–0.18 s; full window open with tree + search ~0.11 s.
  Also done: A, B1, B2, C1, C2.
- **Active / next:** Checkpoint C4 — tag inspector and effective UDT context synchronized
  with the selected tree/search node.
- **Prerequisite state:** `main` is the authoritative baseline; the `editor/` package
  provides project lifecycle, baseline import, and the read-only repository (schema v2:
  `project_meta`, `sources`, `baseline_nodes` + C3 search indexes); the test suite passes
  (126 tests); the GUI remains read-only; no UDT effective resolution (C4),
  `relationships`, or `operations` yet.
- **Branch:** C3 implemented on `checkpoint-c3` from merged C2 baseline `origin/main`.
- **Blocker:** none.
