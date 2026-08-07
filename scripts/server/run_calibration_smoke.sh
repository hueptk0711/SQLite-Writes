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
RESULT_ROOT="${NLDB_CALIBRATION_SMOKE_ROOT:-experiments/calibration/smoke_locked_v3_in28672_out4096}"
PROFILE_DIR="data/calibration/authoring_kit/profiles"
DB_ROOT="data/calibration/authoring_kit/databases"
DATA="data/calibration/dataset.json"
IDS="data/calibration/calibration_smoke_ids.txt"
GOLD="data/calibration/gold_write_plans.jsonl"
EXPECTED=7

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

for required in \
    "$PYTHON_BIN" "$MODEL_CONFIG" "$MODEL_MANIFEST" "$ENV_MANIFEST" \
    "$PROFILE_DIR" "$DB_ROOT" "$DATA" "$IDS" "$GOLD"
do
    if [[ ! -e "$required" ]]; then
        echo "STOP: missing preflight artifact: $required"
        exit 2
    fi
done

"$PYTHON_BIN" scripts/server/validate_calibration_bundle.py \
    --project-root "$PROJECT_ROOT" \
    --model-manifest "$MODEL_MANIFEST" \
    --inference-config "$MODEL_CONFIG" \
    --environment-manifest "$ENV_MANIFEST" \
    --require-gpu \
    --output diagnostics/calibration_smoke_precheck.json

mkdir -p diagnostics "$RESULT_ROOT"
LOCK_DIR="diagnostics/calibration_smoke.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "STOP: calibration smoke appears to be running already"
    exit 2
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

METHODS=(
    "d_fs_m|D-FS-M|configs/final/d_fs_m.json"
    "j_fs_m|J-FS-M|configs/final/j_fs_m.json"
    "mp_fs_m|MP-FS-M|configs/final/mp_fs_m.json"
    "mp_fs_plus|MP-FS+|configs/final/mp_fs_plus.json"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_id method_config <<< "$spec"
    output_dir="$RESULT_ROOT/$slug"
    run_log="diagnostics/calibration_smoke_v3_in28672_out4096_${slug}.log"
    echo
    echo "=================================================="
    echo "SMOKE START: $method_id"
    echo "TIME: $(date --iso-8601=seconds)"
    echo "OUT: $output_dir"
    echo "=================================================="

    if [[ -f "$output_dir/evaluation.jsonl" ]] \
        && [[ "$(wc -l < "$output_dir/evaluation.jsonl")" -eq "$EXPECTED" ]]
    then
        echo "SKIP: $method_id already has $EXPECTED evaluation rows"
    else
        "$PYTHON_BIN" scripts/experiments/run_method.py \
            --stage calibration \
            --config "$method_config" \
            --inference-config "$MODEL_CONFIG" \
            --data "$DATA" \
            --ids "$IDS" \
            --gold-plans "$GOLD" \
            --profile-dir "$PROFILE_DIR" \
            --db-root "$DB_ROOT" \
            --dependency-lock requirements-inference.lock.txt \
            --environment-manifest "$ENV_MANIFEST" \
            --output-dir "$output_dir" \
            2>&1 | tee "$run_log"
    fi

    "$PYTHON_BIN" - \
        "$output_dir" \
        "$EXPECTED" \
        "$method_id" \
        configs/experiments/calibration_protocol.json <<'PY_GATE'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = int(sys.argv[2])
method = sys.argv[3]
protocol = json.load(open(sys.argv[4]))
required = [
    "config.json", "run_lock.json", "manifest.json",
    "raw_generations.jsonl", "evaluation.jsonl", "metrics.json",
    "model_manifest.json", "error_analysis.csv",
]
missing = [name for name in required if not (root / name).is_file()]
if missing:
    raise SystemExit(f"{method}: missing artifacts: {missing}")
raw = [json.loads(line) for line in open(root / "raw_generations.jsonl") if line.strip()]
evaluation = [json.loads(line) for line in open(root / "evaluation.jsonl") if line.strip()]
failures = [row for row in raw if row.get("status") != "success"]
truncated = [row["sample_id"] for row in raw if row.get("input_truncated")]
limited = [row["sample_id"] for row in raw if row.get("hit_max_new_tokens")]
assert len(raw) == expected, (method, "raw", len(raw))
assert len(evaluation) == expected, (method, "evaluation", len(evaluation))
assert len({row["sample_id"] for row in raw}) == expected
assert not failures, [(row.get("sample_id"), row.get("status")) for row in failures]
assert not truncated, (method, "input_truncated", truncated)
assert not limited, (method, "output_limited", limited)
model_manifest = json.load(open(root / "model_manifest.json"))
for key in ("aggregate_sha256", "tokenizer_sha256", "model_config_sha256"):
    actual = model_manifest.get(key)
    locked = protocol["model_lock"][key]
    assert actual == locked, (method, key, actual, locked)
print(f"SMOKE PASS: {method}; rows={expected}; truncation=0; output_limit=0")
PY_GATE
done

"$PYTHON_BIN" - "$RESULT_ROOT" <<'PY_SUMMARY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
methods = ["d_fs_m", "j_fs_m", "mp_fs_m", "mp_fs_plus"]
summary = {
    "status": "pass",
    "purpose": "infrastructure_only_not_reportable",
    "sample_count_per_method": 7,
    "methods": {},
}
for method in methods:
    metrics = json.load(open(root / method / "metrics.json"))
    manifest = json.load(open(root / method / "manifest.json"))
    summary["methods"][method] = {
        "samples": metrics["samples"],
        "parse_success": metrics["parse_success"],
        "build_success": metrics["build_success"],
        "execution_success": metrics["execution_success"],
        "input_truncation_rate": metrics["input_truncation_rate"],
        "output_limit_hit_rate": metrics["output_limit_hit_rate"],
        "run_lock_sha256": manifest["run_lock_sha256"],
    }
target = Path("diagnostics/calibration_smoke_v3_in28672_out4096_summary.json")
target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY_SUMMARY

echo
echo "CALIBRATION SMOKE: PASS"
echo "Next: bash scripts/server/run_calibration_matrix.sh"
