# Stage7E0-A4 English Candidate-Span Server Run Commands

Upload from Windows:

```powershell
cd "D:\paper kltn\text to sql\github_publish\SQLite-Writes"
scp "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_FINAL_REVIEWER_PACKAGE_20260831.zip" uet@222.255.250.24:/home/uet/hue_ptk/
scp "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_FINAL_REVIEWER_PACKAGE_20260831.zip.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
```

Run on the GPU server:

```bash
ssh uet@222.255.250.24
cd /home/uet/hue_ptk
sha256sum -c Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_FINAL_REVIEWER_PACKAGE_20260831.zip.sha256
rm -rf Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_runner
mkdir -p Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_runner
unzip -q Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_FINAL_REVIEWER_PACKAGE_20260831.zip -d Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_runner
cd Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH1_runner
export HF_HOME="$HOME/hue_ptk/hf_cache"
export TRANSFORMERS_OFFLINE=1
PY="${PY:-/home/uet/miniconda3/envs/stage7e0/bin/python}"
"$PY" scripts/server/run_stage7e0_a4_english.py \
  --accepted-protocol-commit fe90bf2183ea5eb812367f608997150788f6ad6e \
  --result-root /home/uet/hue_ptk/stage7e0_a4_english_candidate_span_constrained_results_20260831 \
  --backend constrained_hf \
  --quantization none \
  --phase-o-max-new-tokens 512 \
  --phase-m-max-new-tokens 8192 \
  --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242
```

Do not use `--resume`. If infrastructure interrupts the job, archive the
partial output and rerun in a new empty result root.

Copy results back:

```powershell
scp -r uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_a4_english_candidate_span_constrained_results_20260831 .
```

Validate copied results:

```bash
python scripts/data/validate_stage7e0_a4_server_results.py --result-dir stage7e0_a4_english_candidate_span_constrained_results_20260831
```
