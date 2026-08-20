# Stage 2 D Patch 2 Checkpoint Report

## Baseline

- Branch base: `Stage2-A-C-FINAL`
- Reviewed A–C commit: `f1fa49ddb8e6b920fb5a4237e088b6603579ae23`
- D config: `configs/stage2/v4_structured_parser.json`

## Stage-1 diagnostic evidence targeted

D targets exactly seven manually reviewed deterministic parser cases:

- `final_polar_014` — textual `None` incorrectly coerced to NULL;
- `final_polar_058` — textual `None` incorrectly coerced to NULL;
- `final_robot_026` — control block fabricated as an additional payload row;
- `final_vaccine_040` — eight repeated rows collapsed into one malformed row;
- `final_virtual_010` — colon control block fabricated as a payload row;
- `final_virtual_025` — three `rowN:` records collapsed to the final row;
- `final_virtual_040` — eight `rowN:` records collapsed to the final row.

The exact request texts are frozen in `tests/fixtures/stage2_d_stage1_parser_cases.json` only for parser-level regression testing. This is diagnostic-set reuse, not a confirmatory experiment.

## Parser-level result after D

On the seven diagnostic fixtures:

| Sample | Expected parser structure after D |
|---|---|
| final_polar_014 | 1 collection, 8 rows, two TEXT `"None"` values preserved |
| final_polar_058 | 1 collection, 3 rows, one TEXT `"None"` value preserved |
| final_robot_026 | `robot_record`, 8 rows, no bogus control collection |
| final_vaccine_040 | `shipments`, 8 rows |
| final_virtual_010 | `additionalnotes`, 1 payload row only |
| final_virtual_025 | `eventsandclub`, 3 rows |
| final_virtual_040 | `additionalnotes`, 8 rows |

All parser-level assertions pass. These observations do **not** claim that seven end-to-end examples are rescued. Full causal replay remains a later Stage-2 step.

## Reviewer Patch-2 hardening

The first D review accepted the NULL policy and core integration but identified three trust-boundary gaps. Patch 2 therefore adds no new feature family; it only hardens:

1. multi-prefix dotted rows: `>1` distinct dotted collection prefix makes the explicit D detector defer instead of merging by row label;
2. control context: `operation=login` is not a high-confidence write-operation signal and cannot reclassify a neighboring `table` payload field;
3. provenance: numbered, bulleted, and equals key-value paths now emit the same payload-cell trace contract as CSV/markdown/colon/explicit-row paths.

The original 7 Stage-1 fixtures and D3 NULL policy are unchanged.

## Regression coverage

The dedicated D suite now contains 16 test functions. In addition to the original 12 contracts, Patch 2 adds:

13. multi-prefix `parent.row_N.* + child.row_N.*` defers without cross-collection row merging;
14. single-prefix `robot_record.row_N.*` remains supported;
15. invalid `operation=login` does not reclassify `table=audit` as metadata;
16. provenance completeness is checked across CSV, markdown, colon KV, equals KV, numbered, and bulleted textual formats.

## Acceptance criteria

D should be accepted only if:

- V3/A–C behavior remains unchanged when D is disabled;
- all explicit repeated rows are retained without an added row marker field;
- recognized control-only blocks cannot become payload rows;
- ambiguous text `None`/`nil` is not converted to SQL NULL under V4;
- explicit unquoted `NULL` still maps to null;
- quoted null-looking text remains text;
- prompt-time and pipeline-time parsers use the same D config;
- dedicated, compatibility, full-fast, and CPU-smoke checks have no failures.

## Not yet performed

- full Stage-2 causal replay;
- end-to-end 7B development run;
- 3B/14B scaling;
- token-limit experiments;
- public benchmark experiments.
