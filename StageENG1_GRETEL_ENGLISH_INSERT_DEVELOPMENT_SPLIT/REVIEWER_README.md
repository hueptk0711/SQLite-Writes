# StageENG1 Gretel English INSERT Development Split

This reviewer package freezes the StageENG1 split over the StageENG0 primary
English INSERT development population.

Review order:

1. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_SPLIT_POLICY.json`
2. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/STAGE0_INPUT_HASHES.json`
3. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl`
4. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl`
5. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl`
6. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/SPLIT_GROUP_AUDIT.json`
7. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DUPLICATE_AUDIT.json`
8. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/OFFICIAL_TEST_ISOLATION_AUDIT.json`
9. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DERIVED_ARTIFACT_MANIFEST.json`
10. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/STAGEENG1_LOCK.json`
11. `scripts/data/build_stageeng1_development_split.py`
12. `scripts/data/validate_stageeng1_development_split.py`
13. `tests/test_stageeng1_development_split.py`
14. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/VALIDATION_REPORT.md`

The split contains 828 development-train samples, a held-out 100-sample
development-dev split, and a locked 100-sample pilot pool selected from
development-train. The 51 official-test confirmation rows remain excluded and
confirmation-only.

Rerun:

```bash
uv run --with pyarrow python scripts/data/build_stageeng1_development_split.py \
  --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \
  --raw-dir /path/to/gretel_raw \
  --out-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT
uv run --with pyarrow python scripts/data/validate_stageeng1_development_split.py \
  --stage1-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT \
  --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \
  --raw-dir /path/to/gretel_raw
python -m pytest -q tests/test_stageeng1_development_split.py
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT
```
