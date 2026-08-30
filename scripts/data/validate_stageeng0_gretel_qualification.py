#!/usr/bin/env python3
"""Validate StageENG0 Gretel qualification artifacts."""

from __future__ import annotations

import argparse
import json
import tempfile
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng0_gretel_qualification import (
    DATASET_ID,
    DATASET_REVISION,
    RAW_FILES,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    SUPPORTED_PRIMARY_LITERAL_KINDS,
    build_run,
    load_parquet_rows,
    sha256_file,
)


REQUIRED_STAGE_FILES = [
    "DATASET_SOURCE_LOCK.json",
    "RAW_DATA_HASHES.json",
    "RAW_SCHEMA_AUDIT.json",
    "ELIGIBILITY_POLICY.json",
    "DML_OPERATION_COUNTS.json",
    "WRITE_COMPLEXITY_AUDIT.json",
    "SQLITE_CONTEXT_AUDIT.jsonl",
    "SQLITE_COMPATIBILITY_SUMMARY.json",
    "GOLD_EXECUTION_AUDIT.jsonl",
    "SOURCE_ALIGNABILITY_AUDIT.jsonl",
    "SOURCE_ALIGNABILITY_SUMMARY.json",
    "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl",
    "INSERT_GROUNDING_SUMMARY.json",
    "EXCLUSION_LEDGER.jsonl",
    "EXCLUSION_REASON_COUNTS.json",
    "DATA_LEAKAGE_AUDIT.json",
    "SPLIT_CANDIDATE_AUDIT.json",
    "ELIGIBLE_INSERT_MANIFEST.jsonl",
    "ELIGIBLE_UPDATE_MANIFEST.jsonl",
    "ELIGIBLE_DELETE_MANIFEST.jsonl",
    "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl",
    "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl",
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGEENG0_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def expected_primary_insert(row: dict[str, Any]) -> bool:
    grounding = row.get("insert_assignment_grounding") or {}
    return bool(
        row.get("sqlite_write_eligible")
        and row.get("operation") == "INSERT"
        and row.get("complexity_class") == "single_row_insert"
        and grounding.get("all_assignments_supported_direct_literal") is True
        and grounding.get("all_assignments_individually_source_alignable") is True
        and grounding.get("jointly_source_representable") is True
    )


def validate(
    stage_dir: Path,
    raw_dir: Path | None = None,
    *,
    rebuild_from_raw: bool = True,
) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_STAGE_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")

    if failures:
        return {"status": "FAIL", "failures": failures}

    source_lock = read_json(stage_dir / "DATASET_SOURCE_LOCK.json")
    raw_hashes = read_json(stage_dir / "RAW_DATA_HASHES.json")
    schema = read_json(stage_dir / "RAW_SCHEMA_AUDIT.json")
    dml_counts = read_json(stage_dir / "DML_OPERATION_COUNTS.json")
    policy = read_json(stage_dir / "ELIGIBILITY_POLICY.json")
    lock = read_json(stage_dir / "STAGEENG0_LOCK.json")
    context_rows = read_jsonl(stage_dir / "SQLITE_CONTEXT_AUDIT.jsonl")
    gold_rows = read_jsonl(stage_dir / "GOLD_EXECUTION_AUDIT.jsonl")
    align_rows = read_jsonl(stage_dir / "SOURCE_ALIGNABILITY_AUDIT.jsonl")
    assignment_rows = read_jsonl(stage_dir / "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl")
    ledger_rows = read_jsonl(stage_dir / "EXCLUSION_LEDGER.jsonl")
    manifests = {
        "INSERT": read_jsonl(stage_dir / "ELIGIBLE_INSERT_MANIFEST.jsonl"),
        "UPDATE": read_jsonl(stage_dir / "ELIGIBLE_UPDATE_MANIFEST.jsonl"),
        "DELETE": read_jsonl(stage_dir / "ELIGIBLE_DELETE_MANIFEST.jsonl"),
    }
    development_candidates = read_jsonl(stage_dir / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl")
    confirmation_candidates = read_jsonl(stage_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")
    derived_manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")

    if source_lock.get("dataset_id") != DATASET_ID:
        failures.append("dataset_id_mismatch")
    if source_lock.get("revision") != DATASET_REVISION:
        failures.append("dataset_revision_mismatch")
    if lock.get("model_called") is not False or source_lock.get("model_called") is not False:
        failures.append("model_called_not_false")
    if lock.get("gpu_called") is not False or source_lock.get("gpu_called") is not False:
        failures.append("gpu_called_not_false")
    if policy.get("model_outputs_allowed") is not False:
        failures.append("eligibility_policy_allows_model_outputs")
    primary_policy = policy.get("v2_literal_grounded_primary_scope")
    if not isinstance(primary_policy, dict):
        failures.append("primary_policy_not_machine_readable")
    else:
        if primary_policy.get("operation") != "INSERT":
            failures.append("primary_policy_operation_mismatch")
        if primary_policy.get("complexity_class") != "single_row_insert":
            failures.append("primary_policy_complexity_mismatch")
        if set(primary_policy.get("assignment_value_kinds", [])) != SUPPORTED_PRIMARY_LITERAL_KINDS:
            failures.append("primary_policy_literal_kinds_mismatch")
        if primary_policy.get("require_all_assignments_individually_source_alignable") is not True:
            failures.append("primary_policy_individual_grounding_not_required")
        if primary_policy.get("require_joint_one_to_one_source_matching") is not True:
            failures.append("primary_policy_joint_matching_not_required")
        if "automatic_exclusion" not in str(primary_policy.get("multiple_source_occurrences", "")):
            failures.append("primary_policy_multiple_occurrences_semantics_missing")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(
        stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"
    ):
        failures.append("derived_artifact_manifest_hash_mismatch")
    manifest_by_path = {row["path"]: row for row in derived_manifest.get("artifacts", [])}
    for name in SCIENTIFIC_ARTIFACTS:
        path = stage_dir / name
        if name not in manifest_by_path:
            failures.append(f"derived_manifest_missing_artifact:{name}")
            continue
        if not path.exists():
            failures.append(f"derived_artifact_missing:{name}")
            continue
        if sha256_file(path) != manifest_by_path[name].get("sha256"):
            failures.append(f"derived_artifact_hash_mismatch:{name}")

    raw_total = int(dml_counts["raw_total"])
    if sum(schema["split_counts"].values()) != raw_total:
        failures.append("raw_total_does_not_match_split_counts")
    if len(context_rows) != raw_total or len(gold_rows) != raw_total or len(align_rows) != raw_total:
        failures.append("audit_jsonl_rows_do_not_cover_raw_total")

    dml_total = int(dml_counts["dml_total"])
    if len(ledger_rows) != dml_total:
        failures.append("exclusion_ledger_does_not_cover_dml_population")
    ledger_ids = [row["sample_id"] for row in ledger_rows]
    if len(ledger_ids) != len(set(ledger_ids)):
        failures.append("duplicate_sample_id_in_ledger")

    eligible_ledger_ids = {row["sample_id"] for row in ledger_rows if row["status"] == "eligible"}
    excluded_with_no_reason = [
        row["sample_id"]
        for row in ledger_rows
        if row["status"] == "excluded" and not row.get("exclusion_reasons")
    ]
    if excluded_with_no_reason:
        failures.append(f"excluded_without_reason:{len(excluded_with_no_reason)}")

    manifest_ids: list[str] = []
    for operation, rows in manifests.items():
        for row in rows:
            manifest_ids.append(row["sample_id"])
            if row.get("operation") != operation:
                failures.append(f"manifest_operation_mismatch:{row['sample_id']}")
            if not row.get("sqlite_write_eligible"):
                failures.append(f"manifest_contains_ineligible:{row['sample_id']}")
            if operation == "INSERT":
                expected_primary = expected_primary_insert(row)
                if bool(row.get("v2_literal_grounded_primary_eligible")) != expected_primary:
                    failures.append(f"primary_insert_flag_policy_mismatch:{row['sample_id']}")
    if len(manifest_ids) != len(set(manifest_ids)):
        failures.append("sample_id_in_multiple_manifests")
    if set(manifest_ids) != eligible_ledger_ids:
        failures.append("manifest_ids_do_not_match_eligible_ledger")
    primary_insert_ids = {
        row["sample_id"]
        for row in manifests["INSERT"]
        if row.get("v2_literal_grounded_primary_eligible")
    }
    development_ids = {row["sample_id"] for row in development_candidates}
    confirmation_ids = {row["sample_id"] for row in confirmation_candidates}
    if development_ids & confirmation_ids:
        failures.append("development_candidate_intersects_official_test_confirmation")
    if development_ids | confirmation_ids != primary_insert_ids:
        failures.append("primary_insert_ids_do_not_match_dev_plus_confirmation")
    for row in development_candidates:
        if row.get("source_split") != "train" or row.get("development_allowed") is not True:
            failures.append(f"development_candidate_not_train_allowed:{row.get('sample_id')}")
    for row in confirmation_candidates:
        if row.get("source_split") != "test" or row.get("official_test_confirmation_only") is not True:
            failures.append(f"confirmation_candidate_not_test_only:{row.get('sample_id')}")
    for row in manifests["INSERT"]:
        if row.get("source_split") == "test" and row.get("development_allowed"):
            failures.append(f"official_test_insert_marked_development_allowed:{row.get('sample_id')}")

    assignment_ids = {row["sample_id"] for row in assignment_rows}
    for row in manifests["INSERT"]:
        if row.get("complexity_class") == "single_row_insert" and row["sample_id"] not in assignment_ids:
            failures.append(f"single_row_insert_missing_assignment_audit:{row['sample_id']}")

    gold_by_id = {row["sample_id"]: row for row in gold_rows}
    for sample_id in eligible_ledger_ids:
        gold = gold_by_id.get(sample_id)
        if not gold:
            failures.append(f"eligible_missing_gold_audit:{sample_id}")
            continue
        if gold.get("exec_status") != "success" or gold.get("deterministic") is not True:
            failures.append(f"eligible_gold_not_success_deterministic:{sample_id}")
        if not gold.get("before_state_hash") or not gold.get("after_state_hash"):
            failures.append(f"eligible_missing_state_hash:{sample_id}")

    status_counts = Counter(row["status"] for row in ledger_rows)
    if status_counts["eligible"] != len(manifest_ids):
        failures.append("eligible_count_does_not_match_manifest_count")

    if raw_dir is not None:
        for split, filename in RAW_FILES.items():
            path = raw_dir / filename
            if not path.exists():
                failures.append(f"raw_file_missing:{filename}")
                continue
            expected = raw_hashes[split]["sha256"]
            if sha256_file(path) != expected:
                failures.append(f"raw_file_hash_mismatch:{filename}")
        if rebuild_from_raw and not failures:
            with tempfile.TemporaryDirectory(prefix="stageeng0_rebuild_", dir=stage_dir.parent) as temp:
                temp_stage = Path(temp) / STAGE_NAME
                try:
                    rows_by_split, parquet_schemas = load_parquet_rows(raw_dir)
                    build_run(rows_by_split, temp_stage, raw_dir, parquet_schemas)
                    for name in [*SCIENTIFIC_ARTIFACTS, "DERIVED_ARTIFACT_MANIFEST.json"]:
                        if sha256_file(stage_dir / name) != sha256_file(temp_stage / name):
                            failures.append(f"raw_rebuild_artifact_mismatch:{name}")
                except Exception as exc:
                    failures.append(f"raw_rebuild_failed:{type(exc).__name__}:{exc}")

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "raw_total": raw_total,
        "dml_total": dml_total,
        "eligible_manifest_counts": {operation: len(rows) for operation, rows in manifests.items()},
        "primary_insert_development_train_candidates": len(development_candidates),
        "primary_insert_official_test_confirmation_candidates": len(confirmation_candidates),
        "ledger_status_counts": dict(status_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=Path(STAGE_NAME))
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--no-rebuild", action="store_true")
    args = parser.parse_args()
    result = validate(args.stage_dir, args.raw_dir, rebuild_from_raw=not args.no_rebuild)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
