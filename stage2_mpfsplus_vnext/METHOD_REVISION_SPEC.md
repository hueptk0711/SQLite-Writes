# MP-FS+ vNext — Stage 2 A–C Patch 2 Method Revision Specification

## Scope

Patch 2 fixes the reviewer-blocking integration, ablation-isolation, and safety issues in checkpoint A–C. It still does **not** implement D–G, causal replay, or any new LLM inference.

## Configuration / dispatch contract

All Stage-2 configs inherit **directly** from `configs/final/mp_fs_plus.json` because the current loader resolves one base level. All configs retain:

```text
method_id = MP-FS+
```

so they use the existing MP-FS+ mapping dispatch and preflight path. Ablations are identified separately by `method_variant` and `method_version`:

- original → `stage2-original`
- V1 → `vnext-v1-control`
- V2 → `vnext-v2-conflict`
- V3 → `vnext-v3-update`

The only intended config differences from the frozen MP-FS+ config are variant/version metadata and `stage2_interventions`.

## A — isolated typed operation-control semantics

Feature flag: `control_field_roles`.

Patch 2 narrows A deliberately. V1 does **not** consume `conflict_target`, `update_columns`, `table`, `policy`, or generic metadata merely because an operation field is present.

A consumes only high-confidence operation-control source references whose **own values** parse into a supported typed write operation. Generic payload-like names such as `action` are not treated as operation controls.

Successful semantic consumption is represented as an exact provenance record:

```json
{
  "source_collection": "...",
  "source_row_index": 0,
  "source_field": "operation",
  "source_field_ref": "...",
  "role": "OPERATION_CONTROL",
  "consumed_by": "instruction_semantics.operation",
  "resolved_value": "upsert_update"
}
```

Materialization accepts `consumed_control` only when the exact source reference appears in `consumed_control_refs`. Field-name heuristics alone cannot suppress provenance errors.

## B — group-local conflict-semantic preservation

Feature flag: `explicit_conflict_preservation`.

For semi-structured input, B consumes conflict action/target controls only after the semantic action or target resolves successfully.

For free text, B is **group/table scoped**. In multi-table requests the method first finds a reliable table-local segment, then extracts conflict semantics for that group only. If group scoping is ambiguous, B does not deterministically rewrite the group.

Explicit conflict targets still require exact resolution to one enumerated unique constraint. No fuzzy matching is used. Unresolvable explicit targets remain fail-closed errors.

Semi-structured deterministic-restoration warnings are propagated into the pipeline warning stream for later causal-replay accounting.

## C — update-column preservation with fail-closed parsing

Feature flag: `update_column_consistency`.

C parses **requested names first**, then exact-resolves them to enumerated columns. Unknown names are retained as unresolved evidence and emit:

```text
REQUIRED_UPDATE_COLUMNS_UNRESOLVED
```

For SQL-like `DO UPDATE SET`, only assignment **LHS** names are treated as updated columns. Column mentions on RHS/conditions do not expand the update set.

If the request both requires and excludes the same column, C emits:

```text
CONTRADICTORY_UPDATE_CONTROL
```

as an error. It does not silently choose one policy.

B and C own their own provenance: conflict controls are consumed only after B resolves them; update controls are consumed only after C resolves them without unresolved or contradictory controls.

## Ablation configurations

- V0 `original.json`: A=off, B=off, C=off
- V1 `v1_control.json`: A=on, B=off, C=off
- V2 `v2_conflict.json`: A=on, B=on, C=off
- V3 `v3_update.json`: A=on, B=on, C=on

This makes V0→V1 measure operation-control provenance, V1→V2 measure conflict-semantic preservation, and V2→V3 measure update-column preservation.

## Safety invariants

1. No fuzzy reference or column correction.
2. No gold/post-state information in interventions.
3. V1 does not consume B/C controls.
4. Generic payload-like `action` remains payload.
5. Multi-group free-text rewriting requires a reliable group-local scope.
6. Unknown explicit update columns fail closed.
7. Contradictory update controls fail closed.
8. `consumed_control` requires an exact source reference, role, and `consumed_by`.
9. Existing verifier/preflight behavior remains fail-closed.
10. Baseline compiled serialization still omits Stage-2 trace when no trace exists.
