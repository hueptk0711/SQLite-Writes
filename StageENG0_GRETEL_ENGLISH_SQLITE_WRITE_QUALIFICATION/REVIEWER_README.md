# StageENG0 Gretel English SQLite Write Qualification

This reviewer package contains the StageENG0 dataset qualification artifacts
for `gretelai/synthetic_text_to_sql` at revision `740ab236e64503fba51be1101df7a1be83bf455d`.

Review order:

1. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/DATASET_SOURCE_LOCK.json`
2. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/ELIGIBILITY_POLICY.json`
3. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/DML_OPERATION_COUNTS.json`
4. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/SQLITE_COMPATIBILITY_SUMMARY.json`
5. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/GOLD_EXECUTION_AUDIT.jsonl`
6. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/SOURCE_ALIGNABILITY_SUMMARY.json`
7. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/EXCLUSION_LEDGER.jsonl`
8. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/ELIGIBLE_INSERT_MANIFEST.jsonl`
9. `scripts/data/build_stageeng0_gretel_qualification.py`
10. `scripts/data/validate_stageeng0_gretel_qualification.py`
11. `tests/test_stageeng0_gretel_qualification.py`
12. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/VALIDATION_REPORT.md`

Rerun:

```bash
uv run --with pyarrow python scripts/data/build_stageeng0_gretel_qualification.py \
  --raw-dir /path/to/gretel_raw \
  --out-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \
  --download
python scripts/data/validate_stageeng0_gretel_qualification.py \
  --stage-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \
  --raw-dir /path/to/gretel_raw
python -m pytest -q tests/test_stageeng0_gretel_qualification.py
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION
```
