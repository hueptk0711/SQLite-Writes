# Stage7E0-A3 English Real Generation Preflight PATCH2

This reviewer package restores the accepted PATCH9 constrained backend for the
eight fresh Stage7C-A3 English cases. It wires Phase O to the exact accepted A3
prompt spec, keeps Phase M and the V2-A1 materialization/compiler/preflight path
unchanged, and forbids plain HF fallback, repair, retry, and 4-bit quantization.

Clean extraction checks:

```bash
python scripts/data/validate_stage7e0_a3_english_preflight.py --stage-dir Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
python -m pytest -q tests/test_stage7e0_a3_patch2_constrained_backend.py
```

Server execution commands are in:

```text
Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.md
```

Package:

```text
Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_FINAL_REVIEWER_PACKAGE_20260831.zip
```

Accepted commit:

```text
30dd861ac52df8c1e04070f1dc807a5032591bdc
```
