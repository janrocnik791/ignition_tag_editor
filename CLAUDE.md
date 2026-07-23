Ignition Tag Editor

Project purpose

Build a Calcit-first desktop tool for analysing, mapping, restructuring, validating, and later exporting Ignition tag structures.

Target environment:

Ignition 8.3

Calcit Stahovica and Gospić

IO, UNS, and UDT JSON exports

Python 3.13

The application works offline from exported files. It must not write directly to an active Ignition Gateway or PLC.

Sources of truth

Use this order when working on a task:

The user's current instruction and the plan approved in the current Claude Code session.

The actual repository, tests, schemas, and input data.

IGNITION_TAG_EDITOR_ROADMAP.md for product direction, phase boundaries, and non-goals.

Existing generated reports only as evidence from a particular run, not as permanent truth.

Inspect the repository before proposing changes. Do not assume that a module, field, command, or path exists because it appeared in an earlier discussion.

Read only the parts of the roadmap and codebase relevant to the current task. Do not load large exports into the conversation when they can be inspected programmatically.

Current checkpoint

Phase 0, the read-only analytical foundation, is complete.

The active phase is Phase 1: reference data and expected-state model. The immediate objective is to import the reference tables, normalize them into one internal model, validate their rows, retain source provenance, and support site-specific differences.

Do not start the mapping engine, UI, mutation model, or export system unless the current task explicitly moves into that phase.

When a phase is accepted as complete, update this section and the roadmap in the same change.

Claude Code workflow

Plan Mode

Plan Mode is the default starting point for a new implementation task.

Make no code or data changes.

Inspect the current repository, relevant tests, schemas, fixtures, and a bounded sample of real data.

Identify existing components that can be reused.

Resolve important unknowns from evidence instead of guessing.

Produce one concrete implementation plan for the requested scope.

Include affected components, data-flow changes, tests, risks, migrations if needed, and measurable acceptance criteria.

Separate facts confirmed in the repository from assumptions that still require a decision.

Stop after the plan and wait for approval or the switch to Auto Mode.

Auto Mode

After the plan is approved:

Implement the approved scope only.

Work in small, reviewable checkpoints.

Preserve existing behaviour unless the approved plan intentionally changes it.

Add or update tests with each behavioural change.

Run focused tests first and the full relevant suite before completion.

Avoid unrelated refactors and future-phase scaffolding.

Report changed files, implemented behaviour, test results, unresolved issues, and the next roadmap step concisely.

Do not repeat the full investigation or dump large result tables in the final response. Write detailed machine-readable findings to generated reports and summarize only decision-relevant results.

Git workflow

After an approved implementation is complete, for each coherent checkpoint:

Inspect the diff before committing.

Run the focused tests for the change and then the full relevant suite; both must pass.

Create one meaningful commit per completed coherent checkpoint. End each commit message with the trailer:
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

Push the current branch to origin.

Report the commit hash, branch, and test results.

Never force-push. Never commit real Calcit data, credentials, generated databases, generated reports, or unrelated changes. Real exports under data/raw, data/mappings, and everything under data/generated stay ignored; only non-confidential test fixtures under data/fixtures and tests/fixtures are tracked.

Data and safety invariants

Treat original inputs as immutable. Never overwrite files in data/raw or their equivalent import location.

Keep the original configuration object available alongside any normalized representation.

Preserve unknown Ignition properties so future lossless export remains possible.

Store planned changes separately from imported nodes. Analysis must not mutate the baseline.

Write generated databases, reports, fixtures derived from real data, and future exports only to their designated generated or test locations.

Never silently delete, rename, move, or overwrite a tag.

Never auto-approve an ambiguous inferred relationship.

Keep site and provider context in every identity and lookup where collisions are possible.

Prefer deterministic, explainable rules over opaque similarity guesses.

Ignition domain rules

Resolve UDT definitions per site/provider. The same typeId can validly have different definitions in Stahovica and Gospić.

UDT inheritance and instance overrides must be resolved before comparing an instance with its effective definition.

{InstanceName} is an Ignition-provided parameter and is not unresolved merely because it is absent from user parameters.

A nested UdtInstance may validly have an empty typeId when its type is defined by the parent UDT definition.

Multiple tags sharing an opcItemPath are not automatically an error.

References to known external providers are informational unless a task-specific rule says otherwise.

Do not expand inherited UDT members into local instance overrides.

A relationship must retain its evidence, source, and one of the defined states such as EXACT, INFERRED, AMBIGUOUS, MISSING, CONFLICT, EXTERNAL, or NOT_APPLICABLE.

Manual confirmations outrank later heuristic suggestions but must remain auditable.

Efficient work on large datasets

Analyse large JSON, CSV, and SQLite data with scripts, queries, indexes, and bounded samples.

Do not print complete exports, large node collections, or full report files into the model context.

Reuse existing indexes and generated analysis when their input hashes still match.

Use small synthetic fixtures for unit tests and bounded real fixtures for regression tests.

Run a full-dataset validation only when it is required by the acceptance criteria or as the final integration check.

Summarize counts and representative examples; keep complete findings in report files.

Quality gate

A task is complete only when:

its acceptance criteria are demonstrably satisfied;

new behaviour is covered by tests;

existing relevant tests still pass;

output is deterministic;

source provenance and provider/site context are retained;

no original data was modified;

documentation is updated when a contract, schema, phase, or product decision changed.

Do not mark a whole roadmap phase complete merely because one implementation task within it passes.