# Stage7E0-A4 English Candidate-Span Kaggle Primary Run Commands

Stage7E0-A4 PATCH3 locks the primary scientific runtime to:

```text
primary_runtime_profile_id=kaggle_t4x2_cuda130
```

Run this as a Kaggle notebook Bash cell after adding this reviewer package as a
Kaggle dataset:

```bash
%%bash
set -euo pipefail
cd /kaggle/working
rm -rf Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH3_runner
mkdir -p Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH3_runner
PKG_ROOT="$(find /kaggle/input -type d -name 'Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH3_FINAL_REVIEWER_PACKAGE_*' -print -quit)"
test -n "$PKG_ROOT"
cp -a "$PKG_ROOT"/. Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH3_runner/
cd Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH3_runner
export HF_HOME=/kaggle/working/hf_cache
python -m pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r requirements-inference-kaggle-t4x2.lock.txt
python scripts/data/validate_stage7e0_a4_english_preflight.py --stage-dir Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT
python scripts/server/preflight_runtime.py --expected-profile kaggle_t4x2_cuda130
python scripts/server/run_stage7e0_a4_english.py \
  --accepted-protocol-commit b1cf3e0113f477810c4b1ad8996c1ca6ea0b39b6 \
  --result-root /kaggle/working/stage7e0_a4_english_candidate_span_kaggle_t4x2_results_20260901 \
  --backend constrained_hf \
  --quantization none \
  --phase-o-max-new-tokens 512 \
  --phase-m-max-new-tokens 8192 \
  --model-name-or-path Qwen/Qwen2.5-Coder-7B-Instruct
python scripts/data/validate_stage7e0_a4_server_results.py --result-dir /kaggle/working/stage7e0_a4_english_candidate_span_kaggle_t4x2_results_20260901
```

Do not use `--resume`. If infrastructure interrupts before any completed
primary generation, archive the partial output as infrastructure-aborted and
rerun in a new empty result root. Do not switch to another runtime profile after
observing any primary semantic output.
