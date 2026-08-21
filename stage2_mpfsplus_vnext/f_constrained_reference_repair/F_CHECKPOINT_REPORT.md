# Stage 2-F Patch 1 Checkpoint Report

## Objective

Implement constrained reference repair on top of frozen `Stage2-E-FINAL` without reopening A-E.

## Frozen base

```text
Stage2-E-FINAL
7b9c4ef616fc2414fccba2cbe6b22016a3ed39b4
```

## Final Patch-1 architecture

F is a pipeline-level retry wrapper. It does not add F parameters to frozen planner/reference/materializer functions.

```text
normal V5 reference/materialization boundary
        ↓ invalid diagnostic
F repairs eligible reference on a deep copy
        ↓
normal V5 boundary rerun exactly once
        ↓
pass or fail closed
```

## Implemented

- independent V6 `constrained_reference_repair` configuration;
- one closed-set deterministic repair helper;
- valid-reference and missing-slot trust boundaries;
- exact identifier-name anchor without fuzzy matching;
- singleton closed-set fallback;
- eligible structural kinds: table, column, source collection/selector/field;
- protected non-F semantics: evidence selection, conflict target, update columns;
- mandatory repair provenance finalized only after revalidation;
- no recursive second repair when retry exposes a new reference failure;
- V5/V6 disabled identity and prompt identity checks;
- Stage-1 reference-failure diagnostic classification fixture;
- dedicated/adversarial tests and CPU smoke.

## Diagnostic evidence

35 Stage-1 first reference failures were classified before treating them as regression targets:

| Class | Count | Interpretation |
|---|---:|---|
| `REPAIRABLE_REFERENCE_ONLY` | 14 | all audited blockers are reference errors classified as locally repairable under the F contract |
| `REFERENCE_REPAIR_PARTIAL_BUT_SAMPLE_NOT_SAFE` | 10 | at least one reference error is locally repairable but other blockers remain; no sample-level rescue claim is permitted |
| `NON_REPAIRABLE_REFERENCE` | 11 | current F contract must fail closed |

This classification is diagnostic/development evidence only.

## Isolated/cumulative validation before release

```text
Dedicated F pytest cases: 35/35 PASS
Frozen E + D regression:   56/56 PASS
CPU smoke F:               PASS
```

CPU smoke verifies:

- unique exact identifier-name selection;
- singleton closed-set selection;
- ambiguous closed-set fail-closed behavior;
- valid references never repaired;
- missing references not auto-filled or counted as attempts;
- no fuzzy similarity;
- protected evidence semantics;
- non-reference semantics preserved;
- repair success finalized only after revalidation;
- one-retry/no-recursion contract;
- V5/V6 prompt identity;
- free-text pipeline repair;
- 35-case Stage-1 classification manifest.

## Real-repository validation required before commit

The user's actual branch must still run:

1. `tests/test_stage2_vnext_f.py`;
2. frozen E and D suites;
3. A-F compatibility subset;
4. full fast suite;
5. CPU smoke F;
6. CPU smoke E and D.

Only results from the user's real branch may be used as final reviewer evidence.

## Claims not made

- no Stage-1 end-to-end rescue rate;
- no causal effect estimate;
- no fresh development result;
- no GPU/model result;
- no 7B result.
