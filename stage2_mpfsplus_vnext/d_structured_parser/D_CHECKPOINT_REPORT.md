# Stage 2 D Patch 3 Checkpoint Report

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


## Reviewer Patch-3 hardening

The Patch-2 review accepted D2 control separation, D3 NULL handling, and D4 provenance, but found one remaining D1 namespace case: a named dotted prefix could still be merged with unprefixed dotted rows or unprefixed row-heading/row-marker syntax because the empty prefix was not represented as a namespace.

Patch 3 therefore applies one invariant:

> An explicit D repeated-row result is committed only when all row fields belong to one unambiguous row namespace.

The parser treats unprefixed dotted rows and unprefixed row-heading/row-marker syntax as `__UNPREFIXED__`. If a named dotted prefix appears together with that namespace, or if multiple named prefixes appear, D defers to the historical parser path.

This preserves both supported single-namespace forms:
- all-unprefixed `row_N.field=value`;
- one named prefix such as `robot_record.row_N.field=value`.


## Regression coverage

The dedicated D suite now contains 19 test functions. Patch 2 retains its four trust-boundary tests, and Patch 3 adds three D1 namespace regressions:

17. named-prefix + unprefixed dotted rows defer without synthesis;
18. named-prefix dotted rows mixed with unprefixed `rowN:` headings defer;
19. all-unprefixed dotted rows remain supported.

Patch-2 regressions for multi-prefix safety, single-prefix retention, invalid operation aliases, and provenance completeness remain unchanged.

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
