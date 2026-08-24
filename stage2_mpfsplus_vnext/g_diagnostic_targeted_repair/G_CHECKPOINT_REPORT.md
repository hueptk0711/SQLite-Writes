# Stage 2-G1 Checkpoint Report

## Result

Stage 2-G1 implements only deterministic evidence-span terminal-boundary repair under a conservative policy; eligibility does not independently prove that punctuation is linguistically incorrect. The implementation is independently ablatable, keeps the V6 prompt unchanged, deep-copies the reference plan, changes one diagnosed `value_from` slot, uses only the frozen pre-enumerated candidate set, and performs at most one revalidation. Period is exercised by the frozen fixture; comma remains reserved compatibility behavior with no observed activation.

## Stage-1 diagnostic fixture

The manually audited `final_archeology_032` case selects `e3="SC9081."`; the frozen candidate set also contains `e7="SC9081"` with the same start and end-minus-one boundary. The dedicated regression test verifies the resulting compiled parameter is `SC9081` and records a successful G1 trace.

This is diagnostic/regression evidence only. No model, dataset regeneration, gold change, metric change, or new experimental result is included.

## Safety coverage

Dedicated tests cover:

- deterministic diagnostic gating;
- closed candidate-set restriction;
- no request-wide fallback search;
- unique candidate enforcement;
- exactly one diagnosed slot;
- deep-copy and single-slot mutation;
- replacement evidence collision;
- one materialization retry;
- verifier fail-closed behavior;
- mandatory provenance;
- disabled-mode V6 compatibility;
- unchanged V6/V7 prompt.

Final validation on the resulting code:

```text
dedicated G1 tests:       20 passed
A–G compatibility suite: 157 passed
full fast suite:          307 passed, 1 skipped, 12 subtests passed
CPU smoke G1:             PASS
```

The single skipped test is the repository's pre-existing optional integration skip; it is not a G1 failure.

## Frozen boundary

```text
base tag:    Stage2-F-FINAL
base commit: 48b36e06a0ccef2bee69120029820f99f9fa6af5
```

The resulting Stage 2-G1 commit is recorded in the reviewer package provenance after commit creation.
