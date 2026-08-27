#!/usr/bin/env python3
"""Validate Stage7C-A1 V2 development protocol artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7c_a1_v2_development_protocol import (
    ARTIFACTS,
    FROZEN_MODEL_CONFIG,
    HASH_POLICY,
    LOCK_FILE,
    PROMPT_SERIALIZATION_CONTRACT,
    PASS_STATUS,
    STAGE,
    artifact_hashes,
    count_reused_data,
    input_hashes,
    label_alignment_summary,
    leakage_audit,
    literal_gold_occurrence_audit,
    offset_guide,
    prompt_hash_payload,
    read_json,
    read_jsonl,
    rendered_prompt_sha256,
    serialize_prompt_object,
    sha256_file,
    source_span_label_manifest,
    validation_report_text,
    write_json,
)


def require(condition: bool, violations: list[str], code: str) -> None:
    if not condition:
        violations.append(code)


def validate(output_dir: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    violations: list[str] = []
    checks = {
        "input_hashes_recomputed": False,
        "artifact_hashes_recomputed": False,
        "stage7b_a1_lock_checked": False,
        "stage7c_reuse_checked": False,
        "phase_o_prompt_checked": False,
        "prompt_serialization_checked": False,
        "chat_template_rendering_checked": False,
        "offset_guide_checked": False,
        "phase_o_validation_checked": False,
        "label_alignment_manifest_recomputed": False,
        "phase_m_protocol_checked": False,
        "oracle_diagnostic_checked": False,
        "generation_protocol_checked": False,
        "leakage_audit_recomputed": False,
        "reserved_policy_checked": False,
    }
    for rel in ARTIFACTS + (LOCK_FILE,):
        if not (output_dir / rel).is_file():
            violations.append(f"missing_artifact:{rel}")
    if violations:
        return {"stage": STAGE, "status": "FAIL", "violations": violations, **checks}

    manifest = read_json(output_dir / "STAGE7C_A1_INPUT_MANIFEST.json")
    lock = read_json(output_dir / LOCK_FILE)
    require(manifest.get("hash_policy") == HASH_POLICY, violations, "manifest_hash_policy_mismatch")
    require(lock.get("hash_policy") == HASH_POLICY, violations, "lock_hash_policy_mismatch")
    hashes = input_hashes(root)
    require(manifest.get("input_hashes") == hashes, violations, "manifest_input_hashes_mismatch")
    require(lock.get("input_hashes") == hashes, violations, "lock_input_hashes_mismatch")
    checks["input_hashes_recomputed"] = True
    require(lock.get("artifact_hashes") == artifact_hashes(output_dir), violations, "lock_artifact_hashes_mismatch")
    checks["artifact_hashes_recomputed"] = True
    require(lock.get("status") in {"BUILT_PENDING_VALIDATION", PASS_STATUS}, violations, "lock_status_invalid")
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        require(lock.get(key) is False, violations, f"forbidden_flag_not_false:{key}")

    stage7b_a1_lock = read_json(root / "stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json")
    require(stage7b_a1_lock.get("status") == "PASS_STAGE7B_A1_FREE_TEXT_SLOT_DISCOVERY_AMENDMENT_LOCKED", violations, "stage7b_a1_not_locked")
    require(stage7b_a1_lock.get("total_model_call_count") == 2, violations, "stage7b_a1_model_call_count_changed")
    checks["stage7b_a1_lock_checked"] = True

    reused = read_json(output_dir / "REUSED_DATA_PROTOCOL_MANIFEST.json")
    recomputed_counts = count_reused_data(root)
    require(reused.get("counts") == recomputed_counts, violations, "reused_counts_mismatch")
    require(recomputed_counts["train_create_count"] == 1760, violations, "train_create_count_changed")
    require(recomputed_counts["dev_create_count"] == 240, violations, "dev_create_count_changed")
    require(recomputed_counts["old_stage7c_status"] == "PASS_STAGE7C_DATA_PROTOCOL_LOCKED", violations, "old_stage7c_not_locked")
    require(recomputed_counts["gold_derivation"]["train_pass"] == 1760, violations, "train_gold_derivation_not_1760")
    require(recomputed_counts["gold_derivation"]["dev_pass"] == 240, violations, "dev_gold_derivation_not_240")
    require(recomputed_counts["gold_derivation"]["train_failures"] == 0, violations, "train_gold_failures_nonzero")
    require(recomputed_counts["gold_derivation"]["dev_failures"] == 0, violations, "dev_gold_failures_nonzero")
    require(recomputed_counts["operation_mapping_type0"] == "INSERT", violations, "create_not_mapped_to_insert")
    for key in (
        "train_dev_question_hash_overlap",
        "train_481_question_hash_overlap",
        "dev_481_question_hash_overlap",
        "train_dev_table_id_overlap",
        "train_confirmation_table_id_overlap",
        "dev_confirmation_table_id_overlap",
    ):
        require(recomputed_counts["contamination"][key] == 0, violations, f"contamination_nonzero:{key}")
    require(recomputed_counts["source_span_oracle"]["dev_source_selectable"] == 810, violations, "dev_oracle_source_selectable_changed")
    require(recomputed_counts["source_span_oracle"]["dev_denominator"] == 845, violations, "dev_oracle_denominator_changed")
    require(recomputed_counts["source_span_oracle"]["dev_samples_with_gap"] == 33, violations, "dev_nonalignable_count_changed")
    checks["stage7c_reuse_checked"] = True

    phase_o_prompt = read_json(output_dir / "PHASE_O_PROMPT_SPEC.json")
    prompt_hashes = prompt_hash_payload()
    require(phase_o_prompt.get("prompt_hashes") == prompt_hashes, violations, "phase_o_prompt_hashes_mismatch")
    require(phase_o_prompt.get("few_shot_policy") == "zero_shot_no_examples_before_stage7d_unless_formally_amended_before_generation", violations, "phase_o_fewshot_policy_changed")
    require(phase_o_prompt.get("gold_visible") is False, violations, "phase_o_gold_visible")
    require("Python Unicode code-point offsets" in phase_o_prompt.get("character_offset_instructions", ""), violations, "phase_o_offset_instruction_missing")
    checks["phase_o_prompt_checked"] = True

    serialization = read_json(output_dir / "PROMPT_SERIALIZATION_SPEC.json")
    require(serialization.get("contract") == PROMPT_SERIALIZATION_CONTRACT, violations, "prompt_serialization_contract_changed")
    require(serialization.get("reference_python") == "json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))", violations, "prompt_serialization_reference_changed")
    require(serialization.get("ensure_ascii_true_allowed") is False, violations, "ensure_ascii_true_allowed")
    require(serialization.get("indentation_allowed") is False, violations, "prompt_indentation_allowed")
    require(serialization.get("str_or_repr_serialization_allowed") is False, violations, "str_repr_serialization_allowed")
    require(serialize_prompt_object({"b": "北京", "a": 1}) == '{"a":1,"b":"北京"}', violations, "canonical_serialization_reference_failed")
    require(rendered_prompt_sha256("x\r\ny") == rendered_prompt_sha256("x\ny"), violations, "rendered_prompt_hash_not_lf_canonical")
    checks["prompt_serialization_checked"] = True

    chat_template = read_json(output_dir / "CHAT_TEMPLATE_RENDERING_SPEC.json")
    require(chat_template.get("rendering_call") == "tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)", violations, "chat_template_rendering_call_changed")
    require(chat_template.get("add_generation_prompt") is True, violations, "chat_template_add_generation_prompt_not_true")
    require(chat_template.get("tokenize") is False, violations, "chat_template_tokenize_not_false")
    require(chat_template.get("contract", {}).get("tokenizer_config_file_sha256") == FROZEN_MODEL_CONFIG["tokenizer_config_file_sha256"], violations, "chat_template_tokenizer_config_hash_changed")
    require(chat_template.get("free_choice_in_stage7d_allowed") is False, violations, "chat_template_free_choice_allowed")
    checks["chat_template_rendering_checked"] = True

    guide = read_json(output_dir / "QUESTION_OFFSET_GUIDE_SPEC.json")
    require(guide.get("example_offset_guide") == offset_guide("A北京B上海C"), violations, "offset_guide_example_mismatch")
    require(guide.get("coordinate_system") == "Python Unicode code-point indexing", violations, "offset_coordinate_system_changed")
    require(guide.get("normalization_before_guide") == "none", violations, "offset_guide_normalization_allowed")
    require(guide.get("guide_uses_gold") is False, violations, "offset_guide_uses_gold")
    checks["offset_guide_checked"] = True

    phase_o_validation = read_json(output_dir / "PHASE_O_OUTPUT_VALIDATION_SPEC.json")
    expected_schema_hash = sha256_file(root / "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json")
    expected_span_hash = sha256_file(root / "stage7b_a1_free_text_slot_discovery_amendment/SPAN_VALIDATION_SPEC.json")
    require(phase_o_validation.get("json_schema_sha256") == expected_schema_hash, violations, "phase_o_schema_hash_mismatch")
    require(phase_o_validation.get("span_validation_spec_sha256") == expected_span_hash, violations, "span_validation_hash_mismatch")
    require(phase_o_validation.get("empty_value_spans_policy") == "reject", violations, "empty_spans_not_rejected")
    require(phase_o_validation.get("model_generated_span_ids_policy") == "reject", violations, "model_span_ids_not_rejected")
    require(phase_o_validation.get("model_generated_value_text_policy") == "reject", violations, "model_value_text_not_rejected")
    checks["phase_o_validation_checked"] = True

    label_manifest = read_jsonl(output_dir / "SOURCE_SPAN_LABEL_MANIFEST.jsonl")
    recomputed_label_manifest = source_span_label_manifest(root)
    require(label_manifest == recomputed_label_manifest, violations, "source_span_label_manifest_mismatch")
    label_spec = read_json(output_dir / "PHASE_O_LABEL_ALIGNMENT_SPEC.json")
    label_summary = label_alignment_summary(label_manifest)
    require(label_spec.get("label_manifest") == "SOURCE_SPAN_LABEL_MANIFEST.jsonl", violations, "label_manifest_name_changed")
    require(label_spec.get("label_side_only") is True, violations, "label_alignment_not_label_side_only")
    require(label_spec.get("model_side_visible") is False, violations, "label_alignment_model_side_visible")
    require(label_spec.get("summary") == label_summary, violations, "label_alignment_summary_mismatch")
    require(label_summary["splits"]["train"]["source_alignable_gold_assignment_count"] == 5295, violations, "train_label_alignable_count_changed")
    require(label_summary["splits"]["dev"]["source_alignable_gold_assignment_count"] == 810, violations, "dev_label_alignable_count_changed")
    require(label_summary["splits"]["dev"]["gold_assignment_count"] == 845, violations, "dev_label_gold_count_changed")
    require(label_summary["splits"]["dev"]["samples_with_non_source_alignable_gold"] == 33, violations, "dev_label_nonalignable_samples_changed")
    literal_occurrences = literal_gold_occurrence_audit(root)
    require(label_spec.get("literal_gold_string_multi_occurrence_audit") == literal_occurrences, violations, "literal_multi_occurrence_audit_mismatch")
    require(literal_occurrences["splits"]["train"]["literal_gold_string_multi_occurrence_assignment_count"] == 144, violations, "train_literal_multi_occurrence_count_changed")
    require(literal_occurrences["splits"]["dev"]["literal_gold_string_multi_occurrence_assignment_count"] == 36, violations, "dev_literal_multi_occurrence_count_changed")
    require(label_spec.get("matching_policy") == "maximum-cardinality bipartite matching from accepted predicted spans to gold assignments", violations, "label_matching_policy_changed")
    require(label_spec.get("one_to_one_constraints", {}).get("predicted_span_max_gold_assignments") == 1, violations, "predicted_span_not_one_to_one")
    require(label_spec.get("one_to_one_constraints", {}).get("gold_assignment_max_predicted_spans") == 1, violations, "gold_assignment_not_one_to_one")
    checks["label_alignment_manifest_recomputed"] = True

    phase_m = read_json(output_dir / "PHASE_M_INPUT_SPEC.json")
    require(phase_m.get("model_call_count") == 1, violations, "phase_m_model_call_count_changed")
    require(phase_m.get("slot_inventory_source") == "accepted Phase O spans only", violations, "phase_m_slot_source_changed")
    require(phase_m.get("all_phase_o_slots_required") is True, violations, "phase_m_slots_not_required")
    require("no oracle substitution" in phase_m.get("when_phase_o_is_wrong", ""), violations, "phase_m_allows_oracle_substitution")
    require("gold_spans" in phase_m.get("forbidden_inputs", []), violations, "phase_m_gold_spans_not_forbidden")
    checks["phase_m_protocol_checked"] = True

    oracle = read_json(output_dir / "ORACLE_SPAN_DIAGNOSTIC_PROTOCOL.json")
    require(oracle.get("diagnostic_only") is True, violations, "oracle_not_diagnostic_only")
    require(oracle.get("oracle_spans_used_for_training_or_selection") is False, violations, "oracle_spans_used_for_selection")
    require(oracle.get("p_value_baseline_allowed") is False, violations, "oracle_pvalue_allowed")
    require(oracle.get("dev_source_selectable_gold_values") == 810, violations, "oracle_dev_source_count_changed")
    require(oracle.get("source_span_label_manifest") == "SOURCE_SPAN_LABEL_MANIFEST.jsonl", violations, "oracle_label_manifest_missing")
    require(oracle.get("source_span_label_manifest_sha256") == sha256_file(output_dir / "SOURCE_SPAN_LABEL_MANIFEST.jsonl"), violations, "oracle_label_manifest_hash_mismatch")
    checks["oracle_diagnostic_checked"] = True

    generation = read_json(output_dir / "GENERATION_PROTOCOL_A1.json")
    require(generation.get("model_config") == FROZEN_MODEL_CONFIG, violations, "generation_model_config_changed")
    require(generation.get("phase_o_model_calls") == 1, violations, "generation_phase_o_calls_changed")
    require(generation.get("phase_m_model_calls") == 1, violations, "generation_phase_m_calls_changed")
    require(generation.get("total_model_calls") == 2, violations, "generation_total_calls_changed")
    require(generation.get("hidden_third_llm_call_allowed") is False, violations, "hidden_third_call_allowed")
    require(generation["model_config"]["phase_o_max_new_tokens"] == 512, violations, "phase_o_capacity_not_512")
    require(generation["model_config"]["phase_m_max_new_tokens"] == 8192, violations, "phase_m_capacity_not_8192")
    require(generation["model_config"]["model_revision"] == "c03e6d358207e414f1eca0bb1891e29f1db0e242", violations, "model_revision_changed")
    require(generation.get("prompt_serialization_spec_sha256") == sha256_file(output_dir / "PROMPT_SERIALIZATION_SPEC.json"), violations, "prompt_serialization_spec_hash_mismatch")
    require(generation.get("chat_template_rendering_spec_sha256") == sha256_file(output_dir / "CHAT_TEMPLATE_RENDERING_SPEC.json"), violations, "chat_template_rendering_spec_hash_mismatch")
    require(generation.get("tokenizer_config_file_sha256_for_chat_template") == FROZEN_MODEL_CONFIG["tokenizer_config_file_sha256"], violations, "tokenizer_config_hash_changed")
    for key in ("model_called", "gpu_called", "experiment_run"):
        require(generation.get(key) is False, violations, f"generation_flag_not_false:{key}")
    checks["generation_protocol_checked"] = True

    leak = read_json(output_dir / "DATA_LEAKAGE_AUDIT_A1.json")
    recomputed_leak = leakage_audit(root)
    require(leak == recomputed_leak, violations, "leakage_audit_mismatch")
    require(leak.get("status") == "PASS", violations, "leakage_audit_not_pass")
    require(leak.get("gold_in_phase_o_input") is False, violations, "gold_in_phase_o")
    require(leak.get("gold_in_phase_m_input") is False, violations, "gold_in_phase_m")
    require(leak.get("oracle_spans_in_primary_v2") is False, violations, "oracle_spans_in_primary")
    checks["leakage_audit_recomputed"] = True

    reserved = read_json(output_dir / "RESERVED_BENCHMARK_POLICY.json")
    require(reserved.get("current_481_crudsql_create") == "post_hoc_only_not_selection", violations, "481_policy_changed")
    require(reserved.get("crudsql_update_delete") == "reserved_until_after_v2_a1_freeze", violations, "update_delete_not_reserved")
    require(reserved.get("livesqlbench_sqlite") == "untouched_external_no_gt_access", violations, "livesqlbench_policy_changed")
    require(reserved.get("live_sql_bench_gt_opened") is False, violations, "livesqlbench_opened")
    checks["reserved_policy_checked"] = True

    return {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "train_create_count": recomputed_counts["train_create_count"],
        "dev_create_count": recomputed_counts["dev_create_count"],
        "dev_source_span_oracle": recomputed_counts["source_span_oracle"]["dev_source_selectable"],
        "dev_source_span_oracle_denominator": recomputed_counts["source_span_oracle"]["dev_denominator"],
        "dev_nonalignable_samples": recomputed_counts["source_span_oracle"]["dev_samples_with_gap"],
        "model_called": False,
        "gpu_called": False,
        "v2_implemented": False,
        "experiment_run": False,
        "live_sql_bench_gt_opened": False,
        **checks,
    }


def report_text(report: dict[str, Any]) -> str:
    lines = [
        "# Stage7C-A1 Validation Report",
        "",
        f"Status: {report['status']}",
        "",
        f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}",
        "",
        f"train_create_count: {report.get('train_create_count')}",
        f"dev_create_count: {report.get('dev_create_count')}",
        f"dev_source_span_oracle: {report.get('dev_source_span_oracle')} / {report.get('dev_source_span_oracle_denominator')}",
        f"dev_nonalignable_samples: {report.get('dev_nonalignable_samples')}",
        "",
    ]
    for key in (
        "input_hashes_recomputed",
        "artifact_hashes_recomputed",
        "stage7b_a1_lock_checked",
        "stage7c_reuse_checked",
        "phase_o_prompt_checked",
        "prompt_serialization_checked",
        "chat_template_rendering_checked",
        "offset_guide_checked",
        "phase_o_validation_checked",
        "label_alignment_manifest_recomputed",
        "phase_m_protocol_checked",
        "oracle_diagnostic_checked",
        "generation_protocol_checked",
        "leakage_audit_recomputed",
        "reserved_policy_checked",
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
    report_path.write_text(report_text(report), encoding="utf-8")
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
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7c_a1_v2_development_protocol")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
