#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export CUDA_VISIBLE_DEVICES=0
RESULT_ROOT="/home/uet/hue_ptk/stage7e0_a7_final_a5_uet_rtx4090_primary_results_20260903"

python scripts/server/preflight_runtime_stage7e0_a6.py --expected-profile uet_rtx4090_cuda124_visible0
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY
python scripts/server/run_stage7e0_a7_english.py \
  --accepted-protocol-commit 1b73498d64ac2e233ba953e8fdf3cc98992026ea \
  --result-root "$RESULT_ROOT" \
  --backend constrained_hf \
  --model-name-or-path "/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242" \
  --quantization none \
  --phase-o-max-new-tokens 512
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY --result-dir "$RESULT_ROOT"
tar -C "$(dirname "$RESULT_ROOT")" -czf "/home/uet/hue_ptk/stage7e0_a7_final_a5_uet_rtx4090_primary_results_20260903.tar.gz" "$(basename "$RESULT_ROOT")"
sha256sum "/home/uet/hue_ptk/stage7e0_a7_final_a5_uet_rtx4090_primary_results_20260903.tar.gz" > "/home/uet/hue_ptk/stage7e0_a7_final_a5_uet_rtx4090_primary_results_20260903.tar.gz.sha256"
