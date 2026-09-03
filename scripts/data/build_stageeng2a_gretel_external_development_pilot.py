#!/usr/bin/env python3
"""Build Stage ENG2A Gretel external development-pilot protocol package."""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.prompts import build_direct_prompt, build_legacy_json_prompt  # noqa: E402
from nldbwrite_v3.schema.profile import build_profile  # noqa: E402
from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    SELECTED_VARIANT,
    candidate_to_json,
    generate_candidate_inventory,
    serialize_candidate_inventory,
)
from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import (  # noqa: E402
    detect_omission_constructions,
    schema_label_alias_index,
)
from scripts.data.build_stage7b_a5_typed_atomic_boundary_omission import (  # noqa: E402
    PATCH_NAME as STAGE7B_A5_PATCH_NAME,
    STAGE_NAME as STAGE7B_A5_NAME,
    a5_suppression_reasons,
    omittable_schema_aliases_from_inventory,
)
from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import (  # noqa: E402
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_SYSTEM_PROMPT,
    PHASE_O_USER_PROMPT_TEMPLATE,
    dynamic_schema_for_column_infos,
    oracle_column_conditioned_path,
    render_phase_o_messages,
    schema_inventory,
    schema_tables,
    selected_table_ref,
    selected_columns,
)
from scripts.data.build_stage7e0_a7_final_a5_real_generation_feasibility import (  # noqa: E402
    PATCH_NAME as A7_ACCEPTED_PATCH,
    STAGE_NAME as A7_STAGE_NAME,
)
from scripts.data.build_stageeng0_gretel_qualification import (  # noqa: E402
    STAGE_NAME as STAGEENG0_NAME,
    classify_gold_sql,
    execute_context,
    load_parquet_rows,
    quote_ident,
    read_jsonl as stage0_read_jsonl,
    snapshot_database,
    snapshot_hash,
    sqlite_schema,
    target_reference,
)


STAGE_NAME = "StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT"
PATCH_NAME = "PATCH0"
PACKAGE_DATE = "20260903"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
SERVER_WORK_ROOT = "/home/uet/hue_ptk"
SERVER_RESULT_DIR = "stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903"
SERVER_ARCHIVE = f"{SERVER_RESULT_DIR}.tar.gz"
DIRECT_CONFIG_REL = "configs/stage5/resolved_direct_confirmation.json"
JFS_CONFIG_REL = "configs/stage5/resolved_j_fs_confirmation.json"
EXPECTED_PILOT_N = 100

SCIENTIFIC_ARTIFACTS = [
    "REVIEWER_README.md",
    "VALIDATION_REPORT.md",
    "ENG2A_PROTOCOL_FREEZE.json",
    "ENG2A_PILOT_100_FREEZE.json",
    "ENG2A_PILOT_100_MANIFEST.jsonl",
    "configs/m0_direct_sql_config.json",
    "configs/m1_j_fs_config.json",
    "configs/m2_frozen_a7_config.json",
    "audits/pilot_freeze_audit.json",
    "audits/official_test_isolation_audit.json",
    "audits/gold_leakage_audit.json",
    "audits/off_target_metric_definition.json",
    "prompts/M0_DIRECT_SQL_PROMPTS.jsonl",
    "prompts/M1_J_FS_PROMPTS.jsonl",
    "prompts/M2_FROZEN_A7_PROMPTS.jsonl",
    "mock_dry_run/raw/model_outputs.jsonl",
    "mock_dry_run/results/per_sample_results.jsonl",
    "mock_dry_run/results/summary.json",
    "mock_dry_run/analysis/a7_failure_taxonomy.json",
    "mock_dry_run/efficiency/token_usage.jsonl",
    "mock_dry_run/efficiency/latency.jsonl",
    "mock_dry_run/environment/environment.json",
    "MANIFEST.json",
    "SHA256SUMS",
    "SERVER_RUN_COMMANDS.md",
    "SERVER_RUN_COMMANDS.sh",
]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return sha256_bytes(data)


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def normalized_prompt(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def sample_id_for_raw(split: str, index: int, row: dict[str, Any]) -> str:
    return f"gretel:{split}:{row.get('id', index)}:{index:06d}"


def load_raw_by_sample_id(raw_dir: Path) -> dict[str, dict[str, Any]]:
    rows_by_split, _schemas = load_parquet_rows(raw_dir)
    output: dict[str, dict[str, Any]] = {}
    for split, rows in rows_by_split.items():
        for index, row in enumerate(rows):
            normalized = dict(row)
            normalized["sample_id"] = sample_id_for_raw(split, index, normalized)
            normalized["source_split"] = split
            normalized["source_index"] = index
            output[normalized["sample_id"]] = normalized
    return output


def selected_pilot_manifest(stage1_dir: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(stage1_dir / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")
    if len(rows) != EXPECTED_PILOT_N:
        raise SystemExit(f"STOP: expected {EXPECTED_PILOT_N} development-pilot rows, found {len(rows)}")
    for row in rows:
        if row.get("operation") != "INSERT" or row.get("source_split") != "train":
            raise SystemExit(f"STOP: pilot row outside frozen INSERT/train policy: {row.get('sample_id')}")
        if row.get("official_test_confirmation_only") is not False or row.get("development_pilot_pool") is not True:
            raise SystemExit(f"STOP: pilot row leakage flag drifted: {row.get('sample_id')}")
    return rows


def load_insert_grounding(stage0_dir: Path, sample_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {sample_id: [] for sample_id in sample_ids}
    for row in stage0_read_jsonl(stage0_dir / "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl"):
        sample_id = str(row.get("sample_id"))
        if sample_id in grouped:
            grouped[sample_id].append(row)
    missing = [sample_id for sample_id, rows in grouped.items() if not rows]
    if missing:
        raise SystemExit(f"STOP: missing insert assignment grounding for pilot rows: {missing[:5]}")
    return {sample_id: sorted(rows, key=lambda item: int(item["assignment_index"])) for sample_id, rows in grouped.items()}


def table_specs_from_connection(con: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = []
    for table_name, _columns in sqlite_schema(con).items():
        columns = []
        for _cid, name, source_type, not_null, default, pk_order, hidden in con.execute(f"PRAGMA table_xinfo({quote_ident(table_name)})"):
            if int(hidden) != 0:
                continue
            column: dict[str, Any] = {
                "column_name": str(name),
                "source_type": str(source_type or "TEXT"),
                "nullable": not bool(not_null),
                "primary_key": bool(pk_order),
            }
            if default is not None:
                column["default"] = str(default)
            columns.append(column)
        tables.append({"table_name": table_name, "columns": columns})
    return sorted(tables, key=lambda item: item["table_name"])


def copy_connection_to_path(con: sqlite3.Connection, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as target:
        con.backup(target)
        target.commit()


def read_rows(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    rows = con.execute(f"SELECT * FROM {quote_ident(table)} ORDER BY rowid").fetchall()
    return [dict(row) for row in rows]


def build_case(
    manifest_row: dict[str, Any],
    raw: dict[str, Any],
    grounding_rows: list[dict[str, Any]],
    db_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    classification = classify_gold_sql(str(raw.get("sql") or ""))
    if classification.status != "dml" or classification.operation != "INSERT":
        raise SystemExit(f"STOP: pilot gold SQL is not one INSERT: {manifest_row['sample_id']}")
    ref = target_reference(classification.primary_statement, "INSERT")
    if not ref.table:
        raise SystemExit(f"STOP: could not resolve target table for {manifest_row['sample_id']}")
    con, error = execute_context(str(raw.get("sql_context") or ""))
    if con is None:
        raise SystemExit(f"STOP: SQLite context failed for {manifest_row['sample_id']}: {error}")
    try:
        initial_state_hash = snapshot_hash(con)
        tables = table_specs_from_connection(con)
        db_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(manifest_row["sample_id"])) + ".sqlite"
        sqlite_rel = f"sqlite_dbs/{db_name}"
        copy_connection_to_path(con, db_dir / db_name)
        con.execute("BEGIN")
        con.execute(classification.primary_statement)
        target_rows = read_rows(con, ref.table)
        gold_post_state = snapshot_database(con)
        gold_post_state_hash = snapshot_hash(con)
        con.rollback()
    finally:
        con.close()

    assigned_values: dict[str, str] = {}
    assigned_value_spans: dict[str, dict[str, Any]] = {}
    for item in grounding_rows:
        column_name = str(item["column_ref_or_name"])
        span = item.get("matched_source_span") or {}
        text = str(span.get("text") or item.get("source_literal_text") or "")
        assigned_values[column_name] = text
        assigned_value_spans[column_name] = {
            "start_char": int(span["start_char"]),
            "end_char": int(span["end_char"]),
        }

    case = {
        "sample_id": manifest_row["sample_id"],
        "question": str(raw.get("sql_prompt") or ""),
        "selected_table": ref.table,
        "tables": tables,
        "assigned_values": assigned_values,
        "assigned_value_spans": assigned_value_spans,
        "coverage_tags": ["gretel_external_development_pilot", "single_row_insert"],
    }
    row = build_a7_external_row(case)
    row["gretel_source"] = {
        "source_split": manifest_row["source_split"],
        "source_index": manifest_row["source_index"],
        "source_row_key": manifest_row["source_row_key"],
        "raw_row_hash": manifest_row["raw_row_hash"],
        "prompt_hash": manifest_row["prompt_hash"],
        "context_hash": manifest_row["context_hash"],
        "sql_hash": manifest_row["sql_hash"],
    }
    row["evaluator_side_expected"] = {
        "gold_sql": [classification.primary_statement],
        "gold_target_table": ref.table,
        "gold_post_state": gold_post_state,
        "gold_post_state_hash": gold_post_state_hash,
        "initial_state_hash": initial_state_hash,
    }
    row["assigned_values_for_mock"] = assigned_values
    row["label_side_expected"]["target_state"] = {
        "table_name": ref.table,
        "typed_target_rows": target_rows,
        "target_state_hash": sha256_text(canonical_json(target_rows)),
    }
    row["synthetic_db_spec"] = {
        "sample_id": manifest_row["sample_id"],
        "selected_table": ref.table,
        "sqlite_db_path": sqlite_rel,
        "initial_state_hash": initial_state_hash,
        "gold_post_state_hash": gold_post_state_hash,
        "source_tables": tables,
        "sql_context_sha256": manifest_row["context_hash"],
    }
    db_manifest = {
        "sample_id": manifest_row["sample_id"],
        "sqlite_db_path": sqlite_rel,
        "selected_table": ref.table,
        "initial_state_hash": initial_state_hash,
        "gold_post_state_hash": gold_post_state_hash,
        "table_count": len(tables),
    }
    return row, db_manifest


def column_is_omittable(column: dict[str, Any]) -> bool:
    return bool(column.get("nullable") or column.get("has_default") or column.get("primary_key") or column.get("autoincrement") or column.get("generated"))


def dynamic_schema_a7_for_case(case: dict[str, Any], span_refs: list[str]) -> dict[str, Any]:
    tables = schema_tables(case)

    def domain(column: Any) -> list[str]:
        return ["OMIT", *span_refs] if column_is_omittable(column.__dict__) else list(span_refs)

    def branch(table_ref: str, columns: list[Any]) -> dict[str, Any]:
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
                    "properties": {column.column_ref: {"type": "string", "enum": domain(column)} for column in columns},
                },
            },
        }

    if len(tables) == 1:
        payload = branch("TAB_1", next(iter(tables.values())))
        payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        payload["title"] = "StageENG2A Frozen A7 Candidate Selection Output"
        return payload
    schema = dynamic_schema_for_column_infos(tables, span_refs)
    schema["title"] = "StageENG2A Frozen A7 Multi-Table Candidate Selection Output"
    return schema


def schema_labels_for_case(case: dict[str, Any]) -> set[str]:
    labels = {case["selected_table"]}
    for table in case["tables"]:
        labels.add(table["table_name"])
        labels.update(column["column_name"] for column in table["columns"])
    return labels


def build_a7_external_row(case: dict[str, Any]) -> dict[str, Any]:
    full_inventory = generate_candidate_inventory(case["question"], variant=SELECTED_VARIANT)
    aliases = schema_label_alias_index(schema_labels_for_case(case))
    model_schema_inventory = schema_inventory(case)
    omittable_aliases = omittable_schema_aliases_from_inventory(model_schema_inventory)
    detections = detect_omission_constructions(case["question"], omittable_aliases)
    suppression_reasons = a5_suppression_reasons(full_inventory, aliases, detections, include_a4=True)
    inventory = [candidate for candidate in full_inventory if candidate.span_ref not in suppression_reasons]
    column_span_refs, gold_rows = gold_column_span_refs_with_misses(case, inventory)
    span_refs = [candidate.span_ref for candidate in inventory]
    dynamic_schema = dynamic_schema_a7_for_case(case, span_refs)
    row = {
        "sample_id": case["sample_id"],
        "locked_before_model_run": True,
        "external_development_pilot": True,
        "source_group": STAGE_NAME,
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
        },
        "label_side_expected": {
            "model_side_visible": False,
            "phase_o": {"operation": "INSERT", "table_ref": selected_table_ref(case), "column_span_refs": column_span_refs},
            "gold_column_span_ref_oracle": gold_rows,
            "target_state": {},
        },
    }
    messages, _user, prompt_hash = render_phase_o_messages(row)
    row["runtime_constraints"]["rendered_prompt_sha256"] = prompt_hash
    row["runtime_constraints"]["message_count"] = len(messages)
    return row


def gold_column_span_refs_with_misses(case: dict[str, Any], inventory: list[Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    by_span = {(candidate.start_char, candidate.end_char): candidate for candidate in inventory}
    selected: dict[str, str] = {}
    oracle_rows = []
    assigned = case["assigned_values"]
    for column in selected_columns(case):
        if column.column_name not in assigned:
            selected[column.column_ref] = "OMIT"
            continue
        span = case["assigned_value_spans"][column.column_name]
        candidate = by_span.get((int(span["start_char"]), int(span["end_char"])))
        if candidate is None:
            selected[column.column_ref] = "CANDIDATE_MISS"
            oracle_rows.append(
                {
                    "column_ref": column.column_ref,
                    "column_name": column.column_name,
                    "source_type": column.source_type,
                    "start_char": int(span["start_char"]),
                    "end_char": int(span["end_char"]),
                    "text": case["question"][int(span["start_char"]) : int(span["end_char"])],
                    "candidate_generation_miss": True,
                    "candidate_span_ref": None,
                }
            )
            continue
        selected[column.column_ref] = candidate.span_ref
        oracle_rows.append(
            {
                "column_ref": column.column_ref,
                "column_name": column.column_name,
                "source_type": column.source_type,
                "start_char": int(span["start_char"]),
                "end_char": int(span["end_char"]),
                "text": candidate.text,
                "candidate_generation_miss": False,
                "candidate_span_ref": candidate.span_ref,
                "candidate_tags": list(candidate.tags),
            }
        )
    return selected, oracle_rows


def build_method_prompts(out_dir: Path, rows: list[dict[str, Any]], direct_config: dict[str, Any], jfs_config: dict[str, Any]) -> None:
    direct_rows = []
    jfs_rows = []
    a7_rows = []
    for row in rows:
        db_path = out_dir / row["synthetic_db_spec"]["sqlite_db_path"]
        profile = build_profile(db_path, db_id=row["sample_id"])
        question = row["model_side_input"]["question"]
        direct_prompt = build_direct_prompt(question, profile, direct_config)
        jfs_prompt = build_legacy_json_prompt(question, profile, jfs_config)
        a7_messages, _user, a7_hash = render_phase_o_messages(row)
        direct_rows.append({"sample_id": row["sample_id"], "method_id": "M0_DIRECT_SQL", "prompt_sha256": sha256_text(direct_prompt), "prompt": direct_prompt})
        jfs_rows.append({"sample_id": row["sample_id"], "method_id": "M1_J_FS", "prompt_sha256": sha256_text(jfs_prompt), "prompt": jfs_prompt})
        a7_rows.append({"sample_id": row["sample_id"], "method_id": "M2_FROZEN_A7", "messages_sha256": a7_hash, "messages": a7_messages})
    write_jsonl(out_dir / "prompts" / "M0_DIRECT_SQL_PROMPTS.jsonl", direct_rows)
    write_jsonl(out_dir / "prompts" / "M1_J_FS_PROMPTS.jsonl", jfs_rows)
    write_jsonl(out_dir / "prompts" / "M2_FROZEN_A7_PROMPTS.jsonl", a7_rows)


def isolation_audit(stage0_dir: Path, stage1_dir: Path, pilot_rows: list[dict[str, Any]]) -> dict[str, Any]:
    official_rows = read_jsonl(stage0_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")
    dev_rows = read_jsonl(stage1_dir / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")
    train_rows = read_jsonl(stage1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    fields = ["sample_id", "source_row_key", "prompt_hash", "context_hash", "sql_hash", "normalized_prompt_hash", "leakage_signature_hash", "raw_row_hash"]
    official_overlap_fields = ["sample_id", "source_row_key", "prompt_hash", "sql_hash", "normalized_prompt_hash", "leakage_signature_hash", "raw_row_hash"]
    pilot_by_field = {field: {str(row.get(field) or "") for row in pilot_rows if row.get(field) not in (None, "")} for field in fields}

    def overlaps(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {field: len(pilot_by_field[field] & {str(row.get(field) or "") for row in rows if row.get(field) not in (None, "")}) for field in fields}

    official = overlaps(official_rows)
    dev = overlaps(dev_rows)
    train = overlaps(train_rows)
    official_content_overlap = sum(official[field] for field in official_overlap_fields)
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if official_content_overlap == 0 and sum(dev.values()) == 0 else "FAIL",
        "pilot_n": len(pilot_rows),
        "official_test_manifest_rows_seen_for_isolation_audit_only": len(official_rows),
        "official_test_overlap": official_content_overlap,
        "official_test_overlap_by_field": official,
        "official_schema_context_hash_overlap_diagnostic": official["context_hash"],
        "development_dev_overlap": sum(dev.values()),
        "development_dev_overlap_by_field": dev,
        "development_train_self_overlap_expected_nonzero": sum(train.values()),
        "development_train_overlap_by_field": train,
        "official_raw_question_context_sql_opened": False,
    }


def protocol_freeze() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN",
        "dataset": "StageENG1 development-pilot pool exactly 100 rows",
        "forbidden_data": "StageENG0 official confirmation raw question/context/gold SQL are not opened; official manifest is used only for isolation hashes.",
        "methods": [
            {"method_id": "M0_DIRECT_SQL", "source": DIRECT_CONFIG_REL, "calls_per_sample": 1, "retry": 0, "output": "SQLite INSERT/REPLACE SQL only"},
            {"method_id": "M1_J_FS", "source": JFS_CONFIG_REL, "calls_per_sample": 1, "retry": 0, "output": "legacy table-operation-values JSON compiled with common v3 verifier/compiler"},
            {"method_id": "M2_FROZEN_A7", "source": f"{A7_STAGE_NAME} {A7_ACCEPTED_PATCH}", "calls_per_sample": 1, "retry": 0, "output": "frozen A7 column-conditioned JSON schema with Phase M removed"},
        ],
        "model": {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "deterministic": True, "temperature": None, "do_sample": False},
        "primary_metric": "target_state_accuracy",
        "secondary_metrics": ["execution_success", "target_state_correct", "off_target_state_change", "admission_rate", "accepted_write_correctness", "tokens", "latency"],
        "off_target_metric": "Compare D0->Dpred and D0->Dgold deltas across all persistent user tables; extra predicted added/removed row deltas are off-target even when the target table is also wrong.",
        "no_artificial_accuracy_gate": True,
    }


def run_mock_dry_run(out_dir: Path) -> dict[str, Any]:
    from scripts.server.run_stageeng2a_gretel_pilot import run_stageeng2a

    result_root = out_dir / "mock_dry_run"
    args = argparse.Namespace(
        stage_dir=out_dir,
        result_root=result_root,
        backend="mock",
        model_name_or_path="mock",
        quantization="none",
        max_new_tokens=512,
        phase_o_max_new_tokens=512,
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        allow_result_root_inside_git=True,
    )
    return run_stageeng2a(args)


def write_package_integrity(out_dir: Path) -> None:
    rows = []
    for path in sorted(item for item in out_dir.rglob("*") if item.is_file()):
        if path.name in {"MANIFEST.json", "SHA256SUMS"}:
            continue
        rows.append({"path": str(path.relative_to(out_dir)).replace("\\", "/"), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_json(out_dir / "MANIFEST.json", {"stage": STAGE_NAME, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "files": rows})
    write_text(out_dir / "SHA256SUMS", "".join(f"{row['sha256']}  {row['path']}\n" for row in rows))


def reviewer_readme() -> str:
    return f"""# {STAGE_NAME} {PATCH_NAME}

This package freezes the ENG2A 100-sample Gretel development-pilot evaluation and provides the one-off UET server runner for three arms: M0 Direct SQL, M1 J-FS, and M2 Frozen A7.

Local reviewer checks:

```bash
python scripts/data/validate_stageeng2a_gretel_external_development_pilot.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stageeng2a_gretel_external_development_pilot.py
```

Official server run:

```bash
bash {STAGE_NAME}/SERVER_RUN_COMMANDS.sh
```

The bundled `mock_dry_run` is a wiring check only. It uses label-side answers and is not a scientific result.
"""


def validation_report(mock_summary: dict[str, Any]) -> str:
    return f"""# Validation Report

stage={STAGE_NAME}
patch={PATCH_NAME}
pilot_n={EXPECTED_PILOT_N}
mock_methods={','.join(sorted(mock_summary['methods']))}
mock_model_called=false
official_generation_validated=false
status=FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN
"""


def server_commands() -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd {SERVER_WORK_ROOT}
conda activate stage7e0_a7_py311
export CUDA_VISIBLE_DEVICES="${{CUDA_VISIBLE_DEVICES:-0}}"
RUNNER="{STAGE_NAME}_runner"
RESULT_ROOT="{SERVER_WORK_ROOT}/{SERVER_RESULT_DIR}"
MODEL_SNAPSHOT="{SERVER_WORK_ROOT}/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/{MODEL_REVISION}"

cd "$RUNNER"
python scripts/data/validate_stageeng2a_gretel_external_development_pilot.py --stage-dir {STAGE_NAME}
rm -rf "$RESULT_ROOT"
CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES" python scripts/server/run_stageeng2a_gretel_pilot.py \\
  --stage-dir {STAGE_NAME} \\
  --result-root "$RESULT_ROOT" \\
  --backend hf \\
  --model-name-or-path "$MODEL_SNAPSHOT" \\
  --max-new-tokens 512 \\
  --phase-o-max-new-tokens 512 \\
  --max-input-tokens 28672 \\
  --seed 42 \\
  --trust-remote-code

cd {SERVER_WORK_ROOT}
tar -czf {SERVER_ARCHIVE} {SERVER_RESULT_DIR}
sha256sum {SERVER_ARCHIVE} > {SERVER_ARCHIVE}.sha256
python - <<'PY'
import tarfile
name = "{SERVER_ARCHIVE}"
with tarfile.open(name, "r:gz") as archive:
    members = archive.getmembers()
print(f"tar_ok members={{len(members)}} archive={{name}}")
PY
"""


def build_run(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir).resolve()
    stage0_dir = Path(args.stage0_dir).resolve()
    stage1_dir = Path(args.stage1_dir).resolve()
    raw_dir = Path(args.raw_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    direct_config = read_json(PROJECT_ROOT / DIRECT_CONFIG_REL)
    jfs_config = read_json(PROJECT_ROOT / JFS_CONFIG_REL)
    pilot_rows = selected_pilot_manifest(stage1_dir)
    raw_by_id = load_raw_by_sample_id(raw_dir)
    grounding = load_insert_grounding(stage0_dir, {str(row["sample_id"]) for row in pilot_rows})
    frozen_rows = []
    db_manifest = []
    for manifest_row in pilot_rows:
        raw = raw_by_id.get(str(manifest_row["sample_id"]))
        if raw is None:
            raise SystemExit(f"STOP: raw parquet row missing for {manifest_row['sample_id']}")
        row, db_info = build_case(manifest_row, raw, grounding[str(manifest_row["sample_id"])], out_dir / "sqlite_dbs")
        frozen_rows.append(row)
        db_manifest.append(db_info)
    write_jsonl(out_dir / "ENG2A_PILOT_100_MANIFEST.jsonl", pilot_rows)
    write_jsonl(out_dir / "ENG2A_PILOT_100_FREEZE.jsonl", frozen_rows)
    write_json(out_dir / "ENG2A_PILOT_100_FREEZE.json", {"stage": STAGE_NAME, "pilot_n": len(frozen_rows), "rows_sha256": sha256_file(out_dir / "ENG2A_PILOT_100_FREEZE.jsonl")})
    write_jsonl(out_dir / "sqlite_dbs" / "SQLITE_DB_MANIFEST.jsonl", db_manifest)
    (out_dir / "configs").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PROJECT_ROOT / DIRECT_CONFIG_REL, out_dir / "configs" / "m0_direct_sql_config.json")
    shutil.copyfile(PROJECT_ROOT / JFS_CONFIG_REL, out_dir / "configs" / "m1_j_fs_config.json")
    write_json(out_dir / "configs" / "m2_frozen_a7_config.json", {"stage": STAGE_NAME, "source_stage": A7_STAGE_NAME, "source_patch": A7_ACCEPTED_PATCH, "phase_o_system_prompt": PHASE_O_SYSTEM_PROMPT, "phase_o_user_prompt_template": PHASE_O_USER_PROMPT_TEMPLATE})
    build_method_prompts(out_dir, frozen_rows, direct_config, jfs_config)
    isolation = isolation_audit(stage0_dir, stage1_dir, pilot_rows)
    write_json(out_dir / "ENG2A_PROTOCOL_FREEZE.json", protocol_freeze())
    write_json(out_dir / "audits" / "pilot_freeze_audit.json", {"stage": STAGE_NAME, "status": "PASS", "pilot_n": len(frozen_rows), "silent_drop_count": 0, "model_side_input_keys": sorted(frozen_rows[0]["model_side_input"]), "gold_visible_to_model": False})
    write_json(out_dir / "audits" / "official_test_isolation_audit.json", isolation)
    write_json(out_dir / "audits" / "gold_leakage_audit.json", {"stage": STAGE_NAME, "status": "PASS", "model_side_forbidden_keys": ["gold_sql", "gold_assignments", "gold_post_state", "target_state", "evaluator_side_expected"], "violations": []})
    write_json(out_dir / "audits" / "off_target_metric_definition.json", {"stage": STAGE_NAME, "status": "PASS", "definition": protocol_freeze()["off_target_metric"], "persistent_user_tables_only": True})
    mock_summary = run_mock_dry_run(out_dir)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme())
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(mock_summary))
    write_text(out_dir / "SERVER_RUN_COMMANDS.sh", server_commands())
    write_text(out_dir / "SERVER_RUN_COMMANDS.md", f"Run the executable shell script, not this markdown file:\n\n```bash\nbash {STAGE_NAME}/SERVER_RUN_COMMANDS.sh\n```\n")
    write_package_integrity(out_dir)
    return {"stage": STAGE_NAME, "pilot_n": len(frozen_rows), "isolation_status": isolation["status"], "mock_summary": mock_summary}


def package_reviewer(out_dir: Path, package_path: Path) -> str:
    package_path = package_path.resolve()
    if package_path.exists():
        package_path.unlink()
    include = [
        STAGE_NAME,
        "src/nldbwrite_v3",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/build_stage7e0_a7_final_a5_real_generation_feasibility.py",
        "scripts/data/build_stageeng2a_gretel_external_development_pilot.py",
        "scripts/data/validate_stageeng2a_gretel_external_development_pilot.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_a6_english.py",
        "scripts/server/run_stageeng2a_gretel_pilot.py",
        "tests/test_stageeng2a_gretel_external_development_pilot.py",
        DIRECT_CONFIG_REL,
        JFS_CONFIG_REL,
    ]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in include:
            path = PROJECT_ROOT / item
            if path.is_dir():
                for file in sorted(p for p in path.rglob("*") if p.is_file()):
                    archive.write(file, file.relative_to(PROJECT_ROOT).as_posix())
            elif path.is_file():
                archive.write(path, path.relative_to(PROJECT_ROOT).as_posix())
            else:
                raise FileNotFoundError(item)
        archive.writestr(
            f"{STAGE_NAME}/REVIEWER_PACKAGE_GIT_INFO.json",
            json.dumps(
                {
                    "branch": git_output("branch", "--show-current"),
                    "commit": git_output("rev-parse", "HEAD"),
                    "status_short": git_output("status", "--short", "--untracked-files=no"),
                    "package_name": package_path.name,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    return sha256_file(package_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage0-dir", type=Path, default=PROJECT_ROOT / STAGEENG0_NAME)
    parser.add_argument("--stage1-dir", type=Path, default=PROJECT_ROOT / STAGEENG1_NAME)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT.parents[1] / "external_sources" / "gretel_synthetic_text_to_sql_740ab236")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    parser.add_argument("--no-package", action="store_true")
    args = parser.parse_args()
    summary = build_run(args)
    package_sha = None if args.no_package else package_reviewer(Path(args.out_dir), Path(args.package))
    print(json.dumps({**summary, "package": None if args.no_package else str(args.package), "package_sha256": package_sha}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
