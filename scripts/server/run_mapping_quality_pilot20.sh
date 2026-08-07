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

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export HF_HOME="${HF_HOME:-$HOME/hue_ptk/hf_cache}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"

MODEL_CONFIG="${NLDB_MODEL_CONFIG:-configs/models/hf_local.qwen25_7b_ctx32768_v2.json}"
ENV_MANIFEST="${NLDB_ENVIRONMENT_MANIFEST:-artifacts/environment/environment_manifest_server.json}"
PROTOCOL="configs/experiments/dev_pilot_protocol.json"
DATA="data/frozen/dev/dataset_dev_v3.json"
IDS="data/splits/pilot/dev_mapping_quality_pilot20_ids.txt"
GOLD="data/frozen/dev/gold_write_plans_dev_v3.jsonl"
RESULT_ROOT="${NLDB_MAPPING_PILOT_ROOT:-experiments/dev/mapping_quality_pilot20_v1}"
LOCK_DIR="diagnostics/mapping_quality_pilot20.lock"

for required_path in \
    "$MODEL_CONFIG" \
    "$ENV_MANIFEST" \
    "$PROTOCOL" \
    "$DATA" \
    "$IDS" \
    "$GOLD" \
    "$NLDB_PROFILE_DIR" \
    "$NLDB_DATABASE_ROOT"
do
    if [[ ! -e "$required_path" ]]; then
        echo "MISSING REQUIRED PATH: $required_path"
        exit 1
    fi
done

python3 - "$DATA" "$IDS" "$PROTOCOL" "$NLDB_DATABASE_ROOT" <<'PY_PREFLIGHT'
import hashlib
import json
import sys
from pathlib import Path

data = json.load(open(sys.argv[1]))
ids = [line.strip() for line in open(sys.argv[2]) if line.strip()]
protocol = json.load(open(sys.argv[3]))
database_root = Path(sys.argv[4])

dataset_ids = {str(sample["id"]) for sample in data}
assert len(ids) == 20
assert len(set(ids)) == 20
assert set(ids) <= dataset_ids

expected = protocol["database_identity"]["databases"]
actual = {}
for db_id in sorted(expected):
    candidates = [
        database_root / db_id / f"{db_id}.sqlite",
        database_root / db_id / f"{db_id}.db",
        database_root / f"{db_id}.sqlite",
        database_root / f"{db_id}.db",
    ]
    path = next((item for item in candidates if item.exists()), None)
    if path is None:
        raise SystemExit(f"MISSING DATABASE: {db_id}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual[db_id] = digest.hexdigest()

changed = {
    db_id: {"expected": expected[db_id], "actual": actual[db_id]}
    for db_id in expected
    if actual.get(db_id) != expected[db_id]
}
if changed:
    print(json.dumps(changed, ensure_ascii=False, indent=2))
    raise SystemExit("SOURCE DATABASE IDENTITY CHECK: FAILED")

print("PILOT IDS: VERIFIED")
print("SOURCE DATABASE IDENTITY: VERIFIED")
PY_PREFLIGHT

mkdir -p diagnostics "$RESULT_ROOT"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "STOP: mapping-quality pilot is already running or locked"
    exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

METHODS=(
    "mp_fs|configs/proposed/mp_fs.json"
    "mp_fs_r_semi|configs/proposed/mp_fs_r_semi.json"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_config <<< "$spec"
    output_dir="$RESULT_ROOT/$slug"
    run_log="diagnostics/mapping_quality_pilot20_${slug}.log"

    echo
    echo "=================================================="
    echo "START: $slug"
    echo "TIME:  $(date --iso-8601=seconds)"
    echo "OUT:   $output_dir"
    echo "=================================================="

    if [[ -e "$output_dir" ]]; then
        echo "STOP: output already exists: $output_dir"
        exit 1
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
assert not any(row.get("hit_max_new_tokens") for row in raw)
assert all(repair.get("status") == "success" for repair in repairs)
assert not any(repair.get("input_truncated") for repair in repairs)
assert not any(repair.get("hit_max_new_tokens") for repair in repairs)

loop_probe = next(
    row for row in raw if row["sample_id"] == "seed_000318"
)
assert loop_probe["hit_max_new_tokens"] is False

print(
    f"COMPLETE: {slug}",
    f"rows={len(raw)}",
    "input_truncated=0",
    "output_limited=0",
    f"repair_calls={len(repairs)}",
    "repair_input_truncated=0",
    "repair_limited=0",
    f"missing_artifacts={len(missing)}",
)
PY_GATE

    echo "END:  $slug"
    echo "TIME: $(date --iso-8601=seconds)"
done

python3 - "$RESULT_ROOT" <<'PY_SUMMARY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
summary = {}
for method in ("mp_fs", "mp_fs_r_semi"):
    metrics = json.load(open(root / method / "metrics.json"))
    summary[method] = {
        "samples": metrics["samples"],
        "parse_success": metrics["parse_success"],
        "build_success": metrics["build_success"],
        "execution_success": metrics["execution_success"],
        "target_state_accuracy": metrics["target_state_accuracy"],
        "strict_full_state_accuracy": metrics[
            "strict_full_state_accuracy"
        ],
        "input_truncation_rate": metrics["input_truncation_rate"],
        "output_limit_hit_rate": metrics["output_limit_hit_rate"],
        "multi_table": (metrics.get("slices") or {}).get("multi_table"),
        "repair_attempt_rate_eligible": metrics.get(
            "repair_attempt_rate_eligible"
        ),
        "repair_accept_rate_attempted": metrics.get(
            "repair_accept_rate_attempted"
        ),
    }

target = Path("diagnostics/mapping_quality_pilot20_summary.json")
target.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
print("SUMMARY SAVED:", target)
PY_SUMMARY

echo
echo "MAPPING QUALITY PILOT 20: COMPLETE"
echo "FINISHED: $(date --iso-8601=seconds)"
