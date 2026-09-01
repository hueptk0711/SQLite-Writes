#!/usr/bin/env python3
"""Validate Stage7B-A4 atomic candidate-domain and omission-cue audit artifacts."""

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
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    STRONG_ATOMIC_TAGS,
    atomic_dominance_reason,
    build_stage,
    canonical_json,
    contains_omission_cue,
    generate_candidate_inventory,
    is_exact_omission_cue,
    sha256_file,
    sha256_text,
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
    filtered = read_json(stage_dir / "ATOMIC_FILTERED_DOMAIN_AUDIT.json")
    comparison = read_json(stage_dir / "DOMAIN_COMPARISON_AUDIT.json")
    cue = read_json(stage_dir / "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json")
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
            "filtered": filtered,
            "comparison": comparison,
            "cue": cue,
            "lock": lock,
        },
        failures,
    )

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
    if protocol.get("forbidden_inputs_for_candidate_suppression") != ["gold_sql", "gold_values", "gold_offsets", "target_state", "model_outputs"]:
        failures.append("protocol_forbidden_inputs_mismatch")
    if protocol.get("design_sample_count_required") != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("protocol_design_count_mismatch")
    if atomic.get("audit_only") is not True or atomic.get("gold_blind") is not True:
        failures.append("atomic_rule_not_audit_only_gold_blind")
    if omission.get("audit_only") is not True or omission.get("cue_phrases") != list(OMISSION_CUE_PHRASES):
        failures.append("omission_rule_spec_mismatch")

    for owner, payload in {"current": current, "filtered": filtered, "cue": cue}.items():
        if payload.get("design_sample_count") != EXPECTED_DESIGN_SAMPLE_COUNT:
            failures.append(f"{owner}_design_sample_count_mismatch")
        if payload.get("assignment_count") != EXPECTED_ASSIGNMENT_COUNT:
            failures.append(f"{owner}_assignment_count_mismatch")
    if current.get("candidate_generator_variant") != SELECTED_VARIANT or filtered.get("candidate_generator_variant") != SELECTED_VARIANT:
        failures.append("candidate_generator_variant_mismatch")
    if current.get("covered_assignment_count") != 2252 or current.get("full_sample_covered_count") != 724:
        failures.append("current_baseline_counts_mismatch")
    if filtered.get("covered_assignment_count") != 2249 or filtered.get("full_sample_covered_count") != 721:
        failures.append("filtered_counts_mismatch")
    if float(filtered.get("assignment_representability", 0.0)) < MIN_ASSIGNMENT_COVERAGE:
        failures.append("filtered_assignment_representability_below_threshold")
    if float(filtered.get("full_sample_representability", 0.0)) < MIN_FULL_SAMPLE_COVERAGE:
        failures.append("filtered_full_sample_representability_below_threshold")
    if int(filtered.get("covered_assignment_count", 0)) < math.ceil(MIN_ASSIGNMENT_COVERAGE * EXPECTED_ASSIGNMENT_COUNT):
        failures.append("filtered_covered_assignment_count_below_floor")
    if int(filtered.get("full_sample_covered_count", 0)) < math.ceil(MIN_FULL_SAMPLE_COVERAGE * EXPECTED_DESIGN_SAMPLE_COUNT):
        failures.append("filtered_full_sample_count_below_floor")
    if int(filtered.get("suppressed_candidate_total", 0)) <= 0:
        failures.append("filtered_suppressed_candidate_total_not_positive")
    if filtered.get("suppression_rule_counts", {}).get("ATOMIC_DOMINATED_BROAD_SPAN", 0) <= 0:
        failures.append("atomic_suppression_rule_not_observed")
    if filtered.get("suppression_rule_counts", {}).get("EXACT_OMISSION_CUE", 0) not in {None, 0}:
        failures.append("unexpected_design_train_omission_cue_candidate_suppression")

    if comparison.get("status") != "PASS":
        failures.append("comparison_status_not_pass")
    if comparison.get("threshold_decision") != "PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW":
        failures.append("comparison_threshold_decision_mismatch")
    if comparison.get("method_freeze_authorized") is not False:
        failures.append("comparison_authorizes_freeze")
    if comparison.get("candidate_count_p95_delta", 0) >= 0:
        failures.append("candidate_count_p95_not_reduced")
    if comparison.get("broader_containing_gold_total_delta", 0) >= 0:
        failures.append("broader_candidate_burden_not_reduced")

    if cue.get("true_assigned_value_exact_cue_count") != 0:
        failures.append("cue_exact_gold_value_present")
    if cue.get("true_assigned_value_contains_cue_count") != 0:
        failures.append("cue_containing_gold_value_present")
    if cue.get("omission_cue_suppression_has_design_train_recall_risk") is not False:
        failures.append("cue_recall_risk_not_false")

    if len(rows) != EXPECTED_DESIGN_SAMPLE_COUNT:
        failures.append("audit_rows_count_mismatch")
    row_current_full = sum(1 for row in rows if row.get("current_full_sample_representable") is True)
    row_filtered_full = sum(1 for row in rows if row.get("atomic_filtered_full_sample_representable") is True)
    row_suppressed_total = sum(int(row.get("suppressed_candidate_count", 0)) for row in rows)
    if row_current_full != current.get("full_sample_covered_count"):
        failures.append("row_current_full_count_mismatch")
    if row_filtered_full != filtered.get("full_sample_covered_count"):
        failures.append("row_filtered_full_count_mismatch")
    if row_suppressed_total != filtered.get("suppressed_candidate_total"):
        failures.append("row_suppressed_count_mismatch")
    if not examples:
        failures.append("suppression_examples_missing")
    for example in examples[:20]:
        reason = example.get("reason", {})
        if reason.get("rule") == "ATOMIC_DOMINATED_BROAD_SPAN":
            if not set(reason.get("dominant_child_tags", [])) & STRONG_ATOMIC_TAGS:
                failures.append("suppression_example_missing_strong_child")
                break
        if reason.get("rule") == "EXACT_OMISSION_CUE" and not is_exact_omission_cue(str(example.get("text", ""))):
            failures.append("suppression_example_invalid_omission_cue")
            break

    question = "Insert loan_id LOAN-842 and quarter Q2. Comment omitted."
    inventory = generate_candidate_inventory(question)
    by_text = {candidate.text: candidate for candidate in inventory}
    if atomic_dominance_reason(by_text["loan_id LOAN-842"], inventory) is None:
        failures.append("atomic_rule_does_not_drop_label_plus_identifier")
    if atomic_dominance_reason(by_text["Q2"], inventory) is not None:
        failures.append("atomic_rule_drops_single_token_identifier")
    if not contains_omission_cue("Comment omitted") or not is_exact_omission_cue(" omitted. "):
        failures.append("omission_cue_helpers_regressed")

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
    if lock.get("status") != "PASS_ATOMIC_CANDIDATE_DOMAIN_OMISSION_CUE_AUDIT_READY_FOR_REVIEW":
        failures.append("lock_status_mismatch")
    if lock.get("method_freeze_authorized") is not False:
        failures.append("lock_authorizes_freeze")
    if lock.get("gretel_pilot_opened") is not False or lock.get("development_dev_used") is not False or lock.get("official_test_used") is not False:
        failures.append("lock_reserved_rows_used")

    if raw_dir is not None and rebuild and not failures:
        temp_path = stage_dir.parent / "_stage7b_a4_rebuild_validation_tmp"
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
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "design_sample_count": current.get("design_sample_count"),
        "assignment_count": current.get("assignment_count"),
        "current_assignment_representability": current.get("assignment_representability"),
        "current_full_sample_representability": current.get("full_sample_representability"),
        "atomic_filtered_assignment_representability": filtered.get("assignment_representability"),
        "atomic_filtered_full_sample_representability": filtered.get("full_sample_representability"),
        "suppressed_candidate_total": filtered.get("suppressed_candidate_total"),
        "true_assigned_value_exact_cue_count": cue.get("true_assigned_value_exact_cue_count"),
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
