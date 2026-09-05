# StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION PATCH2

This package freezes and reports the untouched Gretel development-dev 100-sample ENG2C protocol after the official UET RTX4090 model run.

It authorizes exactly one official server run for four arms:

- M0_DIRECT_ZERO
- M0_DIRECT_FS
- M1_J_FS
- M2_FINAL_ENG2B

Primary metric: strict full-state accuracy across all persistent user tables.

Official server result root: `StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION/official_server_run`

Official strict full-state accuracy:

- M0_DIRECT_ZERO: 95/100
- M0_DIRECT_FS: 96/100
- M1_J_FS: 92/100
- M2_FINAL_ENG2B: 88/100

Local reviewer checks:

```bash
python scripts/data/validate_stageeng2c_untouched_dev_evaluation.py --stage-dir StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION --skip-official
python -m pytest -q tests/test_stageeng2c_untouched_dev_evaluation.py
python scripts/server/run_stageeng2c_dev100_evaluation.py --stage-dir StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION --result-root tmp_eng2c_mock_verify --backend mock
python scripts/server/run_stageeng2c_dev100_evaluation.py --stage-dir StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION --result-root tmp_eng2c_dry_config --dry-run-live-config
sha256sum -c StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION/SHA256SUMS
```

Run the GPU evaluation on UET with:

```bash
bash StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION/SERVER_RUN_COMMANDS.sh
```
