#!/usr/bin/env python3
"""Validate Stage 6C review execution and blind R03 preparation artifacts."""

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

from scripts.data.create_stage6c_gold_review_setup import REVIEW_PACKET_COLUMNS, canonical_json  # noqa: E402
from scripts.data.execute_stage6c_gold_review import (  # noqa: E402
    ARCHIVE_NAME,
    CORRECTABLE_GOLD_ERROR_REQUIRED_ACTION,
    CORRECTABLE_GOLD_ERROR_REQUIRED_CONDITION,
    FINAL_REJECTION_LOCK_NAME,
    FINAL_REJECTION_FORBIDDEN_ACTIONS,
    R03_ARCHIVE_NAME,
    R04_ALLOWED_INPUTS,
    R04_ARCHIVE_NAME,
    R04_RESOLUTION_COLUMNS,
    R04_TASK,
    SOURCE_TASK_INVALID_REQUIRED_ACTION,
    SOURCE_TASK_INVALID_REQUIRED_CONDITION,
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


def validate_execution(execution_dir: Path, setup_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = execution_dir / "REVIEW_EXECUTION_MANIFEST.json"
    agreement_path = execution_dir / "R01_R02_AGREEMENT_REPORT.json"
    r01_path = execution_dir / "submissions" / "stage6c_gold_review_R01.submitted.tsv"
    r02_path = execution_dir / "submissions" / "stage6c_gold_review_R02.submitted.tsv"
    for path in [manifest_path, agreement_path, r01_path, r02_path]:
        if not path.is_file():
            violations.append(f"missing_file:{path.relative_to(execution_dir).as_posix()}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    manifest = read_json(manifest_path)
    agreement = read_json(agreement_path)
    review_items = read_jsonl(setup_dir / "artifacts" / "gold_review_items.jsonl")
    item_by_id = {row["stage6_sample_id"]: row for row in review_items}
    setup_r01_fields, setup_r01_rows = read_packet(setup_dir / "review_packets" / "stage6c_gold_review_R01.tsv")
    setup_r02_fields, setup_r02_rows = read_packet(setup_dir / "review_packets" / "stage6c_gold_review_R02.tsv")
    r01_fields, r01_rows = read_packet(r01_path)
    r02_fields, r02_rows = read_packet(r02_path)

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
    if manifest.get("submission_hashes", {}).get("R01_submission_sha256") != sha256_file(r01_path):
        violations.append("R01_submission_hash_mismatch")
    if manifest.get("submission_hashes", {}).get("R02_submission_sha256") != sha256_file(r02_path):
        violations.append("R02_submission_hash_mismatch")
    if manifest.get("agreement_report_sha256") != sha256_file(agreement_path):
        violations.append("agreement_report_hash_mismatch")
    if manifest.get("gold_review_items_sha256") != sha256_file(setup_dir / "artifacts" / "gold_review_items.jsonl"):
        violations.append("gold_review_items_hash_mismatch")

    template_by_role = {
        "R01": {row["stage6_sample_id"]: row for row in setup_r01_rows},
        "R02": {row["stage6_sample_id"]: row for row in setup_r02_rows},
    }
    for role, fields, rows in [("R01", r01_fields, r01_rows), ("R02", r02_fields, r02_rows)]:
        if fields != REVIEW_PACKET_COLUMNS:
            violations.append(f"{role}_unexpected_columns")
        if len(rows) != 500:
            violations.append(f"{role}_row_count_not_500")
        seen = set()
        for row in rows:
            sample_id = row.get("stage6_sample_id", "")
            seen.add(sample_id)
            template = template_by_role[role].get(sample_id)
            item = item_by_id.get(sample_id)
            if template is None or item is None:
                violations.append(f"{role}_unknown_sample_id:{sample_id}")
                continue
            for field in ["stage6_sample_id", "upstream_sample_locator", "authored_content_sha256", "reviewed_by"]:
                if row.get(field, "") != template.get(field, ""):
                    violations.append(f"{role}_immutable_field_changed:{sample_id}:{field}")
            if row.get("authored_content_sha256", "") != item.get("authored_content_sha256"):
                violations.append(f"{role}_content_hash_mismatch:{sample_id}")
            if row.get("decision") not in {"approved", "rejected"}:
                violations.append(f"{role}_invalid_decision:{sample_id}")
            if row.get("decision") == "rejected" and not row.get("notes", "").strip():
                violations.append(f"{role}_rejected_notes_blank:{sample_id}")
        if seen != set(item_by_id):
            violations.append(f"{role}_sample_id_set_mismatch")

    r01_by_id = {row["stage6_sample_id"]: row for row in r01_rows}
    r02_by_id = {row["stage6_sample_id"]: row for row in r02_rows}
    aa, rr, disagreements = [], [], []
    for sample_id in sorted(item_by_id):
        pair = (r01_by_id[sample_id]["decision"], r02_by_id[sample_id]["decision"])
        if pair == ("approved", "approved"):
            aa.append(sample_id)
        elif pair == ("rejected", "rejected"):
            rr.append(sample_id)
        else:
            disagreements.append(sample_id)
    if agreement.get("agreed_approved_count") != len(aa):
        violations.append("agreed_approved_count_mismatch")
    if agreement.get("agreed_rejected_count") != len(rr):
        violations.append("agreed_rejected_count_mismatch")
    if agreement.get("disagreement_count") != len(disagreements):
        violations.append("disagreement_count_mismatch")
    if manifest.get("agreed_approved_count") != len(aa):
        violations.append("manifest_agreed_approved_count_mismatch")
    if manifest.get("agreed_rejected_count") != len(rr):
        violations.append("manifest_agreed_rejected_count_mismatch")
    if manifest.get("disagreement_count") != len(disagreements):
        violations.append("manifest_disagreement_count_mismatch")

    expected_status = (
        "PASS_PENDING_R03_AND_FINAL_REJECTION_RESOLUTION"
        if disagreements and rr else
        "PASS_PENDING_BLIND_R03_ADJUDICATION"
        if disagreements else
        "PASS_FINAL_REJECTED_BLOCKS_CONFIRMATION"
        if rr else
        "PASS_ALL_APPROVED_READY_FOR_FINAL_GOLD_FREEZE"
    )
    if manifest.get("status") != expected_status:
        violations.append("status_not_expected_for_agreement_state")

    next_steps = manifest.get("next_steps")
    if not isinstance(next_steps, list):
        violations.append("next_steps_not_list")
        next_steps = []
    if disagreements and "send_blind_R03_packet_and_wait_for_adjudication" not in next_steps:
        violations.append("missing_R03_next_step")
    if rr and "resolve_agreed_rejected_items_under_FINAL_REJECTION_RESOLUTION_LOCK" not in next_steps:
        violations.append("missing_final_rejection_resolution_next_step")
    if not disagreements and not rr and "create_final_gold_freeze_package" not in next_steps:
        violations.append("missing_final_gold_freeze_next_step")

    if rr:
        lock_path = execution_dir / FINAL_REJECTION_LOCK_NAME
        if not lock_path.is_file():
            violations.append("missing_final_rejection_resolution_lock")
        else:
            lock = read_json(lock_path)
            lock_items = lock.get("agreed_rejected_items", [])
            if lock.get("status") != "LOCKED_PENDING_R04_TECHNICAL_RESOLUTION":
                violations.append("final_rejection_lock_status_not_locked")
            if lock.get("agreed_rejected_count") != len(rr):
                violations.append("final_rejection_lock_count_mismatch")
            if {row.get("stage6_sample_id") for row in lock_items} != set(rr):
                violations.append("final_rejection_lock_item_set_mismatch")
            if lock.get("classification_reviewer_role") != "R04":
                violations.append("final_rejection_lock_role_not_R04")
            if lock.get("R04_task") != R04_TASK:
                violations.append("final_rejection_lock_R04_task_changed")
            if lock.get("R04_allowed_inputs") != R04_ALLOWED_INPUTS:
                violations.append("final_rejection_lock_allowed_inputs_changed")
            for key in [
                "R04_must_be_distinct_from_R01",
                "R04_must_be_distinct_from_R02",
                "R04_must_be_distinct_from_R03",
            ]:
                if lock.get(key) is not True:
                    violations.append(f"final_rejection_lock_{key}_not_locked")
            if lock.get("R04_must_not_see_model_predictions") is not True:
                violations.append("final_rejection_lock_R04_model_blindness_not_locked")
            if lock.get("R04_may_see_R01_R02_rejection_reasons") is not True:
                violations.append("final_rejection_lock_R04_reason_access_not_locked")
            allowed_classes = lock.get("allowed_classes")
            if allowed_classes != ["CORRECTABLE_GOLD_ERROR", "SOURCE_TASK_INVALID"]:
                violations.append("final_rejection_lock_allowed_classes_changed")
            class_rules = lock.get("class_rules", {})
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
            if class_rules != expected_class_rules:
                violations.append("final_rejection_lock_class_rules_changed")
            if lock.get("forbidden_actions") != FINAL_REJECTION_FORBIDDEN_ACTIONS:
                violations.append("final_rejection_lock_forbidden_actions_changed")
            if lock.get("R03_rejected_items_join_same_resolution_workflow") is not True:
                violations.append("final_rejection_lock_R03_rejections_do_not_join_workflow")
            if manifest.get("final_rejection_resolution_lock_created") is not True:
                violations.append("manifest_final_rejection_lock_not_created")
            if manifest.get("final_rejection_resolution_lock_sha256") != sha256_file(lock_path):
                violations.append("manifest_final_rejection_lock_hash_mismatch")

            r04_manifest_path = execution_dir / "r04_resolution_packet" / "R04_PACKET_MANIFEST.json"
            r04_items_path = execution_dir / "r04_resolution_packet" / "r04_resolution_items.jsonl"
            r04_tsv_path = execution_dir / "r04_resolution_packet" / "stage6c_final_rejection_R04.tsv"
            r04_archive_path = execution_dir / R04_ARCHIVE_NAME
            r04_table_metadata_path = execution_dir / "r04_resolution_packet" / "official_table_metadata.jsonl"
            for path in [
                r04_manifest_path,
                r04_items_path,
                r04_tsv_path,
                r04_archive_path,
                r04_table_metadata_path,
            ]:
                if not path.is_file():
                    violations.append(f"missing_R04_artifact:{path.name}")
            if r04_manifest_path.is_file():
                r04_manifest = read_json(r04_manifest_path)
                if r04_manifest.get("packet_role") != "R04":
                    violations.append("R04_manifest_role_not_R04")
                if r04_manifest.get("agreed_rejected_count") != len(rr):
                    violations.append("R04_manifest_count_mismatch")
                for key in [
                    "contains_only_agreed_rejected_items",
                    "R04_must_be_distinct_from_R01",
                    "R04_must_be_distinct_from_R02",
                    "R04_must_be_distinct_from_R03",
                    "R04_must_not_see_model_predictions",
                    "R04_may_see_R01_R02_rejection_reasons",
                    "classification_required_on_submission",
                    "rationale_required_on_submission",
                    "correction_spec_required_when_correctable",
                ]:
                    if r04_manifest.get(key) is not True:
                        violations.append(f"R04_manifest_{key}_not_locked")
                for key in ["contains_R03_disagreement_items", "contains_unrelated_approved_items"]:
                    if r04_manifest.get(key) is not False:
                        violations.append(f"R04_manifest_{key}_not_false")
                if r04_manifest.get("allowed_classes") != ["CORRECTABLE_GOLD_ERROR", "SOURCE_TASK_INVALID"]:
                    violations.append("R04_manifest_allowed_classes_changed")
                if r04_manifest.get("final_rejection_resolution_lock_sha256") != sha256_file(lock_path):
                    violations.append("R04_manifest_lock_hash_mismatch")
                if r04_items_path.is_file() and r04_manifest.get("r04_items_sha256") != sha256_file(r04_items_path):
                    violations.append("R04_manifest_items_hash_mismatch")
                if r04_tsv_path.is_file() and r04_manifest.get("r04_tsv_sha256") != sha256_file(r04_tsv_path):
                    violations.append("R04_manifest_tsv_hash_mismatch")
            if r04_items_path.is_file():
                r04_items = read_jsonl(r04_items_path)
                if {row["stage6_sample_id"] for row in r04_items} != set(rr):
                    violations.append("R04_item_set_not_exact_agreed_rejections")
                for item in r04_items:
                    scope = item.get("R04_resolution_scope", {})
                    sample_id = item.get("stage6_sample_id", "")
                    if scope.get("R01_decision") != "rejected" or scope.get("R02_decision") != "rejected":
                        violations.append(f"R04_item_missing_rejected_decisions:{sample_id}")
                    if scope.get("R01_notes_sha256") != sha256_text(scope.get("R01_notes", "")):
                        violations.append(f"R04_item_R01_notes_hash_mismatch:{sample_id}")
                    if scope.get("R02_notes_sha256") != sha256_text(scope.get("R02_notes", "")):
                        violations.append(f"R04_item_R02_notes_hash_mismatch:{sample_id}")
                    if sample_id not in scope.get("same_table_agreed_rejected_sample_ids", []):
                        violations.append(f"R04_item_missing_table_context:{sample_id}")
            if r04_tsv_path.is_file():
                fields, r04_rows = read_packet(r04_tsv_path)
                if fields != R04_RESOLUTION_COLUMNS:
                    violations.append("R04_unexpected_columns")
                if len(r04_rows) != len(rr):
                    violations.append("R04_row_count_mismatch")
                r04_item_by_id = (
                    {row["stage6_sample_id"]: row for row in read_jsonl(r04_items_path)}
                    if r04_items_path.is_file() else {}
                )
                for row in r04_rows:
                    sample_id = row.get("stage6_sample_id", "")
                    item = r04_item_by_id.get(sample_id, {})
                    scope = item.get("R04_resolution_scope", {})
                    if row.get("reviewed_by") != "R04":
                        violations.append(f"R04_reviewed_by_mismatch:{sample_id}")
                    if row.get("classification") or row.get("rationale") or row.get("correction_spec"):
                        violations.append(f"R04_prefilled_resolution_fields:{sample_id}")
                    if item:
                        if row.get("upstream_sample_locator") != item.get("upstream_sample_locator"):
                            violations.append(f"R04_locator_mismatch:{sample_id}")
                        if row.get("authored_content_sha256") != item.get("authored_content_sha256"):
                            violations.append(f"R04_authored_hash_mismatch:{sample_id}")
                        if row.get("R01_notes_sha256") != scope.get("R01_notes_sha256"):
                            violations.append(f"R04_R01_notes_hash_mismatch:{sample_id}")
                        if row.get("R02_notes_sha256") != scope.get("R02_notes_sha256"):
                            violations.append(f"R04_R02_notes_hash_mismatch:{sample_id}")
            if r04_archive_path.is_file():
                try:
                    with zipfile.ZipFile(r04_archive_path) as archive:
                        bad_member = archive.testzip()
                        names = set(archive.namelist())
                    if bad_member:
                        violations.append(f"R04_archive_bad_member:{bad_member}")
                    expected_names = {
                        FINAL_REJECTION_LOCK_NAME,
                        "r04_resolution_packet/R04_PACKET_MANIFEST.json",
                        "r04_resolution_packet/r04_resolution_items.jsonl",
                        "r04_resolution_packet/official_table_metadata.jsonl",
                        "r04_resolution_packet/stage6c_final_rejection_R04.tsv",
                    }
                    if names != expected_names:
                        violations.append("R04_archive_members_changed")
                except zipfile.BadZipFile:
                    violations.append("R04_archive_not_openable")
            if manifest.get("r04_resolution_packet_created") is not True:
                violations.append("manifest_R04_packet_not_created")
            if r04_manifest_path.is_file() and manifest.get("r04_resolution_packet_manifest_sha256") != sha256_file(r04_manifest_path):
                violations.append("manifest_R04_packet_hash_mismatch")
    elif manifest.get("final_rejection_resolution_lock_created"):
        violations.append("unexpected_final_rejection_lock_created")

    if disagreements:
        r03_manifest_path = execution_dir / "r03_blind_packet" / "R03_BLIND_PACKET_MANIFEST.json"
        r03_items_path = execution_dir / "r03_blind_packet" / "r03_blind_review_items.jsonl"
        r03_tsv_path = execution_dir / "r03_blind_packet" / "stage6c_gold_review_R03.tsv"
        r03_archive_path = execution_dir / R03_ARCHIVE_NAME
        for path in [r03_manifest_path, r03_items_path, r03_tsv_path, r03_archive_path]:
            if not path.is_file():
                violations.append(f"missing_R03_artifact:{path.name}")
        if r03_manifest_path.is_file():
            r03_manifest = read_json(r03_manifest_path)
            if r03_manifest.get("contains_R01_R02_votes_or_notes") is not False:
                violations.append("R03_manifest_allows_votes_or_notes")
            for key in [
                "R03_must_not_see_R01_decision",
                "R03_must_not_see_R02_decision",
                "R03_must_not_see_R01_notes",
                "R03_must_not_see_R02_notes",
                "R03_must_not_see_model_predictions",
            ]:
                if r03_manifest.get(key) is not True:
                    violations.append(f"{key}_not_locked")
        if r03_items_path.is_file():
            r03_items = read_jsonl(r03_items_path)
            if {row["stage6_sample_id"] for row in r03_items} != set(disagreements):
                violations.append("R03_item_set_not_exact_disagreements")
            forbidden_keys = {"R01_decision", "R02_decision", "R01_notes", "R02_notes"}
            for item in r03_items:
                if forbidden_keys & set(item):
                    violations.append(f"R03_item_contains_vote_or_note:{item['stage6_sample_id']}")
        if r03_tsv_path.is_file():
            fields, r03_rows = read_packet(r03_tsv_path)
            if fields != REVIEW_PACKET_COLUMNS:
                violations.append("R03_unexpected_columns")
            for row in r03_rows:
                if row.get("reviewed_by") != "R03":
                    violations.append(f"R03_reviewed_by_mismatch:{row.get('stage6_sample_id')}")
                if row.get("decision") or row.get("notes"):
                    violations.append(f"R03_prefilled_decision_or_notes:{row.get('stage6_sample_id')}")
        if r03_archive_path.is_file():
            try:
                with zipfile.ZipFile(r03_archive_path) as archive:
                    bad_member = archive.testzip()
                    names = set(archive.namelist())
                if bad_member:
                    violations.append(f"R03_archive_bad_member:{bad_member}")
                for forbidden in ["R01", "R02", "submitted"]:
                    if any(forbidden in name and "R03" not in name for name in names):
                        violations.append(f"R03_archive_contains_forbidden_name:{forbidden}")
            except zipfile.BadZipFile:
                violations.append("R03_archive_not_openable")
    archive_info = manifest.get("execution_archive") or {}
    archive_path = execution_dir / archive_info.get("path", ARCHIVE_NAME)
    if not archive_path.is_file():
        violations.append("execution_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("execution_archive_hash_mismatch")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip():
                    violations.append("execution_archive_testzip_failed")
        except zipfile.BadZipFile:
            violations.append("execution_archive_not_openable")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6C_REVIEW_EXECUTION_AND_ADJUDICATION_PREP",
        "agreed_approved_count": len(aa),
        "agreed_rejected_count": len(rr),
        "disagreement_count": len(disagreements),
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "final_gold_freeze_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-dir", default="stage6_gold_review_execution")
    parser.add_argument("--setup-dir", default="stage6_gold_review_setup")
    args = parser.parse_args(argv)
    report = validate_execution(Path(args.execution_dir), Path(args.setup_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
