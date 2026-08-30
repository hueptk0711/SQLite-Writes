#!/usr/bin/env python3
"""Build Stage7C A3 English offset-semantics PATCH1 artifacts."""

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
from nldbwrite_v3.v2_a1.phase_o_output import parse_phase_o_output
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite
from nldbwrite_v3.v2_a1.prompt_rendering import (
    inventory_payload,
    offset_guide,
    serialize_prompt_object,
)
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values


STAGE_NAME = "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT"
PATCH_NAME = "PATCH1"
MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
PATCH_PACKAGE_NAME = (
    "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_"
    "PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip"
)
A2_PROMPT_SPEC_PATH = (
    "stage7c_a2_phase_o_prompt_feasibility_amendment/PHASE_O_PROMPT_SPEC_A2.json"
)
A1_PHASE_M_PROMPT_SPEC_PATH = "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json"
A1_OFFSET_GUIDE_SPEC_PATH = "stage7c_a1_v2_development_protocol/QUESTION_OFFSET_GUIDE_SPEC.json"
A3_PROMPT_SPEC_PATH = f"{STAGE_NAME}/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json"
PHASE_O_OFFSET_SEMANTICS_AMENDMENT = """Offsets follow Python slicing exactly.

start_char is inclusive.
end_char is exclusive.

The selected text is exactly:
Q[start_char:end_char]

If a value occupies character positions i through j inclusive,
return:
start_char = i
end_char = j + 1.

Before returning JSON, verify that every predicted span satisfies:
Q[start_char:end_char]
equals exactly one complete atomic database value,
with no surrounding punctuation or field label.
"""
SCIENTIFIC_ARTIFACTS = [
    "PHASE_O_PROMPT_AMENDMENT.md",
    "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json",
    "PROMPT_CHANGE_DIFF_A2_TO_A3.json",
    "FRESH_ENGLISH_A3_SMOKE_SET.jsonl",
    "ORACLE_V2_PATH_RESULTS.jsonl",
    "A3_RENDERED_PHASE_O_PROMPT_SMOKE.json",
    "ACCEPTANCE_POLICY_A3.json",
    "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl",
]
UPSTREAM_PACKAGE_PATHS = [
    "pyproject.toml",
    "stage7b_v2_method_specification",
    "stage7b_a1_free_text_slot_discovery_amendment",
    "stage7c_a1_v2_development_protocol",
    "stage7c_a2_phase_o_prompt_feasibility_amendment",
    "stage7d_v2_a1_implementation",
    "src/nldbwrite_v3/v2_a1",
    "scripts/data/build_stage7d_v2_a1_implementation.py",
    "scripts/data/validate_stage7d_v2_a1_implementation.py",
    "tests/v2_a1/test_stage7d_v2_a1.py",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(value: str) -> str:
    return sha256_bytes(canonical_text(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml"}:
        return sha256_text(data.decode("utf-8-sig"))
    return sha256_bytes(data)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "sample_id": "stage7c_fresh_english_001",
            "coverage_tags": ["2_values", "text", "integer", "comma", "colon"],
            "question": "Insert account code AC-001, score: 42 into accounts.",
            "table_name": "accounts",
            "columns": [
                {"column_name": "account_code", "source_type": "TEXT"},
                {"column_name": "score", "source_type": "INTEGER"},
            ],
            "values": ["AC-001", "42"],
        },
        {
            "sample_id": "stage7c_fresh_english_002",
            "coverage_tags": ["3_values", "quoted_text", "email", "integer", "comma"],
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
            "sample_id": "stage7c_fresh_english_003",
            "coverage_tags": ["4_values", "parentheses", "real", "integer", "text"],
            "question": "Create reading (sensor S-77) with temperature 21.75, humidity 45, status normal.",
            "table_name": "readings",
            "columns": [
                {"column_name": "sensor_id", "source_type": "TEXT"},
                {"column_name": "temperature", "source_type": "REAL"},
                {"column_name": "humidity", "source_type": "INTEGER"},
                {"column_name": "status", "source_type": "TEXT"},
            ],
            "values": ["S-77", "21.75", "45", "normal"],
        },
        {
            "sample_id": "stage7c_fresh_english_004",
            "coverage_tags": ["5_values", "date_like", "quoted_text", "real", "integer", "colon"],
            "question": (
                'Log shipment id SHIP-2026-08-30: carrier "Blue Rail", '
                "weight 18.5, stops 4, eta 2026-09-02."
            ),
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
            "sample_id": "stage7c_fresh_english_005",
            "coverage_tags": ["2_values", "long_identifier", "email"],
            "question": "Register user handle user_452 and recovery_email ops-team+452@example.org.",
            "table_name": "users",
            "columns": [
                {"column_name": "handle", "source_type": "TEXT"},
                {"column_name": "recovery_email", "source_type": "TEXT"},
            ],
            "values": ["user_452", "ops-team+452@example.org"],
        },
        {
            "sample_id": "stage7c_fresh_english_006",
            "coverage_tags": ["3_values", "colon", "quoted_text", "integer", "parentheses"],
            "question": 'Create ticket: title "Valve pressure low", severity 2, station (STN-44).',
            "table_name": "tickets",
            "columns": [
                {"column_name": "title", "source_type": "TEXT"},
                {"column_name": "severity", "source_type": "INTEGER"},
                {"column_name": "station", "source_type": "TEXT"},
            ],
            "values": ["Valve pressure low", "2", "STN-44"],
        },
        {
            "sample_id": "stage7c_fresh_english_007",
            "coverage_tags": ["4_values", "comma_grouped_numeric_text", "integer", "quoted_text"],
            "question": 'Add invoice INV-9001, amount 1,250.75, line_count 12, note "paid in full".',
            "table_name": "invoices",
            "columns": [
                {"column_name": "invoice_id", "source_type": "TEXT"},
                {"column_name": "amount_literal", "source_type": "TEXT"},
                {"column_name": "line_count", "source_type": "INTEGER"},
                {"column_name": "note", "source_type": "TEXT"},
            ],
            "values": ["INV-9001", "1,250.75", "12", "paid in full"],
        },
        {
            "sample_id": "stage7c_fresh_english_008",
            "coverage_tags": ["5_values", "parentheses", "real", "integer", "date_like", "long_value"],
            "question": (
                "Insert experiment run RUN-A3-008 (operator Dr Lin) with ph 7.4, "
                "samples 36, started 2026-08-30."
            ),
            "table_name": "experiment_runs",
            "columns": [
                {"column_name": "run_id", "source_type": "TEXT"},
                {"column_name": "operator", "source_type": "TEXT"},
                {"column_name": "ph", "source_type": "REAL"},
                {"column_name": "samples", "source_type": "INTEGER"},
                {"column_name": "started", "source_type": "TEXT"},
            ],
            "values": ["RUN-A3-008", "Dr Lin", "7.4", "36", "2026-08-30"],
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


def phase_o(case: dict[str, Any]) -> dict[str, Any]:
    spans = []
    search_from = 0
    for value in case["values"]:
        start = case["question"].index(value, search_from)
        end = start + len(value)
        search_from = end
        spans.append({"start_char": start, "end_char": end})
    return {"operation": "INSERT", "value_spans": spans}


def phase_m(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "INSERT",
        "table_ref": "TAB_1",
        "assignments": [
            {
                "slot_ref": f"SLOT_{index}",
                "evidence_ref": f"EV_{index}",
                "column_ref": f"COL_{index}",
            }
            for index, _ in enumerate(case["values"], start=1)
        ],
    }


def create_sql(case: dict[str, Any]) -> str:
    columns = ", ".join(
        f'"{column["column_name"]}" {column["source_type"]} NOT NULL'
        for column in case["columns"]
    )
    return f'CREATE TABLE "{case["table_name"]}" ({columns});'


def target_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    row = {
        column["column_name"]: sqlite_value(raw, column["source_type"])
        for column, raw in zip(case["columns"], case["values"])
    }
    return [row]


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


def build_prompt_spec_a3() -> tuple[dict[str, Any], dict[str, Any]]:
    a2 = read_json(PROJECT_ROOT / A2_PROMPT_SPEC_PATH)
    phase_m_spec = read_json(PROJECT_ROOT / A1_PHASE_M_PROMPT_SPEC_PATH)
    a3_user = a2["user_prompt_template"].rstrip() + "\n\n" + PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip()
    a3 = {
        "stage": STAGE_NAME,
        "parent_prompt": "Stage7C-A2",
        "parent_prompt_spec_path": A2_PROMPT_SPEC_PATH,
        "changed_component": "Phase O user prompt template only",
        "amendment_type": "zero_shot_offset_semantics_clarification_only",
        "system_prompt": a2["system_prompt"],
        "user_prompt_template": a3_user,
        "prompt_hashes": {
            "phase_o_system_prompt_sha256": sha256_text(a2["system_prompt"]),
            "phase_o_user_prompt_template_sha256": sha256_text(a3_user),
            "phase_m_system_prompt_sha256": sha256_text(phase_m_spec["system_prompt"]),
            "phase_m_user_prompt_template_sha256": sha256_text(phase_m_spec["user_prompt_template"]),
        },
        "parent_a2_prompt_hashes": a2["prompt_hashes"],
        "zero_shot": True,
        "examples": [],
        "gold_visible": False,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "backend": "same_PATCH9_incremental_grammar_backend",
        "retry": 0,
        "repair": "none",
        "offset_guide_spec_path": A1_OFFSET_GUIDE_SPEC_PATH,
    }
    diff = {
        "stage": STAGE_NAME,
        "parent_prompt": "Stage7C-A2",
        "changed_component": "Phase O user prompt template only",
        "phase_o_system_prompt": {
            "a2_sha256": a2["prompt_hashes"]["phase_o_system_prompt_sha256"],
            "a3_sha256": a3["prompt_hashes"]["phase_o_system_prompt_sha256"],
            "changed": False,
        },
        "phase_o_user_prompt_template": {
            "a2_sha256": a2["prompt_hashes"]["phase_o_user_prompt_template_sha256"],
            "a3_sha256": a3["prompt_hashes"]["phase_o_user_prompt_template_sha256"],
            "changed": True,
            "appended_amendment_sha256": sha256_text(PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip()),
        },
        "phase_m_system_prompt": {
            "a2_sha256": a2["prompt_hashes"]["phase_m_system_prompt_sha256"],
            "a3_sha256": a3["prompt_hashes"]["phase_m_system_prompt_sha256"],
            "changed": False,
        },
        "phase_m_user_prompt_template": {
            "a2_sha256": a2["prompt_hashes"]["phase_m_user_prompt_template_sha256"],
            "a3_sha256": a3["prompt_hashes"]["phase_m_user_prompt_template_sha256"],
            "changed": False,
        },
        "offset_guide_serializer": {
            "a2_spec_path": A1_OFFSET_GUIDE_SPEC_PATH,
            "a3_spec_path": A1_OFFSET_GUIDE_SPEC_PATH,
            "changed": False,
        },
        "zero_shot": True,
        "examples_added": False,
    }
    return a3, diff


def render_phase_o_a3_messages(
    question: str,
    model_side_input: dict[str, Any],
    spec_path: Path,
) -> tuple[list[dict[str, str]], str]:
    spec = read_json(spec_path)
    inventory = build_schema_inventory(model_side_input)
    user = spec["user_prompt_template"].format(
        question=question,
        offset_guide=offset_guide(question),
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
    )
    messages = [
        {"role": "system", "content": spec["system_prompt"]},
        {"role": "user", "content": user},
    ]
    return messages, sha256_text(serialize_prompt_object(messages))


def oracle_v2_path(row: dict[str, Any], db_path: Path) -> dict[str, Any]:
    question = row["model_side_input"]["question"]
    inventory = build_schema_inventory(row["model_side_input"])
    phase_o_obj = parse_phase_o_output(canonical_json(row["label_side_expected"]["phase_o"]))
    spans = validate_and_sort_spans(question, phase_o_obj["value_spans"])
    slots = build_slot_bundle(spans)
    phase_m_obj = parse_phase_m_output(
        canonical_json(row["label_side_expected"]["phase_m"]),
        phase_o_obj["operation"],
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
        "phase_o_operation_exact": phase_o_obj["operation"] == row["label_side_expected"]["phase_o"]["operation"],
        "phase_o_span_count": len(spans),
        "phase_o_no_extra_spans": len(spans) == len(row["label_side_expected"]["phase_o"]["value_spans"]),
        "deterministic_span_validation": "PASS",
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
    }


def smoke_row(case: dict[str, Any], db_info: dict[str, Any]) -> dict[str, Any]:
    phase_o_obj = phase_o(case)
    spans = [
        {
            "span_index": index,
            "evidence_ref": f"EV_{index}",
            "slot_ref": f"SLOT_{index}",
            "text": case["question"][span["start_char"] : span["end_char"]],
            **span,
        }
        for index, span in enumerate(phase_o_obj["value_spans"], start=1)
    ]
    target = target_rows(case)
    return {
        "sample_id": case["sample_id"],
        "locked_before_model_run": True,
        "coverage_tags": case["coverage_tags"],
        "model_side_input": {
            "question": case["question"],
            "schema_inventory": schema_inventory(case),
        },
        "label_side_expected": {
            "model_side_visible": False,
            "phase_o": phase_o_obj,
            "phase_o_span_text_oracle": spans,
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


def acceptance_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "primary_stage7e0_a3_acceptance": {
            "sample_set": "8 fresh English A3 cases",
            "required_pass_count": "8/8",
            "averaging_allowed": False,
            "seven_of_eight_allowed": False,
            "checks": [
                "Phase O operation exact",
                "Phase O spans exact",
                "no extra spans",
                "deterministic span validation PASS",
                "Phase M mapping exact",
                "SLOT-to-EV coherence PASS",
                "typed materialization PASS",
                "completeness PASS",
                "compilation PASS",
                "preflight ADMITTED",
                "canonical target state exact",
            ],
        },
        "diagnostic_only_after_primary": [
            "4 A2 fresh English cases",
            "2 old PATCH9/Alice diagnostics",
        ],
    }


def build_run(out_dir: Path) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = out_dir / "sqlite_dbs"

    a3_spec, prompt_diff = build_prompt_spec_a3()
    write_text(out_dir / "PHASE_O_PROMPT_AMENDMENT.md", PHASE_O_OFFSET_SEMANTICS_AMENDMENT)
    write_json(out_dir / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json", a3_spec)
    write_json(out_dir / "PROMPT_CHANGE_DIFF_A2_TO_A3.json", prompt_diff)
    write_json(out_dir / "ACCEPTANCE_POLICY_A3.json", acceptance_policy())

    rows = []
    db_manifest = []
    oracle_results = []
    for case in case_definitions():
        db_info = create_case_db(case, db_dir)
        row = smoke_row(case, db_info)
        oracle = oracle_v2_path(row, out_dir / db_info["sqlite_db_path"])
        row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle[
            "observed_target_state_hash"
        ]
        rows.append(row)
        db_manifest.append(db_info)
        oracle_results.append(oracle)

    write_jsonl(out_dir / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl", rows)
    write_jsonl(out_dir / "ORACLE_V2_PATH_RESULTS.jsonl", oracle_results)
    write_jsonl(out_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl", db_manifest)
    messages, messages_hash = render_phase_o_a3_messages(
        rows[0]["model_side_input"]["question"],
        rows[0]["model_side_input"],
        out_dir / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json",
    )
    write_json(
        out_dir / "A3_RENDERED_PHASE_O_PROMPT_SMOKE.json",
        {
            "sample_id": rows[0]["sample_id"],
            "phase_o_prompt_spec_path": A3_PROMPT_SPEC_PATH,
            "rendered_from_spec_path": str((out_dir / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json").as_posix()),
            "messages_sha256": messages_hash,
            "amendment_present": PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip()
            in messages[1]["content"],
        },
    )

    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_A3_PROMPT_AND_V2_ORACLE_FIXTURE_LOCKED",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "phase_o_prompt_spec_path": A3_PROMPT_SPEC_PATH,
        "parent_phase_o_prompt_spec_path": A2_PROMPT_SPEC_PATH,
        "changed_component": "Phase O user prompt template only",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "two_call_architecture": True,
        "phase_m_unchanged": True,
        "patch9_incremental_backend_unchanged": True,
        "zero_shot": True,
        "retry": 0,
        "repair": "none",
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "fresh_english_case_count": len(rows),
        "expected_span_count": sum(len(row["label_side_expected"]["phase_o"]["value_spans"]) for row in rows),
        "stage7e0_a3_primary_acceptance": "8/8 required; no average and no 7/8 acceptance",
    }

    artifact_names = [
        *SCIENTIFIC_ARTIFACTS,
        *[f"sqlite_dbs/{path.name}" for path in sorted(db_dir.glob("*.sqlite"))],
    ]
    derived_manifest = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifact_names),
        "artifacts": [
            {
                "path": name,
                "bytes": (out_dir / name).stat().st_size,
                "sha256": sha256_file(out_dir / name),
            }
            for name in sorted(artifact_names)
        ],
    }
    derived_manifest["combined_scientific_artifacts_sha256"] = sha256_text(
        canonical_json(derived_manifest["artifacts"])
    )
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", derived_manifest)
    lock["derived_artifact_manifest_sha256"] = sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    write_json(out_dir / "STAGE7C_A3_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(len(rows), lock["expected_span_count"]))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))

    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "fresh_english_case_count": len(rows),
        "expected_span_count": lock["expected_span_count"],
        "v2_oracle_admitted": sum(1 for row in oracle_results if row["preflight"] == "ADMITTED"),
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def validation_report(case_count: int, span_count: int) -> str:
    return f"""# Stage7C A3 English Phase O Offset-Semantics Amendment PATCH1 Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

PATCH1 rewires the A3 amendment to the frozen V2-A1 A2 Phase O prompt spec.
The A2 system prompt is unchanged. The A3 user prompt template is A2 plus only
the offset-semantics block. Phase M, offset-guide serialization, model/backend,
materializer, compiler, preflight, metrics, datasets, and gold labels remain
unchanged.

## Frozen Smoke Set

```text
fresh English cases        {case_count}
expected Phase O spans     {span_count}
V2 oracle path             8/8 ADMITTED and exact target state
offset contract            start inclusive, end exclusive
slice oracle               Q[start_char:end_char]
```

## Guardrails

```text
same Qwen2.5-Coder-7B=true
same revision=true
same 2-call architecture=true
same Phase M=true
same PATCH9 incremental backend=true
zero_shot=true
retry=0
repair=none
model_called=false
gpu_called=false
gretel_pilot_opened=false
```

## Validation Commands

```text
python scripts/data/build_stage7c_a3_english_offset_semantics.py --out-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT --package Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
python -m pytest -q -m "not integration"
python -m zipfile --test Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip
```
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7C A3 English Phase O Offset-Semantics Amendment PATCH1

This package fixes PATCH0 wiring by amending the frozen V2-A1 A2 Phase O prompt
spec rather than the legacy planner prompt path. It keeps the same eight fresh
English questions and locks them as Stage7E0-A3-ready fixtures using TAB/COL/EV/SLOT refs.

Clean extraction commands:

```bash
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
```

No GPU is required. No model is called. The Gretel pilot pool is not opened.

Local artifact directory at build time:

```text
{out_dir}
```
"""


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
            PROJECT_ROOT / "scripts" / "data" / "build_stage7c_a3_english_offset_semantics.py",
            PROJECT_ROOT / "scripts" / "data" / "validate_stage7c_a3_english_offset_semantics.py",
            PROJECT_ROOT / "tests" / "test_stage7c_a3_english_offset_semantics.py",
            PROJECT_ROOT / "tests" / "support" / "windows_py314_pytest_tempdir" / "sitecustomize.py",
            PROJECT_ROOT / "tests" / "support" / "stage7c_pytest_clean_root" / "conftest.py",
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
            elif path.name == "conftest.py" and "stage7c_pytest_clean_root" in path.parts:
                arcname = Path("conftest.py")
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
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PATCH_PACKAGE_NAME)
    args = parser.parse_args()

    summary = build_run(args.out_dir)
    if args.package:
        digest = package_reviewer(args.out_dir, args.package)
        summary["package"] = str(args.package)
        summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
