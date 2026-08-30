# Stage7E0-A3 English Server Run Commands

Run these on the Windows machine to upload the package:

```powershell
scp "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip" uet@222.255.250.24:/home/uet/hue_ptk/
scp "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
```

Run these on the GPU server:

```bash
ssh uet@222.255.250.24
cd /home/uet/hue_ptk
sha256sum -c Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip.sha256
rm -rf Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_runner
mkdir -p Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_runner
unzip -q Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip -d Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_runner
cd Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT_PATCH0_runner
export HF_HOME="$HOME/hue_ptk/hf_cache"
export TRANSFORMERS_OFFLINE=1
/home/uet/hue_ptk/mp_fs_plus_final_gpu_20260731/.venv_gpu/bin/python scripts/server/run_stage7e0_a3_english.py \
  --accepted-protocol-commit ab006242bc498c343fe9573c893283a9733bcc1f \
  --result-root /home/uet/hue_ptk/stage7e0_a3_english_real_generation_preflight_results \
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
