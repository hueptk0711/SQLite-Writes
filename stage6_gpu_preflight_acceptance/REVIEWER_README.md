# Stage6F GPU Preflight Acceptance PATCH2

This directory ingests the hardened server-side Stage6F GPU preflight output. PATCH2 verifies exact frozen model/tokenizer identity, captures SQLite runtime and GPU visibility, runs one synthetic non-confirmation generation smoke, and keeps confirmation inference blocked.

Key results:

```text
server_preflight_status = PASS_GPU_PREFLIGHT_COMPLETE
gpu_environment_preflight_passed = true
final_confirmation_n = 481
prompt_token_rows = 2405
max_observed_input_tokens = 2253
input_truncation_error_count = 0
h2_checked_pairs = 481
h2_mismatch_count = 0
model_aggregate_sha256 = e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c
tokenizer_sha256 = 06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468
model_config_sha256 = 326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357
sqlite_runtime_captured = true
CUDA_VISIBLE_DEVICES = 1
synthetic_smoke = PASS
confirmation_samples_used_by_smoke = 0
model_generate_called_for_confirmation_samples = false
confirmation_predictions_created = false
confirmation_run_allowed_now = false
```
