#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

VENV_DIR="${NLDB_GPU_VENV:-$PROJECT_ROOT/.venv_gpu}"
SYSTEM_PYTHON="${NLDB_SYSTEM_PYTHON:-python3}"
SKIP_INSTALL="${NLDB_SKIP_INSTALL:-0}"

if [[ "$SKIP_INSTALL" == "1" ]]; then
    PYTHON_BIN="${NLDB_PYTHON_BIN:-python3}"
else
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
    fi
    PYTHON_BIN="$VENV_DIR/bin/python"
    "$PYTHON_BIN" -m pip install --upgrade pip wheel
    "$PYTHON_BIN" -m pip install -r requirements-inference.txt
fi

mkdir -p artifacts/environment
"$PYTHON_BIN" scripts/server/verify_runtime_source.py \
    --project-root "$PROJECT_ROOT" \
    --output artifacts/environment/runtime_source_server.json
"$PYTHON_BIN" scripts/server/capture_environment.py \
    --lock requirements-inference.lock.txt \
    --output artifacts/environment/environment_manifest_server.json \
    --require-gpu

cat <<EOF
GPU ENVIRONMENT READY
Project: $PROJECT_ROOT
Python:  $PYTHON_BIN
Manifest: $PROJECT_ROOT/artifacts/environment/environment_manifest_server.json
Runtime source: $PROJECT_ROOT/artifacts/environment/runtime_source_server.json

Before running the smoke:
  export NLDB_PYTHON_BIN="$PYTHON_BIN"
  export NLDB_MODEL_PATH="/absolute/path/to/the/pinned/model"
  bash scripts/server/run_gpu_smoke15.sh
EOF
