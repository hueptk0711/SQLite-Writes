# Stage 2 E Checkpoint Report — Patch 3 Candidate

## Base

```text
Stage2 E Patch 2 commit
a132f37a2173b36eab65683cc6799f948bf7eac4
```

Frozen parent remains `Stage2-D-FINAL` (`0eba16b297100966d3635172456086db872166d6`).

## Reviewer issues addressed

Patch 3 is intentionally limited to the final target-compatibility boundary.

1. **Declared-type classification**
   - removes substring `DATE`/`TIME` recognition;
   - recognizes `DATE`, `DATETIME`, and `TIMESTAMP` as exact Stage-E temporal declarations;
   - treats `TIME` as unsupported because E has no time-only evidence subtype;
   - preserves SQLite text-affinity storage via `CHAR` / `CLOB` / `TEXT`;
   - unknown/custom declarations such as `CANDIDATE` and `RUNTIME` fail closed.

2. **Target temporal subtype consistency**
   - semantic `date` / `date_key` accepts only date evidence;
   - semantic `datetime` / `timestamp` accepts only datetime evidence;
   - generic `text`, `temporal`, or unspecified semantic metadata may accept either subtype
     when declared storage is compatible;
   - exact declared `DATE` accepts only date evidence;
   - exact declared `DATETIME` / `TIMESTAMP` accepts only datetime evidence;
   - mismatches fail with `TEMPORAL_TARGET_SUBTYPE_MISMATCH`.

Patch 3 does not modify candidate evidence typing, ASCII grammar, ambiguous-format policy,
wrong-evidence rejection, causal provenance, raw-evidence preservation, prompt identity,
D3 NULL behavior, or the V4→V5 ablation.

## Stage-1 diagnostic fixture

The existing 14 typed diagnostic cells are unchanged:

```text
12 valid temporal cells -> candidate_type=datetime -> expected normalization accept
Attempt / For           -> candidate_type=text     -> expected typed-evidence reject
```

This remains diagnostic/regression evidence only.

## Regression coverage

Dedicated E coverage is expanded from 28 to an expected **36 pytest cases**.

New adversarial coverage includes:

- custom declared type `CANDIDATE` is not temporal merely because it contains `DATE`;
- custom declared type `RUNTIME` is not temporal merely because it contains `TIME`;
- semantic `date` rejects datetime evidence;
- semantic `datetime` rejects date evidence;
- semantic `date_key` rejects datetime evidence;
- semantic `timestamp` rejects date evidence;
- declared `DATE` rejects datetime evidence;
- declared `DATETIME` rejects date evidence.

Existing TEXT-backed date/datetime acceptance remains covered by prior tests and the
Stage-1 fixture.

## Required validation before reviewer freeze

- dedicated E suite 100%;
- frozen D suite 100%;
- A–E compatibility subset no failures;
- full fast suite no failures;
- CPU smoke E PASS;
- CPU smoke D PASS;
- 14 typed Stage-1 diagnostic cells unchanged at 12 PASS + 2 EXPECTED_REJECT;
- no GPU/model run;
- no F/G/causal replay.
