#!/usr/bin/env python3
"""Validate Stage7C-A5 gold-provenance erratum artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.data.build_stage7c_a5_gold_provenance_erratum import (  # noqa: E402
    CORRECTIONS,
    ERRATUM_ARTIFACTS,
    RESULT_DIR_NAME,
    SERVER_RUN_ID,
    SERVER_TAR_SHA256,
    STAGE_NAME,
    canonical_json,
    sha256_file,
)


REQUIRED_FILES = {
    *ERRATUM_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/primary_summary.json",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/primary_case_results.jsonl",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/raw_primary_phase_o_generations.jsonl",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/run_manifest.json",
}
EXPECTED_CORRECTIONS = {
    ("primary", "stage7c_a5_primary_english_003", "COL_4"): ("SPAN_0013", "SPAN_0030", 75, 76),
    ("primary", "stage7c_a5_primary_english_011", "COL_2"): ("SPAN_0009", "SPAN_0019", 42, 47),
    ("diagnostic", "stage7c_a5_fresh_english_011", "COL_2"): ("SPAN_0008", "SPAN_0021", 33, 41),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(stage_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_FILES):
        if not (stage_dir / rel).is_file():
            failures.append(f"missing_required_file:{rel}")
    if failures:
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}

    erratum = read_json(stage_dir / "GOLD_PROVENANCE_ERRATUM.json")
    audit = read_json(stage_dir / "DUPLICATE_LITERAL_GOLD_AUDIT.json")
    replay_summary = read_json(stage_dir / "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json")
    reclassification = read_json(stage_dir / "SERVER_RESULT_RECLASSIFICATION_PATCH0.json")
    lock = read_json(stage_dir / "ERRATUM_LOCK.json")
    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    corrected_primary = read_jsonl(stage_dir / "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl")
    corrected_diagnostic = read_jsonl(stage_dir / "CORRECTED_A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl")
    replay_rows = read_jsonl(stage_dir / "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_RESULTS.jsonl")

    if erratum.get("corrections") != CORRECTIONS:
        failures.append("corrections_drifted")
    observed_corrections = {
        (item["split"], item["sample_id"], item["column_ref"]): (
            item["old_candidate_span_ref"],
            item["new_candidate_span_ref"],
            item["new_start_char"],
            item["new_end_char"],
        )
        for item in erratum.get("corrections", [])
    }
    if observed_corrections != EXPECTED_CORRECTIONS:
        failures.append("correction_coordinate_set_mismatch")

    primary_by_id = {row["sample_id"]: row for row in corrected_primary}
    diagnostic_by_id = {row["sample_id"]: row for row in corrected_diagnostic}
    if primary_by_id["stage7c_a5_primary_english_003"]["label_side_expected"]["phase_o"]["column_span_refs"].get("COL_4") != "SPAN_0030":
        failures.append("primary_003_col4_not_corrected")
    if primary_by_id["stage7c_a5_primary_english_011"]["label_side_expected"]["phase_o"]["column_span_refs"].get("COL_2") != "SPAN_0019":
        failures.append("primary_011_col2_not_corrected")
    if diagnostic_by_id["stage7c_a5_fresh_english_011"]["label_side_expected"]["phase_o"]["column_span_refs"].get("COL_2") != "SPAN_0021":
        failures.append("diagnostic_011_col2_not_corrected")

    if audit.get("status") != "PASS":
        failures.append("duplicate_literal_audit_not_pass")
    if audit.get("duplicate_literal_count") != 3 or audit.get("primary_duplicate_literal_count") != 2 or audit.get("diagnostic_duplicate_literal_count") != 1:
        failures.append("duplicate_literal_counts_mismatch")
    if audit.get("implicit_first_occurrence_forbidden_count") != 0:
        failures.append("implicit_first_occurrence_still_allowed")
    if len(audit.get("rows", [])) != 99:
        failures.append("duplicate_literal_audit_row_count_mismatch")

    if replay_summary.get("old_gold_primary_pass_count") != "1/12":
        failures.append("old_gold_pass_count_must_be_1_of_12")
    if replay_summary.get("corrected_primary_pass_count") != "2/12":
        failures.append("corrected_pass_count_must_be_2_of_12")
    if replay_summary.get("corrected_pass_case_ids") != ["stage7c_a5_primary_english_003", "stage7c_a5_primary_english_012"]:
        failures.append("corrected_pass_case_ids_mismatch")
    if replay_summary.get("original_classification_superseded") != "SUPERSEDED_BY_GOLD_PROVENANCE_ERRATUM":
        failures.append("old_classification_not_marked_superseded")
    if replay_summary.get("model_called") is not False or replay_summary.get("gpu_called") is not False:
        failures.append("offline_replay_must_not_call_model_or_gpu")
    if len(replay_rows) != 12:
        failures.append("offline_replay_must_have_12_rows")

    if reclassification.get("source_tar_sha256") != SERVER_TAR_SHA256:
        failures.append("source_tar_sha256_mismatch")
    if reclassification.get("evidence_integrity_status") != "PASS" or reclassification.get("protocol_compliance_status") != "PASS":
        failures.append("old_gold_evidence_or_protocol_not_pass")
    if reclassification.get("corrected_primary_pass_count") != "2/12" or reclassification.get("corrected_primary_gate_status") != "FAIL":
        failures.append("reclassification_result_mismatch")
    if reclassification.get("gretel_pilot_opened") is not False or reclassification.get("diagnostics_run") is not False:
        failures.append("gretel_or_diagnostics_opened")

    if lock.get("status") != "REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL":
        failures.append("erratum_lock_status_mismatch")
    if lock.get("correction_count") != 3 or lock.get("source_tar_sha256") != SERVER_TAR_SHA256:
        failures.append("erratum_lock_counts_or_source_mismatch")

    for item in manifest.get("artifacts", []):
        rel = item["path"]
        path = stage_dir / rel
        if not path.is_file():
            failures.append(f"manifested_artifact_missing:{rel}")
        elif item.get("sha256") != sha256_file(path):
            failures.append(f"manifest_hash_mismatch:{rel}")
    expected_combined = __import__("hashlib").sha256(json.dumps(manifest.get("artifacts", []), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if manifest.get("combined_scientific_artifacts_sha256") != expected_combined:
        failures.append("manifest_combined_hash_mismatch")

    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "old_gold_primary_pass_count": replay_summary.get("old_gold_primary_pass_count"),
        "corrected_primary_pass_count": replay_summary.get("corrected_primary_pass_count"),
        "source_tar_sha256": reclassification.get("source_tar_sha256"),
        "gretel_pilot_opened": False,
        "diagnostics_run": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    args = parser.parse_args()
    report = validate(args.stage_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
