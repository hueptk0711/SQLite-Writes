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

if [[ -z "${NLDB_GPU_VENV:-}" ]] \
    || [[ ! -x "$NLDB_GPU_VENV/bin/python" ]]
then
    echo "STOP: NLDB_GPU_VENV does not contain bin/python"
    exit 2
fi
if [[ -z "${NLDB_V2_SOURCE:-}" ]] \
    || [[ ! -d "$NLDB_V2_SOURCE/nldbwrite" ]]
then
    echo "STOP: NLDB_V2_SOURCE must contain nldbwrite/"
    exit 2
fi
PYTHON="$NLDB_GPU_VENV/bin/python"

DATA="data/external_holdout/dataset.final.json"
IDS="data/external_holdout/final_holdout_ids.txt"
GOLD="data/external_holdout/gold_plans.runtime.jsonl"
PROFILE_DIR="data/external_holdout/profiles"
DB_ROOT="data/external_holdout/databases"
INFERENCE_CONFIG="artifacts/server/hf_final_qwen25_7b_in28672_out4096.json"
ENVIRONMENT_MANIFEST="artifacts/environment/environment_manifest_final_server.json"
FINAL_PROTOCOL="configs/experiments/final_protocol.json"
RESULT_ROOT="${NLDB_FINAL_RESULT_ROOT:-experiments/external_holdout/final300_qwen25_7b_20260731}"
LOCK_DIR="diagnostics/final_external_holdout.lock"

for required_path in \
    "$DATA" \
    "$IDS" \
    "$GOLD" \
    "$PROFILE_DIR" \
    "$DB_ROOT" \
    "$INFERENCE_CONFIG" \
    "$ENVIRONMENT_MANIFEST" \
    "$FINAL_PROTOCOL" \
    diagnostics/final_asset_preflight.json \
    diagnostics/final_preflight.sha256 \
    artifacts/calibration/calibration_go_decision.json \
    "$NLDB_V2_SOURCE"
do
    if [[ ! -e "$required_path" ]]; then
        echo "STOP: missing required path: $required_path"
        exit 2
    fi
done

"$PYTHON" - "$FINAL_PROTOCOL" diagnostics/final_asset_preflight.json <<'PY_GATE'
import json
import sys

protocol = json.load(open(sys.argv[1], encoding="utf-8"))
assets = json.load(open(sys.argv[2], encoding="utf-8"))
assert protocol["status"] == "frozen"
assert assets["status"] == "pass"
assert assets["paper_result_eligible"] is True
assert assets["sample_count"] == 300
print("FROZEN FINAL PROTOCOL AND ASSETS: VERIFIED")
PY_GATE

available_kb="$(df -Pk "$PROJECT_ROOT" | awk 'NR==2 {print $4}')"
if [[ "$available_kb" -lt 3145728 ]]; then
    echo "STOP: less than 3 GiB disk space is available"
    df -h "$PROJECT_ROOT"
    exit 2
fi

mkdir -p diagnostics "$RESULT_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "STOP: another final external-holdout matrix appears to be running"
    exit 2
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

METHODS=(
    "d_fs_m|D-FS-M|configs/final/d_fs_m.json|gpu"
    "j_fs_m|J-FS-M|configs/final/j_fs_m.json|gpu"
    "s_fs_v2_m|S-FS-v2-M|configs/final/s_fs_v2_m.json|gpu_v2"
    "mp_fs_m|MP-FS-M|configs/final/mp_fs_m.json|gpu"
    "mp_fs_plus|MP-FS+|configs/final/mp_fs_plus.json|gpu"
    "gold_mp|Gold-MP|configs/oracles/gold_mp.json|oracle"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_id method_config backend <<< "$spec"
    output_dir="$RESULT_ROOT/$slug"
    run_log="diagnostics/final_${slug}.log"

    echo
    echo "=================================================="
    echo "START: $method_id"
    echo "TIME:  $(date --iso-8601=seconds)"
    echo "OUT:   $output_dir"
    echo "=================================================="

    if [[ -f "$output_dir/manifest.json" ]] \
        && [[ -f "$output_dir/evaluation.jsonl" ]] \
        && [[ -f "$output_dir/FINAL_RUN_CONSUMED.json" ]] \
        && [[ "$(wc -l < "$output_dir/evaluation.jsonl")" -eq 300 ]]
    then
        echo "SKIP: $method_id is already complete and consumed"
        continue
    fi

    common_args=(
        --stage external-holdout
        --config "$method_config"
        --data "$DATA"
        --ids "$IDS"
        --profile-dir "$PROFILE_DIR"
        --db-root "$DB_ROOT"
        --gold-plans "$GOLD"
        --output-dir "$output_dir"
        --dependency-lock requirements-inference.lock.txt
        --environment-manifest "$ENVIRONMENT_MANIFEST"
        --final-protocol "$FINAL_PROTOCOL"
    )
    extra_args=()
    if [[ "$backend" == "gpu" ]] || [[ "$backend" == "gpu_v2" ]]; then
        extra_args+=(--inference-config "$INFERENCE_CONFIG")
    fi
    if [[ "$backend" == "gpu_v2" ]]; then
        extra_args+=(--v2-source-path "$NLDB_V2_SOURCE")
    fi

    if ! "$PYTHON" scripts/experiments/run_method.py \
        "${common_args[@]}" \
        "${extra_args[@]}" \
        > "$run_log" 2>&1
    then
        echo "FAILED: $method_id"
        tail -n 120 "$run_log"
        exit 1
    fi

    "$PYTHON" - "$output_dir" "$method_id" "$backend" <<'PY_COMPLETE'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
method_id = sys.argv[2]
backend = sys.argv[3]
required = [
    "config.json",
    "run_lock.json",
    "manifest.json",
    "prompts.jsonl",
    "raw_generations.jsonl",
    "parsed_mapping_plans.jsonl",
    "materialized_write_plans.jsonl",
    "verification.jsonl",
    "compiled_programs.jsonl",
    "execution_logs.jsonl",
    "evaluation.jsonl",
    "metrics.json",
    "error_analysis.csv",
    "FINAL_RUN_CONSUMED.json",
]
if backend != "oracle":
    required.append("model_manifest.json")
missing = [name for name in required if not (root / name).is_file()]
raw = [
    json.loads(line)
    for line in (root / "raw_generations.jsonl").open(encoding="utf-8")
    if line.strip()
]
evaluation = [
    json.loads(line)
    for line in (root / "evaluation.jsonl").open(encoding="utf-8")
    if line.strip()
]
assert not missing, missing
assert len(raw) == 300
assert len(evaluation) == 300
assert len({row["sample_id"] for row in raw}) == 300
assert len({row["sample_id"] for row in evaluation}) == 300
assert not any(bool(row.get("input_truncated")) for row in raw)
assert not any(bool(row.get("hit_max_new_tokens")) for row in raw)
expected = {"not_applicable"} if backend == "oracle" else {"success"}
assert {row.get("status") for row in raw} <= expected
print(f"COMPLETE: {method_id}; rows=300; final marker=present")
PY_COMPLETE

    echo "END:  $method_id"
    echo "TIME: $(date --iso-8601=seconds)"
done

"$PYTHON" scripts/server/summarize_final_matrix.py \
    --result-root "$RESULT_ROOT" \
    --dataset "$DATA" \
    --ids "$IDS" \
    --protocol "$FINAL_PROTOCOL" \
    --output artifacts/reports/final_matrix_results.json \
    --csv-output artifacts/reports/final_main_table.csv \
    --markdown-output artifacts/reports/final_matrix_summary.md

bash scripts/server/package_final_results.sh "$RESULT_ROOT"

echo
echo "FINAL EXTERNAL-HOLDOUT MATRIX: COMPLETE"
echo "FINISHED: $(date --iso-8601=seconds)"
