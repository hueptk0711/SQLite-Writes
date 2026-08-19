# Stage 2 D Checkpoint Report

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

## Regression coverage

Dedicated D tests cover:

1. V4 config inheritance/dispatch and A–C preservation;
2. D-off legacy behavior;
3. `rowN:` segmentation;
4. repeated `row = N` segmentation;
5. dotted `row_N.field=value` segmentation;
6. colon control-row separation;
7. conservative `None`/`NULL`/`nil` policy and provenance;
8. quoted textual null preservation;
9. typed JSON/Python null preservation;
10. prompt-time parser config propagation;
11. all seven exact Stage-1 parser diagnostic fixtures;
12. context-only `table` payload fields remain payload without a strong control signal.

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
