#!/usr/bin/env python3
"""Validate Stage7B-A2 candidate-span reference amendment artifacts."""

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

from scripts.data.build_stage7b_a2_candidate_span_reference import (
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    EXPECTED_DEV_COUNT,
    EXPECTED_OFFICIAL_TEST_COUNT,
    EXPECTED_PILOT_COUNT,
    MIN_ASSIGNMENT_COVERAGE,
    MIN_FULL_SAMPLE_COVERAGE,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    build_stage,
    canonical_json,
    sha256_file,
    sha256_text,
)


REQUIRED_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7B_A2_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(stage_dir: Path, raw_dir: Path | None = None, *, rebuild: bool = False) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")
    if failures:
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}

    scope = read_json(stage_dir / "DESIGN_TRAIN_SCOPE_AUDIT.json")
    inventory = read_json(stage_dir / "SPAN_REFERENCE_INVENTORY_SPEC.json")
    algorithm = read_json(stage_dir / "CANDIDATE_GENERATION_ALGORITHM_SPEC.json")
    schema = read_json(stage_dir / "PHASE_O_SPAN_REFERENCE_SCHEMA.json")
    protocol = read_json(stage_dir / "PHASE_O_SPAN_REFERENCE_PROTOCOL.json")
    downstream = read_json(stage_dir / "DOWNSTREAM_DERIVATION_SPEC.json")
    conclusion = read_json(stage_dir / "A3_FEASIBILITY_CONCLUSION.json")
    coverage = read_json(stage_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.json")
    coverage_rows = read_jsonl(stage_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.jsonl")
    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    lock = read_json(stage_dir / "STAGE7B_A2_LOCK.json")

    if lock.get("status") != "PASS_CANDIDATE_SPAN_REFERENCE_ORACLE_COVERAGE":
        failures.append("lock_status_mismatch")
    for owner, payload in {
        "scope": scope,
        "inventory": inventory,
        "algorithm": algorithm,
        "protocol": protocol,
        "downstream": downstream,
        "coverage": coverage,
        "lock": lock,
    }.items():
        if payload.get("model_called") is not False or payload.get("gpu_called") is not False:
            failures.append(f"{owner}_model_or_gpu_not_false")

    if scope.get("design_train_non_pilot_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("design_train_non_pilot_count_mismatch")
    if scope.get("development_pilot_pool_count") != EXPECTED_PILOT_COUNT:
        failures.append("pilot_pool_count_mismatch")
    if scope.get("development_dev_count") != EXPECTED_DEV_COUNT:
        failures.append("development_dev_count_mismatch")
    if scope.get("official_test_confirmation_count") != EXPECTED_OFFICIAL_TEST_COUNT:
        failures.append("official_test_count_mismatch")
    for key in ["pilot_ids_in_design_train", "development_dev_ids_in_design_train", "official_test_ids_in_design_train"]:
        if scope.get(key):
            failures.append(f"scope_leakage:{key}")

    if inventory.get("model_does_not_generate") or "start_char" in inventory.get("runtime_visibility", {}).get("model_does_not_generate", []):
        pass
    else:
        failures.append("inventory_must_hide_offsets_from_model_generation")
    if algorithm.get("candidate_generation_reads_gold_at_runtime") is True:
        failures.append("algorithm_reads_gold_at_runtime")
    if "gold_sql" not in algorithm.get("forbidden_inputs", []):
        failures.append("algorithm_forbidden_inputs_missing_gold_sql")
    if protocol.get("model_generates_character_offsets") is not False:
        failures.append("protocol_still_generates_offsets")
    if protocol.get("pilot_usage_allowed") is not False:
        failures.append("protocol_allows_pilot_usage")
    if downstream.get("model_generated_offsets_removed") is not True:
        failures.append("downstream_offset_removal_not_locked")

    schema_props = schema.get("properties", {})
    if "span_refs" not in schema_props:
        failures.append("phase_o_schema_missing_span_refs")
    if "start_char" in schema_props or "end_char" in schema_props:
        failures.append("phase_o_schema_contains_numeric_offsets")
    if schema.get("required") != ["operation", "span_refs"]:
        failures.append("phase_o_schema_required_fields_mismatch")

    if conclusion.get("primary_gate_status") != "FAIL":
        failures.append("a3_conclusion_must_be_valid_primary_fail")
    if conclusion.get("gretel_pilot_opened") is not False:
        failures.append("a3_conclusion_must_keep_gretel_closed")

    if coverage.get("status") != "PASS":
        failures.append("coverage_status_not_pass")
    if coverage.get("design_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("coverage_design_sample_count_mismatch")
    if coverage.get("assignment_count") != EXPECTED_ASSIGNMENT_COUNT:
        failures.append("coverage_assignment_count_mismatch")
    if coverage.get("covered_assignment_count") != coverage.get("assignment_count"):
        failures.append("assignment_coverage_not_complete")
    if coverage.get("full_sample_covered_count") != coverage.get("design_sample_count"):
        failures.append("full_sample_coverage_not_complete")
    if float(coverage.get("assignment_candidate_coverage", 0.0)) < MIN_ASSIGNMENT_COVERAGE:
        failures.append("assignment_coverage_below_threshold")
    if float(coverage.get("full_sample_candidate_coverage", 0.0)) < MIN_FULL_SAMPLE_COVERAGE:
        failures.append("full_sample_coverage_below_threshold")
    if coverage.get("missing_assignments"):
        failures.append("coverage_reports_missing_assignments")
    if len(coverage_rows) != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("coverage_jsonl_sample_count_mismatch")
    for row in coverage_rows:
        if row.get("stageeng1_subset") != "development_train_non_pilot":
            failures.append(f"coverage_row_scope_mismatch:{row.get('sample_id')}")
            break
        if not row.get("all_assignments_covered"):
            failures.append(f"coverage_row_not_fully_covered:{row.get('sample_id')}")
            break

    manifest_by_path = {item["path"]: item for item in manifest.get("artifacts", [])}
    if manifest.get("artifact_count") != len(SCIENTIFIC_ARTIFACTS):
        failures.append("derived_artifact_count_mismatch")
    for name in SCIENTIFIC_ARTIFACTS:
        if name not in manifest_by_path:
            failures.append(f"derived_manifest_missing:{name}")
            continue
        if sha256_file(stage_dir / name) != manifest_by_path[name].get("sha256"):
            failures.append(f"derived_manifest_hash_mismatch:{name}")
    if manifest.get("combined_scientific_artifacts_sha256") != sha256_text(canonical_json(manifest.get("artifacts", []))):
        failures.append("combined_scientific_artifacts_hash_mismatch")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("lock_derived_manifest_hash_mismatch")

    if raw_dir is not None and rebuild and not failures:
        with tempfile.TemporaryDirectory(prefix="stage7b_a2_rebuild_", dir=stage_dir.parent) as temp:
            temp_stage = Path(temp) / STAGE_NAME
            try:
                build_stage(temp_stage, raw_dir)
                for name in [*SCIENTIFIC_ARTIFACTS, "DERIVED_ARTIFACT_MANIFEST.json"]:
                    if sha256_file(stage_dir / name) != sha256_file(temp_stage / name):
                        failures.append(f"raw_rebuild_artifact_mismatch:{name}")
            except Exception as exc:
                failures.append(f"raw_rebuild_failed:{type(exc).__name__}:{exc}")

    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "design_sample_count": coverage.get("design_sample_count"),
        "assignment_count": coverage.get("assignment_count"),
        "covered_assignment_count": coverage.get("covered_assignment_count"),
        "assignment_candidate_coverage": coverage.get("assignment_candidate_coverage"),
        "full_sample_candidate_coverage": coverage.get("full_sample_candidate_coverage"),
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    report = validate(args.stage_dir, args.raw_dir, rebuild=args.rebuild)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
