# StageENG0 Gretel English SQLite Write Qualification Validation Report

Status: PASS

Validation date: 2026-08-30

## Scope

StageENG0 is a CPU-only dataset qualification stage. It does not call Qwen,
does not run GPU inference, does not amend Phase O prompts, and does not score
model accuracy. Raw Gretel parquet files are pinned by dataset revision and
SHA-256 outside the repository; derived audit artifacts are written here.

## Funnel

```text
Raw total                         105851
DML                              9909
+-- INSERT                       3454
+-- UPDATE                       3028
+-- DELETE                       3427

SQLite-compatible                9158
Gold-executable                  8476
Deterministic                    8476

Source-alignable                 4482
Derived/non-alignable            3994

Primary eligible INSERT          952
Secondary eligible UPDATE        2671
Secondary eligible DELETE        2808
```

## Counts

SQLite context status:

```json
{
  "failure": 538,
  "not_run": 213,
  "success": 9158
}
```

Source alignability:

```json
{
  "ambiguous_multiple_occurrences": 559,
  "derived_value": 338,
  "implicit_value": 3673,
  "no_gold_literals": 337,
  "source_alignable_literal": 5002
}
```

## Validation Commands

```text
uv run --with pyarrow python scripts/data/build_stageeng0_gretel_qualification.py --raw-dir <raw_dir> --out-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --package StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
python scripts/data/validate_stageeng0_gretel_qualification.py --stage-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir>
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q tests/test_stageeng0_gretel_qualification.py --basetemp .codex_tmp/pytest_stageeng0_tests5
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q -m "not integration" --basetemp .codex_tmp/pytest_stageeng0_regression
python -m zipfile --test StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
```

Results:

```text
build: PASS
validator: PASS
dedicated tests: PASS, 40 tests
regression tests: PASS, non-integration suite
zip integrity: PASS
```

## Guardrails

```text
model_called=false
gpu_called=false
raw_data_modified=false
model_performance_filtering=false
official_test_tuning=false
```
