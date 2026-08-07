#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

for required in \
    experiments/calibration/full_locked_v3_in28672_out4096 \
    diagnostics/calibration_smoke_v3_in28672_out4096_summary.json \
    artifacts/reports/calibration_go_decision.json \
    artifacts/reports/calibration_matrix_summary.md \
    artifacts/server/hf_calibration_locked_in28672_out4096.json \
    artifacts/server/calibration_model_manifest.json \
    artifacts/environment/environment_manifest_server.json \
    artifacts/environment/runtime_source_server.json
do
    if [[ ! -e "$required" ]]; then
        echo "STOP: cannot package without $required"
        exit 2
    fi
done

mkdir -p dist/results artifacts/server
RUN_ID="mp_fs_plus_calibration60_$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$PROJECT_ROOT/dist/results/${RUN_ID}.tar.gz"

tar -czf "$ARCHIVE" \
    experiments/calibration/full_locked_v3_in28672_out4096 \
    experiments/calibration/smoke_locked_v3_in28672_out4096 \
    diagnostics \
    artifacts/reports \
    artifacts/server/hf_calibration_locked_in28672_out4096.json \
    artifacts/server/calibration_model_manifest.json \
    artifacts/environment/environment_manifest_server.json \
    artifacts/environment/runtime_source_server.json \
    artifacts/audit/calibration_metadata.json \
    artifacts/audit/calibration_gold_mp.json \
    configs/experiments/calibration_protocol.json \
    data/calibration/calibration_freeze_manifest.json \
    data/calibration/dataset.sha256
sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"
printf '%s\n' "$ARCHIVE" > artifacts/server/last_calibration_result_archive.txt
printf '%s\n' "${ARCHIVE}.sha256" > artifacts/server/last_calibration_result_checksum.txt

echo "RESULT ARCHIVE: $ARCHIVE"
echo "RESULT CHECKSUM: ${ARCHIVE}.sha256"
