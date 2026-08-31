# Stage7E0-A4 English Candidate-Span Real Generation Preflight Validation Report

Status: PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT

Validation date: 2026-08-31

## Scope

This package prepares the first real Qwen/GPU run for the 10 locked Stage7C-A4
candidate-span cases. Local build validation uses a disclosed mock backend only
to test wiring and does not claim scientific model evidence.

```text
accepted_protocol_commit=8512fbd42886934648c64aa867c710ba48faa827
model=Qwen/Qwen2.5-Coder-7B-Instruct
revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
primary_cases=10
acceptance=10/10 required
phase_o_output=operation + span_refs only
candidate_generator=lexical_ngram2
backend=incremental_json_schema_grammar
do_sample=false
retry=0
repair=none
quantization=none
phase_o_max_new_tokens=512
phase_m_max_new_tokens=8192
gretel_pilot_opened=false
```

## Local Mock Dry-Run

```text
backend=mock
status=PASS
primary_pass_count=10/10
model_called=false
gpu_called=false
mock_uses_label_side_expected=true
```

## Validation Commands

```text
python scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py --stage-dir Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL
python scripts/data/validate_stage7e0_a4_english_preflight.py --stage-dir Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a4_english_preflight.py
python -m zipfile --test Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260831.zip
```
