# Stage6F GPU Preflight Acceptance PATCH1

This directory ingests the server-side Stage6F GPU preflight output. The server run loaded the locked environment, tokenizer, and model, built/tokenized all 481 confirmation prompts for all five arms, and confirmed H2 shared prompt identity.

No confirmation predictions were created. Confirmation inference remains blocked until reviewer acceptance and a separate run-authorization lock.

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
model_generate_called_for_confirmation_samples = false
confirmation_predictions_created = false
confirmation_run_allowed_now = false
```
