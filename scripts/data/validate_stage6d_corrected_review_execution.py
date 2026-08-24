#!/usr/bin/env python3
"""Validate Stage 6D corrected-gold C01/C02 review execution artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.ingest_stage6d_corrected_review import (
    ARCHIVE_NAME,
    STAGE6D_PATCH1_COMMIT,
    build_agreement_report,
    read_json,
    read_packet,
    sha256_file,
    validate_reviewer_rows,
)
from scripts.data.create_stage6d_corrected_gold_review_setup import REVIEW_PACKET_COLUMNS


EXPECTED_RESOLUTION_STATUS = "ALL_21_CORRECTED_ITEMS_ACCEPTED_PENDING_STAGE6_REGISTRATION_REVISION"
EXPECTED_STAGE = "Stage6D_CORRECTED_REVIEW_EXECUTION"
EXPECTED_REVIEWER_ISOLATION_ATTESTATION = {
    "C01_C02_decisions_or_notes_shared_before_both_submitted": False,
    "cross_reviewer_discussion_before_submission": False,
    "model_predictions_visible_to_reviewers": False,
    "C01_is_R04": False,
    "C02_is_R04": False,
}


def expected_resolution(agreement: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "Stage6D_CORRECTED_ITEMS_RESOLUTION",
        "status": EXPECTED_RESOLUTION_STATUS
        if agreement["agreed_approved_count"] == 21
        else "PENDING_CORRECTED_REVIEW_RESOLUTION",
        "corrected_item_count": 21,
        "corrected_items_final_approved_count": agreement["agreed_approved_count"],
        "corrected_items_final_rejected_count": agreement["agreed_rejected_count"],
        "corrected_items_disagreement_count": agreement["disagreement_count"],
        "C03_required": agreement["blind_C03_required"],
        "C03_packet_created": False,
        "final_gold_freeze_created": False,
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "next_required_step": "Stage6_registration_revision_for_19_source_invalid_items_and_final_denominator_lock",
    }


def validate_stage6d_corrected_review_execution(
    execution_dir: Path = PROJECT_ROOT / "stage6_corrected_gold_review_execution",
    setup_dir: Path = PROJECT_ROOT / "stage6_corrected_gold_review_setup",
) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = execution_dir / "CORRECTED_REVIEW_EXECUTION_MANIFEST.json"
    agreement_path = execution_dir / "C01_C02_CORRECTED_AGREEMENT_REPORT.json"
    resolution_path = execution_dir / "CORRECTED_ITEMS_RESOLUTION_REPORT.json"
    c01_path = execution_dir / "submissions" / "stage6d_corrected_gold_review_C01.submitted.tsv"
    c02_path = execution_dir / "submissions" / "stage6d_corrected_gold_review_C02.submitted.tsv"
    c01_template_path = setup_dir / "corrected_review_packets" / "stage6d_corrected_gold_review_C01.tsv"
    c02_template_path = setup_dir / "corrected_review_packets" / "stage6d_corrected_gold_review_C02.tsv"
    required = [
        manifest_path,
        agreement_path,
        resolution_path,
        c01_path,
        c02_path,
        c01_template_path,
        c02_template_path,
        setup_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json",
        setup_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json",
        setup_dir / "artifacts" / "corrected_gold_review_items.jsonl",
    ]
    for path in required:
        if not path.is_file():
            violations.append(f"missing_required_file:{path.as_posix()}")
    if violations:
        return {"status": "FAIL", "violations": violations, "stage": EXPECTED_STAGE}

    manifest = read_json(manifest_path)
    stored_agreement = read_json(agreement_path)
    stored_resolution = read_json(resolution_path)
    c01_template_fields, c01_template_rows = read_packet(c01_template_path)
    c02_template_fields, c02_template_rows = read_packet(c02_template_path)
    c01_fields, c01_rows = read_packet(c01_path)
    c02_fields, c02_rows = read_packet(c02_path)

    if c01_template_fields != REVIEW_PACKET_COLUMNS:
        violations.append("C01_template_columns_changed")
    if c02_template_fields != REVIEW_PACKET_COLUMNS:
        violations.append("C02_template_columns_changed")
    violations.extend(validate_reviewer_rows("C01", c01_fields, c01_rows, {row["stage6_sample_id"]: row for row in c01_template_rows}))
    violations.extend(validate_reviewer_rows("C02", c02_fields, c02_rows, {row["stage6_sample_id"]: row for row in c02_template_rows}))
    if not violations:
        rebuilt_agreement = build_agreement_report(c01_rows, c02_rows)
        if stored_agreement != rebuilt_agreement:
            violations.append("agreement_report_not_reproducible_from_submissions")
        rebuilt_resolution = expected_resolution(rebuilt_agreement)
        if stored_resolution != rebuilt_resolution:
            violations.append("resolution_report_not_reproducible_from_agreement")
    else:
        rebuilt_agreement = {
            "agreed_approved_count": 0,
            "agreed_rejected_count": 0,
            "disagreement_count": 0,
            "blind_C03_required": False,
        }

    expected_false_fields = [
        "model_called",
        "gpu_called",
        "confirmation_run_allowed_now",
        "final_gold_freeze_created",
        "C03_required",
        "C03_packet_created",
    ]
    for field in expected_false_fields:
        if manifest.get(field) is not False:
            violations.append(f"manifest_{field}_not_false")
    if manifest.get("stage") != EXPECTED_STAGE:
        violations.append("manifest_stage_changed")
    if manifest.get("stage6d_setup_patch1_commit") != STAGE6D_PATCH1_COMMIT:
        violations.append("manifest_stage6d_patch1_commit_mismatch")
    if manifest.get("status") != EXPECTED_RESOLUTION_STATUS:
        violations.append("manifest_status_changed")
    if manifest.get("pseudonymous_reviewer_role_ids") != ["C01", "C02"]:
        violations.append("manifest_pseudonymous_reviewer_role_ids_mismatch")
    if manifest.get("distinct_reviewer_assertion") != "required_by_protocol_and_recorded_as_execution_condition":
        violations.append("manifest_distinct_reviewer_assertion_mismatch")
    if manifest.get("reviewer_isolation_attestation") != EXPECTED_REVIEWER_ISOLATION_ATTESTATION:
        violations.append("manifest_reviewer_isolation_attestation_mismatch")
    expected_submission_hashes = {
        "C01_submission_sha256": sha256_file(c01_path),
        "C02_submission_sha256": sha256_file(c02_path),
    }
    if manifest.get("submission_hashes") != expected_submission_hashes:
        violations.append("manifest_submission_hashes_mismatch")
    expected_timestamps = {
        "C01_submission_source_mtime": manifest.get("C01_source_mtime"),
        "C02_submission_source_mtime": manifest.get("C02_source_mtime"),
    }
    if manifest.get("submission_timestamps_recorded_from_source_files") != expected_timestamps:
        violations.append("manifest_submission_timestamps_mismatch")
    for field, expected in {
        "corrected_item_count": 21,
        "agreed_approved_count": 21,
        "agreed_rejected_count": 0,
        "disagreement_count": 0,
    }.items():
        if manifest.get(field) != expected:
            violations.append(f"manifest_{field}_mismatch")
    for field, path in {
        "C01_submission_sha256": c01_path,
        "C02_submission_sha256": c02_path,
        "setup_lock_sha256": setup_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json",
        "protocol_lock_sha256": setup_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json",
        "corrected_gold_review_items_sha256": setup_dir / "artifacts" / "corrected_gold_review_items.jsonl",
        "agreement_report_sha256": agreement_path,
        "resolution_report_sha256": resolution_path,
    }.items():
        if manifest.get(field) != sha256_file(path):
            violations.append(f"manifest_{field}_mismatch")

    archive_info = manifest.get("archive", {})
    archive_path = execution_dir / ARCHIVE_NAME
    if archive_info.get("path") != ARCHIVE_NAME:
        violations.append("archive_path_mismatch")
    if not archive_path.is_file():
        violations.append("execution_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("execution_archive_sha256_mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                violations.append(f"execution_archive_bad_member:{bad_member}")
            declared_members = {row["path"]: row["sha256"] for row in archive_info.get("members", [])}
            actual_members = sorted(archive.namelist())
            if sorted(declared_members) != actual_members:
                violations.append("execution_archive_member_set_mismatch")
            for name in actual_members:
                digest = __import__("hashlib").sha256(archive.read(name)).hexdigest()
                if declared_members.get(name) != digest:
                    violations.append(f"execution_archive_member_sha256_mismatch:{name}")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": EXPECTED_STAGE,
        "corrected_item_count": 21,
        "agreed_approved_count": rebuilt_agreement["agreed_approved_count"],
        "agreed_rejected_count": rebuilt_agreement["agreed_rejected_count"],
        "disagreement_count": rebuilt_agreement["disagreement_count"],
        "C03_required": rebuilt_agreement["blind_C03_required"],
        "C03_packet_created": False,
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "final_gold_freeze_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-dir", default="stage6_corrected_gold_review_execution")
    parser.add_argument("--setup-dir", default="stage6_corrected_gold_review_setup")
    args = parser.parse_args(argv)
    report = validate_stage6d_corrected_review_execution(Path(args.execution_dir), Path(args.setup_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
