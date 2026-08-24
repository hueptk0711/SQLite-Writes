# Stage 2-F Patch 4 Checkpoint Report

## Objective

Close the final F4 target-assignment collision blocker without changing Stage2-F
architecture: a repaired target-column reference must never create a second assignment
to a target column already assigned by another source mapping or constant.

## Git base

```text
Stage2-F Patch 3
fc49aed70cb7033678d7e81c973b20f28f5d28af
```

Frozen parent remains `Stage2-E-FINAL`
(`7b9c4ef616fc2414fccba2cbe6b22016a3ed39b4`).

## Patch-4 changes

1. Defines semi-structured target assignment slots as `field_mapping.values()` plus
   `constants.keys()`.
2. Field-mapping target-column repair checks replacement against all other mapping
   values and all constant keys before mutation.
3. Constant-key repair checks replacement against all other constants and all mapping
   values before mutation.
4. Collision fails closed with `replacement_target_assignment_collision` and
   `repair_applied=false`; no semantic winner is selected.
5. Guard reads the current copied plan, so later collisions see slots created by earlier
   repairs and reuse atomic batch rollback.
6. Adds four adversarial tests: mapping→mapping, constant→mapping, mapping→constant,
   and batch rollback.
7. Adds CPU-smoke coverage for the target-assignment collision boundary.

## Unchanged

- source-field semantic alias guard from Patch 3;
- raw-key collision guards and atomic rollback from Patch 2;
- V5 -> V6 isolation and prompt identity;
- F eligibility/reference-kind whitelist;
- no-fuzzy and singleton policies;
- evidence/conflict/update-column protection;
- missing-slot fail-closed and one-retry boundary;
- Stage-1 classification 13 / 10 / 12;
- repair-rule accounting 70 exact-name / 0 singleton;
- A-E.

## Validation required

Dedicated F is expected to contain 45 pytest cases after Patch 4. Full F/E/D, A-F
compatibility, full-fast, and CPU smoke F/E/D must be rerun on the user's real
repository before commit.

## Not run

No G, no causal replay, no GPU, no 7B/model run.
