# MP-FS+ vNext — Stage 2 A–C Method Revision Specification

## Scope

This checkpoint implements only the first three Stage-2 interventions while preserving the frozen MP-FS+ behavior when all flags are disabled. It does not implement parser-v2, typed normalization-v2, reference repair, targeted semantic repair, causal replay, or any new LLM run.

## Baseline contract

`configs/stage2/original.json` inherits the frozen current MP-FS+ configuration at `configs/final/mp_fs_plus.json` and sets all Stage-2 intervention flags to `false`. With these flags disabled, the A–C code paths do not rewrite conflict semantics, update columns, or source-field provenance.

## A — Typed control/instruction semantics

Feature flag: `control_field_roles`.

Source fields are assigned one of five roles:

- `PAYLOAD_VALUE`
- `OPERATION_CONTROL`
- `CONFLICT_CONTROL`
- `UPDATE_CONTROL`
- `METADATA`

A non-payload source field is not silently ignored. It can be recorded as `consumed_control` only when it is consumed by a typed instruction namespace (`consumed_by`). Operation fields additionally require a recognized write operation. Non-operation control/metadata fields require an instruction context in the same source row. This prevents a payload field merely named `action` or `table` from being suppressed by name alone.

Verifier acceptance for `consumed_control` requires both `role` and `consumed_by`; otherwise `UNRESOLVED_SOURCE_FIELD` remains fail-closed.

## B — Explicit conflict-semantic preservation

Feature flag: `explicit_conflict_preservation`.

For semi-structured requests, exact control metadata is interpreted in reference-ID space. For free text, only explicit lexical conflict constructions are recognized. The intervention never consults gold plans, sample IDs, or post-states.

Supported existing write-semantics representation:

- `plain_insert` → conflict action ERROR
- `insert_ignore` → `DO NOTHING`
- `upsert_update` → `DO UPDATE`

When explicit request semantics contradict or are missing from the generated plan, the typed semantics are restored deterministically and the diagnostic `EXPLICIT_CONFLICT_SEMANTICS_DROPPED` is emitted as a warning with `deterministically_restored=true`. If an explicitly named conflict target cannot be resolved exactly to one enumerated unique constraint, the same diagnostic is emitted as an error and the pipeline remains fail-closed at `semantic_preservation`.

No verifier threshold or safety rule is relaxed.

## C — Explicit update-column preservation

Feature flag: `update_column_consistency`.

For explicit upsert update sets, the request defines a closed update-column set. Exact column-name resolution is used; no fuzzy matching is performed.

The intervention records:

- requested update-column IDs;
- excluded update-column IDs;
- planned update-column IDs before preservation;
- materialized update-column IDs;
- compiled update-column names in the compiled statement semantic trace.

If the plan omits requested columns or adds columns outside the explicit closed set, the exact requested set is deterministically restored and `REQUIRED_UPDATE_COLUMNS_DROPPED` is logged with missing/extras details. Explicitly excluded relationship/update columns are removed. If an explicit column name cannot be resolved exactly, `REQUIRED_UPDATE_COLUMNS_UNRESOLVED` is an error and compilation is not reached.

## Ablation configurations

- `configs/stage2/original.json`: A=off, B=off, C=off
- `configs/stage2/v1_control.json`: A=on, B=off, C=off
- `configs/stage2/v2_conflict.json`: A=on, B=on, C=off
- `configs/stage2/v3_update.json`: A=on, B=on, C=on

The cumulative configurations are intentional for the planned V0→V1→V2→V3 ablation. The flags themselves remain independently configurable.

## Safety invariants

1. No fuzzy source/reference correction.
2. No gold/post-state information in interventions.
3. Unresolvable explicit conflict targets remain errors.
4. Unresolvable explicit update columns remain errors.
5. `consumed_control` must include typed provenance.
6. Existing verifier remains fail-closed.
7. All flags disabled preserves baseline semantic behavior and baseline compiled serialization.
