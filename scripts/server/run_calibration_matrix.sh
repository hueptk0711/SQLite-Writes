#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

GPU_VENV="${NLDB_GPU_VENV:-$PROJECT_ROOT/.venv_gpu}"
PYTHON_BIN="${NLDB_PYTHON_BIN:-$GPU_VENV/bin/python}"
MODEL_CONFIG="artifacts/server/hf_calibration_locked_in28672_out4096.json"
MODEL_MANIFEST="artifacts/server/calibration_model_manifest.json"
ENV_MANIFEST="artifacts/environment/environment_manifest_server.json"
SMOKE_SUMMARY="diagnostics/calibration_smoke_v3_in28672_out4096_summary.json"
RESULT_ROOT="${NLDB_CALIBRATION_ROOT:-experiments/calibration/full_locked_v3_in28672_out4096}"
PROFILE_DIR="data/calibration/authoring_kit/profiles"
DB_ROOT="data/calibration/authoring_kit/databases"
DATA="data/calibration/dataset.json"
IDS="data/calibration/calibration_ids.txt"
GOLD="data/calibration/gold_write_plans.jsonl"
EXPECTED=60

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

for required in \
    "$PYTHON_BIN" "$MODEL_CONFIG" "$MODEL_MANIFEST" "$ENV_MANIFEST" \
    "$SMOKE_SUMMARY" "$PROFILE_DIR" "$DB_ROOT" "$DATA" "$IDS" "$GOLD"
do
    if [[ ! -e "$required" ]]; then
        echo "STOP: missing required artifact: $required"
        exit 2
    fi
done

"$PYTHON_BIN" - "$SMOKE_SUMMARY" <<'PY_SMOKE'
import json
import sys
summary = json.load(open(sys.argv[1]))
assert summary.get("status") == "pass", summary
assert len(summary.get("methods", {})) == 4, summary
print("LOCKED CALIBRATION SMOKE GATE: PASS")
PY_SMOKE

"$PYTHON_BIN" scripts/server/validate_calibration_bundle.py \
    --project-root "$PROJECT_ROOT" \
    --model-manifest "$MODEL_MANIFEST" \
    --inference-config "$MODEL_CONFIG" \
    --environment-manifest "$ENV_MANIFEST" \
    --require-gpu \
    --output diagnostics/calibration_matrix_precheck.json

available_kb="$(df -Pk "$PROJECT_ROOT" | awk 'NR==2 {print $4}')"
if [[ "$available_kb" -lt 2097152 ]]; then
    echo "STOP: less than 2 GiB disk space is available"
    df -h "$PROJECT_ROOT"
    exit 2
fi

mkdir -p diagnostics "$RESULT_ROOT" artifacts/reports dist/results
LOCK_DIR="diagnostics/calibration_matrix.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "STOP: calibration matrix appears to be running already"
    exit 2
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

METHODS=(
    "d_fs_m|D-FS-M|configs/final/d_fs_m.json|gpu"
    "j_fs_m|J-FS-M|configs/final/j_fs_m.json|gpu"
    "mp_fs_m|MP-FS-M|configs/final/mp_fs_m.json|gpu"
    "mp_fs_plus|MP-FS+|configs/final/mp_fs_plus.json|gpu"
    "gold_mp|Gold-MP|configs/oracles/gold_mp.json|oracle"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_id method_config backend <<< "$spec"
    output_dir="$RESULT_ROOT/$slug"
    run_log="diagnostics/calibration_full_v3_in28672_out4096_${slug}.log"
    echo
    echo "=================================================="
    echo "FULL START: $method_id ($backend)"
    echo "TIME: $(date --iso-8601=seconds)"
    echo "OUT: $output_dir"
    echo "=================================================="

    if [[ -f "$output_dir/evaluation.jsonl" ]] \
        && [[ -f "$output_dir/raw_generations.jsonl" ]] \
        && [[ "$(wc -l < "$output_dir/evaluation.jsonl")" -eq "$EXPECTED" ]] \
        && [[ "$(wc -l < "$output_dir/raw_generations.jsonl")" -eq "$EXPECTED" ]]
    then
        echo "SKIP: $method_id already has $EXPECTED complete rows"
        continue
    fi

    common_args=(
        --stage calibration
        --config "$method_config"
        --data "$DATA"
        --ids "$IDS"
        --gold-plans "$GOLD"
        --profile-dir "$PROFILE_DIR"
        --db-root "$DB_ROOT"
        --dependency-lock requirements-inference.lock.txt
        --environment-manifest "$ENV_MANIFEST"
        --output-dir "$output_dir"
    )
    extra_args=()
    if [[ "$backend" == "gpu" ]]; then
        extra_args+=(--inference-config "$MODEL_CONFIG")
    fi

    "$PYTHON_BIN" scripts/experiments/run_method.py \
        "${common_args[@]}" \
        "${extra_args[@]}" \
        2>&1 | tee "$run_log"

    "$PYTHON_BIN" - \
        "$output_dir" \
        "$EXPECTED" \
        "$method_id" \
        "$backend" \
        configs/experiments/calibration_protocol.json <<'PY_GATE'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = int(sys.argv[2])
method = sys.argv[3]
backend = sys.argv[4]
protocol = json.load(open(sys.argv[5]))
raw = [json.loads(line) for line in open(root / "raw_generations.jsonl") if line.strip()]
evaluation = [json.loads(line) for line in open(root / "evaluation.jsonl") if line.strip()]
allowed = {"success"} if backend == "gpu" else {"not_applicable"}
bad = [(row.get("sample_id"), row.get("status")) for row in raw if row.get("status") not in allowed]
assert len(raw) == expected, (method, "raw", len(raw))
assert len(evaluation) == expected, (method, "evaluation", len(evaluation))
assert len({row["sample_id"] for row in raw}) == expected
assert not bad, bad
if backend == "gpu":
    model_manifest = json.load(open(root / "model_manifest.json"))
    for key in ("aggregate_sha256", "tokenizer_sha256", "model_config_sha256"):
        actual = model_manifest.get(key)
        locked = protocol["model_lock"][key]
        assert actual == locked, (method, key, actual, locked)
print(
    f"COMPLETE: {method}; rows={expected}; "
    f"truncated={sum(bool(row.get('input_truncated')) for row in raw)}; "
    f"limited={sum(bool(row.get('hit_max_new_tokens')) for row in raw)}"
)
PY_GATE
done

"$PYTHON_BIN" scripts/server/summarize_calibration_matrix.py \
    --result-root "$RESULT_ROOT" \
    --protocol configs/experiments/calibration_protocol.json \
    --output artifacts/reports/calibration_go_decision.json \
    --markdown-output artifacts/reports/calibration_matrix_summary.md

bash scripts/server/package_calibration_results.sh

echo
echo "CALIBRATION MATRIX: COMPLETE"
echo "Read: artifacts/reports/calibration_go_decision.json"
echo "Download the archive named in: artifacts/server/last_calibration_result_archive.txt"
