# Stage7E0-A5 English Column-Conditioned Real Generation Preflight Validation Report

Status: PASS_READY_FOR_REAL_A5_CONSTRAINED_PREFLIGHT

Validation date: 2026-09-01

## Scope

This package prepares the first real Qwen/GPU run for the 12 locked Stage7C-A5
primary column-conditioned cases. Local validation uses a disclosed mock backend
only to test wiring and does not claim scientific model evidence.

```text
accepted_protocol_commit=1b68ef5ff1bfdc52de05da7ae6fd96857c783f63
model=Qwen/Qwen2.5-Coder-7B-Instruct
revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
primary_cases=12
diagnostic_cases=12 after primary freeze only
acceptance=12/12 required
phase_o_output=operation + table_ref + column_span_refs
phase_m_removed=true
candidate_generator=lexical_ngram2
backend=incremental_json_schema_grammar
do_sample=false
retry=0
repair=none
quantization=none
phase_o_max_new_tokens=512
primary_runtime_profile_id=kaggle_t4x2_cuda130
kaggle_requirements_lock=requirements-inference-kaggle-t4x2.lock.txt
gretel_pilot_opened=false
```

## Local Mock Dry-Run

```text
backend=mock
status=PASS
primary_pass_count=12/12
model_called=false
gpu_called=false
mock_uses_label_side_expected=true
```
