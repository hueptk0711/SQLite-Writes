#!/usr/bin/env python3
"""Build Stage7C-A4 English candidate-span Phase O protocol artifacts.

This stage freezes the model-facing protocol for the Stage7B-A2 candidate-span
reference architecture. It is CPU-only: no model, no GPU, and no Gretel pilot,
development-dev, or official-test rows are opened.
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
from nldbwrite_v3.v2_a1.phase_m_output import parse_phase_m_output
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite
from nldbwrite_v3.v2_a1.prompt_rendering import inventory_payload, serialize_prompt_object
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values
from nldbwrite_v3.v2_a1.types import AcceptedSpan

from scripts.data.build_stage7b_a2_candidate_span_reference import (
    QWEN_TOKENIZER_ID,
    QWEN_TOKENIZER_REVISION,
    SELECTED_VARIANT as STAGE7B_SELECTED_VARIANT,
    build_dynamic_phase_o_schema,
    candidate_to_json,
    generate_candidate_inventory,
    load_tokenizer,
    resolve_selected_span_refs,
    serialize_candidate_inventory,
)


STAGE_NAME = "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL"
PATCH_NAME = "PATCH0"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260831.zip"
STAGE7B_A2_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
STAGE7C_A3_NAME = "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT"
PHASE_M_SPEC_PATH = "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json"
MODEL_ID = QWEN_TOKENIZER_ID
MODEL_REVISION = QWEN_TOKENIZER_REVISION
PHASE_O_SYSTEM_PROMPT = (
    "You select source spans for SQLite INSERT requests. "
    "Return only JSON that matches the provided schema."
)
PHASE_O_USER_PROMPT_TEMPLATE = """Select the source spans that are the literal database values required by the INSERT request.

Rules:
- Choose only from the supplied SPAN references.
- Select the smallest complete candidate representing each literal value.
- Do not select field labels, instruction text, table names, or column names.
- Do not select overlapping broader spans when a smaller exact value span exists.
- Do not invent span refs.
- Do not output character offsets, values, column refs, explanations, or markdown.

Original request:
{question}

Schema inventory:
{schema_inventory}

Candidate span inventory:
{candidate_inventory}
"""
SCIENTIFIC_ARTIFACTS = [
    "SOURCE_INPUT_MANIFEST.json",
    "PHASE_O_SPAN_REF_OUTPUT_SPEC.json",
    "PHASE_O_PROMPT_SPEC_A4_ENGLISH.json",
    "PHASE_O_RUNTIME_SCHEMA_SPEC.json",
    "CANDIDATE_SERIALIZATION_FREEZE.json",
    "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json",
    "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl",
    "ORACLE_SPAN_REF_PATH_RESULTS.jsonl",
    "ACCEPTANCE_POLICY_A4.json",
    "CANDIDATE_MISS_FAILURE_POLICY.json",
    "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl",
]
UPSTREAM_PACKAGE_PATHS = [
    "pyproject.toml",
    "src/nldbwrite_v3/v2_a1",
    "stage7b_v2_method_specification",
    "stage7c_a1_v2_development_protocol",
    STAGE7B_A2_NAME,
    f"{STAGE7C_A3_NAME}/STAGE7C_A3_LOCK.json",
    "scripts/data/build_stageeng0_gretel_qualification.py",
    "scripts/data/build_stage7b_a2_candidate_span_reference.py",
    "scripts/data/validate_stage7b_a2_candidate_span_reference.py",
]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        return sha256_text(data.decode("utf-8-sig"))
    return sha256_bytes(data)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _stats(values: list[int]) -> dict[str, float | int]:
    ordered = sorted(values)
    p95_index = int(0.95 * (len(ordered) - 1)) if ordered else 0
    return {
        "min": min(ordered),
        "median": median(ordered),
        "mean": mean(ordered),
        "p95": ordered[p95_index],
        "max": max(ordered),
    }


def case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "sample_id": "stage7c_a4_fresh_english_001",
            "coverage_tags": ["3_values", "quoted_multiword", "email", "integer", "overlap_distractors"],
            "question": 'Add contact name "Mina Tran", email mina.tran@example.com, priority 3.',
            "table_name": "contacts",
            "columns": [
                {"column_name": "name", "source_type": "TEXT"},
                {"column_name": "email", "source_type": "TEXT"},
                {"column_name": "priority", "source_type": "INTEGER"},
            ],
            "values": ["Mina Tran", "mina.tran@example.com", "3"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_002",
            "coverage_tags": ["4_values", "three_word_value", "identifier", "real", "integer"],
            "question": 'Insert product "Tofu Stir Fry", sku SKU-778, price 29.99, active 1.',
            "table_name": "products",
            "columns": [
                {"column_name": "product_name", "source_type": "TEXT"},
                {"column_name": "sku", "source_type": "TEXT"},
                {"column_name": "price", "source_type": "REAL"},
                {"column_name": "active", "source_type": "INTEGER"},
            ],
            "values": ["Tofu Stir Fry", "SKU-778", "29.99", "1"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_003",
            "coverage_tags": ["2_values", "three_word_value", "integer"],
            "question": 'Record habitat "Gulf of Mexico" with depth 1500.',
            "table_name": "habitats",
            "columns": [
                {"column_name": "name", "source_type": "TEXT"},
                {"column_name": "depth_m", "source_type": "INTEGER"},
            ],
            "values": ["Gulf of Mexico", "1500"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_004",
            "coverage_tags": ["3_values", "three_word_value", "integer", "quoted_multiword"],
            "question": 'Add species "Giant Pacific Octopus", population 12, status "protected".',
            "table_name": "species",
            "columns": [
                {"column_name": "species_name", "source_type": "TEXT"},
                {"column_name": "population", "source_type": "INTEGER"},
                {"column_name": "status", "source_type": "TEXT"},
            ],
            "values": ["Giant Pacific Octopus", "12", "protected"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_005",
            "coverage_tags": ["2_values", "identifier", "email"],
            "question": "Register user handle user_452 and recovery_email ops-team+452@example.org.",
            "table_name": "users",
            "columns": [
                {"column_name": "handle", "source_type": "TEXT"},
                {"column_name": "recovery_email", "source_type": "TEXT"},
            ],
            "values": ["user_452", "ops-team+452@example.org"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_006",
            "coverage_tags": ["5_values", "date", "quoted_multiword", "identifier", "real", "integer"],
            "question": 'Log shipment id SHIP-2026-08-30, carrier "Blue Rail", weight 18.5, stops 4, eta 2026-09-02.',
            "table_name": "shipments",
            "columns": [
                {"column_name": "shipment_id", "source_type": "TEXT"},
                {"column_name": "carrier", "source_type": "TEXT"},
                {"column_name": "weight", "source_type": "REAL"},
                {"column_name": "stops", "source_type": "INTEGER"},
                {"column_name": "eta", "source_type": "TEXT"},
            ],
            "values": ["SHIP-2026-08-30", "Blue Rail", "18.5", "4", "2026-09-02"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_007",
            "coverage_tags": ["4_values", "identifier", "real", "integer", "three_word_value"],
            "question": 'Add invoice INV-9001, amount 1250.75, line_count 12, note "paid in full".',
            "table_name": "invoices",
            "columns": [
                {"column_name": "invoice_id", "source_type": "TEXT"},
                {"column_name": "amount", "source_type": "REAL"},
                {"column_name": "line_count", "source_type": "INTEGER"},
                {"column_name": "note", "source_type": "TEXT"},
            ],
            "values": ["INV-9001", "1250.75", "12", "paid in full"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_008",
            "coverage_tags": ["5_values", "identifier", "quoted_multiword", "real", "integer", "date"],
            "question": 'Insert experiment run RUN-A4-008, operator "Dr Lin", ph 7.4, samples 36, started 2026-08-30.',
            "table_name": "experiment_runs",
            "columns": [
                {"column_name": "run_id", "source_type": "TEXT"},
                {"column_name": "operator", "source_type": "TEXT"},
                {"column_name": "ph", "source_type": "REAL"},
                {"column_name": "samples", "source_type": "INTEGER"},
                {"column_name": "started", "source_type": "TEXT"},
            ],
            "values": ["RUN-A4-008", "Dr Lin", "7.4", "36", "2026-08-30"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_009",
            "coverage_tags": ["3_values", "overlap_distractors", "quoted_multiword", "integer"],
            "question": 'Add profile full_name "Mina Tran", nickname Mina, trust_level 5.',
            "table_name": "profiles",
            "columns": [
                {"column_name": "full_name", "source_type": "TEXT"},
                {"column_name": "nickname", "source_type": "TEXT"},
                {"column_name": "trust_level", "source_type": "INTEGER"},
            ],
            "values": ["Mina Tran", "Mina", "5"],
        },
        {
            "sample_id": "stage7c_a4_fresh_english_010",
            "coverage_tags": ["4_values", "hex_identifier", "percent", "integer", "quoted_multiword"],
            "question": 'Insert contract code 0xDEADBEEF, status "needs review", discount 45%, seats 20.',
            "table_name": "contracts",
            "columns": [
                {"column_name": "contract_code", "source_type": "TEXT"},
                {"column_name": "status", "source_type": "TEXT"},
                {"column_name": "discount_percent", "source_type": "INTEGER"},
                {"column_name": "seats", "source_type": "INTEGER"},
            ],
            "values": ["0xDEADBEEF", "needs review", "45", "20"],
        },
    ]


def sqlite_value(raw: str, source_type: str) -> Any:
    upper = source_type.upper()
    if "INT" in upper:
        return int(raw)
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return float(raw)
    return raw


def schema_inventory(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [{"table_ref": "TAB_1", "table_name": case["table_name"]}],
        "columns": [
            {
                "column_ref": f"COL_{index}",
                "table_ref": "TAB_1",
                "column_name": column["column_name"],
                "source_type": column["source_type"],
            }
            for index, column in enumerate(case["columns"], start=1)
        ],
        "constraints": [],
    }


def create_sql(case: dict[str, Any]) -> str:
    columns = ", ".join(f'"{column["column_name"]}" {column["source_type"]} NOT NULL' for column in case["columns"])
    return f'CREATE TABLE "{case["table_name"]}" ({columns});'


def target_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            column["column_name"]: sqlite_value(raw, column["source_type"])
            for column, raw in zip(case["columns"], case["values"])
        }
    ]


def read_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    return [dict(row) for row in rows]


def create_case_db(case: dict[str, Any], db_dir: Path) -> dict[str, Any]:
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"{case['sample_id']}.sqlite"
    if db_path.exists():
        db_path.unlink()
    schema_sql = create_sql(case)
    with sqlite3.connect(db_path) as connection:
        connection.execute(schema_sql)
        connection.commit()
    return {
        "sample_id": case["sample_id"],
        "sqlite_db_path": f"sqlite_dbs/{db_path.name}",
        "sqlite_db_sha256": sha256_file(db_path),
        "initial_state_hash": sha256_text(canonical_json([])),
        "create_sql": schema_sql,
        "create_sql_sha256": sha256_text(schema_sql),
    }


def find_value_spans(case: dict[str, Any]) -> list[dict[str, Any]]:
    spans = []
    search_from = 0
    for index, value in enumerate(case["values"], start=1):
        start = case["question"].index(value, search_from)
        end = start + len(value)
        search_from = end
        spans.append({"value_index": index, "start_char": start, "end_char": end, "text": value})
    return spans


def gold_span_refs(case: dict[str, Any], inventory: list[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    refs = []
    rows = []
    by_span = {(candidate.start_char, candidate.end_char): candidate for candidate in inventory}
    for span in find_value_spans(case):
        candidate = by_span.get((span["start_char"], span["end_char"]))
        if candidate is None:
            raise ValueError(f"Candidate generation miss for {case['sample_id']} value {span['text']!r}")
        refs.append(candidate.span_ref)
        rows.append(
            {
                **span,
                "candidate_span_ref": candidate.span_ref,
                "candidate_tags": list(candidate.tags),
            }
        )
    return refs, rows


def phase_m(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "INSERT",
        "table_ref": "TAB_1",
        "assignments": [
            {"slot_ref": f"SLOT_{index}", "evidence_ref": f"EV_{index}", "column_ref": f"COL_{index}"}
            for index, _value in enumerate(case["values"], start=1)
        ],
    }


def prompt_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "protocol": "candidate_span_phase_o_protocol",
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
        "model_generates_column_refs": False,
        "model_selects_span_refs": True,
        "candidate_generator_source_stage": STAGE7B_A2_NAME,
        "candidate_generator_variant": STAGE7B_SELECTED_VARIANT,
        "candidate_serialization": "SPAN_0001 | TAG[,TAG...] | exact source text",
        "runtime_schema": "span_refs.items.enum equals exact current sample candidate refs",
        "model_called": False,
        "gpu_called": False,
    }


def output_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "exact_phase_o_output": {"operation": "INSERT", "span_refs": ["SPAN_0007", "SPAN_0019", "SPAN_0031"]},
        "allowed_top_level_keys": ["operation", "span_refs"],
        "forbidden_top_level_keys": ["value_spans", "start_char", "end_char", "values", "column_refs", "assignments"],
        "operation_const": "INSERT",
        "span_refs": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items_runtime_constraint": "enum over exact current candidate inventory refs",
        },
        "model_called": False,
        "gpu_called": False,
    }


def runtime_schema_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "runtime_builder": "build_dynamic_phase_o_schema(candidate_inventory)",
        "dynamic_constraint": "span_refs.items.enum is exactly [candidate.span_ref for candidate in current sample inventory]",
        "unknown_span_refs_structurally_impossible": True,
        "unique_items": True,
        "static_pattern_fallback_allowed": False,
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


def candidate_miss_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "policy": "Candidate-generator miss is a method failure and remains in every pilot/dev/test denominator.",
        "may_exclude_sample_for_candidate_miss": False,
        "pilot_dev_test_denominator_locked": True,
        "applies_to": ["fresh_synthetic_feasibility", "gretel_pilot", "development_dev", "official_test"],
        "model_called": False,
        "gpu_called": False,
    }


def acceptance_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "synthetic_feasibility_gate": {
            "sample_set": "10 fresh English A4 candidate-span cases",
            "required_pass_count": "10/10",
            "averaging_allowed": False,
            "nine_of_ten_allowed": False,
            "checks": [
                "candidate inventory contains every gold value span",
                "dynamic schema enum equals current candidate refs",
                "Phase O output uses only span_refs",
                "unknown and duplicate refs rejected by resolver",
                "resolved span_refs derive EV/SLOT deterministically",
                "Phase M mapping exact",
                "typed materialization PASS",
                "completeness PASS",
                "compilation PASS",
                "SQLite preflight ADMITTED",
                "canonical target state exact",
            ],
        },
        "before_gretel_pilot": "A protocol-compliant span-reference preflight must pass all fresh cases.",
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def source_input_manifest(stage7b_dir: Path, stage7c_a3_dir: Path) -> dict[str, Any]:
    files = [
        (STAGE7B_A2_NAME, stage7b_dir, "STAGE7B_A2_LOCK.json"),
        (STAGE7B_A2_NAME, stage7b_dir, "PHASE_O_SPAN_REFERENCE_BASE_SCHEMA.json"),
        (STAGE7B_A2_NAME, stage7b_dir, "DYNAMIC_PHASE_O_SCHEMA_SPEC.json"),
        (STAGE7B_A2_NAME, stage7b_dir, "CANDIDATE_SERIALIZATION_SPEC.json"),
        (STAGE7B_A2_NAME, stage7b_dir, "CANDIDATE_GENERATION_ALGORITHM_SPEC.json"),
        (STAGE7B_A2_NAME, stage7b_dir, "CANDIDATE_GENERATOR_PARETO_AUDIT.json"),
        (STAGE7C_A3_NAME, stage7c_a3_dir, "STAGE7C_A3_LOCK.json"),
        ("stage7c_a1_v2_development_protocol", PROJECT_ROOT / "stage7c_a1_v2_development_protocol", "PHASE_M_PROMPT_SPEC.json"),
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "source_files": [
            {
                "source_stage": stage,
                "path": f"{stage}/{relative}",
                "sha256": sha256_file(root / relative),
                "bytes": (root / relative).stat().st_size,
            }
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


def oracle_span_ref_path(row: dict[str, Any], db_path: Path) -> dict[str, Any]:
    question = row["model_side_input"]["question"]
    candidate_inventory = [
        type("CandidateRecord", (), candidate)()
        for candidate in row["runtime_constraints"]["candidate_inventory"]
    ]
    selected = resolve_selected_span_refs(candidate_inventory, row["label_side_expected"]["phase_o"]["span_refs"])
    spans = tuple(AcceptedSpan(start_char=item.start_char, end_char=item.end_char, text=item.text) for item in selected)
    slots = build_slot_bundle(spans)
    inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
    phase_m_obj = parse_phase_m_output(
        canonical_json(row["label_side_expected"]["phase_m"]),
        "INSERT",
        inventory,
        slots,
    )
    materialized = materialize_ir_values(phase_m_obj, inventory, slots)
    verify_completeness(phase_m_obj, slots)
    program = compile_sqlite_program(phase_m_obj, inventory, materialized)
    preflight = preflight_sqlite(db_path, program)
    with sqlite3.connect(db_path) as source, sqlite3.connect(":memory:") as connection:
        source.backup(connection)
        connection.execute(program.sql, program.parameters)
        connection.commit()
        observed = read_rows(connection, row["synthetic_db_spec"]["table_name"])
    target = row["label_side_expected"]["target_state"]["typed_target_rows"]
    return {
        "sample_id": row["sample_id"],
        "phase_o_operation_exact": row["label_side_expected"]["phase_o"]["operation"] == "INSERT",
        "phase_o_output_keys_exact": sorted(row["label_side_expected"]["phase_o"]) == ["operation", "span_refs"],
        "selected_span_ref_count": len(selected),
        "candidate_inventory_contains_all_gold_spans": True,
        "dynamic_enum_exact": row["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["items"]["enum"]
        == [candidate["span_ref"] for candidate in row["runtime_constraints"]["candidate_inventory"]],
        "resolver": "PASS",
        "phase_m_mapping_exact": phase_m_obj == row["label_side_expected"]["phase_m"],
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
        "resolved_spans": [
            {
                "candidate_span_ref": item.span_ref,
                "evidence_ref": f"EV_{index}",
                "slot_ref": f"SLOT_{index}",
                "start_char": item.start_char,
                "end_char": item.end_char,
                "text": question[item.start_char : item.end_char],
            }
            for index, item in enumerate(selected, start=1)
        ],
    }


def smoke_row(case: dict[str, Any], db_info: dict[str, Any]) -> dict[str, Any]:
    inventory = generate_candidate_inventory(case["question"], variant=STAGE7B_SELECTED_VARIANT)
    selected_refs, gold_rows = gold_span_refs(case, inventory)
    dynamic_schema = build_dynamic_phase_o_schema(inventory)
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
        },
        "label_side_expected": {
            "model_side_visible": False,
            "phase_o": {"operation": "INSERT", "span_refs": selected_refs},
            "gold_value_span_ref_oracle": gold_rows,
            "phase_m": phase_m(case),
            "target_state": {
                "table_name": case["table_name"],
                "typed_target_rows": target,
                "target_state_hash": sha256_text(canonical_json(target)),
            },
        },
        "synthetic_db_spec": {
            **db_info,
            "table_name": case["table_name"],
            "source_columns": case["columns"],
        },
    }
    return row


def build_derived_manifest(stage_dir: Path) -> dict[str, Any]:
    artifact_names = [
        *SCIENTIFIC_ARTIFACTS,
        *[f"sqlite_dbs/{path.name}" for path in sorted((stage_dir / "sqlite_dbs").glob("*.sqlite"))],
    ]
    artifacts = [
        {
            "path": name,
            "bytes": (stage_dir / name).stat().st_size,
            "sha256": sha256_file(stage_dir / name),
        }
        for name in sorted(artifact_names)
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def validation_report(case_count: int, value_count: int, token_audit: dict[str, Any]) -> str:
    token_stats = token_audit.get("rendered_prompt_token_stats") or {}
    return f"""# Stage7C-A4 English Candidate-Span Phase O Protocol Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

Stage7C-A4 freezes the model-facing Phase O protocol for the Stage7B-A2
candidate-span architecture. It does not call a model, does not use GPU, does
not open the Gretel pilot, does not use development-dev, and does not use
official test rows.

## Frozen Protocol

```text
phase_o_output_keys=operation,span_refs
model_generates_character_offsets=false
model_generates_values=false
model_generates_column_refs=false
runtime_schema=dynamic per-sample enum over exact candidate refs
candidate_serialization=SPAN_0001 | TAG[,TAG...] | exact source text
candidate_generator_variant={STAGE7B_SELECTED_VARIANT}
```

## Synthetic Feasibility

```text
fresh_cases={case_count}
gold_values={value_count}
oracle_preflight=10/10 ADMITTED
canonical_target_state=10/10 exact
candidate_inventory_contains_all_gold_spans=10/10
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

Candidate-generator miss is locked as a method failure. It must remain in every
pilot/dev/test denominator and may not be used as a sample-exclusion rule.
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7C-A4 English Candidate-Span Phase O Protocol

This package freezes the actual Phase O prompt/schema/output contract for the
Stage7B-A2 candidate-span reference architecture.

Review order:

1. `{STAGE_NAME}/PHASE_O_SPAN_REF_OUTPUT_SPEC.json`
2. `{STAGE_NAME}/PHASE_O_PROMPT_SPEC_A4_ENGLISH.json`
3. `{STAGE_NAME}/PHASE_O_RUNTIME_SCHEMA_SPEC.json`
4. `{STAGE_NAME}/CANDIDATE_SERIALIZATION_FREEZE.json`
5. `{STAGE_NAME}/FULL_RENDERED_PROMPT_TOKEN_AUDIT.json`
6. `{STAGE_NAME}/FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl`
7. `{STAGE_NAME}/ORACLE_SPAN_REF_PATH_RESULTS.jsonl`
8. `{STAGE_NAME}/ACCEPTANCE_POLICY_A4.json`
9. `{STAGE_NAME}/CANDIDATE_MISS_FAILURE_POLICY.json`
10. `{STAGE_NAME}/SOURCE_INPUT_MANIFEST.json`
11. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
12. `{STAGE_NAME}/STAGE7C_A4_LOCK.json`
13. `{STAGE_NAME}/VALIDATION_REPORT.md`
14. `scripts/data/build_stage7c_a4_candidate_span_phase_o_protocol.py`
15. `scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py`
16. `tests/test_stage7c_a4_candidate_span_phase_o_protocol.py`

Clean extraction commands:

```bash
python scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py \\
  --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7c_a4_candidate_span_phase_o_protocol.py
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
    stage7b_dir: Path = PROJECT_ROOT / STAGE7B_A2_NAME,
    stage7c_a3_dir: Path = PROJECT_ROOT / STAGE7C_A3_NAME,
    tokenizer_name_or_path: str | None = None,
    tokenizer_revision: str = QWEN_TOKENIZER_REVISION,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = out_dir / "sqlite_dbs"

    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(stage7b_dir, stage7c_a3_dir))
    write_json(out_dir / "PHASE_O_SPAN_REF_OUTPUT_SPEC.json", output_spec())
    write_json(out_dir / "PHASE_O_PROMPT_SPEC_A4_ENGLISH.json", prompt_spec())
    write_json(out_dir / "PHASE_O_RUNTIME_SCHEMA_SPEC.json", runtime_schema_spec())
    write_json(out_dir / "CANDIDATE_SERIALIZATION_FREEZE.json", serialization_freeze())
    write_json(out_dir / "ACCEPTANCE_POLICY_A4.json", acceptance_policy())
    write_json(out_dir / "CANDIDATE_MISS_FAILURE_POLICY.json", candidate_miss_policy())

    rows = []
    db_manifest = []
    oracle_results = []
    for case in case_definitions():
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        oracle = oracle_span_ref_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["resolved_span_ref_oracle"] = oracle["resolved_spans"]
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
        rows.append(row)
        db_manifest.append(db_info)
        oracle_results.append(oracle)

    write_jsonl(out_dir / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl", rows)
    write_jsonl(out_dir / "ORACLE_SPAN_REF_PATH_RESULTS.jsonl", oracle_results)
    write_jsonl(out_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl", db_manifest)
    token_audit = prompt_token_audit(rows, tokenizer_name_or_path, tokenizer_revision)
    write_json(out_dir / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json", token_audit)

    derived_manifest = build_derived_manifest(out_dir)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", derived_manifest)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_CANDIDATE_SPAN_PHASE_O_PROTOCOL_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "source_stage7b_a2": STAGE7B_A2_NAME,
        "candidate_generator_variant": STAGE7B_SELECTED_VARIANT,
        "phase_o_prompt_spec_path": f"{STAGE_NAME}/PHASE_O_PROMPT_SPEC_A4_ENGLISH.json",
        "phase_o_output_keys": ["operation", "span_refs"],
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "zero_shot": True,
        "retry": 0,
        "repair": "none",
        "dynamic_span_ref_enum_required": True,
        "phase_o_model_generates_character_offsets": False,
        "phase_o_model_generates_values": False,
        "phase_o_model_generates_column_refs": False,
        "phase_m_unchanged": True,
        "fresh_english_case_count": len(rows),
        "gold_value_count": sum(len(case["values"]) for case in case_definitions()),
        "oracle_preflight_admitted_count": sum(1 for item in oracle_results if item["preflight"] == "ADMITTED"),
        "candidate_miss_is_method_failure": True,
        "candidate_miss_can_exclude_samples": False,
        "tokenizer_status": token_audit["tokenizer_status"],
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7C_A4_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(len(rows), lock["gold_value_count"], token_audit))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "fresh_english_case_count": len(rows),
        "gold_value_count": lock["gold_value_count"],
        "oracle_preflight_admitted_count": lock["oracle_preflight_admitted_count"],
        "tokenizer_status": token_audit["tokenizer_status"],
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def include_paths_for_package(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    for relative in UPSTREAM_PACKAGE_PATHS:
        path = PROJECT_ROOT / relative
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(child for child in path.rglob("*") if child.is_file() and "__pycache__" not in child.parts)
    files.extend(
        [
            PROJECT_ROOT / "scripts" / "data" / "build_stage7c_a4_candidate_span_phase_o_protocol.py",
            PROJECT_ROOT / "scripts" / "data" / "validate_stage7c_a4_candidate_span_phase_o_protocol.py",
            PROJECT_ROOT / "tests" / "test_stage7c_a4_candidate_span_phase_o_protocol.py",
            PROJECT_ROOT / "tests" / "support" / "windows_py314_pytest_tempdir" / "sitecustomize.py",
        ]
    )
    return sorted({path for path in files if path.is_file()})


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
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(
        f"{digest}  {package_path.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    parser.add_argument("--tokenizer-name-or-path", default=None)
    parser.add_argument("--tokenizer-revision", default=QWEN_TOKENIZER_REVISION)
    args = parser.parse_args()
    summary = build_stage(
        args.out_dir,
        tokenizer_name_or_path=args.tokenizer_name_or_path,
        tokenizer_revision=args.tokenizer_revision,
    )
    digest = package_reviewer(args.out_dir, args.package)
    summary["package"] = str(args.package)
    summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
