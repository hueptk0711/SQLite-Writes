#!/usr/bin/env bash
set -euo pipefail

cd /home/uet/hue_ptk
conda activate stage7e0_a7_py311
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
RUNNER="StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT_runner"
RESULT_ROOT="/home/uet/hue_ptk/stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903"
MODEL_SNAPSHOT="/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242"

cd "$RUNNER"
python scripts/data/validate_stageeng2a_gretel_external_development_pilot.py --stage-dir StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT
rm -rf "$RESULT_ROOT"
CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES" python scripts/server/run_stageeng2a_gretel_pilot.py \
  --stage-dir StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT \
  --result-root "$RESULT_ROOT" \
  --backend hf \
  --model-name-or-path "$MODEL_SNAPSHOT" \
  --max-new-tokens 512 \
  --phase-o-max-new-tokens 512 \
  --max-input-tokens 28672 \
  --seed 42 \
  --trust-remote-code

cd /home/uet/hue_ptk
tar -czf stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903.tar.gz stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903
sha256sum stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903.tar.gz > stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903.tar.gz.sha256
python - <<'PY'
import tarfile
name = "stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903.tar.gz"
with tarfile.open(name, "r:gz") as archive:
    members = archive.getmembers()
print(f"tar_ok members={len(members)} archive={name}")
PY
