#!/usr/bin/env python3
"""Validate Stage7B-A4 PATCH1 schema-label-aware candidate-domain audit."""

from __future__ import annotations

import argparse
import json
import math
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
    MIN_ASSIGNMENT_COVERAGE,
    MIN_FULL_SAMPLE_COVERAGE,
    SELECTED_VARIANT,
)
from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import (  # noqa: E402
    OMISSION_CUE_PHRASES,
    PATCH_NAME,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    STRONG_ATOMIC_TAGS,
    build_stage,
    canonical_json,
    detect_omission_constructions,
    generate_candidate_inventory,
    generic_atomic_dominance_reason,
    is_exact_omission_cue,
    schema_label_aware_dominance_reason,
    sha256_file,
    sha256_text,
    suppressible_span_refs,
)


REQUIRED_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7B_A4_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}

    source = read_json(stage_dir / "SOURCE_INPUT_MANIFEST.json")
    a5 = read_json(stage_dir / "A5_CORRECTED_VALID_FAIL_FREEZE.json")
    protocol = read_json(stage_dir / "DOMAIN_AUDIT_PROTOCOL.json")
    atomic = read_json(stage_dir / "ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json")
    omission = read_json(stage_dir / "OMISSION_CUE_SUPPRESSION_RULE_SPEC.json")
    current = read_json(stage_dir / "CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json")
    patch0 = read_json(stage_dir / "PATCH0_GENERIC_ATOMIC_DOMAIN_AUDIT.json")
    schema_aware = read_json(stage_dir / "SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json")
    filtered_alias = read_json(stage_dir / "ATOMIC_FILTERED_DOMAIN_AUDIT.json")
    comparison = read_json(stage_dir / "DOMAIN_COMPARISON_AUDIT.json")
    false_suppression = read_json(stage_dir / "FALSE_SUPPRESSION_AUDIT.json")
    cue = read_json(stage_dir / "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json")
    synthetic = read_json(stage_dir / "SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json")
    rows = read_jsonl(stage_dir / "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl")
    examples = read_jsonl(stage_dir / "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl")
    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    lock = read_json(stage_dir / "STAGE7B_A4_LOCK.json")

    _check_false_model_gpu(
        {
            "source": source,
            "a5": a5,
            "protocol": protocol,
            "atomic": atomic,
            "omission": omission,
            "current": current,
            "patch0": patch0,
            "schema_aware": schema_aware,
            "comparison": comparison,
            "false_suppression": false_suppression,
            "cue": cue,
            "synthetic": synthetic,
            "lock": lock,
        },
        failures,
    )

    if PATCH_NAME != "PATCH1" or lock.get("patch") != "PATCH1":
        failures.append("patch_name_not_patch1")
    if a5.get("status") != "STAGE7E0_A5_VALID_FEASIBILITY_FAIL_CLOSED":
        failures.append("a5_closed_status_mismatch")
    if a5.get("corrected_primary_pass_count") != "2/12":
        failures.append("a5_corrected_count_mismatch")
    if a5.get("old_classification_status") != "SUPERSEDED_BY_GOLD_PROVENANCE_ERRATUM":
        failures.append("a5_old_classification_not_superseded")
    if a5.get("gretel_pilot_opened") is not False or a5.get("diagnostics_run") is not False:
        failures.append("a5_downstream_gate_mismatch")

    if protocol.get("baseline_candidate_generator") != SELECTED_VARIANT:
        failures.append("protocol_baseline_variant_mismatch")
    if protocol.get("design_sample_count_required") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("protocol_design_count_mismatch")
    if atomic.get("rule_name") != "schema_label_aware_atomic_dominated_broad_span_suppression_audit":
        failures.append("atomic_rule_name_mismatch")
    if atomic.get("uses_model_visible_schema") is not True or atomic.get("gold_blind") is not True:
        failures.append("atomic_rule_not_schema_label_gold_blind")
    if omission.get("cue_phrases") != list(OMISSION_CUE_PHRASES):
        failures.append("omission_rule_spec_mismatch")
    if "schema-label" not in omission.get("candidate_is_suppressible_when", ""):
        failures.append("omission_rule_not_context_aware")

    for owner, payload in {"current": current, "patch0": patch0, "schema_aware": schema_aware, "cue": cue}.items():
        if payload.get("design_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
            failures.append(f"{owner}_design_sample_count_mismatch")
        if payload.get("assignment_count") != EXPECTED_ASSIGNMENT_COUNT:
            failures.append(f"{owner}_assignment_count_mismatch")
    if current.get("candidate_generator_variant") != SELECTED_VARIANT or schema_aware.get("candidate_generator_variant") != SELECTED_VARIANT:
        failures.append("candidate_generator_variant_mismatch")
    if filtered_alias != schema_aware:
        failures.append("atomic_filtered_alias_mismatch")

    if current.get("covered_assignment_count") != 2252 or current.get("full_sample_covered_count") != 724:
        failures.append("current_baseline_counts_mismatch")
    if patch0.get("covered_assignment_count") != 2249 or patch0.get("full_sample_covered_count") != 721:
        failures.append("patch0_generic_counts_mismatch")
    if schema_aware.get("covered_assignment_count") != 2252 or schema_aware.get("full_sample_covered_count") != 724:
        failures.append("schema_aware_counts_mismatch")
    if float(schema_aware.get("assignment_representability", 0.0)) < MIN_ASSIGNMENT_COVERAGE:
        failures.append("schema_aware_assignment_representability_below_threshold")
    if float(schema_aware.get("full_sample_representability", 0.0)) < MIN_FULL_SAMPLE_COVERAGE:
        failures.append("schema_aware_full_sample_representability_below_threshold")
    if int(schema_aware.get("covered_assignment_count", 0)) < math.ceil(MIN_ASSIGNMENT_COVERAGE * EXPECTED_ASSIGNMENT_COUNT):
        failures.append("schema_aware_covered_assignment_count_below_floor")
    if int(schema_aware.get("full_sample_covered_count", 0)) < math.ceil(MIN_FULL_SAMPLE_COVERAGE * EXPECTED_DESIGN_SAMPLE_COUNT):
        failures.append("schema_aware_full_sample_count_below_floor")
    if schema_aware.get("suppression_rule_counts", {}).get("SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE", 0) <= 0:
        failures.append("schema_label_suppression_rule_not_observed")
    if schema_aware.get("suppression_rule_counts", {}).get("CONTEXT_AWARE_OMISSION_CUE", 0) not in {None, 0}:
        failures.append("unexpected_design_train_context_omission_suppression")
    if int(schema_aware.get("suppressed_candidate_total", 0)) >= int(patch0.get("suppressed_candidate_total", 0)):
        failures.append("schema_aware_not_more_conservative_than_patch0")
    if schema_aware.get("broader_containing_gold_total", 0) >= current.get("broader_containing_gold_total", 0):
        failures.append("schema_aware_broad_burden_not_reduced")

    if comparison.get("status") != "PASS":
        failures.append("comparison_status_not_pass")
    if comparison.get("threshold_decision") != "PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW":
        failures.append("comparison_threshold_decision_mismatch")
    if comparison.get("method_freeze_authorized") is not False:
        failures.append("comparison_authorizes_freeze")
    pareto_domains = [row.get("domain") for row in comparison.get("pareto_rows", [])]
    if pareto_domains != ["lexical_ngram2", "patch0_generic_atomic", "patch1_schema_label_aware"]:
        failures.append("comparison_pareto_domains_mismatch")
    if comparison.get("assignment_representability_delta") != 0.0 or comparison.get("full_sample_representability_delta") != 0.0:
        failures.append("schema_aware_representability_not_preserved")
    if comparison.get("candidate_count_p95_delta", 0) >= 0:
        failures.append("candidate_count_p95_not_reduced")
    if comparison.get("broader_containing_gold_total_delta", 0) >= 0:
        failures.append("broader_candidate_burden_not_reduced")

    if false_suppression.get("status") != "PASS":
        failures.append("false_suppression_status_not_pass")
    if false_suppression.get("additional_assignment_losses") != 0:
        failures.append("false_suppression_assignment_losses_present")
    if false_suppression.get("additional_full_sample_losses") != 0:
        failures.append("false_suppression_full_sample_losses_present")
    if false_suppression.get("preferred_freeze_gate_passed") is not True:
        failures.append("false_suppression_preferred_gate_not_passed")

    if cue.get("true_assigned_value_exact_cue_count") != 0:
        failures.append("cue_exact_gold_value_present")
    if cue.get("true_assigned_value_contains_cue_count") != 0:
        failures.append("cue_containing_gold_value_present")
    if cue.get("question_cue_occurrence_count") != 0:
        failures.append("unexpected_design_train_cue_occurrence")
    if synthetic.get("status") != "PASS":
        failures.append("synthetic_omission_safety_not_pass")
    if synthetic.get("positive_fixture_count") != 4 or synthetic.get("negative_literal_fixture_count") != 4:
        failures.append("synthetic_omission_fixture_count_mismatch")

    if len(rows) != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("audit_rows_count_mismatch")
    if sum(1 for row in rows if row.get("current_full_sample_representable") is True) != current.get("full_sample_covered_count"):
        failures.append("row_current_full_count_mismatch")
    if sum(1 for row in rows if row.get("patch0_generic_full_sample_representable") is True) != patch0.get("full_sample_covered_count"):
        failures.append("row_patch0_full_count_mismatch")
    if sum(1 for row in rows if row.get("atomic_filtered_full_sample_representable") is True) != schema_aware.get("full_sample_covered_count"):
        failures.append("row_schema_aware_full_count_mismatch")
    if sum(int(row.get("suppressed_candidate_count", 0)) for row in rows) != schema_aware.get("suppressed_candidate_total"):
        failures.append("row_suppressed_count_mismatch")
    if not examples:
        failures.append("suppression_examples_missing")
    for example in examples[:20]:
        reason = example.get("reason", {})
        if reason.get("rule") == "SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE":
            allowed_tags = STRONG_ATOMIC_TAGS | {"DATE", "DATETIME", "ORDINAL_PHRASE", "COMPOUND_LITERAL"}
            if not set(reason.get("dominant_child_tags", [])) & allowed_tags:
                failures.append("suppression_example_missing_atomic_child")
                break
            if not reason.get("schema_label_residual"):
                failures.append("suppression_example_missing_schema_residual")
                break

    inventory = generate_candidate_inventory("Insert loan_id LOAN-842, mass 0.42, century 20th Century.", variant=SELECTED_VARIANT)
    by_text = {candidate.text: candidate for candidate in inventory}
    schema_labels = {"loan id", "mass", "century"}
    if generic_atomic_dominance_reason(by_text["20th Century"], inventory) is not None:
        failures.append("lexical_hardening_drops_ordinal_phrase")
    if schema_label_aware_dominance_reason(by_text["loan_id LOAN-842"], inventory, schema_labels) is None:
        failures.append("schema_aware_does_not_drop_label_identifier")
    if schema_label_aware_dominance_reason(by_text["20th Century"], inventory, schema_labels) is not None:
        failures.append("schema_aware_drops_ordinal_phrase")

    omission_question = 'Insert status "missing". phone not provided.'
    omission_inventory = generate_candidate_inventory(omission_question, variant=SELECTED_VARIANT)
    detections = detect_omission_constructions(omission_question, {"status", "phone"})
    reasons = suppressible_span_refs(omission_inventory, {"status", "phone"}, detections)
    suppressed_texts = {candidate.text for candidate in omission_inventory if candidate.span_ref in reasons}
    if "not provided" not in suppressed_texts:
        failures.append("context_omission_positive_not_suppressed")
    if "missing" in suppressed_texts:
        failures.append("context_omission_suppresses_quoted_literal")
    if not is_exact_omission_cue("missing"):
        failures.append("omission_cue_exact_helper_regressed")

    for item in source.get("source_files", []):
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
    if lock.get("status") != "PASS_SCHEMA_LABEL_AWARE_CANDIDATE_DOMAIN_OMISSION_CUE_AUDIT_READY_FOR_REVIEW":
        failures.append("lock_status_mismatch")
    if lock.get("method_freeze_authorized") is not False:
        failures.append("lock_authorizes_freeze")
    if lock.get("additional_assignment_losses") != 0 or lock.get("additional_full_sample_losses") != 0:
        failures.append("lock_false_suppression_counts_mismatch")
    if lock.get("gretel_pilot_opened") is not False or lock.get("development_dev_used") is not False or lock.get("official_test_used") is not False:
        failures.append("lock_reserved_rows_used")

    if raw_dir is not None and rebuild and not failures:
        temp_path = stage_dir.parent / "_stage7b_a4_patch1_rebuild_validation_tmp"
        shutil.rmtree(temp_path, ignore_errors=True)
        temp_path.mkdir(parents=True, exist_ok=True)
        temp_stage = temp_path / STAGE_NAME
        try:
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
        "design_sample_count": current.get("design_sample_count"),
        "assignment_count": current.get("assignment_count"),
        "current_assignment_representability": current.get("assignment_representability"),
        "patch0_generic_assignment_representability": patch0.get("assignment_representability"),
        "schema_label_aware_assignment_representability": schema_aware.get("assignment_representability"),
        "schema_label_aware_full_sample_representability": schema_aware.get("full_sample_representability"),
        "additional_assignment_losses": false_suppression.get("additional_assignment_losses"),
        "additional_full_sample_losses": false_suppression.get("additional_full_sample_losses"),
        "suppressed_candidate_total": schema_aware.get("suppressed_candidate_total"),
        "synthetic_omission_cue_safety_status": synthetic.get("status"),
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
