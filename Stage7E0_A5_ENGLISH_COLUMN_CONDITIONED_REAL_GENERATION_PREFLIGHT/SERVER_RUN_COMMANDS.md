# Stage7E0-A5 English Column-Conditioned Kaggle T4x2 Commands

Run the primary set first. Do not run diagnostics before the primary result is
frozen and reviewed.

```bash
%%bash
set -euo pipefail
cd /kaggle/working
rm -rf Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH0_runner
mkdir -p Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH0_runner
PKG_ROOT="$(find /kaggle/input -type d -name 'Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_*' -print -quit)"
test -n "$PKG_ROOT"
cp -a "$PKG_ROOT"/. Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH0_runner/
cd Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH0_runner
export HF_HOME=/kaggle/working/hf_cache
python -m pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r requirements-inference-kaggle-t4x2.lock.txt
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py --stage-dir Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE
python scripts/data/validate_stage7e0_a5_english_preflight.py --stage-dir Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT
python scripts/server/preflight_runtime_stage7e0_a5.py --expected-profile kaggle_t4x2_cuda130
python scripts/server/run_stage7e0_a5_english.py \
  --accepted-protocol-commit 1b68ef5ff1bfdc52de05da7ae6fd96857c783f63 \
  --result-root /kaggle/working/stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901 \
  --backend constrained_hf \
  --quantization none \
  --phase-o-max-new-tokens 512 \
  --model-name-or-path Qwen/Qwen2.5-Coder-7B-Instruct
python scripts/data/validate_stage7e0_a5_server_results.py --result-dir /kaggle/working/stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901
tar -czf /kaggle/working/stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901.tar.gz -C /kaggle/working stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901
sha256sum /kaggle/working/stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901.tar.gz > /kaggle/working/stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901.tar.gz.sha256
```

Do not use `--resume`. If infrastructure interrupts, archive the partial output
as infrastructure-aborted and rerun in a new empty result root. Diagnostics are
not part of this primary preflight command; run them only after the primary
result is frozen and reviewed as 12/12 PASS.
