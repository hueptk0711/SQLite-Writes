# Stage 2 E Checkpoint Report — Patch 1 Candidate

## Base

```text
Stage2-D-FINAL
0eba16b297100966d3635172456086db872166d6
```

## What E changes

E adds an independently ablatable free-text typed temporal normalizer at the evidence
materialization boundary. It extends the old `iso_date_normalization` path only when the
reference-plan rule, resolved target type, and strict evidence surface agree.

## Stage-1 diagnostic evidence

The Stage-1 DATE_NORMALIZATION audit found:

- 11 reviewed date-tagged samples;
- 12 valid explicit datetime values rejected by the old date-only rule;
- two wrong evidence tokens (`Attempt`, `For`) that must continue to fail;
- three clean normalization-only samples;
- seven samples with additional non-date failures.

The encoded fixture therefore tests 14 cell-level diagnostic cases. Passing these fixtures
is normalization-level regression evidence, not evidence that 12 samples are rescued
end-to-end.

## Safety policy

E requires:

```text
explicit iso_date_normalization request
+ strict year-first temporal surface
+ compatible resolved target storage type
```

Otherwise it fails closed or leaves non-E normalization untouched.

Key negative invariants:

- no global punctuation stripping;
- no DD/MM versus MM/DD guessing;
- no evidence repair;
- no quoted-text reinterpretation;
- no structured-source D3 changes.

## Ablation identity

```text
V4: vnext-v4-structured-parser
V5: vnext-v5-typed-normalization
```

V5 retains the exact V4 A–D blocks and adds only `free_text_typed_normalization`.

## Required validation before reviewer freeze

- dedicated `tests/test_stage2_vnext_e.py` 100%;
- frozen D suite 100%;
- A–D compatibility subset no failures;
- full fast suite no failures;
- V4/V5 prompt identity PASS;
- E-disabled behavior equals V4 PASS;
- E CPU smoke PASS;
- Stage-1 diagnostic temporal fixture 12 expected accepts + 2 expected rejects;
- no GPU/model run.
