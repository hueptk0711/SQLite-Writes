#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PACKAGE_ROOT"

mkdir -p /home/uet/hue_ptk

if command -v conda >/dev/null 2>&1; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  if ! conda env list | awk '{print $1}' | grep -qx stage7e0_a5_uet_py312; then
    conda create -y -n stage7e0_a5_uet_py312 python=3.12
  fi
  conda activate stage7e0_a5_uet_py312
fi

python -m pip install --upgrade pip
python -m pip install --extra-index-url https://download.pytorch.org/whl/cu124 -r requirements-inference-uet-rtx4090-cu124.lock.txt

export CUDA_VISIBLE_DEVICES=0
export HF_HOME=/home/uet/hue_ptk/hf_cache

RESULT_ROOT="/home/uet/hue_ptk/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901"
if [ -e "$RESULT_ROOT" ]; then
  echo "STOP: result root already exists: $RESULT_ROOT" >&2
  echo "Archive it as infrastructure-aborted if incomplete, or choose a reviewed new run directory." >&2
  exit 2
fi

python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py --stage-dir Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE
python scripts/data/validate_stage7e0_a5_english_preflight.py --stage-dir Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT
python scripts/server/preflight_runtime_stage7e0_a5.py --expected-profile uet_rtx4090_cuda124_visible0

MODEL_SNAPSHOT="/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242"
if [ ! -d "$MODEL_SNAPSHOT" ]; then
  MODEL_SNAPSHOT="Qwen/Qwen2.5-Coder-7B-Instruct"
fi

python scripts/server/run_stage7e0_a5_english.py \
  --accepted-protocol-commit 1b68ef5ff1bfdc52de05da7ae6fd96857c783f63 \
  --result-root "$RESULT_ROOT" \
  --backend constrained_hf \
  --quantization none \
  --phase-o-max-new-tokens 512 \
  --model-name-or-path "$MODEL_SNAPSHOT"
python scripts/data/validate_stage7e0_a5_server_results.py --result-dir "$RESULT_ROOT"
tar -czf "$RESULT_ROOT.tar.gz" -C /home/uet/hue_ptk stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901
sha256sum "$RESULT_ROOT.tar.gz" > "$RESULT_ROOT.tar.gz.sha256"
