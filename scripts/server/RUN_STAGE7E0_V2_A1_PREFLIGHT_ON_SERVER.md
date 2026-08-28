# Stage7E0 V2-A1 Real Generation Preflight

This server package is for the GPU infrastructure smoke only. It does not run
the 1,760 train generation pass, does not run dev, does not evaluate the 481
confirmation set, and does not open LiveSQLBench ground truth.

Expected PATCH1 server location:

```bash
/home/uet/hue_ptk/Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_RUN_PACKAGE_20260828.zip
```

Run on the server:

```bash
ssh uet@222.255.250.24
cd /home/uet/hue_ptk

rm -rf stage7e0_v2_a1_preflight_patch1_run_20260828
mkdir -p stage7e0_v2_a1_preflight_patch1_run_20260828
unzip -q Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_RUN_PACKAGE_20260828.zip -d stage7e0_v2_a1_preflight_patch1_run_20260828
cd stage7e0_v2_a1_preflight_patch1_run_20260828

export CUDA_VISIBLE_DEVICES=0
export MODEL_PATH=/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242
export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
PY=/home/uet/miniconda3/envs/spin/bin/python

$PY -m py_compile scripts/server/run_stage7e0_v2_a1_preflight.py
$PY scripts/data/validate_stage7d_v2_a1_implementation.py
$PY -m pytest -q tests/v2_a1/test_stage7d_v2_a1.py

$PY scripts/server/run_stage7e0_v2_a1_preflight.py \
  --model-path "$MODEL_PATH" \
  --output-dir stage7e0_real_generation_preflight_patch1

zip -r Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_OUTPUT_20260828.zip stage7e0_real_generation_preflight_patch1
sha256sum Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_OUTPUT_20260828.zip > Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_OUTPUT_20260828.zip.sha256
```

Copy output back to local if needed:

```bash
scp uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_v2_a1_preflight_patch1_run_20260828/Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_OUTPUT_20260828.zip .
scp uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_v2_a1_preflight_patch1_run_20260828/Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT_PATCH1_SERVER_OUTPUT_20260828.zip.sha256 .
```
