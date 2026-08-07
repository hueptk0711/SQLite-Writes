#!/usr/bin/env bash
set -euo pipefail

PROJECT="${NLDB_FINAL_PROJECT:-$HOME/hue_ptk/mp_fs_plus_final_gpu_v2_out8192_20260731}"
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT"

if [[ -z "${NLDB_GPU_VENV:-}" ]] || [[ ! -x "$NLDB_GPU_VENV/bin/python" ]]; then
    echo "STOP: set NLDB_GPU_VENV to a venv containing executable bin/python"
    exit 2
fi
PY="$NLDB_GPU_VENV/bin/python"
export PYTHONPATH="$PROJECT/src${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export NLDB_NVIDIA_SMI_ID="${NLDB_NVIDIA_SMI_ID:-0}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

DATA="data/external_holdout/dataset.final.json"
IDS="data/external_holdout/final_holdout_ids.txt"
GOLD="data/external_holdout/gold_plans.runtime.jsonl"
PROFILE_DIR="data/external_holdout/profiles"
DB_ROOT="data/external_holdout/databases"
INFERENCE="artifacts/server/hf_second_model_qwen25_coder_14b_in28672_out8192.json"
PROTOCOL="configs/experiments/second_model_qwen25_coder_14b_protocol_v1.json"
ENVIRONMENT="artifacts/environment/environment_manifest_final_server.json"
RESULT_ROOT="${NLDB_SECOND_MODEL_RESULT_ROOT:-experiments/second_model/qwen25_coder_14b_final300_posthoc_v1}"

for path in "$DATA" "$IDS" "$GOLD" "$PROFILE_DIR" "$DB_ROOT" "$INFERENCE" "$PROTOCOL" "$ENVIRONMENT"; do
    if [[ ! -e "$path" ]]; then
        echo "STOP: missing $path"
        exit 2
    fi
done

mkdir -p diagnostics artifacts/reports dist/results "$RESULT_ROOT"
METHODS=(
    "d_fs_m|D-FS-M|configs/final/d_fs_m.json"
    "j_fs_m|J-FS-M|configs/final/j_fs_m.json"
    "mp_fs_plus|MP-FS+|configs/final/mp_fs_plus.json"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_id config <<< "$spec"
    out="$RESULT_ROOT/$slug"
    log="diagnostics/second_model_${slug}.log"
    if [[ -f "$out/FINAL_RUN_CONSUMED.json" ]] && [[ "$(wc -l < "$out/evaluation.jsonl")" -eq 300 ]]; then
        echo "SKIP: $method_id already complete"
        continue
    fi
    echo "START: $method_id at $(date --iso-8601=seconds)"
    if ! "$PY" scripts/experiments/run_method.py \
        --stage second-model \
        --config "$config" \
        --data "$DATA" \
        --ids "$IDS" \
        --profile-dir "$PROFILE_DIR" \
        --db-root "$DB_ROOT" \
        --gold-plans "$GOLD" \
        --output-dir "$out" \
        --inference-config "$INFERENCE" \
        --dependency-lock requirements-inference.lock.txt \
        --environment-manifest "$ENVIRONMENT" \
        --final-protocol "$PROTOCOL" \
        > "$log" 2>&1
    then
        echo "FAILED: $method_id"
        tail -n 120 "$log"
        exit 1
    fi
    test -f "$out/FINAL_RUN_CONSUMED.json"
    test "$(wc -l < "$out/evaluation.jsonl")" -eq 300
    echo "COMPLETE: $method_id at $(date --iso-8601=seconds)"
done

"$PY" "$BUNDLE_DIR/summarize_second_model.py" \
    --result-root "$RESULT_ROOT" \
    --protocol "$PROTOCOL" \
    --output-root artifacts/reports

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="dist/results/mp_fs_plus_second_model_qwen25_coder_14b_v1_${stamp}.tar.gz"
tar -czf "$archive" \
    "$RESULT_ROOT" \
    artifacts/reports/second_model_qwen25_coder_14b_v1.json \
    artifacts/reports/second_model_qwen25_coder_14b_v1.csv \
    artifacts/reports/second_model_qwen25_coder_14b_v1.md \
    "$PROTOCOL" \
    "$INFERENCE"
sha256sum "$archive" > "$archive.sha256"
echo "RESULT ARCHIVE: $PROJECT/$archive"
echo "RESULT CHECKSUM: $PROJECT/$archive.sha256"
