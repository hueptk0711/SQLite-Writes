# StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION PATCH0

This package freezes the untouched Gretel development-dev 100-sample ENG2C protocol before any official model call.

It authorizes exactly one official server run for four arms:

- M0_DIRECT_ZERO
- M0_DIRECT_FS
- M1_J_FS
- M2_FINAL_ENG2B

Primary metric: strict full-state accuracy across all persistent user tables.

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
