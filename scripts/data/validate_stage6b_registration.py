#!/usr/bin/env python3
"""Validate the CPU-only Stage 6B CRUDSQL dataset registration."""

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

from scripts.data.register_crudsql_stage6b import (
    ARCHIVE_NAME,
    CRUDSQL_COMMIT,
    STAGE5_METHOD_COMMIT,
    STAGE5_PROTOCOL_COMMIT,
    STAGE6A_ACCEPTED_COMMIT,
    registry_self_hash,
)


EXPECTED_ARTIFACT_HASH_FIELDS = {
    "registered_ids_sha256": "artifacts/registered_ids.tsv",
    "registered_samples_sha256": "artifacts/registered_samples.jsonl",
    "gold_write_plans_sha256": "artifacts/gold_write_plans.jsonl",
    "gold_programs_sha256": "artifacts/gold_programs.jsonl",
    "gold_post_state_hashes_sha256": "artifacts/gold_post_state_hashes.jsonl",
    "isolated_table_db_manifest_sha256": "artifacts/isolated_table_db_manifest.json",
    "overlap_registry_sha256": "artifacts/stage6_seen_reference_registry.json",
    "overlap_audit_sha256": "artifacts/crudsql_overlap_audit.json",
    "distribution_report_sha256": "artifacts/distribution_report.json",
    "gold_review_protocol_sha256": "GOLD_REVIEW_PROTOCOL_LOCK.json",
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


def add_violation(violations: list[str], condition: bool, message: str) -> None:
    if condition:
        violations.append(message)


def validate_registration(registration_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = registration_dir / "CONFIRMATION_DATASET_MANIFEST.json"
    lock_path = registration_dir / "STAGE6B_REGISTRATION_LOCK.json"
    gold_protocol_path = registration_dir / "GOLD_REVIEW_PROTOCOL_LOCK.json"
    artifacts = registration_dir / "artifacts"

    required_files = [
        manifest_path,
        lock_path,
        gold_protocol_path,
        registration_dir / "REVIEWER_README.md",
        registration_dir / "VALIDATION_REPORT.md",
    ]
    for path in required_files:
        add_violation(violations, not path.is_file(), f"missing_file:{path.name}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    manifest = read_json(manifest_path)
    lock = read_json(lock_path)
    gold_protocol = read_json(gold_protocol_path)

    add_violation(violations, lock.get("status") != "PASS_REGISTERED_PENDING_REVIEWER_ACCEPTANCE", "lock_status_not_pass")
    add_violation(violations, manifest.get("status") != "REGISTERED_PENDING_REVIEWER_ACCEPTANCE", "manifest_status_not_registered")
    for name, value in {
        "lock_confirmation_run_allowed_now": lock.get("confirmation_run_allowed_now"),
        "manifest_confirmation_run_allowed_now": manifest.get("confirmation_run_allowed_now"),
        "gold_protocol_confirmation_run_allowed_now": gold_protocol.get("confirmation_run_allowed_now"),
        "lock_model_called": lock.get("model_called"),
        "lock_gpu_called": lock.get("gpu_called"),
        "manifest_model_called": manifest.get("model_called"),
        "manifest_gpu_called": manifest.get("gpu_called"),
    }.items():
        expected = False
        add_violation(violations, value is not expected, f"{name}_not_false")

    source = manifest.get("source") or {}
    add_violation(violations, source.get("commit") != CRUDSQL_COMMIT, "crudsql_commit_not_locked")
    add_violation(violations, source.get("split") != "official test", "split_not_official_test")
    add_violation(violations, source.get("subset") != "all type=0 Create examples", "subset_not_all_type0_create")
    add_violation(violations, source.get("sampling") != "none_use_all_eligible_examples", "sampling_not_none")
    add_violation(violations, manifest.get("sample_count") != 500, "sample_count_not_500")
    add_violation(violations, manifest.get("table_count") != 125, "table_count_not_125")
    add_violation(violations, manifest.get("stage6a_accepted_commit") != STAGE6A_ACCEPTED_COMMIT, "stage6a_commit_not_locked")
    add_violation(violations, manifest.get("stage5_protocol_commit") != STAGE5_PROTOCOL_COMMIT, "stage5_protocol_commit_not_locked")
    add_violation(violations, manifest.get("stage5_method_commit") != STAGE5_METHOD_COMMIT, "stage5_method_commit_not_locked")

    for field, rel_path in EXPECTED_ARTIFACT_HASH_FIELDS.items():
        path = registration_dir / rel_path
        if not path.is_file():
            violations.append(f"missing_hashed_artifact:{rel_path}")
        elif manifest.get(field) != sha256_file(path):
            violations.append(f"manifest_hash_mismatch:{field}")
    for field, rel_path in {
        "dataset_manifest_sha256": "CONFIRMATION_DATASET_MANIFEST.json",
        "reviewer_readme_sha256": "REVIEWER_README.md",
        "validation_report_sha256": "VALIDATION_REPORT.md",
    }.items():
        path = registration_dir / rel_path
        if lock.get(field) != sha256_file(path):
            violations.append(f"lock_hash_mismatch:{field}")

    samples = read_jsonl(artifacts / "registered_samples.jsonl")
    plans = read_jsonl(artifacts / "gold_write_plans.jsonl")
    programs = read_jsonl(artifacts / "gold_programs.jsonl")
    post_hashes = read_jsonl(artifacts / "gold_post_state_hashes.jsonl")
    table_manifest = read_json(artifacts / "isolated_table_db_manifest.json")
    registry = read_json(artifacts / "stage6_seen_reference_registry.json")
    overlap = read_json(artifacts / "crudsql_overlap_audit.json")
    distribution = read_json(artifacts / "distribution_report.json")

    add_violation(violations, len(samples) != 500, "registered_samples_count_not_500")
    add_violation(violations, len(plans) != 500, "gold_write_plans_count_not_500")
    add_violation(violations, len(programs) != 500, "gold_programs_count_not_500")
    add_violation(violations, len(post_hashes) != 500, "gold_post_state_hashes_count_not_500")
    add_violation(violations, len(table_manifest) != 125, "isolated_db_manifest_count_not_125")
    add_violation(violations, distribution.get("sample_count") != 500, "distribution_sample_count_not_500")
    add_violation(violations, distribution.get("table_count") != 125, "distribution_table_count_not_125")

    sample_ids = [row["stage6_sample_id"] for row in samples]
    locators = [row["upstream_sample_locator"] for row in samples]
    add_violation(violations, len(sample_ids) != len(set(sample_ids)), "duplicate_stage6_sample_id")
    add_violation(violations, len(locators) != len(set(locators)), "duplicate_upstream_sample_locator")
    expected_ids = set(sample_ids)
    for label, rows in {
        "gold_write_plans": plans,
        "gold_programs": programs,
        "gold_post_state_hashes": post_hashes,
    }.items():
        ids = {row["stage6_sample_id"] for row in rows}
        add_violation(violations, ids != expected_ids, f"{label}_id_set_mismatch")

    declared_registry_hash = registry.get("registry_sha256_excluding_self")
    add_violation(violations, declared_registry_hash != registry_self_hash(registry), "registry_self_hash_mismatch")
    add_violation(violations, manifest.get("overlap_registry_self_hash") != declared_registry_hash, "manifest_registry_self_hash_mismatch")
    add_violation(violations, int((registry.get("digest_counts") or {}).get("input_text_sha256") or 0) <= 0, "registry_input_text_hashes_empty")

    add_violation(violations, overlap.get("status") != "PASS", "overlap_status_not_pass")
    for key, value in sorted(overlap.items()):
        if key.endswith("_overlap_count") and int(value) != 0:
            violations.append(f"nonzero_overlap_count:{key}={value}")

    db_hashes = set()
    for row in table_manifest:
        rel_path = row["isolated_db_path"]
        db_name = Path(rel_path).name
        db_path = registration_dir / "isolated_table_dbs" / db_name
        if not db_path.is_file():
            violations.append(f"missing_isolated_db:{db_name}")
            continue
        actual_hash = sha256_file(db_path)
        if actual_hash != row["isolated_db_sha256"]:
            violations.append(f"isolated_db_hash_mismatch:{db_name}")
        db_hashes.add(actual_hash)
    add_violation(violations, len(db_hashes) != 125, "isolated_db_hashes_not_unique_125")

    add_violation(violations, gold_protocol.get("two_independent_reviews_required") is not True, "gold_two_reviews_not_required")
    add_violation(violations, gold_protocol.get("reviewer_must_not_see_model_predictions") is not True, "gold_review_prediction_blinding_not_locked")
    add_violation(violations, gold_protocol.get("final_gold_hash_required_before_gpu") is not True, "final_gold_hash_not_required")

    archive_info = lock.get("dataset_archive") or {}
    archive_path = registration_dir / archive_info.get("path", ARCHIVE_NAME)
    if not archive_path.is_file():
        violations.append("dataset_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("dataset_archive_sha256_mismatch")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                bad_member = archive.testzip()
                names = set(archive.namelist())
            if bad_member is not None:
                violations.append(f"dataset_archive_bad_member:{bad_member}")
            for name in [
                "CONFIRMATION_DATASET_MANIFEST.json",
                "GOLD_REVIEW_PROTOCOL_LOCK.json",
                "artifacts/registered_samples.jsonl",
                "artifacts/gold_write_plans.jsonl",
                "artifacts/gold_programs.jsonl",
                "artifacts/gold_post_state_hashes.jsonl",
            ]:
                if name not in names:
                    violations.append(f"dataset_archive_missing_member:{name}")
            db_member_count = sum(name.startswith("isolated_table_dbs/") and name.endswith(".sqlite") for name in names)
            if db_member_count != 125:
                violations.append(f"dataset_archive_db_member_count_not_125:{db_member_count}")
            if archive_info.get("member_count") != len(names):
                violations.append("dataset_archive_member_count_mismatch")
        except zipfile.BadZipFile:
            violations.append("dataset_archive_not_openable")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6B_CRUDSQL_CONFIRMATION_DATASET_REGISTRATION",
        "sample_count": len(samples),
        "table_count": len(table_manifest),
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration-dir", default="stage6_crudsql_registration")
    args = parser.parse_args(argv)
    report = validate_registration(Path(args.registration_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
