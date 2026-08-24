# Stage4R.1 Fresh Failure Attribution Validation Report

## Execution scope

- Model calls: none
- GPU calls: none
- Raw generations changed: no
- Evaluations changed: no
- Frozen Stage 4 sample IDs changed: no
- Frozen Stage 4 prompt/config/protocol changed: no
- Primary result changed: no

## Revision checks added

- Added `D_F_G1_DIAGNOSTIC` projection from frozen D_G1/FULL outputs.
- Verified `D_G1 → D_F_G1_DIAGNOSTIC` has `5` rescues and `0` regressions.
- Verified `D_F_G1_DIAGNOSTIC → FULL` has `0` rescues and `0` regressions.
- F activation now requires `repair_attempted == true`.
- F outcome taxonomy now separates `fail_closed` from `false_accept`.
- D_G1 failure taxonomy now includes `dependency_sensitive`.
- Added requested marginal/cross failure-family tables.
- Added preflight-abstention reason drilldown.
- Exported error-family precedence as JSON.
- Removed `analysis_manifest.json` self-reference from the artifact manifest.
- Reworded max-token-hit cases without using “output-length failure” as a claim.

## Stage4R.1 artifact generation

Command:

```text
python scripts\analysis\run_stage4r_fresh_failure_attribution.py --protocol-root stage4_fresh_7b_protocol --result-root <extracted Stage4_FRESH_7B_ENVFINAL_RESULTS_FOR_REVIEW_20260823>\stage4_fresh_7b_results_envfinal --output-dir stage4r_fresh_failure_attribution\artifacts
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

D_G1_correct = 99
D_F_G1_DIAGNOSTIC_correct = 104
FULL_correct = 104
D_G1_to_D_F_G1_rescue = 5
D_G1_to_D_F_G1_regression = 0
D_F_G1_to_FULL_rescue = 0
D_F_G1_to_FULL_regression = 0
```

## Preflight drilldown

```text
unique_constraint = 45
semantic_risk_gate = 15
foreign_key = 6
type_or_datatype = 1
```

## Corrected frozen analysis regeneration

Command:

```text
python scripts\analysis\analyze_stage4_fresh_7b.py --protocol-root stage4_fresh_7b_protocol --result-root <extracted Stage4_FRESH_7B_ENVFINAL_RESULTS_FOR_REVIEW_20260823>\stage4_fresh_7b_results_envfinal --output-dir stage4r_fresh_failure_attribution\corrected_frozen_analysis
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

## Local validation commands

Literal stdout/stderr logs are archived under:

```text
stage4r_fresh_failure_attribution/validation/
```

The committed logs include:

```text
environment.txt
python_compile.txt
dedicated_stage4r1_tests.txt
full_fast_suite.txt
protocol_hash_tests.txt
stage4r1_generation.txt
corrected_frozen_analysis_generation.txt
```

Observed status:

```text
compile_exit = 0
stage4r_generation_exit = 0
corrected_analysis_exit = 0
dedicated_tests_exit = 0
protocol_tests_exit = 0
full_suite_exit = 0
```

Local note: Windows Python 3.14 created pytest temp directories with restrictive
permissions in this environment. Pytest validation was run with the existing
`tests/support/windows_py314_pytest_tempdir/sitecustomize.py` shim on
`PYTHONPATH` to avoid the local tempdir ACL issue. This shim is test-only and is
not used by project code or server/GPU execution.
