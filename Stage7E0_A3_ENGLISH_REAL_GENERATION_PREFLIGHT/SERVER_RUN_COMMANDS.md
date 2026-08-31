# Stage7E0-A3 English Server Run Commands

Run these on the Windows machine to upload the package:

```powershell
scp "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_FINAL_REVIEWER_PACKAGE_20260831.zip" uet@222.255.250.24:/home/uet/hue_ptk/
scp "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_FINAL_REVIEWER_PACKAGE_20260831.zip.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
```

Run these on the GPU server:

```bash
ssh uet@222.255.250.24
cd /home/uet/hue_ptk
sha256sum -c Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_FINAL_REVIEWER_PACKAGE_20260831.zip.sha256
rm -rf Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_runner
mkdir -p Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_runner
unzip -q Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_FINAL_REVIEWER_PACKAGE_20260831.zip -d Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_runner
cd Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH2_runner
export HF_HOME="$HOME/hue_ptk/hf_cache"
export TRANSFORMERS_OFFLINE=1
PY="${PY:-/home/uet/miniconda3/envs/stage7e0/bin/python}"
"$PY" scripts/server/run_stage7e0_a3_english.py \
  --accepted-protocol-commit 30dd861ac52df8c1e04070f1dc807a5032591bdc \
  --result-root /home/uet/hue_ptk/stage7e0_a3_english_real_generation_preflight_results \
  --backend constrained_hf \
  --quantization none \
  --phase-o-max-new-tokens 512 \
  --phase-m-max-new-tokens 8192 \
  --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242
```

If the run is interrupted before completion, resume with the same command plus:

```bash
  --resume
```

After completion, copy these result files back for review:

```powershell
scp -r uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_a3_english_real_generation_preflight_results .
```
