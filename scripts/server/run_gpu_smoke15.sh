#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

PYTHON_BIN="${NLDB_PYTHON_BIN:-$PROJECT_ROOT/.venv_gpu/bin/python}"
MODEL_PATH="${NLDB_MODEL_PATH:-}"
MODEL_REVISION="${NLDB_MODEL_REVISION:-}"
V2_ROOT="${NLDB_V2_ROOT:-$HOME/hue_ptk/paper_v2_20260714/nl_db_write_pipeline}"
export NLDB_PROFILE_DIR="${NLDB_PROFILE_DIR:-$V2_ROOT/artifacts/profiles_aug900}"
export NLDB_DATABASE_ROOT="${NLDB_DATABASE_ROOT:-$V2_ROOT/data/bird_databases}"
export NLDB_V2_SOURCE="${NLDB_V2_SOURCE:-$V2_ROOT/src}"
export NLDB_ENVIRONMENT_MANIFEST="${NLDB_ENVIRONMENT_MANIFEST:-$PROJECT_ROOT/artifacts/environment/environment_manifest_server.json}"
RUNTIME_SOURCE_MANIFEST="$PROJECT_ROOT/artifacts/environment/runtime_source_server.json"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"

if [[ -z "$MODEL_PATH" ]]; then
    echo "STOP: set NLDB_MODEL_PATH to a local model directory or HF model ID"
    exit 2
fi

for required_path in \
    "$PYTHON_BIN" \
    "$NLDB_PROFILE_DIR" \
    "$NLDB_DATABASE_ROOT" \
    "$NLDB_ENVIRONMENT_MANIFEST"
do
    if [[ ! -e "$required_path" ]]; then
        echo "STOP: missing required path: $required_path"
        exit 2
    fi
done

mkdir -p \
    artifacts/server \
    data/smoke/real_model_smoke15 \
    diagnostics \
    experiments/gpu_smoke \
    dist/results

"$PYTHON_BIN" scripts/server/verify_runtime_source.py \
    --project-root "$PROJECT_ROOT" \
    --output "$RUNTIME_SOURCE_MANIFEST"
"$PYTHON_BIN" scripts/data/build_real_model_smoke15.py
"$PYTHON_BIN" scripts/server/capture_environment.py \
    --lock requirements-inference.lock.txt \
    --output "$NLDB_ENVIRONMENT_MANIFEST" \
    --require-gpu
"$PYTHON_BIN" scripts/server/verify_external_assets.py \
    --manifest data/smoke/real_model_smoke15/server_external_assets_manifest.json \
    --profile-dir "$NLDB_PROFILE_DIR" \
    --db-root "$NLDB_DATABASE_ROOT"

MODEL_CONFIG="artifacts/server/hf_gpu_smoke15.json"
MODEL_MANIFEST_INPUT="artifacts/server/model_manifest_input.json"
MODEL_CONFIG_ARGS=(
    --model "$MODEL_PATH"
    --output "$MODEL_CONFIG"
    --batch-size "${NLDB_BATCH_SIZE:-1}"
    --max-input-tokens "${NLDB_MAX_INPUT_TOKENS:-16384}"
    --max-new-tokens "${NLDB_MAX_NEW_TOKENS:-2048}"
    --quantization "${NLDB_QUANTIZATION:-4bit}"
    --compute-dtype "${NLDB_COMPUTE_DTYPE:-float16}"
)

if [[ -d "$MODEL_PATH" ]]; then
    export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
    export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
    "$PYTHON_BIN" scripts/server/build_model_manifest.py \
        --model-path "$MODEL_PATH" \
        --output "$MODEL_MANIFEST_INPUT"
    MODEL_CONFIG_ARGS+=(--model-manifest "$MODEL_MANIFEST_INPUT")
else
    export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-0}"
    export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-0}"
    if [[ ! "$MODEL_REVISION" =~ ^[0-9a-fA-F]{40}$ ]]; then
        echo "STOP: remote HF models require NLDB_MODEL_REVISION (40 hex chars)"
        exit 2
    fi
    MODEL_CONFIG_ARGS+=(--revision "$MODEL_REVISION")
fi

"$PYTHON_BIN" scripts/server/create_smoke_inference_config.py \
    "${MODEL_CONFIG_ARGS[@]}"

RUN_ID="${NLDB_SMOKE_RUN_ID:-mp_fs_plus_smoke15_$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="$PROJECT_ROOT/experiments/gpu_smoke/$RUN_ID"
RUN_LOG="$PROJECT_ROOT/diagnostics/${RUN_ID}.log"
VALIDATION="$PROJECT_ROOT/diagnostics/${RUN_ID}_validation.json"

echo "START GPU SMOKE: $RUN_ID"
echo "MODEL: $MODEL_PATH"
echo "OUTPUT: $RUN_DIR"

"$PYTHON_BIN" scripts/experiments/run_method.py \
    --stage dev \
    --config configs/final/mp_fs_plus.json \
    --inference-config "$MODEL_CONFIG" \
    --data data/smoke/real_model_smoke15/dataset.json \
    --ids data/smoke/real_model_smoke15/ids.txt \
    --gold-plans data/smoke/real_model_smoke15/gold_write_plans.jsonl \
    --profile-dir "$NLDB_PROFILE_DIR" \
    --db-root "$NLDB_DATABASE_ROOT" \
    --dependency-lock requirements-inference.lock.txt \
    --environment-manifest "$NLDB_ENVIRONMENT_MANIFEST" \
    --output-dir "$RUN_DIR" \
    2>&1 | tee "$RUN_LOG"

set +e
"$PYTHON_BIN" scripts/server/validate_real_model_smoke.py \
    --run-dir "$RUN_DIR" \
    --selection-manifest data/smoke/real_model_smoke15/selection_manifest.json \
    --runtime-source-manifest "$RUNTIME_SOURCE_MANIFEST" \
    --output "$VALIDATION"
VALIDATION_STATUS=$?
set -e

RESULT_ARCHIVE="$PROJECT_ROOT/dist/results/${RUN_ID}.tar.gz"
tar -czf "$RESULT_ARCHIVE" \
    -C "$PROJECT_ROOT" \
    "experiments/gpu_smoke/$RUN_ID" \
    "diagnostics/${RUN_ID}.log" \
    "diagnostics/${RUN_ID}_validation.json" \
    "artifacts/environment/environment_manifest_server.json" \
    "artifacts/environment/runtime_source_server.json" \
    "artifacts/server/hf_gpu_smoke15.json"
sha256sum "$RESULT_ARCHIVE" > "${RESULT_ARCHIVE}.sha256"

echo "$RUN_DIR" > artifacts/server/last_gpu_smoke_output.txt
echo
echo "GPU SMOKE FINISHED"
echo "Validation: $VALIDATION"
echo "Result bundle: $RESULT_ARCHIVE"
echo "Checksum: ${RESULT_ARCHIVE}.sha256"

exit "$VALIDATION_STATUS"
