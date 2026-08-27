#!/usr/bin/env python3
"""Validate Stage7C V2 development/data protocol artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7c_v2_development_data_protocol import (
    ARTIFACTS,
    CRUDSQL_COMMIT,
    EXPECTED_CREATE_COUNTS,
    EXPECTED_TABLE_COUNTS,
    FROZEN_GENERATION_CONFIG,
    LOCK_FILE,
    OPERATION_LABEL_MAPPING,
    RAW_ARTIFACTS,
    RAW_SOURCE_RELS,
    STAGE,
    STAGE6_TEST_INPUTS,
    STAGE7B_INPUTS,
    canonical_json,
    evidence_and_slots,
    gold_program_derivation_audit,
    gold_insert_program,
    read_json,
    read_jsonl,
    semantic_slot_derivation_audit,
    sha256_file,
    sha256_text,
    source_split_counts,
)


HASH_POLICY = "sha256_bytes_for_raw_files_text_sha256_canonical_lf_for_json_artifacts"
PASS_STATUS = "PASS_STAGE7C_DATA_PROTOCOL_LOCKED"
FORBIDDEN_MODEL_SIDE_KEYS = {
    "operation",
    "operation_label",
    "gold",
    "gold_sql",
    "crudsql_sql",
    "conds",
    "sel",
    "agg",
    "target_state",
    "post_state_hash",
    "dev_metric",
}


def require(condition: bool, violations: list[str], code: str) -> None:
    if not condition:
        violations.append(code)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def input_hashes(root: Path, violations: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in STAGE7B_INPUTS + STAGE6_TEST_INPUTS:
        path = root / rel
        if not path.is_file():
            violations.append(f"missing_input:{rel}")
            continue
        hashes[rel] = sha256_file(path)
    return hashes


def sqlite_integrity(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return str(con.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        con.close()


def artifact_hashes(output_dir: Path, violations: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in (*ARTIFACTS, *RAW_ARTIFACTS):
        path = output_dir / rel
        if not path.is_file():
            violations.append(f"missing_artifact:{rel}")
            continue
        hashes[rel] = sha256_file(path)
    return hashes


def forbidden_keys_present(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in FORBIDDEN_MODEL_SIDE_KEYS:
                found.add(key)
            found |= forbidden_keys_present(nested)
    elif isinstance(value, list):
        for item in value:
            found |= forbidden_keys_present(item)
    return found


def validate_manifest_rows(rows: list[dict[str, Any]], split: str, raw_rows: list[dict[str, Any]], tables: dict[str, dict[str, Any]], violations: list[str]) -> dict[str, Any]:
    ids = [row.get("sample_id") for row in rows]
    question_hashes = [row.get("question_sha256") for row in rows]
    require(len(ids) == len(set(ids)), violations, f"{split}_sample_ids_not_unique")
    require(len(question_hashes) == len(set(question_hashes)), violations, f"{split}_question_hashes_not_unique")
    for index, row in enumerate(rows):
        prefix = f"{split}:{index}:{row.get('sample_id')}"
        require(row.get("split") == split, violations, f"{prefix}:split_mismatch")
        require(row.get("source_commit") == CRUDSQL_COMMIT, violations, f"{prefix}:commit_mismatch")
        require(row.get("operation_label_for_evaluation_only") == "CREATE", violations, f"{prefix}:operation_label_not_create")
        require(row.get("v2_gold_operation_for_evaluation_only") == "INSERT", violations, f"{prefix}:v2_gold_operation_not_insert")
        require(row.get("operation_label_visible_to_phase_o") is False, violations, f"{prefix}:operation_label_visible")
        require(row.get("model_side_input_fields") == ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"], violations, f"{prefix}:model_fields_changed")
        require(row.get("semantic_slot_inventory_derivation_inputs") == ["question"], violations, f"{prefix}:slot_derivation_not_question_only")
        label = row.get("label_side_bookkeeping", {})
        require(label.get("crudsql_type") == 0, violations, f"{prefix}:label_not_create_type0")
        require(label.get("crudsql_operation_label") == "Create", violations, f"{prefix}:crudsql_label_not_create")
        require(label.get("v2_gold_operation") == "INSERT", violations, f"{prefix}:label_v2_operation_not_insert")
        require(label.get("gold_sql_or_structured_annotation_visible_to_model") is False, violations, f"{prefix}:gold_visible_to_model")
        model_side = row.get("model_side_input", {})
        require(row.get("model_side_input_sha256") == sha256_text(canonical_json(model_side)), violations, f"{prefix}:model_side_hash_mismatch")
        require(not forbidden_keys_present(model_side), violations, f"{prefix}:forbidden_model_key_present")
        require(model_side.get("question") == row.get("question"), violations, f"{prefix}:question_mismatch")
        raw = raw_rows[row["source_sql_index"]]
        table = tables[raw["table_id"]]
        expected_evidence, expected_slots = evidence_and_slots(row.get("question", ""), table)
        require(model_side.get("evidence_inventory") == expected_evidence, violations, f"{prefix}:evidence_not_recomputed")
        require(model_side.get("semantic_slot_inventory") == expected_slots, violations, f"{prefix}:slots_not_recomputed")
        require(label.get("gold_assignment_count") == len(raw["sql"].get("conds", [])), violations, f"{prefix}:gold_assignment_count_mismatch")
        require(label.get("gold_insert_program_sha256") == sha256_text(canonical_json(gold_insert_program(raw, table))), violations, f"{prefix}:gold_program_hash_mismatch")
        slots = model_side.get("semantic_slot_inventory", {}).get("slots", [])
        evidence_refs = {entry.get("evidence_ref") for entry in model_side.get("evidence_inventory", {}).get("evidence", [])}
        require(all(slot.get("evidence_ref") in evidence_refs for slot in slots), violations, f"{prefix}:slot_evidence_missing")
        require(model_side.get("semantic_slot_inventory", {}).get("uses_gold_sql") is False, violations, f"{prefix}:slots_use_gold_sql")
        require(model_side.get("semantic_slot_inventory", {}).get("model_call_used") is False, violations, f"{prefix}:slot_model_call_used")
    return {"count": len(rows), "unique_sample_ids": len(set(ids)), "unique_question_hashes": len(set(question_hashes))}


def recompute_contamination(root: Path, train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, int]:
    test_rows = read_jsonl(root / STAGE6_TEST_INPUTS[0])
    train_hashes = {row["question_sha256"] for row in train_rows}
    dev_hashes = {row["question_sha256"] for row in dev_rows}
    test_hashes = {row["input_text_sha256"] for row in test_rows}
    train_tables = {row["table_id"] for row in train_rows}
    dev_tables = {row["table_id"] for row in dev_rows}
    confirmation_tables = {row["table_id"] for row in test_rows}
    return {
        "train_dev_question_hash_overlap": len(train_hashes & dev_hashes),
        "train_481_question_hash_overlap": len(train_hashes & test_hashes),
        "dev_481_question_hash_overlap": len(dev_hashes & test_hashes),
        "train_dev_sample_id_overlap": len({row["sample_id"] for row in train_rows} & {row["sample_id"] for row in dev_rows}),
        "train_dev_table_id_overlap": len(train_tables & dev_tables),
        "train_confirmation_table_id_overlap": len(train_tables & confirmation_tables),
        "dev_confirmation_table_id_overlap": len(dev_tables & confirmation_tables),
    }


def validate(output_dir: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    violations: list[str] = []
    checks = {
        "input_hashes_recomputed": False,
        "raw_crudsql_hashes_recomputed": False,
        "train_dev_create_manifests_recomputed": False,
        "model_input_leakage_recomputed": False,
        "split_contamination_recomputed": False,
        "semantic_slot_derivation_audit_recomputed": False,
        "gold_program_derivation_audit_recomputed": False,
        "operation_mapping_validated": False,
        "generation_config_validated": False,
        "selection_policy_validated": False,
        "reserved_benchmarks_validated": False,
    }

    current_hashes = artifact_hashes(output_dir, violations)
    if violations:
        return {"stage": STAGE, "status": "FAIL", "violations": violations, "model_called": False, "gpu_called": False, **checks}

    inputs = input_hashes(root, violations)
    checks["input_hashes_recomputed"] = True
    manifest = read_json(output_dir / "STAGE7C_INPUT_MANIFEST.json")
    lock = read_json(output_dir / LOCK_FILE)
    require(manifest.get("hash_policy") == HASH_POLICY, violations, "input_manifest_hash_policy_mismatch")
    require(lock.get("hash_policy") == HASH_POLICY, violations, "lock_hash_policy_mismatch")
    require(manifest.get("input_hashes") == inputs, violations, "input_manifest_hashes_mismatch")
    require(lock.get("input_hashes") == inputs, violations, "lock_input_hashes_mismatch")
    require(lock.get("artifact_hashes") == current_hashes, violations, "lock_artifact_hashes_mismatch")
    require(lock.get("status") in {"BUILT_PENDING_VALIDATION", PASS_STATUS}, violations, "lock_status_invalid")

    stage7b_lock = read_json(root / "stage7b_v2_method_specification" / "STAGE7B_V2_SPECIFICATION_LOCK.json")
    require(stage7b_lock.get("status") == "PASS_V2_METHOD_SPECIFICATION_LOCKED", violations, "stage7b_not_pass_locked")
    require(stage7b_lock.get("v2_implemented") is False, violations, "stage7b_v2_implemented")
    require(stage7b_lock.get("model_called") is False and stage7b_lock.get("gpu_called") is False, violations, "stage7b_model_or_gpu_called")

    source_manifest = read_json(output_dir / "CRUDSQL_SOURCE_MANIFEST.json")
    require(source_manifest.get("source", {}).get("commit") == CRUDSQL_COMMIT, violations, "crudsql_commit_mismatch")
    require(source_manifest.get("included_splits") == ["train", "dev"], violations, "included_splits_not_train_dev")
    require(source_manifest.get("excluded_splits") == ["test"], violations, "test_split_not_excluded")
    require(not (output_dir / "upstream_crudsql" / "data" / "test").exists(), violations, "test_split_copied_into_stage7c")
    raw_entries = {entry.get("source_path"): entry for entry in source_manifest.get("files", [])}
    for rel in RAW_SOURCE_RELS:
        entry = raw_entries.get(rel, {})
        require(entry.get("sha256") == sha256_file(output_dir / "upstream_crudsql" / rel), violations, f"raw_source_hash_mismatch:{rel}")
    for split in ("train", "dev"):
        require(sqlite_integrity(output_dir / "upstream_crudsql" / "data" / split / f"{split}.db") == "ok", violations, f"{split}_db_integrity_not_ok")
    checks["raw_crudsql_hashes_recomputed"] = True

    source_counts = {split: source_split_counts(output_dir, split) for split in ("train", "dev")}
    require(source_counts["train"]["total_records"] == 7040, violations, "train_total_not_7040")
    require(source_counts["dev"]["total_records"] == 960, violations, "dev_total_not_960")
    require(source_counts["train"]["create_type0_count"] == EXPECTED_CREATE_COUNTS["train"], violations, "train_create_source_count_not_1760")
    require(source_counts["dev"]["create_type0_count"] == EXPECTED_CREATE_COUNTS["dev"], violations, "dev_create_source_count_not_240")

    train_rows = read_jsonl(output_dir / "TRAIN_CREATE_MANIFEST.jsonl")
    dev_rows = read_jsonl(output_dir / "DEV_CREATE_MANIFEST.jsonl")
    train_raw = read_json(output_dir / "upstream_crudsql" / "data" / "train" / "crud_train_sql.json")
    dev_raw = read_json(output_dir / "upstream_crudsql" / "data" / "dev" / "crud_dev_sql.json")
    train_tables = {row["id"]: row for row in read_json(output_dir / "upstream_crudsql" / "data" / "train" / "crud_train_table.json")}
    dev_tables = {row["id"]: row for row in read_json(output_dir / "upstream_crudsql" / "data" / "dev" / "crud_dev_table.json")}
    train_checks = validate_manifest_rows(train_rows, "train", train_raw, train_tables, violations)
    dev_checks = validate_manifest_rows(dev_rows, "dev", dev_raw, dev_tables, violations)
    require(train_checks["count"] == EXPECTED_CREATE_COUNTS["train"], violations, "train_create_manifest_count_not_1760")
    require(dev_checks["count"] == EXPECTED_CREATE_COUNTS["dev"], violations, "dev_create_manifest_count_not_240")
    checks["train_dev_create_manifests_recomputed"] = True
    checks["model_input_leakage_recomputed"] = True

    eligibility = read_json(output_dir / "DATASET_ELIGIBILITY_AUDIT.json")
    require(eligibility.get("expected_official_create_counts") == EXPECTED_CREATE_COUNTS, violations, "eligibility_expected_counts_changed")
    require(eligibility.get("eligible_create_counts") == {"train": 1760, "dev": 240}, violations, "eligibility_counts_changed")
    require(eligibility.get("all_exclusions_method_agnostic") is True, violations, "eligibility_not_method_agnostic")

    contamination = read_json(output_dir / "SPLIT_CONTAMINATION_AUDIT.json")
    recomputed_contamination = recompute_contamination(root, train_rows, dev_rows)
    for key, value in recomputed_contamination.items():
        require(contamination.get(key) == value, violations, f"contamination_mismatch:{key}")
        require(value == 0, violations, f"contamination_nonzero:{key}")
    require(contamination.get("test_question_text_imported") is False, violations, "test_question_text_imported")
    require(contamination.get("model_input_leakage_status") == "PASS", violations, "model_input_leakage_status_not_pass")
    require(contamination.get("model_input_leakage_counts") == {}, violations, "model_input_leakage_counts_nonempty")
    require(contamination.get("train_table_id_count") == EXPECTED_TABLE_COUNTS["train"], violations, "train_table_count_not_440")
    require(contamination.get("dev_table_id_count") == EXPECTED_TABLE_COUNTS["dev"], violations, "dev_table_count_not_60")
    require(contamination.get("confirmation_table_id_count") == EXPECTED_TABLE_COUNTS["confirmation"], violations, "confirmation_table_count_not_121")
    require(contamination.get("train_dev_table_id_overlap") == 0, violations, "train_dev_table_overlap_nonzero")
    require(contamination.get("train_confirmation_table_id_overlap") == 0, violations, "train_confirmation_table_overlap_nonzero")
    require(contamination.get("dev_confirmation_table_id_overlap") == 0, violations, "dev_confirmation_table_overlap_nonzero")
    checks["split_contamination_recomputed"] = True

    policy = read_json(output_dir / "MODEL_INPUT_LEAKAGE_POLICY.json")
    require(policy.get("phase_o_must_predict_operation") is True, violations, "phase_o_operation_prediction_not_required")
    require(policy.get("slot_inventory_gold_sql_use_allowed") is False, violations, "slot_inventory_gold_sql_allowed")
    require("only row['model_side_input']" in policy.get("stage7d_prompt_input_contract", ""), violations, "stage7d_prompt_contract_missing")

    mapping = read_json(output_dir / "OPERATION_LABEL_MAPPING_SPEC.json")
    require(mapping.get("mapping") == OPERATION_LABEL_MAPPING, violations, "operation_label_mapping_changed")
    require(mapping.get("phase_o_dev_accuracy_gold_operation") == "INSERT", violations, "phase_o_gold_operation_not_insert")
    require(mapping.get("literal_create_compared_to_phase_o_output") is False, violations, "literal_create_compared_to_phase_o")
    checks["operation_mapping_validated"] = True

    slot_spec = read_json(output_dir / "SEMANTIC_SLOT_DERIVATION_SPEC.json")
    require(slot_spec.get("model_side_inputs") == ["question", "schema_inventory"], violations, "slot_derivation_model_inputs_changed")
    require("sql.conds" in slot_spec.get("forbidden_derivation_inputs", []), violations, "slot_derivation_conds_not_forbidden")
    require(slot_spec.get("model_call_used") is False, violations, "slot_derivation_model_call_used")
    slot_audit = read_json(output_dir / "SEMANTIC_SLOT_DERIVATION_AUDIT.json")
    require(slot_audit == semantic_slot_derivation_audit(output_dir, train_rows, dev_rows), violations, "semantic_slot_derivation_audit_mismatch")
    require(slot_audit.get("gold_used_for_model_side_inventory") is False, violations, "slot_audit_gold_model_side")
    require(slot_audit.get("gold_used_for_label_side_audit_only") is True, violations, "slot_audit_gold_not_label_side_only")
    checks["semantic_slot_derivation_audit_recomputed"] = True

    gold_spec = read_json(output_dir / "GOLD_PROGRAM_DERIVATION_SPEC.json")
    post_spec = read_json(output_dir / "GOLD_POST_STATE_PROTOCOL.json")
    require(gold_spec.get("compiler") == "INSERT INTO Table_<table_id>(col_i, ...) VALUES (?, ...)", violations, "gold_compiler_not_frozen")
    require(gold_spec.get("gold_visible_to_model") is False, violations, "gold_program_visible_to_model")
    require(post_spec.get("gold_visible_to_model") is False, violations, "gold_post_state_visible_to_model")
    gold_audit = read_json(output_dir / "GOLD_PROGRAM_DERIVATION_AUDIT.json")
    recomputed_gold_audit = gold_program_derivation_audit(output_dir, train_rows, dev_rows)
    require(gold_audit == recomputed_gold_audit, violations, "gold_program_derivation_audit_mismatch")
    require(gold_audit.get("status") == "PASS", violations, "gold_program_audit_not_pass")
    require(gold_audit.get("splits", {}).get("train", {}).get("gold_derivation_pass_count") == EXPECTED_CREATE_COUNTS["train"], violations, "train_gold_derivation_not_1760")
    require(gold_audit.get("splits", {}).get("dev", {}).get("gold_derivation_pass_count") == EXPECTED_CREATE_COUNTS["dev"], violations, "dev_gold_derivation_not_240")
    require(gold_audit.get("splits", {}).get("train", {}).get("gold_execution_failure_count") == 0, violations, "train_gold_execution_failures")
    require(gold_audit.get("splits", {}).get("dev", {}).get("gold_execution_failure_count") == 0, violations, "dev_gold_execution_failures")
    checks["gold_program_derivation_audit_recomputed"] = True

    generation = read_json(output_dir / "GENERATION_PROTOCOL_SPEC.json")
    require(generation.get("core_v2_max_model_calls") == 2, violations, "hidden_third_model_call_allowed")
    require(generation.get("semantic_slot_inventory_model_call_allowed") is False, violations, "slot_inventory_model_call_allowed")
    require(generation.get("v2_generation_run") is False, violations, "v2_generation_already_run")
    require(generation.get("config") == FROZEN_GENERATION_CONFIG, violations, "generation_config_not_frozen")
    require("to_be_frozen" not in json.dumps(generation, ensure_ascii=False).casefold(), violations, "generation_config_contains_tbd")
    environment = read_json(output_dir / "EVALUATION_ENVIRONMENT_SPEC.json")
    require(environment.get("execution_timeout_seconds_per_sample") == FROZEN_GENERATION_CONFIG["execution_timeout_seconds_per_sample"], violations, "execution_timeout_not_frozen")
    require("to_be_frozen" not in json.dumps(environment, ensure_ascii=False).casefold(), violations, "environment_contains_tbd")
    checks["generation_config_validated"] = True

    dev = read_json(output_dir / "DEV_SELECTION_PROTOCOL.json")
    require(dev.get("primary_metric") == "Target-State Accuracy", violations, "dev_primary_metric_changed")
    require(dev.get("selection_split") == "CRUDSQL dev Create", violations, "dev_selection_split_changed")
    require(dev.get("forbidden_selection_split") == "current 481 CRUDSQL Create test", violations, "481_not_forbidden_selection")
    checks["selection_policy_validated"] = True

    reserved = read_json(output_dir / "RESERVED_BENCHMARK_POLICY.json")
    require(reserved.get("current_481_crudsql_create") == "post_hoc_only_not_selection", violations, "481_policy_changed")
    require(reserved.get("crudsql_update_delete") == "reserved_until_after_v2_freeze", violations, "update_delete_not_reserved")
    require(reserved.get("livesqlbench_sqlite") == "untouched_external_no_gt_access", violations, "livesqlbench_policy_changed")
    require(reserved.get("live_sql_bench_gt_opened") is False, violations, "livesqlbench_gt_opened")
    checks["reserved_benchmarks_validated"] = True

    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        require(lock.get(key) is False, violations, f"lock_{key}_not_false")

    return {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "train_create_count": len(train_rows),
        "dev_create_count": len(dev_rows),
        "model_called": False,
        "gpu_called": False,
        "v2_implemented": False,
        "experiment_run": False,
        "live_sql_bench_gt_opened": False,
        **checks,
    }


def validation_report_text(report: dict[str, Any]) -> str:
    lines = [
        "# Stage7C Validation Report",
        "",
        f"Status: {report['status']}",
        "",
        f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}",
        "",
        f"train_create_count: {report.get('train_create_count')}",
        f"dev_create_count: {report.get('dev_create_count')}",
        "",
    ]
    for key in (
        "input_hashes_recomputed",
        "raw_crudsql_hashes_recomputed",
        "train_dev_create_manifests_recomputed",
        "model_input_leakage_recomputed",
        "split_contamination_recomputed",
        "semantic_slot_derivation_audit_recomputed",
        "gold_program_derivation_audit_recomputed",
        "operation_mapping_validated",
        "generation_config_validated",
        "selection_policy_validated",
        "reserved_benchmarks_validated",
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
    hashes = artifact_hashes(output_dir, [])
    hashes["VALIDATION_REPORT.md"] = sha256_file(report_path)
    lock["artifact_hashes"] = hashes
    write_json(lock_path, lock)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7c_v2_development_data_protocol")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
