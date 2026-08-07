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
PROTOCOL="configs/experiments/dev_pilot_protocol.json"
PREFLIGHT_GATE="diagnostics/preflight20_gate_summary.json"
DATA="data/frozen/dev/dataset_dev_v3.json"
IDS="data/frozen/dev/dev_ids_v3.txt"
GOLD="data/frozen/dev/gold_write_plans_dev_v3.jsonl"
RESULT_ROOT="${NLDB_DEV120_ROOT:-experiments/dev/dev120_mnt16384_v1}"
LOCK_DIR="diagnostics/dev120_matrix.lock"

for required_path in \
    "$MODEL_CONFIG" \
    "$ENV_MANIFEST" \
    "$PROTOCOL" \
    "$PREFLIGHT_GATE" \
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

python3 - "$PROTOCOL" "$PREFLIGHT_GATE" "$IDS" "$NLDB_DATABASE_ROOT" <<'PY_PREFLIGHT'
import hashlib
import json
import sys
from pathlib import Path

protocol = json.load(open(sys.argv[1]))
preflight = json.load(open(sys.argv[2]))
ids = [line.strip() for line in open(sys.argv[3]) if line.strip()]
database_root = Path(sys.argv[4])

assert protocol["sample_count"] == 120
assert len(ids) == 120
assert len(set(ids)) == 120
assert preflight["gate"]["infrastructure_ok"] is True
assert preflight["gate"]["checkpoint_resume_verified"] is True
assert preflight["gate"]["structured_methods_output_limit_zero"] is True

go_no_go = protocol["go_no_go"]
assert (
    go_no_go[
        "mp_fs_target_state_max_absolute_gap_to_best_primary_comparator"
    ]
    == 0.02
)
assert (
    go_no_go["mp_fs_required_slice_improvement"][
        "minimum_absolute_gain"
    ]
    == 0.01
)

expected_databases = protocol["database_identity"]["databases"]
actual_databases = {}
for db_id in sorted(expected_databases):
    candidates = [
        database_root / db_id / f"{db_id}.sqlite",
        database_root / db_id / f"{db_id}.db",
        database_root / f"{db_id}.sqlite",
        database_root / f"{db_id}.db",
    ]
    database_path = next((path for path in candidates if path.exists()), None)
    if database_path is None:
        raise SystemExit(f"MISSING DATABASE: {db_id}")
    digest = hashlib.sha256()
    with database_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_databases[db_id] = digest.hexdigest()

changed = {
    db_id: {
        "expected": expected_databases[db_id],
        "actual": actual_databases.get(db_id),
    }
    for db_id in sorted(expected_databases)
    if actual_databases.get(db_id) != expected_databases[db_id]
}
if changed:
    print(json.dumps(changed, ensure_ascii=False, indent=2))
    raise SystemExit("SOURCE DATABASE IDENTITY CHECK: FAILED")

aggregate = hashlib.sha256(
    json.dumps(
        actual_databases,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
assert aggregate == protocol["database_identity"]["aggregate_sha256"]

print("PRE-FLIGHT 20 GATE AND NUMERIC DEV THRESHOLDS: VERIFIED")
print("SOURCE DATABASE IDENTITY: VERIFIED")
PY_PREFLIGHT

available_kb="$(df -Pk "$PROJECT_ROOT" | awk 'NR==2 {print $4}')"
if [[ "$available_kb" -lt 5242880 ]]; then
    echo "STOP: less than 5 GiB disk space is available"
    df -h "$PROJECT_ROOT"
    exit 1
fi

mkdir -p diagnostics "$RESULT_ROOT"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "STOP: another dev-120 matrix appears to be running"
    exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

METHODS=(
    "d_fs|configs/baselines/direct_fs.json|gpu"
    "j_fs_common|configs/baselines/record_json_fs_common.json|gpu"
    "s_fs_v2|configs/baselines/structured_fs_v2.json|gpu"
    "mp_fs|configs/proposed/mp_fs.json|gpu"
    "mp_fs_r_semi|configs/proposed/mp_fs_r_semi.json|gpu"
    "d_zs|configs/baselines/direct_zs.json|gpu"
    "j_zs|configs/baselines/record_json_zs.json|gpu"
    "mp_zs|configs/proposed/mp_zs.json|gpu"
    "gold_mp|configs/oracles/gold_mp.json|oracle"
)

for spec in "${METHODS[@]}"; do
    IFS='|' read -r slug method_config backend <<< "$spec"
    output_dir="$RESULT_ROOT/$slug"
    run_log="diagnostics/dev120_${slug}.log"

    echo
    echo "=================================================="
    echo "START: $slug"
    echo "TIME:  $(date --iso-8601=seconds)"
    echo "OUT:   $output_dir"
    echo "=================================================="

    if [[ -f "$output_dir/manifest.json" ]] \
        && [[ -f "$output_dir/evaluation.jsonl" ]] \
        && [[ "$(wc -l < "$output_dir/evaluation.jsonl")" -eq 120 ]]
    then
        echo "SKIP: $slug already complete"
        continue
    fi

    common_args=(
        --stage dev
        --config "$method_config"
        --data "$DATA"
        --ids "$IDS"
        --gold-plans "$GOLD"
        --output-dir "$output_dir"
        --dependency-lock requirements-inference.lock.txt
        --environment-manifest "$ENV_MANIFEST"
    )
    extra_args=()

    if [[ "$backend" == "gpu" ]]; then
        extra_args+=(--inference-config "$MODEL_CONFIG")
    fi
    if [[ "$slug" == "s_fs_v2" ]]; then
        extra_args+=(--v2-source "$NLDB_V2_SOURCE")
    fi

    if ! python3 scripts/experiments/run_method.py \
        "${common_args[@]}" \
        "${extra_args[@]}" \
        > "$run_log" 2>&1
    then
        echo "FAILED: $slug"
        tail -n 100 "$run_log"
        exit 1
    fi

    python3 - "$output_dir" "$slug" "$backend" <<'PY_GATE'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
slug = sys.argv[2]
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
]
if backend == "gpu":
    required.append("model_manifest.json")

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

expected_statuses = (
    {"success", "input_truncation_error"}
    if backend == "gpu"
    else {"not_applicable"}
)

assert not missing, missing
assert len(raw) == 120
assert len(evaluation) == 120
assert len({row["sample_id"] for row in raw}) == 120
assert {row.get("status") for row in raw} <= expected_statuses
assert all(
    repair.get("status") in {"success", "input_truncation_error"}
    for repair in repairs
)

print(
    f"COMPLETE: {slug}",
    f"rows={len(raw)}",
    f"input_truncated={sum(bool(x.get('input_truncated')) for x in raw)}",
    f"output_limited={sum(bool(x.get('hit_max_new_tokens')) for x in raw)}",
    f"oom_fallback={sum(bool(x.get('oom_fallback_used')) for x in raw)}",
    f"repair_calls={len(repairs)}",
    f"repair_input_truncated={sum(bool(x.get('input_truncated')) for x in repairs)}",
    f"repair_limited={sum(bool(x.get('hit_max_new_tokens')) for x in repairs)}",
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
methods = [
    "d_fs",
    "j_fs_common",
    "s_fs_v2",
    "mp_fs",
    "mp_fs_r_semi",
    "d_zs",
    "j_zs",
    "mp_zs",
    "gold_mp",
]

summary = {}
for method in methods:
    run = root / method
    metrics = json.load(open(run / "metrics.json"))
    manifest = json.load(open(run / "manifest.json"))
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
        "repair_attempt_rate_eligible": metrics.get(
            "repair_attempt_rate_eligible"
        ),
        "repair_accept_rate_attempted": metrics.get(
            "repair_accept_rate_attempted"
        ),
        "run_lock_sha256": manifest["run_lock_sha256"],
    }

target = Path("diagnostics/dev120_matrix_summary.json")
target.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

assert all(item["samples"] == 120 for item in summary.values())
assert summary["gold_mp"]["strict_full_state_accuracy"] >= 0.99

print("DEV-120 SUMMARY SAVED:", target)
PY_SUMMARY

echo
echo "DEV-120 MATRIX: COMPLETE"
echo "FINISHED: $(date --iso-8601=seconds)"
