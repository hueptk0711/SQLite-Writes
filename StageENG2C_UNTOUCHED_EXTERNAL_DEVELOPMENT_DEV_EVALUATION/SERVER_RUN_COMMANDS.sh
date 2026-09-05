#!/usr/bin/env bash
set -euo pipefail

STAGE="StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION"
RESULT_DIR="/home/uet/hue_ptk/stageeng2c_untouched_dev100_uet_rtx4090_results_20260905"
ARCHIVE="stageeng2c_untouched_dev100_uet_rtx4090_results_20260905.tar.gz"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
python -m py_compile scripts/server/run_stageeng2c_dev100_evaluation.py scripts/server/run_eng2_final_method.py scripts/data/validate_stageeng2c_untouched_dev_evaluation.py
python scripts/data/validate_stageeng2c_untouched_dev_evaluation.py --stage-dir "$STAGE" --skip-official
python scripts/server/run_stageeng2c_dev100_evaluation.py --stage-dir "$STAGE" --result-root "/home/uet/hue_ptk/eng2c_dry_live_config" --dry-run-live-config

rm -rf "$RESULT_DIR"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" python scripts/server/run_stageeng2c_dev100_evaluation.py \
  --stage-dir "$STAGE" \
  --result-root "$RESULT_DIR" \
  --backend hf

python scripts/data/validate_stageeng2c_untouched_dev_evaluation.py --stage-dir "$STAGE" --official-result-root "$RESULT_DIR" --require-official

cd "/home/uet/hue_ptk"
tar -czf "$ARCHIVE" "stageeng2c_untouched_dev100_uet_rtx4090_results_20260905"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
python - <<'PY'
import tarfile
name = "stageeng2c_untouched_dev100_uet_rtx4090_results_20260905.tar.gz"
with tarfile.open(name, "r:gz") as archive:
    members = archive.getmembers()
print(f"tar_ok members={len(members)} archive={name}")
PY
