from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a1_free_text_slot_discovery_amendment import (
    ARTIFACTS,
    HASH_POLICY,
    LOCK_FILE,
    PHASE_O_MAX_NEW_TOKENS,
    STAGE,
    STAGE7B_INPUTS,
    STAGE7C_PATCH2_INPUTS,
    artifact_hashes,
    canonical_json,
    input_hashes,
    materializable_slot_audit,
    phase_o_schema,
    read_json,
    sha256_file,
    source_span_oracle_audit,
    write_json,
)


PASS_STATUS = "PASS_STAGE7B_A1_FREE_TEXT_SLOT_DISCOVERY_AMENDMENT_LOCKED"


def require(condition: bool, violations: list[str], code: str) -> None:
    if not condition:
        violations.append(code)


def schema_has_model_text_field(schema: dict[str, Any]) -> bool:
    text = canonical_json(schema)
    return '"text"' in text or '"value"' in text or '"raw_value"' in text


def validate(output_dir: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    violations: list[str] = []
    checks = {
        "input_hashes_recomputed": False,
        "materializable_audit_recomputed": False,
        "source_span_oracle_recomputed": False,
        "phase_o_schema_validated": False,
        "span_validation_validated": False,
        "nonalignable_policy_validated": False,
        "evidence_slot_separation_validated": False,
        "completeness_amendment_validated": False,
        "ablation_amendment_validated": False,
        "capacity_amendment_validated": False,
    }
    for rel in ARTIFACTS + (LOCK_FILE,):
        if not (output_dir / rel).is_file():
            violations.append(f"missing_artifact:{rel}")
    if violations:
        return {"stage": STAGE, "status": "FAIL", "violations": violations, "model_called": False, "gpu_called": False, **checks}

    hashes = {}
    for rel in STAGE7B_INPUTS + STAGE7C_PATCH2_INPUTS:
        path = root / rel
        if not path.is_file():
            violations.append(f"missing_input:{rel}")
        else:
            hashes[rel] = sha256_file(path)
    checks["input_hashes_recomputed"] = True

    manifest = read_json(output_dir / "STAGE7B_A1_INPUT_MANIFEST.json")
    lock = read_json(output_dir / LOCK_FILE)
    require(manifest.get("hash_policy") == HASH_POLICY, violations, "manifest_hash_policy_mismatch")
    require(lock.get("hash_policy") == HASH_POLICY, violations, "lock_hash_policy_mismatch")
    require(manifest.get("input_hashes") == hashes, violations, "manifest_input_hashes_mismatch")
    require(lock.get("input_hashes") == hashes, violations, "lock_input_hashes_mismatch")
    require(lock.get("artifact_hashes") == artifact_hashes(output_dir), violations, "lock_artifact_hashes_mismatch")
    require(lock.get("status") in {"BUILT_PENDING_VALIDATION", PASS_STATUS}, violations, "lock_status_invalid")
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        require(lock.get(key) is False, violations, f"forbidden_flag_not_false:{key}")

    stage7b_lock = read_json(root / "stage7b_v2_method_specification" / "STAGE7B_V2_SPECIFICATION_LOCK.json")
    stage7c_lock = read_json(root / "stage7c_v2_development_data_protocol" / "STAGE7C_DATA_PROTOCOL_LOCK.json")
    require(stage7b_lock.get("status") == "PASS_V2_METHOD_SPECIFICATION_LOCKED", violations, "stage7b_base_not_locked")
    require(stage7c_lock.get("model_called") is False and stage7c_lock.get("gpu_called") is False, violations, "stage7c_model_gpu_flags_invalid")

    audit = read_json(output_dir / "MATERIALIZABLE_SLOT_AUDIT.json")
    recomputed_audit = materializable_slot_audit()
    require(audit == recomputed_audit, violations, "materializable_slot_audit_mismatch")
    require(audit["dev"]["substring_candidate_coverage_rate"] >= 0.95, violations, "dev_substring_coverage_not_reproduced")
    require(audit["dev"]["materializable_candidate_coverage_count"] == 257, violations, "dev_materializable_count_changed")
    require(audit["dev"]["gold_assignment_count"] == 845, violations, "dev_gold_assignment_count_changed")
    require(audit["dev"]["materializable_candidate_coverage_rate"] < 0.5, violations, "dev_materializable_coverage_not_low_enough_to_trigger_amendment")
    require(audit["dev"]["samples_missing_materializable_candidate"] == 217, violations, "dev_missing_materializable_sample_count_changed")
    require(audit["dev"]["required_slot_count"] == 4, violations, "dev_required_slot_count_changed")
    require(audit["dev"]["required_slots_per_gold_assignment"] < 0.01, violations, "dev_required_slot_ratio_not_near_zero")
    require(audit["train"]["materializable_candidate_coverage_count"] == 2366, violations, "train_materializable_count_changed")
    checks["materializable_audit_recomputed"] = True

    oracle = read_json(output_dir / "SOURCE_SPAN_ORACLE_AUDIT.json")
    recomputed_oracle = source_span_oracle_audit()
    require(oracle == recomputed_oracle, violations, "source_span_oracle_audit_mismatch")
    require(oracle["train"]["source_selectable_gold_value_count"] == 5295, violations, "train_oracle_source_selectable_count_changed")
    require(oracle["train"]["gold_assignment_count"] == 5407, violations, "train_oracle_denominator_changed")
    require(oracle["train"]["source_selectable_gold_value_rate"] == 0.979286, violations, "train_oracle_rate_changed")
    require(oracle["train"]["samples_with_at_least_one_non_source_alignable_value"] == 93, violations, "train_nonalignable_sample_count_changed")
    require(oracle["dev"]["source_selectable_gold_value_count"] == 810, violations, "dev_oracle_source_selectable_count_changed")
    require(oracle["dev"]["gold_assignment_count"] == 845, violations, "dev_oracle_denominator_changed")
    require(oracle["dev"]["source_selectable_gold_value_rate"] == 0.95858, violations, "dev_oracle_rate_changed")
    require(oracle["dev"]["samples_with_at_least_one_non_source_alignable_value"] == 33, violations, "dev_nonalignable_sample_count_changed")
    oracle_policy = oracle.get("nonalignable_policy", {})
    require(oracle_policy.get("retain_in_primary_dev_denominator") is True, violations, "oracle_policy_excludes_dev_nonalignable")
    require(oracle_policy.get("eligible") is True, violations, "oracle_policy_marks_nonalignable_ineligible")
    require(oracle_policy.get("modify_gold") is False, violations, "oracle_policy_allows_gold_modification")
    require(oracle_policy.get("add_post_hoc_normalization") is False, violations, "oracle_policy_allows_post_hoc_normalization")
    checks["source_span_oracle_recomputed"] = True

    rationale = read_json(output_dir / "STAGE7B_A1_AMENDMENT_RATIONALE.json")
    require(rationale.get("decision") == "reopen Stage7B by amending Phase O from operation-only to operation plus grounded atomic semantic span selection", violations, "rationale_decision_changed")
    require(rationale.get("not_a_stage7c_regex_patch") is True, violations, "rationale_allows_regex_patch")

    schema = read_json(output_dir / "PHASE_O_JSON_SCHEMA.json")
    require(schema == phase_o_schema(), violations, "phase_o_schema_changed")
    require(schema.get("additionalProperties") is False, violations, "phase_o_schema_allows_extra_fields")
    require(schema.get("required") == ["operation", "value_spans"], violations, "phase_o_required_fields_changed")
    require(schema["properties"]["operation"]["enum"] == ["INSERT", "UPDATE", "DELETE", "UPSERT"], violations, "phase_o_operation_enum_changed")
    span_item = schema["properties"]["value_spans"]["items"]
    require(schema["properties"]["value_spans"].get("minItems") == 1, violations, "phase_o_allows_empty_value_spans")
    require(span_item.get("required") == ["start_char", "end_char"], violations, "phase_o_span_required_fields_changed")
    require("span_ref" not in span_item.get("properties", {}), violations, "phase_o_schema_allows_model_generated_span_ref")
    require(not schema_has_model_text_field(schema), violations, "phase_o_schema_allows_model_emitted_text")
    checks["phase_o_schema_validated"] = True

    span_spec = read_json(output_dir / "SPAN_VALIDATION_SPEC.json")
    require(span_spec.get("span_text_source") == "question[start_char:end_char] only", violations, "span_text_not_offset_derived")
    require(span_spec.get("model_emitted_text_allowed") is False, violations, "span_model_text_allowed")
    require(span_spec.get("model_generated_span_ids_allowed") is False, violations, "span_model_generated_ids_allowed")
    require(span_spec.get("offset_coordinate_system") == "Python Unicode code-point indexing", violations, "offset_coordinate_system_not_frozen")
    require(span_spec.get("range_convention") == "[start_char, end_char)", violations, "offset_range_convention_not_frozen")
    require(span_spec.get("phase_o_question_string") == "exact original question string Q", violations, "phase_o_question_string_not_exact_original")
    require(span_spec.get("normalization_before_offset_validation") == "none", violations, "offset_normalization_not_forbidden")
    require(all(value is False for value in span_spec.get("normalization_policy", {}).values()), violations, "normalization_policy_not_all_false")
    require(span_spec.get("duplicate_span_policy") == "reject", violations, "duplicate_span_policy_not_reject")
    require(span_spec.get("nested_span_policy") == "reject", violations, "nested_span_policy_not_reject")
    require(span_spec.get("partial_overlap_policy") == "reject", violations, "partial_overlap_policy_not_reject")
    require(span_spec.get("inventory_assignment_order") == "sort_by_start_char_then_end_char", violations, "inventory_order_not_deterministic")
    checks["span_validation_validated"] = True

    policy = read_json(output_dir / "NONALIGNABLE_SOURCE_SPAN_POLICY.json")
    require(policy.get("diagnostic_flag") == "source_gold_nonalignable_under_frozen_materializer", violations, "nonalignable_flag_changed")
    require(policy.get("retain_in_primary_train_denominator") is True, violations, "nonalignable_train_denominator_not_retained")
    require(policy.get("retain_in_primary_dev_denominator") is True, violations, "nonalignable_dev_denominator_not_retained")
    require(policy.get("train_eligible") is True and policy.get("dev_eligible") is True, violations, "nonalignable_marked_ineligible")
    require(policy.get("exclude_after_model_performance") is False, violations, "nonalignable_can_be_excluded_post_hoc")
    require(policy.get("modify_gold") is False, violations, "nonalignable_policy_allows_gold_modification")
    require(policy.get("add_post_hoc_normalization") is False, violations, "nonalignable_policy_allows_post_hoc_normalization")
    checks["nonalignable_policy_validated"] = True

    separation = read_json(output_dir / "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json")
    require(separation.get("forbidden_mapping") == "do_not_convert_every_context_evidence_span_into_SLOT", violations, "evidence_slot_forbidden_mapping_missing")
    require(separation.get("semantic_slots_from_phase_o_only") is True, violations, "semantic_slots_not_phase_o_only")
    require(separation.get("broad_context_evidence_required") is False, violations, "broad_context_evidence_marked_required")
    checks["evidence_slot_separation_validated"] = True

    completeness = read_json(output_dir / "COMPLETENESS_AMENDED_SPEC.json")
    require(completeness.get("required_set") == "all SLOT_* created from accepted Phase O value_spans", violations, "completeness_required_set_changed")
    require("required_set - mapped_set" in completeness.get("missing", ""), violations, "completeness_missing_formula_changed")
    require("mapped_set - allowed_slot_set" in completeness.get("extra", ""), violations, "completeness_extra_formula_changed")
    checks["completeness_amendment_validated"] = True

    ablation = read_json(output_dir / "ABLATION_AMENDMENT.json")
    require("V2-D_MINUS_COMPLETENESS_VERIFICATION" in ablation.get("amended_variants", {}), violations, "v2d_ablation_missing")
    require("V2-O_MINUS_SPAN_SELECTION" in ablation.get("amended_variants", {}), violations, "span_selection_diagnostic_missing")
    v2a = ablation.get("amended_variants", {}).get("V2-A_MINUS_OPERATION_CONDITIONING", {})
    require(isinstance(v2a, dict), violations, "v2a_intervention_not_structured")
    require(v2a.get("phase_o_a_responsibilities") == ["semantic_span_selection"], violations, "v2a_phase_o_responsibilities_not_frozen")
    require(v2a.get("phase_m_a_responsibilities") == ["operation_prediction", "slot_to_column_or_predicate_mapping"], violations, "v2a_phase_m_responsibilities_not_frozen")
    require(v2a.get("total_model_calls") == 2, violations, "v2a_model_call_count_changed")
    require(v2a.get("schemas") == "single unified operation-unconditioned Phase M schema", violations, "v2a_schema_not_frozen")
    v2o = ablation.get("amended_variants", {}).get("V2-O_MINUS_SPAN_SELECTION", {})
    require(isinstance(v2o, dict) and v2o.get("diagnostic_only") is True, violations, "v2o_not_marked_diagnostic_only")
    require(isinstance(v2o, dict) and v2o.get("confirmatory_ablation_family_member") is False, violations, "v2o_marked_confirmatory")
    require(ablation.get("hidden_third_model_call_allowed") is False, violations, "ablation_allows_hidden_third_call")
    require("V2-O_MINUS_SPAN_SELECTION" not in ablation.get("confirmatory_ablation_family", []), violations, "v2o_in_confirmatory_ablation_family")
    checks["ablation_amendment_validated"] = True

    capacity = read_json(output_dir / "GENERATION_CAPACITY_AMENDMENT.json")
    require(capacity.get("old_phase_o_max_new_tokens") == 32, violations, "old_phase_o_cap_changed")
    require(capacity.get("new_phase_o_max_new_tokens") == PHASE_O_MAX_NEW_TOKENS, violations, "new_phase_o_cap_changed")
    require(capacity.get("phase_m_max_new_tokens") == 8192, violations, "phase_m_cap_changed")
    require("c03e6d358207e414f1eca0bb1891e29f1db0e242" in capacity.get("model_revision_unchanged", ""), violations, "model_revision_not_preserved")
    checks["capacity_amendment_validated"] = True

    return {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "dev_materializable_candidate_coverage": audit["dev"]["materializable_candidate_coverage_rate"],
        "dev_source_span_oracle_coverage": oracle["dev"]["source_selectable_gold_value_rate"],
        "dev_required_slots_per_gold_assignment": audit["dev"]["required_slots_per_gold_assignment"],
        "model_called": False,
        "gpu_called": False,
        "v2_implemented": False,
        "experiment_run": False,
        "live_sql_bench_gt_opened": False,
        **checks,
    }


def validation_report_text(report: dict[str, Any]) -> str:
    lines = [
        "# Stage7B A1 Validation Report",
        "",
        f"Status: {report['status']}",
        "",
        f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}",
        "",
        f"dev_materializable_candidate_coverage: {report.get('dev_materializable_candidate_coverage')}",
        f"dev_source_span_oracle_coverage: {report.get('dev_source_span_oracle_coverage')}",
        f"dev_required_slots_per_gold_assignment: {report.get('dev_required_slots_per_gold_assignment')}",
        "",
    ]
    for key in (
        "input_hashes_recomputed",
        "materializable_audit_recomputed",
        "source_span_oracle_recomputed",
        "phase_o_schema_validated",
        "span_validation_validated",
        "nonalignable_policy_validated",
        "evidence_slot_separation_validated",
        "completeness_amendment_validated",
        "ablation_amendment_validated",
        "capacity_amendment_validated",
    ):
        lines.append(f"{key}: {str(report.get(key)).lower()}")
    lines.extend(
        [
            "",
            f"model_called: {str(report.get('model_called')).lower()}",
            f"gpu_called: {str(report.get('gpu_called')).lower()}",
            f"v2_implemented: {str(report.get('v2_implemented')).lower()}",
            f"experiment_run: {str(report.get('experiment_run')).lower()}",
            f"live_sql_bench_gt_opened: {str(report.get('live_sql_bench_gt_opened')).lower()}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report_and_update_lock(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = output_dir / "VALIDATION_REPORT.md"
    report_path.write_text(validation_report_text(report), encoding="utf-8")
    lock_path = output_dir / LOCK_FILE
    lock = read_json(lock_path)
    if report["status"] == "PASS":
        lock["status"] = PASS_STATUS
    hashes = artifact_hashes(output_dir)
    hashes["VALIDATION_REPORT.md"] = sha256_file(report_path)
    lock["artifact_hashes"] = hashes
    write_json(lock_path, lock)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7b_a1_free_text_slot_discovery_amendment")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
