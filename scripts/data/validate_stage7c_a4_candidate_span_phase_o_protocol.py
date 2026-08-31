#!/usr/bin/env python3
"""Validate Stage7C-A4 English candidate-span Phase O protocol artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
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

from scripts.data.build_stage7c_a4_candidate_span_phase_o_protocol import (
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
    oracle_span_ref_path,
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
    "STAGE7C_A4_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]
REQUIRED_COVERAGE_TAGS = {
    "2_values",
    "3_values",
    "4_values",
    "5_values",
    "quoted_multiword",
    "three_word_value",
    "overlap_distractors",
    "email",
    "identifier",
    "hex_identifier",
    "date",
    "percent",
    "integer",
    "real",
}


def validate_prompt_and_schema(stage_dir: Path, failures: list[str]) -> None:
    prompt = read_json(stage_dir / "PHASE_O_PROMPT_SPEC_A4_ENGLISH.json")
    output = read_json(stage_dir / "PHASE_O_SPAN_REF_OUTPUT_SPEC.json")
    runtime = read_json(stage_dir / "PHASE_O_RUNTIME_SCHEMA_SPEC.json")
    serialization = read_json(stage_dir / "CANDIDATE_SERIALIZATION_FREEZE.json")

    if prompt.get("system_prompt") != PHASE_O_SYSTEM_PROMPT:
        failures.append("prompt_system_text_mismatch")
    if prompt.get("user_prompt_template") != PHASE_O_USER_PROMPT_TEMPLATE:
        failures.append("prompt_user_template_mismatch")
    hashes = prompt.get("prompt_hashes", {})
    if hashes.get("phase_o_system_prompt_sha256") != sha256_text(PHASE_O_SYSTEM_PROMPT):
        failures.append("prompt_system_hash_mismatch")
    if hashes.get("phase_o_user_prompt_template_sha256") != sha256_text(PHASE_O_USER_PROMPT_TEMPLATE):
        failures.append("prompt_user_hash_mismatch")
    for key in ("zero_shot",):
        if prompt.get(key) is not True:
            failures.append(f"prompt_{key}_not_true")
    if prompt.get("examples") != [] or prompt.get("retry") != 0 or prompt.get("repair") != "none":
        failures.append("prompt_examples_retry_repair_changed")
    if prompt.get("model_id") != MODEL_ID or prompt.get("model_revision") != MODEL_REVISION:
        failures.append("prompt_model_lock_mismatch")
    for key in ("model_generates_character_offsets", "model_generates_values", "model_generates_column_refs"):
        if prompt.get(key) is not False:
            failures.append(f"prompt_{key}_not_false")
    if prompt.get("model_selects_span_refs") is not True:
        failures.append("prompt_model_selects_span_refs_not_true")

    if output.get("allowed_top_level_keys") != ["operation", "span_refs"]:
        failures.append("output_allowed_keys_mismatch")
    forbidden = set(output.get("forbidden_top_level_keys", []))
    if not {"value_spans", "start_char", "end_char", "values", "column_refs", "assignments"} <= forbidden:
        failures.append("output_forbidden_keys_incomplete")
    if output.get("span_refs", {}).get("uniqueItems") is not True:
        failures.append("output_span_refs_unique_items_not_true")
    if runtime.get("unknown_span_refs_structurally_impossible") is not True:
        failures.append("runtime_unknown_refs_not_structurally_impossible")
    if runtime.get("static_pattern_fallback_allowed") is not False:
        failures.append("runtime_static_pattern_fallback_allowed")
    if serialization.get("line_template") != "SPAN_0001 | TAG[,TAG...] | exact source text":
        failures.append("serialization_line_template_mismatch")
    if serialization.get("model_hidden_fields") != ["start_char", "end_char", "provenance_tags"]:
        failures.append("serialization_hidden_fields_mismatch")
    if "start_char" in serialization.get("model_visible_fields", []) or "end_char" in serialization.get("model_visible_fields", []):
        failures.append("serialization_exposes_offsets")


def validate_rows(stage_dir: Path, failures: list[str]) -> tuple[int, int, int]:
    rows = read_jsonl(stage_dir / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl")
    oracle_rows = read_jsonl(stage_dir / "ORACLE_SPAN_REF_PATH_RESULTS.jsonl")
    oracle_by_id = {row["sample_id"]: row for row in oracle_rows}
    if len(rows) != 10:
        failures.append("fresh_case_count_mismatch")
    sample_ids = [row["sample_id"] for row in rows]
    if len(set(sample_ids)) != len(sample_ids):
        failures.append("duplicate_sample_id")
    if any(str(sample_id).startswith("gretel:") for sample_id in sample_ids):
        failures.append("gretel_sample_id_present")

    tags = {tag for row in rows for tag in row.get("coverage_tags", [])}
    missing_tags = sorted(REQUIRED_COVERAGE_TAGS - tags)
    if missing_tags:
        failures.append(f"coverage_tags_missing:{','.join(missing_tags)}")
    value_counts = sorted({len(row["label_side_expected"]["phase_o"]["span_refs"]) for row in rows})
    if value_counts != [2, 3, 4, 5]:
        failures.append("value_count_coverage_mismatch")

    value_count = 0
    admitted_count = 0
    for row in rows:
        sample_id = row["sample_id"]
        if row.get("locked_before_model_run") is not True:
            failures.append(f"not_locked_before_model_run:{sample_id}")
        if row.get("fresh_synthetic") is not True:
            failures.append(f"fresh_synthetic_not_true:{sample_id}")
        if set(row.get("model_side_input", {})) != {"question", "schema_inventory", "candidate_inventory_text"}:
            failures.append(f"model_side_input_keys_mismatch:{sample_id}")
        if FORBIDDEN_MODEL_SIDE_KEYS.intersection(row.get("model_side_input", {})):
            failures.append(f"model_side_gold_leakage:{sample_id}")
        if "start_char" in row["model_side_input"]["candidate_inventory_text"] or "end_char" in row["model_side_input"]["candidate_inventory_text"]:
            failures.append(f"candidate_inventory_text_exposes_offsets:{sample_id}")
        if row["label_side_expected"].get("model_side_visible") is not False:
            failures.append(f"label_side_visible_not_false:{sample_id}")

        phase_o = row["label_side_expected"]["phase_o"]
        if sorted(phase_o) != ["operation", "span_refs"]:
            failures.append(f"phase_o_keys_not_span_ref_only:{sample_id}")
        if phase_o.get("operation") != "INSERT":
            failures.append(f"phase_o_operation_not_insert:{sample_id}")
        if len(phase_o.get("span_refs", [])) != len(set(phase_o.get("span_refs", []))):
            failures.append(f"duplicate_gold_span_ref:{sample_id}")

        candidates = row["runtime_constraints"]["candidate_inventory"]
        candidate_refs = [candidate["span_ref"] for candidate in candidates]
        enum = row["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["items"]["enum"]
        if enum != candidate_refs:
            failures.append(f"dynamic_enum_not_exact:{sample_id}")
        if "pattern" in row["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["items"]:
            failures.append(f"dynamic_schema_contains_pattern:{sample_id}")
        if not set(phase_o["span_refs"]) <= set(candidate_refs):
            failures.append(f"gold_span_ref_missing_from_inventory:{sample_id}")
        if row["runtime_constraints"].get("candidate_generator_variant") != STAGE7B_SELECTED_VARIANT:
            failures.append(f"candidate_generator_variant_mismatch:{sample_id}")

        question = row["model_side_input"]["question"]
        gold_oracle = row["label_side_expected"].get("gold_value_span_ref_oracle", [])
        if len(gold_oracle) != len(phase_o["span_refs"]):
            failures.append(f"gold_oracle_count_mismatch:{sample_id}")
        value_count += len(gold_oracle)
        for item in gold_oracle:
            start = int(item["start_char"])
            end = int(item["end_char"])
            if question[start:end] != item["text"]:
                failures.append(f"gold_span_slice_mismatch:{sample_id}:{item['value_index']}")
            if item["candidate_span_ref"] not in candidate_refs:
                failures.append(f"gold_candidate_ref_not_in_inventory:{sample_id}:{item['value_index']}")

        messages, _user, message_hash = render_phase_o_messages(row)
        if len(message_hash) != 64 or not messages[1]["content"].count("Candidate span inventory:"):
            failures.append(f"rendered_prompt_invalid:{sample_id}")

        db_path = stage_dir / row["synthetic_db_spec"]["sqlite_db_path"]
        if not db_path.is_file():
            failures.append(f"missing_sqlite_db:{sample_id}")
            continue
        expected_logical_hash = logical_db_fixture_hash(
            {
                "sample_id": sample_id,
                "table_name": row["synthetic_db_spec"]["table_name"],
                "columns": row["synthetic_db_spec"]["source_columns"],
            },
            row["synthetic_db_spec"]["create_sql"],
        )
        if row["synthetic_db_spec"].get("logical_db_fixture_hash") != expected_logical_hash:
            failures.append(f"logical_db_fixture_hash_mismatch:{sample_id}")
        if "sqlite_db_sha256" in row["synthetic_db_spec"]:
            failures.append(f"sqlite_binary_hash_embedded_in_scientific_fixture:{sample_id}")
        try:
            oracle = oracle_span_ref_path(row, db_path)
        except Exception as exc:
            failures.append(f"oracle_exception:{sample_id}:{type(exc).__name__}:{exc}")
            continue
        if oracle != oracle_by_id.get(sample_id):
            failures.append(f"oracle_result_mismatch:{sample_id}")
        if oracle.get("preflight") == "ADMITTED":
            admitted_count += 1
        for key in (
            "phase_o_operation_exact",
            "phase_o_output_keys_exact",
            "candidate_inventory_contains_all_gold_spans",
            "dynamic_enum_exact",
            "phase_m_mapping_exact",
            "canonical_target_state_exact",
        ):
            if oracle.get(key) is not True:
                failures.append(f"oracle_{key}_not_true:{sample_id}")
        for key in ("resolver", "slot_ev_coherence", "typed_materialization", "completeness", "compilation"):
            if oracle.get(key) != "PASS":
                failures.append(f"oracle_{key}_not_pass:{sample_id}")
        if oracle.get("preflight") != "ADMITTED":
            failures.append(f"oracle_preflight_not_admitted:{sample_id}")
    return len(rows), value_count, admitted_count


def validate_manifests(stage_dir: Path, failures: list[str]) -> None:
    source_manifest = read_json(stage_dir / "SOURCE_INPUT_MANIFEST.json")
    derived_manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    package_integrity = read_json(stage_dir / "PACKAGE_FILE_INTEGRITY_MANIFEST.json")
    lock = read_json(stage_dir / "STAGE7C_A4_LOCK.json")
    token_audit = read_json(stage_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
    miss_policy = read_json(stage_dir / "CANDIDATE_MISS_FAILURE_POLICY.json")
    acceptance = read_json(stage_dir / "ACCEPTANCE_POLICY_A4.json")

    for payload_name, payload in {
        "source_manifest": source_manifest,
        "lock": lock,
        "package_integrity": package_integrity,
        "token_audit": token_audit,
        "miss_policy": miss_policy,
        "acceptance": acceptance,
    }.items():
        if payload.get("model_called") is not False or payload.get("gpu_called") is not False:
            failures.append(f"{payload_name}_model_or_gpu_not_false")
    if lock.get("gretel_pilot_opened") is not False or lock.get("development_dev_used") is not False or lock.get("official_test_used") is not False:
        failures.append("lock_population_isolation_mismatch")
    if lock.get("status") != "PASS_CANDIDATE_SPAN_PHASE_O_PROTOCOL_FROZEN":
        failures.append("lock_status_mismatch")
    if lock.get("phase_o_output_keys") != ["operation", "span_refs"]:
        failures.append("lock_phase_o_output_keys_mismatch")
    if lock.get("dynamic_span_ref_enum_required") is not True:
        failures.append("lock_dynamic_enum_not_required")
    if lock.get("candidate_miss_is_method_failure") is not True or lock.get("candidate_miss_can_exclude_samples") is not False:
        failures.append("lock_candidate_miss_policy_mismatch")
    if miss_policy.get("may_exclude_sample_for_candidate_miss") is not False:
        failures.append("miss_policy_allows_exclusion")
    gate = acceptance.get("synthetic_feasibility_gate", {})
    if gate.get("required_pass_count") != "10/10" or gate.get("nine_of_ten_allowed") is not False:
        failures.append("acceptance_gate_not_10_of_10")

    if token_audit.get("tokenizer_status") == "FAIL":
        failures.append("tokenizer_audit_failed")
    if token_audit.get("tokenizer_status") == "PASS" and not token_audit.get("rendered_prompt_token_stats"):
        failures.append("token_stats_missing")
    if token_audit.get("fresh_case_count") != 10:
        failures.append("token_audit_case_count_mismatch")
    if package_integrity.get("identity_scope") != "physical_package_file_integrity_not_cross_environment_scientific_rebuild":
        failures.append("package_integrity_scope_mismatch")

    for source in source_manifest.get("source_files", []):
        path = PROJECT_ROOT / source.get("path", "")
        if not path.is_file():
            failures.append(f"source_manifest_missing:{source.get('path')}")
            continue
        if path.stat().st_size != source.get("bytes"):
            failures.append(f"source_manifest_size_mismatch:{source.get('path')}")
        if sha256_file(path) != source.get("sha256"):
            failures.append(f"source_manifest_hash_mismatch:{source.get('path')}")

    manifest_by_path = {row["path"]: row for row in derived_manifest.get("artifacts", [])}
    if derived_manifest.get("identity_scope") != "scientific_logical_artifacts_only":
        failures.append("derived_manifest_identity_scope_mismatch")
    for name in SCIENTIFIC_ARTIFACTS:
        if name not in manifest_by_path:
            failures.append(f"derived_manifest_missing:{name}")
    for forbidden in PACKAGE_INTEGRITY_ARTIFACTS:
        if forbidden in manifest_by_path:
            failures.append(f"derived_manifest_contains_package_integrity_artifact:{forbidden}")
    for artifact in derived_manifest.get("artifacts", []):
        path = stage_dir / artifact["path"]
        if not path.is_file():
            failures.append(f"derived_artifact_missing:{artifact['path']}")
        elif sha256_file(path) != artifact.get("sha256"):
            failures.append(f"derived_artifact_hash_mismatch:{artifact['path']}")
    if derived_manifest.get("combined_scientific_artifacts_sha256") != sha256_text(canonical_json(derived_manifest.get("artifacts", []))):
        failures.append("combined_scientific_artifacts_hash_mismatch")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("lock_derived_manifest_hash_mismatch")
    sqlite_integrity_rows = package_integrity.get("sqlite_binary_artifacts", [])
    if package_integrity.get("sqlite_binary_artifact_count") != len(sqlite_integrity_rows):
        failures.append("package_integrity_count_mismatch")
    if package_integrity.get("combined_sqlite_binary_artifacts_sha256") != sha256_text(canonical_json(sqlite_integrity_rows)):
        failures.append("package_integrity_combined_hash_mismatch")
    for artifact in sqlite_integrity_rows:
        path = stage_dir / artifact["path"]
        if not path.is_file():
            failures.append(f"package_integrity_missing_sqlite:{artifact['path']}")
        elif sha256_file(path) != artifact.get("sqlite_binary_file_sha256"):
            failures.append(f"package_integrity_sqlite_hash_mismatch:{artifact['path']}")


def sqlite_table_info(db_path: Path, table_name: str) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [
        {
            "cid": row[0],
            "name": row[1],
            "type": row[2],
            "notnull": row[3],
            "dflt_value": row[4],
            "pk": row[5],
        }
        for row in rows
    ]


def validate_rebuild_logical_db_semantics(stage_dir: Path, rebuild_dir: Path, failures: list[str]) -> None:
    original_rows = read_jsonl(stage_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl")
    rebuilt_rows = read_jsonl(rebuild_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl")
    if len(original_rows) != len(rebuilt_rows):
        failures.append("rebuild_logical_db_manifest_count_mismatch")
        return
    rebuilt_by_id = {row["sample_id"]: row for row in rebuilt_rows}
    for original in original_rows:
        sample_id = original["sample_id"]
        rebuilt = rebuilt_by_id.get(sample_id)
        if rebuilt is None:
            failures.append(f"rebuild_logical_db_missing_sample:{sample_id}")
            continue
        for key in (
            "table_name",
            "source_columns",
            "create_sql",
            "create_sql_sha256",
            "initial_state_hash",
            "logical_db_fixture_hash",
        ):
            if original.get(key) != rebuilt.get(key):
                failures.append(f"rebuild_logical_db_{key}_mismatch:{sample_id}")
        if original.get("sqlite_db_path") != rebuilt.get("sqlite_db_path"):
            failures.append(f"rebuild_logical_db_path_mismatch:{sample_id}")
        original_table = original.get("table_name")
        rebuilt_table = rebuilt.get("table_name")
        if original_table != rebuilt_table:
            failures.append(f"rebuild_logical_db_table_name_mismatch:{sample_id}")
            continue
        if sqlite_table_info(stage_dir / original["sqlite_db_path"], original_table) != sqlite_table_info(rebuild_dir / rebuilt["sqlite_db_path"], rebuilt_table):
            failures.append(f"rebuild_logical_db_schema_mismatch:{sample_id}")


def validate(
    stage_dir: Path,
    *,
    rebuild: bool = False,
    tokenizer_name_or_path: str | None = None,
    tokenizer_revision: str = QWEN_TOKENIZER_REVISION,
) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")
    if failures:
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}

    validate_prompt_and_schema(stage_dir, failures)
    case_count, value_count, admitted_count = validate_rows(stage_dir, failures)
    validate_manifests(stage_dir, failures)

    if rebuild and not failures:
        token_audit = read_json(stage_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
        if token_audit.get("tokenizer_status") == "PASS" and not tokenizer_name_or_path:
            failures.append("TOKENIZER_REQUIRED_FOR_REBUILD")
        if failures:
            pass
        else:
            rebuild_dir = stage_dir.parent / f"{STAGE_NAME}_rebuild_validation"
            try:
                build_stage(
                    rebuild_dir,
                    tokenizer_name_or_path=tokenizer_name_or_path,
                    tokenizer_revision=tokenizer_revision,
                )
                rebuilt_token_audit = read_json(rebuild_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
                if token_audit.get("tokenizer_status") == "PASS" and rebuilt_token_audit.get("tokenizer_status") != "PASS":
                    failures.append("TOKENIZER_UNAVAILABLE_FOR_REBUILD")
                else:
                    for name in [*SCIENTIFIC_ARTIFACTS, "DERIVED_ARTIFACT_MANIFEST.json"]:
                        if sha256_file(stage_dir / name) != sha256_file(rebuild_dir / name):
                            failures.append(f"rebuild_artifact_mismatch:{name}")
                    validate_rebuild_logical_db_semantics(stage_dir, rebuild_dir, failures)
            finally:
                if rebuild_dir.exists():
                    shutil.rmtree(rebuild_dir, ignore_errors=True)

    lock = read_json(stage_dir / "STAGE7C_A4_LOCK.json")
    token_audit = read_json(stage_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "fresh_english_case_count": case_count,
        "gold_value_count": value_count,
        "oracle_preflight_admitted_count": admitted_count,
        "tokenizer_status": token_audit.get("tokenizer_status"),
        "model_called": lock.get("model_called"),
        "gpu_called": lock.get("gpu_called"),
        "gretel_pilot_opened": lock.get("gretel_pilot_opened"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--tokenizer-name-or-path", default=None)
    parser.add_argument("--tokenizer-revision", default=QWEN_TOKENIZER_REVISION)
    args = parser.parse_args()
    result = validate(
        args.stage_dir,
        rebuild=args.rebuild,
        tokenizer_name_or_path=args.tokenizer_name_or_path,
        tokenizer_revision=args.tokenizer_revision,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
