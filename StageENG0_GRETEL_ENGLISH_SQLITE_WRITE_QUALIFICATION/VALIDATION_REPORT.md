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
DML-leading candidate population  9909
Single-statement DML              9696
DML-leading multi-statement       213
+-- INSERT                       3454
+-- UPDATE                       3028
+-- DELETE                       3427

SQLite-compatible                9158
Gold-executable                  8476
Deterministic                    8476

Source-alignable                 4680
Derived/non-alignable            3796

Primary eligible INSERT          979
Secondary eligible UPDATE        2671
Secondary eligible DELETE        2808
```

## PATCH1 INSERT Grounding Funnel

```text
Single-row INSERT eligible              2410
Direct-literal representable            2290
Individually source-alignable            984
Jointly source-representable             979
Development train candidates             928
Official test confirmation candidates    51
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
  "derived_value": 359,
  "implicit_value": 3998,
  "no_gold_literals": 348,
  "source_alignable_literal": 5204
}
```

## Validation Commands

```text
uv run --with pyarrow python scripts/data/build_stageeng0_gretel_qualification.py --raw-dir <raw_dir> --out-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --package StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
uv run --with pyarrow python scripts/data/validate_stageeng0_gretel_qualification.py --stage-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir>
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q tests/test_stageeng0_gretel_qualification.py --basetemp .codex_tmp/pytest_stageeng0_tests5
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q -m "not integration" --basetemp .codex_tmp/pytest_stageeng0_regression
python -m zipfile --test StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
```

Results:

```text
build: PASS
validator: PASS
dedicated tests: PASS, 68 tests
regression tests: PASS, non-integration suite
zip integrity: PASS
derived artifact manifest: PASS
```

## Guardrails

```text
model_called=false
gpu_called=false
raw_data_modified=false
model_performance_filtering=false
official_test_tuning=false
```
