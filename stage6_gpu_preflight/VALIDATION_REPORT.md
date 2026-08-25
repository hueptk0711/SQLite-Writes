# Stage6F Validation Report

Validation date: 2026-08-25

## Local CPU Validation

```text
python scripts/data/create_stage6f_gpu_preflight.py --output-dir stage6_gpu_preflight
status = PENDING_GPU_EXECUTION
frozen_artifact_audit_status = PASS
confirmation_predictions_created = false
confirmation_run_allowed_now = false
```

```text
python scripts/data/validate_stage6f_gpu_preflight.py --preflight-dir stage6_gpu_preflight
status = PASS
violations = []
```

```text
python -m pytest -q tests/test_stage6f_gpu_preflight.py
6 passed
```

## GPU Validation

GPU validation was not executed in the local workspace. This is intentional:
the local machine did not provide the locked GPU/model environment. The package
therefore does not claim GPU preflight PASS.

The required server command sequence is packaged in:

```text
stage6_gpu_preflight/RUN_STAGE6F_ON_SERVER.md
```

Expected server-side validation after execution:

```text
python scripts/data/validate_stage6f_gpu_preflight.py \
  --preflight-dir <server-output-dir>/stage6_gpu_preflight \
  --require-gpu-pass
```

## Result

```text
final_confirmation_n = 481
gpu_environment_preflight_passed = false
confirmation_predictions_created = false
confirmation_run_allowed_now = false
status = PENDING_GPU_EXECUTION
```

