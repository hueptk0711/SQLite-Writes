# Stage 6B CRUDSQL Registration Validation Report

Status: PASS

Validation date: 2026-08-24

Stage6B registers all 500 official CRUDSQL test Create examples. It does not
call Qwen, does not run GPU inference, and does not permit confirmation runs
yet.

Key results:

- registered samples: 500
- isolated SQLite DBs: 125
- gold write plans: 500
- gold programs: 500
- post-state hashes: 500
- prior input-text hashes: 965
- overlap counts: all zero, including database ID namespace
- dataset archive SHA-256: 348c365ee60afb20ded0773e8f78d5a941033ae4870f29d8654a6538f4983fce
- confirmation_run_allowed_now: false

Tests:

- `python scripts/data/validate_stage6b_registration.py --registration-dir stage6_crudsql_registration`
- `python -m pytest -q tests/test_stage6b_registration.py`
