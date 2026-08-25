# Stage6F GPU Preflight Acceptance Validation Report

Validation date: 2026-08-25

Server output ZIP SHA-256:

```text
2c349435bc7b4f509a64135e4ad34c14db5d49fde499373b1195b6eebdffd0ab
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

No confirmatory inference was run.
