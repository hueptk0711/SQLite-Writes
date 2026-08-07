#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

GPU_VENV="${NLDB_GPU_VENV:-$PROJECT_ROOT/.venv_gpu}"
PYTHON_BIN="${NLDB_PYTHON_BIN:-$GPU_VENV/bin/python}"
MODEL_PATH="${NLDB_MODEL_PATH:-}"
ENV_MANIFEST="artifacts/environment/environment_manifest_server.json"
MODEL_MANIFEST="artifacts/server/calibration_model_manifest.json"
MODEL_CONFIG="artifacts/server/hf_calibration_locked_in28672_out4096.json"
PROFILE_DIR="data/calibration/authoring_kit/profiles"
DB_ROOT="data/calibration/authoring_kit/databases"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

if [[ -z "$MODEL_PATH" ]]; then
    echo "STOP: set NLDB_MODEL_PATH to the accepted local Qwen snapshot"
    exit 2
fi
for required in "$PYTHON_BIN" "$MODEL_PATH" "$ENV_MANIFEST" "$PROFILE_DIR" "$DB_ROOT"; do
    if [[ ! -e "$required" ]]; then
        echo "STOP: missing required path: $required"
        exit 2
    fi
done

mkdir -p artifacts/audit artifacts/server diagnostics

"$PYTHON_BIN" scripts/data/audit_calibration.py \
    --data data/calibration/dataset.json \
    --reserved-final-db-ids data/calibration/reserved_final_database_ids.txt \
    --output diagnostics/calibration_metadata_server_recheck.json
"$PYTHON_BIN" scripts/data/audit_calibration_gold_mp.py \
    --data data/calibration/dataset.json \
    --profile-dir "$PROFILE_DIR" \
    --db-root "$DB_ROOT" \
    --output diagnostics/calibration_gold_mp_server_recheck.json
"$PYTHON_BIN" scripts/server/build_model_manifest.py \
    --model-path "$MODEL_PATH" \
    --output "$MODEL_MANIFEST"
"$PYTHON_BIN" scripts/server/create_smoke_inference_config.py \
    --model "$MODEL_PATH" \
    --model-manifest "$MODEL_MANIFEST" \
    --output "$MODEL_CONFIG" \
    --batch-size 1 \
    --max-input-tokens 28672 \
    --max-new-tokens 4096 \
    --quantization 4bit \
    --compute-dtype float16
"$PYTHON_BIN" scripts/server/capture_environment.py \
    --lock requirements-inference.lock.txt \
    --output "$ENV_MANIFEST" \
    --require-gpu
"$PYTHON_BIN" scripts/server/validate_calibration_bundle.py \
    --project-root "$PROJECT_ROOT" \
    --model-manifest "$MODEL_MANIFEST" \
    --inference-config "$MODEL_CONFIG" \
    --environment-manifest "$ENV_MANIFEST" \
    --require-gpu \
    --output diagnostics/calibration_gpu_preflight_v3_in28672_out4096.json

echo
echo "GPU CALIBRATION PREFLIGHT: PASS"
echo "Model manifest: $MODEL_MANIFEST"
echo "Inference config: $MODEL_CONFIG"
echo "Next: bash scripts/server/run_calibration_smoke.sh"
