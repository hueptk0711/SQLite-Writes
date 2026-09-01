#!/usr/bin/env python3
"""Build Stage7B-A3 column-conditioned candidate selection artifacts.

This CPU-only design stage closes Stage7E0-A4 as a valid feasibility failure
and audits whether replacing a free-length ``span_refs`` set with per-column
``SPAN``/``OMIT`` decisions is representable on the frozen 728-sample
design-train scope. Gold labels are used only for oracle audit, never as
runtime constraints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    DATASET_ID,
    DATASET_REVISION,
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    EXPECTED_DEV_COUNT,
    EXPECTED_OFFICIAL_TEST_COUNT,
    EXPECTED_PILOT_COUNT,
    QWEN_TOKENIZER_REVISION,
    SELECTED_VARIANT,
    candidate_to_json,
    design_assignments,
    generate_candidate_inventory,
    load_raw_by_sample_id,
    read_jsonl,
    serialize_candidate_inventory,
)


STAGE_NAME = "Stage7B_A3_ENGLISH_COLUMN_CONDITIONED_CANDIDATE_SELECTION_AMENDMENT"
PATCH_NAME = "PATCH1"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260901.zip"
STAGEENG0_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
STAGE7B_A2_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
STAGE7C_A4_NAME = "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL"
STAGE7E0_A4_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
CANDIDATE_MISS_SENTINEL = "UNREPRESENTABLE_CANDIDATE_MISS"

SCIENTIFIC_ARTIFACTS = [
    "SOURCE_INPUT_MANIFEST.json",
    "A4_VALID_FAIL_FREEZE.json",
    "A4_ROOT_CAUSE_CLASSIFICATION.json",
    "COLUMN_CONDITIONED_REPRESENTATION_SPEC.json",
    "COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json",
    "OMIT_POLICY_AND_COLUMN_SCOPE_SPEC.json",
    "TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json",
    "DESIGN_TRAIN_COLUMN_SCHEMA_AUDIT.json",
    "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.json",
    "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.jsonl",
    "TYPE_COMPATIBLE_CANDIDATE_AUDIT.json",
    "REPRESENTATION_COMPARISON_AUDIT.json",
]


@dataclass(frozen=True)
class ColumnInfo:
    table_name: str
    column_name: str
    column_ref: str
    source_type: str
    nullable: bool
    has_default: bool
    primary_key: bool
    autoincrement: bool


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml"}:
        return sha256_text(data.decode("utf-8-sig"))
    return sha256_bytes(data)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


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


def _identifier(value: str) -> str:
    value = value.strip()
    if value.startswith(("`", '"', "[")):
        closing = "]" if value.startswith("[") else value[0]
        end = value.find(closing, 1)
        if end != -1:
            return value[1:end]
    return value.split()[0].strip("`\"[]")


def _normalize_identifier(value: str) -> str:
    return _identifier(value).split(".")[-1].strip().strip("`\"[]").casefold()


def split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def parse_insert_target(sql: str) -> tuple[str, list[str]]:
    match = re.search(r"insert\s+into\s+([`\"\[\]\w.]+)\s*(?:\((.*?)\))?\s*values", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError(f"Could not parse INSERT target: {sql[:120]}")
    table_name = _normalize_identifier(match.group(1))
    raw_columns = match.group(2)
    columns = [_normalize_identifier(item) for item in split_top_level(raw_columns)] if raw_columns else []
    return table_name, columns


def _matching_create_table_statements(sql_context: str) -> Iterable[tuple[str, str]]:
    pattern = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?", flags=re.IGNORECASE)
    for match in pattern.finditer(sql_context):
        open_index = sql_context.find("(", match.end())
        if open_index == -1:
            continue
        header = sql_context[match.end() : open_index].strip()
        depth = 0
        quote: str | None = None
        for index in range(open_index, len(sql_context)):
            char = sql_context[index]
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in {"'", '"', "`"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield _normalize_identifier(header), sql_context[open_index + 1 : index]
                    break


def parse_create_table_columns(sql_context: str, table_name: str) -> list[ColumnInfo]:
    normalized_table = _normalize_identifier(table_name)
    body = None
    for candidate_table, candidate_body in _matching_create_table_statements(sql_context):
        if candidate_table == normalized_table:
            body = candidate_body
            break
    if body is None:
        raise ValueError(f"CREATE TABLE not found for {table_name}")

    columns: list[ColumnInfo] = []
    constraint_prefixes = {"primary", "foreign", "unique", "check", "constraint", "key"}
    constraint_tokens = {"not", "null", "default", "primary", "references", "unique", "check", "collate", "generated", "as"}
    for raw_part in split_top_level(body):
        if not raw_part:
            continue
        first = raw_part.lstrip().split(maxsplit=1)[0].strip("`\"[]").casefold()
        if first in constraint_prefixes:
            continue
        column_name = _identifier(raw_part)
        remainder = raw_part[len(raw_part.split(maxsplit=1)[0]) :].strip()
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\S", remainder)
        type_tokens: list[str] = []
        for token in tokens:
            if token.casefold() in constraint_tokens:
                break
            type_tokens.append(token)
        source_type = " ".join(type_tokens).strip() or "TEXT"
        lowered = raw_part.casefold()
        columns.append(
            ColumnInfo(
                table_name=normalized_table,
                column_name=column_name,
                column_ref=f"COL_{len(columns) + 1}",
                source_type=source_type,
                nullable="not null" not in lowered and "primary key" not in lowered,
                has_default="default" in lowered,
                primary_key="primary key" in lowered,
                autoincrement="autoincrement" in lowered,
            )
        )
    if not columns:
        raise ValueError(f"No columns parsed for {table_name}")
    return columns


def parse_schema_tables(sql_context: str) -> dict[str, list[ColumnInfo]]:
    """Parse model-visible CREATE TABLE context without reading gold SQL."""

    tables: dict[str, list[ColumnInfo]] = {}
    for table_name, _body in _matching_create_table_statements(sql_context):
        tables[table_name] = parse_create_table_columns(sql_context, table_name)
    if not tables:
        raise ValueError("No CREATE TABLE statements found in sql_context")
    return tables


def derive_runtime_target_table(schema_tables: dict[str, list[ColumnInfo]]) -> tuple[str | None, list[ColumnInfo] | None, str]:
    if len(schema_tables) == 1:
        table_name, columns = next(iter(schema_tables.items()))
        return table_name, columns, "schema_only_single_table_context"
    return None, None, "multi_table_requires_schema_branch_selection"


def schema_table_branches(schema_tables: dict[str, list[ColumnInfo]]) -> list[tuple[str, str, list[ColumnInfo]]]:
    branches: list[tuple[str, str, list[ColumnInfo]]] = []
    multi_table = len(schema_tables) > 1
    for table_index, (table_name, columns) in enumerate(sorted(schema_tables.items()), start=1):
        table_ref = f"TAB_{table_index}"
        branch_columns = [
            ColumnInfo(
                table_name=column.table_name,
                column_name=column.column_name,
                column_ref=f"{table_ref}_{column.column_ref}" if multi_table else column.column_ref,
                source_type=column.source_type,
                nullable=column.nullable,
                has_default=column.has_default,
                primary_key=column.primary_key,
                autoincrement=column.autoincrement,
            )
            for column in columns
        ]
        branches.append((table_name, table_ref, branch_columns))
    return branches


def strict_int(text: str) -> bool:
    return re.fullmatch(r"[-+]?\d+", text.strip()) is not None


def strict_real(text: str) -> bool:
    return re.fullmatch(r"[-+]?(?:\d+\.\d+|\d+)", text.strip()) is not None


def type_family(source_type: str) -> str:
    upper = source_type.upper()
    if "INT" in upper:
        return "INTEGER"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB", "DECIMAL", "NUMERIC")):
        return "REAL"
    return "TEXT"


def candidate_type_compatible(text: str, source_type: str) -> bool:
    family = type_family(source_type)
    if family == "INTEGER":
        return strict_int(text)
    if family == "REAL":
        return strict_real(text)
    return bool(text)


def scope_audit(stageeng0_dir: Path, stageeng1_dir: Path) -> dict[str, Any]:
    train_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    dev_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")
    pilot_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")
    official_rows = read_jsonl(stageeng0_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")
    train_ids = {str(row["sample_id"]) for row in train_rows}
    pilot_ids = {str(row["sample_id"]) for row in pilot_rows}
    dev_ids = {str(row["sample_id"]) for row in dev_rows}
    official_ids = {str(row["sample_id"]) for row in official_rows}
    design_ids = train_ids - pilot_ids
    return {
        "stage": STAGE_NAME,
        "source_stageeng1_train_count": len(train_rows),
        "development_pilot_pool_count": len(pilot_ids),
        "development_dev_count": len(dev_ids),
        "official_test_confirmation_count": len(official_ids),
        "design_train_non_pilot_count": len(design_ids),
        "pilot_ids_in_design_train": sorted(design_ids & pilot_ids),
        "development_dev_ids_in_design_train": sorted(design_ids & dev_ids),
        "official_test_ids_in_design_train": sorted(design_ids & official_ids),
        "model_called": False,
        "gpu_called": False,
    }


def a4_valid_fail_freeze(stage7e0_a4_dir: Path) -> dict[str, Any]:
    lock_path = stage7e0_a4_dir / "STAGE7E0_A4_SERVER_RESULT_LOCK.json"
    lock = read_json(lock_path)
    return {
        "stage": STAGE_NAME,
        "source_stage": STAGE7E0_A4_NAME,
        "source_lock_path": f"{STAGE7E0_A4_NAME}/STAGE7E0_A4_SERVER_RESULT_LOCK.json",
        "source_lock_sha256": sha256_file(lock_path),
        "status": "STAGE7E0_A4_VALID_FEASIBILITY_FAIL_CLOSED",
        "primary_pass_count": lock.get("primary_pass_count"),
        "required_pass_count": lock.get("required_pass_count"),
        "evidence_integrity_status": lock.get("evidence_integrity_status"),
        "protocol_compliance_status": lock.get("protocol_compliance_status"),
        "primary_gate_status": lock.get("primary_gate_status"),
        "scientific_result_eligible": lock.get("scientific_result_eligible"),
        "gretel_pilot_opened": lock.get("gretel_pilot_opened"),
        "diagnostics_run": lock.get("diagnostics_run"),
        "no_a4_rerun_allowed": True,
        "gretel_pilot_must_remain_closed": True,
        "model_called": False,
        "gpu_called": False,
    }


def a4_root_cause_classification(stage7e0_a4_dir: Path) -> dict[str, Any]:
    summary = read_json(stage7e0_a4_dir / "SERVER_RESULT_CLASSIFICATION_PATCH4.json")
    lock = read_json(stage7e0_a4_dir / "STAGE7E0_A4_SERVER_RESULT_LOCK.json")
    return {
        "stage": STAGE_NAME,
        "source_stage": STAGE7E0_A4_NAME,
        "source_classification_path": f"{STAGE7E0_A4_NAME}/SERVER_RESULT_CLASSIFICATION_PATCH4.json",
        "source_classification_sha256": sha256_file(stage7e0_a4_dir / "SERVER_RESULT_CLASSIFICATION_PATCH4.json"),
        "primary_pass_count": summary.get("primary_pass_count") or lock.get("primary_pass_count"),
        "case_level_exact_selection": "6/10",
        "operation_prediction": "10/10 INSERT",
        "root_cause_counts": {
            "phase_o_severe_under_selection": 3,
            "phase_o_non_atomic_broader_span_selection": 1,
            "phase_m_primary_root_cause": 0,
            "compiler_or_materializer_bug": 0,
        },
        "failed_cases": [
            {"sample_id": "stage7c_a4_fresh_english_002", "reported_failure_stage": "acceptance_gate", "root_cause": "phase_o_severe_under_selection"},
            {"sample_id": "stage7c_a4_fresh_english_004", "reported_failure_stage": "acceptance_gate", "root_cause": "phase_o_severe_under_selection"},
            {"sample_id": "stage7c_a4_fresh_english_006", "reported_failure_stage": "acceptance_gate", "root_cause": "phase_o_severe_under_selection"},
            {"sample_id": "stage7c_a4_fresh_english_009", "reported_failure_stage": "materialization_failure", "root_cause": "phase_o_non_atomic_broader_span_selection"},
        ],
        "method_implication": "Move from free-length span_refs to required per-column SPAN/OMIT decisions.",
        "model_called": False,
        "gpu_called": False,
    }


def representation_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "representation": "column_conditioned_candidate_selection",
        "phase_o_output": {
            "operation": "INSERT",
            "table_ref": "TAB_1",
            "column_span_refs": {
                "COL_1": "SPAN_0009",
                "COL_2": "SPAN_0019",
                "COL_3": "SPAN_0026",
                "COL_4": "OMIT",
            },
        },
        "column_decision_domain": "For each target-table column, exactly one value: a current candidate SPAN ref or OMIT.",
        "structural_decision_completeness_property": "The JSON object requires one decision key for every target-table COL ref under column_span_refs.",
        "semantic_value_completeness_guaranteed_by_schema": False,
        "free_length_span_set_removed": True,
        "phase_m_status": "candidate for removal in a later protocol stage; not removed by this design audit",
        "model_called": False,
        "gpu_called": False,
    }


def dynamic_schema_for_columns(columns: list[ColumnInfo], span_refs: list[str]) -> dict[str, Any]:
    domain = ["OMIT", *span_refs]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7B-A3 Column-Conditioned Candidate Selection Output",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "table_ref", "column_span_refs"],
        "properties": {
            "operation": {"type": "string", "const": "INSERT"},
            "table_ref": {"type": "string", "const": "TAB_1"},
            "column_span_refs": {
                "type": "object",
                "additionalProperties": False,
                "required": [column.column_ref for column in columns],
                "properties": {column.column_ref: {"type": "string", "enum": domain} for column in columns},
            },
        },
    }


def dynamic_schema_for_schema_tables(schema_tables: dict[str, list[ColumnInfo]], span_refs: list[str]) -> dict[str, Any]:
    if len(schema_tables) == 1:
        return dynamic_schema_for_columns(next(iter(schema_tables.values())), span_refs)

    domain = ["OMIT", *span_refs]
    branches: list[dict[str, Any]] = []
    for _table_name, table_ref, branch_columns in schema_table_branches(schema_tables):
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "table_ref", "column_span_refs"],
                "properties": {
                    "operation": {"type": "string", "const": "INSERT"},
                    "table_ref": {"type": "string", "const": table_ref},
                    "column_span_refs": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [column.column_ref for column in branch_columns],
                        "properties": {column.column_ref: {"type": "string", "enum": domain} for column in branch_columns},
                    },
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7B-A3 Multi-Table Column-Conditioned Candidate Selection Output",
        "oneOf": branches,
    }


def schema_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "runtime_schema_builder": "dynamic_schema_for_schema_tables(model_visible_schema_tables, candidate_span_refs)",
        "required_top_level_keys": ["operation", "table_ref", "column_span_refs"],
        "column_span_refs_required_keys": "exact COL refs for every target-table column",
        "column_value_domain": ["OMIT", "SPAN_0001", "SPAN_0002", "..."],
        "forbidden_top_level_keys": ["span_refs", "value_spans", "start_char", "end_char", "values", "assignments"],
        "target_table_derivation_at_runtime": "single-table contexts derive TAB_1 from schema only; multi-table contexts use oneOf branches for all model-visible tables",
        "gold_sql_required_for_runtime_schema": False,
        "multi_table_schema_strategy": "oneOf branch per visible table_ref with branch-local required column_span_refs",
        "unknown_span_refs_structurally_impossible": True,
        "early_array_stop_structurally_impossible": True,
        "omit_under_selection_still_schema_valid": True,
        "structural_claim_scope": "decision completeness per visible target column, not semantic value completeness",
        "model_generates_character_offsets": False,
        "model_generates_free_length_span_set": False,
        "model_called": False,
        "gpu_called": False,
    }


def omit_policy_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "omit_token": "OMIT",
        "column_scope": "all target-table columns from the SQL schema, not only gold INSERT columns",
        "omit_meaning": "The request does not provide a literal value for this column; SQLite default/null/autoincrement behavior is left to deterministic compilation policy.",
        "audit_only": True,
        "runtime_gold_blind": True,
        "gold_used_only_for_oracle_representability_audit": True,
        "omitted_required_without_default_policy": "flag in audit; do not silently exclude samples",
        "model_called": False,
        "gpu_called": False,
    }


def source_input_manifest(stageeng0_dir: Path, stageeng1_dir: Path, stage7b_a2_dir: Path, stage7c_a4_dir: Path, stage7e0_a4_dir: Path) -> dict[str, Any]:
    files = [
        (STAGEENG0_NAME, stageeng0_dir, "STAGEENG0_LOCK.json"),
        (STAGEENG0_NAME, stageeng0_dir, "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl"),
        (STAGEENG1_NAME, stageeng1_dir, "STAGEENG1_LOCK.json"),
        (STAGEENG1_NAME, stageeng1_dir, "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl"),
        (STAGEENG1_NAME, stageeng1_dir, "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl"),
        (STAGEENG1_NAME, stageeng1_dir, "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "STAGE7B_A2_LOCK.json"),
        (STAGE7C_A4_NAME, stage7c_a4_dir, "STAGE7C_A4_LOCK.json"),
        (STAGE7E0_A4_NAME, stage7e0_a4_dir, "STAGE7E0_A4_SERVER_RESULT_LOCK.json"),
        (STAGE7E0_A4_NAME, stage7e0_a4_dir, "SERVER_RESULT_CLASSIFICATION_PATCH4.json"),
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "model_called": False,
        "gpu_called": False,
        "source_files": [
            {
                "source_stage": stage,
                "path": f"{stage}/{relative}",
                "sha256": sha256_file(root / relative),
                "bytes": (root / relative).stat().st_size,
            }
            for stage, root, relative in files
        ],
    }


def target_table_runtime_feasibility_audit(raw_by_id: dict[str, dict[str, Any]], design_ids: set[str]) -> dict[str, Any]:
    table_counts: list[int] = []
    failures: list[dict[str, str]] = []
    single_table_samples: list[str] = []
    multi_table_samples: list[dict[str, Any]] = []
    for sample_id in sorted(design_ids):
        try:
            schema_tables = parse_schema_tables(str(raw_by_id[sample_id]["sql_context"]))
            count = len(schema_tables)
            table_counts.append(count)
            if count == 1:
                single_table_samples.append(sample_id)
            else:
                multi_table_samples.append({"sample_id": sample_id, "schema_table_count": count, "table_names": sorted(schema_tables)})
        except Exception as exc:
            failures.append({"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"})

    multi_table_count = len(multi_table_samples)
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "design_sample_count": len(design_ids),
        "parsed_sample_count": len(design_ids) - len(failures),
        "parse_failure_count": len(failures),
        "parse_failures": failures[:20],
        "schema_table_count_stats": _stats(table_counts),
        "single_table_context_count": len(single_table_samples),
        "multi_table_context_count": multi_table_count,
        "multi_table_examples": multi_table_samples[:20],
        "target_table_derivation_at_runtime": (
            "schema_only_single_table_context"
            if multi_table_count == 0
            else "dynamic oneOf branches cover every model-visible table; table_ref is selected inside the schema, not from gold SQL"
        ),
        "gold_sql_required": False,
        "gold_sql_used_for_runtime_schema": False,
        "table_ref_const_tab1_allowed": multi_table_count == 0,
        "multi_table_schema_strategy_required": multi_table_count > 0,
        "multi_table_schema_strategy": "oneOf_per_model_visible_table",
        "model_called": False,
        "gpu_called": False,
    }


def audit_design_train(stageeng0_dir: Path, stageeng1_dir: Path, raw_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    raw_by_id = load_raw_by_sample_id(raw_dir)
    design_ids, assignments_by_sample, assignment_rows = design_assignments(stageeng0_dir, stageeng1_dir)
    target_table_audit = target_table_runtime_feasibility_audit(raw_by_id, design_ids)

    rows: list[dict[str, Any]] = []
    parse_failures: list[dict[str, str]] = []
    table_column_counts: list[int] = []
    assignment_counts: list[int] = []
    omit_counts: list[int] = []
    candidate_counts: list[int] = []
    compatible_counts: list[int] = []
    covered_assignments = 0
    type_compatible_gold = 0
    omitted_columns = 0
    omitted_with_default_or_nullable = 0
    omitted_required_without_default = 0
    samples_with_omit = 0
    type_family_counts: dict[str, int] = {}
    candidate_miss_count = 0

    for sample_id in sorted(design_ids):
        try:
            raw = raw_by_id[sample_id]
            schema_tables = parse_schema_tables(str(raw["sql_context"]))
            assignments = sorted(assignments_by_sample.get(sample_id, []), key=lambda row: int(row["assignment_index"]))
            assigned_names = [_normalize_identifier(str(row["column_ref_or_name"])) for row in assignments]
            runtime_table_name, runtime_columns, runtime_target_source = derive_runtime_target_table(schema_tables)
            if runtime_table_name is not None and runtime_columns is not None:
                table_name = runtime_table_name
                table_ref = "TAB_1"
                columns = runtime_columns
                oracle_target_source = "schema_only_single_table_context"
            else:
                candidates = []
                for candidate_table, candidate_ref, candidate_columns in schema_table_branches(schema_tables):
                    candidate_column_names = {_normalize_identifier(column.column_name) for column in candidate_columns}
                    if set(assigned_names) <= candidate_column_names:
                        candidates.append((candidate_table, candidate_ref, candidate_columns))
                if len(candidates) != 1:
                    raise ValueError(f"Could not identify a unique oracle target-table branch from assignment column names: {assigned_names}")
                table_name, table_ref, columns = candidates[0]
                oracle_target_source = "label_side_assignment_column_names_for_oracle_audit_only"
            inventory = generate_candidate_inventory(str(raw["sql_prompt"]), variant=SELECTED_VARIANT)
            candidate_by_span = {(item.start_char, item.end_char, item.text): item for item in inventory}
            assignment_by_column = dict(zip(assigned_names, assignments))
            span_refs = [candidate.span_ref for candidate in inventory]
            schema = dynamic_schema_for_schema_tables(schema_tables, span_refs)

            decisions: dict[str, str | None] = {}
            column_rows: list[dict[str, Any]] = []
            sample_full = True
            sample_type_full = True
            sample_omit_count = 0
            sample_candidate_miss_count = 0
            for column in columns:
                family = type_family(column.source_type)
                type_family_counts[family] = type_family_counts.get(family, 0) + 1
                type_compatible = [candidate for candidate in inventory if candidate_type_compatible(candidate.text, column.source_type)]
                compatible_counts.append(len(type_compatible))
                assignment = assignment_by_column.get(_normalize_identifier(column.column_name))
                assignment_present = assignment is not None
                expected: str | None = "OMIT"
                covered = True
                type_covered = True
                gold_text = None
                failure_reason = None
                representation_status = "OMIT"
                if assignment_present:
                    span = assignment["matched_source_span"]
                    gold_text = str(span["text"])
                    candidate = candidate_by_span.get((int(span["start_char"]), int(span["end_char"]), gold_text))
                    covered = candidate is not None
                    type_covered = bool(candidate and candidate_type_compatible(candidate.text, column.source_type))
                    sample_full = sample_full and covered
                    sample_type_full = sample_type_full and type_covered
                    if covered:
                        covered_assignments += 1
                        expected = candidate.span_ref
                        representation_status = "REPRESENTABLE"
                    else:
                        expected = None
                        failure_reason = CANDIDATE_MISS_SENTINEL
                        representation_status = "CANDIDATE_MISS"
                        candidate_miss_count += 1
                        sample_candidate_miss_count += 1
                    if type_covered:
                        type_compatible_gold += 1
                else:
                    sample_omit_count += 1
                    omitted_columns += 1
                    if column.nullable or column.has_default or column.primary_key or column.autoincrement:
                        omitted_with_default_or_nullable += 1
                    else:
                        omitted_required_without_default += 1
                decisions[column.column_ref] = expected
                column_rows.append(
                    {
                        "column_ref": column.column_ref,
                        "column_name": column.column_name,
                        "source_type": column.source_type,
                        "type_family": family,
                        "assignment_present": assignment_present,
                        "expected_decision": expected,
                        "representation_status": representation_status,
                        "failure_reason": failure_reason,
                        "gold_text": gold_text,
                        "oracle_span_covered": covered,
                        "oracle_span_type_compatible": type_covered,
                        "type_compatible_candidate_count": len(type_compatible),
                        "omitted": assignment is None,
                        "nullable": column.nullable,
                        "has_default": column.has_default,
                        "primary_key": column.primary_key,
                        "autoincrement": column.autoincrement,
                    }
                )
            if sample_omit_count:
                samples_with_omit += 1
            table_column_counts.append(len(columns))
            assignment_counts.append(len(assignments))
            omit_counts.append(sample_omit_count)
            candidate_counts.append(len(inventory))
            rows.append(
                {
                    "sample_id": sample_id,
                    "question_sha256": sha256_text(str(raw["sql_prompt"])),
                    "sql_sha256": sha256_text(str(raw["sql"])),
                    "target_table": table_name,
                    "target_table_derivation_at_runtime": runtime_target_source,
                    "oracle_target_table_branch_source": oracle_target_source,
                    "gold_sql_used_for_runtime_target_derivation": False,
                    "target_table_column_count": len(columns),
                    "assignment_count": len(assignments),
                    "omit_decision_count": sample_omit_count,
                    "candidate_miss_count": sample_candidate_miss_count,
                    "candidate_count": len(inventory),
                    "column_conditioned_schema_required_columns": [column.column_ref for column in columns],
                    "dynamic_domain_size_per_column": len(span_refs) + 1,
                    "all_gold_assignments_representable": sample_full,
                    "all_gold_assignments_type_compatible": sample_type_full,
                    "oracle_phase_o_output_schema_valid": sample_full,
                    "candidate_miss_sentinel": CANDIDATE_MISS_SENTINEL,
                    "oracle_phase_o_output": {"operation": "INSERT", "table_ref": table_ref, "column_span_refs": decisions},
                    "model_side_input_preview": {
                        "question_sha256": sha256_text(str(raw["sql_prompt"])),
                        "schema_column_refs": [column.column_ref for column in columns],
                        "visible_schema_table_count": len(schema_tables),
                        "candidate_inventory_sha256": sha256_text(serialize_candidate_inventory(inventory)),
                    },
                    "runtime_constraints_preview": {
                        "candidate_inventory": [candidate_to_json(candidate) for candidate in inventory],
                        "column_conditioned_schema_sha256": sha256_text(canonical_json(schema)),
                        "target_table_derivation_at_runtime": runtime_target_source,
                        "gold_sql_required_for_runtime_schema": False,
                    },
                    "columns": column_rows,
                }
            )
        except Exception as exc:
            parse_failures.append({"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"})

    assignment_count = len(assignment_rows)
    table_decision_count = sum(table_column_counts)
    full_sample_count = sum(1 for row in rows if row["all_gold_assignments_representable"])
    full_type_sample_count = sum(1 for row in rows if row["all_gold_assignments_type_compatible"])
    schema_audit = {
        "stage": STAGE_NAME,
        "status": "PASS" if not parse_failures else "FAIL",
        "design_sample_count": len(design_ids),
        "parsed_sample_count": len(rows),
        "parse_failure_count": len(parse_failures),
        "parse_failures": parse_failures[:20],
        "target_table_column_count_stats": _stats(table_column_counts),
        "assignment_count_stats": _stats(assignment_counts),
        "omit_decision_count_stats": _stats(omit_counts),
        "samples_with_omit_count": samples_with_omit,
        "target_table_column_decision_count": table_decision_count,
        "assigned_column_decision_count": assignment_count,
        "omit_decision_count": omitted_columns,
        "candidate_miss_count": candidate_miss_count,
        "omitted_columns_nullable_default_or_key_count": omitted_with_default_or_nullable,
        "omitted_required_without_default_count": omitted_required_without_default,
        "type_family_column_counts": dict(sorted(type_family_counts.items())),
        "model_called": False,
        "gpu_called": False,
    }
    representability = {
        "stage": STAGE_NAME,
        "status": "PASS" if not parse_failures else "FAIL",
        "scope": "StageENG1 development_train excluding 100-sample pilot pool",
        "candidate_generator_variant": SELECTED_VARIANT,
        "design_sample_count": len(design_ids),
        "assignment_count": assignment_count,
        "covered_assignment_count": covered_assignments,
        "missing_assignment_count": assignment_count - covered_assignments,
        "assignment_candidate_coverage": covered_assignments / assignment_count,
        "full_sample_covered_count": full_sample_count,
        "full_sample_candidate_coverage": full_sample_count / len(design_ids),
        "column_conditioned_decision_count": table_decision_count,
        "structural_column_key_coverage": table_decision_count / table_decision_count if table_decision_count else 0,
        "semantic_assignment_representability": covered_assignments / assignment_count,
        "candidate_miss_count": candidate_miss_count,
        "candidate_miss_sentinel": CANDIDATE_MISS_SENTINEL,
        "candidate_miss_sentinel_in_model_schema": False,
        "omit_decision_count": omitted_columns,
        "gold_used_only_for_oracle_coverage_audit": True,
        "runtime_gold_blind": True,
        "model_called": False,
        "gpu_called": False,
    }
    type_audit = {
        "stage": STAGE_NAME,
        "status": "PASS" if not parse_failures else "FAIL",
        "candidate_generator_variant": SELECTED_VARIANT,
        "design_sample_count": len(design_ids),
        "candidate_count_stats": _stats(candidate_counts),
        "type_compatible_candidate_count_stats": _stats(compatible_counts),
        "gold_assignment_type_compatible_count": type_compatible_gold,
        "gold_assignment_type_compatible_coverage": type_compatible_gold / assignment_count,
        "full_sample_type_compatible_count": full_type_sample_count,
        "full_sample_type_compatible_coverage": full_type_sample_count / len(design_ids),
        "type_family_column_counts": dict(sorted(type_family_counts.items())),
        "type_filter_policy": "audit only; any future type pruning must remain gold-blind and preserve oracle coverage",
        "model_called": False,
        "gpu_called": False,
    }
    return schema_audit, representability, type_audit, target_table_audit, rows


def representation_comparison(schema_audit: dict[str, Any], representability: dict[str, Any], type_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "status": "PASS",
        "comparison_scope": "oracle representability and structural burden only; no model call",
        "current_free_span_set": {
            "source_stage": STAGE7C_A4_NAME,
            "output_shape": {"operation": "INSERT", "span_refs": ["SPAN_0001", "..."]},
            "min_span_items": 1,
            "unknown_length_array": True,
            "early_stop_after_one_value_schema_valid": True,
            "observed_a4_primary_pass_count": "6/10",
            "observed_failure_family": "phase_o_under_selection_and_non_atomic_span_selection",
        },
        "column_conditioned_selection": {
            "output_shape": {"operation": "INSERT", "table_ref": "TAB_1", "column_span_refs": {"COL_n": "SPAN_ref_or_OMIT"}},
            "unknown_length_array": False,
            "requires_one_decision_per_target_column": True,
            "early_stop_after_one_value_schema_valid": False,
            "omit_under_selection_still_schema_valid": True,
            "semantic_value_completeness_guaranteed_by_schema": False,
            "structural_claim_scope": "decision completeness per target-table column",
            "target_table_column_decision_count": schema_audit["target_table_column_decision_count"],
            "omit_decision_count": schema_audit["omit_decision_count"],
            "candidate_miss_count": representability["candidate_miss_count"],
            "oracle_assignment_candidate_coverage": representability["assignment_candidate_coverage"],
            "semantic_assignment_representability": representability["semantic_assignment_representability"],
            "oracle_type_compatible_coverage": type_audit["gold_assignment_type_compatible_coverage"],
        },
        "method_decision": "Proceed to a later protocol stage only if reviewer accepts the structural-completeness tradeoff.",
        "pilot_usage_allowed": False,
        "development_dev_usage_allowed": False,
        "official_test_usage_allowed": False,
        "model_called": False,
        "gpu_called": False,
    }


def build_derived_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = [
        {"path": name, "bytes": (stage_dir / name).stat().st_size, "sha256": sha256_file(stage_dir / name)}
        for name in SCIENTIFIC_ARTIFACTS
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def validation_report(
    scope: dict[str, Any],
    freeze: dict[str, Any],
    root_cause: dict[str, Any],
    target_table_audit: dict[str, Any],
    schema_audit: dict[str, Any],
    representability: dict[str, Any],
    type_audit: dict[str, Any],
) -> str:
    return f"""# Stage7B-A3 English Column-Conditioned Candidate Selection Validation Report

Status: {representability["status"]}

Validation date: {date.today().isoformat()}

## Scope

Stage7B-A3 is a CPU-only architecture amendment. It freezes Stage7E0-A4 as a
valid scientific feasibility failure and audits a column-conditioned
candidate-selection representation on the 728 non-pilot design-train samples.
It does not call a model, does not use GPU, does not open the 100-sample
development pilot, does not use development-dev, and does not use official
Gretel test rows.

```text
design_train_non_pilot_count={scope["design_train_non_pilot_count"]}
development_pilot_pool_count={scope["development_pilot_pool_count"]}
development_dev_count={scope["development_dev_count"]}
official_test_confirmation_count={scope["official_test_confirmation_count"]}
model_called=false
gpu_called=false
```

## Frozen A4 Result

```text
stage7e0_a4_status={freeze["status"]}
primary_pass_count={freeze["primary_pass_count"]}
required_pass_count={freeze["required_pass_count"]}
primary_gate_status={freeze["primary_gate_status"]}
scientific_result_eligible={str(freeze["scientific_result_eligible"]).lower()}
gretel_pilot_opened={str(freeze["gretel_pilot_opened"]).lower()}
```

## Root Cause

```text
phase_o_severe_under_selection={root_cause["root_cause_counts"]["phase_o_severe_under_selection"]}
phase_o_non_atomic_broader_span_selection={root_cause["root_cause_counts"]["phase_o_non_atomic_broader_span_selection"]}
phase_m_primary_root_cause={root_cause["root_cause_counts"]["phase_m_primary_root_cause"]}
compiler_or_materializer_bug={root_cause["root_cause_counts"]["compiler_or_materializer_bug"]}
```

## Column-Conditioned Audit

```text
schema_table_count_min={target_table_audit["schema_table_count_stats"]["min"]}
schema_table_count_median={target_table_audit["schema_table_count_stats"]["median"]}
schema_table_count_p95={target_table_audit["schema_table_count_stats"]["p95"]}
schema_table_count_max={target_table_audit["schema_table_count_stats"]["max"]}
single_table_context_count={target_table_audit["single_table_context_count"]}/{target_table_audit["design_sample_count"]}
multi_table_context_count={target_table_audit["multi_table_context_count"]}
target_table_derivation_at_runtime={target_table_audit["target_table_derivation_at_runtime"]}
gold_sql_required={str(target_table_audit["gold_sql_required"]).lower()}
parsed_sample_count={schema_audit["parsed_sample_count"]}
target_table_column_decision_count={schema_audit["target_table_column_decision_count"]}
assigned_column_decision_count={schema_audit["assigned_column_decision_count"]}
omit_decision_count={schema_audit["omit_decision_count"]}
candidate_miss_count={representability["candidate_miss_count"]}
omitted_required_without_default_count={schema_audit["omitted_required_without_default_count"]}
assignment_candidate_coverage={representability["covered_assignment_count"]}/{representability["assignment_count"]}
semantic_assignment_representability={representability["semantic_assignment_representability"]}
structural_column_key_coverage={representability["structural_column_key_coverage"]}
full_sample_candidate_coverage={representability["full_sample_covered_count"]}/{representability["design_sample_count"]}
gold_assignment_type_compatible_coverage={type_audit["gold_assignment_type_compatible_count"]}/{representability["assignment_count"]}
candidate_count_p95={type_audit["candidate_count_stats"]["p95"]}
type_compatible_candidate_count_p95={type_audit["type_compatible_candidate_count_stats"]["p95"]}
```

## Decision

Column-conditioned candidate selection directly addresses the A4 early-stop
failure mode by requiring one decision key for every target-table column. This
is decision completeness, not a structural guarantee of semantic value
completeness: the model can still choose OMIT incorrectly, and that remains an
evaluation failure. This stage does not run a new primary feasibility
experiment and does not authorize opening Gretel pilot/dev/test rows.
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7B-A3 English Column-Conditioned Candidate Selection Amendment

This package freezes Stage7E0-A4 as a valid feasibility failure and audits a
column-conditioned candidate-selection representation.

Review order:

1. `{STAGE_NAME}/A4_VALID_FAIL_FREEZE.json`
2. `{STAGE_NAME}/A4_ROOT_CAUSE_CLASSIFICATION.json`
3. `{STAGE_NAME}/COLUMN_CONDITIONED_REPRESENTATION_SPEC.json`
4. `{STAGE_NAME}/COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json`
5. `{STAGE_NAME}/OMIT_POLICY_AND_COLUMN_SCOPE_SPEC.json`
6. `{STAGE_NAME}/TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json`
7. `{STAGE_NAME}/DESIGN_TRAIN_COLUMN_SCHEMA_AUDIT.json`
8. `{STAGE_NAME}/ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.json`
9. `{STAGE_NAME}/ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.jsonl`
10. `{STAGE_NAME}/TYPE_COMPATIBLE_CANDIDATE_AUDIT.json`
11. `{STAGE_NAME}/REPRESENTATION_COMPARISON_AUDIT.json`
12. `{STAGE_NAME}/SOURCE_INPUT_MANIFEST.json`
13. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
14. `{STAGE_NAME}/STAGE7B_A3_LOCK.json`
15. `{STAGE_NAME}/VALIDATION_REPORT.md`
16. `scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py`
17. `scripts/data/validate_stage7b_a3_column_conditioned_candidate_selection.py`
18. `tests/test_stage7b_a3_column_conditioned_candidate_selection.py`

Rerun with local Gretel parquet:

```bash
uv run --with pyarrow python scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py \\
  --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236
python scripts/data/validate_stage7b_a3_column_conditioned_candidate_selection.py \\
  --stage-dir {STAGE_NAME}
```

No GPU is required. No model is called. The Gretel pilot remains closed.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def build_stage(
    out_dir: Path,
    raw_dir: Path,
    *,
    stageeng0_dir: Path = PROJECT_ROOT / STAGEENG0_NAME,
    stageeng1_dir: Path = PROJECT_ROOT / STAGEENG1_NAME,
    stage7b_a2_dir: Path = PROJECT_ROOT / STAGE7B_A2_NAME,
    stage7c_a4_dir: Path = PROJECT_ROOT / STAGE7C_A4_NAME,
    stage7e0_a4_dir: Path = PROJECT_ROOT / STAGE7E0_A4_NAME,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scope = scope_audit(stageeng0_dir, stageeng1_dir)
    freeze = a4_valid_fail_freeze(stage7e0_a4_dir)
    root_cause = a4_root_cause_classification(stage7e0_a4_dir)
    schema_audit, representability, type_audit, target_table_audit, rows = audit_design_train(stageeng0_dir, stageeng1_dir, raw_dir)
    comparison = representation_comparison(schema_audit, representability, type_audit)

    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(stageeng0_dir, stageeng1_dir, stage7b_a2_dir, stage7c_a4_dir, stage7e0_a4_dir))
    write_json(out_dir / "A4_VALID_FAIL_FREEZE.json", freeze)
    write_json(out_dir / "A4_ROOT_CAUSE_CLASSIFICATION.json", root_cause)
    write_json(out_dir / "COLUMN_CONDITIONED_REPRESENTATION_SPEC.json", representation_spec())
    write_json(out_dir / "COLUMN_CONDITIONED_JSON_SCHEMA_SPEC.json", schema_spec())
    write_json(out_dir / "OMIT_POLICY_AND_COLUMN_SCOPE_SPEC.json", omit_policy_spec())
    write_json(out_dir / "TARGET_TABLE_RUNTIME_FEASIBILITY_AUDIT.json", target_table_audit)
    write_json(out_dir / "DESIGN_TRAIN_COLUMN_SCHEMA_AUDIT.json", schema_audit)
    write_json(out_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.json", representability)
    write_jsonl(out_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.jsonl", rows)
    write_json(out_dir / "TYPE_COMPATIBLE_CANDIDATE_AUDIT.json", type_audit)
    write_json(out_dir / "REPRESENTATION_COMPARISON_AUDIT.json", comparison)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_derived_manifest(out_dir))

    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_COLUMN_CONDITIONED_ORACLE_REPRESENTABILITY_AUDIT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "source_stage7e0_a4_status": freeze["status"],
        "source_stage7e0_a4_primary_pass_count": freeze["primary_pass_count"],
        "source_stage7e0_a4_primary_gate_status": freeze["primary_gate_status"],
        "stage7e0_a4_rerun_allowed": False,
        "gretel_pilot_opened": False,
        "design_train_non_pilot_count": scope["design_train_non_pilot_count"],
        "assignment_count": representability["assignment_count"],
        "assignment_candidate_coverage": representability["assignment_candidate_coverage"],
        "full_sample_candidate_coverage": representability["full_sample_candidate_coverage"],
        "target_table_column_decision_count": schema_audit["target_table_column_decision_count"],
        "target_table_runtime_gold_sql_required": target_table_audit["gold_sql_required"],
        "single_table_context_count": target_table_audit["single_table_context_count"],
        "multi_table_context_count": target_table_audit["multi_table_context_count"],
        "omit_decision_count": schema_audit["omit_decision_count"],
        "candidate_miss_count": representability["candidate_miss_count"],
        "gold_assignment_type_compatible_coverage": type_audit["gold_assignment_type_compatible_coverage"],
        "column_conditioned_output_required": True,
        "free_length_span_refs_removed": True,
        "model_called": False,
        "gpu_called": False,
        "development_pilot_pool_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7B_A3_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(scope, freeze, root_cause, target_table_audit, schema_audit, representability, type_audit))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "design_train_non_pilot_count": scope["design_train_non_pilot_count"],
        "assignment_count": representability["assignment_count"],
        "assignment_candidate_coverage": representability["assignment_candidate_coverage"],
        "full_sample_candidate_coverage": representability["full_sample_candidate_coverage"],
        "target_table_column_decision_count": schema_audit["target_table_column_decision_count"],
        "single_table_context_count": target_table_audit["single_table_context_count"],
        "multi_table_context_count": target_table_audit["multi_table_context_count"],
        "omit_decision_count": schema_audit["omit_decision_count"],
        "candidate_miss_count": representability["candidate_miss_count"],
        "model_called": False,
        "gpu_called": False,
    }


def include_paths_for_package(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    for relative in [
        "pyproject.toml",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/validate_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/validate_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "tests/test_stage7b_a3_column_conditioned_candidate_selection.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        f"{STAGEENG0_NAME}/STAGEENG0_LOCK.json",
        f"{STAGEENG0_NAME}/INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl",
        f"{STAGEENG0_NAME}/OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl",
        f"{STAGEENG1_NAME}/STAGEENG1_LOCK.json",
        f"{STAGEENG1_NAME}/DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl",
        f"{STAGEENG1_NAME}/DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl",
        f"{STAGEENG1_NAME}/DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl",
        f"{STAGE7B_A2_NAME}/STAGE7B_A2_LOCK.json",
        f"{STAGE7B_A2_NAME}/ORACLE_CANDIDATE_COVERAGE_AUDIT.json",
        f"{STAGE7C_A4_NAME}/STAGE7C_A4_LOCK.json",
        f"{STAGE7E0_A4_NAME}/STAGE7E0_A4_SERVER_RESULT_LOCK.json",
        f"{STAGE7E0_A4_NAME}/SERVER_RESULT_CLASSIFICATION_PATCH4.json",
        f"{STAGE7E0_A4_NAME}/SERVER_RESULT_FAILURE_ANALYSIS.md",
        f"{STAGE7E0_A4_NAME}/VALIDATION_REPORT_PATCH4.md",
    ]:
        path = PROJECT_ROOT / relative
        if path.is_file():
            files.append(path)
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
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(f"{digest}  {package_path.name}\n", encoding="utf-8", newline="\n")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    args = parser.parse_args()
    summary = build_stage(args.out_dir, args.raw_dir)
    digest = package_reviewer(args.out_dir, args.package)
    summary["package"] = str(args.package)
    summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
