# Stage 2-F Patch 3 Checkpoint Report

## Objective

Close the remaining F4 blocker without changing Stage2-F architecture: source-field
repair must not create two raw references that resolve to the same semantic source-field
identity under the frozen resolver.

## Git base

```text
Stage2-F Patch 2
5c48c6b692b4158c48b372f2afc9182f6a6425de
```

Frozen parent remains `Stage2-E-FINAL`
(`7b9c4ef616fc2414fccba2cbe6b22016a3ed39b4`).

## Patch-3 changes

1. Adds resolver-equivalent source-field identity resolution: field ID aliases resolve
   to their field name; valid field names retain the same identity.
2. `UNKNOWN_SOURCE_FIELD_ID` repair checks semantic identity across all other keys in
   the same `field_mapping`, in addition to Patch-2 raw-key collision checks.
3. Semantic alias collision fails closed before mutation and records
   `replacement_semantic_slot_collision` with `repair_applied=false`.
4. Reuses Patch-2 atomic batch rollback if alias collision appears after an earlier safe
   repair.
5. Adds direct alias-collision and batch-rollback adversarial tests.
6. Adds CPU-smoke coverage for the semantic-alias collision boundary.

## Unchanged

- V5 -> V6 isolation and prompt identity;
- F eligibility and reference-kind whitelist;
- no-fuzzy and singleton policies;
- evidence/conflict/update-column protection;
- missing-slot fail-closed behavior;
- one-retry boundary;
- Patch-2 raw-key collision guards and atomic rollback;
- Stage-1 classification 13 / 10 / 12;
- repair-rule accounting 70 exact-name / 0 singleton.

## Isolated Patch-3 validation

Patch-3 exact artifact validation must show:

- source-field name vs field-ID alias collision: fail closed;
- original mapping preserved;
- `repair_applied=false`;
- `replacement_semantic_slot_collision` provenance;
- earlier safe repair rolled back when a later alias collision occurs;
- non-collision source-field repair remains unchanged.

Full F/E/D, A-F compatibility, full-fast, and CPU smoke must be rerun on the user's real
repository before commit.

## Not run

No G, no causal replay, no GPU, no 7B/model run.
