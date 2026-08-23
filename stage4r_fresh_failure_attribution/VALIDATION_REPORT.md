# Stage4R Fresh Failure Attribution Validation Report

## Execution scope

- Model calls: none
- GPU calls: none
- Raw generations changed: no
- Evaluations changed: no
- Configs changed: no
- Protocol selection changed: no

## Dedicated validation

```text
python -m pytest -q tests\test_stage4_analysis_freeze.py tests\test_stage4r_fresh_failure_attribution.py --basetemp <workspace-temp>
```

Status:

```text
PASS — 9 passed
```

Local note: Windows Python 3.14 created pytest temp directories with restrictive
permissions in this environment. Dedicated pytest was run with
`tests/support/windows_py314_pytest_tempdir/sitecustomize.py` on `PYTHONPATH` to
avoid the local tempdir ACL issue. This shim is test-only and not used by
project code.

## Stage4R artifact generation

```text
python scripts\analysis\run_stage4r_fresh_failure_attribution.py \
  --protocol-root stage4_fresh_7b_protocol \
  --result-root <extracted Stage4_FRESH_7B_ENVFINAL_RESULTS_FOR_REVIEW_20260823>/stage4_fresh_7b_results_envfinal \
  --output-dir stage4r_fresh_failure_attribution\artifacts
```

Status:

```text
PASS
```

Key summary:

```text
D_G1_incorrect_count = 201
F_activation_sample_count = 9
F_exact_name_repair_count = 38
F_rescue_count = 5
F_fail_closed_count = 4
F_regression_count = 0
FULL_vs_D_G1 = 5 rescues, 0 regressions
```

## Corrected frozen analysis regeneration

```text
python scripts\analysis\analyze_stage4_fresh_7b.py \
  --protocol-root stage4_fresh_7b_protocol \
  --result-root <extracted Stage4_FRESH_7B_ENVFINAL_RESULTS_FOR_REVIEW_20260823>/stage4_fresh_7b_results_envfinal \
  --output-dir stage4r_fresh_failure_attribution\corrected_frozen_analysis
```

Status:

```text
PASS
```

Corrected intervention summary:

```text
G1_attempts = 0
G1_applied = 0
G1_revalidation_success = 0
G1_trace_sample_count = 0
```
