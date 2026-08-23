# Stage4R.2 Validation Report

## Scope

- Model calls: none
- GPU calls: none
- Raw Stage-4 generations modified: no
- Fresh sample IDs modified: no
- Dataset/gold labels modified: no
- Protocol changed: no

## Actual replay command

```text
python scripts\analysis\run_stage4r2_actual_dfg1_replay.py --protocol-root stage4_fresh_7b_protocol --result-root <Stage4_FRESH_7B_ENVFINAL_RESULTS_FOR_REVIEW_20260823>\stage4_fresh_7b_results_envfinal --config configs\stage4\d_f_g1_diagnostic.json --actual-run-dir stage4r2_actual_d_f_g1_replay\actual_run --output-dir stage4r2_actual_d_f_g1_replay\artifacts
```

Status:

```text
PASS
```

## Actual replay summary

```text
fresh_sample_count = 300
D_G1_correct = 99
ACTUAL_D_F_G1_correct = 104
FULL_correct = 104
D_G1_to_ACTUAL_D_F_G1_rescue = 5
D_G1_to_ACTUAL_D_F_G1_regression = 0
ACTUAL_D_F_G1_to_FULL_rescue = 0
ACTUAL_D_F_G1_to_FULL_regression = 0
F_activation_sample_count = 9
F_exact_name_repair_count = 38
```

## Test validation

Committed validation logs are under:

```text
stage4r2_actual_d_f_g1_replay/validation/
```

They include environment/git status, compile output, Stage4 protocol validator
output, Stage4R.2 analysis-from-actual-run output, actual replay summary,
dedicated pytest output, full-suite pytest output, and internal checksum
verification.

Expected exit statuses:

```text
python_compile_exit = 0
stage4r2_analysis_from_actual_run_exit = 0
dedicated_stage4r2_tests_exit = 0
stage4_protocol_validator_exit = 0
full_fast_suite_exit = 0
internal_checksum_verifier_exit = 0
```

Local note: Windows Python 3.14 requires the existing test-only
`tests/support/windows_py314_pytest_tempdir/sitecustomize.py` shim for pytest
tempdir ACL handling. The shim is not used by project code or replay execution.
