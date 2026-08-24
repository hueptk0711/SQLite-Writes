#!/usr/bin/env python3
"""Validate Stage 6E final registration revision artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.create_stage6e_final_registration import (
    ARCHIVE_NAME,
    CORRECTABLE_GOLD_ERROR_QUEUE_SHA256,
    SOURCE_TASK_INVALID_QUEUE_SHA256,
    STAGE6B_DIR,
    STAGE6C_SETUP_DIR,
    STAGE6C_EXEC_DIR,
    STAGE6C_R03_DIR,
    STAGE6C_R04_DIR,
    STAGE6D_EXEC_DIR,
    STAGE6D_EXECUTION_PATCH1_COMMIT,
    STAGE6D_SETUP_DIR,
    STAGE6E_DIR,
    artifact_root_hashes,
    build_stage6e_artifacts,
    load_inputs,
    read_json,
    read_jsonl,
    sha256_file,
)


EXPECTED_STATUS = "FINAL_CONFIRMATION_REGISTRATION_LOCKED"


def compare_jsonl(path: Path, expected: list[dict[str, Any]], violations: list[str], label: str) -> None:
    if not path.is_file():
        violations.append(f"missing_{label}")
        return
    if read_jsonl(path) != expected:
        violations.append(f"{label}_mismatch")


def compare_json(path: Path, expected: dict[str, Any], violations: list[str], label: str) -> None:
    if not path.is_file():
        violations.append(f"missing_{label}")
        return
    if read_json(path) != expected:
        violations.append(f"{label}_mismatch")


def validate_stage6e_final_registration(
    stage6e_dir: Path = STAGE6E_DIR,
    stage6b_dir: Path = STAGE6B_DIR,
    stage6c_setup_dir: Path = STAGE6C_SETUP_DIR,
    stage6c_exec_dir: Path = STAGE6C_EXEC_DIR,
    stage6c_r03_dir: Path = STAGE6C_R03_DIR,
    stage6c_r04_dir: Path = STAGE6C_R04_DIR,
    stage6d_setup_dir: Path = STAGE6D_SETUP_DIR,
    stage6d_exec_dir: Path = STAGE6D_EXEC_DIR,
) -> dict[str, Any]:
    violations: list[str] = []
    artifacts_dir = stage6e_dir / "artifacts"
    lock_path = stage6e_dir / "STAGE6E_FINAL_REGISTRATION_LOCK.json"
    required = [
        lock_path,
        stage6e_dir / "REVIEWER_README.md",
        stage6e_dir / "VALIDATION_REPORT.md",
        artifacts_dir / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl",
        artifacts_dir / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
        artifacts_dir / "FINAL_GOLD_WRITE_PLANS.jsonl",
        artifacts_dir / "FINAL_GOLD_PROGRAMS.jsonl",
        artifacts_dir / "FINAL_GOLD_POST_STATE_HASHES.jsonl",
        artifacts_dir / "FINAL_GOLD_CORPUS.jsonl",
        artifacts_dir / "FINAL_REVIEWED_GOLD_PROVENANCE.jsonl",
        artifacts_dir / "FINAL_GOLD_REPLAY_REPORT.json",
        artifacts_dir / "FINAL_DISTRIBUTION_REPORT.json",
        artifacts_dir / "FINAL_OVERLAP_AUDIT.json",
        artifacts_dir / "MCNEMAR_THRESHOLD_SENSITIVITY_N481.json",
    ]
    for path in required:
        if not path.is_file():
            violations.append(f"missing_required_file:{path.as_posix()}")
    if violations:
        return {"status": "FAIL", "violations": violations, "stage": "Stage6E_FINAL_REGISTRATION_REVISION"}

    inputs = load_inputs(
        stage6b_dir,
        stage6c_setup_dir,
        stage6c_exec_dir,
        stage6c_r03_dir,
        stage6c_r04_dir,
        stage6d_setup_dir,
        stage6d_exec_dir,
    )
    expected = build_stage6e_artifacts(inputs, stage6b_dir)
    compare_jsonl(artifacts_dir / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl", expected["exclusions"], violations, "source_task_invalid_exclusions")
    compare_jsonl(artifacts_dir / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl", expected["final_samples"], violations, "final_confirmation_sample_manifest")
    compare_jsonl(artifacts_dir / "FINAL_GOLD_WRITE_PLANS.jsonl", expected["final_plans"], violations, "final_gold_write_plans")
    compare_jsonl(artifacts_dir / "FINAL_GOLD_PROGRAMS.jsonl", expected["final_programs"], violations, "final_gold_programs")
    compare_jsonl(artifacts_dir / "FINAL_GOLD_POST_STATE_HASHES.jsonl", expected["final_posts"], violations, "final_gold_post_state_hashes")
    compare_jsonl(artifacts_dir / "FINAL_GOLD_CORPUS.jsonl", expected["final_corpus"], violations, "final_gold_corpus")
    compare_jsonl(
        artifacts_dir / "FINAL_REVIEWED_GOLD_PROVENANCE.jsonl",
        expected["final_review_provenance"],
        violations,
        "final_reviewed_gold_provenance",
    )
    compare_json(artifacts_dir / "FINAL_GOLD_REPLAY_REPORT.json", expected["replay_report"], violations, "final_gold_replay_report")
    compare_json(artifacts_dir / "FINAL_DISTRIBUTION_REPORT.json", expected["distribution"], violations, "final_distribution_report")
    compare_json(artifacts_dir / "FINAL_OVERLAP_AUDIT.json", expected["overlap"], violations, "final_overlap_audit")
    compare_json(artifacts_dir / "MCNEMAR_THRESHOLD_SENSITIVITY_N481.json", expected["mcnemar"], violations, "mcnemar_threshold_sensitivity")

    lock = read_json(lock_path)
    expected_lock = expected["lock"]
    root_hashes = artifact_root_hashes(
        stage6b_dir,
        stage6c_setup_dir,
        stage6c_exec_dir,
        stage6c_r03_dir,
        stage6c_r04_dir,
        stage6d_setup_dir,
        stage6d_exec_dir,
    )
    expected_lock.update(
        {
            "final_confirmation_sample_manifest_sha256": sha256_file(artifacts_dir / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"),
            "final_gold_write_plans_sha256": sha256_file(artifacts_dir / "FINAL_GOLD_WRITE_PLANS.jsonl"),
            "final_gold_programs_sha256": sha256_file(artifacts_dir / "FINAL_GOLD_PROGRAMS.jsonl"),
            "final_gold_post_state_hashes_sha256": sha256_file(artifacts_dir / "FINAL_GOLD_POST_STATE_HASHES.jsonl"),
            "final_gold_corpus_sha256": sha256_file(artifacts_dir / "FINAL_GOLD_CORPUS.jsonl"),
            "final_reviewed_gold_provenance_sha256": sha256_file(
                artifacts_dir / "FINAL_REVIEWED_GOLD_PROVENANCE.jsonl"
            ),
            "source_task_invalid_exclusions_sha256": sha256_file(artifacts_dir / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl"),
            "final_gold_replay_report_sha256": sha256_file(artifacts_dir / "FINAL_GOLD_REPLAY_REPORT.json"),
            "final_distribution_report_sha256": sha256_file(artifacts_dir / "FINAL_DISTRIBUTION_REPORT.json"),
            "final_overlap_audit_sha256": sha256_file(artifacts_dir / "FINAL_OVERLAP_AUDIT.json"),
            "mcnemar_threshold_sensitivity_sha256": sha256_file(artifacts_dir / "MCNEMAR_THRESHOLD_SENSITIVITY_N481.json"),
            "accepted_upstream_artifact_roots": root_hashes,
            "reviewed_gold_provenance_anchored": True,
        }
    )
    archive_info = lock.get("archive", {})
    expected_lock["archive"] = archive_info
    if lock != expected_lock:
        violations.append("stage6e_lock_mismatch")
    exact_scalars = {
        "status": EXPECTED_STATUS,
        "stage6d_execution_patch1_commit": STAGE6D_EXECUTION_PATCH1_COMMIT,
        "original_registered_n": 500,
        "source_task_invalid_n": 19,
        "replacement_samples": 0,
        "replacement_policy": "NONE",
        "final_confirmation_n": 481,
        "final_gold_freeze_created": True,
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "source_task_invalid_queue_sha256": SOURCE_TASK_INVALID_QUEUE_SHA256,
        "correctable_gold_error_queue_sha256": CORRECTABLE_GOLD_ERROR_QUEUE_SHA256,
        "reviewed_gold_provenance_anchored": True,
    }
    for field, value in exact_scalars.items():
        if lock.get(field) != value:
            violations.append(f"lock_{field}_mismatch")
    if lock.get("gold_source_type_counts") != {"ORIGINAL_REVIEW_ACCEPTED": 460, "CORRECTED_REVIEW_ACCEPTED": 21}:
        violations.append("lock_gold_source_type_counts_mismatch")

    archive_path = stage6e_dir / ARCHIVE_NAME
    if archive_info.get("path") != ARCHIVE_NAME:
        violations.append("archive_path_mismatch")
    if not archive_path.is_file():
        violations.append("stage6e_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("stage6e_archive_sha256_mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                violations.append(f"stage6e_archive_bad_member:{bad_member}")
            declared = {row["path"]: row["sha256"] for row in archive_info.get("members", [])}
            actual = sorted(archive.namelist())
            if sorted(declared) != actual:
                violations.append("stage6e_archive_member_set_mismatch")
            for name in actual:
                digest = hashlib.sha256(archive.read(name)).hexdigest()
                if declared.get(name) != digest:
                    violations.append(f"stage6e_archive_member_sha256_mismatch:{name}")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6E_FINAL_REGISTRATION_REVISION",
        "original_registered_n": 500,
        "source_task_invalid_n": 19,
        "replacement_samples": 0,
        "final_confirmation_n": 481,
        "original_review_accepted_count": 460,
        "corrected_review_accepted_count": 21,
        "final_gold_replay_pass_count": 481,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": True,
        "model_called": False,
        "gpu_called": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage6e-dir", default="stage6_final_registration_revision")
    args = parser.parse_args(argv)
    report = validate_stage6e_final_registration(stage6e_dir=Path(args.stage6e_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
