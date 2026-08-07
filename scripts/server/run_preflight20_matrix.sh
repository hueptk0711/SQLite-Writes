#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

python3 scripts/server/verify_runtime_source.py \
    --project-root "$PROJECT_ROOT" \
    --output artifacts/environment/runtime_source_server.json

V2_ROOT="${NLDB_V2_ROOT:-$HOME/hue_ptk/paper_v2_20260714/nl_db_write_pipeline}"
export NLDB_PROFILE_DIR="${NLDB_PROFILE_DIR:-$V2_ROOT/artifacts/profiles_aug900}"
export NLDB_DATABASE_ROOT="${NLDB_DATABASE_ROOT:-$V2_ROOT/data/bird_databases}"
export NLDB_V2_SOURCE="${NLDB_V2_SOURCE:-$V2_ROOT/src}"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"

MODEL_CONFIG="${NLDB_MODEL_CONFIG:-configs/models/hf_local.qwen25_7b_mnt16384.json}"
ENV_MANIFEST="${NLDB_ENVIRONMENT_MANIFEST:-artifacts/environment/environment_manifest_server.json}"
DATA="data/frozen/dev/dataset_dev_v3.json"
IDS="data/splits/preflight/dev_preflight_20_ids.txt"
GOLD="data/frozen/dev/gold_write_plans_dev_v3.jsonl"
RESULT_ROOT="${NLDB_MATRIX20_ROOT:-experiments/dev/matrix20_mnt16384_v1}"
LOCK_DIR="diagnostics/preflight20_matrix.lock"

for required_path in \
    "$MODEL_CONFIG" \
    "$ENV_MANIFEST" \
    "$DATA" \
    "$IDS" \
    "$GOLD" \
    "$NLDB_PROFILE_DIR" \
    "$NLDB_DATABASE_ROOT" \
    "$NLDB_V2_SOURCE"
do
    if [[ ! -e "$required_path" ]]; then
        echo "MISSING REQUIRED PATH: $required_path"
        exit 1
    fi
done

mkdir -p diagnostics "$RESULT_ROOT"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "STOP: another preflight-20 matrix appears to be running"
    exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

METHODS=(
    "d_fs|configs/baselines/direct_fs.json"
    "j_fs_common|configs/baselines/record_json_fs_common.json"
    "s_fs_v2|configs/baselines/structured_fs_v2.json"
    "mp_fs|configs/proposed/mp_fs.json"
    "mp_fs_r_semi|configs/proposed/mp_fs_r_semi.json"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_config <<< "$spec"
    output_dir="$RESULT_ROOT/$slug"
    run_log="diagnostics/matrix20_${slug}.log"

    echo
    echo "=================================================="
    echo "START: $slug"
    echo "TIME:  $(date --iso-8601=seconds)"
    echo "OUT:   $output_dir"
    echo "=================================================="

    if [[ -f "$output_dir/manifest.json" ]] \
        && [[ -f "$output_dir/evaluation.jsonl" ]] \
        && [[ "$(wc -l < "$output_dir/evaluation.jsonl")" -eq 20 ]]
    then
        echo "SKIP: $slug already complete"
        continue
    fi

    extra_args=()
    if [[ "$slug" == "s_fs_v2" ]]; then
        extra_args=(--v2-source "$NLDB_V2_SOURCE")
    fi

    if ! python3 scripts/experiments/run_method.py \
        --stage dev \
        --config "$method_config" \
        --inference-config "$MODEL_CONFIG" \
        --data "$DATA" \
        --ids "$IDS" \
        --gold-plans "$GOLD" \
        --output-dir "$output_dir" \
        --dependency-lock requirements-inference.lock.txt \
        --environment-manifest "$ENV_MANIFEST" \
        "${extra_args[@]}" \
        > "$run_log" 2>&1
    then
        echo "FAILED: $slug"
        tail -n 100 "$run_log"
        exit 1
    fi

    python3 - "$output_dir" "$slug" <<'PY_GATE'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
slug = sys.argv[2]

required = [
    "config.json",
    "run_lock.json",
    "manifest.json",
    "model_manifest.json",
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
]

missing = [name for name in required if not (root / name).exists()]
raw = [
    json.loads(line)
    for line in open(root / "raw_generations.jsonl")
    if line.strip()
]
evaluation = [
    json.loads(line)
    for line in open(root / "evaluation.jsonl")
    if line.strip()
]
repairs = [row["repair"] for row in raw if row.get("repair")]

assert not missing, missing
assert len(raw) == 20
assert len(evaluation) == 20
assert len({row["sample_id"] for row in raw}) == 20
assert all(row.get("status") == "success" for row in raw)
assert not any(row.get("input_truncated") for row in raw)
assert all(
    repair.get("status") == "success"
    and not repair.get("input_truncated")
    for repair in repairs
)

print(
    f"COMPLETE: {slug}",
    f"rows={len(raw)}",
    f"output_limited={sum(bool(x.get('hit_max_new_tokens')) for x in raw)}",
    f"oom_fallback={sum(bool(x.get('oom_fallback_used')) for x in raw)}",
    f"repair_calls={len(repairs)}",
    f"repair_limited={sum(bool(x.get('hit_max_new_tokens')) for x in repairs)}",
    f"missing_artifacts={len(missing)}",
)
PY_GATE

    echo "END:  $slug"
    echo "TIME: $(date --iso-8601=seconds)"
done

echo
echo "PRE-FLIGHT 20 MATRIX: COMPLETE"
echo "FINISHED: $(date --iso-8601=seconds)"
