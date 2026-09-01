# Stage7E0-A5 English Column-Conditioned UET RTX4090 Commands

Run the primary set first. Do not run diagnostics before the primary result is
frozen and reviewed. A completed primary result is preserved whether it is
12/12 PASS or a protocol-compliant scientific FAIL below 12/12.

```bash
set -euo pipefail
cd /home/uet/hue_ptk

if command -v conda >/dev/null 2>&1; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  if ! conda env list | awk '{print $1}' | grep -qx stage7e0_a5_uet_py312; then
    conda create -y -n stage7e0_a5_uet_py312 python=3.12
  fi
  conda activate stage7e0_a5_uet_py312
fi

python -m pip install --upgrade pip

rm -rf Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH3_runner
mkdir -p Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH3_runner
unzip -q -o Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH3_FINAL_REVIEWER_PACKAGE_20260901.zip -d Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH3_runner
cd Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH3_runner
python -m pip install --extra-index-url https://download.pytorch.org/whl/cu124 -r requirements-inference-uet-rtx4090-cu124.lock.txt

export CUDA_VISIBLE_DEVICES=0
export HF_HOME=/home/uet/hue_ptk/hf_cache
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py --stage-dir Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE
python scripts/data/validate_stage7e0_a5_english_preflight.py --stage-dir Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT
python scripts/server/preflight_runtime_stage7e0_a5.py --expected-profile uet_rtx4090_cuda124_visible0

MODEL_SNAPSHOT="/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242"
if [ ! -d "$MODEL_SNAPSHOT" ]; then
  MODEL_SNAPSHOT="Qwen/Qwen2.5-Coder-7B-Instruct"
fi

python scripts/server/run_stage7e0_a5_english.py \
  --accepted-protocol-commit 1b68ef5ff1bfdc52de05da7ae6fd96857c783f63 \
  --result-root /home/uet/hue_ptk/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901 \
  --backend constrained_hf \
  --quantization none \
  --phase-o-max-new-tokens 512 \
  --model-name-or-path "$MODEL_SNAPSHOT"
python scripts/data/validate_stage7e0_a5_server_results.py --result-dir /home/uet/hue_ptk/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901
tar -czf /home/uet/hue_ptk/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz -C /home/uet/hue_ptk stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901
sha256sum /home/uet/hue_ptk/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz > /home/uet/hue_ptk/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz.sha256
```

Do not use `--resume`. If infrastructure interrupts, archive the partial output
as infrastructure-aborted and rerun in a new empty result root. If the primary
run completes with less than 12/12, keep running the validator, archive, and
sha256 commands above; that is a completed scientific result, not an
infrastructure failure. Diagnostics are not part of this primary preflight
command; run them only after the primary result is frozen and reviewed as 12/12
PASS.
