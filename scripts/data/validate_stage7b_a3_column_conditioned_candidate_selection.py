#!/usr/bin/env python3
"""Validate Stage7B-A3 column-conditioned candidate-selection artifacts."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    EXPECTED_DEV_COUNT,
    EXPECTED_OFFICIAL_TEST_COUNT,
    EXPECTED_PILOT_COUNT,
    SELECTED_VARIANT,
)
from scripts.data.build_stage7b_a3_column_conditioned_candidate_selection import (  # noqa: E402
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
    "STAGE7B_A3_LOCK.json",
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

    source = read_json(stage_dir / "SOURCE_INPUT_MANIFEST.json")
    freeze = read_json(stage_dir / "A4_VALID_FAIL_FREEZE.json")
    root_cause = read_json(stage_dir / "A4_ROOT_CAUSE_CLASSIFICATION.json")
    representation = read_json(stage_dir / "COLUMN_CONDITIONED_REPRESENTATION_SPEC.json")
    schema = read_json(stage_dir / "COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json")
    omit = read_json(stage_dir / "OMIT_POLICY_AND_COLUMN_SCOPE_SPEC.json")
    schema_audit = read_json(stage_dir / "DESIGN_TRAIN_COLUMN_SCHEMA_AUDIT.json")
    representability = read_json(stage_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.json")
    rows = read_jsonl(stage_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.jsonl")
    type_audit = read_json(stage_dir / "TYPE_COMPATIBLE_CANDIDATE_AUDIT.json")
    comparison = read_json(stage_dir / "REPRESENTATION_COMPARISON_AUDIT.json")
    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    lock = read_json(stage_dir / "STAGE7B_A3_LOCK.json")

    for owner, payload in {
        "source": source,
        "freeze": freeze,
        "root_cause": root_cause,
        "representation": representation,
        "schema": schema,
        "omit": omit,
        "schema_audit": schema_audit,
        "representability": representability,
        "type_audit": type_audit,
        "comparison": comparison,
        "lock": lock,
    }.items():
        if payload.get("model_called") is not False or payload.get("gpu_called") is not False:
            failures.append(f"{owner}_model_or_gpu_not_false")

    if lock.get("status") != "PASS_COLUMN_CONDITIONED_ORACLE_REPRESENTABILITY_AUDIT":
        failures.append("lock_status_mismatch")
    if freeze.get("status") != "STAGE7E0_A4_VALID_FEASIBILITY_FAIL_CLOSED":
        failures.append("a4_freeze_status_mismatch")
    if freeze.get("primary_pass_count") != "6/10" or freeze.get("required_pass_count") != "10/10":
        failures.append("a4_primary_count_mismatch")
    if freeze.get("primary_gate_status") != "FAIL":
        failures.append("a4_primary_gate_not_fail")
    if freeze.get("scientific_result_eligible") is not True:
        failures.append("a4_scientific_result_not_eligible")
    if freeze.get("gretel_pilot_opened") is not False:
        failures.append("a4_gretel_pilot_opened")
    if freeze.get("no_a4_rerun_allowed") is not True:
        failures.append("a4_rerun_not_forbidden")

    root_counts = root_cause.get("root_cause_counts", {})
    if root_counts.get("phase_o_severe_under_selection") != 3:
        failures.append("root_cause_under_selection_count_mismatch")
    if root_counts.get("phase_o_non_atomic_broader_span_selection") != 1:
        failures.append("root_cause_non_atomic_count_mismatch")
    if root_counts.get("phase_m_primary_root_cause") != 0:
        failures.append("phase_m_should_not_be_primary_root_cause")
    if root_counts.get("compiler_or_materializer_bug") != 0:
        failures.append("compiler_materializer_bug_count_mismatch")

    if representation.get("free_length_span_set_removed") is not True:
        failures.append("representation_does_not_remove_free_span_set")
    output = representation.get("phase_o_output", {})
    if "column_span_refs" not in output or "span_refs" in output:
        failures.append("representation_output_shape_mismatch")
    if schema.get("required_top_level_keys") != ["operation", "table_ref", "column_span_refs"]:
        failures.append("schema_required_top_level_mismatch")
    forbidden = set(schema.get("forbidden_top_level_keys", []))
    if "span_refs" not in forbidden or "start_char" not in forbidden or "end_char" not in forbidden:
        failures.append("schema_forbidden_keys_incomplete")
    if schema.get("early_array_stop_structurally_impossible") is not True:
        failures.append("schema_does_not_block_early_stop")
    if omit.get("runtime_gold_blind") is not True:
        failures.append("omit_policy_not_gold_blind")

    if schema_audit.get("status") != "PASS":
        failures.append("schema_audit_status_not_pass")
    if schema_audit.get("design_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("schema_audit_design_count_mismatch")
    if schema_audit.get("parsed_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("schema_audit_parsed_count_mismatch")
    if schema_audit.get("parse_failure_count") != 0:
        failures.append("schema_parse_failures_present")
    if schema_audit.get("assigned_column_decision_count") != EXPECTED_ASSIGNMENT_COUNT:
        failures.append("assigned_column_decision_count_mismatch")
    if schema_audit.get("target_table_column_decision_count", 0) < EXPECTED_ASSIGNMENT_COUNT:
        failures.append("target_column_decision_count_too_small")

    if representability.get("status") != "PASS":
        failures.append("representability_status_not_pass")
    if representability.get("candidate_generator_variant") != SELECTED_VARIANT:
        failures.append("representability_variant_mismatch")
    if representability.get("design_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("representability_design_count_mismatch")
    if representability.get("assignment_count") != EXPECTED_ASSIGNMENT_COUNT:
        failures.append("representability_assignment_count_mismatch")
    if int(representability.get("covered_assignment_count", 0)) < math.ceil(0.99 * EXPECTED_ASSIGNMENT_COUNT):
        failures.append("assignment_coverage_below_stage7b_a2_floor")
    if float(representability.get("assignment_candidate_coverage", 0.0)) < 0.99:
        failures.append("assignment_coverage_below_threshold")
    if len(rows) != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("representability_jsonl_count_mismatch")
    row_full = sum(1 for row in rows if row.get("all_gold_assignments_representable") is True)
    if row_full != representability.get("full_sample_covered_count"):
        failures.append("representability_jsonl_full_count_mismatch")
    for row in rows:
        decisions = row.get("oracle_phase_o_output", {}).get("column_span_refs", {})
        required = row.get("column_conditioned_schema_required_columns", [])
        if sorted(decisions) != sorted(required):
            failures.append(f"row_required_column_decision_mismatch:{row.get('sample_id')}")
            break
        if row.get("dynamic_domain_size_per_column") != row.get("candidate_count", 0) + 1:
            failures.append(f"row_dynamic_domain_size_mismatch:{row.get('sample_id')}")
            break

    if type_audit.get("status") != "PASS":
        failures.append("type_audit_status_not_pass")
    if type_audit.get("gold_assignment_type_compatible_count", 0) > representability.get("covered_assignment_count", 0):
        failures.append("type_compatible_count_exceeds_covered_assignments")
    if comparison.get("current_free_span_set", {}).get("early_stop_after_one_value_schema_valid") is not True:
        failures.append("comparison_free_span_early_stop_not_recorded")
    if comparison.get("column_conditioned_selection", {}).get("early_stop_after_one_value_schema_valid") is not False:
        failures.append("comparison_column_conditioned_early_stop_not_blocked")
    if comparison.get("pilot_usage_allowed") is not False:
        failures.append("comparison_allows_pilot")

    if source.get("source_files"):
        for item in source["source_files"]:
            source_path = PROJECT_ROOT / str(item["path"])
            if not source_path.is_file():
                failures.append(f"source_manifest_missing:{item['path']}")
                continue
            if source_path.stat().st_size != item.get("bytes"):
                failures.append(f"source_manifest_size_mismatch:{item['path']}")
            if sha256_file(source_path) != item.get("sha256"):
                failures.append(f"source_manifest_hash_mismatch:{item['path']}")

    manifest_by_path = {item["path"]: item for item in manifest.get("artifacts", [])}
    if manifest.get("artifact_count") != len(SCIENTIFIC_ARTIFACTS):
        failures.append("derived_manifest_artifact_count_mismatch")
    for name in SCIENTIFIC_ARTIFACTS:
        if name not in manifest_by_path:
            failures.append(f"derived_manifest_missing:{name}")
            continue
        if sha256_file(stage_dir / name) != manifest_by_path[name].get("sha256"):
            failures.append(f"derived_manifest_hash_mismatch:{name}")
    if manifest.get("combined_scientific_artifacts_sha256") != sha256_text(canonical_json(manifest.get("artifacts", []))):
        failures.append("derived_manifest_combined_hash_mismatch")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("lock_derived_manifest_hash_mismatch")
    if lock.get("development_pilot_pool_opened") is not False or lock.get("development_dev_used") is not False or lock.get("official_test_used") is not False:
        failures.append("lock_reserved_rows_used")

    if raw_dir is not None and rebuild and not failures:
        with tempfile.TemporaryDirectory(prefix="stage7b_a3_rebuild_", dir=stage_dir.parent) as temp:
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
        "design_sample_count": representability.get("design_sample_count"),
        "assignment_count": representability.get("assignment_count"),
        "covered_assignment_count": representability.get("covered_assignment_count"),
        "assignment_candidate_coverage": representability.get("assignment_candidate_coverage"),
        "full_sample_candidate_coverage": representability.get("full_sample_candidate_coverage"),
        "target_table_column_decision_count": schema_audit.get("target_table_column_decision_count"),
        "omit_decision_count": schema_audit.get("omit_decision_count"),
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
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
