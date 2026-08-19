# MP-FS+ vNext — Stage 2 A–C Patch 3 Method Revision Specification

## Scope

Patch 3 hardens the already-isolated A–C interventions. No new intervention family is introduced.

## Identifier-resolution contract

Control-field aliases may use loose normalization so spellings such as `conflict-target` and `ConflictTarget` can identify the same control role. Database identifiers use a different key:

```text
strip SQL identifier quotes
→ casefold
→ preserve underscore and other identifier characters
```

Therefore `USER_ID == user_id`, while `user_id != userid`. Resolution stores all candidates for an exact identifier key; more than one candidate produces `AMBIGUOUS_IDENTIFIER` and fails closed. The same exact boundary is used by conflict-target lookup, update-column lookup, requested/excluded intersection, and unique-key matching.

## A — typed operation control

A consumes only fields classified as `OPERATION_CONTROL` whose own value exactly matches an approved typed alias. Substring inference is prohibited. A value such as `skip_validation` is unresolved, not `insert_ignore`.

## B — conflict semantics

Conflict controls are split into:

```text
CONFLICT_ACTION_CONTROL
CONFLICT_TARGET_CONTROL
```

Only action controls may produce an operation/action signal. Target/key values can only participate in exact conflict-target resolution.

For free text, quoted payload literals are masked before deterministic semantic detection. Restoration requires high-confidence instruction syntax (SQL-like conflict clauses, typed operation assignment, or explicit conditional conflict/duplicate wording with a nearby action). Bare tokens such as a payload value `"upsert"` or `"do nothing"` are not signals.

## C — update columns

Patch-2 closed-set, SET-LHS, unknown-name, and contradiction rules remain. All column comparisons now use the exact DB identifier boundary, so `user_id` and `userid` cannot collide or create false contradictions.

## V0 compatibility

With all Stage-2 flags off, materialization does not add Stage-2-only role metadata to unresolved-field records. A frozen fixture compares the complete materialized structure.

## Experiment identity

`method_id` remains the dispatch family. `method_variant` and `method_version` identify the ablation and are persisted in:

- run lock;
- manifest;
- processed sample-level artifact rows;
- `summary_metadata.json`;
- final consumed marker metadata.

Historical configs without variant/version do not gain these fields in the run lock or sample-level artifacts.

## Safety invariants

1. no fuzzy DB identifier correction;
2. no silent identifier collision;
3. conflict targets cannot define conflict actions;
4. structured operations use exact alias sets;
5. quoted/bare payload lexical values cannot trigger free-text conflict restoration;
6. unresolved or ambiguous explicit identifiers fail closed;
7. V0 retains frozen materialization artifact semantics;
8. verifier/preflight behavior remains fail closed.
