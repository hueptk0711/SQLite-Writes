#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export NLDB_NVIDIA_SMI_ID="${NLDB_NVIDIA_SMI_ID:-0}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"

if [[ -z "${NLDB_GPU_VENV:-}" ]]; then
    echo "STOP: set NLDB_GPU_VENV to an existing GPU virtual environment"
    exit 2
fi
PYTHON="$NLDB_GPU_VENV/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "STOP: missing executable Python: $PYTHON"
    exit 2
fi
if [[ -z "${NLDB_MODEL_PATH:-}" ]] || [[ ! -d "$NLDB_MODEL_PATH" ]]; then
    echo "STOP: NLDB_MODEL_PATH must point to the local model snapshot"
    exit 2
fi
if [[ -z "${NLDB_V2_SOURCE:-}" ]] \
    || [[ ! -d "$NLDB_V2_SOURCE/nldbwrite" ]]
then
    echo "STOP: NLDB_V2_SOURCE must contain the nldbwrite/ package"
    exit 2
fi

DATA="data/external_holdout/dataset.final.json"
IDS="data/external_holdout/final_holdout_ids.txt"
GOLD="data/external_holdout/gold_plans.runtime.jsonl"
PROFILE_DIR="data/external_holdout/profiles"
DB_ROOT="data/external_holdout/databases"
MODEL_MANIFEST="artifacts/server/final_model_manifest.json"
INFERENCE_CONFIG="artifacts/server/hf_final_qwen25_7b_in28672_out4096.json"
ENVIRONMENT_MANIFEST="artifacts/environment/environment_manifest_final_server.json"
FINAL_PROTOCOL="configs/experiments/final_protocol.json"

mkdir -p diagnostics artifacts/server artifacts/environment

"$PYTHON" scripts/server/verify_runtime_source.py \
    --project-root "$PROJECT_ROOT" \
    --output artifacts/environment/runtime_source_final_server.json

"$PYTHON" scripts/server/prepare_final_assets.py \
    --data-root data/external_holdout \
    --calibration-go artifacts/calibration/calibration_go_decision.json \
    --runtime-gold-output "$GOLD" \
    --output diagnostics/final_asset_preflight.json

"$PYTHON" scripts/data/audit_external_holdout.py \
    --data "$DATA" \
    --output diagnostics/final_metadata_audit.json

"$PYTHON" scripts/server/capture_environment.py \
    --lock requirements-inference.lock.txt \
    --output "$ENVIRONMENT_MANIFEST" \
    --require-gpu

"$PYTHON" scripts/server/build_model_manifest.py \
    --model-path "$NLDB_MODEL_PATH" \
    --output "$MODEL_MANIFEST"

"$PYTHON" scripts/server/create_smoke_inference_config.py \
    --model "$NLDB_MODEL_PATH" \
    --model-manifest "$MODEL_MANIFEST" \
    --output "$INFERENCE_CONFIG" \
    --batch-size 1 \
    --max-input-tokens 28672 \
    --max-new-tokens 4096 \
    --quantization 4bit \
    --compute-dtype float16

"$PYTHON" scripts/server/freeze_final_protocol.py \
    --template configs/experiments/final_protocol.template.json \
    --data "$DATA" \
    --ids "$IDS" \
    --gold-plans "$GOLD" \
    --run "external-holdout|D-FS-M|configs/final/d_fs_m.json|$INFERENCE_CONFIG" \
    --run "external-holdout|J-FS-M|configs/final/j_fs_m.json|$INFERENCE_CONFIG" \
    --run "external-holdout|S-FS-v2-M|configs/final/s_fs_v2_m.json|$INFERENCE_CONFIG" \
    --run "external-holdout|MP-FS-M|configs/final/mp_fs_m.json|$INFERENCE_CONFIG" \
    --run "external-holdout|MP-FS+|configs/final/mp_fs_plus.json|$INFERENCE_CONFIG" \
    --run "external-holdout|Gold-MP|configs/oracles/gold_mp.json|-" \
    --output "$FINAL_PROTOCOL" \
    --verify-existing

"$PYTHON" - "$FINAL_PROTOCOL" \
    diagnostics/final_asset_preflight.json \
    "$ENVIRONMENT_MANIFEST" <<'PY_VERIFY'
import json
import sys

protocol = json.load(open(sys.argv[1], encoding="utf-8"))
assets = json.load(open(sys.argv[2], encoding="utf-8"))
environment = json.load(open(sys.argv[3], encoding="utf-8"))
assert protocol["status"] == "frozen"
assert set(protocol["authorized_runs"]["external-holdout"]) == {
    "D-FS-M",
    "J-FS-M",
    "S-FS-v2-M",
    "MP-FS-M",
    "MP-FS+",
    "Gold-MP",
}
assert assets["status"] == "pass"
assert assets["sample_count"] == 300
assert environment["status"] == "gpu_ready"
print("FINAL GPU PREFLIGHT: PASS")
print("FINAL PROTOCOL:", sys.argv[1])
PY_VERIFY

sha256sum \
    "$DATA" \
    "$IDS" \
    "$GOLD" \
    "$MODEL_MANIFEST" \
    "$INFERENCE_CONFIG" \
    "$ENVIRONMENT_MANIFEST" \
    "$FINAL_PROTOCOL" \
    > diagnostics/final_preflight.sha256

echo "NEXT: bash scripts/server/run_final_external_holdout.sh"
