# Stage 6B CRUDSQL Confirmation Dataset Registration

Status: registered pending reviewer acceptance.

This package is CPU-only. It freezes all 500 official CRUDSQL test `type=0`
Create examples, 125 isolated single-table SQLite databases, deterministic gold
write plans/programs, post-state hashes, overlap registry, and gold-review
protocol. It does not call a model and does not allow GPU confirmation yet.

Important lock:

```text
N = 500
source = CRUDSQL commit 63bfce67d8391185453a812751e115a499201363
split = official test
subset = all type=0 Create examples
sampling = none
confirmation_run_allowed_now = false
```

Run validation:

```bash
python scripts/data/validate_stage6b_registration.py --registration-dir stage6_crudsql_registration
PYTHONPATH=tests/support/windows_py314_pytest_tempdir \
python -m pytest -q tests/test_stage6b_registration.py --basetemp pytest_tmp_stage6b_tests
```
