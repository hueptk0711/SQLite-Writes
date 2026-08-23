# Stage4R.1 Fresh Failure Attribution Targeted Revision

## Scope

Stage4R.1 is a CPU-only post-hoc diagnostic audit of the accepted Stage 4
fresh 7B run. It does not call the model, use GPU, regenerate prompts, change
raw generations, change evaluations, or alter the frozen Stage 4 protocol.

This patch addresses the reviewer-requested targeted revision after
`Stage4R_FRESH_FAILURE_ATTRIBUTION_REVIEWER_PACKAGE_20260823.zip`.

Accepted Stage 4 execution commit:

```text
d984e9815c13da5490b73b097181c563b5a1c534
```

Stage4R base commit:

```text
5e4e062fdc3bd72519da41fbab0b130d4166b319
```

## Main interpretation

Stage4R.1 adds a frozen-output diagnostic projection:

```text
D_G1
  ↓
D_F_G1_DIAGNOSTIC = D + F + G1, projected from frozen D_G1/FULL outputs
  ↓
FULL
```

The diagnostic projection is not a new confirmatory generation/evaluation run.
It answers whether the observed FULL-vs-D_G1 fresh rescues are isolated to F
within the already-frozen Stage 4 outputs.

Key result:

```text
D_G1_correct = 99/300
D_F_G1_DIAGNOSTIC_correct = 104/300
FULL_correct = 104/300

D_G1 → D_F_G1_DIAGNOSTIC: 5 rescues, 0 regressions
D_F_G1_DIAGNOSTIC → FULL: 0 rescues, 0 regressions
```

Conservative conclusion: F alone was sufficient to account for the five
FULL-vs-D_G1 discordant rescues within the frozen Stage 4 outputs. This remains
post-hoc diagnostic evidence, not a new primary method claim.

## F attribution details

- F activation means at least one constrained-reference repair trace with
  `repair_attempted == true`.
- F activation samples: `9`
- Exact-name F repairs: `38`
- F rescues: `5`
- F fail-closed cases: `4`
- F regressions: `0`
- `fail_closed` is counted only when FULL is still incorrect and
  `accepted_output == false`; incorrect accepted outputs are classified as
  `false_accept`.

The five F-rescue samples are concentrated in:

```text
input_type = semi_structured
operation_type = plain_insert
error_family = schema_reference_grounding / UNKNOWN_COLUMN_ID
db_id = financial for 4/5 rescues, student_club for 1/5 rescue
```

The component-activation drilldown for the five F rescues shows no observed
A/B/C/E/G trace contribution in those cases; B/C are not applicable for
`plain_insert`.

## Failure taxonomy additions

Stage4R.1 exports the full requested D_G1 failure taxonomy with marginal and
cross tables:

```text
failure_family_summary.csv
failure_by_input_type.csv
failure_by_operation.csv
failure_by_database.csv
failure_by_dependency_sensitive.csv
failure_family_x_input_type.csv
failure_family_x_operation.csv
failure_family_x_dependency.csv
```

The detailed D_G1 failure sample table now includes `dependency_sensitive`.
Error-family precedence is documented in:

```text
artifacts/error_family_precedence.json
```

## Preflight-abstention drilldown

D_G1 has `67` preflight-abstention cases. Stage4R.1 classifies them as:

```text
unique_constraint = 45
semantic_risk_gate = 15
foreign_key = 6
type_or_datatype = 1
```

No model or evaluator state was changed while producing this drilldown.

## Max-token-hit artifact

The prior wording “output-length failure” has been replaced with
“max-token-hit associated cases”. The counts are preserved:

```text
Direct = 15
J-FS = 33
Original MP-FS+ = 9
D_G1 = 9
D-only = 9
FULL = 9
NO-C = 9
```

## Main artifacts

```text
artifacts/stage4r_summary.json
artifacts/d_f_g1_diagnostic_sample_level.csv
artifacts/d_f_g1_diagnostic_paired_summary.csv
artifacts/component_activation_on_f_rescues.csv
artifacts/f_activation_sample_level.csv
artifacts/f_exact_name_repairs.csv
artifacts/full_vs_dg1_paired_summary.csv
artifacts/full_vs_dg1_paired_sample_level.csv
artifacts/d_g1_failure_sample_level.csv
artifacts/d_g1_failure_taxonomy.csv
artifacts/failure_family_summary.csv
artifacts/failure_by_input_type.csv
artifacts/failure_by_operation.csv
artifacts/failure_by_database.csv
artifacts/failure_by_dependency_sensitive.csv
artifacts/failure_family_x_input_type.csv
artifacts/failure_family_x_operation.csv
artifacts/failure_family_x_dependency.csv
artifacts/preflight_rejection_summary.csv
artifacts/preflight_rejection_sample_level.csv
artifacts/hit_max_new_tokens_summary.csv
artifacts/hit_max_new_tokens_samples.csv
artifacts/error_family_precedence.json
corrected_frozen_analysis/intervention_summary.csv
corrected_frozen_analysis/variant_metrics.csv
corrected_frozen_analysis/primary_paired_analysis.json
```

`artifacts/analysis_manifest.json` no longer lists itself in its artifact table.

## Code touched

```text
configs/stage4/d_f_g1_diagnostic.json
scripts/analysis/run_stage4r_fresh_failure_attribution.py
tests/test_stage4r_fresh_failure_attribution.py
stage4r_fresh_failure_attribution/*
```

The Windows Python 3.14 pytest tempdir shim remains test-only:

```text
tests/support/windows_py314_pytest_tempdir/sitecustomize.py
```
