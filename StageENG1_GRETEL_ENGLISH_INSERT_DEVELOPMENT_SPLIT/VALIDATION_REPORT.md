# StageENG1 Gretel English INSERT Development Split Validation Report

Status: PASS

Validation date: 2026-08-30

## Scope

StageENG1 freezes a leakage-guarded development split over the 928
StageENG0 `development_allowed=true` primary English INSERT samples. It does
not run Qwen, does not use GPU inference, does not score model outputs, and
does not include the 51 official-test confirmation rows.

## Frozen Counts

```text
StageENG0 development candidates     928
StageENG0 official confirmation       51
Development train                     828
Development dev                       100
Development train pilot pool          100
Leakage components                    791
Cross-split signature violations      0
```

## Duplicate Audit

```json
{
  "context_hash": 99,
  "normalized_prompt_hash": 0,
  "prompt_hash": 0,
  "schema_database_group": 99,
  "source_row_key": 0,
  "sql_hash": 0,
  "sql_template_hash": 112
}
```

## Validation Commands

```text
uv run --with pyarrow python scripts/data/build_stageeng1_development_split.py --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir> --out-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT --package StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
uv run --with pyarrow python scripts/data/validate_stageeng1_development_split.py --stage1-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir>
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q tests/test_stageeng1_development_split.py
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q -m "not integration"
python -m zipfile --test StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
```

## Guardrails

```text
model_called=false
gpu_called=false
official_test_tuning=false
official_test_confirmation_rows_in_split=0
pilot_subset_of_development_train=true
pilot_intersects_development_dev=false
```
