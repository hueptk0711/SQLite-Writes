#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

RESULT_ROOT="${1:-experiments/external_holdout/final300_qwen25_7b_20260731}"
for required in \
    "$RESULT_ROOT/d_fs_m" \
    "$RESULT_ROOT/j_fs_m" \
    "$RESULT_ROOT/s_fs_v2_m" \
    "$RESULT_ROOT/mp_fs_m" \
    "$RESULT_ROOT/mp_fs_plus" \
    "$RESULT_ROOT/gold_mp" \
    artifacts/reports/final_matrix_results.json \
    artifacts/reports/final_main_table.csv \
    artifacts/reports/final_matrix_summary.md \
    configs/experiments/final_protocol.json \
    diagnostics/final_asset_preflight.json \
    artifacts/server/final_model_manifest.json \
    artifacts/server/hf_final_qwen25_7b_in28672_out4096.json \
    artifacts/environment/environment_manifest_final_server.json
do
    if [[ ! -e "$required" ]]; then
        echo "STOP: cannot package without $required"
        exit 2
    fi
done

python3 - artifacts/reports/final_matrix_results.json <<'PY_RESULT_GATE'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["status"] == "pass"
assert report["paper_result_eligible"] is True
assert report["sample_count"] == 300
assert report["method_count"] == 6
print("FINAL RESULT REPORT: PASS AND PAPER-RESULT ELIGIBLE")
PY_RESULT_GATE

mkdir -p dist/results
RUN_ID="mp_fs_plus_final300_$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$PROJECT_ROOT/dist/results/${RUN_ID}.tar.gz"

tar -czf "$ARCHIVE" \
    "$RESULT_ROOT" \
    artifacts/reports/final_matrix_results.json \
    artifacts/reports/final_main_table.csv \
    artifacts/reports/final_matrix_summary.md \
    artifacts/calibration/calibration_go_decision.json \
    artifacts/server/final_model_manifest.json \
    artifacts/server/hf_final_qwen25_7b_in28672_out4096.json \
    artifacts/environment/environment_manifest_final_server.json \
    artifacts/environment/runtime_source_final_server.json \
    diagnostics/final_asset_preflight.json \
    diagnostics/final_metadata_audit.json \
    diagnostics/final_preflight.sha256 \
    diagnostics/final_*.log \
    configs/experiments/final_protocol.json \
    data/external_holdout/FINAL_RELEASE_MANIFEST.json \
    data/external_holdout/final_validation_report.json \
    data/external_holdout/SHA256SUMS.txt

sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"
printf '%s\n' "$ARCHIVE" > artifacts/server/last_final_result_archive.txt
printf '%s\n' "${ARCHIVE}.sha256" > artifacts/server/last_final_result_checksum.txt

echo "RESULT ARCHIVE: $ARCHIVE"
echo "RESULT CHECKSUM: ${ARCHIVE}.sha256"
