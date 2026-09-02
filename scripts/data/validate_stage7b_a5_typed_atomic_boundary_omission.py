#!/usr/bin/env python3
"""Validate Stage7B-A5 typed atomic-boundary and omission-construction audit."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
)
from scripts.data.build_stage7b_a5_typed_atomic_boundary_omission import (  # noqa: E402
    PATCH_NAME,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    a5_suppression_reasons,
    build_stage,
    canonical_json,
    detect_omission_constructions,
    generate_candidate_inventory,
    omittable_schema_aliases_from_inventory,
    schema_label_alias_index,
    sha256_file,
    sha256_text,
    typed_complete_literal_reason,
)


REQUIRED_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7B_A5_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _candidate_for_text_in_region(inventory: list[Any], question: str, text: str, region: str) -> Any:
    region_start = question.casefold().index(region.casefold())
    region_end = region_start + len(region)
    return next(
        candidate
        for candidate in inventory
        if candidate.text == text and region_start <= candidate.start_char and candidate.end_char <= region_end
    )


def _check_false_model_gpu(payloads: dict[str, dict[str, Any]], failures: list[str]) -> None:
    for owner, payload in payloads.items():
        if payload.get("model_called") is not False or payload.get("gpu_called") is not False:
            failures.append(f"{owner}_model_or_gpu_not_false")


def validate(stage_dir: Path, raw_dir: Path | None = None, *, rebuild: bool = False) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")
    if failures:
        return {"stage": STAGE_NAME, "patch": PATCH_NAME, "status": "FAIL", "failures": failures}

    source = read_json(stage_dir / "SOURCE_INPUT_MANIFEST.json")
    a6_freeze = read_json(stage_dir / "A6_VALID_FEASIBILITY_FAIL_FREEZE.json")
    protocol = read_json(stage_dir / "METHOD_AUDIT_PROTOCOL.json")
    typed = read_json(stage_dir / "TYPED_ATOMICITY_RULE_SPEC.json")
    omit_admissibility = read_json(stage_dir / "OMIT_ADMISSIBILITY_RULE_SPEC.json")
    omission = read_json(stage_dir / "OMISSION_CONSTRUCTION_SUPPRESSION_RULE_SPEC.json")
    boundary = read_json(stage_dir / "QUOTE_BOUNDARY_RULE_SPEC.json")
    baseline = read_json(stage_dir / "DESIGN_TRAIN_BASELINE_A4_DOMAIN_AUDIT.json")
    a5 = read_json(stage_dir / "DESIGN_TRAIN_STAGE7B_A5_DOMAIN_AUDIT.json")
    false = read_json(stage_dir / "FALSE_SUPPRESSION_AUDIT.json")
    a6 = read_json(stage_dir / "A6_OBSERVED_ERROR_COUNTERFACTUAL_AUDIT.json")
    synthetic = read_json(stage_dir / "SYNTHETIC_TYPED_OMISSION_BOUNDARY_SAFETY_AUDIT.json")
    rows = read_jsonl(stage_dir / "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl")
    examples = read_jsonl(stage_dir / "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl")
    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    lock = read_json(stage_dir / "STAGE7B_A5_LOCK.json")

    _check_false_model_gpu(
        {
            "source": source,
            "a6_freeze": a6_freeze,
            "protocol": protocol,
            "typed": typed,
            "omit_admissibility": omit_admissibility,
            "omission": omission,
            "boundary": boundary,
            "baseline": baseline,
            "a5": a5,
            "false": false,
            "a6": a6,
            "synthetic": synthetic,
            "lock": lock,
        },
        failures,
    )

    if lock.get("stage") != STAGE_NAME or lock.get("patch") != PATCH_NAME:
        failures.append("lock_stage_or_patch_mismatch")
    if lock.get("status") != "PASS_READY_FOR_REVIEW":
        failures.append("lock_status_not_pass")
    if lock.get("method_freeze_authorized") is not False or lock.get("a6_rerun_authorized") is not False:
        failures.append("lock_authorizes_forbidden_next_step")
    if lock.get("gretel_pilot_opened") is not False:
        failures.append("gretel_pilot_opened")

    if a6_freeze.get("source_status") != "VALID_FEASIBILITY_FAIL_CLOSED":
        failures.append("a6_freeze_status_mismatch")
    if a6_freeze.get("primary_pass_count") != "2/12" or a6_freeze.get("required_pass_count") != "12/12":
        failures.append("a6_freeze_primary_count_mismatch")
    if a6_freeze.get("diagnostics_run") is not False or a6_freeze.get("gretel_pilot_opened") is not False:
        failures.append("a6_downstream_gate_mismatch")
    if a6_freeze.get("a6_failures_used_as") != "development_diagnostic_not_independent_evaluation":
        failures.append("a6_diagnostic_scope_mismatch")

    if protocol.get("design_train_non_pilot_rows") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("protocol_design_count_mismatch")
    if protocol.get("assignment_count") != EXPECTED_ASSIGNMENT_COUNT:
        failures.append("protocol_assignment_count_mismatch")
    if "A6 primary rerun" not in protocol.get("forbidden", []):
        failures.append("protocol_does_not_forbid_a6_rerun")
    if typed.get("rule_name") != "typed_complete_literal_dominates_numeric_child":
        failures.append("typed_rule_name_mismatch")
    if "abc" not in typed.get("forbidden_suffix_policy", ""):
        failures.append("typed_rule_does_not_forbid_generic_alpha_suffix")
    if omit_admissibility.get("rule_name") != "schema_semantic_omit_admissibility":
        failures.append("omit_admissibility_rule_name_mismatch")
    if "has_default" not in omit_admissibility.get("omit_admissible_when_any", []):
        failures.append("omit_admissibility_spec_incomplete")
    if omission.get("rule_name") != "full_omission_construction_region_suppression":
        failures.append("omission_rule_name_mismatch")
    if len(boundary.get("rules", [])) < 4:
        failures.append("boundary_rule_spec_incomplete")

    for owner, payload in {"baseline": baseline, "a5": a5}.items():
        if payload.get("design_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
            failures.append(f"{owner}_design_sample_count_mismatch")
        if payload.get("assignment_count") != EXPECTED_ASSIGNMENT_COUNT:
            failures.append(f"{owner}_assignment_count_mismatch")
    if baseline.get("covered_assignment_count") != 2252 or baseline.get("full_sample_covered_count") != 724:
        failures.append("baseline_a4_counts_mismatch")
    if a5.get("covered_assignment_count") != baseline.get("covered_assignment_count"):
        failures.append("a5_assignment_coverage_changed")
    if a5.get("full_sample_covered_count") != baseline.get("full_sample_covered_count"):
        failures.append("a5_full_sample_coverage_changed")
    if false.get("status") != "PASS":
        failures.append("false_suppression_status_not_pass")
    if false.get("additional_assignment_losses") != 0:
        failures.append("additional_assignment_losses_nonzero")
    if false.get("additional_full_sample_losses") != 0:
        failures.append("additional_full_sample_losses_nonzero")
    for rule in [
        "BOUNDARY_UNBALANCED_DOUBLE_QUOTE",
        "BOUNDARY_TRAILING_PUNCTUATION_HAS_STRIPPED_CANDIDATE",
    ]:
        if a5.get("suppression_rule_counts", {}).get(rule, 0) <= 0:
            failures.append(f"a5_rule_not_observed:{rule}")

    if a6.get("status") != "PASS":
        failures.append("a6_counterfactual_status_not_pass")
    if a6.get("audit_type") != "development_diagnostic_not_independent_evaluation":
        failures.append("a6_counterfactual_scope_mismatch")
    if a6.get("case_exact_pass_count") != "2/12":
        failures.append("a6_counterfactual_case_count_mismatch")
    if a6.get("wrong_decision_count") != 15:
        failures.append("a6_wrong_decision_count_mismatch")
    if a6.get("stage7b_a5_correct_gold_suppressed") != 0:
        failures.append("a6_correct_gold_suppressed")
    if a6.get("stage7b_a5_wrong_span_choices_suppressed") != 14:
        failures.append("a6_wrong_span_choices_suppressed_mismatch")
    if a6.get("stage7b_a5_wrong_required_omit_structurally_impossible") != 1:
        failures.append("a6_required_omit_structural_count_mismatch")
    if a6.get("stage7b_a5_observed_wrong_decisions_addressed") != 15:
        failures.append("a6_observed_wrong_decisions_addressed_mismatch")
    for family in ["typed_complete_literal_numeric_child", "omission_construction_candidate_leak", "quote_punctuation_boundary_quality", "false_omit_required_value"]:
        if family not in a6.get("error_family_counts", {}):
            failures.append(f"a6_missing_error_family:{family}")

    if synthetic.get("status") != "PASS":
        failures.append("synthetic_safety_not_pass")
    if len(rows) != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("audit_rows_count_mismatch")
    if not examples:
        failures.append("suppression_examples_missing")

    helper_question = 'Insert hydration_pct 68%, required status absent, required status missing, and optional memo absent.'
    inventory = generate_candidate_inventory(helper_question)
    aliases = schema_label_alias_index({"hydration pct", "status", "memo"})
    schema_inventory = {
        "columns": [
            {"column_name": "hydration_pct", "nullable": False, "has_default": False},
            {"column_name": "status", "nullable": False, "has_default": False},
            {"column_name": "memo", "nullable": True, "has_default": False},
        ]
    }
    omittable_aliases = omittable_schema_aliases_from_inventory(schema_inventory)
    detections = detect_omission_constructions(
        helper_question,
        omittable_aliases,
    )
    reasons = a5_suppression_reasons(inventory, aliases, detections, include_a4=False)
    pct_child = _candidate_for_text_in_region(inventory, helper_question, "68", "hydration_pct 68%")
    if typed_complete_literal_reason(pct_child, inventory) is None:
        failures.append("typed_helper_does_not_find_percent_parent")
    if pct_child.span_ref not in reasons:
        failures.append("a5_helper_does_not_suppress_percent_child")
    required_status_absent = _candidate_for_text_in_region(inventory, helper_question, "status absent", "required status absent")
    required_absent = _candidate_for_text_in_region(inventory, helper_question, "absent", "required status absent")
    required_status_missing = _candidate_for_text_in_region(inventory, helper_question, "status missing", "required status missing")
    required_missing = _candidate_for_text_in_region(inventory, helper_question, "missing", "required status missing")
    optional_memo_absent = _candidate_for_text_in_region(inventory, helper_question, "memo absent", "optional memo absent")
    if required_status_absent.span_ref in reasons or required_absent.span_ref in reasons:
        failures.append("a5_helper_suppresses_required_absent_literal")
    if required_status_missing.span_ref in reasons or required_missing.span_ref in reasons:
        failures.append("a5_helper_suppresses_required_missing_literal")
    if optional_memo_absent.span_ref not in reasons:
        failures.append("a5_helper_does_not_suppress_optional_memo_absent")

    unit_question = "Insert hydration_pct 68kg, completion_percentage 68abc, weight_kg 68kg, and duration_ms 25ms."
    unit_inventory = generate_candidate_inventory(unit_question)
    unit_reasons = a5_suppression_reasons(unit_inventory, schema_label_alias_index({"hydration pct", "completion percentage", "weight kg", "duration ms"}), [], include_a4=False)
    for text, region in [("68", "68kg"), ("68kg", "68kg"), ("68abc", "68abc"), ("25", "25ms"), ("25ms", "25ms")]:
        try:
            candidate = _candidate_for_text_in_region(unit_inventory, unit_question, text, region)
        except StopIteration:
            failures.append(f"unit_fixture_missing:{text}")
            continue
        if candidate.span_ref in unit_reasons:
            failures.append(f"unit_fixture_incorrectly_suppressed:{text}")

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

    for item in source.get("source_files", []):
        source_path = PROJECT_ROOT / str(item["path"])
        if not source_path.is_file():
            failures.append(f"source_manifest_missing:{item['path']}")
            continue
        if source_path.stat().st_size != item.get("bytes"):
            failures.append(f"source_manifest_size_mismatch:{item['path']}")
        if sha256_file(source_path) != item.get("sha256"):
            failures.append(f"source_manifest_hash_mismatch:{item['path']}")

    if raw_dir is not None and rebuild and not failures:
        temp_path = stage_dir.parent / "_stage7b_a5_rebuild_validation_tmp"
        shutil.rmtree(temp_path, ignore_errors=True)
        temp_stage = temp_path / STAGE_NAME
        try:
            build_stage(temp_stage, raw_dir)
            for name in [*SCIENTIFIC_ARTIFACTS, "DERIVED_ARTIFACT_MANIFEST.json"]:
                if sha256_file(stage_dir / name) != sha256_file(temp_stage / name):
                    failures.append(f"raw_rebuild_artifact_mismatch:{name}")
        except Exception as exc:
            failures.append(f"raw_rebuild_failed:{type(exc).__name__}:{exc}")
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)

    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "design_sample_count": baseline.get("design_sample_count"),
        "assignment_count": baseline.get("assignment_count"),
        "baseline_a4_assignment_representability": baseline.get("assignment_representability"),
        "stage7b_a5_assignment_representability": a5.get("assignment_representability"),
        "additional_assignment_losses": false.get("additional_assignment_losses"),
        "additional_full_sample_losses": false.get("additional_full_sample_losses"),
        "a6_case_exact_pass_count": a6.get("case_exact_pass_count"),
        "a6_wrong_decisions": a6.get("wrong_decision_count"),
        "a6_wrong_span_choices_suppressed_by_a5": a6.get("stage7b_a5_wrong_span_choices_suppressed"),
        "a6_wrong_required_omit_structurally_impossible": a6.get("stage7b_a5_wrong_required_omit_structurally_impossible"),
        "a6_observed_wrong_decisions_addressed": a6.get("stage7b_a5_observed_wrong_decisions_addressed"),
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
