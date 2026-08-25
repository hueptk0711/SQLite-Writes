#!/usr/bin/env python3
"""Validate Stage 6C R03 adjudication ingest artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.create_stage6c_gold_review_setup import REVIEW_PACKET_COLUMNS
from scripts.data.execute_stage6c_gold_review import (
    CORRECTABLE_GOLD_ERROR_REQUIRED_ACTION,
    CORRECTABLE_GOLD_ERROR_REQUIRED_CONDITION,
    FINAL_REJECTION_FORBIDDEN_ACTIONS,
    R04_TASK,
    SOURCE_TASK_INVALID_REQUIRED_ACTION,
    SOURCE_TASK_INVALID_REQUIRED_CONDITION,
)
from scripts.data.ingest_stage6c_r03_adjudication import (
    ARCHIVE_NAME,
    FINAL_REJECTION_QUEUE_NAME,
    R04_AFTER_R03_ARCHIVE_NAME,
    R04_AFTER_R03_COLUMNS,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_packet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def validate_r03_adjudication(adjudication_dir: Path, execution_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = adjudication_dir / "R03_INGEST_MANIFEST.json"
    report_path = adjudication_dir / "R03_ADJUDICATION_REPORT.json"
    queue_path = adjudication_dir / FINAL_REJECTION_QUEUE_NAME
    r03_path = adjudication_dir / "submissions" / "stage6c_gold_review_R03.submitted.tsv"
    template_path = execution_dir / "r03_blind_packet" / "stage6c_gold_review_R03.tsv"
    for path in [manifest_path, report_path, queue_path, r03_path, template_path]:
        if not path.is_file():
            violations.append(f"missing_file:{path.name}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    manifest = read_json(manifest_path)
    report = read_json(report_path)
    queue = read_json(queue_path)
    template_fields, template_rows = read_packet(template_path)
    r03_fields, r03_rows = read_packet(r03_path)
    template_by_id = {row["stage6_sample_id"]: row for row in template_rows}

    for label, value in {
        "model_called": manifest.get("model_called"),
        "gpu_called": manifest.get("gpu_called"),
        "confirmation_run_allowed_now": manifest.get("confirmation_run_allowed_now"),
        "final_gold_freeze_created": manifest.get("final_gold_freeze_created"),
    }.items():
        if value is not False:
            violations.append(f"{label}_not_false")
    if manifest.get("validation_violations"):
        violations.append("manifest_contains_validation_violations")
    if manifest.get("status") != "PASS_R03_COMPLETE_PENDING_FINAL_REJECTION_RESOLUTION":
        violations.append("manifest_status_not_pending_final_rejection_resolution")
    if template_fields != REVIEW_PACKET_COLUMNS or r03_fields != REVIEW_PACKET_COLUMNS:
        violations.append("R03_columns_changed")
    if len(r03_rows) != 52:
        violations.append("R03_row_count_not_52")
    seen_ids: set[str] = set()
    approved: list[str] = []
    rejected: list[dict[str, str]] = []
    for row in r03_rows:
        sample_id = row.get("stage6_sample_id", "")
        seen_ids.add(sample_id)
        template = template_by_id.get(sample_id)
        if template is None:
            violations.append(f"R03_unknown_sample_id:{sample_id}")
            continue
        for field in ["stage6_sample_id", "upstream_sample_locator", "authored_content_sha256", "reviewed_by"]:
            if row.get(field, "") != template.get(field, ""):
                violations.append(f"R03_immutable_field_changed:{sample_id}:{field}")
        if row.get("decision") == "approved":
            approved.append(sample_id)
        elif row.get("decision") == "rejected":
            if not row.get("notes", "").strip():
                violations.append(f"R03_rejected_notes_blank:{sample_id}")
            rejected.append({"stage6_sample_id": sample_id, "R03_notes_sha256": sha256_text(row.get("notes", ""))})
        else:
            violations.append(f"R03_invalid_or_blank_decision:{sample_id}")
    if seen_ids != set(template_by_id):
        violations.append("R03_sample_id_set_mismatch")
    approved = sorted(approved)
    rejected = sorted(rejected, key=lambda item: item["stage6_sample_id"])
    if report.get("R03_approved_count") != len(approved):
        violations.append("R03_report_approved_count_mismatch")
    if report.get("R03_rejected_count") != len(rejected):
        violations.append("R03_report_rejected_count_mismatch")
    if report.get("R03_approved_ids") != approved:
        violations.append("R03_report_approved_ids_mismatch")
    if report.get("R03_rejected_items") != rejected:
        violations.append("R03_report_rejected_items_mismatch")
    if manifest.get("r03_submission_sha256") != sha256_file(r03_path):
        violations.append("manifest_R03_submission_hash_mismatch")

    final_rejected_items = queue.get("final_rejected_items", [])
    final_rejected_ids = {row.get("stage6_sample_id") for row in final_rejected_items}
    r03_rejected_ids = {row["stage6_sample_id"] for row in rejected}
    for item in final_rejected_items:
        sample_id = item.get("stage6_sample_id", "")
        for role in ["R01", "R02", "R03"]:
            decision = item.get(f"{role}_decision")
            note = item.get(f"{role}_notes", "")
            declared_hash = item.get(f"{role}_notes_sha256", "")
            if decision == "rejected":
                if not note.strip():
                    violations.append(f"queue_missing_{role}_rejection_note:{sample_id}")
                if sha256_text(note) != declared_hash:
                    violations.append(f"queue_{role}_rejection_note_hash_mismatch:{sample_id}")
            elif decision in {"approved", "not_applicable"}:
                if note and sha256_text(note) != declared_hash:
                    violations.append(f"queue_{role}_note_hash_mismatch:{sample_id}")
    if queue.get("status") != "LOCKED_PENDING_R04_TECHNICAL_RESOLUTION":
        violations.append("queue_status_not_locked")
    if queue.get("R04_task") != R04_TASK:
        violations.append("queue_R04_task_changed")
    if queue.get("R04_may_see_R03_rejection_reasons") is not True:
        violations.append("queue_R04_R03_reason_access_not_locked")
    for key in [
        "R04_must_be_distinct_from_R01",
        "R04_must_be_distinct_from_R02",
        "R04_must_be_distinct_from_R03",
        "R04_must_not_see_model_predictions",
    ]:
        if queue.get(key) is not True:
            violations.append(f"queue_{key}_not_locked")
    expected_class_rules = {
        "CORRECTABLE_GOLD_ERROR": {
            "required_condition": CORRECTABLE_GOLD_ERROR_REQUIRED_CONDITION,
            "required_action": CORRECTABLE_GOLD_ERROR_REQUIRED_ACTION,
        },
        "SOURCE_TASK_INVALID": {
            "required_condition": SOURCE_TASK_INVALID_REQUIRED_CONDITION,
            "required_action": SOURCE_TASK_INVALID_REQUIRED_ACTION,
        },
    }
    if queue.get("class_rules") != expected_class_rules:
        violations.append("queue_class_rules_changed")
    if queue.get("forbidden_actions") != FINAL_REJECTION_FORBIDDEN_ACTIONS:
        violations.append("queue_forbidden_actions_changed")
    if queue.get("initial_R01_R02_agreed_rejected_count") != 17:
        violations.append("queue_initial_reject_count_changed")
    if queue.get("R03_rejected_disagreement_count") != len(rejected):
        violations.append("queue_R03_reject_count_mismatch")
    if queue.get("R03_approved_disagreement_count") != len(approved):
        violations.append("queue_R03_approved_count_mismatch")
    if queue.get("final_rejected_count") != 17 + len(rejected):
        violations.append("queue_final_rejected_count_mismatch")
    if not r03_rejected_ids.issubset(final_rejected_ids):
        violations.append("queue_missing_R03_rejected_items")
    if manifest.get("final_rejected_count_after_R03") != queue.get("final_rejected_count"):
        violations.append("manifest_final_rejected_count_mismatch")
    if manifest.get("final_approved_count_after_R03") != 431 + len(approved):
        violations.append("manifest_final_approved_count_mismatch")
    if manifest.get("final_rejection_resolution_queue_sha256") != sha256_file(queue_path):
        violations.append("manifest_queue_hash_mismatch")

    r04_manifest_path = adjudication_dir / "r04_after_r03_resolution_packet" / "R04_AFTER_R03_PACKET_MANIFEST.json"
    r04_items_path = adjudication_dir / "r04_after_r03_resolution_packet" / "r04_after_R03_resolution_items.jsonl"
    r04_tsv_path = adjudication_dir / "r04_after_r03_resolution_packet" / "stage6c_final_rejection_after_R03_R04.tsv"
    r04_archive_path = adjudication_dir / R04_AFTER_R03_ARCHIVE_NAME
    for path in [r04_manifest_path, r04_items_path, r04_tsv_path, r04_archive_path]:
        if not path.is_file():
            violations.append(f"missing_R04_after_R03_artifact:{path.name}")
    if r04_manifest_path.is_file():
        r04_manifest = read_json(r04_manifest_path)
        if r04_manifest.get("final_rejected_count") != queue.get("final_rejected_count"):
            violations.append("R04_after_R03_manifest_count_mismatch")
        for key in [
            "contains_only_final_rejected_items",
            "R04_must_be_distinct_from_R01",
            "R04_must_be_distinct_from_R02",
            "R04_must_be_distinct_from_R03",
            "R04_must_not_see_model_predictions",
            "R04_may_see_R01_R02_rejection_reasons",
            "R04_may_see_R03_rejection_reasons",
            "classification_required_on_submission",
            "rationale_required_on_submission",
            "correction_spec_required_when_correctable",
        ]:
            if r04_manifest.get(key) is not True:
                violations.append(f"R04_after_R03_manifest_{key}_not_locked")
        if r04_manifest.get("contains_unrelated_approved_items") is not False:
            violations.append("R04_after_R03_manifest_contains_approved_items")
        if r04_manifest.get("resolution_queue_sha256") != sha256_file(queue_path):
            violations.append("R04_after_R03_manifest_queue_hash_mismatch")
        if r04_items_path.is_file() and r04_manifest.get("r04_items_sha256") != sha256_file(r04_items_path):
            violations.append("R04_after_R03_manifest_items_hash_mismatch")
        if r04_tsv_path.is_file() and r04_manifest.get("r04_tsv_sha256") != sha256_file(r04_tsv_path):
            violations.append("R04_after_R03_manifest_tsv_hash_mismatch")
    if r04_items_path.is_file():
        r04_items = read_jsonl(r04_items_path)
        if {row["stage6_sample_id"] for row in r04_items} != final_rejected_ids:
            violations.append("R04_after_R03_item_set_not_final_rejections")
        for item in r04_items:
            scope = item.get("R04_resolution_scope", {})
            sample_id = item.get("stage6_sample_id", "")
            if scope.get("final_rejection_source") == "R03_REJECTED_DISAGREEMENT":
                if scope.get("R03_decision") != "rejected" or not scope.get("R03_notes"):
                    violations.append(f"R04_after_R03_missing_R03_rejection_reason:{sample_id}")
            for role in ["R01", "R02", "R03"]:
                decision = scope.get(f"{role}_decision")
                note = scope.get(f"{role}_notes", "")
                declared_hash = scope.get(f"{role}_notes_sha256", "")
                if decision == "rejected":
                    if not note.strip():
                        violations.append(f"R04_after_R03_missing_{role}_rejection_reason:{sample_id}")
                    if sha256_text(note) != declared_hash:
                        violations.append(f"R04_after_R03_{role}_rejection_note_hash_mismatch:{sample_id}")
            if sample_id not in scope.get("same_table_final_rejected_sample_ids", []):
                violations.append(f"R04_after_R03_missing_table_context:{sample_id}")
    if r04_tsv_path.is_file():
        fields, r04_rows = read_packet(r04_tsv_path)
        if fields != R04_AFTER_R03_COLUMNS:
            violations.append("R04_after_R03_unexpected_columns")
        if len(r04_rows) != queue.get("final_rejected_count"):
            violations.append("R04_after_R03_row_count_mismatch")
        for row in r04_rows:
            if row.get("reviewed_by") != "R04":
                violations.append(f"R04_after_R03_reviewed_by_mismatch:{row.get('stage6_sample_id')}")
            if row.get("classification") or row.get("rationale") or row.get("correction_spec"):
                violations.append(f"R04_after_R03_prefilled_resolution_fields:{row.get('stage6_sample_id')}")
    if r04_archive_path.is_file():
        try:
            with zipfile.ZipFile(r04_archive_path) as archive:
                bad_member = archive.testzip()
                names = set(archive.namelist())
            if bad_member:
                violations.append(f"R04_after_R03_archive_bad_member:{bad_member}")
            expected_names = {
                FINAL_REJECTION_QUEUE_NAME,
                "r04_after_r03_resolution_packet/R04_AFTER_R03_PACKET_MANIFEST.json",
                "r04_after_r03_resolution_packet/r04_after_R03_resolution_items.jsonl",
                "r04_after_r03_resolution_packet/official_table_metadata.jsonl",
                "r04_after_r03_resolution_packet/stage6c_final_rejection_after_R03_R04.tsv",
            }
            if names != expected_names:
                violations.append("R04_after_R03_archive_members_changed")
        except zipfile.BadZipFile:
            violations.append("R04_after_R03_archive_not_openable")

    archive_info = manifest.get("archive") or {}
    archive_path = adjudication_dir / archive_info.get("path", ARCHIVE_NAME)
    if not archive_path.is_file():
        violations.append("adjudication_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("adjudication_archive_hash_mismatch")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip():
                    violations.append("adjudication_archive_testzip_failed")
        except zipfile.BadZipFile:
            violations.append("adjudication_archive_not_openable")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6C_R03_ADJUDICATION_INGEST",
        "R03_approved_count": len(approved),
        "R03_rejected_count": len(rejected),
        "final_approved_count_after_R03": 431 + len(approved),
        "final_rejected_count_after_R03": 17 + len(rejected),
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "final_gold_freeze_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adjudication-dir", default="stage6_gold_review_r03_adjudication")
    parser.add_argument("--execution-dir", default="stage6_gold_review_execution")
    args = parser.parse_args(argv)
    report = validate_r03_adjudication(Path(args.adjudication_dir), Path(args.execution_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
