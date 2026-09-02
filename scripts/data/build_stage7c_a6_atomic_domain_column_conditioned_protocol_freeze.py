#!/usr/bin/env python3
"""Build Stage7C-A6 column-conditioned Phase O protocol artifacts.

This stage freezes the one-call column-conditioned candidate-selection
protocol. It is CPU-only: no model, no GPU, no Gretel pilot, no development-dev
rows, and no official test rows are opened.
"""

from __future__ import annotations

import argparse
import difflib
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
from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import (
    PATCH_NAME as STAGE7B_A4_PATCH_NAME,
    SCHEMA_ALIAS_STOPWORDS,
    STAGE_NAME as STAGE7B_A4_SOURCE_NAME,
    detect_omission_constructions,
    schema_label_alias_index,
    suppressible_span_refs,
)


STAGE_NAME = "Stage7C_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROTOCOL_FREEZE"
PATCH_NAME = "PATCH2"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260902.zip"
STAGE7B_A2_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
STAGE7B_A3_NAME = "Stage7B_A3_ENGLISH_COLUMN_CONDITIONED_CANDIDATE_SELECTION_AMENDMENT"
STAGE7B_A4_NAME = STAGE7B_A4_SOURCE_NAME
STAGE7C_A4_NAME = "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL"
STAGE7C_A5_ERRATUM_NAME = "Stage7C_A5_PRIMARY_GOLD_PROVENANCE_ERRATUM_PATCH0"
STAGE7E0_A4_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
STAGE7E0_A5_NAME = "Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT"
MODEL_ID = QWEN_TOKENIZER_ID
MODEL_REVISION = QWEN_TOKENIZER_REVISION
PHASE_O_SYSTEM_PROMPT = (
    "You select one source span or OMIT for every SQLite INSERT column. "
    "Return only JSON that matches the provided schema."
)
PHASE_O_USER_PROMPT_TEMPLATE = """Select the literal source span for each target-table column in the INSERT request.

Rules:
- Choose exactly one SPAN reference or OMIT for every column in the selected table branch.
- Use each non-OMIT SPAN reference for at most one column.
- Use OMIT only when the request gives no literal value for that column.
- Choose the smallest complete atomic value span.
- The candidate span inventory has already removed deterministic schema-label/value distractors and omission-cue distractors.
- Do not select field labels, instruction text, table names, column names, or label-plus-value spans.
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
    "ATOMIC_DOMAIN_COLUMN_CONDITIONED_OUTPUT_SPEC_A6.json",
    "ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROMPT_SPEC_A6_ENGLISH.json",
    "ATOMIC_DOMAIN_COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A6.json",
    "ATOMIC_DOMAIN_COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json",
    "CANDIDATE_DOMAIN_RUNTIME_FREEZE_A6.json",
    "A6_ORACLE_CANDIDATE_DOMAIN_AUDIT.json",
    "TARGET_TABLE_BRANCHING_PROTOCOL_A6.json",
    "NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A6.json",
    "OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A6.json",
    "EVALUATOR_SEMANTICS_A6.json",
    "A6_PRIMARY_SET_CONSTRUCTION_PROTOCOL.json",
    "A6_PRIMARY_INDEPENDENCE_AUDIT.json",
    "PRIOR_DESIGN_EVIDENCE_INDEPENDENCE_AUDIT_A6.json",
    "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl",
    "A5_OBSERVED_REGRESSION_DIAGNOSTICS_A6.jsonl",
    "A6_METHOD_STRESS_REGRESSION_DIAGNOSTICS_A6.jsonl",
    "REVIEWER_GUIDED_A6_STRESS_DIAGNOSTICS.jsonl",
    "ORACLE_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PRIMARY_RESULTS.jsonl",
    "ORACLE_A5_OBSERVED_DIAGNOSTIC_RESULTS.jsonl",
    "ORACLE_A6_METHOD_STRESS_DIAGNOSTIC_RESULTS.jsonl",
    "ORACLE_REVIEWER_GUIDED_A6_STRESS_DIAGNOSTIC_RESULTS.jsonl",
    "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json",
    "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl",
    "ACCEPTANCE_POLICY_A6.json",
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


def _legacy_a5_protocol_case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "sample_id": "stage7c_a6_fresh_english_001",
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
            "sample_id": "stage7c_a6_fresh_english_002",
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
            "sample_id": "stage7c_a6_fresh_english_003",
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
            "sample_id": "stage7c_a6_fresh_english_004",
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
            "sample_id": "stage7c_a6_fresh_english_005",
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
            "sample_id": "stage7c_a6_fresh_english_006",
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
            "sample_id": "stage7c_a6_fresh_english_007",
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
            "sample_id": "stage7c_a6_fresh_english_008",
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
            "sample_id": "stage7c_a6_fresh_english_009",
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
            "sample_id": "stage7c_a6_fresh_english_010",
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
            "sample_id": "stage7c_a6_fresh_english_011",
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
            "assigned_value_spans": {"state_name": {"start_char": 33, "end_char": 41}},
        },
        {
            "sample_id": "stage7c_a6_fresh_english_012",
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


def _case_from_corrected_a5_row(row: dict[str, Any]) -> dict[str, Any]:
    assigned_values = {
        item["column_name"]: item["text"]
        for item in row["label_side_expected"]["gold_column_span_ref_oracle"]
    }
    assigned_value_spans = {
        item["column_name"]: {"start_char": item["start_char"], "end_char": item["end_char"]}
        for item in row["label_side_expected"]["gold_column_span_ref_oracle"]
    }
    return {
        "sample_id": row["sample_id"],
        "coverage_tags": row["coverage_tags"],
        "question": row["model_side_input"]["question"],
        "selected_table": row["synthetic_db_spec"]["selected_table"],
        "tables": row["synthetic_db_spec"]["source_tables"],
        "assigned_values": assigned_values,
        "assigned_value_spans": assigned_value_spans,
    }


def a5_observed_diagnostic_case_definitions(stage7c_a5_erratum_dir: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(stage7c_a5_erratum_dir / "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl")
    return [_case_from_corrected_a5_row(row) for row in rows]


PRIMARY_DOMAIN_SELECTION_SEED = "stage7c-a6-patch2-fresh-primary-v1-20260902"
FROZEN_PRIMARY_DOMAIN_POOL = [
    {"domain_id": "aquifer_sample_log", "domain_label": "aquifer sample log"},
    {"domain_id": "bakery_proofing_batch", "domain_label": "bakery proofing batch"},
    {"domain_id": "ceramic_kiln_cycle", "domain_label": "ceramic kiln cycle"},
    {"domain_id": "court_filing_packet", "domain_label": "court filing packet"},
    {"domain_id": "greenhouse_nutrient_mix", "domain_label": "greenhouse nutrient mix"},
    {"domain_id": "harbor_beacon_inspection", "domain_label": "harbor beacon inspection"},
    {"domain_id": "language_exam_roster", "domain_label": "language exam roster"},
    {"domain_id": "textile_dye_lot", "domain_label": "textile dye lot"},
    {"domain_id": "robotics_bench_calibration", "domain_label": "robotics bench calibration"},
    {"domain_id": "pharmacy_refill_queue", "domain_label": "pharmacy refill queue"},
    {"domain_id": "wildfire_sensor_ping", "domain_label": "wildfire sensor ping"},
    {"domain_id": "transit_card_adjustment", "domain_label": "transit card adjustment"},
    {"domain_id": "archive_digitization_job", "domain_label": "archive digitization job"},
    {"domain_id": "coral_tank_chemistry", "domain_label": "coral tank chemistry"},
    {"domain_id": "drone_survey_tile", "domain_label": "drone survey tile"},
    {"domain_id": "ceramic_glaze_recipe", "domain_label": "ceramic glaze recipe"},
    {"domain_id": "microloan_disbursement", "domain_label": "microloan disbursement"},
    {"domain_id": "theater_prop_checkout", "domain_label": "theater prop checkout"},
    {"domain_id": "lab_reagent_shelf", "domain_label": "lab reagent shelf"},
    {"domain_id": "bike_share_rebalance", "domain_label": "bike share rebalance"},
    {"domain_id": "canal_lock_maintenance", "domain_label": "canal lock maintenance"},
    {"domain_id": "patent_intake_record", "domain_label": "patent intake record"},
    {"domain_id": "payroll_exception_log", "domain_label": "payroll exception log"},
    {"domain_id": "vineyard_irrigation_run", "domain_label": "vineyard irrigation run"},
]
REVIEWER_SUGGESTED_DOMAIN_BLACKLIST = [
    "astronomy observation",
    "insurance claim",
    "museum shipment",
    "energy meter",
    "course enrollment",
    "ferry booking",
    "chemical batch",
    "building sensor",
    "journal submission",
    "orchard harvest",
    "device calibration",
    "film screening",
]
REVIEWER_SUGGESTED_LITERAL_BLACKLIST = ["Not Found", "Unavailable", "Empty Quarter"]
STRESS_REQUIREMENTS = [
    "schema-label + identifier",
    "alias-bearing column",
    "legitimate multiword compound",
    "DATETIME",
    "possessive text",
    "ordinal/compound expression",
    "true omission constructions using the frozen cue rules",
    "legitimate omission-looking literal",
    "multi-table oneOf branch selection",
    "generic alias-stoplist case",
    "overlapping candidate spans",
]


def _normalize_blacklist_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().strip())


def selected_primary_domains() -> list[dict[str, str]]:
    return sorted(
        FROZEN_PRIMARY_DOMAIN_POOL,
        key=lambda row: sha256_text(f"{PRIMARY_DOMAIN_SELECTION_SEED}:{canonical_json(row)}"),
    )[:12]


def case_definitions() -> list[dict[str, Any]]:
    case_by_domain = {
        "microloan_disbursement": {
            "construction_domain_id": "microloan_disbursement",
            "construction_domain_label": "microloan disbursement",
            "sample_id": "stage7c_a6_primary_english_001",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "identifier", "real", "percent", "datetime", "schema_alias"],
            "question": 'Insert microloan LOAN-P72 for borrower "Anika Bose", principal_usd 1250.40, interest_pct 6.8%, disbursed_at 2026-10-12 09:35:00, officer_code OFC-3. Cosigner note not provided.',
            "selected_table": "microloan_disbursements",
            "tables": [
                {
                    "table_name": "microloan_disbursements",
                    "columns": [
                        {"column_name": "loan_ref", "source_type": "TEXT", "nullable": False},
                        {"column_name": "borrower_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "principal_usd", "source_type": "REAL", "nullable": False},
                        {"column_name": "interest_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "disbursed_at", "source_type": "TEXT", "nullable": False},
                        {"column_name": "officer_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "cosigner_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"loan_ref": "LOAN-P72", "borrower_name": "Anika Bose", "principal_usd": "1250.40", "interest_pct": "6.8%", "disbursed_at": "2026-10-12 09:35:00", "officer_code": "OFC-3"},
        },
        "harbor_beacon_inspection": {
            "construction_domain_id": "harbor_beacon_inspection",
            "construction_domain_label": "harbor beacon inspection",
            "sample_id": "stage7c_a6_primary_english_002",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "hex_identifier", "integer", "real", "generic_stoplist", "schema_alias"],
            "question": "Create harbor beacon inspection beacon 0xA17C, lens_rating 4.7, battery_pct 82%, tower_height_m 31. Inspector memo left empty.",
            "selected_table": "harbor_beacon_inspections",
            "tables": [
                {
                    "table_name": "harbor_beacon_inspections",
                    "columns": [
                        {"column_name": "beacon_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "lens_rating", "source_type": "REAL", "nullable": False},
                        {"column_name": "battery_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "tower_height_m", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "inspector_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"beacon_id": "0xA17C", "lens_rating": "4.7", "battery_pct": "82%", "tower_height_m": "31"},
        },
        "drone_survey_tile": {
            "construction_domain_id": "drone_survey_tile",
            "construction_domain_label": "drone survey tile",
            "sample_id": "stage7c_a6_primary_english_003",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "overlapping_candidates", "quoted_multiword", "identifier", "date", "integer"],
            "question": 'Add drone_survey_tiles tile TILE-884 for sector "North Pier", short_label "North", captured_on 2026-11-05, altitude_m 120. Operator note absent.',
            "selected_table": "drone_survey_tiles",
            "tables": [
                {
                    "table_name": "drone_survey_tiles",
                    "columns": [
                        {"column_name": "tile_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "sector_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "short_label", "source_type": "TEXT", "nullable": False},
                        {"column_name": "captured_on", "source_type": "TEXT", "nullable": False},
                        {"column_name": "altitude_m", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "operator_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"tile_code": "TILE-884", "sector_name": "North Pier", "short_label": "North", "captured_on": "2026-11-05", "altitude_m": "120"},
            "assigned_value_spans": {"short_label": {"start_char": 75, "end_char": 80}},
        },
        "patent_intake_record": {
            "construction_domain_id": "patent_intake_record",
            "construction_domain_label": "patent intake record",
            "sample_id": "stage7c_a6_primary_english_004",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "ordinal_phrase", "possessive_text", "three_word_value", "quoted_multiword", "email", "identifier"],
            "question": 'Register patent intake PAT-410, invention_title "Iris\'s Quiet Valve", docket_stage second review, applicant_email iris.cho@patent.example, filing_code F-2026-7. Prior art note omitted.',
            "selected_table": "patent_intake_records",
            "tables": [
                {
                    "table_name": "patent_intake_records",
                    "columns": [
                        {"column_name": "intake_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "invention_title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "docket_stage", "source_type": "TEXT", "nullable": False},
                        {"column_name": "applicant_email", "source_type": "TEXT", "nullable": False},
                        {"column_name": "filing_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "prior_art_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"intake_id": "PAT-410", "invention_title": "Iris's Quiet Valve", "docket_stage": "second review", "applicant_email": "iris.cho@patent.example", "filing_code": "F-2026-7"},
        },
        "coral_tank_chemistry": {
            "construction_domain_id": "coral_tank_chemistry",
            "construction_domain_label": "coral tank chemistry",
            "sample_id": "stage7c_a6_primary_english_005",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "legitimate_omission_literals", "quoted_multiword", "real", "percent"],
            "question": 'Record coral tank TANK-19, sample_label "Missing Current", ph_level 8.1, salinity_pct 3.5%. Reagent note not supplied.',
            "selected_table": "coral_tank_chemistry",
            "tables": [
                {
                    "table_name": "coral_tank_chemistry",
                    "columns": [
                        {"column_name": "tank_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "sample_label", "source_type": "TEXT", "nullable": False},
                        {"column_name": "ph_level", "source_type": "REAL", "nullable": False},
                        {"column_name": "salinity_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "reagent_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"tank_code": "TANK-19", "sample_label": "Missing Current", "ph_level": "8.1", "salinity_pct": "3.5%"},
        },
        "bike_share_rebalance": {
            "construction_domain_id": "bike_share_rebalance",
            "construction_domain_label": "bike share rebalance",
            "sample_id": "stage7c_a6_primary_english_006",
            "coverage_tags": ["multi_table", "oneOf", "5_assigned_columns", "true_omit", "identifier", "integer", "real", "text_numeric_mix"],
            "question": 'Use bike_rebalance_jobs: job_code BRB-552, station_code ST-9B, bikes_in 14, bikes_out 6, truck_load 20.5. Route note missing.',
            "selected_table": "bike_rebalance_jobs",
            "tables": [
                {
                    "table_name": "bike_stations",
                    "columns": [
                        {"column_name": "station_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "station_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "bike_rebalance_jobs",
                    "columns": [
                        {"column_name": "job_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "station_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "bikes_in", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "bikes_out", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "truck_load", "source_type": "REAL", "nullable": False},
                        {"column_name": "route_note", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"job_code": "BRB-552", "station_code": "ST-9B", "bikes_in": "14", "bikes_out": "6", "truck_load": "20.5"},
        },
        "textile_dye_lot": {
            "construction_domain_id": "textile_dye_lot",
            "construction_domain_label": "textile dye lot",
            "sample_id": "stage7c_a6_primary_english_007",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "quoted_multiword", "real", "percent", "schema_alias"],
            "question": 'Insert textile dye lot DYE-307, shade_name "Copper Rain", mordant_pct 12%, bath_temp_c 61.5. Rinse note blank.',
            "selected_table": "textile_dye_lots",
            "tables": [
                {
                    "table_name": "textile_dye_lots",
                    "columns": [
                        {"column_name": "lot_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "shade_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "mordant_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "bath_temp_c", "source_type": "REAL", "nullable": False},
                        {"column_name": "rinse_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"lot_code": "DYE-307", "shade_name": "Copper Rain", "mordant_pct": "12%", "bath_temp_c": "61.5"},
        },
        "wildfire_sensor_ping": {
            "construction_domain_id": "wildfire_sensor_ping",
            "construction_domain_label": "wildfire sensor ping",
            "sample_id": "stage7c_a6_primary_english_008",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "datetime", "integer", "real", "schema_alias"],
            "question": "Log wildfire sensor ping PING-640, sensor_tag WF-12, heat_index 91.4, smoke_ppm 388, pinged_at 2026-09-14 18:22:10. Field memo absent.",
            "selected_table": "wildfire_sensor_pings",
            "tables": [
                {
                    "table_name": "wildfire_sensor_pings",
                    "columns": [
                        {"column_name": "ping_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "sensor_tag", "source_type": "TEXT", "nullable": False},
                        {"column_name": "heat_index", "source_type": "REAL", "nullable": False},
                        {"column_name": "smoke_ppm", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "pinged_at", "source_type": "TEXT", "nullable": False},
                        {"column_name": "field_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"ping_id": "PING-640", "sensor_tag": "WF-12", "heat_index": "91.4", "smoke_ppm": "388", "pinged_at": "2026-09-14 18:22:10"},
        },
        "archive_digitization_job": {
            "construction_domain_id": "archive_digitization_job",
            "construction_domain_label": "archive digitization job",
            "sample_id": "stage7c_a6_primary_english_009",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "possessive_text", "three_word_value", "quoted_multiword", "integer", "date"],
            "question": 'Create archive digitization job DIG-226 for collection "Baker\'s Street Ledger", scanner "flatbed nine", page_count 142, due_date 2026-10-28. Conservation memo omitted.',
            "selected_table": "archive_digitization_jobs",
            "tables": [
                {
                    "table_name": "archive_digitization_jobs",
                    "columns": [
                        {"column_name": "job_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "collection_title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "scanner_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "page_count", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "due_date", "source_type": "TEXT", "nullable": False},
                        {"column_name": "conservation_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"job_id": "DIG-226", "collection_title": "Baker's Street Ledger", "scanner_name": "flatbed nine", "page_count": "142", "due_date": "2026-10-28"},
        },
        "language_exam_roster": {
            "construction_domain_id": "language_exam_roster",
            "construction_domain_label": "language exam roster",
            "sample_id": "stage7c_a6_primary_english_010",
            "coverage_tags": ["multi_table", "oneOf", "4_assigned_columns", "true_omit", "email", "identifier", "integer"],
            "question": 'For exam_roster_entries, add candidate "Lena Ortiz", candidate_email lena.ortiz@exam.example, exam_code LANG-44, room_number 205. Listening accommodation not provided.',
            "selected_table": "exam_roster_entries",
            "tables": [
                {
                    "table_name": "exam_sessions",
                    "columns": [
                        {"column_name": "exam_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "exam_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "exam_roster_entries",
                    "columns": [
                        {"column_name": "candidate_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "candidate_email", "source_type": "TEXT", "nullable": False},
                        {"column_name": "exam_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "room_number", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "listening_accommodation", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"candidate_name": "Lena Ortiz", "candidate_email": "lena.ortiz@exam.example", "exam_code": "LANG-44", "room_number": "205"},
        },
        "bakery_proofing_batch": {
            "construction_domain_id": "bakery_proofing_batch",
            "construction_domain_label": "bakery proofing batch",
            "sample_id": "stage7c_a6_primary_english_011",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "many_nullable_columns", "quoted_multiword", "integer", "real"],
            "question": 'Add bakery proofing batch BAK-731, dough_name "Rye Lantern", hydration_pct 68%, proof_minutes 44. Baker note missing and glaze memo left empty.',
            "selected_table": "bakery_proofing_batches",
            "tables": [
                {
                    "table_name": "bakery_proofing_batches",
                    "columns": [
                        {"column_name": "batch_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "dough_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "hydration_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "proof_minutes", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "baker_note", "source_type": "TEXT", "nullable": True},
                        {"column_name": "glaze_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"batch_code": "BAK-731", "dough_name": "Rye Lantern", "hydration_pct": "68%", "proof_minutes": "44"},
        },
        "theater_prop_checkout": {
            "construction_domain_id": "theater_prop_checkout",
            "construction_domain_label": "theater prop checkout",
            "sample_id": "stage7c_a6_primary_english_012",
            "coverage_tags": ["single_table", "3_assigned_columns", "true_omit", "many_nullable_columns", "quoted_multiword", "identifier", "date"],
            "question": 'Prepare theater prop checkout PROP-618 for prop_name "Silver Lantern", return_date 2026-12-08. Handler, repair note, sponsor note, and deposit memo absent.',
            "selected_table": "theater_prop_checkouts",
            "tables": [
                {
                    "table_name": "theater_prop_checkouts",
                    "columns": [
                        {"column_name": "checkout_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "prop_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "handler", "source_type": "TEXT", "nullable": True},
                        {"column_name": "return_date", "source_type": "TEXT", "nullable": False},
                        {"column_name": "deposit_usd", "source_type": "REAL", "nullable": True},
                        {"column_name": "repair_note", "source_type": "TEXT", "nullable": True},
                        {"column_name": "sponsor_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"checkout_id": "PROP-618", "prop_name": "Silver Lantern", "return_date": "2026-12-08"},
        },
    }
    selected = selected_primary_domains()
    missing = [row["domain_id"] for row in selected if row["domain_id"] not in case_by_domain]
    if missing:
        raise RuntimeError(f"missing constructed primary case(s): {missing}")
    return [case_by_domain[row["domain_id"]] for row in selected]


def primary_set_construction_protocol(primary_cases: list[dict[str, Any]]) -> dict[str, Any]:
    selected = selected_primary_domains()
    selected_domain_ids = [row["domain_id"] for row in selected]
    selected_domain_labels = [row["domain_label"] for row in selected]
    case_domain_ids = [case["construction_domain_id"] for case in primary_cases]
    normalized_reviewer_domains = {_normalize_blacklist_text(value) for value in REVIEWER_SUGGESTED_DOMAIN_BLACKLIST}
    normalized_reviewer_literals = {_normalize_blacklist_text(value) for value in REVIEWER_SUGGESTED_LITERAL_BLACKLIST}
    selected_domain_overlaps = sorted(
        label for label in selected_domain_labels if _normalize_blacklist_text(label) in normalized_reviewer_domains
    )
    literal_values = [
        str(value)
        for case in primary_cases
        for value in case["assigned_values"].values()
        if _is_design_literal(str(value))
    ]
    literal_overlaps = sorted(
        value for value in literal_values if _normalize_blacklist_text(value) in normalized_reviewer_literals
    )
    question_literal_overlaps = sorted(
        literal
        for literal in REVIEWER_SUGGESTED_LITERAL_BLACKLIST
        if any(re.search(rf"\b{re.escape(literal)}\b", case["question"], flags=re.IGNORECASE) for case in primary_cases)
    )
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "construction_date": "2026-09-02",
        "domain_source": "frozen internal neutral domain pool created before PATCH2 primary case materialization",
        "domain_pool_size": len(FROZEN_PRIMARY_DOMAIN_POOL),
        "domain_pool": FROZEN_PRIMARY_DOMAIN_POOL,
        "domain_pool_sha256": sha256_text(canonical_json(FROZEN_PRIMARY_DOMAIN_POOL)),
        "selection_seed": PRIMARY_DOMAIN_SELECTION_SEED,
        "selection_algorithm": "sort domain_pool rows by sha256(selection_seed + ':' + canonical_json(row)); select first 12 rows",
        "selected_domain_ids": selected_domain_ids,
        "selected_domain_labels": selected_domain_labels,
        "case_domain_ids_match_selected_order": case_domain_ids == selected_domain_ids,
        "stress_requirements": STRESS_REQUIREMENTS,
        "prior_reviewer_example_blacklist": {
            "domains": REVIEWER_SUGGESTED_DOMAIN_BLACKLIST,
            "literals": REVIEWER_SUGGESTED_LITERAL_BLACKLIST,
        },
        "exact_reviewer_suggested_domain_reuse_count": len(selected_domain_overlaps),
        "exact_reviewer_suggested_literal_reuse_count": len(set(literal_overlaps) | set(question_literal_overlaps)),
        "reviewer_suggested_domain_overlaps": selected_domain_overlaps,
        "reviewer_suggested_literal_overlaps": sorted(set(literal_overlaps) | set(question_literal_overlaps)),
        "reviewer_suggested_domains_used": False,
        "reviewer_suggested_literals_used": False,
        "status": "PASS" if case_domain_ids == selected_domain_ids and not selected_domain_overlaps and not literal_overlaps and not question_literal_overlaps else "FAIL",
        "model_called": False,
        "gpu_called": False,
    }


def reviewer_guided_a6_stress_diagnostic_case_definitions() -> list[dict[str, Any]]:
    cases = [
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_001",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "date", "datetime", "quoted_multiword", "schema_alias"],
            "question": 'Log astronomy observation OBS-742 for target "Vela Dwarf", band "Lyman alpha", captured_at 2026-12-03 04:15:30, aperture_code AP-9. Calibration memo absent.',
            "selected_table": "astronomy_observations",
            "tables": [
                {
                    "table_name": "astronomy_observations",
                    "columns": [
                        {"column_name": "observation_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "target_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "spectral_band", "source_type": "TEXT", "nullable": False},
                        {"column_name": "captured_at", "source_type": "TEXT", "nullable": False},
                        {"column_name": "aperture_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "calibration_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"observation_id": "OBS-742", "target_name": "Vela Dwarf", "spectral_band": "Lyman alpha", "captured_at": "2026-12-03 04:15:30", "aperture_code": "AP-9"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_002",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "percent", "real", "quoted_multiword"],
            "question": 'File insurance claim CLM-880 for policy POL-44, claimant "Ivy Raman", loss_pct 18%, reserve_usd 2400.75. Adjuster comment not provided.',
            "selected_table": "insurance_claims",
            "tables": [
                {
                    "table_name": "insurance_claims",
                    "columns": [
                        {"column_name": "claim_no", "source_type": "TEXT", "nullable": False},
                        {"column_name": "policy_no", "source_type": "TEXT", "nullable": False},
                        {"column_name": "claimant_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "loss_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "reserve_usd", "source_type": "REAL", "nullable": False},
                        {"column_name": "adjuster_comment", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"claim_no": "CLM-880", "policy_no": "POL-44", "claimant_name": "Ivy Raman", "loss_pct": "18%", "reserve_usd": "2400.75"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_003",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "ordinal_phrase", "possessive_text", "three_word_value", "quoted_multiword", "real"],
            "question": 'Register museum shipment MSH-640 with crate "Curator\'s Ivory Compass", origin "Quito", weight_kg 16.2, arrival_rank third. Customs note omitted.',
            "selected_table": "museum_shipments",
            "tables": [
                {
                    "table_name": "museum_shipments",
                    "columns": [
                        {"column_name": "shipment_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "crate_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "origin_city", "source_type": "TEXT", "nullable": False},
                        {"column_name": "weight_kg", "source_type": "REAL", "nullable": False},
                        {"column_name": "arrival_rank", "source_type": "TEXT", "nullable": False},
                        {"column_name": "customs_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"shipment_code": "MSH-640", "crate_name": "Curator's Ivory Compass", "origin_city": "Quito", "weight_kg": "16.2", "arrival_rank": "third"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_004",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "hex_identifier", "generic_stoplist", "integer", "real", "schema_alias"],
            "question": "Create energy meter row meter 0xD00DCAFE, reading_kwh 735.4, voltage_v 221, tariff_code TAR-C2. Service memo left empty.",
            "selected_table": "energy_meters",
            "tables": [
                {
                    "table_name": "energy_meters",
                    "columns": [
                        {"column_name": "meter_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "reading_kwh", "source_type": "REAL", "nullable": False},
                        {"column_name": "voltage_v", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "tariff_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "service_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"meter_id": "0xD00DCAFE", "reading_kwh": "735.4", "voltage_v": "221", "tariff_code": "TAR-C2"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_005",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "identifier", "integer", "many_nullable_columns"],
            "question": 'Enroll student "Noor Patel", course_code CHEM-220, section S-7, credit_units 4. Accommodation plan blank.',
            "selected_table": "course_enrollments",
            "tables": [
                {
                    "table_name": "course_enrollments",
                    "columns": [
                        {"column_name": "student_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "course_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "section_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "credit_units", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "accommodation_plan", "source_type": "TEXT", "nullable": True},
                        {"column_name": "advisor_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"student_name": "Noor Patel", "course_code": "CHEM-220", "section_code": "S-7", "credit_units": "4"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_006",
            "coverage_tags": ["multi_table", "oneOf", "5_assigned_columns", "true_omit", "real", "text_numeric_mix"],
            "question": 'For ferry_bookings, add booking_ref FERRY-118, route_code BAY-6, passenger "Omar Silva", fare 32.50, seat 12A. Meal preference missing.',
            "selected_table": "ferry_bookings",
            "tables": [
                {
                    "table_name": "ferry_routes",
                    "columns": [
                        {"column_name": "route_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "route_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "ferry_bookings",
                    "columns": [
                        {"column_name": "booking_ref", "source_type": "TEXT", "nullable": False},
                        {"column_name": "route_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "passenger_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "fare", "source_type": "REAL", "nullable": False},
                        {"column_name": "seat", "source_type": "TEXT", "nullable": False},
                        {"column_name": "meal_preference", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"booking_ref": "FERRY-118", "route_code": "BAY-6", "passenger_name": "Omar Silva", "fare": "32.50", "seat": "12A"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_007",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "percent", "schema_alias", "quoted_multiword"],
            "question": 'Insert chemical batch BATCH-Q9, compound_name "Sodium Formate", purity_pct 99.1%, mass_kg 2.75. Storage advisory not supplied.',
            "selected_table": "chemical_batches",
            "tables": [
                {
                    "table_name": "chemical_batches",
                    "columns": [
                        {"column_name": "batch_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "compound_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "purity_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "mass_kg", "source_type": "REAL", "nullable": False},
                        {"column_name": "storage_advisory", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"batch_code": "BATCH-Q9", "compound_name": "Sodium Formate", "purity_pct": "99.1%", "mass_kg": "2.75"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_008",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "legitimate_omission_literals", "integer"],
            "question": 'Create building sensor sensor_tag SNS-330, floor_label L14, co2_ppm 640, status "Unavailable". Maintenance ticket absent.',
            "selected_table": "building_sensors",
            "tables": [
                {
                    "table_name": "building_sensors",
                    "columns": [
                        {"column_name": "sensor_tag", "source_type": "TEXT", "nullable": False},
                        {"column_name": "floor_label", "source_type": "TEXT", "nullable": False},
                        {"column_name": "co2_ppm", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "status", "source_type": "TEXT", "nullable": False},
                        {"column_name": "maintenance_ticket", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"sensor_tag": "SNS-330", "floor_label": "L14", "co2_ppm": "640", "status": "Unavailable"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_009",
            "coverage_tags": ["multi_table", "oneOf", "4_assigned_columns", "true_omit", "email", "quoted_multiword", "integer"],
            "question": 'Use journal_submissions: manuscript_id MS-772, title "Quiet Methods", author_email lee.kwon@journal.example, page_count 27. Reviewer note omitted.',
            "selected_table": "journal_submissions",
            "tables": [
                {
                    "table_name": "journals",
                    "columns": [
                        {"column_name": "journal_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "journal_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "journal_submissions",
                    "columns": [
                        {"column_name": "manuscript_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "author_email", "source_type": "TEXT", "nullable": False},
                        {"column_name": "page_count", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "reviewer_note", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"manuscript_id": "MS-772", "title": "Quiet Methods", "author_email": "lee.kwon@journal.example", "page_count": "27"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_010",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "overlapping_candidates", "quoted_multiword", "identifier"],
            "question": 'Record orchard harvest lot HARV-26, cultivar "Pink Dawn", bushel_count 58, quality_grade "Pink". Weather memo not provided.',
            "selected_table": "orchard_harvests",
            "tables": [
                {
                    "table_name": "orchard_harvests",
                    "columns": [
                        {"column_name": "harvest_lot", "source_type": "TEXT", "nullable": False},
                        {"column_name": "cultivar", "source_type": "TEXT", "nullable": False},
                        {"column_name": "bushel_count", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "quality_grade", "source_type": "TEXT", "nullable": False},
                        {"column_name": "weather_memo", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"harvest_lot": "HARV-26", "cultivar": "Pink Dawn", "bushel_count": "58", "quality_grade": "Pink"},
            "assigned_value_spans": {"quality_grade": {"start_char": 90, "end_char": 94}},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_011",
            "coverage_tags": ["single_table", "3_assigned_columns", "true_omit", "many_nullable_columns", "quoted_multiword", "real"],
            "question": 'Calibrate device DEV-515 against standard "Delta Zero" with error_margin 0.006. Technician memo missing and certificate note left empty.',
            "selected_table": "device_calibrations",
            "tables": [
                {
                    "table_name": "device_calibrations",
                    "columns": [
                        {"column_name": "device_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "standard_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "error_margin", "source_type": "REAL", "nullable": False},
                        {"column_name": "technician_memo", "source_type": "TEXT", "nullable": True},
                        {"column_name": "certificate_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"device_code": "DEV-515", "standard_name": "Delta Zero", "error_margin": "0.006"},
        },
        {
            "sample_id": "stage7c_a6_reviewer_guided_english_012",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "legitimate_omission_literals", "datetime", "quoted_multiword"],
            "question": 'Schedule film screening SCR-442 for film_title "Not Found", auditorium AUD-3, start_time 2026-12-22 19:45:00, ticket_price 11.25. Sponsor text blank.',
            "selected_table": "film_screenings",
            "tables": [
                {
                    "table_name": "film_screenings",
                    "columns": [
                        {"column_name": "screening_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "film_title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "auditorium", "source_type": "TEXT", "nullable": False},
                        {"column_name": "start_time", "source_type": "TEXT", "nullable": False},
                        {"column_name": "ticket_price", "source_type": "REAL", "nullable": False},
                        {"column_name": "sponsor_text", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"screening_code": "SCR-442", "film_title": "Not Found", "auditorium": "AUD-3", "start_time": "2026-12-22 19:45:00", "ticket_price": "11.25"},
        },
    ]
    return cases


def a6_method_stress_diagnostic_case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "sample_id": "stage7c_a6_method_stress_english_001",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "possessive_text", "ordinal_phrase", "datetime", "identifier"],
            "question": "Create archive record archive_code ARC-901, exhibit_title Children's Rights, era_label 20th Century, opened_at 2026-09-02 10:30:00, case_no CASE-77. Curator note not provided.",
            "selected_table": "archive_records",
            "tables": [
                {
                    "table_name": "archive_records",
                    "columns": [
                        {"column_name": "archive_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "exhibit_title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "era_label", "source_type": "TEXT", "nullable": False},
                        {"column_name": "opened_at", "source_type": "TEXT", "nullable": False},
                        {"column_name": "case_no", "source_type": "TEXT", "nullable": False},
                        {"column_name": "curator_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"archive_code": "ARC-901", "exhibit_title": "Children's Rights", "era_label": "20th Century", "opened_at": "2026-09-02 10:30:00", "case_no": "CASE-77"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_002",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "hex_identifier", "real", "percent", "schema_alias"],
            "question": "Record sensor station 0xBEEF2026, temperature 23.4, humidity 67%, mass 0.58. Accessibility note missing.",
            "selected_table": "sensor_readings",
            "tables": [
                {
                    "table_name": "sensor_readings",
                    "columns": [
                        {"column_name": "station_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "temperature_c", "source_type": "REAL", "nullable": False},
                        {"column_name": "humidity_pct", "source_type": "TEXT", "nullable": False},
                        {"column_name": "mass_g", "source_type": "REAL", "nullable": False},
                        {"column_name": "accessibility_note", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"station_id": "0xBEEF2026", "temperature_c": "23.4", "humidity_pct": "67%", "mass_g": "0.58"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_003",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "identifier", "schema_alias", "integer"],
            "question": 'Add checkout loan LOAN-314, card CARD-314, title "Ocean Ledger", days 14. Shelf omitted.',
            "selected_table": "checkout_events",
            "tables": [
                {
                    "table_name": "checkout_events",
                    "columns": [
                        {"column_name": "loan_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "borrower_card", "source_type": "TEXT", "nullable": False},
                        {"column_name": "title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "loan_days", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "hold_shelf", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"loan_id": "LOAN-314", "borrower_card": "CARD-314", "title": "Ocean Ledger", "loan_days": "14"},
            "assigned_value_spans": {"loan_days": {"start_char": 70, "end_char": 72}},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_004",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "generic_stoplist", "legitimate_omission_literals", "quoted_multiword"],
            "question": 'Insert product_id PRD-55, product_name "Missing Link", display_name "Blank Space", state "Absent". Code value is not supplied.',
            "selected_table": "catalog_items",
            "tables": [
                {
                    "table_name": "catalog_items",
                    "columns": [
                        {"column_name": "product_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "product_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "display_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "state", "source_type": "TEXT", "nullable": False},
                        {"column_name": "code_value", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"product_id": "PRD-55", "product_name": "Missing Link", "display_name": "Blank Space", "state": "Absent"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_005",
            "coverage_tags": ["single_table", "5_assigned_columns", "true_omit", "email", "identifier", "schema_alias", "integer"],
            "question": 'Register attendee "Maya Ortiz", email maya.ortiz@conf.example, badge BADGE-82, seat B-22, paid 1. Contact omitted.',
            "selected_table": "event_registrations",
            "tables": [
                {
                    "table_name": "event_registrations",
                    "columns": [
                        {"column_name": "attendee_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "badge_email", "source_type": "TEXT", "nullable": False},
                        {"column_name": "badge_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "seat", "source_type": "TEXT", "nullable": False},
                        {"column_name": "paid", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "emergency_contact", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"attendee_name": "Maya Ortiz", "badge_email": "maya.ortiz@conf.example", "badge_code": "BADGE-82", "seat": "B-22", "paid": "1"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_006",
            "coverage_tags": ["single_table", "3_assigned_columns", "many_nullable_columns", "true_omit", "quoted_multiword", "date"],
            "question": 'Create task title "Audit packet", owner "qa team", due_date 2026-09-20. Leave description, tag, reviewer, and closed_at empty.',
            "selected_table": "workflow_tasks",
            "tables": [
                {
                    "table_name": "workflow_tasks",
                    "columns": [
                        {"column_name": "title", "source_type": "TEXT", "nullable": False},
                        {"column_name": "owner", "source_type": "TEXT", "nullable": False},
                        {"column_name": "due_date", "source_type": "TEXT", "nullable": False},
                        {"column_name": "description", "source_type": "TEXT", "nullable": True},
                        {"column_name": "tag", "source_type": "TEXT", "nullable": True},
                        {"column_name": "reviewer", "source_type": "TEXT", "nullable": True},
                        {"column_name": "closed_at", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"title": "Audit packet", "owner": "qa team", "due_date": "2026-09-20"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_007",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "overlapping_candidates", "quoted_multiword", "integer"],
            "question": 'Add city "New York City", state "New York", rank 1, nickname "Harbor Light". Region omitted.',
            "selected_table": "city_aliases",
            "tables": [
                {
                    "table_name": "city_aliases",
                    "columns": [
                        {"column_name": "city_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "state_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "rank", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "nickname", "source_type": "TEXT", "nullable": False},
                        {"column_name": "region", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"city_name": "New York City", "state_name": "New York", "rank": "1", "nickname": "Harbor Light"},
            "assigned_value_spans": {"state_name": {"start_char": 33, "end_char": 41}},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_008",
            "coverage_tags": ["multi_table", "oneOf", "4_assigned_columns", "true_omit", "identifier", "real", "date"],
            "question": "Log maintenance job MX-741 for asset ELEV-3, cost 412.60, opened_on 2026-12-19. Supervisor initials absent.",
            "selected_table": "maintenance_jobs",
            "tables": [
                {
                    "table_name": "assets",
                    "columns": [
                        {"column_name": "asset_tag", "source_type": "TEXT", "nullable": False},
                        {"column_name": "asset_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "maintenance_jobs",
                    "columns": [
                        {"column_name": "job_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "asset_tag", "source_type": "TEXT", "nullable": False},
                        {"column_name": "cost", "source_type": "REAL", "nullable": False},
                        {"column_name": "opened_on", "source_type": "TEXT", "nullable": False},
                        {"column_name": "supervisor_initials", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"job_code": "MX-741", "asset_tag": "ELEV-3", "cost": "412.60", "opened_on": "2026-12-19"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_009",
            "coverage_tags": ["multi_table", "oneOf", "4_assigned_columns", "true_omit", "quoted_multiword", "schema_alias", "real"],
            "question": 'Use lab_vials: vial_code VIAL-771, specimen_name "cedar cutting", weight 0.93, freezer_slot RACK-42. Analysis note missing.',
            "selected_table": "lab_vials",
            "tables": [
                {
                    "table_name": "storage_rooms",
                    "columns": [
                        {"column_name": "room_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "room_name", "source_type": "TEXT", "nullable": False},
                    ],
                },
                {
                    "table_name": "lab_vials",
                    "columns": [
                        {"column_name": "vial_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "specimen_name", "source_type": "TEXT", "nullable": False},
                        {"column_name": "weight_g", "source_type": "REAL", "nullable": False},
                        {"column_name": "freezer_slot", "source_type": "TEXT", "nullable": False},
                        {"column_name": "analysis_note", "source_type": "TEXT", "nullable": True},
                    ],
                },
            ],
            "assigned_values": {"vial_code": "VIAL-771", "specimen_name": "cedar cutting", "weight_g": "0.93", "freezer_slot": "RACK-42"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_010",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "quoted_multiword", "integer", "schema_alias"],
            "question": 'Schedule rail service RAIL-602 from "Hue" toward "Osaka"; travel_minutes 318 and boarding_platform P5. Delay reason not supplied.',
            "selected_table": "rail_trips",
            "tables": [
                {
                    "table_name": "rail_trips",
                    "columns": [
                        {"column_name": "trip_code", "source_type": "TEXT", "nullable": False},
                        {"column_name": "origin", "source_type": "TEXT", "nullable": False},
                        {"column_name": "destination", "source_type": "TEXT", "nullable": False},
                        {"column_name": "duration_minutes", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "platform", "source_type": "TEXT", "nullable": False},
                        {"column_name": "delay_reason", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"trip_code": "RAIL-602", "origin": "Hue", "destination": "Osaka", "duration_minutes": "318", "platform": "P5"},
        },
        {
            "sample_id": "stage7c_a6_method_stress_english_011",
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
        {
            "sample_id": "stage7c_a6_method_stress_english_012",
            "coverage_tags": ["single_table", "4_assigned_columns", "true_omit", "quoted_multiword", "real", "integer", "text_numeric_mix"],
            "question": 'Prepare field kit KIT-902: item "field scanner", loan_length 9, bond_amount 450.25, pickup_period "afternoon". Return slot blank.',
            "selected_table": "field_kits",
            "tables": [
                {
                    "table_name": "field_kits",
                    "columns": [
                        {"column_name": "kit_id", "source_type": "TEXT", "nullable": False},
                        {"column_name": "item", "source_type": "TEXT", "nullable": False},
                        {"column_name": "days", "source_type": "INTEGER", "nullable": False},
                        {"column_name": "deposit", "source_type": "REAL", "nullable": False},
                        {"column_name": "pickup_window", "source_type": "TEXT", "nullable": False},
                        {"column_name": "return_slot", "source_type": "TEXT", "nullable": True},
                    ],
                }
            ],
            "assigned_values": {"kit_id": "KIT-902", "item": "field scanner", "days": "9", "deposit": "450.25", "pickup_window": "afternoon"},
            "assigned_value_spans": {"days": {"start_char": 61, "end_char": 62}},
        },
    ]


def diagnostic_case_definitions(stage7c_a5_erratum_dir: Path) -> list[dict[str, Any]]:
    return a5_observed_diagnostic_case_definitions(stage7c_a5_erratum_dir)




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
        payload["title"] = "Stage7C-A6 Column-Conditioned Candidate Selection Output"
        return payload
    refs = {table_name: table_ref for table_name, table_ref in table_ref_map({"tables": [{"table_name": table_name} for table_name in tables]}).items()}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7C-A6 Multi-Table Column-Conditioned Candidate Selection Output",
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


def literal_occurrences(question: str, value: str) -> list[dict[str, Any]]:
    occurrences = []
    start = 0
    while True:
        index = question.find(value, start)
        if index < 0:
            return occurrences
        occurrences.append({"start_char": index, "end_char": index + len(value), "text": value})
        start = index + 1


def find_value_span(case: dict[str, Any], column_name: str, value: str) -> dict[str, Any]:
    question = case["question"]
    occurrences = literal_occurrences(question, value)
    explicit = case.get("assigned_value_spans", {}).get(column_name)
    if explicit is not None:
        start = int(explicit["start_char"])
        end = int(explicit["end_char"])
        if question[start:end] != value:
            raise ValueError(f"Explicit source span mismatch for {case['sample_id']} column {column_name}: {start}:{end} != {value!r}")
        return {
            "start_char": start,
            "end_char": end,
            "text": value,
            "occurrence_count": len(occurrences),
            "explicitly_disambiguated": True,
        }
    if len(occurrences) != 1:
        raise ValueError(f"Ambiguous assigned literal for {case['sample_id']} column {column_name}: {value!r} occurs {len(occurrences)} times; explicit source span required")
    return {**occurrences[0], "occurrence_count": 1, "explicitly_disambiguated": False}


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
        span = find_value_span(case, column.column_name, value)
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
    selected_table_ref_value = row["label_side_expected"]["phase_o"]["table_ref"]
    omitted_required = [
        column
        for column in row["model_side_input"]["schema_inventory"]["columns"]
        if column["table_ref"] == selected_table_ref_value
        and column_decisions.get(column["column_ref"]) == "OMIT"
        and column.get("nullable") is False
        and column.get("has_default") is False
    ]
    if omitted_required:
        names = ", ".join(f"{column['column_ref']}:{column['column_name']}" for column in omitted_required)
        raise ValueError(f"required_column_omitted:{names}")
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
        "protocol": "atomic_domain_column_conditioned_phase_o_protocol",
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
        "candidate_domain_filter_source_stage": STAGE7B_A4_NAME,
        "candidate_domain_filter_source_patch": STAGE7B_A4_PATCH_NAME,
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
        "non_omit_span_refs_unique_across_columns": True,
        "omit_token": "OMIT",
        "model_called": False,
        "gpu_called": False,
    }


def runtime_schema_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "runtime_builder": "dynamic_schema_for_schema_tables(model_visible_schema_tables, filtered_candidate_span_refs)",
        "single_table_strategy": "const table_ref TAB_1 with every COL_n key required",
        "multi_table_strategy": "oneOf branch per model-visible table_ref with branch-local required column_span_refs",
        "column_value_domain": ["OMIT", "SPAN_0001", "SPAN_0002", "..."],
        "unknown_span_refs_structurally_impossible": True,
        "early_array_stop_structurally_impossible": True,
        "static_pattern_fallback_allowed": False,
        "gold_sql_required_for_runtime_schema": False,
        "type_based_candidate_pruning_enabled": False,
        "candidate_domain_filter_enabled": True,
        "candidate_domain_filter": "Stage7B-A4 PATCH2 schema-label-alias atomic dominance plus context-aware omission cues",
        "model_called": False,
        "gpu_called": False,
    }


def serialization_freeze() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "source_stage": STAGE7B_A2_NAME,
        "candidate_generator_variant": STAGE7B_SELECTED_VARIANT,
        "candidate_domain_filter_source_stage": STAGE7B_A4_NAME,
        "candidate_domain_filter_source_patch": STAGE7B_A4_PATCH_NAME,
        "line_template": "SPAN_0001 | TAG[,TAG...] | exact source text",
        "model_visible_fields": ["span_ref", "tags", "text"],
        "model_hidden_fields": ["start_char", "end_char", "provenance_tags"],
        "format_frozen_before_model_run": True,
        "model_called": False,
        "gpu_called": False,
    }


def candidate_domain_runtime_freeze() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "source_stage": STAGE7B_A4_NAME,
        "source_patch": STAGE7B_A4_PATCH_NAME,
        "runtime_order": [
            "generate lexical_ngram2 candidate inventory",
            "derive schema-label aliases from model-visible table and column names",
            "detect schema-label/schema-alias omission constructions in the question",
            "suppress schema-label/schema-alias dominated label-plus-atomic spans",
            "suppress context-aware omission-cue spans and full omission constructions",
            "build dynamic column-conditioned JSON schema over remaining SPAN refs plus OMIT",
        ],
        "schema_alias_stopwords": sorted(SCHEMA_ALIAS_STOPWORDS),
        "gold_blind_runtime_inputs": ["question", "candidate_inventory", "model_visible_schema", "frozen_cue_list", "frozen_stoplist"],
        "forbidden_runtime_inputs": ["gold_sql", "gold_values", "gold_offsets", "target_state", "model_outputs"],
        "candidate_miss_policy": "If the filter suppresses a true gold span on pilot/dev/test, it is a method failure and cannot exclude the sample.",
        "manual_alias_additions_allowed_after_a6_outputs": False,
        "model_called": False,
        "gpu_called": False,
    }


def candidate_domain_oracle_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row_payloads = []
    total_unfiltered = 0
    total_filtered = 0
    total_suppressed = 0
    total_gold_suppressed = 0
    for row in rows:
        audit = row["candidate_domain_audit"]
        total_unfiltered += int(audit["unfiltered_candidate_count"])
        total_filtered += int(audit["filtered_candidate_count"])
        total_suppressed += int(audit["suppressed_candidate_count"])
        total_gold_suppressed += int(audit["gold_suppressed_count"])
        row_payloads.append(
            {
                "sample_id": row["sample_id"],
                "unfiltered_candidate_count": audit["unfiltered_candidate_count"],
                "filtered_candidate_count": audit["filtered_candidate_count"],
                "suppressed_candidate_count": audit["suppressed_candidate_count"],
                "gold_suppressed_count": audit["gold_suppressed_count"],
                "suppression_rule_counts": row["runtime_constraints"]["suppression_rule_counts"],
            }
        )
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if total_gold_suppressed == 0 else "FAIL",
        "fresh_primary_case_count": len(rows),
        "unfiltered_candidate_total": total_unfiltered,
        "filtered_candidate_total": total_filtered,
        "suppressed_candidate_total": total_suppressed,
        "gold_suppressed_total": total_gold_suppressed,
        "per_sample": row_payloads,
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
        "duplicate_span_reuse_is_method_failure": True,
        "may_exclude_sample_for_candidate_miss": False,
        "pilot_dev_test_denominator_locked": True,
        "type_based_candidate_pruning_enabled": False,
        "model_called": False,
        "gpu_called": False,
    }


def evaluator_semantics() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "column_span_refs_mapping_equality": "order_insensitive_by_object_key",
        "json_object_key_order_has_semantics": False,
        "span_reuse_rule": "Each non-OMIT SPAN reference may be used for at most one column.",
        "duplicate_span_reuse_outcome": "method_failure",
        "primary_acceptance_precedes_diagnostics": True,
        "diagnostic_cases_can_never_compensate_primary_failures": True,
        "model_called": False,
        "gpu_called": False,
    }


def acceptance_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "synthetic_feasibility_gate": {
            "sample_set": "12 genuinely new English A6 primary column-conditioned cases",
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
        "diagnostic_gate": {
            "sample_set": "12 corrected A5 observed diagnostics plus 12 A6 method-stress diagnostics plus 12 reviewer-guided A6 stress diagnostics",
            "required_pass_count": "12/12",
            "role": "diagnostic_only_after_primary",
            "can_compensate_primary_failure": False,
        },
        "before_stage7e0_a6": "A protocol-compliant atomic-domain column-conditioned oracle path must pass all fresh primary cases before any Stage7E0-A6 GPU run.",
        "gretel_pilot_opened": False,
        "model_called": False,
        "gpu_called": False,
    }


def _is_design_literal(value: str) -> bool:
    stripped = value.strip()
    return len(stripped) >= 2 and (any(ch.isalpha() for ch in stripped) or any(ch in stripped for ch in "-_@.%:"))


def _collect_design_strings(payload: Any) -> set[str]:
    strings: set[str] = set()
    if isinstance(payload, str):
        if _is_design_literal(payload):
            strings.add(payload)
        strings.update(item for item in re.findall(r'"([^"]+)"', payload) if _is_design_literal(item))
    elif isinstance(payload, list):
        for item in payload:
            strings.update(_collect_design_strings(item))
    elif isinstance(payload, dict):
        for value in payload.values():
            strings.update(_collect_design_strings(value))
    return strings


def prior_design_evidence(
    stage7b_a4_dir: Path,
    stage7c_a5_erratum_dir: Path,
    a5_diagnostic_cases: list[dict[str, Any]],
    method_stress_cases: list[dict[str, Any]],
    reviewer_guided_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    questions: dict[str, str] = {}
    literals_by_source: dict[str, set[str]] = {}

    def add_source(source_name: str, *, payload: Any | None = None, cases: list[dict[str, Any]] | None = None) -> None:
        literals = literals_by_source.setdefault(source_name, set())
        if cases:
            for case in cases:
                questions[f"{source_name}:{case['sample_id']}"] = case["question"]
                literals.update(str(value) for value in case["assigned_values"].values() if _is_design_literal(str(value)))
        if payload is not None:
            literals.update(_collect_design_strings(payload))

    corrected_rows = read_jsonl(stage7c_a5_erratum_dir / "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl")
    corrected_questions = {
        f"corrected_a5:{row['sample_id']}": row["model_side_input"]["question"]
        for row in corrected_rows
    }
    questions.update(corrected_questions)
    add_source("corrected_a5_erratum_primary", cases=a5_diagnostic_cases, payload=corrected_rows)
    add_source("a6_method_stress_diagnostics", cases=method_stress_cases)
    add_source("reviewer_guided_after_method_freeze_diagnostics", cases=reviewer_guided_cases)
    for filename in [
        "SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json",
        "A5_OBSERVED_ERROR_COUNTERFACTUAL_DOMAIN_AUDIT.json",
        "FALSE_SUPPRESSION_AUDIT.json",
    ]:
        payload = read_json(stage7b_a4_dir / filename)
        add_source(f"{STAGE7B_A4_NAME}:{filename}", payload=payload)
        for fixture in payload.get("fixtures", []) if isinstance(payload, dict) else []:
            if "question" in fixture:
                questions[f"{filename}:{fixture.get('case_id', len(questions))}"] = fixture["question"]
    return {
        "source_literal_counts": {source: len(values) for source, values in sorted(literals_by_source.items())},
        "source_question_count": len(questions),
        "literals_by_source": {source: sorted(values) for source, values in sorted(literals_by_source.items())},
        "questions": questions,
    }


def primary_independence_audit(
    primary_cases: list[dict[str, Any]],
    prior_evidence: dict[str, Any],
    construction_protocol: dict[str, Any],
) -> dict[str, Any]:
    prior_questions = prior_evidence["questions"]
    literals_by_source = prior_evidence["literals_by_source"]
    prior_literals = {
        literal
        for literals in literals_by_source.values()
        for literal in literals
    }
    synthetic_fixture_literals = set(literals_by_source.get(f"{STAGE7B_A4_NAME}:SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json", []))
    normalized_reviewer_domains = {_normalize_blacklist_text(value) for value in REVIEWER_SUGGESTED_DOMAIN_BLACKLIST}
    normalized_reviewer_literals = {_normalize_blacklist_text(value) for value in REVIEWER_SUGGESTED_LITERAL_BLACKLIST}
    rows = []
    exact_literal_reuse_count = 0
    exact_synthetic_fixture_reuse_count = 0
    exact_reviewer_domain_reuse_count = 0
    exact_reviewer_literal_reuse_count = 0
    max_similarity = 0.0
    for case in primary_cases:
        literals = {str(value) for value in case["assigned_values"].values() if _is_design_literal(str(value))}
        overlaps = sorted(literals & prior_literals)
        synthetic_overlaps = sorted(literals & synthetic_fixture_literals)
        reviewer_domain_overlaps = sorted(
            value
            for value in [case.get("construction_domain_label", "")]
            if _normalize_blacklist_text(str(value)) in normalized_reviewer_domains
        )
        reviewer_literal_overlaps = sorted(
            value
            for value in literals
            if _normalize_blacklist_text(value) in normalized_reviewer_literals
        )
        similarities = [
            {
                "prior_evidence_id": prior_id,
                "sequence_similarity": difflib.SequenceMatcher(None, case["question"], prior_question).ratio(),
            }
            for prior_id, prior_question in prior_questions.items()
        ]
        nearest = max(similarities, key=lambda item: item["sequence_similarity"])
        max_similarity = max(max_similarity, float(nearest["sequence_similarity"]))
        if overlaps:
            exact_literal_reuse_count += 1
        if synthetic_overlaps:
            exact_synthetic_fixture_reuse_count += 1
        if reviewer_domain_overlaps:
            exact_reviewer_domain_reuse_count += 1
        if reviewer_literal_overlaps:
            exact_reviewer_literal_reuse_count += 1
        rows.append(
            {
                "sample_id": case["sample_id"],
                "exact_prior_design_literal_reuse": overlaps,
                "exact_synthetic_fixture_literal_reuse": synthetic_overlaps,
                "exact_reviewer_suggested_domain_reuse": reviewer_domain_overlaps,
                "exact_reviewer_suggested_literal_reuse": reviewer_literal_overlaps,
                "nearest_prior_design_question": nearest["prior_evidence_id"],
                "nearest_question_sequence_similarity": nearest["sequence_similarity"],
                "passes_independence_gate": not overlaps and not synthetic_overlaps and not reviewer_domain_overlaps and not reviewer_literal_overlaps and nearest["sequence_similarity"] < 0.60,
            }
        )
    failures = [row["sample_id"] for row in rows if not row["passes_independence_gate"]]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "primary_case_count": len(primary_cases),
        "prior_design_scope": [
            "corrected A5 primary/regression cases from Gold Provenance Erratum",
            "Stage7B-A4 synthetic omission safety fixtures",
            "Stage7B-A4 A5 observed-error counterfactual examples",
            "Stage7B-A4 known false-suppression audit payload",
            "A6 method-stress diagnostics moved out of primary acceptance",
            "Reviewer-guided A6 stress diagnostics moved out of primary acceptance",
            "Reviewer-suggested PATCH0 domain and literal blacklist",
        ],
        "prior_design_source_literal_counts": prior_evidence["source_literal_counts"],
        "prior_design_source_question_count": prior_evidence["source_question_count"],
        "construction_protocol_status": construction_protocol["status"],
        "domain_pool_sha256": construction_protocol["domain_pool_sha256"],
        "selection_seed": construction_protocol["selection_seed"],
        "exact_prior_design_literal_reuse_case_count": exact_literal_reuse_count,
        "exact_synthetic_fixture_reuse_case_count": exact_synthetic_fixture_reuse_count,
        "exact_reviewer_suggested_domain_reuse_case_count": exact_reviewer_domain_reuse_count,
        "exact_reviewer_suggested_literal_reuse_case_count": exact_reviewer_literal_reuse_count,
        "reviewer_suggested_domains_used": construction_protocol["reviewer_suggested_domains_used"],
        "reviewer_suggested_literals_used": construction_protocol["reviewer_suggested_literals_used"],
        "known_development_example_reuse_case_count": exact_literal_reuse_count,
        "max_nearest_question_sequence_similarity": max_similarity,
        "similarity_threshold": 0.60,
        "status": "PASS" if not failures and construction_protocol["status"] == "PASS" else "FAIL",
        "failures": failures,
        "per_primary_case": rows,
        "model_called": False,
        "gpu_called": False,
    }


def source_input_manifest(
    stage7b_a2_dir: Path,
    stage7b_a3_dir: Path,
    stage7b_a4_dir: Path,
    stage7c_a4_dir: Path,
    stage7c_a5_erratum_dir: Path,
    stage7e0_a4_dir: Path,
    stage7e0_a5_dir: Path,
) -> dict[str, Any]:
    files = [
        (STAGE7B_A2_NAME, stage7b_a2_dir, "STAGE7B_A2_LOCK.json"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "CANDIDATE_GENERATION_ALGORITHM_SPEC.json"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "CANDIDATE_SERIALIZATION_SPEC.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "STAGE7B_A3_LOCK.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "COLUMN_CONDITIONED_REPRESENTATION_SPEC.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "STAGE7B_A4_LOCK.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "SCHEMA_LABEL_ALIAS_SPEC.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "OMISSION_CUE_SUPPRESSION_RULE_SPEC.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "A5_OBSERVED_ERROR_COUNTERFACTUAL_DOMAIN_AUDIT.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "FALSE_SUPPRESSION_AUDIT.json"),
        (STAGE7B_A4_NAME, stage7b_a4_dir, "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl"),
        (STAGE7C_A4_NAME, stage7c_a4_dir, "STAGE7C_A4_LOCK.json"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "ERRATUM_LOCK.json"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "CORRECTED_A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "GOLD_PROVENANCE_ERRATUM.json"),
        (STAGE7E0_A4_NAME, stage7e0_a4_dir, "STAGE7E0_A4_SERVER_RESULT_LOCK.json"),
        (STAGE7E0_A5_NAME, stage7e0_a5_dir, "STAGE7E0_A5_SERVER_RESULT_LOCK.json"),
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
    unfiltered_candidate_counts = []
    filtered_candidate_counts = []
    suppressed_candidate_counts = []
    message_rows = []
    for row in rows:
        messages, user, digest = render_phase_o_messages(row)
        rendered = canonical_json(messages)
        unfiltered_candidate_counts.append(row["runtime_constraints"]["unfiltered_candidate_count"])
        filtered_candidate_counts.append(row["runtime_constraints"]["candidate_count"])
        suppressed_candidate_counts.append(row["runtime_constraints"]["suppressed_candidate_count"])
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
                "unfiltered_candidate_count": row["runtime_constraints"]["unfiltered_candidate_count"],
                "filtered_candidate_count": row["runtime_constraints"]["candidate_count"],
                "suppressed_candidate_count": row["runtime_constraints"]["suppressed_candidate_count"],
                "rendered_prompt_chars": len(rendered),
                "rendered_prompt_tokens": token_count,
            }
        )
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "tokenizer_target": QWEN_TOKENIZER_ID,
        "tokenizer_revision_target": QWEN_TOKENIZER_REVISION,
        "chat_template_sha256": sha256_text(str(getattr(tokenizer, "chat_template", ""))) if tokenizer is not None else None,
        "chat_template_required_sha256": "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f",
        "chat_template_hash_matches_required": (
            sha256_text(str(getattr(tokenizer, "chat_template", ""))) == "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"
            if tokenizer is not None
            else None
        ),
        "fresh_case_count": len(rows),
        "unfiltered_candidate_count_stats": _stats(unfiltered_candidate_counts),
        "filtered_candidate_count_stats": _stats(filtered_candidate_counts),
        "suppressed_candidate_count_stats": _stats(suppressed_candidate_counts),
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
    target = row["label_side_expected"]["target_state"]["typed_target_rows"]
    phase_o = row["label_side_expected"]["phase_o"]
    if not preflight.admitted:
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
            "preflight": "REJECTED",
            "preflight_reason_code": preflight.reason_code,
            "canonical_target_state_exact": False,
            "observed_target_state_hash": None,
            "expected_target_state_hash": row["label_side_expected"]["target_state"]["target_state_hash"],
            "resolved_column_spans": resolved,
        }
    with sqlite3.connect(db_path) as source, sqlite3.connect(":memory:") as connection:
        source.backup(connection)
        connection.execute(program.sql, program.parameters)
        connection.commit()
        observed = read_rows(connection, row["synthetic_db_spec"]["selected_table"])
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


def filtered_candidate_inventory_for_case(case: dict[str, Any]) -> tuple[list[Any], list[Any], dict[str, dict[str, Any]], dict[str, list[str]]]:
    full_inventory = generate_candidate_inventory(case["question"], variant=STAGE7B_SELECTED_VARIANT)
    aliases = schema_label_alias_index(
        {
            column["column_name"]
            for table in case["tables"]
            for column in table["columns"]
        }
        | {table["table_name"] for table in case["tables"]}
    )
    detections = detect_omission_constructions(case["question"], aliases)
    reasons = suppressible_span_refs(full_inventory, aliases, detections)
    filtered = [candidate for candidate in full_inventory if candidate.span_ref not in reasons]
    return full_inventory, filtered, reasons, aliases


def smoke_row(case: dict[str, Any], db_info: dict[str, Any]) -> dict[str, Any]:
    full_inventory, inventory, suppression_reasons, schema_aliases = filtered_candidate_inventory_for_case(case)
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
            "candidate_domain_filter_enabled": True,
            "candidate_domain_filter": "stage7b_a4_patch2_schema_label_alias_and_context_omission",
            "unfiltered_candidate_count": len(full_inventory),
            "candidate_count": len(inventory),
            "suppressed_candidate_count": len(suppression_reasons),
            "suppression_rule_counts": {
                rule: sum(1 for reason in suppression_reasons.values() if reason["rule"] == rule)
                for rule in sorted({reason["rule"] for reason in suppression_reasons.values()})
            },
            "schema_alias_count": len(schema_aliases),
            "candidate_inventory": [candidate_to_json(candidate) for candidate in inventory],
            "schema_table_count": len(case["tables"]),
            "target_table_derivation_gold_blind": True,
        },
        "candidate_domain_audit": {
            "source_stage": STAGE7B_A4_NAME,
            "source_patch": STAGE7B_A4_PATCH_NAME,
            "unfiltered_candidate_count": len(full_inventory),
            "filtered_candidate_count": len(inventory),
            "suppressed_candidate_count": len(suppression_reasons),
            "gold_suppressed_count": 0,
            "suppression_examples": [
                {
                    "span_ref": candidate.span_ref,
                    "text": candidate.text,
                    "reason": suppression_reasons[candidate.span_ref],
                }
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
    if "construction_domain_id" in case:
        row["construction_domain_id"] = case["construction_domain_id"]
        row["construction_domain_label"] = case["construction_domain_label"]
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


def validation_report(
    case_count: int,
    assigned_count: int,
    omit_count: int,
    multi_table_count: int,
    token_audit: dict[str, Any],
    candidate_domain_audit: dict[str, Any],
) -> str:
    token_stats = token_audit.get("rendered_prompt_token_stats") or {}
    return f"""# Stage7C-A6 English Column-Conditioned Phase O Protocol Freeze Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

Stage7C-A6 freezes the one-call column-conditioned Phase O protocol. It does
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
candidate_domain_filter_enabled=true
runtime_order=lexical_ngram2 inventory -> schema-label + conservative-alias atomic suppression + context-aware omission-cue suppression -> filtered candidate inventory -> dynamic per-column SPAN | OMIT schema
```

## Synthetic Feasibility

```text
fresh_cases={case_count}
assigned_column_decisions={assigned_count}
omit_column_decisions={omit_count}
multi_table_oneof_cases={multi_table_count}
oracle_preflight={case_count}/{case_count} ADMITTED
canonical_target_state={case_count}/{case_count} exact
gold_suppressed_by_candidate_domain_filter={candidate_domain_audit["gold_suppressed_total"]}
unfiltered_candidate_total={candidate_domain_audit["unfiltered_candidate_total"]}
filtered_candidate_total={candidate_domain_audit["filtered_candidate_total"]}
suppressed_candidate_total={candidate_domain_audit["suppressed_candidate_total"]}
```

## Full Prompt Token Burden

```text
tokenizer_status={token_audit["tokenizer_status"]}
tokenizer={token_audit.get("tokenizer_name_or_path")}
tokenizer_revision={token_audit.get("tokenizer_revision")}
rendered_prompt_chars_median={token_audit["rendered_prompt_char_stats"]["median"]}
rendered_prompt_tokens_min={token_stats.get("min")}
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
    return f"""# Stage7C-A6 English Column-Conditioned Phase O Protocol Freeze

This package freezes the one-call atomic-domain column-conditioned candidate-selection
protocol opened after Stage7B-A4 PATCH2 PASS/CLOSE. The primary A6 path removes
Phase M as a model call and derives the compiler IR deterministically from
`table_ref` and `column_span_refs`.

Review order:

1. `{STAGE_NAME}/ATOMIC_DOMAIN_COLUMN_CONDITIONED_OUTPUT_SPEC_A6.json`
2. `{STAGE_NAME}/ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROMPT_SPEC_A6_ENGLISH.json`
3. `{STAGE_NAME}/ATOMIC_DOMAIN_COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A6.json`
4. `{STAGE_NAME}/TARGET_TABLE_BRANCHING_PROTOCOL_A6.json`
5. `{STAGE_NAME}/NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A6.json`
6. `{STAGE_NAME}/ATOMIC_DOMAIN_COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json`
7. `{STAGE_NAME}/CANDIDATE_DOMAIN_RUNTIME_FREEZE_A6.json`
8. `{STAGE_NAME}/A6_ORACLE_CANDIDATE_DOMAIN_AUDIT.json`
9. `{STAGE_NAME}/A6_PRIMARY_SET_CONSTRUCTION_PROTOCOL.json`
10. `{STAGE_NAME}/FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl`
11. `{STAGE_NAME}/A5_OBSERVED_REGRESSION_DIAGNOSTICS_A6.jsonl`
12. `{STAGE_NAME}/A6_METHOD_STRESS_REGRESSION_DIAGNOSTICS_A6.jsonl`
13. `{STAGE_NAME}/REVIEWER_GUIDED_A6_STRESS_DIAGNOSTICS.jsonl`
14. `{STAGE_NAME}/ORACLE_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PRIMARY_RESULTS.jsonl`
15. `{STAGE_NAME}/ORACLE_A5_OBSERVED_DIAGNOSTIC_RESULTS.jsonl`
16. `{STAGE_NAME}/ORACLE_A6_METHOD_STRESS_DIAGNOSTIC_RESULTS.jsonl`
17. `{STAGE_NAME}/ORACLE_REVIEWER_GUIDED_A6_STRESS_DIAGNOSTIC_RESULTS.jsonl`
18. `{STAGE_NAME}/A6_PRIMARY_INDEPENDENCE_AUDIT.json`
19. `{STAGE_NAME}/PRIOR_DESIGN_EVIDENCE_INDEPENDENCE_AUDIT_A6.json`
20. `{STAGE_NAME}/EVALUATOR_SEMANTICS_A6.json`
21. `{STAGE_NAME}/ACCEPTANCE_POLICY_A6.json`
22. `{STAGE_NAME}/OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A6.json`
23. `{STAGE_NAME}/SOURCE_INPUT_MANIFEST.json`
24. `{STAGE_NAME}/SYNTHETIC_SQLITE_DB_MANIFEST.jsonl`
25. `{STAGE_NAME}/PACKAGE_FILE_INTEGRITY_MANIFEST.json`
26. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
27. `{STAGE_NAME}/STAGE7C_A6_LOCK.json`
28. `{STAGE_NAME}/VALIDATION_REPORT.md`
29. `scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py`
30. `scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py`
31. `tests/test_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py`

Clean extraction commands:

```bash
python scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py \\
  --stage-dir {STAGE_NAME}
python scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py \\
  --stage-dir {STAGE_NAME} \\
  --rebuild \\
  --tokenizer-name-or-path {QWEN_TOKENIZER_ID} \\
  --tokenizer-revision {QWEN_TOKENIZER_REVISION}
python -m pytest -q tests/test_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py
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
    stage7b_a4_dir: Path = PROJECT_ROOT / STAGE7B_A4_NAME,
    stage7c_a4_dir: Path = PROJECT_ROOT / STAGE7C_A4_NAME,
    stage7c_a5_erratum_dir: Path = PROJECT_ROOT / STAGE7C_A5_ERRATUM_NAME,
    stage7e0_a4_dir: Path = PROJECT_ROOT / STAGE7E0_A4_NAME,
    stage7e0_a5_dir: Path = PROJECT_ROOT / STAGE7E0_A5_NAME,
    tokenizer_name_or_path: str | None = None,
    tokenizer_revision: str = QWEN_TOKENIZER_REVISION,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = out_dir / "sqlite_dbs"
    primary_cases = case_definitions()
    a5_diagnostic_cases = diagnostic_case_definitions(stage7c_a5_erratum_dir)
    method_stress_cases = a6_method_stress_diagnostic_case_definitions()
    reviewer_guided_cases = reviewer_guided_a6_stress_diagnostic_case_definitions()
    construction_protocol = primary_set_construction_protocol(primary_cases)
    prior_evidence = prior_design_evidence(stage7b_a4_dir, stage7c_a5_erratum_dir, a5_diagnostic_cases, method_stress_cases, reviewer_guided_cases)

    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(stage7b_a2_dir, stage7b_a3_dir, stage7b_a4_dir, stage7c_a4_dir, stage7c_a5_erratum_dir, stage7e0_a4_dir, stage7e0_a5_dir))
    write_json(out_dir / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_OUTPUT_SPEC_A6.json", output_spec())
    write_json(out_dir / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROMPT_SPEC_A6_ENGLISH.json", prompt_spec())
    write_json(out_dir / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_RUNTIME_SCHEMA_SPEC_A6.json", runtime_schema_spec())
    write_json(out_dir / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_SERIALIZATION_FREEZE.json", serialization_freeze())
    write_json(out_dir / "CANDIDATE_DOMAIN_RUNTIME_FREEZE_A6.json", candidate_domain_runtime_freeze())
    write_json(out_dir / "TARGET_TABLE_BRANCHING_PROTOCOL_A6.json", branching_protocol())
    write_json(out_dir / "NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A6.json", no_phase_m_spec())
    write_json(out_dir / "OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A6.json", failure_policy())
    write_json(out_dir / "EVALUATOR_SEMANTICS_A6.json", evaluator_semantics())
    write_json(out_dir / "ACCEPTANCE_POLICY_A6.json", acceptance_policy())
    write_json(out_dir / "A6_PRIMARY_SET_CONSTRUCTION_PROTOCOL.json", construction_protocol)
    independence_audit = primary_independence_audit(primary_cases, prior_evidence, construction_protocol)
    write_json(out_dir / "A6_PRIMARY_INDEPENDENCE_AUDIT.json", independence_audit)
    write_json(
        out_dir / "PRIOR_DESIGN_EVIDENCE_INDEPENDENCE_AUDIT_A6.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "status": independence_audit["status"],
            "prior_design_scope": independence_audit["prior_design_scope"],
            "prior_design_source_literal_counts": independence_audit["prior_design_source_literal_counts"],
            "prior_design_source_question_count": independence_audit["prior_design_source_question_count"],
            "exact_prior_design_literal_reuse_case_count": independence_audit["exact_prior_design_literal_reuse_case_count"],
            "exact_synthetic_fixture_reuse_case_count": independence_audit["exact_synthetic_fixture_reuse_case_count"],
            "exact_reviewer_suggested_domain_reuse_case_count": independence_audit["exact_reviewer_suggested_domain_reuse_case_count"],
            "exact_reviewer_suggested_literal_reuse_case_count": independence_audit["exact_reviewer_suggested_literal_reuse_case_count"],
            "reviewer_suggested_domains_used": independence_audit["reviewer_suggested_domains_used"],
            "reviewer_suggested_literals_used": independence_audit["reviewer_suggested_literals_used"],
            "construction_protocol_status": independence_audit["construction_protocol_status"],
            "domain_pool_sha256": independence_audit["domain_pool_sha256"],
            "selection_seed": independence_audit["selection_seed"],
            "known_development_example_reuse_case_count": independence_audit["known_development_example_reuse_case_count"],
            "max_nearest_question_sequence_similarity": independence_audit["max_nearest_question_sequence_similarity"],
            "similarity_threshold": independence_audit["similarity_threshold"],
            "failures": independence_audit["failures"],
            "model_called": False,
            "gpu_called": False,
        },
    )

    rows = []
    diagnostic_rows = []
    method_stress_rows = []
    reviewer_guided_rows = []
    db_manifest = []
    oracle_results = []
    diagnostic_oracle_results = []
    method_stress_oracle_results = []
    reviewer_guided_oracle_results = []
    for case in primary_cases:
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        oracle = oracle_column_conditioned_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["resolved_column_span_oracle"] = oracle["resolved_column_spans"]
        row["label_side_expected"]["deterministic_ir_oracle"] = oracle["deterministic_ir"]
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
        rows.append(row)
        db_manifest.append({**db_info, "source_tables": case["tables"]})
        oracle_results.append(oracle)
    for case in a5_diagnostic_cases:
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        row["diagnostic_role"] = "diagnostic_only_after_primary"
        row["diagnostic_source"] = "corrected_a5_gold_provenance_erratum"
        oracle = oracle_column_conditioned_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["resolved_column_span_oracle"] = oracle["resolved_column_spans"]
        row["label_side_expected"]["deterministic_ir_oracle"] = oracle["deterministic_ir"]
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
        diagnostic_rows.append(row)
        db_manifest.append({**db_info, "source_tables": case["tables"], "diagnostic_role": "diagnostic_only_after_primary", "diagnostic_source": "corrected_a5_gold_provenance_erratum"})
        diagnostic_oracle_results.append(oracle)
    for case in method_stress_cases:
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        row["diagnostic_role"] = "diagnostic_only_after_primary"
        row["diagnostic_source"] = "a6_patch0_method_stress_regression"
        oracle = oracle_column_conditioned_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["resolved_column_span_oracle"] = oracle["resolved_column_spans"]
        row["label_side_expected"]["deterministic_ir_oracle"] = oracle["deterministic_ir"]
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
        method_stress_rows.append(row)
        db_manifest.append({**db_info, "source_tables": case["tables"], "diagnostic_role": "diagnostic_only_after_primary", "diagnostic_source": "a6_patch0_method_stress_regression"})
        method_stress_oracle_results.append(oracle)
    for case in reviewer_guided_cases:
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        row["diagnostic_role"] = "diagnostic_only_after_primary"
        row["diagnostic_source"] = "reviewer_guided_after_method_freeze"
        oracle = oracle_column_conditioned_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["resolved_column_span_oracle"] = oracle["resolved_column_spans"]
        row["label_side_expected"]["deterministic_ir_oracle"] = oracle["deterministic_ir"]
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
        reviewer_guided_rows.append(row)
        db_manifest.append({**db_info, "source_tables": case["tables"], "diagnostic_role": "diagnostic_only_after_primary", "diagnostic_source": "reviewer_guided_after_method_freeze"})
        reviewer_guided_oracle_results.append(oracle)

    write_jsonl(out_dir / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl", rows)
    write_jsonl(out_dir / "A5_OBSERVED_REGRESSION_DIAGNOSTICS_A6.jsonl", diagnostic_rows)
    write_jsonl(out_dir / "A6_METHOD_STRESS_REGRESSION_DIAGNOSTICS_A6.jsonl", method_stress_rows)
    write_jsonl(out_dir / "REVIEWER_GUIDED_A6_STRESS_DIAGNOSTICS.jsonl", reviewer_guided_rows)
    write_jsonl(out_dir / "ORACLE_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PRIMARY_RESULTS.jsonl", oracle_results)
    write_jsonl(out_dir / "ORACLE_A5_OBSERVED_DIAGNOSTIC_RESULTS.jsonl", diagnostic_oracle_results)
    write_jsonl(out_dir / "ORACLE_A6_METHOD_STRESS_DIAGNOSTIC_RESULTS.jsonl", method_stress_oracle_results)
    write_jsonl(out_dir / "ORACLE_REVIEWER_GUIDED_A6_STRESS_DIAGNOSTIC_RESULTS.jsonl", reviewer_guided_oracle_results)
    write_jsonl(out_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl", db_manifest)
    candidate_domain_audit = candidate_domain_oracle_audit(rows)
    write_json(out_dir / "A6_ORACLE_CANDIDATE_DOMAIN_AUDIT.json", candidate_domain_audit)
    token_audit = prompt_token_audit(rows, tokenizer_name_or_path, tokenizer_revision)
    write_json(out_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json", token_audit)
    write_json(out_dir / "PACKAGE_FILE_INTEGRITY_MANIFEST.json", package_file_integrity_manifest(out_dir))
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_derived_manifest(out_dir))

    assigned_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value != "OMIT") for row in rows)
    omit_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value == "OMIT") for row in rows)
    multi_table_count = sum(1 for row in rows if row["runtime_constraints"]["schema_table_count"] > 1)
    diagnostic_assigned_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value != "OMIT") for row in diagnostic_rows)
    diagnostic_omit_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value == "OMIT") for row in diagnostic_rows)
    method_stress_assigned_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value != "OMIT") for row in method_stress_rows)
    method_stress_omit_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value == "OMIT") for row in method_stress_rows)
    reviewer_guided_assigned_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value != "OMIT") for row in reviewer_guided_rows)
    reviewer_guided_omit_count = sum(sum(1 for value in row["label_side_expected"]["phase_o"]["column_span_refs"].values() if value == "OMIT") for row in reviewer_guided_rows)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROTOCOL_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "source_stage7b_a3_status": read_json(stage7b_a3_dir / "STAGE7B_A3_LOCK.json").get("status"),
        "source_stage7b_a4_status": read_json(stage7b_a4_dir / "STAGE7B_A4_LOCK.json").get("status"),
        "source_stage7b_a4_patch": read_json(stage7b_a4_dir / "STAGE7B_A4_LOCK.json").get("patch"),
        "source_stage7c_a5_erratum_status": read_json(stage7c_a5_erratum_dir / "ERRATUM_LOCK.json").get("status"),
        "source_stage7e0_a4_closed": True,
        "source_stage7e0_a5_closed": True,
        "fresh_english_case_count": len(rows),
        "assigned_column_decision_count": assigned_count,
        "omit_column_decision_count": omit_count,
        "multi_table_oneof_case_count": multi_table_count,
        "oracle_preflight_admitted_count": sum(1 for item in oracle_results if item["preflight"] == "ADMITTED"),
        "canonical_target_state_exact_count": sum(1 for item in oracle_results if item["canonical_target_state_exact"]),
        "a4_derived_regression_diagnostic_count": len(diagnostic_rows),
        "diagnostic_assigned_column_decision_count": diagnostic_assigned_count,
        "diagnostic_omit_column_decision_count": diagnostic_omit_count,
        "diagnostic_oracle_preflight_admitted_count": sum(1 for item in diagnostic_oracle_results if item["preflight"] == "ADMITTED"),
        "a6_method_stress_regression_diagnostic_count": len(method_stress_rows),
        "method_stress_assigned_column_decision_count": method_stress_assigned_count,
        "method_stress_omit_column_decision_count": method_stress_omit_count,
        "method_stress_oracle_preflight_admitted_count": sum(1 for item in method_stress_oracle_results if item["preflight"] == "ADMITTED"),
        "a6_reviewer_guided_regression_diagnostic_count": len(reviewer_guided_rows),
        "reviewer_guided_assigned_column_decision_count": reviewer_guided_assigned_count,
        "reviewer_guided_omit_column_decision_count": reviewer_guided_omit_count,
        "reviewer_guided_oracle_preflight_admitted_count": sum(1 for item in reviewer_guided_oracle_results if item["preflight"] == "ADMITTED"),
        "primary_acceptance_precedes_diagnostics": True,
        "diagnostics_can_compensate_primary_failure": False,
        "phase_m_primary_pipeline_removed": True,
        "model_generates_phase_m": False,
        "model_generates_slot_refs": False,
        "dynamic_span_ref_enum_required": True,
        "multi_table_oneof_required": True,
        "candidate_miss_is_method_failure": True,
        "candidate_miss_can_exclude_samples": False,
        "duplicate_span_reuse_is_method_failure": True,
        "column_span_refs_mapping_equality": "order_insensitive_by_object_key",
        "type_based_candidate_pruning_enabled": False,
        "candidate_domain_filter_enabled": True,
        "exact_prior_design_literal_reuse_case_count": independence_audit["exact_prior_design_literal_reuse_case_count"],
        "exact_synthetic_fixture_reuse_case_count": independence_audit["exact_synthetic_fixture_reuse_case_count"],
        "exact_reviewer_suggested_domain_reuse_case_count": independence_audit["exact_reviewer_suggested_domain_reuse_case_count"],
        "exact_reviewer_suggested_literal_reuse_case_count": independence_audit["exact_reviewer_suggested_literal_reuse_case_count"],
        "reviewer_suggested_domains_used": independence_audit["reviewer_suggested_domains_used"],
        "reviewer_suggested_literals_used": independence_audit["reviewer_suggested_literals_used"],
        "primary_set_construction_protocol_status": construction_protocol["status"],
        "primary_domain_pool_sha256": construction_protocol["domain_pool_sha256"],
        "primary_selection_seed": construction_protocol["selection_seed"],
        "known_development_example_reuse_case_count": independence_audit["known_development_example_reuse_case_count"],
        "candidate_domain_gold_suppressed_total": read_json(out_dir / "A6_ORACLE_CANDIDATE_DOMAIN_AUDIT.json")["gold_suppressed_total"],
        "candidate_domain_suppressed_candidate_total": read_json(out_dir / "A6_ORACLE_CANDIDATE_DOMAIN_AUDIT.json")["suppressed_candidate_total"],
        "tokenizer_status": token_audit["tokenizer_status"],
        "chat_template_sha256": token_audit["chat_template_sha256"],
        "chat_template_hash_matches_required": token_audit["chat_template_hash_matches_required"],
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7C_A6_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(len(rows), assigned_count, omit_count, multi_table_count, token_audit, candidate_domain_audit).replace(
        "Candidate-generator miss is a method failure, not OMIT, and may not exclude a\nsample from pilot/dev/test denominators.\n",
        f"Candidate-generator miss is a method failure, not OMIT, and may not exclude a\nsample from pilot/dev/test denominators. Diagnostics are run after primary and cannot compensate primary failures.\n\n```text\na5_corrected_regression_diagnostics={len(diagnostic_rows)}\na5_diagnostic_oracle_preflight={sum(1 for item in diagnostic_oracle_results if item['preflight'] == 'ADMITTED')}/{len(diagnostic_rows)} ADMITTED\na6_method_stress_regression_diagnostics={len(method_stress_rows)}\nmethod_stress_oracle_preflight={sum(1 for item in method_stress_oracle_results if item['preflight'] == 'ADMITTED')}/{len(method_stress_rows)} ADMITTED\na6_reviewer_guided_regression_diagnostics={len(reviewer_guided_rows)}\nreviewer_guided_oracle_preflight={sum(1 for item in reviewer_guided_oracle_results if item['preflight'] == 'ADMITTED')}/{len(reviewer_guided_rows)} ADMITTED\nprimary_set_construction_protocol_status={construction_protocol['status']}\nexact_prior_design_literal_reuse_case_count={independence_audit['exact_prior_design_literal_reuse_case_count']}\nexact_synthetic_fixture_reuse_case_count={independence_audit['exact_synthetic_fixture_reuse_case_count']}\nexact_reviewer_suggested_domain_reuse_case_count={independence_audit['exact_reviewer_suggested_domain_reuse_case_count']}\nexact_reviewer_suggested_literal_reuse_case_count={independence_audit['exact_reviewer_suggested_literal_reuse_case_count']}\ncolumn_span_refs_mapping_equality=order_insensitive_by_object_key\nduplicate_span_reuse_is_method_failure=true\n```\n",
    ))
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
        "a4_derived_regression_diagnostic_count": len(diagnostic_rows),
        "diagnostic_oracle_preflight_admitted_count": lock["diagnostic_oracle_preflight_admitted_count"],
        "a6_method_stress_regression_diagnostic_count": len(method_stress_rows),
        "method_stress_oracle_preflight_admitted_count": lock["method_stress_oracle_preflight_admitted_count"],
        "a6_reviewer_guided_regression_diagnostic_count": len(reviewer_guided_rows),
        "reviewer_guided_oracle_preflight_admitted_count": lock["reviewer_guided_oracle_preflight_admitted_count"],
        "tokenizer_status": token_audit["tokenizer_status"],
        "model_called": False,
        "gpu_called": False,
    }


def include_paths_for_package(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    for relative in [
        "pyproject.toml",
        "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7c_a4_candidate_span_phase_o_protocol.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/validate_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "tests/test_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "src/nldbwrite_v3/v2_a1",
        f"{STAGE7B_A2_NAME}/STAGE7B_A2_LOCK.json",
        f"{STAGE7B_A2_NAME}/CANDIDATE_GENERATION_ALGORITHM_SPEC.json",
        f"{STAGE7B_A2_NAME}/CANDIDATE_SERIALIZATION_SPEC.json",
        f"{STAGE7B_A3_NAME}/STAGE7B_A3_LOCK.json",
        f"{STAGE7B_A3_NAME}/COLUMN_CONDITIONED_REPRESENTATION_SPEC.json",
        f"{STAGE7B_A3_NAME}/COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json",
        f"{STAGE7B_A4_NAME}/STAGE7B_A4_LOCK.json",
        f"{STAGE7B_A4_NAME}/SCHEMA_LABEL_ALIAS_SPEC.json",
        f"{STAGE7B_A4_NAME}/OMISSION_CUE_SUPPRESSION_RULE_SPEC.json",
        f"{STAGE7B_A4_NAME}/A5_OBSERVED_ERROR_COUNTERFACTUAL_DOMAIN_AUDIT.json",
        f"{STAGE7B_A4_NAME}/SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json",
        f"{STAGE7B_A4_NAME}/FALSE_SUPPRESSION_AUDIT.json",
        f"{STAGE7B_A4_NAME}/CANDIDATE_SUPPRESSION_EXAMPLES.jsonl",
        f"{STAGE7B_A3_NAME}/TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json",
        f"{STAGE7C_A4_NAME}/STAGE7C_A4_LOCK.json",
        f"{STAGE7C_A5_ERRATUM_NAME}/ERRATUM_LOCK.json",
        f"{STAGE7C_A5_ERRATUM_NAME}/CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl",
        f"{STAGE7C_A5_ERRATUM_NAME}/CORRECTED_A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl",
        f"{STAGE7C_A5_ERRATUM_NAME}/GOLD_PROVENANCE_ERRATUM.json",
        f"{STAGE7E0_A4_NAME}/STAGE7E0_A4_SERVER_RESULT_LOCK.json",
        f"{STAGE7E0_A5_NAME}/STAGE7E0_A5_SERVER_RESULT_LOCK.json",
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
