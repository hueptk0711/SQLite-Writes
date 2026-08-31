# Stage7E0-A3 English Real Generation Preflight PATCH3 Validation Report

Status: PASS_PATCH3_READY_FOR_REAL_CONSTRAINED_RUN

Validation date: 2026-08-31

## Scope

This patch hardens the accepted PATCH9 incremental JSON-schema grammar backend
package before GPU execution. It does not claim a new scientific model result
unless `backend=constrained_hf` is run on the GPU server in the fresh PATCH3
result root.
The local dry-run uses label-side expected outputs only as a mock infrastructure
test and is marked as non-scientific model evidence.

## Locked Inputs

```text
accepted_protocol_commit=ca5aab5629a62a702f06dea7f9752702a8d2314f
phase_o_prompt_spec=Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json
model=Qwen/Qwen2.5-Coder-7B-Instruct
revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
primary_cases=8
acceptance=8/8 required
retry=0
repair=none
diagnostics_run=false
gretel_pilot_opened=false
backend=incremental_json_schema_grammar
token_level_enforcement=true
fallback_to_unconstrained=false
quantization=none
phase_o_max_new_tokens=512
phase_m_max_new_tokens=8192
resume_allowed=false
fresh_result_root=/home/uet/hue_ptk/stage7e0_a3_english_patch3_constrained_results_20260831
frozen_runtime_versions={"accelerate":"1.14.0","safetensors":"0.5.3","tokenizers":"0.22.2","torch":"2.6.0+cu124","transformers":"5.5.3"}
```

## Invalid Prior Run Classification

The prior PATCH1 server output is preserved as evidence but is not scientifically
eligible because it used plain unconstrained HF generation. Its primary gate is
therefore `INVALID_NOT_EVALUATED`, not `FAIL_0_OF_8`.

## Local Mock Dry-Run

```text
backend=mock
status=PASS
primary_pass_count=8/8
model_called=false
gpu_called=false
mock_uses_label_side_expected=true
```

## Validation Commands

```text
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python scripts/data/validate_stage7e0_a3_english_preflight.py --stage-dir Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT
python scripts/data/validate_stage7e0_a3_server_results.py --stage-dir Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
python -m pytest -q tests/test_stage7e0_a3_patch2_constrained_backend.py
python -m pytest -q tests/test_stage7e0_a3_patch3_protocol_hardening.py
python -m zipfile --test Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH3_FINAL_REVIEWER_PACKAGE_20260831.zip
```
