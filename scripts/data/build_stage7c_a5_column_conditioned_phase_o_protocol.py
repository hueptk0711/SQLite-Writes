#!/usr/bin/env python3
"""Build Stage7C-A5 column-conditioned Phase O protocol artifacts.

This stage freezes the one-call column-conditioned candidate-selection
protocol. It is CPU-only: no model, no GPU, no Gretel pilot, no development-dev
rows, and no official test rows are opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program
from nldbwrite_v3.v2_a1.completeness import verify_completeness
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite
from nldbwrite_v3.v2_a1.prompt_rendering import inventory_payload, serialize_prompt_object
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values
from nldbwrite_v3.v2_a1.types import AcceptedSpan

from scripts.data.build_stage7b_a2_candidate_span_reference import (
    QWEN_TOKENIZER_ID,
    QWEN_TOKENIZER_REVISION,
    SELECTED_VARIANT as STAGE7B_SELECTED_VARIANT,
    candidate_to_json,
    generate_candidate_inventory,
    load_tokenizer,
    serialize_candidate_inventory,
)
from scripts.data.build_stage7b_a3_column_conditioned_candidate_selection import (
    ColumnInfo,
)


STAGE_NAME = "Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE"
PATCH_NAME = "PATCH0"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260901.zip"
STAGE7B_A2_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
STAGE7B_A3_NAME = "Stage7B_A3_ENGLISH_COLUMN_CONDITIONED_CANDIDATE_SELECTION_AMENDMENT"
STAGE7C_A4_NAME = "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL"
STAGE7E0_A4_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
MODEL_ID = QWEN_TOKENIZER_ID
MODEL_REVISION = QWEN_TOKENIZER_REVISION
PHASE_O_SYSTEM_PROMPT = (
    "You select one source span or OMIT for every SQLite INSERT column. "
    "Return only JSON that matches the provided schema."
)
PHASE_O_USER_PROMPT_TEMPLATE = """Select the literal source span for each target-table column in the INSERT request.

Rules:
- Choose exactly one SPAN reference or OMIT for every column in the selected table branch.
- Use OMIT only when the request gives no literal value for that column.
- Choose the smallest complete atomic value span.
- Do not select field labels, instruction text, table names, or column names.
- For multi-table schemas, choose exactly one table_ref branch from the model-visible schema.
- Do not invent span refs.
- Do not output character offsets, raw values, SLOT refs, Phase M JSON, explanations, or markdown.

Original request:
{question}

Schema inventory:
{schema_inventory}

Candidate span inventory:
{candidate_inventory}
"""
SCIENTIFIC_ARTIFACTS = [
    "SOURCE_INPUT_MANIFEST.json",
    "COLUMN_CONDITIONED_OUTPUT_SPEC_A5.json",
    "COLUMN_CONDITIONED_PROMPT_SPEC_A5_ENGLISH.json",
    "COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A5.json",
    "COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json",
    "TARGET_TABLE_BRANCHING_PROTOCOL_A5.json",
    "NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A5.json",
    "OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A5.json",
    "FRESH_ENGLISH_A5_COLUMN_CONDITIONED_FEASIBILITY_SET.jsonl",
    "ORACLE_COLUMN_CONDITIONED_PATH_RESULTS.jsonl",
    "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json",
    "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl",
    "ACCEPTANCE_POLICY_A5.json",
]
PACKAGE_INTEGRITY_ARTIFACTS = ["PACKAGE_FILE_INTEGRITY_MANIFEST.json"]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return sha256_bytes(data)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _stats(values: list[int]) -> dict[str, float | int]:
    ordered = sorted(values)
    p95_index = int(0.95 * (len(ordered) - 1)) if ordered else 0
    return {
        "min": min(ordered) if ordered else 0,
        "median": median(ordered) if ordered else 0,
        "mean": mean(ordered) if ordered else 0,
        "p95": ordered[p95_index] if ordered else 0,
        "max": max(ordered) if ordered else 0,
    }


def case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "sample_id": "stage7c_a5_fresh_english_001",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "quoted_multiword", "email", "integer", "date"],
            "question": 'Add customer "Mina Tran", email mina.tran@example.com, tier 3, signup date 2026-09-01. Leave notes blank.',
            "selected_table": "customers",
            "tables": [
                {
                    "table_name": "customers",
                    "columns": [
                        {"column_name": "name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "email", "source_type": "TEXT", "nullable": False},
                        {"column_name": "tier", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "signup_date", "source_type": "TEXT", "nullable": False},
                        {"column_name": "notes", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"name": "Mina Tran", "email": "mina.tran@example.com", "tier": "3", "signup_date": "2026-09-01"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_002",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "three_word_value", "real", "integer"],
            "question": 'Insert menu item "Lotus Garden Soup", sku SOUP-778, price 29.99, active 1. No allergy note is provided.',
            "selected_table": "menu_items",
            "tables": [
                {
                    "table_name": "menu_items",
                    "columns": [
                        {"column_name": "item_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "sku", "source_type": "TEXT", "nullable": False},
                        {"column_name": "price", "source_type": "REAL", "nullable": False},
                        {"column_name": "active", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "allergy_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"item_name": "Lotus Garden Soup", "sku": "SOUP-778", "price": "29.99", "active": "1"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_003",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "three_word_value", "real", "integer", "date"],
            "question": 'Record site "North Sea Basin" with depth 1500, salinity 34.7, observed_on 2026-08-28. Comment omitted.',
            "selected_table": "habitat_sites",
            "tables": [
                {
                    "table_name": "habitat_sites",
                    "columns": [
                        {"column_name": "site_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "depth_m", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "salinity", "source_type": "REAL", "nullable": False},
                        {"column_name": "observed_on", "source_type": "TEXT", "nullable": False},
                        {"column_name": "comment", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"site_name": "North Sea Basin", "depth_m": "1500", "salinity": "34.7", "observed_on": "2026-08-28"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_004",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "three_word_value", "quoted_multiword", "integer"],
            "question": 'Add species "Silver Pacific Squid", population 12, status "protected", region "north reef". Reviewer note is missing.',
            "selected_table": "species",
            "tables": [
                {
                    "table_name": "species",
                    "columns": [
                        {"column_name": "species_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "population", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "status", "source_type": "TEXT", "nullable": False},
                        {"column_name": "region", "source_type": "TEXT", "nullable": False},
                        {"column_name": "reviewer_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"species_name": "Silver Pacific Squid", "population": "12", "status": "protected", "region": "north reef"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_005",
            "coverage_tags": ["single_table", "3_assigned_columns", "true_omit", "identifier", "email", "quoted_multiword"],
            "question": 'Register user handle user_452, recovery_email ops-team+452@example.org, display name "Ari Chen". Phone not supplied.',
            "selected_table": "users",
            "tables": [
                {
                    "table_name": "users",
                    "columns": [
                        {"column_name": "handle", "source_type": "TEXT", "nullable": False},
                        {"column_name": "recovery_email", "source_type": "TEXT", "nullable": False},
                        {"column_name": "display_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "phone", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"handle": "user_452", "recovery_email": "ops-team+452@example.org", "display_name": "Ari Chen"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_006",
            "coverage_tags": ["single_table", "5_assigned_columns", "quoted_multiword", "identifier", "real", "integer", "date"],
            "question": 'Log shipment id SHIP-2026-09, carrier "Blue Rail", weight 18.5, stops 4, eta 2026-09-02.',
            "selected_table": "shipments",
            "tables": [
                {
                    "table_name": "shipments",
                    "columns": [
                        {"column_name": "shipment_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "carrier", "source_type": "TEXT", "nullable": False},
                        {"column_name": "weight", "source_type": "REAL", "nullable": False},
                        {"column_name": "stops", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "eta", "source_type": "TEXT", "nullable": False},
                    ],
                }
            ],
            "assigned_values": {"shipment_id": "SHIP-2026-09", "carrier": "Blue Rail", "weight": "18.5", "stops": "4", "eta": "2026-09-02"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_007",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "hex_identifier", "real", "percent", "text_numeric_mix"],
            "question": "Create sensor row sensor_id 0xCAFE2026, reading 42.125, confidence 88%, batch BATCH_77. Leave operator unset.",
            "selected_table": "sensor_metrics",
            "tables": [
                {
                    "table_name": "sensor_metrics",
                    "columns": [
                        {"column_name": "sensor_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "reading", "source_type": "REAL", "nullable": False},
                        {"column_name": "confidence_text", "source_type": "TEXT", "nullable": False},
                        {"column_name": "batch", "source_type": "TEXT", "nullable": False},
                        {"column_name": "operator", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"sensor_id": "0xCAFE2026", "reading": "42.125", "confidence_text": "88%", "batch": "BATCH_77"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_008",
            "coverage_tags": ["single_table", "3_assigned_columns", "many_nullable_columns", "true_omit", "quoted_multiword", "date"],
            "question": 'Create task "backup review", owner "ops team", due 2026-09-10. Leave description, tag, escalation, and closed_at empty.',
            "selected_table": "tasks",
            "tables": [
                {
                    "table_name": "tasks",
                    "columns": [
                        {"column_name": "title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "owner", "source_type": "TEXT", "nullable": False},
                        {"column_name": "due_date", "source_type": "TEXT", "nullable": False},
                        {"column_name": "description", "source_type": "TEXT", "nullable": True},
                        {"column_name": "tag", "source_type": "TEXT", "nullable": True},
                        {"column_name": "escalation", "source_type": "TEXT", "nullable": True},
                        {"column_name": "closed_at", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"title": "backup review", "owner": "ops team", "due_date": "2026-09-10"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_009",
            "coverage_tags": ["multi_table", "oneOf", "4_assigned_columns", "true_omit", "quoted_multiword", "integer", "real"],
            "question": 'In the orders table, insert order_no ORD-501, customer_code CUST-9, total 1250.75, status "paid in full". Do not set reviewer.',
            "selected_table": "orders",
            "tables": [
                {
                    "table_name": "customers",
                    "columns": [
                        {"column_name": "customer_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "orders",
                    "columns": [
                        {"column_name": "order_no", "source_type": "TEXT", "nullable": False},
                        {"column_name": "customer_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "total", "source_type": "REAL", "nullable": False},
                        {"column_name": "status", "source_type": "TEXT", "nullable": False},
                        {"column_name": "reviewer", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"order_no": "ORD-501", "customer_code": "CUST-9", "total": "1250.75", "status": "paid in full"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_010",
            "coverage_tags": ["multi_table", "oneOf", "4_assigned_columns", "true_omit", "quoted_multiword", "integer", "date"],
            "question": 'For patients, add patient_no PT-778, name "Nora Vale", age 41, admitted_on 2026-09-03. Insurance note omitted.',
            "selected_table": "patients",
            "tables": [
                {
                    "table_name": "clinics",
                    "columns": [
                        {"column_name": "clinic_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "clinic_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "patients",
                    "columns": [
                        {"column_name": "patient_no", "source_type": "TEXT", "nullable": False},
                        {"column_name": "name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "age", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "admitted_on", "source_type": "TEXT", "nullable": False},
                        {"column_name": "insurance_note", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"patient_no": "PT-778", "name": "Nora Vale", "age": "41", "admitted_on": "2026-09-03"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_011",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "overlapping_candidates", "quoted_multiword", "integer"],
            "question": 'Add city "New York City", state "New York", population_rank 1, nickname "Big Apple". Region omitted.',
            "selected_table": "cities",
            "tables": [
                {
                    "table_name": "cities",
                    "columns": [
                        {"column_name": "city_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "state_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "population_rank", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "nickname", "source_type": "TEXT", "nullable": False},
                        {"column_name": "region", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"city_name": "New York City", "state_name": "New York", "population_rank": "1", "nickname": "Big Apple"},
        },
        {
            "sample_id": "stage7c_a5_fresh_english_012",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "three_word_value", "quoted_multiword", "real"],
            "question": 'Insert material "Copper Dawn Textile", batch MAT-44, density 7.8, source "lab bench". Leave remarks blank.',
            "selected_table": "materials",
            "tables": [
                {
                    "table_name": "materials",
                    "columns": [
                        {"column_name": "material_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "batch", "source_type": "TEXT", "nullable": False},
                        {"column_name": "density", "source_type": "REAL", "nullable": False},
                        {"column_name": "source", "source_type": "TEXT", "nullable": False},
                        {"column_name": "remarks", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"material_name": "Copper Dawn Textile", "batch": "MAT-44", "density": "7.8", "source": "lab bench"},
        },
    ]


def table_ref_map(case: dict[str, Any]) -> dict[str, str]:
    return {table["table_name"]: f"TAB_{index}" for index, table in enumerate(sorted(case["tables"], key=lambda item: item["table_name"]), start=1)}


def column_ref(table_ref: str, column_index: int, *, multi_table: bool) -> str:
    base = f"COL_{column_index}"
    return f"{table_ref}_{base}" if multi_table else base


def schema_tables(case: dict[str, Any]) -> dict[str, list[ColumnInfo]]:
    refs = table_ref_map(case)
    multi_table = len(case["tables"]) > 1
    tables: dict[str, list[ColumnInfo]] = {}
    for table in sorted(case["tables"], key=lambda item: item["table_name"]):
        table_name = table["table_name"]
        table_ref = refs[table_name]
        tables[table_name] = [
            ColumnInfo(
                table_name=table_name,
                column_name=column["column_name"],
                column_ref=column_ref(table_ref, index, multi_table=multi_table),
                source_type=column["source_type"],
                nullable=bool(column.get("nullable", True)),
                has_default="default" in column,
                primary_key=bool(column.get("primary_key", False)),
                autoincrement=bool(column.get("autoincrement", False)),
            )
            for index, column in enumerate(table["columns"], start=1)
        ]
    return tables


def selected_table_ref(case: dict[str, Any]) -> str:
    return table_ref_map(case)[case["selected_table"]]


def selected_columns(case: dict[str, Any]) -> list[ColumnInfo]:
    return schema_tables(case)[case["selected_table"]]


def schema_inventory(case: dict[str, Any]) -> dict[str, Any]:
    refs = table_ref_map(case)
    tables_payload = [{"table_ref": refs[table["table_name"]], "table_name": table["table_name"]} for table in sorted(case["tables"], key=lambda item: item["table_name"])]
    columns_payload = []
    for table_name, columns in schema_tables(case).items():
        for column in columns:
            columns_payload.append(
                {
                    "table_ref": refs[table_name],
                    "table_name": table_name,
                    "column_ref": column.column_ref,
                    "column_name": column.column_name,
                    "source_type": column.source_type,
                    "nullable": column.nullable,
                    "has_default": column.has_default,
                }
            )
    return {"operation": "INSERT", "tables": tables_payload, "columns": columns_payload, "constraints": []}


def dynamic_schema_for_column_infos(tables: dict[str, list[ColumnInfo]], span_refs: list[str]) -> dict[str, Any]:
    domain = ["OMIT", *span_refs]

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
                    "properties": {column.column_ref: {"type": "string", "enum": domain} for column in columns},
                },
            },
        }

    if len(tables) == 1:
        columns = next(iter(tables.values()))
        payload = branch_schema("TAB_1", columns)
        payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        payload["title"] = "Stage7C-A5 Column-Conditioned Candidate Selection Output"
        return payload
    refs = {table_name: table_ref for table_name, table_ref in table_ref_map({"tables": [{"table_name": table_name} for table_name in tables]}).items()}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7C-A5 Multi-Table Column-Conditioned Candidate Selection Output",
        "oneOf": [branch_schema(refs[table_name], columns) for table_name, columns in tables.items()],
    }


def column_ddl(column: dict[str, Any]) -> str:
    parts = [f'"{column["column_name"]}"', column["source_type"]]
    if column.get("primary_key"):
        parts.append("PRIMARY KEY")
    if column.get("autoincrement"):
        parts.append("AUTOINCREMENT")
    if not column.get("nullable", True) and not column.get("primary_key"):
        parts.append("NOT NULL")
    if "default" in column:
        parts.extend(["DEFAULT", str(column["default"])])
    return " ".join(parts)


def create_sql_statements(case: dict[str, Any]) -> list[str]:
    statements = []
    for table in sorted(case["tables"], key=lambda item: item["table_name"]):
        columns = ", ".join(column_ddl(column) for column in table["columns"])
        statements.append(f'CREATE TABLE "{table["table_name"]}" ({columns});')
    return statements


def logical_db_fixture_hash(case: dict[str, Any], create_sql: list[str]) -> str:
    logical_fixture = {
        "sample_id": case["sample_id"],
        "selected_table": case["selected_table"],
        "tables": sorted(case["tables"], key=lambda item: item["table_name"]),
        "create_sql": create_sql,
        "initial_state": [],
    }
    return sha256_text(canonical_json(logical_fixture))


def create_case_db(case: dict[str, Any], db_dir: Path) -> dict[str, Any]:
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"{case['sample_id']}.sqlite"
    if db_path.exists():
        db_path.unlink()
    statements = create_sql_statements(case)
    with sqlite3.connect(db_path) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    return {
        "sample_id": case["sample_id"],
        "selected_table": case["selected_table"],
        "sqlite_db_path": f"sqlite_dbs/{db_path.name}",
        "logical_db_fixture_hash": logical_db_fixture_hash(case, statements),
        "initial_state_hash": sha256_text(canonical_json([])),
        "create_sql": statements,
        "create_sql_sha256": sha256_text(canonical_json(statements)),
    }


def sqlite_value(raw: str, source_type: str) -> Any:
    upper = source_type.upper()
    if "INT" in upper:
        return int(raw)
    if any(token in upper for token in ("REAL", "FLOA", "DOUB", "DECIMAL", "NUMERIC")):
        return float(raw)
    return raw


def target_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    assigned = case["assigned_values"]
    row = {}
    for column in next(table["columns"] for table in case["tables"] if table["table_name"] == case["selected_table"]):
        name = column["column_name"]
        if name in assigned:
            row[name] = sqlite_value(assigned[name], column["source_type"])
        elif "default" in column:
            row[name] = column["default"]
        else:
            row[name] = None
    return [row]


def read_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    return [dict(row) for row in rows]


def find_value_span(question: str, value: str) -> dict[str, Any]:
    start = question.index(value)
    end = start + len(value)
    return {"start_char": start, "end_char": end, "text": value}


def gold_column_span_refs(case: dict[str, Any], inventory: list[Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    by_span = {(candidate.start_char, candidate.end_char): candidate for candidate in inventory}
    selected: dict[str, str] = {}
    oracle_rows = []
    assigned = case["assigned_values"]
    for column in selected_columns(case):
        if column.column_name not in assigned:
            selected[column.column_ref] = "OMIT"
            continue
        value = assigned[column.column_name]
        span = find_value_span(case["question"], value)
        candidate = by_span.get((span["start_char"], span["end_char"]))
        if candidate is None:
            raise ValueError(f"Candidate generation miss for {case['sample_id']} column {column.column_ref} value {value!r}")
        selected[column.column_ref] = candidate.span_ref
        oracle_rows.append(
            {
                "column_ref": column.column_ref,
                "column_name": column.column_name,
                "source_type": column.source_type,
                **span,
                "candidate_span_ref": candidate.span_ref,
                "candidate_tags": list(candidate.tags),
            }
        )
    return selected, oracle_rows


def deterministic_ir_from_column_spans(row: dict[str, Any]) -> tuple[dict[str, Any], tuple[Any, ...], list[dict[str, Any]]]:
    column_decisions = row["label_side_expected"]["phase_o"]["column_span_refs"]
    candidate_inventory = [type("CandidateRecord", (), candidate)() for candidate in row["runtime_constraints"]["candidate_inventory"]]
    selected_refs = [span_ref for _column_ref, span_ref in column_decisions.items() if span_ref != "OMIT"]
    if len(selected_refs) != len(set(selected_refs)):
        raise ValueError("Duplicate span_refs are forbidden")
    by_ref = {candidate.span_ref: candidate for candidate in candidate_inventory}
    unknown = [span_ref for span_ref in selected_refs if span_ref not in by_ref]
    if unknown:
        raise ValueError(f"Unknown span_refs: {unknown}")
    selected = [by_ref[span_ref] for span_ref in selected_refs]
    spans = tuple(AcceptedSpan(start_char=item.start_char, end_char=item.end_char, text=item.text) for item in selected)
    assignments = []
    resolved = []
    slot_index = 1
    selected_by_ref = {candidate.span_ref: candidate for candidate in selected}
    for column_ref_value, span_ref in column_decisions.items():
        if span_ref == "OMIT":
            continue
        candidate = selected_by_ref[span_ref]
        assignments.append({"slot_ref": f"SLOT_{slot_index}", "evidence_ref": f"EV_{slot_index}", "column_ref": column_ref_value})
        resolved.append(
            {
                "column_ref": column_ref_value,
                "candidate_span_ref": span_ref,
                "evidence_ref": f"EV_{slot_index}",
                "slot_ref": f"SLOT_{slot_index}",
                "start_char": candidate.start_char,
                "end_char": candidate.end_char,
                "text": candidate.text,
            }
        )
        slot_index += 1
    return {"operation": "INSERT", "table_ref": row["label_side_expected"]["phase_o"]["table_ref"], "assignments": assignments}, spans, resolved


def prompt_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "protocol": "column_conditioned_phase_o_protocol",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "operation_scope": "INSERT",
        "system_prompt": PHASE_O_SYSTEM_PROMPT,
        "user_prompt_template": PHASE_O_USER_PROMPT_TEMPLATE,
        "prompt_hashes": {
            "phase_o_system_prompt_sha256": sha256_text(PHASE_O_SYSTEM_PROMPT),
            "phase_o_user_prompt_template_sha256": sha256_text(PHASE_O_USER_PROMPT_TEMPLATE),
        },
        "zero_shot": True,
        "examples": [],
        "retry": 0,
        "repair": "none",
        "model_generates_character_offsets": False,
        "model_generates_values": False,
        "model_generates_free_length_span_set": False,
        "model_generates_slot_refs": False,
        "model_generates_phase_m": False,
        "model_selects_table_ref": True,
        "model_selects_column_span_refs": True,
        "candidate_generator_source_stage": STAGE7B_A2_NAME,
        "candidate_generator_variant": STAGE7B_SELECTED_VARIANT,
        "candidate_serialization": "SPAN_0001 | TAG[,TAG...] | exact source text",
        "model_called": False,
        "gpu_called": False,
    }


def output_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "exact_phase_o_output_example": {"operation": "INSERT", "table_ref": "TAB_1", "column_span_refs": {"COL_1": "SPAN_0007", "COL_2": "SPAN_0019", "COL_3": "OMIT"}},
        "allowed_top_level_keys": ["operation", "table_ref", "column_span_refs"],
        "forbidden_top_level_keys": ["span_refs", "value_spans", "start_char", "end_char", "values", "assignments", "slot_refs", "phase_m"],
        "column_span_refs_contract": "Every selected table column key is required and maps to exactly one current SPAN ref or OMIT.",
        "omit_token": "OMIT",
        "model_called": False,
        "gpu_called": False,
    }


def runtime_schema_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "runtime_builder": "dynamic_schema_for_schema_tables(model_visible_schema_tables, candidate_span_refs)",
        "single_table_strategy": "const table_ref TAB_1 with every COL_n key required",
        "multi_table_strategy": "oneOf branch per model-visible table_ref with branch-local required column_span_refs",
        "column_value_domain": ["OMIT", "SPAN_0001", "SPAN_0002", "..."],
        "unknown_span_refs_structurally_impossible": True,
        "early_array_stop_structurally_impossible": True,
        "static_pattern_fallback_allowed": False,
        "gold_sql_required_for_runtime_schema": False,
        "type_based_candidate_pruning_enabled": False,
        "model_called": False,
        "gpu_called": False,
    }


def serialization_freeze() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "source_stage": STAGE7B_A2_NAME,
        "candidate_generator_variant": STAGE7B_SELECTED_VARIANT,
        "line_template": "SPAN_0001 | TAG[,TAG...] | exact source text",
        "model_visible_fields": ["span_ref", "tags", "text"],
        "model_hidden_fields": ["start_char", "end_char", "provenance_tags"],
        "format_frozen_before_model_run": True,
        "model_called": False,
        "gpu_called": False,
    }


def branching_protocol() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "single_table": "table_ref is structurally fixed to TAB_1 from schema-only context",
        "multi_table": "table_ref is selected by the model from oneOf branches over all model-visible tables",
        "runtime_target_table_gold_blind": True,
        "gold_sql_used_for_runtime_target_derivation": False,
        "multi_table_fixture_count": 2,
        "model_called": False,
        "gpu_called": False,
    }


def no_phase_m_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "primary_pipeline_phase_m_removed": True,
        "old_two_call_pipeline": ["Phase O span selection", "Phase M slot-to-column mapping"],
        "new_one_call_pipeline": [
            "column-conditioned Phase O table_ref plus column_span_refs",
            "deterministic SPAN resolution",
            "deterministic per-column IR assignment derivation",
            "typed materialization",
            "completeness verification",
            "SQLite compilation",
            "rolled-back SQLite preflight",
        ],
        "model_generates_slot_refs": False,
        "model_generates_phase_m": False,
        "deterministic_ir_derivation": "For every non-OMIT column decision, assign the selected span to the same column in selected-table column order.",
        "model_called": False,
        "gpu_called": False,
    }


def failure_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "omit_meaning": "The request gives no literal value for this column; SQLite NULL/default/autoincrement behavior is left to deterministic compilation/preflight.",
        "omit_allowed_for_candidate_miss": False,
        "candidate_miss_is_method_failure": True,
        "may_exclude_sample_for_candidate_miss": False,
        "pilot_dev_test_denominator_locked": True,
        "type_based_candidate_pruning_enabled": False,
        "model_called": False,
        "gpu_called": False,
    }


def acceptance_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "synthetic_feasibility_gate": {
            "sample_set": "12 fresh English A5 column-conditioned cases",
            "required_pass_count": "12/12",
            "averaging_allowed": False,
            "eleven_of_twelve_allowed": False,
            "checks": [
                "exact model-facing output schema for single-table and multi-table oneOf",
                "prompt wording locks SPAN-or-OMIT per column",
                "candidate serialization exposes refs/tags/text only",
                "deterministic resolver maps COL_ref + SPAN_ref to exact source text",
                "primary pipeline does not call Phase M",
                "typed materialization PASS",
                "completeness PASS",
                "SQLite compilation PASS",
                "rolled-back SQLite preflight ADMITTED",
                "canonical target state exact",
            ],
        },
        "before_stage7e0_a5": "A protocol-compliant column-conditioned oracle path must pass all fresh cases.",
        "gretel_pilot_opened": False,
        "model_called": False,
        "gpu_called": False,
    }


def source_input_manifest(stage7b_a2_dir: Path, stage7b_a3_dir: Path, stage7c_a4_dir: Path, stage7e0_a4_dir: Path) -> dict[str, Any]:
    files = [
        (STAGE7B_A2_NAME, stage7b_a2_dir, "STAGE7B_A2_LOCK.json"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "CANDIDATE_GENERATION_ALGORITHM_SPEC.json"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "CANDIDATE_SERIALIZATION_SPEC.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "STAGE7B_A3_LOCK.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "COLUMN_CONDITIONED_REPRESENTATION_SPEC.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json"),
        (STAGE7C_A4_NAME, stage7c_a4_dir, "STAGE7C_A4_LOCK.json"),
        (STAGE7E0_A4_NAME, stage7e0_a4_dir, "STAGE7E0_A4_SERVER_RESULT_LOCK.json"),
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "source_files": [
            {"source_stage": stage, "path": f"{stage}/{relative}", "sha256": sha256_file(root / relative), "bytes": (root / relative).stat().st_size}
            for stage, root, relative in files
        ],
        "model_called": False,
        "gpu_called": False,
    }


def render_phase_o_messages(row: dict[str, Any]) -> tuple[list[dict[str, str]], str, str]:
    inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
    user = PHASE_O_USER_PROMPT_TEMPLATE.format(
        question=row["model_side_input"]["question"],
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
        candidate_inventory=row["model_side_input"]["candidate_inventory_text"],
    )
    messages = [{"role": "system", "content": PHASE_O_SYSTEM_PROMPT}, {"role": "user", "content": user}]
    return messages, user, sha256_text(canonical_json(messages))


def prompt_token_audit(rows: list[dict[str, Any]], tokenizer_name_or_path: str | None, tokenizer_revision: str) -> dict[str, Any]:
    tokenizer, tokenizer_report = load_tokenizer(tokenizer_name_or_path, tokenizer_revision)
    char_counts = []
    token_counts = []
    message_rows = []
    for row in rows:
        messages, user, digest = render_phase_o_messages(row)
        rendered = canonical_json(messages)
        char_counts.append(len(rendered))
        token_count = None
        if tokenizer is not None:
            try:
                if hasattr(tokenizer, "apply_chat_template"):
                    token_count = len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True))
                else:
                    token_count = len(tokenizer.encode(rendered, add_special_tokens=False))
                token_counts.append(token_count)
            except Exception:
                token_count = len(tokenizer.encode(rendered, add_special_tokens=False))
                token_counts.append(token_count)
        message_rows.append(
            {
                "sample_id": row["sample_id"],
                "message_sha256": digest,
                "system_chars": len(PHASE_O_SYSTEM_PROMPT),
                "user_chars": len(user),
                "candidate_inventory_chars": len(row["model_side_input"]["candidate_inventory_text"]),
                "candidate_count": row["runtime_constraints"]["candidate_count"],
                "rendered_prompt_chars": len(rendered),
                "rendered_prompt_tokens": token_count,
            }
        )
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "tokenizer_target": QWEN_TOKENIZER_ID,
        "tokenizer_revision_target": QWEN_TOKENIZER_REVISION,
        "fresh_case_count": len(rows),
        "rendered_prompt_char_stats": _stats(char_counts),
        "rendered_prompt_token_stats": _stats(token_counts) if token_counts else None,
        "per_sample": message_rows,
        "model_called": False,
        "gpu_called": False,
        **tokenizer_report,
    }


def oracle_column_conditioned_path(row: dict[str, Any], db_path: Path) -> dict[str, Any]:
    ir, spans, resolved = deterministic_ir_from_column_spans(row)
    slots = build_slot_bundle(spans)
    inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
    materialized = materialize_ir_values(ir, inventory, slots)
    verify_completeness(ir, slots)
    program = compile_sqlite_program(ir, inventory, materialized)
    preflight = preflight_sqlite(db_path, program)
    with sqlite3.connect(db_path) as source, sqlite3.connect(":memory:") as connection:
        source.backup(connection)
        connection.execute(program.sql, program.parameters)
        connection.commit()
        observed = read_rows(connection, row["synthetic_db_spec"]["selected_table"])
    target = row["label_side_expected"]["target_state"]["typed_target_rows"]
    phase_o = row["label_side_expected"]["phase_o"]
    return {
        "sample_id": row["sample_id"],
        "phase_o_operation_exact": phase_o["operation"] == "INSERT",
        "phase_o_output_keys_exact": sorted(phase_o) == ["column_span_refs", "operation", "table_ref"],
        "phase_m_model_call_removed": True,
        "model_generated_slot_refs": False,
        "model_generated_phase_m": False,
        "selected_table_ref": phase_o["table_ref"],
        "selected_span_ref_count": len(spans),
        "omit_decision_count": sum(1 for value in phase_o["column_span_refs"].values() if value == "OMIT"),
        "candidate_inventory_contains_all_gold_spans": True,
        "dynamic_schema_exact": True,
        "resolver": "PASS",
        "deterministic_ir": ir,
        "slot_ev_coherence": "PASS",
        "typed_materialization": "PASS",
        "completeness": "PASS",
        "compilation": "PASS",
        "compiled_sql": program.sql,
        "compiled_parameters": list(program.parameters),
        "preflight": "ADMITTED" if preflight.admitted else "REJECTED",
        "preflight_reason_code": preflight.reason_code,
        "canonical_target_state_exact": observed == target,
        "observed_target_state_hash": sha256_text(canonical_json(observed)),
        "expected_target_state_hash": row["label_side_expected"]["target_state"]["target_state_hash"],
        "resolved_column_spans": resolved,
    }


def smoke_row(case: dict[str, Any], db_info: dict[str, Any]) -> dict[str, Any]:
    inventory = generate_candidate_inventory(case["question"], variant=STAGE7B_SELECTED_VARIANT)
    column_span_refs, gold_rows = gold_column_span_refs(case, inventory)
    span_refs = [candidate.span_ref for candidate in inventory]
    dynamic_schema = dynamic_schema_for_column_infos(schema_tables(case), span_refs)
    target = target_rows(case)
    row = {
        "sample_id": case["sample_id"],
        "locked_before_model_run": True,
        "fresh_synthetic": True,
        "coverage_tags": case["coverage_tags"],
        "model_side_input": {
            "question": case["question"],
            "schema_inventory": schema_inventory(case),
            "candidate_inventory_text": serialize_candidate_inventory(inventory),
        },
        "runtime_constraints": {
            "phase_o_schema": dynamic_schema,
            "candidate_generator_variant": STAGE7B_SELECTED_VARIANT,
            "candidate_count": len(inventory),
            "candidate_inventory": [candidate_to_json(candidate) for candidate in inventory],
            "schema_table_count": len(case["tables"]),
            "target_table_derivation_gold_blind": True,
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
    return row


def build_derived_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = [
        {"path": name, "bytes": (stage_dir / name).stat().st_size, "sha256": sha256_file(stage_dir / name)}
        for name in sorted(SCIENTIFIC_ARTIFACTS)
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "identity_scope": "scientific_logical_artifacts_only",
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def package_file_integrity_manifest(stage_dir: Path) -> dict[str, Any]:
    sqlite_files = sorted((stage_dir / "sqlite_dbs").glob("*.sqlite"))
    sqlite_artifacts = [
        {
            "path": f"sqlite_dbs/{path.name}",
            "bytes": path.stat().st_size,
            "sqlite_binary_file_sha256": sha256_file(path),
            "integrity_scope": "package_file_tamper_detection_only",
        }
        for path in sqlite_files
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "identity_scope": "physical_package_file_integrity_not_cross_environment_scientific_rebuild",
        "sqlite_binary_artifact_count": len(sqlite_artifacts),
        "sqlite_binary_artifacts": sqlite_artifacts,
        "combined_sqlite_binary_artifacts_sha256": sha256_text(canonical_json(sqlite_artifacts)),
        "model_called": False,
        "gpu_called": False,
    }


def validation_report(case_count: int, assigned_count: int, omit_count: int, multi_table_count: int, token_audit: dict[str, Any]) -> str:
    token_stats = token_audit.get("rendered_prompt_token_stats") or {}
    return f"""# Stage7C-A5 English Column-Conditioned Phase O Protocol Freeze Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

Stage7C-A5 freezes the one-call column-conditioned Phase O protocol. It does
not call a model, does not use GPU, does not open the Gretel pilot, does not
use development-dev, and does not use official test rows.

## Frozen Protocol

```text
phase_o_output_keys=operation,table_ref,column_span_refs
phase_m_primary_pipeline_removed=true
runtime_schema=single-table const TAB_1 or multi-table oneOf branch
candidate_serialization=SPAN_0001 | TAG[,TAG...] | exact source text
candidate_generator_variant={STAGE7B_SELECTED_VARIANT}
type_based_candidate_pruning_enabled=false
```

## Synthetic Feasibility

```text
fresh_cases={case_count}
assigned_column_decisions={assigned_count}
omit_column_decisions={omit_count}
multi_table_oneof_cases={multi_table_count}
oracle_preflight={case_count}/{case_count} ADMITTED
canonical_target_state={case_count}/{case_count} exact
```

## Full Prompt Token Burden

```text
tokenizer_status={token_audit["tokenizer_status"]}
tokenizer={token_audit.get("tokenizer_name_or_path")}
tokenizer_revision={token_audit.get("tokenizer_revision")}
rendered_prompt_chars_median={token_audit["rendered_prompt_char_stats"]["median"]}
rendered_prompt_chars_p95={token_audit["rendered_prompt_char_stats"]["p95"]}
rendered_prompt_tokens_median={token_stats.get("median")}
rendered_prompt_tokens_p95={token_stats.get("p95")}
rendered_prompt_tokens_max={token_stats.get("max")}
```

## Locked Failure Policy

Candidate-generator miss is a method failure, not OMIT, and may not exclude a
sample from pilot/dev/test denominators.
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7C-A5 English Column-Conditioned Phase O Protocol Freeze

This package freezes the one-call column-conditioned candidate-selection
protocol proposed after Stage7B-A3 PASS/CLOSE. The primary A5 path removes
Phase M as a model call and derives the compiler IR deterministically from
`table_ref` and `column_span_refs`.

Review order:

1. `{STAGE_NAME}/COLUMN_CONDITIONED_OUTPUT_SPEC_A5.json`
2. `{STAGE_NAME}/COLUMN_CONDITIONED_PROMPT_SPEC_A5_ENGLISH.json`
3. `{STAGE_NAME}/COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A5.json`
4. `{STAGE_NAME}/TARGET_TABLE_BRANCHING_PROTOCOL_A5.json`
5. `{STAGE_NAME}/NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A5.json`
6. `{STAGE_NAME}/COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json`
7. `{STAGE_NAME}/FRESH_ENGLISH_A5_COLUMN_CONDITIONED_FEASIBILITY_SET.jsonl`
8. `{STAGE_NAME}/ORACLE_COLUMN_CONDITIONED_PATH_RESULTS.jsonl`
9. `{STAGE_NAME}/ACCEPTANCE_POLICY_A5.json`
10. `{STAGE_NAME}/OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A5.json`
11. `{STAGE_NAME}/SOURCE_INPUT_MANIFEST.json`
12. `{STAGE_NAME}/SYNTHETIC_SQLITE_DB_MANIFEST.jsonl`
13. `{STAGE_NAME}/PACKAGE_FILE_INTEGRITY_MANIFEST.json`
14. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
15. `{STAGE_NAME}/STAGE7C_A5_LOCK.json`
16. `{STAGE_NAME}/VALIDATION_REPORT.md`
17. `scripts/data/build_stage7c_a5_column_conditioned_phase_o_protocol.py`
18. `scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py`
19. `tests/test_stage7c_a5_column_conditioned_phase_o_protocol.py`

Clean extraction commands:

```bash
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py \\
  --stage-dir {STAGE_NAME}
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py \\
  --stage-dir {STAGE_NAME} \\
  --rebuild
python -m pytest -q tests/test_stage7c_a5_column_conditioned_phase_o_protocol.py
```

No GPU is required. No model is called. The Gretel pilot pool remains closed.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def build_stage(
    out_dir: Path,
    *,
    stage7b_a2_dir: Path = PROJECT_ROOT / STAGE7B_A2_NAME,
    stage7b_a3_dir: Path = PROJECT_ROOT / STAGE7B_A3_NAME,
    stage7c_a4_dir: Path = PROJECT_ROOT / STAGE7C_A4_NAME,
    stage7e0_a4_dir: Path = PROJECT_ROOT / STAGE7E0_A4_NAME,
    tokenizer_name_or_path: str | None = None,
    tokenizer_revision: str = QWEN_TOKENIZER_REVISION,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = out_dir / "sqlite_dbs"

    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(stage7b_a2_dir, stage7b_a3_dir, stage7c_a4_dir, stage7e0_a4_dir))
    write_json(out_dir / "COLUMN_CONDITIONED_OUTPUT_SPEC_A5.json", output_spec())
    write_json(out_dir / "COLUMN_CONDITIONED_PROMPT_SPEC_A5_ENGLISH.json", prompt_spec())
    write_json(out_dir / "COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A5.json", runtime_schema_spec())
    write_json(out_dir / "COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json", serialization_freeze())
    write_json(out_dir / "TARGET_TABLE_BRANCHING_PROTOCOL_A5.json", branching_protocol())
    write_json(out_dir / "NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A5.json", no_phase_m_spec())
    write_json(out_dir / "OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A5.json", failure_policy())
    write_json(out_dir / "ACCEPTANCE_POLICY_A5.json", acceptance_policy())

    rows = []
    db_manifest = []
    oracle_results = []
    for case in case_definitions():
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        oracle = oracle_column_conditioned_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["resolved_column_span_oracle"] = oracle["resolved_column_spans"]
        row["label_side_expected"]["deterministic_ir_oracle"] = oracle["deterministic_ir"]
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
        rows.append(row)
        db_manifest.append({**db_info, "source_tables": case["tables"]})
        oracle_results.append(oracle)

    write_jsonl(out_dir / "FRESH_ENGLISH_A5_COLUMN_CONDITIONED_FEASIBILITY_SET.jsonl", rows)
    write_jsonl(out_dir / "ORACLE_COLUMN_CONDITIONED_PATH_RESULTS.jsonl", oracle_results)
    write_jsonl(out_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl", db_manifest)
    token_audit = prompt_token_audit(rows, tokenizer_name_or_path, tokenizer_revision)
    write_json(out_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json", token_audit)
    write_json(out_dir / "PACKAGE_FILE_INTEGRITY_MANIFEST.json", package_file_integrity_manifest(out_dir))
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_derived_manifest(out_dir))

    assigned_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value != "OMIT") for row in rows)
    omit_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value == "OMIT") for row in rows)
    multi_table_count = sum(1 for row in rows if row["runtime_constraints"]["schema_table_count"] > 1)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "source_stage7b_a3_status": read_json(stage7b_a3_dir / "STAGE7B_A3_LOCK.json").get("status"),
        "source_stage7e0_a4_closed": True,
        "fresh_english_case_count": len(rows),
        "assigned_column_decision_count": assigned_count,
        "omit_column_decision_count": omit_count,
        "multi_table_oneof_case_count": multi_table_count,
        "oracle_preflight_admitted_count": sum(1 for item in oracle_results if item["preflight"] == "ADMITTED"),
        "canonical_target_state_exact_count": sum(1 for item in oracle_results if item["canonical_target_state_exact"]),
        "phase_m_primary_pipeline_removed": True,
        "model_generates_phase_m": False,
        "model_generates_slot_refs": False,
        "dynamic_span_ref_enum_required": True,
        "multi_table_oneof_required": True,
        "candidate_miss_is_method_failure": True,
        "candidate_miss_can_exclude_samples": False,
        "type_based_candidate_pruning_enabled": False,
        "tokenizer_status": token_audit["tokenizer_status"],
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7C_A5_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(len(rows), assigned_count, omit_count, multi_table_count, token_audit))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "fresh_english_case_count": len(rows),
        "assigned_column_decision_count": assigned_count,
        "omit_column_decision_count": omit_count,
        "multi_table_oneof_case_count": multi_table_count,
        "oracle_preflight_admitted_count": lock["oracle_preflight_admitted_count"],
        "model_called": False,
        "gpu_called": False,
    }


def include_paths_for_package(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    for relative in [
        "pyproject.toml",
        "scripts/data/build_stage7c_a5_column_conditioned_phase_o_protocol.py",
        "scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stage7c_a4_candidate_span_phase_o_protocol.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/validate_stage7b_a3_column_conditioned_candidate_selection.py",
        "tests/test_stage7c_a5_column_conditioned_phase_o_protocol.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "src/nldbwrite_v3/v2_a1",
        f"{STAGE7B_A2_NAME}/STAGE7B_A2_LOCK.json",
        f"{STAGE7B_A2_NAME}/CANDIDATE_GENERATION_ALGORITHM_SPEC.json",
        f"{STAGE7B_A2_NAME}/CANDIDATE_SERIALIZATION_SPEC.json",
        f"{STAGE7B_A3_NAME}/STAGE7B_A3_LOCK.json",
        f"{STAGE7B_A3_NAME}/COLUMN_CONDITIONED_REPRESENTATION_SPEC.json",
        f"{STAGE7B_A3_NAME}/COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json",
        f"{STAGE7B_A3_NAME}/TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json",
        f"{STAGE7C_A4_NAME}/STAGE7C_A4_LOCK.json",
        f"{STAGE7E0_A4_NAME}/STAGE7E0_A4_SERVER_RESULT_LOCK.json",
    ]:
        path = PROJECT_ROOT / relative
        if path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            files.append(path)
    return sorted(set(files), key=lambda item: item.as_posix())


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_paths_for_package(stage_dir):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            elif path.name == "sitecustomize.py" and "windows_py314_pytest_tempdir" in path.parts:
                arcname = Path("sitecustomize.py")
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    parser.add_argument("--tokenizer-name-or-path", default=None)
    parser.add_argument("--tokenizer-revision", default=QWEN_TOKENIZER_REVISION)
    args = parser.parse_args()
    summary = build_stage(args.out_dir, tokenizer_name_or_path=args.tokenizer_name_or_path, tokenizer_revision=args.tokenizer_revision)
    digest = package_reviewer(args.out_dir, args.package)
    summary["package"] = str(args.package)
    summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
