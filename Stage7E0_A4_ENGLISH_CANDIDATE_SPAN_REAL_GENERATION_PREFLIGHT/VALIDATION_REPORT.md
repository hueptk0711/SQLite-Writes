# Stage7E0-A4 English Candidate-Span Real Generation Preflight Validation Report

Status: PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT

Validation date: 2026-09-01

## Scope

This package prepares the first real Qwen/GPU run for the 10 locked Stage7C-A4
candidate-span cases. Local build validation uses a disclosed mock backend only
to test wiring and does not claim scientific model evidence.

```text
accepted_protocol_commit=b1cf3e0113f477810c4b1ad8996c1ca6ea0b39b6
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
primary_runtime_profile_id=kaggle_t4x2_cuda130
kaggle_requirements_lock=requirements-inference-kaggle-t4x2.lock.txt
single_primary_runtime_profile=true
runtime_profile_switch_after_completed_generation_allowed=false
gpu_topology_fail_fast_before_model_load=true
allowed_runtime_profiles=[{"gpu_requirement":"cuda_available=true","packages":{"accelerate":["1.14.0"],"safetensors":["0.5.3"],"tokenizers":["0.22.2"],"torch":["2.6.0+cu124"],"transformers":["5.5.3"]},"profile_id":"uet_server_cuda124","role":"historical_reference_only","torch_cuda":"12.4"},{"cuda_available":true,"gpu_count":2,"gpu_device_substring":"Tesla T4","gpu_requirement":"two Tesla T4 devices expected on Kaggle","packages":{"accelerate":["1.14.0"],"safetensors":["0.5.3"],"tokenizers":["0.22.2"],"torch":["2.13.0","2.13.0+cu130"],"transformers":["5.5.3"]},"profile_id":"kaggle_t4x2_cuda130","role":"primary_scientific_runtime","torch_cuda":"13.0"}]
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
python scripts/server/preflight_runtime.py --expected-profile kaggle_t4x2_cuda130
python -m pytest -q tests/test_stage7e0_a4_english_preflight.py
python -m zipfile --test Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH3_FINAL_REVIEWER_PACKAGE_20260901.zip
```
