# Stage6F GPU preflight server commands

Run this after the Stage6F reviewer accepts the script package. These commands
do not run confirmatory inference and do not create predictions for the 481
confirmation samples.

```bash
ssh uet@222.255.250.24
mkdir -p /home/uet/hue_ptk
cd /home/uet/hue_ptk

if [ ! -d SQLite-Writes ]; then
  git clone https://github.com/hueptk0711/SQLite-Writes.git SQLite-Writes
fi

cd SQLite-Writes
git fetch --all --tags
git checkout stage6f/gpu-environment-preflight
EXPECTED_EXECUTION_COMMIT="$(git rev-parse HEAD)"
echo "$EXPECTED_EXECUTION_COMMIT"
git status --porcelain

# The checkout must be clean before preflight.
# Replace MODEL_PATH with the local cached Qwen2.5-Coder-7B-Instruct snapshot
# path if the server uses an offline HF cache.
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Coder-7B-Instruct}"
OUT_DIR="/home/uet/hue_ptk/stage6f_gpu_preflight_outputs/stage6_gpu_preflight"

python scripts/data/create_stage6f_gpu_preflight.py \
  --output-dir "$OUT_DIR" \
  --execute-gpu-preflight \
  --expected-execution-commit "$EXPECTED_EXECUTION_COMMIT" \
  --model-name-or-path "$MODEL_PATH" \
  --load-model

python scripts/data/validate_stage6f_gpu_preflight.py \
  --preflight-dir "$OUT_DIR" \
  --require-gpu-pass
```
