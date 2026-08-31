# Stage7E0-A3 English Real Generation Preflight PATCH3

This reviewer package hardens the accepted PATCH9 constrained backend before
running the eight fresh Stage7C-A3 English cases on GPU. It keeps the prompt,
cases, schemas, Phase M, model revision, no-repair/no-retry policy, and decoder
method unchanged, while making the package self-contained and the post-run
validator outcome-generic.

Clean extraction checks:

```bash
python scripts/data/validate_stage7e0_a3_english_preflight.py --stage-dir Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python scripts/data/validate_stage7e0_a3_server_results.py --stage-dir Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
python -m pytest -q tests/test_stage7e0_a3_patch2_constrained_backend.py
python -m pytest -q tests/test_stage7e0_a3_patch3_protocol_hardening.py
```

Server execution commands are in:

```text
Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.md
```

Package:

```text
Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH3_FINAL_REVIEWER_PACKAGE_20260831.zip
```

Accepted commit:

```text
ca5aab5629a62a702f06dea7f9752702a8d2314f
```
