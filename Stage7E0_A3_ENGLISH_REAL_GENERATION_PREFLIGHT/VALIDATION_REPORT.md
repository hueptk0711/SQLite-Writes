# Stage7E0-A3 English Real Generation Preflight PATCH0 Validation Report

Status: PASS_PROTOCOL_READY_FOR_REAL_QWEN_RUN

Validation date: 2026-08-30

## Scope

This patch prepares the real Stage7E0-A3 runner and server reviewer package. It
does not claim a real model result unless `backend=hf` is run on the GPU server.
The local dry-run uses label-side expected outputs only as a mock infrastructure
test and is marked as non-scientific model evidence.

## Locked Inputs

```text
accepted_protocol_commit=ab006242bc498c343fe9573c893283a9733bcc1f
phase_o_prompt_spec=Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json
model=Qwen/Qwen2.5-Coder-7B-Instruct
revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
primary_cases=8
acceptance=8/8 required
retry=0
repair=none
diagnostics_run=false
gretel_pilot_opened=false
```

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
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
python -m zipfile --test Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
```
