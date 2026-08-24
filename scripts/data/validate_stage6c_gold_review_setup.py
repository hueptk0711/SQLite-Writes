#!/usr/bin/env python3
"""Validate the CPU-only Stage 6C gold-review setup package."""

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

from scripts.data.create_stage6c_gold_review_setup import (  # noqa: E402
    PATCH0_GOLD_REVIEW_ITEMS_SHA256,
    PATCH0_OFFICIAL_TABLE_METADATA_SHA256,
    REVIEW_PACKET_COLUMNS,
    R01_ARCHIVE_NAME,
    R02_ARCHIVE_NAME,
    STAGE6B_COMMIT,
    canonical_json,
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


def read_packet(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != REVIEW_PACKET_COLUMNS:
            raise ValueError(f"Unexpected packet columns: {reader.fieldnames}")
        return list(reader)


def validate_setup(setup_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    lock_path = setup_dir / "STAGE6C_GOLD_REVIEW_SETUP_LOCK.json"
    addendum_path = setup_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"
    manifest_path = setup_dir / "GOLD_REVIEW_PACKET_MANIFEST.json"
    items_path = setup_dir / "artifacts" / "gold_review_items.jsonl"
    table_metadata_path = setup_dir / "artifacts" / "official_table_metadata.jsonl"
    packet_r01 = setup_dir / "review_packets" / "stage6c_gold_review_R01.tsv"
    packet_r02 = setup_dir / "review_packets" / "stage6c_gold_review_R02.tsv"
    r01_archive_path = setup_dir / R01_ARCHIVE_NAME
    r02_archive_path = setup_dir / R02_ARCHIVE_NAME

    for path in [
        lock_path,
        addendum_path,
        manifest_path,
        items_path,
        table_metadata_path,
        packet_r01,
        packet_r02,
        setup_dir / "review_packets" / "Stage6C_R01_PACKET_MANIFEST.json",
        setup_dir / "review_packets" / "Stage6C_R02_PACKET_MANIFEST.json",
        r01_archive_path,
        r02_archive_path,
        setup_dir / "REVIEWER_README.md",
        setup_dir / "VALIDATION_REPORT.md",
    ]:
        if not path.is_file():
            violations.append(f"missing_file:{path.relative_to(setup_dir).as_posix()}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    lock = read_json(lock_path)
    addendum = read_json(addendum_path)
    manifest = read_json(manifest_path)
    items = read_jsonl(items_path)
    table_metadata = read_jsonl(table_metadata_path)

    if lock.get("status") != "PASS_LOCKED_PENDING_HUMAN_REVIEW_EXECUTION":
        violations.append("lock_status_not_pending_execution")
    if addendum.get("status") != "LOCKED_PENDING_HUMAN_REVIEW_EXECUTION":
        violations.append("addendum_status_not_pending_execution")
    if manifest.get("status") != "PACKETS_CREATED_PENDING_HUMAN_REVIEW":
        violations.append("packet_manifest_status_not_pending_review")
    for label, obj in {"lock": lock, "addendum": addendum, "manifest": manifest}.items():
        if obj.get("model_called") is not False:
            violations.append(f"{label}_model_called_not_false")
        if obj.get("gpu_called") is not False:
            violations.append(f"{label}_gpu_called_not_false")
        if obj.get("confirmation_run_allowed_now") is not False:
            violations.append(f"{label}_confirmation_run_allowed_now_not_false")

    if lock.get("stage6b_commit") != STAGE6B_COMMIT:
        violations.append("stage6b_commit_not_locked")
    if len(items) != 500 or manifest.get("sample_count") != 500 or lock.get("sample_count") != 500:
        violations.append("sample_count_not_500")
    if len(table_metadata) != 125:
        violations.append("official_table_metadata_count_not_125")

    adjudication = addendum.get("adjudication_policy") or {}
    rejection = addendum.get("rejection_policy") or {}
    reviewer_isolation = addendum.get("reviewer_isolation") or {}
    final_decision_rule = addendum.get("final_decision_rule") or []
    if reviewer_isolation.get("reviewer_roles_must_be_distinct") is not True:
        violations.append("reviewer_roles_must_be_distinct_not_locked")
    if reviewer_isolation.get("R01_must_not_see_R02_decisions_or_notes_before_submission") is not True:
        violations.append("R01_blind_to_R02_review_not_locked")
    if reviewer_isolation.get("R02_must_not_see_R01_decisions_or_notes_before_submission") is not True:
        violations.append("R02_blind_to_R01_review_not_locked")
    if reviewer_isolation.get("cross_reviewer_discussion_before_submission") is not False:
        violations.append("cross_reviewer_discussion_before_submission_not_forbidden")
    if reviewer_isolation.get("each_reviewer_receives_only_own_decision_packet") is not True:
        violations.append("own_packet_distribution_not_locked")
    if reviewer_isolation.get("reviewer_outputs_are_sealed_until_both_submitted") is not True:
        violations.append("sealed_outputs_until_both_submitted_not_locked")
    if adjudication.get("disagreement_rule") != "third_independent_adjudicator":
        violations.append("adjudication_rule_not_third_independent")
    if adjudication.get("joint_discussion_allowed") is not False:
        violations.append("joint_discussion_not_forbidden")
    if adjudication.get("decision_after_seeing_model_outputs_allowed") is not False:
        violations.append("post_model_adjudication_not_forbidden")
    if adjudication.get("third_adjudicator_must_not_see_model_predictions") is not True:
        violations.append("third_adjudicator_blinding_not_locked")
    for field in [
        "R03_must_not_see_R01_decision",
        "R03_must_not_see_R02_decision",
        "R03_must_not_see_R01_notes",
        "R03_must_not_see_R02_notes",
    ]:
        if adjudication.get(field) is not True:
            violations.append(f"{field}_not_locked")
    expected_rules = {
        ("approved", "approved", "final_approved"),
        ("rejected", "rejected", "final_rejected_confirmation_blocked"),
        ("approved", "rejected", "blind_R03_adjudication"),
        ("rejected", "approved", "blind_R03_adjudication"),
    }
    observed_rules = {
        (row.get("R01"), row.get("R02"), row.get("action"))
        for row in final_decision_rule
        if "R01" in row and "R02" in row
    }
    if observed_rules != expected_rules:
        violations.append("final_decision_table_R01_R02_rules_mismatch")
    observed_r03_rules = {(row.get("R03"), row.get("action")) for row in final_decision_rule if "R03" in row}
    if observed_r03_rules != {
        ("approved", "final_approved"),
        ("rejected", "final_rejected_confirmation_blocked"),
    }:
        violations.append("final_decision_table_R03_rules_mismatch")
    if rejection.get("any_final_unresolved_or_rejected_gold_blocks_confirmation") is not True:
        violations.append("final_rejected_or_unresolved_does_not_block")
    if rejection.get("initial_R01_R02_disagreement_goes_to_blind_R03_not_immediate_block") is not True:
        violations.append("initial_disagreement_R03_resolution_not_locked")
    if rejection.get("drop_rejected_samples_allowed") is not False:
        violations.append("drop_rejected_samples_not_forbidden")
    if rejection.get("silent_exclusion_allowed") is not False:
        violations.append("silent_exclusion_not_forbidden")

    for field, rel_path in {
        "protocol_addendum_sha256": "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json",
        "packet_manifest_sha256": "GOLD_REVIEW_PACKET_MANIFEST.json",
        "reviewer_readme_sha256": "REVIEWER_README.md",
        "validation_report_sha256": "VALIDATION_REPORT.md",
    }.items():
        if lock.get(field) != sha256_file(setup_dir / rel_path):
            violations.append(f"lock_hash_mismatch:{field}")
    if manifest.get("gold_review_items_sha256") != sha256_file(items_path):
        violations.append("manifest_gold_review_items_hash_mismatch")
    if manifest.get("official_table_metadata_sha256") != sha256_file(table_metadata_path):
        violations.append("manifest_table_metadata_hash_mismatch")
    if sha256_file(items_path) != PATCH0_GOLD_REVIEW_ITEMS_SHA256:
        violations.append("patch1_gold_review_items_changed_from_patch0")
    if sha256_file(table_metadata_path) != PATCH0_OFFICIAL_TABLE_METADATA_SHA256:
        violations.append("patch1_official_table_metadata_changed_from_patch0")
    if manifest.get("protocol_addendum_sha256") != sha256_file(addendum_path):
        violations.append("manifest_protocol_hash_mismatch")
    if manifest.get("reviewer_isolation_distribution") != "separate_archive_per_reviewer":
        violations.append("reviewer_isolation_distribution_not_separate_archives")

    item_by_id = {row["stage6_sample_id"]: row for row in items}
    if len(item_by_id) != len(items):
        violations.append("duplicate_review_item_id")
    for item in items:
        declared = item.get("authored_content_sha256")
        payload = {key: value for key, value in item.items() if key != "authored_content_sha256"}
        if declared != sha256_text(canonical_json(payload)):
            violations.append(f"authored_content_hash_mismatch:{item.get('stage6_sample_id')}")
        required_paths = [
            ("official_annotation", item.get("official_annotation")),
            ("official_table_metadata", item.get("official_table_metadata")),
            ("gold_write_plan", item.get("gold_write_plan")),
            ("gold_program", item.get("gold_program")),
        ]
        for label, value in required_paths:
            if not value:
                violations.append(f"missing_review_content:{label}:{item.get('stage6_sample_id')}")

    try:
        rows_r01 = read_packet(packet_r01)
        rows_r02 = read_packet(packet_r02)
    except ValueError as exc:
        return {"status": "FAIL", "violations": violations + [str(exc)]}
    for reviewer, rows in {"R01": rows_r01, "R02": rows_r02}.items():
        if len(rows) != 500:
            violations.append(f"{reviewer}_packet_count_not_500")
        seen_ids = set()
        for row in rows:
            seen_ids.add(row["stage6_sample_id"])
            item = item_by_id.get(row["stage6_sample_id"])
            if item is None:
                violations.append(f"{reviewer}_unknown_sample_id:{row['stage6_sample_id']}")
                continue
            if row["upstream_sample_locator"] != item["upstream_sample_locator"]:
                violations.append(f"{reviewer}_locator_mismatch:{row['stage6_sample_id']}")
            if row["authored_content_sha256"] != item["authored_content_sha256"]:
                violations.append(f"{reviewer}_content_hash_mismatch:{row['stage6_sample_id']}")
            if row["reviewed_by"] != reviewer:
                violations.append(f"{reviewer}_reviewer_id_mismatch:{row['stage6_sample_id']}")
            if row["decision"] != "":
                violations.append(f"{reviewer}_decision_not_blank_before_execution:{row['stage6_sample_id']}")
            if row["notes"] != "":
                violations.append(f"{reviewer}_notes_not_blank_before_execution:{row['stage6_sample_id']}")
        if seen_ids != set(item_by_id):
            violations.append(f"{reviewer}_packet_id_set_mismatch")

    archives_by_role = {row.get("reviewer_id"): row.get("isolated_archive") for row in manifest.get("reviewer_packets") or []}
    lock_archives = {Path(row.get("path", "")).name: row for row in lock.get("review_packet_archives") or []}
    for reviewer, archive_name, own_tsv, other_tsv in [
        ("R01", R01_ARCHIVE_NAME, "review_packets/stage6c_gold_review_R01.tsv", "review_packets/stage6c_gold_review_R02.tsv"),
        ("R02", R02_ARCHIVE_NAME, "review_packets/stage6c_gold_review_R02.tsv", "review_packets/stage6c_gold_review_R01.tsv"),
    ]:
        archive_info = archives_by_role.get(reviewer) or {}
        lock_archive_info = lock_archives.get(archive_name) or {}
        archive_path = setup_dir / archive_info.get("path", archive_name)
        if not archive_path.is_file():
            violations.append(f"{reviewer}_isolated_archive_missing")
            continue
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append(f"{reviewer}_manifest_archive_sha256_mismatch")
        if lock_archive_info.get("sha256") != sha256_file(archive_path):
            violations.append(f"{reviewer}_lock_archive_sha256_mismatch")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                bad_member = archive.testzip()
                names = set(archive.namelist())
            if bad_member is not None:
                violations.append(f"{reviewer}_isolated_archive_bad_member:{bad_member}")
            for name in [
                "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json",
                "artifacts/gold_review_items.jsonl",
                "artifacts/official_table_metadata.jsonl",
                own_tsv,
            ]:
                if name not in names:
                    violations.append(f"{reviewer}_isolated_archive_missing_member:{name}")
            if other_tsv in names:
                violations.append(f"{reviewer}_isolated_archive_contains_other_reviewer_tsv")
            if archive_info.get("member_count") != len(names):
                violations.append(f"{reviewer}_manifest_archive_member_count_mismatch")
            if lock_archive_info.get("member_count") != len(names):
                violations.append(f"{reviewer}_lock_archive_member_count_mismatch")
        except zipfile.BadZipFile:
            violations.append(f"{reviewer}_isolated_archive_not_openable")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6C_INDEPENDENT_GOLD_REVIEW_SETUP",
        "sample_count": len(items),
        "reviewer_packets": 2,
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup-dir", default="stage6_gold_review_setup")
    args = parser.parse_args(argv)
    report = validate_setup(Path(args.setup_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
