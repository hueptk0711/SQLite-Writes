# Stage7E0-A6 English Atomic-Domain Column-Conditioned Real Generation Preflight Validation Report

Status: PASS_READY_FOR_REAL_A6_CONSTRAINED_PREFLIGHT

Validation date: 2026-09-02

## Scope

This package prepares the first real Qwen/GPU run for the 12 locked Stage7C-A6
primary column-conditioned cases. Local validation uses a disclosed mock backend
only to test wiring and does not claim scientific model evidence.

```text
accepted_protocol_commit=e1f4b4b73fdaeb6a2235c1d96e4928ce8736bc49
model=Qwen/Qwen2.5-Coder-7B-Instruct
revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
primary_cases=12
diagnostic_cases=36 after primary freeze and review only
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
primary_runtime_profile_id=uet_rtx4090_cuda124_visible0
server_requirements_lock=requirements-inference-uet-rtx4090-cu124.lock.txt
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
