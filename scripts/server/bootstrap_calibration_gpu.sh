#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

GPU_VENV="${NLDB_GPU_VENV:-$PROJECT_ROOT/.venv_gpu}"
PYTHON_BIN="${NLDB_PYTHON_BIN:-$GPU_VENV/bin/python}"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"

mkdir -p artifacts/environment artifacts/server diagnostics dist/results

if [[ "${NLDB_SKIP_INSTALL:-0}" != "1" ]]; then
    if [[ ! -x "$PYTHON_BIN" ]]; then
        python3 -m venv "$GPU_VENV"
    fi
    "$PYTHON_BIN" -m pip install -U pip
    "$PYTHON_BIN" -m pip install -r requirements-inference.txt
elif [[ ! -x "$PYTHON_BIN" ]]; then
    echo "STOP: NLDB_SKIP_INSTALL=1 but Python is missing: $PYTHON_BIN"
    exit 2
fi

"$PYTHON_BIN" scripts/server/verify_runtime_source.py \
    --project-root "$PROJECT_ROOT" \
    --output artifacts/environment/runtime_source_server.json
"$PYTHON_BIN" scripts/server/capture_environment.py \
    --lock requirements-inference.lock.txt \
    --output artifacts/environment/environment_manifest_server.json \
    --require-gpu
"$PYTHON_BIN" scripts/server/validate_calibration_bundle.py \
    --project-root "$PROJECT_ROOT" \
    --environment-manifest artifacts/environment/environment_manifest_server.json \
    --require-gpu \
    --output diagnostics/calibration_bundle_bootstrap_validation.json

echo
echo "BOOTSTRAP GPU: PASS"
echo "Python: $PYTHON_BIN"
nvidia-smi
