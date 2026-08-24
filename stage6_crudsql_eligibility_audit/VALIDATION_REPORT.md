# Stage 6A CRUDSQL Eligibility Validation Report

Status: PASS

Validation date: 2026-08-24

## Scope

Stage 6A is an eligibility audit only. It does not register the confirmation
dataset, does not call a model, does not run GPU inference, and does not edit
Stage 5 method/protocol files.

## Commands

```text
git ls-remote https://github.com/bizard-lab/CRUDSQL.git HEAD
```

Result:

```text
63bfce67d8391185453a812751e115a499201363
```

```text
git clone https://github.com/bizard-lab/CRUDSQL.git D:\paper kltn\text to sql\external_sources\CRUDSQL_63bfce67
git checkout 63bfce67d8391185453a812751e115a499201363
```

Result: source checkout clean.

```text
python scripts\data\audit_crudsql_stage6a.py --crudsql-root "D:\paper kltn\text to sql\external_sources\CRUDSQL_63bfce67" --out-dir stage6_crudsql_eligibility_audit
```

Result: PASS.

```text
$env:PYTHONPATH = "tests\support\windows_py314_pytest_tempdir"
python -m pytest -q tests\test_stage6_crudsql_eligibility.py --basetemp pytest_tmp_stage6a_fresh
```

Result: PASS, 2 tests passed.

On Windows Python 3.14, the explicit support shim avoids restrictive temp
directory permissions observed in this local environment. The fresh
`--basetemp` path avoids stale locked temp directories from earlier runs.

## Findings

- Official test split has 2,000 examples.
- Official test split has 500 `type=0` Create examples.
- All 500 `type=0` examples compile and execute as deterministic SQLite INSERT
  operations on an in-memory DB copy.
- `train` and `dev` were counted for audit only and are not recommended for
  registration.
- SQLite `train.db`, `dev.db`, and `test.db` open and pass `PRAGMA
  integrity_check`.
- Overlap audit against available Stage4 fresh, final holdout, and archived 677
  pool artifacts reports zero overlap by sample ID, source group, database ID,
  input-text SHA-256, and canonical-content SHA-256.
- Sample-size sensitivity recommends using all 500 eligible official test
  `type=0` examples after reviewer acceptance.

## Decision

```text
status = PASS_ELIGIBLE_FOR_STAGE6B_REGISTRATION
registration_status = not_registered_in_stage6a
recommended_registration_n = 500
recommended_sampling_policy = use_all_eligible_official_test_type0_examples_no_random_sampling
model_called = false
gpu_called = false
```
