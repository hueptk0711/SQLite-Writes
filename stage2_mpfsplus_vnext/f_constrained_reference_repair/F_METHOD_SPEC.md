# Stage 2-F Method Specification — Constrained Reference Repair

## Scope

Stage 2-F is a deterministic post-generation intervention applied only **after** an existing frozen reference/materialization boundary has rejected a generated non-empty reference.

F does **not** regenerate a plan, search arbitrary schema objects, repair values or evidence text, change operation/conflict/update semantics, or use fuzzy/edit-distance similarity.

## Frozen parent

```text
Stage2-E-FINAL
7b9c4ef616fc2414fccba2cbe6b22016a3ed39b4
```

A-E are frozen inputs to F. F does not change planner/reference/materializer APIs. The V5 boundary runs first; F may create one repaired copy of the generated plan and retry the same frozen boundary once.

## F1 — Repair eligibility

A slot is eligible only when all of the following hold:

1. F is enabled.
2. The slot contains a non-empty reference.
3. The ordinary deterministic boundary has already rejected that reference.
4. The diagnostic identifies a repairable reference kind and slot path.
5. The diagnostic exposes a slot-local closed set of valid references.
6. The current F invocation has not already consumed its single retry.

A valid reference is never rewritten. A missing reference is not auto-filled and is not counted as a repair attempt.

## F2 — Closed-set selection

F may select only from the diagnostic-provided slot-local closed set.

Two deterministic selection rules are allowed, in this order:

1. `unique_exact_identifier_name`: after identifier quote stripping and casefolding, the invalid reference's identifier name exactly matches one named valid reference. Punctuation and underscores are preserved; no fuzzy matching is used.
2. `unique_closed_set_candidate`: if no exact-name anchor exists and the closed set contains exactly one valid reference, select it.

If the closed set is empty, ambiguous, or the exact name matches more than one candidate, F fails closed.

## F3 — Reference-kind isolation

Eligible F repairs are restricted to structural reference slots for which the frozen boundary provides a closed valid set, including:

- target table references;
- target column references;
- source collection references;
- source selector references;
- source-field references.

The following are explicitly **protected and non-repairable in F** because they encode semantic choices already assigned to other frozen stages or later G work:

- evidence selection (`value_from` / evidence IDs);
- conflict-target semantics;
- update-column semantics.

F never cross-repairs between reference kinds.

## F4 — Non-reference semantic invariance

Repair changes only the invalid reference token in a deep-copied generated plan. It must not modify:

- source/evidence value;
- raw evidence span or offsets;
- operation/write semantics;
- conflict action or target semantics;
- update-column semantics;
- target values;
- prompt text;
- any frozen A-E behavior.

## F5 — One-retry fail-closed contract

```text
max_attempts_per_slot = 1
require_unique_candidate = true
preserve_non_reference_semantics = true
emit_repair_provenance = true
```

These are safety invariants, not tunable coverage knobs. Configuration that attempts to relax them is rejected.

Pipeline control flow is:

```text
frozen V5 boundary
    ↓ invalid reference diagnostic
F closed-set repair on copied plan
    ↓ if exactly one replacement is selected
same frozen boundary, one retry only
    ↓
pass OR fail closed
```

If the retry exposes a new invalid reference, F does **not** start a second repair cycle.

## F6 — Provenance

Every diagnostic handled by F records:

```text
repair_attempted
repair_applied
repair_succeeded
reference_kind
slot_path
original_reference
replacement_reference
candidate_set
candidate_count
repair_rule
repair_reason
validation_before
validation_after
max_attempts_per_slot
```

`repair_succeeded` becomes true only after the repaired plan passes the same original reference boundary for that slot. Selecting a replacement alone is not recorded as success.

This trace is required for later causal replay. It does not itself prove an end-to-end rescue.

## Ablation

```text
V5 = A+B+C+D+E
V6 = A+B+C+D+E+F
```

F is controlled only by `constrained_reference_repair.enabled`. With F disabled, V6 must reproduce V5 behavior and prompt construction.

## Stage-1 diagnostic classification

The frozen Stage-1 diagnostic set contains 35 reference-resolution first failures:

```text
REF_UNKNOWN_COLUMN       28
REF_INVALID_SOURCE_REF    7
```

Diagnostic classification used for regression design:

```text
REPAIRABLE_REFERENCE_ONLY                     14
REFERENCE_REPAIR_PARTIAL_BUT_SAMPLE_NOT_SAFE  10
NON_REPAIRABLE_REFERENCE                      11
```

These counts are development evidence only. `REPAIRABLE_REFERENCE_ONLY=14` must **not** be reported as 14 rescued samples. Whole-pipeline target-state correctness has not been established at this checkpoint.

## Explicitly out of scope

- semantic correction of a reference that is already valid;
- evidence-span or evidence-ID repair;
- conflict-target or update-column repair;
- plan regeneration;
- fuzzy/edit-distance matching;
- G diagnostic-driven targeted repair;
- full A-G causal replay;
- GPU / 3B / 5B / 7B / 14B runs.
