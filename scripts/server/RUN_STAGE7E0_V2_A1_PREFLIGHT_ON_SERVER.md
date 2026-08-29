# Stage7E0 V2-A1 Real Generation Preflight

This server package is for the GPU infrastructure smoke only. It does not run
the 1,760 train generation pass, does not run dev, does not evaluate the 481
confirmation set, and does not open LiveSQLBench ground truth.

Expected PATCH8 server location:

```bash
/home/uet/hue_ptk/Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_RUN_PACKAGE_20260829.zip
```

Upload from local PowerShell:

```powershell
scp "D:\paper kltn\text to sql\w\s6c_exec\reviewer_packages\Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_RUN_PACKAGE_20260829.zip" uet@222.255.250.24:/home/uet/hue_ptk/
scp "D:\paper kltn\text to sql\w\s6c_exec\reviewer_packages\Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_RUN_PACKAGE_20260829.zip.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
ssh uet@222.255.250.24
```

Run after SSH login:

```bash
cd /home/uet/hue_ptk

rm -rf stage7e0_v2_a1_preflight_PATCH8_run_20260829
mkdir -p stage7e0_v2_a1_preflight_PATCH8_run_20260829
unzip -q Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_RUN_PACKAGE_20260829.zip -d stage7e0_v2_a1_preflight_PATCH8_run_20260829
cd stage7e0_v2_a1_preflight_PATCH8_run_20260829
bash RUN_COMMAND_SERVER_ONLY.sh
```

The server-only script runs:

```bash
export CUDA_VISIBLE_DEVICES=0
export MODEL_PATH=/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242
export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
PY=/home/uet/miniconda3/envs/spin/bin/python

$PY -m py_compile scripts/server/run_stage7e0_v2_a1_preflight.py
$PY scripts/data/validate_stage7d_v2_a1_implementation.py
$PY -m pytest -q tests/v2_a1/test_stage7d_v2_a1.py tests/v2_a1/test_stage7e0_real_generation_preflight.py

runner_status=0
$PY scripts/server/run_stage7e0_v2_a1_preflight.py \
  --model-path "$MODEL_PATH" \
  --output-dir stage7e0_real_generation_preflight_PATCH8 || runner_status=$?

zip -r Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_OUTPUT_20260829.zip stage7e0_real_generation_preflight_PATCH8
sha256sum Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_OUTPUT_20260829.zip > Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_OUTPUT_20260829.zip.sha256
exit "$runner_status"
```

Copy output back to local if needed:

```bash
scp uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_v2_a1_preflight_PATCH8_run_20260829/Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_OUTPUT_20260829.zip .
scp uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_v2_a1_preflight_PATCH8_run_20260829/Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH8_SERVER_OUTPUT_20260829.zip.sha256 .
```
