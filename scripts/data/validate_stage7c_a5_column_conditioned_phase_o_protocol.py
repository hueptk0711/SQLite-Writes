#!/usr/bin/env python3
"""Validate Stage7C-A5 column-conditioned Phase O protocol artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nldbwrite_v3.v2_a1.inventories import FORBIDDEN_MODEL_SIDE_KEYS

from scripts.data.build_stage7c_a5_column_conditioned_phase_o_protocol import (
    MODEL_ID,
    MODEL_REVISION,
    PACKAGE_INTEGRITY_ARTIFACTS,
    PHASE_O_SYSTEM_PROMPT,
    PHASE_O_USER_PROMPT_TEMPLATE,
    QWEN_TOKENIZER_REVISION,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    STAGE7B_SELECTED_VARIANT,
    build_stage,
    canonical_json,
    logical_db_fixture_hash,
    oracle_column_conditioned_path,
    read_json,
    read_jsonl,
    render_phase_o_messages,
    sha256_file,
    sha256_text,
)


REQUIRED_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    *PACKAGE_INTEGRITY_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7C_A5_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]
REQUIRED_COVERAGE_TAGS = {
    "single_table",
    "multi_table",
    "oneOf",
    "3_assigned_columns",
    "4_assigned_columns",
    "5_assigned_columns",
    "true_omit",
    "many_nullable_columns",
    "quoted_multiword",
    "three_word_value",
    "overlapping_candidates",
    "email",
    "identifier",
    "hex_identifier",
    "date",
    "percent",
    "integer",
    "real",
    "text_numeric_mix",
}


def validate_prompt_and_schema(stage_dir: Path, failures: list[str]) -> None:
    prompt = read_json(stage_dir / "COLUMN_CONDITIONED_PROMPT_SPEC_A5_ENGLISH.json")
    output = read_json(stage_dir / "COLUMN_CONDITIONED_OUTPUT_SPEC_A5.json")
    runtime = read_json(stage_dir / "COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A5.json")
    serialization = read_json(stage_dir / "COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json")
    branching = read_json(stage_dir / "TARGET_TABLE_BRANCHING_PROTOCOL_A5.json")
    no_phase_m = read_json(stage_dir / "NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A5.json")
    failure_policy = read_json(stage_dir / "OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A5.json")
    evaluator = read_json(stage_dir / "EVALUATOR_SEMANTICS_A5.json")

    if prompt.get("system_prompt") != PHASE_O_SYSTEM_PROMPT:
        failures.append("prompt_system_text_mismatch")
    if prompt.get("user_prompt_template") != PHASE_O_USER_PROMPT_TEMPLATE:
        failures.append("prompt_user_template_mismatch")
    hashes = prompt.get("prompt_hashes", {})
    if hashes.get("phase_o_system_prompt_sha256") != sha256_text(PHASE_O_SYSTEM_PROMPT):
        failures.append("prompt_system_hash_mismatch")
    if hashes.get("phase_o_user_prompt_template_sha256") != sha256_text(PHASE_O_USER_PROMPT_TEMPLATE):
        failures.append("prompt_user_hash_mismatch")
    for key in ("zero_shot", "model_selects_table_ref", "model_selects_column_span_refs"):
        if prompt.get(key) is not True:
            failures.append(f"prompt_{key}_not_true")
    for key in ("model_generates_character_offsets", "model_generates_values", "model_generates_free_length_span_set", "model_generates_slot_refs", "model_generates_phase_m"):
        if prompt.get(key) is not False:
            failures.append(f"prompt_{key}_not_false")
    if prompt.get("examples") != [] or prompt.get("retry") != 0 or prompt.get("repair") != "none":
        failures.append("prompt_examples_retry_repair_changed")
    if prompt.get("model_id") != MODEL_ID or prompt.get("model_revision") != MODEL_REVISION:
        failures.append("prompt_model_lock_mismatch")

    if output.get("allowed_top_level_keys") != ["operation", "table_ref", "column_span_refs"]:
        failures.append("output_allowed_keys_mismatch")
    if output.get("non_omit_span_refs_unique_across_columns") is not True:
        failures.append("output_unique_non_omit_span_refs_missing")
    forbidden = set(output.get("forbidden_top_level_keys", []))
    if not {"span_refs", "value_spans", "start_char", "end_char", "values", "assignments", "slot_refs", "phase_m"} <= forbidden:
        failures.append("output_forbidden_keys_incomplete")
    if runtime.get("unknown_span_refs_structurally_impossible") is not True:
        failures.append("runtime_unknown_refs_not_structurally_impossible")
    if runtime.get("static_pattern_fallback_allowed") is not False:
        failures.append("runtime_static_pattern_fallback_allowed")
    if runtime.get("type_based_candidate_pruning_enabled") is not False:
        failures.append("runtime_type_pruning_enabled")
    if branching.get("runtime_target_table_gold_blind") is not True or branching.get("gold_sql_used_for_runtime_target_derivation") is not False:
        failures.append("branching_gold_blind_mismatch")
    if no_phase_m.get("primary_pipeline_phase_m_removed") is not True:
        failures.append("no_phase_m_not_locked")
    if no_phase_m.get("model_generates_phase_m") is not False or no_phase_m.get("model_generates_slot_refs") is not False:
        failures.append("no_phase_m_model_fields_not_false")
    if serialization.get("line_template") != "SPAN_0001 | TAG[,TAG...] | exact source text":
        failures.append("serialization_line_template_mismatch")
    if serialization.get("model_hidden_fields") != ["start_char", "end_char", "provenance_tags"]:
        failures.append("serialization_hidden_fields_mismatch")
    if "start_char" in serialization.get("model_visible_fields", []) or "end_char" in serialization.get("model_visible_fields", []):
        failures.append("serialization_exposes_offsets")
    if failure_policy.get("omit_allowed_for_candidate_miss") is not False or failure_policy.get("candidate_miss_is_method_failure") is not True:
        failures.append("candidate_miss_policy_mismatch")
    if failure_policy.get("duplicate_span_reuse_is_method_failure") is not True:
        failures.append("duplicate_span_reuse_policy_missing")
    if evaluator.get("column_span_refs_mapping_equality") != "order_insensitive_by_object_key":
        failures.append("evaluator_mapping_equality_not_order_insensitive")
    if evaluator.get("duplicate_span_reuse_outcome") != "method_failure":
        failures.append("evaluator_duplicate_span_reuse_outcome_mismatch")


def _schema_candidate_domain(schema: dict[str, Any]) -> list[str]:
    if "oneOf" in schema:
        first = schema["oneOf"][0]
        props = first["properties"]["column_span_refs"]["properties"]
    else:
        props = schema["properties"]["column_span_refs"]["properties"]
    first_col = next(iter(props.values()))
    return first_col["enum"]


def _required_columns(schema: dict[str, Any], table_ref: str) -> list[str]:
    if "oneOf" not in schema:
        return schema["properties"]["column_span_refs"]["required"]
    for branch in schema["oneOf"]:
        if branch["properties"]["table_ref"]["const"] == table_ref:
            return branch["properties"]["column_span_refs"]["required"]
    raise AssertionError(f"missing oneOf branch for {table_ref}")


def validate_rows(
    stage_dir: Path,
    failures: list[str],
    *,
    rows_name: str,
    oracle_name: str,
    expected_prefix: str,
    diagnostic: bool = False,
) -> tuple[int, int, int, int]:
    rows = read_jsonl(stage_dir / rows_name)
    oracle_rows = read_jsonl(stage_dir / oracle_name)
    oracle_by_id = {row["sample_id"]: row for row in oracle_rows}
    if len(rows) != 12:
        failures.append("fresh_case_count_mismatch")
    sample_ids = [row["sample_id"] for row in rows]
    if len(set(sample_ids)) != len(sample_ids):
        failures.append("duplicate_sample_id")
    if any(not str(sample_id).startswith(expected_prefix) or str(sample_id).startswith("gretel:") for sample_id in sample_ids):
        failures.append("non_fresh_or_gretel_sample_id_present")
    tags = {tag for row in rows for tag in row.get("coverage_tags", [])}
    missing_tags = sorted(REQUIRED_COVERAGE_TAGS - tags)
    if missing_tags:
        failures.append(f"coverage_tags_missing:{','.join(missing_tags)}")

    assigned_count = 0
    omit_count = 0
    multi_table_count = 0
    for row in rows:
        sample_id = row["sample_id"]
        if row.get("locked_before_model_run") is not True:
            failures.append(f"not_locked_before_model_run:{sample_id}")
        if row.get("fresh_synthetic") is not True:
            failures.append(f"fresh_synthetic_not_true:{sample_id}")
        if diagnostic and row.get("diagnostic_role") != "diagnostic_only_after_primary":
            failures.append(f"diagnostic_role_mismatch:{sample_id}")
        if set(row.get("model_side_input", {})) != {"question", "schema_inventory", "candidate_inventory_text"}:
            failures.append(f"model_side_input_keys_mismatch:{sample_id}")
        if FORBIDDEN_MODEL_SIDE_KEYS.intersection(row.get("model_side_input", {})):
            failures.append(f"model_side_gold_leakage:{sample_id}")
        if "start_char" in row["model_side_input"]["candidate_inventory_text"] or "end_char" in row["model_side_input"]["candidate_inventory_text"]:
            failures.append(f"candidate_inventory_text_exposes_offsets:{sample_id}")
        if row["label_side_expected"].get("model_side_visible") is not False:
            failures.append(f"label_side_visible_not_false:{sample_id}")

        phase_o = row["label_side_expected"]["phase_o"]
        if sorted(phase_o) != ["column_span_refs", "operation", "table_ref"]:
            failures.append(f"phase_o_keys_not_column_conditioned:{sample_id}")
        if phase_o.get("operation") != "INSERT":
            failures.append(f"phase_o_operation_not_insert:{sample_id}")
        if "span_refs" in phase_o or "assignments" in phase_o or "phase_m" in row["label_side_expected"]:
            failures.append(f"phase_o_or_label_side_contains_removed_phase_m_surface:{sample_id}")

        schema = row["runtime_constraints"]["phase_o_schema"]
        candidates = row["runtime_constraints"]["candidate_inventory"]
        candidate_refs = [candidate["span_ref"] for candidate in candidates]
        domain = _schema_candidate_domain(schema)
        if domain != ["OMIT", *candidate_refs]:
            failures.append(f"dynamic_domain_not_exact:{sample_id}")
        if "pattern" in canonical_json(schema):
            failures.append(f"dynamic_schema_contains_pattern:{sample_id}")
        required_cols = _required_columns(schema, phase_o["table_ref"])
        if list(phase_o["column_span_refs"]) != required_cols:
            failures.append(f"column_span_ref_keys_not_required_order:{sample_id}")
        if set(phase_o["column_span_refs"]) != set(required_cols):
            failures.append(f"column_span_ref_required_keys_mismatch:{sample_id}")
        if row["runtime_constraints"].get("candidate_generator_variant") != STAGE7B_SELECTED_VARIANT:
            failures.append(f"candidate_generator_variant_mismatch:{sample_id}")
        if row["runtime_constraints"].get("schema_table_count", 0) > 1:
            multi_table_count += 1
            if "oneOf" not in schema or len(schema["oneOf"]) < 2:
                failures.append(f"multi_table_schema_missing_oneof:{sample_id}")

        question = row["model_side_input"]["question"]
        for column_ref, span_ref in phase_o["column_span_refs"].items():
            if span_ref == "OMIT":
                omit_count += 1
            else:
                assigned_count += 1
                if span_ref not in candidate_refs:
                    failures.append(f"selected_span_ref_missing_from_inventory:{sample_id}:{column_ref}")
        non_omit_refs = [span_ref for span_ref in phase_o["column_span_refs"].values() if span_ref != "OMIT"]
        if len(non_omit_refs) != len(set(non_omit_refs)):
            failures.append(f"duplicate_span_ref_reuse:{sample_id}")
        for item in row["label_side_expected"].get("gold_column_span_ref_oracle", []):
            start = int(item["start_char"])
            end = int(item["end_char"])
            if question[start:end] != item["text"]:
                failures.append(f"gold_span_slice_mismatch:{sample_id}:{item['column_ref']}")
            if phase_o["column_span_refs"].get(item["column_ref"]) != item["candidate_span_ref"]:
                failures.append(f"gold_column_ref_decision_mismatch:{sample_id}:{item['column_ref']}")

        messages, _user, message_hash = render_phase_o_messages(row)
        if len(message_hash) != 64 or not messages[1]["content"].count("Candidate span inventory:"):
            failures.append(f"rendered_prompt_invalid:{sample_id}")

        db_path = stage_dir / row["synthetic_db_spec"]["sqlite_db_path"]
        if not db_path.is_file():
            failures.append(f"missing_sqlite_db:{sample_id}")
            continue
        if "sqlite_db_sha256" in row["synthetic_db_spec"]:
            failures.append(f"sqlite_binary_hash_embedded_in_scientific_fixture:{sample_id}")
        expected_logical_hash = logical_db_fixture_hash(
            {
                "sample_id": sample_id,
                "selected_table": row["synthetic_db_spec"]["selected_table"],
                "tables": row["synthetic_db_spec"]["source_tables"],
            },
            row["synthetic_db_spec"]["create_sql"],
        )
        if row["synthetic_db_spec"].get("logical_db_fixture_hash") != expected_logical_hash:
            failures.append(f"logical_db_fixture_hash_mismatch:{sample_id}")
        try:
            oracle = oracle_column_conditioned_path(row, db_path)
        except Exception as exc:
            failures.append(f"oracle_exception:{sample_id}:{type(exc).__name__}:{exc}")
            continue
        if oracle != oracle_by_id.get(sample_id):
            failures.append(f"oracle_result_mismatch:{sample_id}")
        for key in ("phase_o_operation_exact", "phase_o_output_keys_exact", "phase_m_model_call_removed", "candidate_inventory_contains_all_gold_spans", "dynamic_schema_exact", "canonical_target_state_exact"):
            if oracle.get(key) is not True:
                failures.append(f"oracle_{key}_not_true:{sample_id}")
        if oracle.get("model_generated_slot_refs") is not False or oracle.get("model_generated_phase_m") is not False:
            failures.append(f"oracle_removed_model_surface_not_false:{sample_id}")
        for key in ("resolver", "slot_ev_coherence", "typed_materialization", "completeness", "compilation"):
            if oracle.get(key) != "PASS":
                failures.append(f"oracle_{key}_not_pass:{sample_id}")
        if oracle.get("preflight") != "ADMITTED":
            failures.append(f"oracle_preflight_not_admitted:{sample_id}")
    return len(rows), assigned_count, omit_count, multi_table_count


def validate_manifests(stage_dir: Path, failures: list[str]) -> None:
    source_manifest = read_json(stage_dir / "SOURCE_INPUT_MANIFEST.json")
    derived_manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    package_integrity = read_json(stage_dir / "PACKAGE_FILE_INTEGRITY_MANIFEST.json")
    lock = read_json(stage_dir / "STAGE7C_A5_LOCK.json")
    token_audit = read_json(stage_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
    acceptance = read_json(stage_dir / "ACCEPTANCE_POLICY_A5.json")
    independence = read_json(stage_dir / "A5_PRIMARY_INDEPENDENCE_AUDIT.json")

    for payload_name, payload in {
        "source_manifest": source_manifest,
        "lock": lock,
        "package_integrity": package_integrity,
        "token_audit": token_audit,
        "acceptance": acceptance,
    }.items():
        if payload.get("model_called") is not False or payload.get("gpu_called") is not False:
            failures.append(f"{payload_name}_model_or_gpu_not_false")
    if lock.get("gretel_pilot_opened") is not False or lock.get("development_dev_used") is not False or lock.get("official_test_used") is not False:
        failures.append("lock_population_isolation_mismatch")
    if lock.get("status") != "PASS_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FROZEN":
        failures.append("lock_status_mismatch")
    if lock.get("phase_m_primary_pipeline_removed") is not True or lock.get("model_generates_phase_m") is not False:
        failures.append("lock_no_phase_m_mismatch")
    if lock.get("type_based_candidate_pruning_enabled") is not False:
        failures.append("lock_type_pruning_enabled")
    if lock.get("duplicate_span_reuse_is_method_failure") is not True:
        failures.append("lock_duplicate_span_policy_missing")
    if lock.get("column_span_refs_mapping_equality") != "order_insensitive_by_object_key":
        failures.append("lock_mapping_equality_mismatch")
    if lock.get("primary_acceptance_precedes_diagnostics") is not True or lock.get("diagnostics_can_compensate_primary_failure") is not False:
        failures.append("lock_primary_diagnostic_order_mismatch")
    if token_audit.get("tokenizer_status") != "PASS":
        failures.append("tokenizer_status_not_pass")
    if token_audit.get("rendered_prompt_token_stats") is None:
        failures.append("token_stats_missing")
    if token_audit.get("chat_template_hash_matches_required") is not True:
        failures.append("chat_template_hash_mismatch")
    if independence.get("status") != "PASS" or independence.get("exact_literal_overlap_case_count") != 0:
        failures.append("primary_independence_audit_not_pass")

    for item in source_manifest.get("source_files", []):
        path = PROJECT_ROOT / item["path"]
        if path.is_file() and sha256_file(path) != item["sha256"]:
            failures.append(f"source_manifest_hash_mismatch:{item['path']}")
    artifacts = derived_manifest.get("artifacts", [])
    if derived_manifest.get("artifact_count") != len(SCIENTIFIC_ARTIFACTS):
        failures.append("derived_artifact_count_mismatch")
    if derived_manifest.get("combined_scientific_artifacts_sha256") != sha256_text(canonical_json(artifacts)):
        failures.append("derived_combined_hash_mismatch")
    for item in artifacts:
        path = stage_dir / item["path"]
        if not path.is_file():
            failures.append(f"derived_missing_artifact:{item['path']}")
        elif sha256_file(path) != item["sha256"]:
            failures.append(f"derived_artifact_hash_mismatch:{item['path']}")

    sqlite_artifacts = package_integrity.get("sqlite_binary_artifacts", [])
    if package_integrity.get("sqlite_binary_artifact_count") != 24 or len(sqlite_artifacts) != 24:
        failures.append("sqlite_binary_artifact_count_mismatch")
    for item in sqlite_artifacts:
        path = stage_dir / item["path"]
        if not path.is_file():
            failures.append(f"sqlite_binary_missing:{item['path']}")
        elif sha256_file(path) != item["sqlite_binary_file_sha256"]:
            failures.append(f"sqlite_binary_hash_mismatch:{item['path']}")


def validate(stage_dir: Path, *, rebuild: bool = False, tokenizer_name_or_path: str | None = None, tokenizer_revision: str = QWEN_TOKENIZER_REVISION) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")
    if failures:
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}
    validate_prompt_and_schema(stage_dir, failures)
    case_count, assigned_count, omit_count, multi_table_count = validate_rows(
        stage_dir,
        failures,
        rows_name="FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl",
        oracle_name="ORACLE_COLUMN_CONDITIONED_PRIMARY_RESULTS.jsonl",
        expected_prefix="stage7c_a5_primary_english_",
    )
    diagnostic_case_count, diagnostic_assigned_count, diagnostic_omit_count, diagnostic_multi_table_count = validate_rows(
        stage_dir,
        failures,
        rows_name="A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl",
        oracle_name="ORACLE_A4_DERIVED_DIAGNOSTIC_RESULTS.jsonl",
        expected_prefix="stage7c_a5_fresh_english_",
        diagnostic=True,
    )
    validate_manifests(stage_dir, failures)

    if rebuild:
        token_audit = read_json(stage_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
        if token_audit.get("tokenizer_status") == "PASS" and not tokenizer_name_or_path:
            failures.append("TOKENIZER_REQUIRED_FOR_REBUILD")
            return {
                "stage": STAGE_NAME,
                "status": "FAIL",
                "failures": failures,
                "fresh_english_case_count": case_count,
                "assigned_column_decision_count": assigned_count,
                "omit_column_decision_count": omit_count,
                "multi_table_oneof_case_count": multi_table_count,
                "a4_derived_regression_diagnostic_count": diagnostic_case_count,
            }
        rebuild_parent = stage_dir.parent / f"{stage_dir.name}__semantic_rebuild_tmp"
        if rebuild_parent.exists():
            shutil.rmtree(rebuild_parent, ignore_errors=True)
        build_stage(rebuild_parent, tokenizer_name_or_path=tokenizer_name_or_path, tokenizer_revision=tokenizer_revision)
        try:
            rebuilt_failures: list[str] = []
            validate_prompt_and_schema(rebuild_parent, rebuilt_failures)
            rebuilt_counts = validate_rows(
                rebuild_parent,
                rebuilt_failures,
                rows_name="FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl",
                oracle_name="ORACLE_COLUMN_CONDITIONED_PRIMARY_RESULTS.jsonl",
                expected_prefix="stage7c_a5_primary_english_",
            )
            rebuilt_diagnostic_counts = validate_rows(
                rebuild_parent,
                rebuilt_failures,
                rows_name="A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl",
                oracle_name="ORACLE_A4_DERIVED_DIAGNOSTIC_RESULTS.jsonl",
                expected_prefix="stage7c_a5_fresh_english_",
                diagnostic=True,
            )
            validate_manifests(rebuild_parent, rebuilt_failures)
            if rebuilt_failures:
                failures.append(f"semantic_rebuild_failed:{rebuilt_failures}")
            if rebuilt_counts != (case_count, assigned_count, omit_count, multi_table_count):
                failures.append("semantic_rebuild_counts_mismatch")
            if rebuilt_diagnostic_counts != (diagnostic_case_count, diagnostic_assigned_count, diagnostic_omit_count, diagnostic_multi_table_count):
                failures.append("semantic_rebuild_diagnostic_counts_mismatch")
        finally:
            shutil.rmtree(rebuild_parent, ignore_errors=True)

    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "fresh_english_case_count": case_count,
        "assigned_column_decision_count": assigned_count,
        "omit_column_decision_count": omit_count,
        "multi_table_oneof_case_count": multi_table_count,
        "a4_derived_regression_diagnostic_count": diagnostic_case_count,
        "diagnostic_assigned_column_decision_count": diagnostic_assigned_count,
        "diagnostic_omit_column_decision_count": diagnostic_omit_count,
        "diagnostic_multi_table_oneof_case_count": diagnostic_multi_table_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--tokenizer-name-or-path", default=None)
    parser.add_argument("--tokenizer-revision", default=QWEN_TOKENIZER_REVISION)
    args = parser.parse_args()
    report = validate(args.stage_dir, rebuild=args.rebuild, tokenizer_name_or_path=args.tokenizer_name_or_path, tokenizer_revision=args.tokenizer_revision)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
