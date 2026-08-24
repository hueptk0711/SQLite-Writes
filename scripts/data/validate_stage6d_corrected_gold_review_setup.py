#!/usr/bin/env python3
"""Validate Stage 6D corrected-gold re-review setup artifacts."""

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

from scripts.data.create_stage6c_gold_review_setup import canonical_json
from scripts.data.create_stage6d_corrected_gold_review_setup import (
    ACCEPTED_CORRECTABLE_QUEUE_SHA256,
    ACCEPTED_SOURCE_INVALID_QUEUE_SHA256,
    ARCHIVE_NAME,
    C01_ARCHIVE_NAME,
    C02_ARCHIVE_NAME,
    REVIEW_PACKET_COLUMNS,
    apply_operations_to_plan,
    execute_corrected_insert,
    normalize_correction_operations,
    sha256_text,
)

STAGE6C_R04_COMMIT = "7f0950184ee40afb53581bd7b2127862fd581cde"

EXPECTED_PROTOCOL = {
    "stage": "Stage6D_CORRECTED_GOLD_RE_REVIEW_SETUP",
    "status": "LOCKED_PENDING_CORRECTED_ITEM_HUMAN_RE_REVIEW",
    "model_called": False,
    "gpu_called": False,
    "confirmation_run_allowed_now": False,
    "final_gold_freeze_created": False,
    "scope": "21_R04_CORRECTABLE_GOLD_ERROR_ITEMS_ONLY",
    "forbidden_actions": [
        "do_not_modify_the_19_SOURCE_TASK_INVALID_items_in_this_patch",
        "do_not_drop_or_replace_any_registered_sample",
        "do_not_create_final_gold_freeze_from_corrected_items_before_re_review_acceptance",
        "do_not_permit_gpu_preflight_before_all_corrected_items_and_registration_revision_are_resolved",
    ],
    "reviewer_roles": ["C01", "C02"],
    "reviewer_roles_must_be_distinct": True,
    "C01_must_be_distinct_from_R04": True,
    "C02_must_be_distinct_from_R04": True,
    "reviewers_must_not_see_model_predictions": True,
    "allowed_decisions_after_execution": ["approved", "rejected"],
    "notes_required_for_rejected": True,
    "reviewer_isolation": {
        "C01_must_not_see_C02_decisions_or_notes_before_submission": True,
        "C02_must_not_see_C01_decisions_or_notes_before_submission": True,
        "cross_reviewer_discussion_before_submission": False,
        "each_reviewer_receives_only_own_decision_packet": True,
        "reviewer_outputs_are_sealed_until_both_submitted": True,
    },
    "final_decision_rule": [
        {"C01": "approved", "C02": "approved", "action": "corrected_item_accepted_pending_final_gold_freeze"},
        {"C01": "rejected", "C02": "rejected", "action": "correction_rejected_confirmation_blocked"},
        {"C01": "approved", "C02": "rejected", "action": "blind_C03_adjudication"},
        {"C01": "rejected", "C02": "approved", "action": "blind_C03_adjudication"},
        {"C03": "approved", "action": "corrected_item_accepted_pending_final_gold_freeze"},
        {"C03": "rejected", "action": "correction_rejected_confirmation_blocked"},
    ],
    "corrected_adjudicator": {
        "role": "C03",
        "C03_must_be_distinct_from_C01": True,
        "C03_must_be_distinct_from_C02": True,
        "C03_must_not_see_C01_decision": True,
        "C03_must_not_see_C02_decision": True,
        "C03_must_not_see_C01_notes": True,
        "C03_must_not_see_C02_notes": True,
        "C03_must_not_see_model_predictions": True,
        "joint_discussion_allowed": False,
        "only_disagreement_ids_go_to_isolated_C03_packet": True,
    },
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_packet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def expected_corrected_artifacts(
    stage6b_dir: Path,
    setup_dir: Path,
    r04_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    queue = read_json(r04_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json")
    setup_items = {
        item["stage6_sample_id"]: item
        for item in read_jsonl(setup_dir / "artifacts" / "gold_review_items.jsonl")
    }
    corrected_items: list[dict[str, Any]] = []
    corrected_plans: list[dict[str, Any]] = []
    corrected_programs: list[dict[str, Any]] = []
    corrected_post_hashes: list[dict[str, Any]] = []
    operation_rows: list[dict[str, Any]] = []
    for queue_item in queue["items"]:
        sample_id = queue_item["stage6_sample_id"]
        item = setup_items[sample_id]
        operations = normalize_correction_operations(item, queue_item["correction_spec"])
        plan, program = apply_operations_to_plan(item, operations)
        hashes, execution = execute_corrected_insert(stage6b_dir, item, plan, program)
        corrected_plan = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": item["upstream_sample_locator"],
            "table_id": item["source"]["table_id"],
            "operation": "INSERT",
            "column_indexes": plan["column_indexes"],
            "columns": plan["columns"],
            "values": plan["values"],
            "expected_inserted_row": plan["expected_inserted_row"],
            "fresh_db_per_sample": True,
            "source_correctable_authored_content_sha256": queue_item["authored_content_sha256"],
            "corrected_gold_write_plan_sha256": hashes["corrected_gold_write_plan_sha256"],
        }
        corrected_program = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": item["upstream_sample_locator"],
            "table_id": item["source"]["table_id"],
            "sqlite_parameter_style": "qmark",
            "sql_template": program["sql_template"],
            "parameters": program["parameters"],
            "expected_inserted_row": program["expected_inserted_row"],
            "source_correctable_authored_content_sha256": queue_item["authored_content_sha256"],
            "corrected_gold_program_sha256": hashes["corrected_gold_program_sha256"],
        }
        content = {
            "stage": "Stage6D_CORRECTED_GOLD_REVIEW_ITEM",
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": item["upstream_sample_locator"],
            "source": item["source"],
            "human_re_review_scope": [
                "R04-correctable item was corrected deterministically before any model run",
                "Chinese NL plus official CRUDSQL annotation supports the corrected value mapping",
                "corrected INSERT columns and values reflect the intended write",
                "omitted fields remain unsupported by the NL instruction",
            ],
            "question": item["question"],
            "official_annotation": item["official_annotation"],
            "official_table_metadata": item["official_table_metadata"],
            "original_authored_content_sha256": item["authored_content_sha256"],
            "original_gold_write_plan": item["gold_write_plan"],
            "original_gold_program": item["gold_program"],
            "original_hashes": item["hashes"],
            "r04_resolution": {
                "classification": "CORRECTABLE_GOLD_ERROR",
                "rationale": queue_item["rationale"],
                "correction_spec": queue_item["correction_spec"],
                "canonical_correction_operations": operations,
            },
            "corrected_gold_write_plan": {
                "operation": "INSERT",
                "columns": corrected_plan["columns"],
                "column_indexes": corrected_plan["column_indexes"],
                "values": corrected_plan["values"],
                "expected_inserted_row": corrected_plan["expected_inserted_row"],
                "fresh_db_per_sample": True,
            },
            "corrected_gold_program": {
                "sqlite_parameter_style": "qmark",
                "sql_template": corrected_program["sql_template"],
                "parameters": corrected_program["parameters"],
            },
            "corrected_execution_audit": execution,
            "corrected_hashes": {
                **hashes,
                "official_annotation_sha256": item["hashes"]["official_annotation_sha256"],
                "official_table_metadata_sha256": item["hashes"]["official_table_metadata_sha256"],
            },
        }
        corrected_items.append(content | {"corrected_authored_content_sha256": sha256_text(canonical_json(content))})
        corrected_plans.append(corrected_plan)
        corrected_programs.append(corrected_program)
        corrected_post_hashes.append(
            {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": item["upstream_sample_locator"],
                "table_id": item["source"]["table_id"],
                "initial_state_sha256": hashes["initial_state_sha256"],
                "corrected_post_state_sha256": hashes["corrected_post_state_sha256"],
            }
        )
        operation_rows.append(
            {
                "stage6_sample_id": sample_id,
                "r04_rationale": queue_item["rationale"],
                "correction_spec": queue_item["correction_spec"],
                "canonical_correction_operations": operations,
            }
        )
    key = lambda row: row["stage6_sample_id"]
    return (
        sorted(corrected_items, key=key),
        sorted(corrected_plans, key=key),
        sorted(corrected_programs, key=key),
        sorted(corrected_post_hashes, key=key),
        sorted(operation_rows, key=key),
    )


def validate_packet(
    stage6d_dir: Path,
    corrected_items: list[dict[str, Any]],
    reviewer: str,
    archive_name: str,
) -> list[str]:
    violations: list[str] = []
    packet_dir = stage6d_dir / "corrected_review_packets"
    tsv_path = packet_dir / f"stage6d_corrected_gold_review_{reviewer}.tsv"
    manifest_path = packet_dir / f"Stage6D_{reviewer}_PACKET_MANIFEST.json"
    for path in [tsv_path, manifest_path, stage6d_dir / archive_name]:
        if not path.is_file():
            violations.append(f"missing_{reviewer}_packet_file:{path.name}")
    if violations:
        return violations
    fields, rows = read_packet(tsv_path)
    by_id = {item["stage6_sample_id"]: item for item in corrected_items}
    if fields != REVIEW_PACKET_COLUMNS:
        violations.append(f"{reviewer}_columns_changed")
    if len(rows) != 21:
        violations.append(f"{reviewer}_row_count_not_21")
    if set(row["stage6_sample_id"] for row in rows) != set(by_id):
        violations.append(f"{reviewer}_sample_id_set_mismatch")
    for row in rows:
        item = by_id.get(row.get("stage6_sample_id", ""))
        if item is None:
            continue
        if row.get("upstream_sample_locator") != item["upstream_sample_locator"]:
            violations.append(f"{reviewer}_locator_changed:{row.get('stage6_sample_id')}")
        if row.get("corrected_authored_content_sha256") != item["corrected_authored_content_sha256"]:
            violations.append(f"{reviewer}_content_hash_changed:{row.get('stage6_sample_id')}")
        if row.get("reviewed_by") != reviewer:
            violations.append(f"{reviewer}_reviewed_by_changed:{row.get('stage6_sample_id')}")
        if row.get("decision") or row.get("notes"):
            violations.append(f"{reviewer}_decision_or_notes_prefilled:{row.get('stage6_sample_id')}")
    manifest = read_json(manifest_path)
    if manifest.get("reviewer_id") != reviewer:
        violations.append(f"{reviewer}_manifest_reviewer_id_changed")
    if manifest.get("contains_only_own_decision_packet") is not True:
        violations.append(f"{reviewer}_manifest_not_isolated")
    if manifest.get("decision_packet", {}).get("sha256") != sha256_file(tsv_path):
        violations.append(f"{reviewer}_manifest_tsv_hash_mismatch")
    with zipfile.ZipFile(stage6d_dir / archive_name) as archive:
        names = set(archive.namelist())
        if archive.testzip():
            violations.append(f"{reviewer}_archive_testzip_failed")
        other = "C02" if reviewer == "C01" else "C01"
        if any(other in name for name in names):
            violations.append(f"{reviewer}_archive_contains_other_reviewer_packet")
    return violations


def validate_stage6d_corrected_gold_review_setup(
    stage6d_dir: Path,
    stage6b_dir: Path,
    setup_dir: Path,
    r04_dir: Path,
) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = stage6d_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json"
    protocol_path = stage6d_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json"
    artifacts = stage6d_dir / "artifacts"
    required = [
        manifest_path,
        protocol_path,
        artifacts / "corrected_gold_review_items.jsonl",
        artifacts / "corrected_gold_write_plans.jsonl",
        artifacts / "corrected_gold_programs.jsonl",
        artifacts / "corrected_gold_post_state_hashes.jsonl",
        artifacts / "correction_operations.jsonl",
        artifacts / "official_table_metadata.jsonl",
    ]
    for path in required:
        if not path.is_file():
            violations.append(f"missing_file:{path.name}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    manifest = read_json(manifest_path)
    protocol = read_json(protocol_path)
    actual_items = read_jsonl(artifacts / "corrected_gold_review_items.jsonl")
    actual_plans = read_jsonl(artifacts / "corrected_gold_write_plans.jsonl")
    actual_programs = read_jsonl(artifacts / "corrected_gold_programs.jsonl")
    actual_posts = read_jsonl(artifacts / "corrected_gold_post_state_hashes.jsonl")
    actual_ops = read_jsonl(artifacts / "correction_operations.jsonl")
    expected_items, expected_plans, expected_programs, expected_posts, expected_ops = expected_corrected_artifacts(
        stage6b_dir, setup_dir, r04_dir
    )

    if protocol != EXPECTED_PROTOCOL:
        violations.append("protocol_lock_not_exact_expected")
    if len(actual_items) != 21:
        violations.append("corrected_item_count_not_21")
    if actual_items != expected_items:
        violations.append("corrected_gold_review_items_mismatch")
    if actual_plans != expected_plans:
        violations.append("corrected_gold_write_plans_mismatch")
    if actual_programs != expected_programs:
        violations.append("corrected_gold_programs_mismatch")
    if actual_posts != expected_posts:
        violations.append("corrected_gold_post_state_hashes_mismatch")
    if actual_ops != expected_ops:
        violations.append("correction_operations_mismatch")
    for item in actual_items:
        declared = item.get("corrected_authored_content_sha256")
        payload = {key: value for key, value in item.items() if key != "corrected_authored_content_sha256"}
        if declared != sha256_text(canonical_json(payload)):
            violations.append(f"corrected_authored_content_hash_mismatch:{item.get('stage6_sample_id')}")
        if item.get("original_authored_content_sha256") == declared:
            violations.append(f"corrected_hash_equal_original_hash:{item.get('stage6_sample_id')}")
    for field, rel in {
        "corrected_gold_review_items_sha256": artifacts / "corrected_gold_review_items.jsonl",
        "corrected_gold_write_plans_sha256": artifacts / "corrected_gold_write_plans.jsonl",
        "corrected_gold_programs_sha256": artifacts / "corrected_gold_programs.jsonl",
        "corrected_gold_post_state_hashes_sha256": artifacts / "corrected_gold_post_state_hashes.jsonl",
        "correction_operations_sha256": artifacts / "correction_operations.jsonl",
        "protocol_lock_sha256": protocol_path,
    }.items():
        if manifest.get(field) != sha256_file(rel):
            violations.append(f"manifest_{field}_mismatch")
    for label, value in {
        "model_called": manifest.get("model_called"),
        "gpu_called": manifest.get("gpu_called"),
        "confirmation_run_allowed_now": manifest.get("confirmation_run_allowed_now"),
        "final_gold_freeze_created": manifest.get("final_gold_freeze_created"),
    }.items():
        if value is not False:
            violations.append(f"{label}_not_false")
    if manifest.get("status") != "PASS_PENDING_CORRECTED_ITEM_RE_REVIEW":
        violations.append("manifest_status_changed")
    if manifest.get("stage6c_r04_resolution_commit") != STAGE6C_R04_COMMIT:
        violations.append("manifest_r04_commit_mismatch")
    expected_inputs = {
        "accepted_R04_commit": STAGE6C_R04_COMMIT,
        "CORRECTABLE_GOLD_ERROR_QUEUE_SHA256": ACCEPTED_CORRECTABLE_QUEUE_SHA256,
        "SOURCE_TASK_INVALID_QUEUE_SHA256": ACCEPTED_SOURCE_INVALID_QUEUE_SHA256,
    }
    if manifest.get("accepted_r04_inputs") != expected_inputs:
        violations.append("manifest_accepted_r04_inputs_mismatch")
    actual_correctable_queue_sha = sha256_file(r04_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json")
    actual_source_invalid_queue_sha = sha256_file(r04_dir / "SOURCE_TASK_INVALID_QUEUE.json")
    if actual_correctable_queue_sha != ACCEPTED_CORRECTABLE_QUEUE_SHA256:
        violations.append("actual_correctable_queue_not_accepted_hash")
    if actual_source_invalid_queue_sha != ACCEPTED_SOURCE_INVALID_QUEUE_SHA256:
        violations.append("actual_source_invalid_queue_not_accepted_hash")
    if manifest.get("correctable_queue_sha256") != ACCEPTED_CORRECTABLE_QUEUE_SHA256:
        violations.append("manifest_correctable_queue_not_accepted_hash")
    if manifest.get("source_invalid_queue_sha256") != ACCEPTED_SOURCE_INVALID_QUEUE_SHA256:
        violations.append("manifest_source_invalid_queue_not_accepted_hash")
    if manifest.get("corrected_item_count") != 21:
        violations.append("manifest_corrected_item_count_not_21")
    if manifest.get("source_invalid_item_count_not_processed_here") != 19:
        violations.append("source_invalid_count_processed_or_changed")

    violations.extend(validate_packet(stage6d_dir, actual_items, "C01", C01_ARCHIVE_NAME))
    violations.extend(validate_packet(stage6d_dir, actual_items, "C02", C02_ARCHIVE_NAME))
    archive_path = stage6d_dir / ARCHIVE_NAME
    if not archive_path.is_file():
        violations.append("stage6d_archive_missing")
    else:
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip():
                violations.append("stage6d_archive_testzip_failed")
    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6D_CORRECTED_GOLD_RE_REVIEW_SETUP",
        "corrected_item_count": len(actual_items),
        "source_invalid_item_count_not_processed_here": 19,
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "final_gold_freeze_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage6d-dir", default="stage6_corrected_gold_review_setup")
    parser.add_argument("--stage6b-dir", default="stage6_crudsql_registration")
    parser.add_argument("--setup-dir", default="stage6_gold_review_setup")
    parser.add_argument("--r04-dir", default="stage6_gold_review_r04_resolution")
    args = parser.parse_args(argv)
    report = validate_stage6d_corrected_gold_review_setup(
        Path(args.stage6d_dir),
        Path(args.stage6b_dir),
        Path(args.setup_dir),
        Path(args.r04_dir),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
