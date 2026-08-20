# Stage 2 E Checkpoint Report — Patch 2 Candidate

## Base

```text
Stage2 E Patch 1 commit
5d1575728abcfde209ba3e2c4ef1a1bf5843f9c2
```

Frozen parent remains `Stage2-D-FINAL` (`0eba16b297100966d3635172456086db872166d6`).

## Reviewer issues addressed

Patch 2 is intentionally narrow and addresses four review items:

1. **Target semantic guard** — explicit incompatible semantics such as identifier,
   boolean, JSON, numeric/blob-like targets now fail closed. TEXT storage with semantic
   `text` remains supported.
2. **Causal provenance activation** — `applied`/`intervention_applied` now represent E
   handling, while `value_changed` independently records byte/string mutation.
3. **Candidate-type invariant** — missing type is rejected; only `date`/`datetime` are
   accepted; subtype must match the grammar.
4. **ASCII grammar** — temporal regexes use `[0-9]`, rejecting full-width/Unicode digits.

`preserve_raw_evidence=false` is also rejected when E is enabled because provenance
preservation is mandatory rather than an optional behavioral toggle.

## Stage-1 diagnostic fixture

The 14 cell-level fixtures now carry typed metadata:

```text
12 valid temporal cells -> candidate_type=datetime -> expected normalization accept
Attempt / For           -> candidate_type=text     -> expected typed-evidence reject
```

This is still diagnostic/regression evidence only.

## Regression coverage

Dedicated E coverage is expanded from 19 to an expected **28 pytest cases**, including:

- identifier / boolean / JSON target semantic rejection;
- TEXT temporal storage acceptance;
- intervention activation with unchanged DATETIME surface;
- value-changed provenance for slash-date canonicalization;
- missing candidate type rejection;
- date-vs-datetime subtype mismatch rejection in both directions;
- quoted text candidate rejection;
- full-width digit rejection;
- mandatory raw-evidence provenance config.

## Required validation before reviewer freeze

- dedicated E suite 100%;
- frozen D suite 100%;
- A–E compatibility subset no failures;
- full fast suite no failures;
- CPU smoke E PASS;
- CPU smoke D PASS;
- 14 typed Stage-1 diagnostic cells behave as declared;
- no GPU/model run;
- no F/G/causal replay.
