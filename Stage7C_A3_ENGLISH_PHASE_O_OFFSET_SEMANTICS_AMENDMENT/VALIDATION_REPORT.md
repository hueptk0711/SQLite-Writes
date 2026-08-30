# Stage7C A3 English Phase O Offset-Semantics Amendment PATCH1 Validation Report

Status: PASS

Validation date: 2026-08-30

## Scope

PATCH1 rewires the A3 amendment to the frozen V2-A1 A2 Phase O prompt spec.
The A2 system prompt is unchanged. The A3 user prompt template is A2 plus only
the offset-semantics block. Phase M, offset-guide serialization, model/backend,
materializer, compiler, preflight, metrics, datasets, and gold labels remain
unchanged.

## Frozen Smoke Set

```text
fresh English cases        8
expected Phase O spans     28
V2 oracle path             8/8 ADMITTED and exact target state
offset contract            start inclusive, end exclusive
slice oracle               Q[start_char:end_char]
```

## Guardrails

```text
same Qwen2.5-Coder-7B=true
same revision=true
same 2-call architecture=true
same Phase M=true
same PATCH9 incremental backend=true
zero_shot=true
retry=0
repair=none
model_called=false
gpu_called=false
gretel_pilot_opened=false
```

## Validation Commands

```text
python scripts/data/build_stage7c_a3_english_offset_semantics.py --out-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT --package Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
python -m pytest -q -m "not integration"
python -m zipfile --test Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
```
