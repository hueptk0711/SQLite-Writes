#!/usr/bin/env python3
"""Build Stage7C-A1 V2 development protocol artifacts.

This stage freezes the A1 development protocol after Stage7B-A1. It does not
run the model, create Phase O or Phase M outputs, or evaluate V2 performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE = "Stage7C_A1_V2_DEVELOPMENT_PROTOCOL"
DATE = "20260827"
HASH_POLICY = "sha256_bytes_for_raw_inputs_text_sha256_canonical_lf_for_json_artifacts"
LOCK_FILE = "STAGE7C_A1_PROTOCOL_LOCK.json"
PASS_STATUS = "PASS_STAGE7C_A1_V2_DEVELOPMENT_PROTOCOL_LOCKED"

MODEL_CALLED = False
GPU_CALLED = False
V2_IMPLEMENTED = False
EXPERIMENT_RUN = False
LIVESQLBENCH_GT_OPENED = False

STAGE7B_A1_INPUTS = (
    "stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json",
    "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json",
    "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_SEMANTIC_SPAN_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/SPAN_VALIDATION_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/EVIDENCE_VS_SLOT_SEPARATION_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/COMPLETENESS_AMENDED_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/SOURCE_SPAN_ORACLE_AUDIT.json",
    "stage7b_a1_free_text_slot_discovery_amendment/NONALIGNABLE_SOURCE_SPAN_POLICY.json",
    "stage7b_a1_free_text_slot_discovery_amendment/GENERATION_CAPACITY_AMENDMENT.json",
    "stage7b_a1_free_text_slot_discovery_amendment/ABLATION_AMENDMENT.json",
)

REUSED_STAGE7C_INPUTS = (
    "stage7c_v2_development_data_protocol/STAGE7C_DATA_PROTOCOL_LOCK.json",
    "stage7c_v2_development_data_protocol/STAGE7C_INPUT_MANIFEST.json",
    "stage7c_v2_development_data_protocol/CRUDSQL_SOURCE_MANIFEST.json",
    "stage7c_v2_development_data_protocol/TRAIN_CREATE_MANIFEST.jsonl",
    "stage7c_v2_development_data_protocol/DEV_CREATE_MANIFEST.jsonl",
    "stage7c_v2_development_data_protocol/OPERATION_LABEL_MAPPING_SPEC.json",
    "stage7c_v2_development_data_protocol/GOLD_PROGRAM_DERIVATION_SPEC.json",
    "stage7c_v2_development_data_protocol/GOLD_PROGRAM_DERIVATION_AUDIT.json",
    "stage7c_v2_development_data_protocol/GOLD_POST_STATE_PROTOCOL.json",
    "stage7c_v2_development_data_protocol/SPLIT_CONTAMINATION_AUDIT.json",
    "stage7c_v2_development_data_protocol/RESERVED_BENCHMARK_POLICY.json",
    "stage7c_v2_development_data_protocol/GENERATION_PROTOCOL_SPEC.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/train/crud_train_sql.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/train/crud_train_table.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/train/train.db",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/dev/crud_dev_sql.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/dev/crud_dev_table.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/dev/dev.db",
)

ARTIFACTS = (
    "STAGE7C_A1_INPUT_MANIFEST.json",
    "REUSED_DATA_PROTOCOL_MANIFEST.json",
    "PHASE_O_INPUT_SPEC.json",
    "PHASE_O_PROMPT_SPEC.json",
    "QUESTION_OFFSET_GUIDE_SPEC.json",
    "PHASE_O_OUTPUT_VALIDATION_SPEC.json",
    "PHASE_O_EVALUATION_PROTOCOL.json",
    "PHASE_M_INPUT_SPEC.json",
    "PHASE_M_PROMPT_SPEC.json",
    "PHASE_M_EVALUATION_PROTOCOL.json",
    "ORACLE_SPAN_DIAGNOSTIC_PROTOCOL.json",
    "NONALIGNABLE_SAMPLE_POLICY.json",
    "GENERATION_PROTOCOL_A1.json",
    "DEV_SELECTION_PROTOCOL_A1.json",
    "DATA_LEAKAGE_AUDIT_A1.json",
    "RESERVED_BENCHMARK_POLICY.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)

FROZEN_MODEL_CONFIG = {
    "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "model_path": "/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "model_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "tokenizer_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "stage6i_model_manifest_sha256": "1fb55083eb25b22fc3a1b158ff8f716694e6662f607e6f1efea757158075b54c",
    "stage6i_final_protocol_sha256": "128e9c4c1a5d1228b728cffbb3d788165ba9e16c23df444dc7724a4ee9849ab8",
    "stage6i_protocol_amendment_sha256": "d0f6f7aee01b8da38b5ae50933cc327dd046bdae6d58d8f1557ecb1be0f18478",
    "stage6i_environment_manifest_sha256": "4e043eabc96da2bc38689f72ec32a63e9cf4ba987a388adb9d3b1af9135c46d1",
    "stage6i_inference_config_sha256": "06846f45097dc9510eed8124d446ec43c4eaac8df81eb6a87c539a7e12863484",
    "hf_model_config_sha256": "c0242402ad6a13b331ea320feea8c7e3776ffb7a4eff0757b9cd667e116d9a28",
    "hf_generation_config_sha256": "1a628a5775bc69cde01c6749a531150ca4d3189652c618a174f7077923acf3b1",
    "tokenizer_json_sha256": "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539",
    "tokenizer_config_sha256": "959e7f1d9a1b7641a6d6ce05ca97b75c7894fcb66cbe5a040406458fb1128ee4",
    "chat_template_sha256": "959e7f1d9a1b7641a6d6ce05ca97b75c7894fcb66cbe5a040406458fb1128ee4",
    "chat_template_source": "Stage6I Qwen tokenizer_config.json; standalone chat-template file was not frozen separately",
    "transformers_version": "5.5.3",
    "torch_version": "2.6.0+cu124",
    "torch_dtype": "auto",
    "do_sample": False,
    "temperature": 0.0,
    "top_p": 1.0,
    "seed_policy": "deterministic_decoding_no_sampling_seed_not_used",
    "stop_criteria": ["single_json_object_complete", "max_new_tokens"],
    "retry_count": 0,
    "phase_o_max_new_tokens": 512,
    "phase_m_max_new_tokens": 8192,
    "generation_timeout_seconds_per_phase": 120,
    "execution_timeout_seconds_per_sample": 30,
}

PHASE_O_SYSTEM_PROMPT = (
    "You select the SQLite write operation and atomic semantic value spans from the original request. "
    "Return one JSON object that satisfies the supplied schema. Use Python Unicode code-point offsets over the exact original question string."
)
PHASE_O_USER_PROMPT_TEMPLATE = (
    "Original question Q, unchanged:\n{question}\n\n"
    "Python code-point offset guide derived from Q:\n{offset_guide}\n\n"
    "Schema inventory:\n{schema_inventory}\n\n"
    "Return JSON with operation and value_spans only. Do not generate SPAN, EV, SLOT ids or value text."
)
PHASE_M_SYSTEM_PROMPT = (
    "You map accepted required semantic SLOT ids to database columns or predicates under the predicted operation. "
    "Use only supplied inventory ids."
)
PHASE_M_USER_PROMPT_TEMPLATE = (
    "Predicted operation:\n{operation}\n\n"
    "Schema inventory:\n{schema_inventory}\n\n"
    "Evidence inventory from accepted Phase O spans:\n{evidence_inventory}\n\n"
    "Semantic slot inventory:\n{semantic_slot_inventory}\n\n"
    "Return the operation-specific slot-grounded IR."
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def input_hashes(root: Path = PROJECT_ROOT) -> dict[str, str]:
    hashes = {}
    for rel in STAGE7B_A1_INPUTS + REUSED_STAGE7C_INPUTS:
        path = root / rel
        if not path.is_file():
            raise FileNotFoundError(f"Missing Stage7C-A1 input: {rel}")
        hashes[rel] = sha256_file(path)
    return hashes


def artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {rel: sha256_file(output_dir / rel) for rel in ARTIFACTS}


def reset_output_dir(output_dir: Path, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise RuntimeError(f"{output_dir} exists; pass --force to rebuild.")
        resolved_output = output_dir.resolve()
        resolved_root = PROJECT_ROOT.resolve()
        if output_dir.name != "stage7c_a1_v2_development_protocol" or resolved_root not in resolved_output.parents:
            raise RuntimeError(f"Refusing to remove output outside Stage7C-A1 path: {output_dir}")
        for path in sorted(output_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        output_dir.rmdir()
    output_dir.mkdir(parents=True)


def offset_guide(question: str) -> str:
    return "\n".join(f"{index}\t{char}" for index, char in enumerate(question))


def prompt_hash_payload() -> dict[str, str]:
    return {
        "phase_o_system_prompt_sha256": sha256_text(PHASE_O_SYSTEM_PROMPT),
        "phase_o_user_prompt_template_sha256": sha256_text(PHASE_O_USER_PROMPT_TEMPLATE),
        "phase_m_system_prompt_sha256": sha256_text(PHASE_M_SYSTEM_PROMPT),
        "phase_m_user_prompt_template_sha256": sha256_text(PHASE_M_USER_PROMPT_TEMPLATE),
    }


def count_reused_data(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    train_rows = read_jsonl(root / "stage7c_v2_development_data_protocol/TRAIN_CREATE_MANIFEST.jsonl")
    dev_rows = read_jsonl(root / "stage7c_v2_development_data_protocol/DEV_CREATE_MANIFEST.jsonl")
    old_lock = read_json(root / "stage7c_v2_development_data_protocol/STAGE7C_DATA_PROTOCOL_LOCK.json")
    gold_audit = read_json(root / "stage7c_v2_development_data_protocol/GOLD_PROGRAM_DERIVATION_AUDIT.json")
    contamination = read_json(root / "stage7c_v2_development_data_protocol/SPLIT_CONTAMINATION_AUDIT.json")
    operation_mapping = read_json(root / "stage7c_v2_development_data_protocol/OPERATION_LABEL_MAPPING_SPEC.json")
    oracle = read_json(root / "stage7b_a1_free_text_slot_discovery_amendment/SOURCE_SPAN_ORACLE_AUDIT.json")
    return {
        "train_create_count": len(train_rows),
        "dev_create_count": len(dev_rows),
        "old_stage7c_status": old_lock.get("status"),
        "gold_derivation": {
            "train_pass": gold_audit["splits"]["train"]["gold_derivation_pass_count"],
            "train_failures": gold_audit["splits"]["train"]["gold_execution_failure_count"],
            "dev_pass": gold_audit["splits"]["dev"]["gold_derivation_pass_count"],
            "dev_failures": gold_audit["splits"]["dev"]["gold_execution_failure_count"],
        },
        "operation_mapping_type0": operation_mapping["mapping"]["0"]["v2_operation"],
        "contamination": {
            "train_dev_question_hash_overlap": contamination["train_dev_question_hash_overlap"],
            "train_481_question_hash_overlap": contamination["train_481_question_hash_overlap"],
            "dev_481_question_hash_overlap": contamination["dev_481_question_hash_overlap"],
            "train_dev_table_id_overlap": contamination["train_dev_table_id_overlap"],
            "train_confirmation_table_id_overlap": contamination["train_confirmation_table_id_overlap"],
            "dev_confirmation_table_id_overlap": contamination["dev_confirmation_table_id_overlap"],
            "train_table_id_count": contamination["train_table_id_count"],
            "dev_table_id_count": contamination["dev_table_id_count"],
            "confirmation_table_id_count": contamination["confirmation_table_id_count"],
        },
        "source_span_oracle": {
            "train_source_selectable": oracle["train"]["source_selectable_gold_value_count"],
            "train_denominator": oracle["train"]["gold_assignment_count"],
            "train_samples_with_gap": oracle["train"]["samples_with_at_least_one_non_source_alignable_value"],
            "dev_source_selectable": oracle["dev"]["source_selectable_gold_value_count"],
            "dev_denominator": oracle["dev"]["gold_assignment_count"],
            "dev_samples_with_gap": oracle["dev"]["samples_with_at_least_one_non_source_alignable_value"],
        },
    }


def leakage_audit(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    forbidden = {"operation_label", "gold_sql", "crudsql_sql", "conds", "sel", "agg", "target_state", "post_state_hash", "gold_program", "gold_post_state"}
    rows = read_jsonl(root / "stage7c_v2_development_data_protocol/TRAIN_CREATE_MANIFEST.jsonl") + read_jsonl(
        root / "stage7c_v2_development_data_protocol/DEV_CREATE_MANIFEST.jsonl"
    )
    model_side_violations = []
    for row in rows:
        model_side = row.get("model_side_input", {})
        present = sorted(forbidden.intersection(model_side.keys()))
        if present:
            model_side_violations.append({"sample_id": row["sample_id"], "forbidden_fields": present})
    return {
        "stage": STAGE,
        "status": "PASS" if not model_side_violations else "FAIL",
        "sample_count": len(rows),
        "phase_o_input_sources": ["model_side_input.question", "model_side_input.schema_inventory", "deterministic_offset_guide_from_exact_question"],
        "phase_m_input_sources": ["predicted_phase_o_operation", "schema_inventory", "phase_o_accepted_evidence_inventory", "phase_o_accepted_semantic_slot_inventory"],
        "old_stage7c_regex_semantic_slot_inventory_status": "superseded_not_model_side_input_for_v2_a1_primary",
        "forbidden_model_side_fields": sorted(forbidden),
        "model_side_violation_count": len(model_side_violations),
        "example_violations": model_side_violations[:10],
        "gold_in_phase_o_input": False,
        "gold_in_phase_m_input": False,
        "oracle_spans_in_primary_v2": False,
        "hidden_third_llm_call": False,
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
    }


def static_artifacts(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    counts = count_reused_data(root)
    phase_o_schema_hash = sha256_file(root / "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json")
    span_validation_hash = sha256_file(root / "stage7b_a1_free_text_slot_discovery_amendment/SPAN_VALIDATION_SPEC.json")
    prompt_hashes = prompt_hash_payload()
    return {
        "REUSED_DATA_PROTOCOL_MANIFEST.json": {
            "stage": STAGE,
            "reused_from": "Stage7C_V2_DEVELOPMENT_DATA_PROTOCOL_PATCH2",
            "reused_without_modification": [
                "CRUDSQL source freeze",
                "train/dev Create manifests",
                "gold INSERT derivation",
                "gold post-state protocol",
                "Create/type0 to INSERT mapping",
                "question/table contamination audit",
                "reserved benchmark policy",
            ],
            "superseded_for_v2_a1_primary": ["deterministic regex semantic_slot_inventory", "required_optional_regex_confidence"],
            "counts": counts,
        },
        "PHASE_O_INPUT_SPEC.json": {
            "stage": STAGE,
            "phase": "Phase O",
            "model_call_count": 1,
            "input_fields": ["exact_original_question", "schema_inventory", "question_offset_guide", "phase_o_json_schema"],
            "exact_original_question_preserved": True,
            "question_normalization_before_prompt": "none",
            "schema_inventory_source": "reused Stage7C model_side_input.schema_inventory",
            "offset_guide_source": "deterministically derived from exact_original_question using Python code-point indices",
            "excluded_stage7c_fields": ["semantic_slot_inventory", "label_side_bookkeeping", "operation_label_for_evaluation_only", "v2_gold_operation_for_evaluation_only"],
            "forbidden_inputs": ["gold_sql", "sql.conds", "gold_operation", "gold_program", "gold_post_state", "481_test_labels", "LiveSQLBench_ground_truth"],
        },
        "PHASE_O_PROMPT_SPEC.json": {
            "stage": STAGE,
            "phase": "Phase O",
            "system_prompt": PHASE_O_SYSTEM_PROMPT,
            "user_prompt_template": PHASE_O_USER_PROMPT_TEMPLATE,
            "prompt_hashes": prompt_hashes,
            "schema_sha256": phase_o_schema_hash,
            "few_shot_policy": "zero_shot_no_examples_before_stage7d_unless_formally_amended_before_generation",
            "json_schema_version": "Stage7B-A1 PHASE_O_JSON_SCHEMA.json",
            "character_offset_instructions": "Use Python Unicode code-point offsets on exact Q; [start_char, end_char); return offsets only.",
            "gold_visible": False,
        },
        "QUESTION_OFFSET_GUIDE_SPEC.json": {
            "stage": STAGE,
            "format": "one line per Python code point: '<zero_based_index>\\t<character>'",
            "example_question": "A北京B上海C",
            "example_offset_guide": offset_guide("A北京B上海C"),
            "coordinate_system": "Python Unicode code-point indexing",
            "range_convention": "[start_char, end_char)",
            "normalization_before_guide": "none",
            "guide_is_deterministic": True,
            "guide_uses_gold": False,
        },
        "PHASE_O_OUTPUT_VALIDATION_SPEC.json": {
            "stage": STAGE,
            "phase": "Phase O",
            "json_schema_sha256": phase_o_schema_hash,
            "span_validation_spec_sha256": span_validation_hash,
            "validation_order": [
                "validate Stage7B-A1 JSON schema",
                "check offsets against exact original Q",
                "derive text as Q[start_char:end_char]",
                "reject duplicate exact offsets",
                "reject nested or partially overlapping spans",
                "sort by start_char then end_char",
                "assign SPAN_i, EV_i, SLOT_i deterministically",
            ],
            "empty_value_spans_policy": "reject",
            "model_generated_span_ids_policy": "reject",
            "model_generated_value_text_policy": "reject",
        },
        "PHASE_O_EVALUATION_PROTOCOL.json": {
            "stage": STAGE,
            "metrics": {
                "operation_accuracy": "predicted INSERT/UPDATE/DELETE/UPSERT vs label-side V2 operation",
                "exact_span_precision": "accepted predicted spans matching label-side source-alignable gold spans over predicted spans",
                "exact_span_recall_all_gold_values": "matched source spans over all label-side gold values",
                "exact_span_recall_source_alignable": "matched source spans over source-alignable gold values only",
                "spurious_span_rate": "accepted predicted spans not aligned to a label-side gold value",
                "invalid_offset_rate": "Phase O outputs rejected by offset/schema validation",
                "overlap_rejection_rate": "Phase O outputs rejected for duplicate/nested/overlap offsets",
            },
            "primary_end_to_end_denominator": "full train/dev Create denominator; non-alignable samples retained",
            "source_alignable_subset": "diagnostic only",
        },
        "PHASE_M_INPUT_SPEC.json": {
            "stage": STAGE,
            "phase": "Phase M",
            "model_call_count": 1,
            "input_fields": ["predicted_phase_o_operation", "schema_inventory", "phase_o_accepted_evidence_inventory", "phase_o_accepted_semantic_slot_inventory", "operation_specific_dynamic_schema"],
            "slot_inventory_source": "accepted Phase O spans only",
            "all_phase_o_slots_required": True,
            "when_phase_o_is_wrong": "Phase M receives the predicted Phase O artifacts; no oracle substitution in primary V2-A1",
            "forbidden_inputs": ["gold_spans", "gold_operation", "gold_columns", "sql.conds", "gold_program", "gold_post_state"],
        },
        "PHASE_M_PROMPT_SPEC.json": {
            "stage": STAGE,
            "phase": "Phase M",
            "system_prompt": PHASE_M_SYSTEM_PROMPT,
            "user_prompt_template": PHASE_M_USER_PROMPT_TEMPLATE,
            "prompt_hashes": prompt_hashes,
            "reference_constraint": "dynamic per-sample enum membership from Stage7B PATCH2/A1 specs",
            "gold_visible": False,
        },
        "PHASE_M_EVALUATION_PROTOCOL.json": {
            "stage": STAGE,
            "metrics": {
                "slot_to_column_grounding_accuracy": "label-side diagnostic against CRUDSQL cond column indices",
                "reference_violation_rate": "invalid TAB/COL/EV/SLOT ids after dynamic membership validation",
                "completeness_rejection_rate": "accepted Phase O required SLOT ids omitted or used extra by Phase M",
                "materialization_failure_rate": "accepted Phase M evidence cannot be typed under frozen materializer",
                "compiler_or_preflight_failure_rate": "deterministic SQLite compiler/preflight rejects the IR",
            },
            "primary_metric_remains": "Target-State Accuracy",
            "oracle_phase_o_substitution": "diagnostic only and never primary",
        },
        "ORACLE_SPAN_DIAGNOSTIC_PROTOCOL.json": {
            "stage": STAGE,
            "diagnostic_only": True,
            "primary_v2_output_source": "predicted Phase O spans only",
            "question": "If Phase M receives perfect source-alignable semantic spans, what mapping/materialization ceiling remains?",
            "allowed_splits": ["CRUDSQL train Create", "CRUDSQL dev Create"],
            "forbidden_splits": ["current 481 test", "LiveSQLBench"],
            "source_span_oracle_audit_sha256": sha256_file(root / "stage7b_a1_free_text_slot_discovery_amendment/SOURCE_SPAN_ORACLE_AUDIT.json"),
            "dev_source_selectable_gold_values": counts["source_span_oracle"]["dev_source_selectable"],
            "dev_gold_value_denominator": counts["source_span_oracle"]["dev_denominator"],
            "oracle_spans_used_for_training_or_selection": False,
            "p_value_baseline_allowed": False,
        },
        "NONALIGNABLE_SAMPLE_POLICY.json": {
            "stage": STAGE,
            "source": "Stage7B-A1 NONALIGNABLE_SOURCE_SPAN_POLICY.json",
            "diagnostic_flag": "source_gold_nonalignable_under_frozen_materializer",
            "dev_samples_with_gap": counts["source_span_oracle"]["dev_samples_with_gap"],
            "train_samples_with_gap": counts["source_span_oracle"]["train_samples_with_gap"],
            "retain_in_primary_train_denominator": True,
            "retain_in_primary_dev_denominator": True,
            "eligible": True,
            "exclude_after_model_performance": False,
            "modify_gold": False,
            "add_post_hoc_normalization": False,
        },
        "GENERATION_PROTOCOL_A1.json": {
            "stage": STAGE,
            "model_config": FROZEN_MODEL_CONFIG,
            "phase_o_prompt_sha256": prompt_hashes["phase_o_user_prompt_template_sha256"],
            "phase_m_prompt_sha256": prompt_hashes["phase_m_user_prompt_template_sha256"],
            "phase_o_json_schema_sha256": phase_o_schema_hash,
            "chat_template_sha256": FROZEN_MODEL_CONFIG["chat_template_sha256"],
            "phase_o_model_calls": 1,
            "phase_m_model_calls": 1,
            "total_model_calls": 2,
            "hidden_third_llm_call_allowed": False,
            "model_called": MODEL_CALLED,
            "gpu_called": GPU_CALLED,
            "experiment_run": EXPERIMENT_RUN,
        },
        "DEV_SELECTION_PROTOCOL_A1.json": {
            "stage": STAGE,
            "train_split": "CRUDSQL train Create",
            "selection_split": "CRUDSQL dev Create",
            "primary_metric": "Target-State Accuracy",
            "phase_o_metrics_are_diagnostic": True,
            "phase_m_metrics_are_diagnostic": True,
            "current_481_test": "post_hoc_only_not_selection",
            "selection_after_481_forbidden": True,
            "prompt_or_capacity_change_after_dev_generation": "formal_amendment_required_before_any_selection",
        },
        "RESERVED_BENCHMARK_POLICY.json": {
            "stage": STAGE,
            "current_481_crudsql_create": "post_hoc_only_not_selection",
            "crudsql_update_delete": "reserved_until_after_v2_a1_freeze",
            "livesqlbench_sqlite": "untouched_external_no_gt_access",
            "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
        },
    }


def validation_report_text(status: str = "PENDING_VALIDATION", violations: list[str] | None = None) -> str:
    return "# Stage7C-A1 Validation Report\n\n" + f"Status: {status}\n\nviolations: {json.dumps(violations or [], ensure_ascii=False, sort_keys=True)}\n"


def reviewer_readme() -> str:
    return """# Stage7C-A1 V2 Development Protocol

This package freezes the V2-A1 development protocol after Stage7B-A1. It reuses
the previously validated CRUDSQL source, train/dev Create manifests, gold INSERT
derivation, operation mapping, and contamination audits, while replacing the
superseded deterministic regex semantic-slot protocol with Phase O grounded
offset span selection.

Commands:
```bash
python scripts/data/build_stage7c_a1_v2_development_protocol.py --force
python scripts/data/validate_stage7c_a1_v2_development_protocol.py
python scripts/data/audit_stage7c_a1_leakage.py
python -m pytest -q tests/test_stage7c_a1_v2_development_protocol.py
```

No Qwen generation, GPU call, V2 implementation, experiment, 481-test tuning, or
LiveSQLBench ground-truth access is performed in this stage.
"""


def lock(output_dir: Path, inputs: dict[str, str]) -> dict[str, Any]:
    reused = read_json(output_dir / "REUSED_DATA_PROTOCOL_MANIFEST.json")
    generation = read_json(output_dir / "GENERATION_PROTOCOL_A1.json")
    return {
        "stage": STAGE,
        "status": "BUILT_PENDING_VALIDATION",
        "date": DATE,
        "hash_policy": HASH_POLICY,
        "input_hashes": inputs,
        "artifact_hashes": artifact_hashes(output_dir),
        "stage7b_a1_locked": True,
        "reused_stage7c_patch2_data": True,
        "train_create_count": reused["counts"]["train_create_count"],
        "dev_create_count": reused["counts"]["dev_create_count"],
        "phase_o_model_calls": 1,
        "phase_m_model_calls": 1,
        "total_model_calls": 2,
        "phase_o_max_new_tokens": generation["model_config"]["phase_o_max_new_tokens"],
        "phase_m_max_new_tokens": generation["model_config"]["phase_m_max_new_tokens"],
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
        "experiment_run": EXPERIMENT_RUN,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
    }


def build_stage7c_a1(output_dir: Path = PROJECT_ROOT / "stage7c_a1_v2_development_protocol", *, force: bool = False) -> dict[str, Any]:
    reset_output_dir(output_dir, force)
    inputs = input_hashes()
    write_json(output_dir / "STAGE7C_A1_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "hash_policy": HASH_POLICY, "input_hashes": inputs})
    for rel, payload in static_artifacts().items():
        write_json(output_dir / rel, payload)
    write_json(output_dir / "DATA_LEAKAGE_AUDIT_A1.json", leakage_audit())
    (output_dir / "VALIDATION_REPORT.md").write_text(validation_report_text(), encoding="utf-8")
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    write_json(output_dir / LOCK_FILE, lock(output_dir, inputs))
    counts = count_reused_data()
    return {
        "stage": STAGE,
        "status": "PASS_BUILT",
        "train_create": counts["train_create_count"],
        "dev_create": counts["dev_create_count"],
        "phase_o_max_new_tokens": FROZEN_MODEL_CONFIG["phase_o_max_new_tokens"],
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7c_a1_v2_development_protocol")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_stage7c_a1(args.output_dir, force=args.force), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
