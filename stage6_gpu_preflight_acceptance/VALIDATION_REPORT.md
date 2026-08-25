# Stage6F GPU Preflight Acceptance PATCH2 Validation Report

Validation date: 2026-08-25

Server output ZIP SHA-256:

```text
004913b1778cc145d44f32aa49a60039438b24cc36d1f17a5c85091e8cc5bd1b
```

Server-side validator result:

```text
python scripts/data/validate_stage6f_gpu_preflight.py --preflight-dir $OUT_DIR --require-gpu-pass
status = PASS
violations = []
```

Local ingestion validator result:

```text
python scripts/data/validate_stage6f_gpu_preflight_acceptance.py --acceptance-dir stage6_gpu_preflight_acceptance
status = PASS
violations = []
```

Dedicated tests:

```text
python -m pytest -q tests/test_stage6f_gpu_preflight.py tests/test_stage6f_gpu_preflight_acceptance.py
14 passed
```

PATCH2 hardening checks:

```text
model aggregate SHA-256 = PASS
tokenizer SHA-256       = PASS
model config SHA-256    = PASS
SQLite runtime captured = PASS
CUDA visibility captured = PASS
synthetic smoke          = PASS
synthetic confirmation samples used = 0
H2 input-ID identity     = 481/481 PASS
nested server ZIP SHA    = PASS
extracted mirror vs ZIP  = PASS
```

No confirmatory inference was run.
