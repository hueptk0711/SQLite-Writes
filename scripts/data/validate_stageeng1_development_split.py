#!/usr/bin/env python3
"""Validate StageENG1 Gretel English INSERT development split artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng1_development_split import (
    EXPECTED_STAGE0_CONFIRMATION_COUNT,
    EXPECTED_STAGE0_DEVELOPMENT_COUNT,
    PILOT_TARGET,
    SCIENTIFIC_ARTIFACTS,
    SIGNATURE_FIELDS,
    STAGE0_INPUT_FILES,
    STAGE0_NAME,
    STAGE_NAME,
    build_run,
    canonical_json,
    sha256_file,
    sha256_text,
)


REQUIRED_STAGE1_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGEENG1_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def validate(
    stage1_dir: Path,
    stage0_dir: Path,
    raw_dir: Path | None = None,
    *,
    rebuild: bool = True,
    strict_counts: bool = True,
) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_STAGE1_FILES:
        if not (stage1_dir / name).is_file():
            failures.append(f"missing_required_stage1_file:{name}")
    for name in STAGE0_INPUT_FILES:
        if not (stage0_dir / name).is_file():
            failures.append(f"missing_required_stage0_input:{name}")
    if failures:
        return {"status": "FAIL", "failures": failures}

    policy = read_json(stage1_dir / "DEVELOPMENT_SPLIT_POLICY.json")
    input_hashes = read_json(stage1_dir / "STAGE0_INPUT_HASHES.json")
    train_rows = read_jsonl(stage1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    dev_rows = read_jsonl(stage1_dir / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")
    pilot_rows = read_jsonl(stage1_dir / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")
    duplicate_audit = read_json(stage1_dir / "DUPLICATE_AUDIT.json")
    official_audit = read_json(stage1_dir / "OFFICIAL_TEST_ISOLATION_AUDIT.json")
    group_audit = read_json(stage1_dir / "SPLIT_GROUP_AUDIT.json")
    summary = read_json(stage1_dir / "STAGEENG1_SPLIT_SUMMARY.json")
    derived_manifest = read_json(stage1_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    lock = read_json(stage1_dir / "STAGEENG1_LOCK.json")
    stage0_lock = read_json(stage0_dir / "STAGEENG0_LOCK.json")
    stage0_dev = read_jsonl(stage0_dir / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl")
    stage0_confirmation = read_jsonl(stage0_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")

    if policy.get("stage") != STAGE_NAME or policy.get("source_stage") != STAGE0_NAME:
        failures.append("policy_stage_mismatch")
    if policy.get("model_called") is not False or policy.get("gpu_called") is not False:
        failures.append("policy_allows_model_or_gpu")
    if lock.get("model_called") is not False or lock.get("gpu_called") is not False:
        failures.append("lock_model_or_gpu_not_false")
    if stage0_lock.get("model_called") is not False or stage0_lock.get("gpu_called") is not False:
        failures.append("stage0_lock_model_or_gpu_not_false")
    expected_pilot_target = int(policy.get("split", {}).get("development_dev_target_count", PILOT_TARGET))
    if strict_counts and expected_pilot_target != PILOT_TARGET:
        failures.append("policy_pilot_target_mismatch")
    if policy.get("official_test_policy", {}).get("included_in_stageeng1_split") is not False:
        failures.append("policy_includes_official_test")
    if policy.get("leakage_component_signatures") != SIGNATURE_FIELDS:
        failures.append("policy_signature_fields_mismatch")

    manifest_by_path = {row["path"]: row for row in derived_manifest.get("artifacts", [])}
    if derived_manifest.get("artifact_count") != len(SCIENTIFIC_ARTIFACTS):
        failures.append("derived_artifact_count_mismatch")
    for name in SCIENTIFIC_ARTIFACTS:
        path = stage1_dir / name
        if name not in manifest_by_path:
            failures.append(f"derived_manifest_missing_artifact:{name}")
            continue
        if sha256_file(path) != manifest_by_path[name].get("sha256"):
            failures.append(f"derived_artifact_hash_mismatch:{name}")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage1_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("lock_derived_manifest_hash_mismatch")
    if derived_manifest.get("combined_scientific_artifacts_sha256") != sha256_text(
        canonical_json(derived_manifest.get("artifacts", []))
    ):
        failures.append("combined_scientific_artifacts_hash_mismatch")

    stage0_input_files = input_hashes.get("input_files", {})
    for name in STAGE0_INPUT_FILES:
        if name not in stage0_input_files:
            failures.append(f"stage0_input_hash_missing:{name}")
            continue
        if sha256_file(stage0_dir / name) != stage0_input_files[name].get("sha256"):
            failures.append(f"stage0_input_hash_mismatch:{name}")

    if strict_counts and len(stage0_dev) != EXPECTED_STAGE0_DEVELOPMENT_COUNT:
        failures.append("stage0_development_candidate_count_mismatch")
    if strict_counts and len(stage0_confirmation) != EXPECTED_STAGE0_CONFIRMATION_COUNT:
        failures.append("stage0_confirmation_count_mismatch")
    for row in stage0_dev:
        if row.get("source_split") != "train" or row.get("development_allowed") is not True:
            failures.append(f"stage0_development_row_not_train_allowed:{row.get('sample_id')}")
        if row.get("official_test_confirmation_only") is not False:
            failures.append(f"stage0_development_row_marked_confirmation:{row.get('sample_id')}")
        if row.get("operation") != "INSERT" or row.get("complexity_class") != "single_row_insert":
            failures.append(f"stage0_development_row_not_primary_insert_scope:{row.get('sample_id')}")
        if row.get("v2_literal_grounded_primary_eligible") is not True:
            failures.append(f"stage0_development_row_not_primary:{row.get('sample_id')}")

    train_ids = {str(row["sample_id"]) for row in train_rows}
    dev_ids = {str(row["sample_id"]) for row in dev_rows}
    pilot_ids = {str(row["sample_id"]) for row in pilot_rows}
    stage0_dev_ids = {str(row["sample_id"]) for row in stage0_dev}
    confirmation_ids = {str(row["sample_id"]) for row in stage0_confirmation}
    if train_ids & dev_ids:
        failures.append("development_train_intersects_development_dev")
    if train_ids | dev_ids != stage0_dev_ids:
        failures.append("stageeng1_split_ids_do_not_match_stage0_development_candidates")
    if dev_ids != pilot_ids:
        failures.append("pilot_pool_ids_do_not_match_development_dev")
    if confirmation_ids & (train_ids | dev_ids | pilot_ids):
        failures.append("official_test_confirmation_ids_in_stageeng1_split")
    if len(dev_rows) != expected_pilot_target or len(pilot_rows) != expected_pilot_target:
        failures.append("development_dev_or_pilot_count_mismatch")
    if strict_counts and len(train_rows) != EXPECTED_STAGE0_DEVELOPMENT_COUNT - expected_pilot_target:
        failures.append("development_train_count_mismatch")
    if not strict_counts and len(train_rows) != len(stage0_dev) - expected_pilot_target:
        failures.append("development_train_count_mismatch")

    for row in [*train_rows, *dev_rows, *pilot_rows]:
        expected_split = "development_dev" if row["sample_id"] in dev_ids else "development_train"
        if row.get("source_split") != "train" or row.get("development_allowed") is not True:
            failures.append(f"split_row_not_train_allowed:{row.get('sample_id')}")
        if row.get("official_test_confirmation_only") is not False:
            failures.append(f"split_row_marked_confirmation:{row.get('sample_id')}")
        if row.get("operation") != "INSERT" or row.get("v2_literal_grounded_primary_eligible") is not True:
            failures.append(f"split_row_not_primary_insert:{row.get('sample_id')}")
        if row.get("stageeng1_split") != expected_split:
            failures.append(f"split_row_label_mismatch:{row.get('sample_id')}")

    group_to_splits: dict[str, set[str]] = {}
    signature_to_splits: dict[str, set[str]] = {}
    for row in [*train_rows, *dev_rows]:
        group_to_splits.setdefault(str(row["split_group_id"]), set()).add(str(row["stageeng1_split"]))
        for field in SIGNATURE_FIELDS:
            value = row.get(field)
            if value in (None, ""):
                continue
            signature_to_splits.setdefault(f"{field}:{value}", set()).add(str(row["stageeng1_split"]))
    leaking_groups = [group for group, splits in group_to_splits.items() if len(splits) > 1]
    leaking_signatures = [signature for signature, splits in signature_to_splits.items() if len(splits) > 1]
    if leaking_groups:
        failures.append(f"split_group_cross_split_violation:{len(leaking_groups)}")
    if leaking_signatures:
        failures.append(f"signature_cross_split_violation:{len(leaking_signatures)}")
    if duplicate_audit.get("cross_split_signature_violations"):
        failures.append("duplicate_audit_reports_cross_split_violations")
    if group_audit.get("development_dev_count") != len(dev_rows):
        failures.append("group_audit_development_dev_count_mismatch")
    if group_audit.get("development_train_count") != len(train_rows):
        failures.append("group_audit_development_train_count_mismatch")
    if official_audit.get("official_test_confirmation_only_ids_in_stageeng1_split"):
        failures.append("official_audit_reports_confirmation_ids_in_split")
    if summary.get("development_train_count") != len(train_rows):
        failures.append("summary_development_train_count_mismatch")
    if summary.get("development_dev_count") != len(dev_rows):
        failures.append("summary_development_dev_count_mismatch")
    if summary.get("cross_split_signature_violation_count") != 0:
        failures.append("summary_reports_cross_split_signature_violations")

    if raw_dir is not None and rebuild and not failures:
        with tempfile.TemporaryDirectory(prefix="stageeng1_rebuild_", dir=stage1_dir.parent) as temp:
            temp_stage1 = Path(temp) / STAGE_NAME
            try:
                build_run(stage0_dir, temp_stage1, pilot_target=expected_pilot_target, raw_dir=raw_dir)
                for name in [*SCIENTIFIC_ARTIFACTS, "DERIVED_ARTIFACT_MANIFEST.json"]:
                    if sha256_file(stage1_dir / name) != sha256_file(temp_stage1 / name):
                        failures.append(f"raw_rebuild_artifact_mismatch:{name}")
            except Exception as exc:
                failures.append(f"raw_rebuild_failed:{type(exc).__name__}:{exc}")

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "stage0_development_candidate_count": len(stage0_dev),
        "stage0_official_confirmation_count": len(stage0_confirmation),
        "development_train_count": len(train_rows),
        "development_dev_count": len(dev_rows),
        "development_pilot_pool_count": len(pilot_rows),
        "split_group_count": group_audit.get("component_count"),
        "cross_split_signature_violation_count": len(
            duplicate_audit.get("cross_split_signature_violations", [])
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage1-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--stage0-dir", type=Path, default=PROJECT_ROOT / STAGE0_NAME)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--no-rebuild", action="store_true")
    args = parser.parse_args()
    result = validate(args.stage1_dir, args.stage0_dir, args.raw_dir, rebuild=not args.no_rebuild)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
