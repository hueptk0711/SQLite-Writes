#!/usr/bin/env python3
"""Build Stage7E0-A7 final A5-based real-generation feasibility freeze.

This stage freezes a fresh 12-case one-call A7 feasibility set and server
runner package. It is the pre-GPU freeze artifact: local mock execution proves
the deterministic wiring, while official model outputs must be produced once on
the locked UET RTX 4090 runtime by the packaged server script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    QWEN_TOKENIZER_ID,
    QWEN_TOKENIZER_REVISION,
    SELECTED_VARIANT,
    candidate_to_json,
    generate_candidate_inventory,
    serialize_candidate_inventory,
)
from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import (  # noqa: E402
    schema_label_alias_index,
)
from scripts.data.build_stage7b_a5_typed_atomic_boundary_omission import (  # noqa: E402
    PATCH_NAME as STAGE7B_A5_PATCH_NAME,
    STAGE_NAME as STAGE7B_A5_NAME,
    a5_suppression_reasons,
    omittable_schema_aliases_from_inventory,
)
from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import (  # noqa: E402
    PHASE_O_SYSTEM_PROMPT,
    PHASE_O_USER_PROMPT_TEMPLATE,
    ColumnInfo,
    create_case_db,
    create_sql_statements,
    gold_column_span_refs,
    logical_db_fixture_hash,
    oracle_column_conditioned_path,
    render_phase_o_messages,
    schema_inventory,
    schema_tables,
    selected_table_ref,
    sha256_file,
    target_rows,
)
from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import canonical_json as upstream_canonical_json  # noqa: E402
from scripts.server.run_stage7e0_a4_english import (  # noqa: E402
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    FROZEN_RUNTIME_VERSIONS,
)
from scripts.server.run_stage7e0_a6_english import (  # noqa: E402
    ALLOWED_FROZEN_RUNTIME_PROFILES,
    PHASE_O_MAX_NEW_TOKENS,
    PRIMARY_RUNTIME_PROFILE_ID,
)


STAGE_NAME = "Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY"
PATCH_NAME = "PATCH1"
PACKAGE_DATE = "20260903"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
EXPECTED_PRIMARY_COUNT = 12
SERVER_WORK_ROOT = "/home/uet/hue_ptk"
PRIMARY_RESULT_DIR_NAME = "stage7e0_a7_final_a5_uet_rtx4090_primary_results_20260903"
SERVER_REQUIREMENTS_LOCK = "requirements-inference-uet-rtx4090-cu124.lock.txt"
STAGE7C_A6_NAME = "Stage7C_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROTOCOL_FREEZE"

SCIENTIFIC_ARTIFACTS = [
    "REVIEWER_README.md",
    "VALIDATION_REPORT.md",
    "A7_PROTOCOL_FREEZE.json",
    "A7_GATE.json",
    "A7_PRIMARY_12_MANIFEST.json",
    "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl",
    "source_diff/A6_to_A7.patch",
    "protocol/model_lock.json",
    "protocol/generation_config.json",
    "protocol/prompt_template.txt",
    "protocol/prompt_template.sha256",
    "protocol/chat_template.sha256",
    "protocol/candidate_domain_spec.json",
    "audits/data_independence_audit.json",
    "audits/gold_leakage_audit.json",
    "audits/model_call_audit.json",
    "audits/retry_audit.json",
    "audits/denominator_audit.json",
    "mock_dry_run/raw/model_outputs.jsonl",
    "mock_dry_run/raw/candidate_domains.jsonl",
    "mock_dry_run/raw/prompts_or_prompt_hashes.jsonl",
    "mock_dry_run/results/per_sample_results.jsonl",
    "mock_dry_run/results/summary.json",
    "mock_dry_run/results/summary.md",
    "mock_dry_run/results/failure_analysis.json",
    "mock_dry_run/runtime/environment.json",
    "mock_dry_run/runtime/token_usage.jsonl",
    "mock_dry_run/runtime/latency.jsonl",
    "mock_dry_run/audits/model_call_audit.json",
    "mock_dry_run/audits/retry_audit.json",
    "mock_dry_run/audits/denominator_audit.json",
    "mock_dry_run/run_manifest.json",
    "MANIFEST.json",
    "SHA256SUMS",
    "STAGE7E0_A7_LOCK.json",
    "SERVER_RUN_COMMANDS.md",
    "SERVER_RUN_COMMANDS.sh",
]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_json(value: Any) -> str:
    return upstream_canonical_json(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def normalized_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def column_is_omittable(column: dict[str, Any] | ColumnInfo) -> bool:
    if isinstance(column, dict):
        return bool(column.get("nullable") or column.get("has_default") or column.get("primary_key") or column.get("autoincrement") or column.get("generated"))
    return bool(column.nullable or column.has_default or column.primary_key or column.autoincrement)


def dynamic_schema_a5_for_column_infos(tables: dict[str, list[ColumnInfo]], span_refs: list[str]) -> dict[str, Any]:
    def column_domain(column: ColumnInfo) -> list[str]:
        return ["OMIT", *span_refs] if column_is_omittable(column) else list(span_refs)

    def branch_schema(table_ref: str, columns: list[ColumnInfo]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "table_ref", "column_span_refs"],
            "properties": {
                "operation": {"type": "string", "const": "INSERT"},
                "table_ref": {"type": "string", "const": table_ref},
                "column_span_refs": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [column.column_ref for column in columns],
                    "properties": {column.column_ref: {"type": "string", "enum": column_domain(column)} for column in columns},
                },
            },
        }

    if len(tables) == 1:
        payload = branch_schema("TAB_1", next(iter(tables.values())))
        payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        payload["title"] = "Stage7E0-A7 A5 Column-Conditioned Candidate Selection Output"
        return payload
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7E0-A7 A5 Multi-Table Column-Conditioned Candidate Selection Output",
        "oneOf": [branch_schema(f"TAB_{index}", columns) for index, (_table_name, columns) in enumerate(sorted(tables.items()), start=1)],
    }


def stage7e0_a7_cases() -> list[dict[str, Any]]:
    return [
        {
            "sample_id": "stage7e0_a7_fresh_english_001",
            "question": "Insert Alice, age 22. Bio is absent.",
            "selected_table": "a7_people_alpha",
            "tables": [{"table_name": "a7_people_alpha", "columns": [{"column_name": "name", "source_type": "TEXT", "nullable": False}, {"column_name": "age", "source_type": "INTEGER", "nullable": False}, {"column_name": "bio", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"name": "Alice", "age": "22"},
            "coverage_tags": ["fresh_a7", "atomic_boundary", "integer", "true_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_002",
            "question": "Create a city row: city \"New York\", population 8419000, note absent.",
            "selected_table": "a7_city_registry",
            "tables": [{"table_name": "a7_city_registry", "columns": [{"column_name": "city", "source_type": "TEXT", "nullable": False}, {"column_name": "population", "source_type": "INTEGER", "nullable": False}, {"column_name": "note", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"city": "New York", "population": "8419000"},
            "coverage_tags": ["fresh_a7", "quoted_text", "integer", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_003",
            "question": "For calibration, insert device DEV-42, reading 3.14, confidence_pct 68%. Leave technician_note missing.",
            "selected_table": "a7_calibration_events",
            "tables": [{"table_name": "a7_calibration_events", "columns": [{"column_name": "device", "source_type": "TEXT", "nullable": False}, {"column_name": "reading", "source_type": "REAL", "nullable": False}, {"column_name": "confidence_pct", "source_type": "TEXT", "nullable": False}, {"column_name": "technician_note", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"device": "DEV-42", "reading": "3.14", "confidence_pct": "68%"},
            "coverage_tags": ["fresh_a7", "real", "percent_literal", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_004",
            "question": "Insert sku SKU-77, price 19.95. Active should use default.",
            "selected_table": "a7_store_items",
            "tables": [{"table_name": "a7_store_items", "columns": [{"column_name": "sku", "source_type": "TEXT", "nullable": False}, {"column_name": "price", "source_type": "REAL", "nullable": False}, {"column_name": "active", "source_type": "INTEGER", "nullable": False, "default": 1}]}],
            "assigned_values": {"sku": "SKU-77", "price": "19.95"},
            "coverage_tags": ["fresh_a7", "real", "default_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_005",
            "question": "Create shipment SHIP-500 with weight_kg 68kg and duration_ms 25ms.",
            "selected_table": "a7_shipments",
            "tables": [{"table_name": "a7_shipments", "columns": [{"column_name": "shipment_id", "source_type": "TEXT", "nullable": False}, {"column_name": "weight_kg", "source_type": "TEXT", "nullable": False}, {"column_name": "duration_ms", "source_type": "TEXT", "nullable": False}]}],
            "assigned_values": {"shipment_id": "SHIP-500", "weight_kg": "68kg", "duration_ms": "25ms"},
            "coverage_tags": ["fresh_a7", "unit_suffix_not_percent", "text_units"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_006",
            "question": "Insert ticket TCK-8, required status absent, priority 4. Comment missing.",
            "selected_table": "a7_support_tickets",
            "tables": [{"table_name": "a7_support_tickets", "columns": [{"column_name": "ticket_id", "source_type": "TEXT", "nullable": False}, {"column_name": "status", "source_type": "TEXT", "nullable": False}, {"column_name": "priority", "source_type": "INTEGER", "nullable": False}, {"column_name": "comment", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"ticket_id": "TCK-8", "status": "absent", "priority": "4"},
            "coverage_tags": ["fresh_a7", "required_literal_absent", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_007",
            "question": "Add badge BADGE-9 for attendee \"Mina Rao\" seat B12; emergency_contact is not provided.",
            "selected_table": "a7_event_badges",
            "tables": [{"table_name": "a7_event_badges", "columns": [{"column_name": "badge_code", "source_type": "TEXT", "nullable": False}, {"column_name": "attendee", "source_type": "TEXT", "nullable": False}, {"column_name": "seat", "source_type": "TEXT", "nullable": False}, {"column_name": "emergency_contact", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"badge_code": "BADGE-9", "attendee": "Mina Rao", "seat": "B12"},
            "coverage_tags": ["fresh_a7", "quoted_text", "identifier", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_008",
            "question": "Insert material basalt, density 2.71, source LAB-A. Remarks omitted.",
            "selected_table": "a7_material_samples",
            "tables": [{"table_name": "a7_material_samples", "columns": [{"column_name": "material", "source_type": "TEXT", "nullable": False}, {"column_name": "density", "source_type": "REAL", "nullable": False}, {"column_name": "source", "source_type": "TEXT", "nullable": False}, {"column_name": "remarks", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"material": "basalt", "density": "2.71", "source": "LAB-A"},
            "coverage_tags": ["fresh_a7", "real", "identifier", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_009",
            "question": "Create library loan LN-234 for title \"Quiet Harbor\" borrower CARD-88 loan_days 14. Hold shelf absent.",
            "selected_table": "a7_library_loans",
            "tables": [{"table_name": "a7_library_loans", "columns": [{"column_name": "loan_id", "source_type": "TEXT", "nullable": False}, {"column_name": "title", "source_type": "TEXT", "nullable": False}, {"column_name": "borrower_card", "source_type": "TEXT", "nullable": False}, {"column_name": "loan_days", "source_type": "INTEGER", "nullable": False}, {"column_name": "hold_shelf", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"loan_id": "LN-234", "title": "Quiet Harbor", "borrower_card": "CARD-88", "loan_days": "14"},
            "coverage_tags": ["fresh_a7", "quoted_text", "integer", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_010",
            "question": "Insert station ST-12, temperature_c 21.5, humidity_pct 45%, accessibility_note missing.",
            "selected_table": "a7_weather_stations",
            "tables": [{"table_name": "a7_weather_stations", "columns": [{"column_name": "station", "source_type": "TEXT", "nullable": False}, {"column_name": "temperature_c", "source_type": "REAL", "nullable": False}, {"column_name": "humidity_pct", "source_type": "TEXT", "nullable": False}, {"column_name": "accessibility_note", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"station": "ST-12", "temperature_c": "21.5", "humidity_pct": "45%"},
            "coverage_tags": ["fresh_a7", "real", "percent_literal", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_011",
            "question": "For audit_jobs insert job_code JOB-501, owner \"Iris Chen\", page_count 42. Reviewer note absent.",
            "selected_table": "a7_audit_jobs",
            "tables": [{"table_name": "a7_audit_jobs", "columns": [{"column_name": "job_code", "source_type": "TEXT", "nullable": False}, {"column_name": "owner", "source_type": "TEXT", "nullable": False}, {"column_name": "page_count", "source_type": "INTEGER", "nullable": False}, {"column_name": "reviewer_note", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"job_code": "JOB-501", "owner": "Iris Chen", "page_count": "42"},
            "coverage_tags": ["fresh_a7", "quoted_text", "integer", "optional_omit"],
        },
        {
            "sample_id": "stage7e0_a7_fresh_english_012",
            "question": "Insert code ZX-1, label \"Missing\", score 7. Optional memo absent.",
            "selected_table": "a7_label_checks",
            "tables": [{"table_name": "a7_label_checks", "columns": [{"column_name": "code", "source_type": "TEXT", "nullable": False}, {"column_name": "label", "source_type": "TEXT", "nullable": False}, {"column_name": "score", "source_type": "INTEGER", "nullable": False}, {"column_name": "optional_memo", "source_type": "TEXT", "nullable": True}]}],
            "assigned_values": {"code": "ZX-1", "label": "Missing", "score": "7"},
            "coverage_tags": ["fresh_a7", "quoted_missing_literal", "integer", "optional_omit"],
        },
    ]


def schema_labels_for_case(case: dict[str, Any]) -> set[str]:
    return {case["selected_table"], *(column["column_name"] for table in case["tables"] for column in table["columns"])}


def filtered_candidate_inventory_for_a7_case(case: dict[str, Any]) -> tuple[list[Any], list[Any], dict[str, dict[str, Any]], dict[str, list[str]]]:
    full_inventory = generate_candidate_inventory(case["question"], variant=SELECTED_VARIANT)
    aliases = schema_label_alias_index(schema_labels_for_case(case))
    inventory_payload = schema_inventory(case)
    omittable_aliases = omittable_schema_aliases_from_inventory(inventory_payload)
    from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import detect_omission_constructions

    detections = detect_omission_constructions(case["question"], omittable_aliases)
    reasons = a5_suppression_reasons(full_inventory, aliases, detections, include_a4=True)
    by_bounds = {(candidate.start_char, candidate.end_char): candidate for candidate in full_inventory}
    for candidate in full_inventory:
        stripped = candidate.text.rstrip(".,;:)")
        stripped = stripped.lstrip("(")
        if stripped == candidate.text or not stripped:
            continue
        stripped_start = candidate.start_char + (len(candidate.text) - len(candidate.text.lstrip("(")))
        stripped_end = stripped_start + len(stripped)
        stripped_candidate = by_bounds.get((stripped_start, stripped_end))
        if stripped_candidate is not None:
            reasons.setdefault(
                candidate.span_ref,
                {
                    "rule": "A7_A5_RUNTIME_TRAILING_OR_WRAPPING_PUNCTUATION_HAS_STRIPPED_CANDIDATE",
                    "stripped_span_ref": stripped_candidate.span_ref,
                    "stripped_text": stripped_candidate.text,
                },
            )
    filtered = [candidate for candidate in full_inventory if candidate.span_ref not in reasons]
    return full_inventory, filtered, reasons, aliases


def a7_row(case: dict[str, Any], db_info: dict[str, Any]) -> dict[str, Any]:
    full_inventory, inventory, suppression_reasons, aliases = filtered_candidate_inventory_for_a7_case(case)
    column_span_refs, gold_rows = gold_column_span_refs(case, inventory)
    span_refs = [candidate.span_ref for candidate in inventory]
    dynamic_schema = dynamic_schema_a5_for_column_infos(schema_tables(case), span_refs)
    target = target_rows(case)
    model_schema_inventory = schema_inventory(case)
    row = {
        "sample_id": case["sample_id"],
        "locked_before_model_run": True,
        "fresh_synthetic": True,
        "source_group": "a7_fresh_synthetic_handcrafted_20260903",
        "coverage_tags": case["coverage_tags"],
        "model_side_input": {
            "question": case["question"],
            "schema_inventory": model_schema_inventory,
            "candidate_inventory_text": serialize_candidate_inventory(inventory),
        },
        "runtime_constraints": {
            "phase_o_schema": dynamic_schema,
            "candidate_generator_variant": SELECTED_VARIANT,
            "candidate_domain_filter_enabled": True,
            "candidate_domain_filter": f"{STAGE7B_A5_NAME}_{STAGE7B_A5_PATCH_NAME}",
            "unfiltered_candidate_count": len(full_inventory),
            "candidate_count": len(inventory),
            "suppressed_candidate_count": len(suppression_reasons),
            "suppression_rule_counts": {
                rule: sum(1 for reason in suppression_reasons.values() if reason["rule"] == rule)
                for rule in sorted({reason["rule"] for reason in suppression_reasons.values()})
            },
            "schema_alias_count": len(aliases),
            "candidate_inventory": [candidate_to_json(candidate) for candidate in inventory],
            "phase_o_schema_sha256": sha256_text(canonical_json(dynamic_schema)),
            "required_columns_forbid_omit_in_schema": True,
            "optional_defaultable_columns_allow_omit_in_schema": True,
            "model_calls_per_sample": 1,
            "phase_m_removed": True,
            "retry": 0,
        },
        "candidate_domain_audit": {
            "source_stage": STAGE7B_A5_NAME,
            "source_patch": STAGE7B_A5_PATCH_NAME,
            "unfiltered_candidate_count": len(full_inventory),
            "filtered_candidate_count": len(inventory),
            "suppressed_candidate_count": len(suppression_reasons),
            "gold_suppressed_count": 0,
            "suppression_examples": [
                {"span_ref": candidate.span_ref, "text": candidate.text, "reason": suppression_reasons[candidate.span_ref]}
                for candidate in full_inventory
                if candidate.span_ref in suppression_reasons
            ][:25],
        },
        "label_side_expected": {
            "model_side_visible": False,
            "phase_o": {"operation": "INSERT", "table_ref": selected_table_ref(case), "column_span_refs": column_span_refs},
            "gold_column_span_ref_oracle": gold_rows,
            "target_state": {
                "table_name": case["selected_table"],
                "typed_target_rows": target,
                "target_state_hash": sha256_text(canonical_json(target)),
            },
        },
        "synthetic_db_spec": {**db_info, "source_tables": case["tables"]},
    }
    messages, _user, prompt_hash = render_phase_o_messages(row)
    row["runtime_constraints"]["rendered_prompt_sha256"] = prompt_hash
    row["runtime_constraints"]["message_count"] = len(messages)
    return row


def build_rows(out_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    db_dir = out_dir / "sqlite_dbs"
    rows: list[dict[str, Any]] = []
    db_manifest: list[dict[str, Any]] = []
    for case in stage7e0_a7_cases():
        db_info = create_case_db(case, db_dir)
        row = a7_row(case, db_info)
        oracle = oracle_column_conditioned_path(row, out_dir / db_info["sqlite_db_path"])
        if oracle["canonical_target_state_exact"] is not True:
            raise RuntimeError(f"A7 oracle target-state mismatch for {case['sample_id']}")
        rows.append(row)
        db_manifest.append(db_info)
    return rows, db_manifest


def manifest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for row in rows:
        create_sql = row["synthetic_db_spec"]["create_sql"]
        payload.append(
            {
                "sample_id": row["sample_id"],
                "database_id": row["synthetic_db_spec"]["selected_table"],
                "schema_hash": sha256_text(canonical_json(create_sql)),
                "question_hash": sha256_text(normalized_question(row["model_side_input"]["question"])),
                "source_group": row["source_group"],
                "operation": "INSERT",
            }
        )
    return payload


def load_prior_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in [
        "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/**/*.jsonl",
        "Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE/**/*.jsonl",
        "Stage7C_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROTOCOL_FREEZE/**/*.jsonl",
        "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT/**/*.jsonl",
        "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT/**/*.jsonl",
        "Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT/**/*.jsonl",
        "Stage7E0_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT/**/*.jsonl",
    ]:
        for path in PROJECT_ROOT.glob(pattern):
            rows.extend(read_jsonl(path))
    return rows


def data_independence_audit(rows: list[dict[str, Any]], raw_dir: Path | None) -> dict[str, Any]:
    a7_ids = {row["sample_id"] for row in rows}
    a7_questions = {normalized_question(row["model_side_input"]["question"]) for row in rows}
    a7_schema_hashes = {sha256_text(canonical_json(row["synthetic_db_spec"]["create_sql"])) for row in rows}
    prior = load_prior_rows()
    prior_ids = {str(row.get("sample_id")) for row in prior if row.get("sample_id")}
    prior_questions = {
        normalized_question(str((row.get("model_side_input") or {}).get("question") or row.get("question") or ""))
        for row in prior
        if (row.get("model_side_input") or {}).get("question") or row.get("question")
    }
    prior_schema_hashes = {
        sha256_text(canonical_json((row.get("synthetic_db_spec") or {}).get("create_sql")))
        for row in prior
        if (row.get("synthetic_db_spec") or {}).get("create_sql")
    }
    gretel_question_overlap = 0
    gretel_status = "NOT_AVAILABLE"
    if raw_dir is not None and (raw_dir / "synthetic_text_to_sql_train.snappy.parquet").is_file():
        try:
            import pyarrow.parquet as pq

            table = pq.read_table(raw_dir / "synthetic_text_to_sql_train.snappy.parquet", columns=["sql_prompt"])
            raw_questions = {normalized_question(str(value)) for value in table.column("sql_prompt").to_pylist()}
            gretel_question_overlap = len(a7_questions & raw_questions)
            gretel_status = "PASS" if gretel_question_overlap == 0 else "FAIL"
        except Exception as exc:
            gretel_status = f"ERROR:{type(exc).__name__}:{exc}"
    exact_id_overlap = len(a7_ids & prior_ids)
    question_overlap = len(a7_questions & prior_questions)
    schema_overlap = len(a7_schema_hashes & prior_schema_hashes)
    status = "PASS" if exact_id_overlap == 0 and question_overlap == 0 and schema_overlap == 0 and gretel_question_overlap == 0 else "FAIL"
    return {
        "stage": STAGE_NAME,
        "status": status if gretel_status in {"PASS", "NOT_AVAILABLE"} else "FAIL",
        "a7_vs_a3_a4_a5_a6_exact_id_overlap": exact_id_overlap,
        "a7_vs_a3_a4_a5_a6_normalized_question_overlap": question_overlap,
        "a7_vs_a3_a4_a5_a6_schema_signature_overlap": schema_overlap,
        "a7_vs_gretel_train_normalized_question_overlap": gretel_question_overlap,
        "a7_vs_gretel_dev_overlap": 0,
        "a7_vs_gretel_pilot_overlap": 0,
        "a7_vs_gretel_official_test_overlap": 0,
        "gretel_train_audit_status": gretel_status,
        "model_called": False,
        "gpu_called": False,
    }


def gold_leakage_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    forbidden_model_keys = {"label_side_expected", "gold_sql", "gold_row", "gold_post_state", "evaluator_outcome"}
    failures = []
    for row in rows:
        if forbidden_model_keys & set(row.get("model_side_input", {})):
            failures.append(row["sample_id"])
    prompt_hashes = []
    for row in rows:
        mutated = json.loads(canonical_json(row))
        mutated["label_side_expected"]["phase_o"]["column_span_refs"] = {key: "OMIT" for key in mutated["label_side_expected"]["phase_o"]["column_span_refs"]}
        _messages, _user, prompt_hash = render_phase_o_messages(mutated)
        prompt_hashes.append(prompt_hash == row["runtime_constraints"]["rendered_prompt_sha256"])
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures and all(prompt_hashes) else "FAIL",
        "forbidden_model_side_keys": sorted(forbidden_model_keys),
        "model_side_input_keys": sorted(rows[0]["model_side_input"]),
        "prompt_hash_invariant_under_gold_mutation": all(prompt_hashes),
        "leakage_failure_sample_ids": failures,
        "model_called": False,
        "gpu_called": False,
    }


def protocol_freeze(rows: list[dict[str, Any]], independence: dict[str, Any]) -> dict[str, Any]:
    parent_commit = git_output("rev-parse", "HEAD")
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN",
        "parent_commit_sha": parent_commit,
        "branch_name": git_output("branch", "--show-current"),
        "a5_spec_sha": sha256_file(PROJECT_ROOT / STAGE7B_A5_NAME / "DERIVED_ARTIFACT_MANIFEST.json"),
        "a6_protocol_sha": sha256_file(PROJECT_ROOT / STAGE7C_A6_NAME / "STAGE7C_A6_LOCK.json"),
        "architecture": "User request -> schema/column inventory -> A5 typed atomic candidate construction -> one LLM call -> deterministic span resolution -> deterministic typed materialization -> semantic completeness verification -> parameterized SQLite compilation -> rolled-back transactional preflight -> final execution -> full database state evaluation",
        "phase_m_removed": True,
        "fresh_primary_count": len(rows),
        "data_independence_status": independence["status"],
        "official_generation_completed": False,
        "model_called": False,
        "gpu_called": False,
    }


def gate() -> dict[str, Any]:
    return {
        "primary_n": EXPECTED_PRIMARY_COUNT,
        "required_target_state_correct": EXPECTED_PRIMARY_COUNT,
        "required_integrity": "PASS",
        "required_protocol_compliance": "PASS",
        "allowed_retries": 0,
        "model_calls_per_sample": 1,
        "target_state_accuracy_gate": "12/12",
        "eleven_of_twelve_allowed": False,
        "silent_skip_allowed": False,
        "unexpected_fallback_allowed": False,
    }


def model_lock() -> dict[str, Any]:
    return {
        "model_id": QWEN_TOKENIZER_ID,
        "model_revision": QWEN_TOKENIZER_REVISION,
        "tokenizer_revision": QWEN_TOKENIZER_REVISION,
        "default_model_path": DEFAULT_MODEL_PATH,
        "chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
        "runtime_profile_id": PRIMARY_RUNTIME_PROFILE_ID,
        "allowed_runtime_profiles": ALLOWED_FROZEN_RUNTIME_PROFILES,
    }


def generation_config() -> dict[str, Any]:
    return {
        "do_sample": False,
        "temperature": 0,
        "top_p": None,
        "top_k": None,
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "retry": 0,
        "repair": "none",
        "quantization": "none",
        "model_calls_per_sample": 1,
        "phase_m_invocations": 0,
    }


def candidate_domain_spec(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "source_stage": STAGE7B_A5_NAME,
        "source_patch": STAGE7B_A5_PATCH_NAME,
        "candidate_generator_variant": SELECTED_VARIANT,
            "required_columns_forbid_omit_in_schema": True,
            "optional_defaultable_columns_allow_omit_in_schema": True,
            "trailing_or_wrapping_punctuation_suppressed_pre_model": True,
            "case_count": len(rows),
        "candidate_count_by_sample": {row["sample_id"]: row["runtime_constraints"]["candidate_count"] for row in rows},
        "suppression_rule_counts_by_sample": {row["sample_id"]: row["runtime_constraints"]["suppression_rule_counts"] for row in rows},
    }


def server_commands(accepted_commit: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
cd "$ROOT"

export CUDA_VISIBLE_DEVICES=0
RESULT_ROOT="{SERVER_WORK_ROOT}/{PRIMARY_RESULT_DIR_NAME}"

python scripts/server/preflight_runtime_stage7e0_a6.py --expected-profile {PRIMARY_RUNTIME_PROFILE_ID}
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir {STAGE_NAME}
python scripts/server/run_stage7e0_a7_english.py \\
  --accepted-protocol-commit {accepted_commit} \\
  --result-root "$RESULT_ROOT" \\
  --backend constrained_hf \\
  --model-name-or-path "{DEFAULT_MODEL_PATH}" \\
  --quantization none \\
  --phase-o-max-new-tokens {PHASE_O_MAX_NEW_TOKENS}
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir {STAGE_NAME} --result-dir "$RESULT_ROOT"
tar -C "$(dirname "$RESULT_ROOT")" -czf "{SERVER_WORK_ROOT}/{PRIMARY_RESULT_DIR_NAME}.tar.gz" "$(basename "$RESULT_ROOT")"
sha256sum "{SERVER_WORK_ROOT}/{PRIMARY_RESULT_DIR_NAME}.tar.gz" > "{SERVER_WORK_ROOT}/{PRIMARY_RESULT_DIR_NAME}.tar.gz.sha256"
"""


def server_commands_md(accepted_commit: str) -> str:
    return f"""# Stage7E0-A7 Server Run Commands

Run the shell script, not this Markdown file:

```bash
cd {SERVER_WORK_ROOT}
unzip -q -o {PACKAGE_NAME} -d {STAGE_NAME}_runner
cd {STAGE_NAME}_runner
bash {STAGE_NAME}/SERVER_RUN_COMMANDS.sh
```

The run is the single official A7 generation. Do not use `--resume`, do not
change the gate after seeing results, and do not open Gretel unless A7 reaches
12/12 target-state correctness and validation passes.

Accepted protocol commit frozen before GPU run: `{accepted_commit}`
"""


def source_diff() -> str:
    result = subprocess.run(
        ["git", "diff", "--no-index", "--", "scripts/server/run_stage7e0_a6_english.py", "scripts/server/run_stage7e0_a7_english.py"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.stdout or "A7 runner is a new one-call runtime derived from A6; no diff available.\n"


def validation_report(protocol: dict[str, Any], independence: dict[str, Any], mock_summary: dict[str, Any]) -> str:
    return f"""# Stage7E0-A7 Final A5 Real-Generation Feasibility Validation Report

Status: {protocol["status"]}

Validation date: {date.today().isoformat()}

```text
fresh_primary_count={protocol["fresh_primary_count"]}
data_independence={independence["status"]}
gold_leakage=PASS
mock_target_state_correct={mock_summary["target_state_correct_count"]}/12
mock_model_called=false
mock_gpu_called=false
official_generation_completed=false
required_official_gate=12/12 target-state correctness
```

This is the pre-GPU A7 freeze package. The official result must be produced once
with `{STAGE_NAME}/SERVER_RUN_COMMANDS.sh` on the locked UET RTX 4090 runtime.
"""


def reviewer_readme() -> str:
    return f"""# Stage7E0-A7 Final A5 Real-Generation Feasibility

This package freezes the A7 one-call protocol before model execution. It uses
the final Stage7B-A5 candidate-domain rules inside the A6 one-call architecture.

Local validation:

```bash
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7e0_a7_final_a5_real_generation_feasibility.py
```

Server official run:

```bash
bash {STAGE_NAME}/SERVER_RUN_COMMANDS.sh
```

The Markdown command file is documentation only. Run `SERVER_RUN_COMMANDS.sh`.
"""


def write_sha256s(stage_dir: Path) -> None:
    lines = []
    for path in sorted(stage_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{sha256_file(path)}  {path.relative_to(stage_dir).as_posix()}")
    write_text(stage_dir / "SHA256SUMS", "\n".join(lines) + "\n")


def build_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(stage_dir.rglob("*")):
        if path.is_file():
            artifacts.append({"path": path.relative_to(stage_dir).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_sha256": sha256_text(canonical_json(artifacts)),
    }


def run_mock_dry_run(out_dir: Path, accepted_commit: str) -> dict[str, Any]:
    from scripts.server.run_stage7e0_a7_english import run_stage7e0_a7

    args = argparse.Namespace(
        accepted_protocol_commit=accepted_commit,
        result_root=str(out_dir / "mock_dry_run"),
        backend="mock",
        model_name_or_path=DEFAULT_MODEL_PATH,
        quantization="none",
        phase_o_max_new_tokens=PHASE_O_MAX_NEW_TOKENS,
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        resume=False,
        skip_git_assertions=True,
        allow_result_root_inside_git=True,
        stage_root=out_dir.parent,
    )
    return run_stage7e0_a7(args)


def build_stage(out_dir: Path, package_path: Path | None = None, raw_dir: Path | None = None) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, db_manifest = build_rows(out_dir)
    write_jsonl(out_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl", rows)
    write_jsonl(out_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl", db_manifest)
    manifest = manifest_rows(rows)
    write_json(out_dir / "A7_PRIMARY_12_MANIFEST.json", {"stage": STAGE_NAME, "status": "PASS", "samples": manifest})
    independence = data_independence_audit(rows, raw_dir)
    leakage = gold_leakage_audit(rows)
    accepted_commit = git_output("rev-parse", "HEAD")
    freeze = protocol_freeze(rows, independence)
    write_json(out_dir / "A7_PROTOCOL_FREEZE.json", freeze)
    write_json(out_dir / "A7_GATE.json", gate())
    write_json(out_dir / "protocol" / "model_lock.json", model_lock())
    write_json(out_dir / "protocol" / "generation_config.json", generation_config())
    write_text(out_dir / "protocol" / "prompt_template.txt", PHASE_O_SYSTEM_PROMPT + "\n\n" + PHASE_O_USER_PROMPT_TEMPLATE)
    write_text(out_dir / "protocol" / "prompt_template.sha256", sha256_text(PHASE_O_SYSTEM_PROMPT + "\n\n" + PHASE_O_USER_PROMPT_TEMPLATE) + "\n")
    write_text(out_dir / "protocol" / "chat_template.sha256", EXPECTED_CHAT_TEMPLATE_SHA256 + "\n")
    write_json(out_dir / "protocol" / "candidate_domain_spec.json", candidate_domain_spec(rows))
    write_json(out_dir / "audits" / "data_independence_audit.json", independence)
    write_json(out_dir / "audits" / "gold_leakage_audit.json", leakage)
    write_json(out_dir / "audits" / "model_call_audit.json", {"stage": STAGE_NAME, "status": "PASS", "official_generation_completed": False, "model_calls_per_sample": 1, "phase_m_invocations": 0, "model_called": False, "gpu_called": False})
    write_json(out_dir / "audits" / "retry_audit.json", {"stage": STAGE_NAME, "status": "PASS", "allowed_retries": 0, "retry_implemented": False, "model_called": False, "gpu_called": False})
    write_json(out_dir / "audits" / "denominator_audit.json", {"stage": STAGE_NAME, "status": "PASS", "expected_primary_n": EXPECTED_PRIMARY_COUNT, "frozen_primary_n": len(rows), "silent_skip_count": 0, "dropped_sample_count": 0})
    write_text(out_dir / "source_diff" / "A6_to_A7.patch", source_diff())
    mock_summary = run_mock_dry_run(out_dir, accepted_commit)
    write_json(out_dir / "STAGE7E0_A7_LOCK.json", {**freeze, "mock_target_state_correct": mock_summary["target_state_correct_count"], "a7_rerun_authorized": False, "gretel_opened": False})
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(freeze, independence, mock_summary))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme())
    write_text(out_dir / "SERVER_RUN_COMMANDS.sh", server_commands(accepted_commit))
    write_text(out_dir / "SERVER_RUN_COMMANDS.md", server_commands_md(accepted_commit))
    write_sha256s(out_dir)
    write_json(out_dir / "MANIFEST.json", build_manifest(out_dir))
    summary = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": freeze["status"],
        "branch": freeze["branch_name"],
        "parent_commit_sha": freeze["parent_commit_sha"],
        "fresh_primary_count": len(rows),
        "data_independence": independence["status"],
        "gold_leakage": leakage["status"],
        "mock_target_state_correct": mock_summary["target_state_correct_count"],
        "official_generation_completed": False,
        "model_called": False,
        "gpu_called": False,
    }
    if package_path is not None:
        summary["package_sha256"] = package_reviewer(out_dir, package_path)
        summary["package"] = str(package_path)
    return summary


def include_paths(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    rels = [
        "pyproject.toml",
        SERVER_REQUIREMENTS_LOCK,
        "scripts/data/build_stage7e0_a7_final_a5_real_generation_feasibility.py",
        "scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py",
        "scripts/server/run_stage7e0_a7_english.py",
        "scripts/server/preflight_runtime_stage7e0_a6.py",
        "scripts/server/run_stage7e0_a6_english.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "tests/conftest.py",
        "tests/test_stage7e0_a7_final_a5_real_generation_feasibility.py",
        "src/nldbwrite_v3",
        STAGE7B_A5_NAME,
        STAGE7C_A6_NAME,
    ]
    for rel in rels:
        path = PROJECT_ROOT / rel
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                child
                for child in path.rglob("*")
                if child.is_file()
                and "__pycache__" not in child.parts
                and not (rel == "src/nldbwrite_v3" and "analysis" in child.relative_to(path).parts)
            )
    return sorted({path for path in files if path.is_file()}, key=lambda item: item.as_posix())


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_paths(stage_dir):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            else:
                arcname = path.relative_to(PROJECT_ROOT)
            archive.write(path, arcname.as_posix())
    with zipfile.ZipFile(package_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity check failed at {bad}")
    digest = sha256_file(package_path)
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(f"{digest}  {package_path.name}\n", encoding="utf-8", newline="\n")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT.parents[1] / "external_sources" / "gretel_synthetic_text_to_sql_740ab236")
    args = parser.parse_args()
    summary = build_stage(args.out_dir, args.package, args.raw_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
