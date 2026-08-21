# Stage 2-F Patch 2 Checkpoint Report

## Objective

Close the final Patch-1 safety blocker without changing F architecture: a reference repair must never overwrite an already-existing structural slot. Regenerate Stage-1 diagnostic classification using the actual F eligibility whitelist.

## Git base

```text
Stage2-F Patch 1
bfc77d5d5710791a29859db903e3b89c8d7d2057
```

Frozen parent remains `Stage2-E-FINAL` (`7b9c4ef616fc2414fccba2cbe6b22016a3ed39b4`).

## Patch-2 changes

1. Adds pre-mutation replacement-key collision guards for source-field keys, constants keys, and free-text row column keys.
2. Collision is fail-closed: no overwrite, no merge, no first/second-wins policy.
3. If a later diagnostic in the same repair batch collides after an earlier safe mutation, the whole copied-plan repair batch is rolled back atomically.
4. Adds four reviewer-requested adversarial collision tests.
5. Regenerates the 35-case Stage-1 F classification under the implementation whitelist.
6. Separately records exact-name vs singleton diagnostic repair-rule counts.

## Updated Stage-1 diagnostic classification

```text
REPAIRABLE_REFERENCE_ONLY                     13
REFERENCE_REPAIR_PARTIAL_BUT_SAMPLE_NOT_SAFE  10
NON_REPAIRABLE_REFERENCE                      12
```

Specific corrections:

- `final_vaccine_018`: `NON_REPAIRABLE_REFERENCE`, because `conflict_target_id` is protected by F.
- `final_vaccine_033`: remains partial, but F-eligible repair count is 8 rather than 12 because four `update_column_ids` repairs are protected.

F-eligible proposed repair rules in the diagnostic set:

```text
unique_exact_identifier_name  70
unique_closed_set_candidate    0
```

These are diagnostic/development counts only and are not an end-to-end rescue rate.

## Isolated Patch-2 validation

- Python syntax: PASS.
- Existing non-collision exact-name repair: PASS.
- free-text existing-valid-key collision: PASS / fail closed.
- source-field key collision: PASS / fail closed.
- constants key collision: PASS / fail closed.
- two-invalid-refs-to-one-replacement atomic rollback: PASS / fail closed.
- regenerated classification contract: PASS.

Full F/E/D, A-F compatibility, full-fast, and CPU smoke must be rerun on the user's real repository before commit.

## Not changed

V5→V6 isolation, prompt identity, deep-copy architecture, evidence/conflict/update protection, no-fuzzy rule, missing-slot policy, one-retry contract, and revalidation semantics remain unchanged.

## Not run

No G, no causal replay, no GPU, no 7B/model run.
