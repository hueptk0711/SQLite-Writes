# Stage7E0-A3 English Real Generation Preflight PATCH0

This reviewer package prepares the first stage allowed to call Qwen on the
eight fresh Stage7C-A3 English cases. It wires Phase O to the exact accepted A3
prompt spec and keeps Phase M, V2-A1 materialization, completeness, compiler,
preflight, backend, retry, and repair policy unchanged.

Clean extraction checks:

```bash
python scripts/data/validate_stage7e0_a3_english_preflight.py --stage-dir Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
```

Server execution commands are in:

```text
Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.md
```

Package:

```text
Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
```

Accepted commit:

```text
ab006242bc498c343fe9573c893283a9733bcc1f
```
