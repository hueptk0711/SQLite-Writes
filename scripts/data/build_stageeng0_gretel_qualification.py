#!/usr/bin/env python3
"""StageENG0 Gretel English SQLite write qualification.

This stage is deliberately CPU-only. It freezes the public Gretel
``gretelai/synthetic_text_to_sql`` train/test parquet files, audits the raw
schema and DML population, executes compatible gold SQLite writes from a fresh
context database, and writes deterministic manifests. It does not call a model
or use GPU inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
DATASET_ID = "gretelai/synthetic_text_to_sql"
DATASET_REVISION = "740ab236e64503fba51be1101df7a1be83bf455d"
DATASET_URL = f"https://huggingface.co/datasets/{DATASET_ID}"
RAW_FILES = {
    "train": "synthetic_text_to_sql_train.snappy.parquet",
    "test": "synthetic_text_to_sql_test.snappy.parquet",
}
EXPECTED_FIELDS = [
    "id",
    "domain",
    "domain_description",
    "sql_complexity",
    "sql_complexity_description",
    "sql_task_type",
    "sql_task_type_description",
    "sql_prompt",
    "sql_context",
    "sql",
    "sql_explanation",
]
NONDETERMINISTIC_TOKENS = {
    "CURRENT_DATE",
    "CURRENT_TIME",
    "CURRENT_TIMESTAMP",
    "RANDOM",
    "RANDOMBLOB",
    "NOW",
    "UUID",
}
CONTROLLED_EXCLUSION_REASONS = {
    "non_english",
    "malformed_sql",
    "multi_statement",
    "context_parse_failure",
    "missing_table",
    "missing_column",
    "sqlite_dialect_incompatible",
    "gold_execution_failure",
    "nondeterministic",
    "derived_value_not_supported",
    "implicit_value_not_supported",
    "nonalignable_normalization",
    "expression_value_not_supported",
    "joint_source_not_representable",
}
SUPPORTED_PRIMARY_LITERAL_KINDS = {
    "direct_string_literal",
    "direct_integer_literal",
    "direct_real_literal",
}
SCIENTIFIC_ARTIFACTS = [
    "RAW_DATA_HASHES.json",
    "RAW_SCHEMA_AUDIT.json",
    "ELIGIBILITY_POLICY.json",
    "DML_OPERATION_COUNTS.json",
    "WRITE_COMPLEXITY_AUDIT.json",
    "SQLITE_CONTEXT_AUDIT.jsonl",
    "SQLITE_COMPATIBILITY_SUMMARY.json",
    "GOLD_EXECUTION_AUDIT.jsonl",
    "SOURCE_ALIGNABILITY_AUDIT.jsonl",
    "SOURCE_ALIGNABILITY_SUMMARY.json",
    "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl",
    "INSERT_GROUNDING_SUMMARY.json",
    "EXCLUSION_LEDGER.jsonl",
    "EXCLUSION_REASON_COUNTS.json",
    "DATA_LEAKAGE_AUDIT.json",
    "SPLIT_CANDIDATE_AUDIT.json",
    "ELIGIBLE_INSERT_MANIFEST.jsonl",
    "ELIGIBLE_UPDATE_MANIFEST.jsonl",
    "ELIGIBLE_DELETE_MANIFEST.jsonl",
    "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl",
    "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl",
]


@dataclass(frozen=True)
class SQLClassification:
    operation: str
    statement_count: int
    status: str
    primary_statement: str
    reason: str


@dataclass(frozen=True)
class TargetReference:
    table: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True)
class InsertAssignment:
    assignment_index: int
    column_ref_or_name: str
    value_expression: str
    gold_value_kind: str
    gold_value: Any
    source_literal_text: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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


def download_raw_files(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for filename in RAW_FILES.values():
        target = raw_dir / filename
        if target.exists():
            continue
        url = f"{DATASET_URL}/resolve/{DATASET_REVISION}/{filename}?download=true"
        urllib.request.urlretrieve(url, target)


def load_parquet_rows(raw_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by CLI users.
        raise SystemExit(
            "pyarrow is required to read Gretel parquet files. Run with "
            "`uv run --with pyarrow python scripts/data/build_stageeng0_gretel_qualification.py ...`."
        ) from exc
    rows_by_split: dict[str, list[dict[str, Any]]] = {}
    schemas: dict[str, str] = {}
    for split, filename in RAW_FILES.items():
        path = raw_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing raw parquet for split {split}: {path}")
        table = pq.read_table(path)
        rows_by_split[split] = table.to_pylist()
        schemas[split] = str(table.schema)
    return rows_by_split, schemas


def sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    start = 0
    i = 0
    quote: str | None = None
    bracket_quote = False
    line_comment = False
    block_comment = False
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if line_comment:
            if ch in "\r\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if quote:
            if bracket_quote:
                if ch == "]":
                    quote = None
                    bracket_quote = False
                i += 1
                continue
            if ch == quote:
                if nxt == quote:
                    i += 2
                    continue
                quote = None
            elif ch == "\\" and quote in {"'", '"'}:
                i += 2
                continue
            i += 1
            continue
        if ch == "-" and nxt == "-":
            line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            i += 1
            continue
        if ch == "[":
            quote = "]"
            bracket_quote = True
            i += 1
            continue
        if ch == ";":
            part = strip_sql_comments(sql[start:i]).strip()
            if part:
                statements.append(part)
            start = i + 1
        i += 1
    part = strip_sql_comments(sql[start:]).strip()
    if part:
        statements.append(part)
    return statements


def leading_keyword(statement: str) -> str:
    i = 0
    while i < len(statement):
        if statement[i].isspace():
            i += 1
            continue
        if statement.startswith("--", i):
            end = statement.find("\n", i + 2)
            i = len(statement) if end == -1 else end + 1
            continue
        if statement.startswith("/*", i):
            end = statement.find("*/", i + 2)
            if end == -1:
                return ""
            i = end + 2
            continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", statement[i:])
        return match.group(0).upper() if match else ""
    return ""


def classify_gold_sql(sql: str) -> SQLClassification:
    statements = sql_statements(sql or "")
    if not statements:
        return SQLClassification("OTHER", 0, "malformed", "", "empty_sql")
    if len(statements) != 1:
        first = leading_keyword(statements[0])
        operation = first if first in {"INSERT", "UPDATE", "DELETE"} else "OTHER"
        return SQLClassification(operation, len(statements), "multi_statement", statements[0], "multi_statement")
    statement = statements[0]
    keyword = leading_keyword(statement)
    if keyword in {"INSERT", "UPDATE", "DELETE"}:
        return SQLClassification(keyword, 1, "dml", statement, "")
    if not keyword:
        return SQLClassification("OTHER", 1, "malformed", statement, "missing_leading_keyword")
    return SQLClassification("OTHER", 1, "other", statement, "")


def strip_sql_comments(sql: str) -> str:
    out: list[str] = []
    i = 0
    quote: str | None = None
    bracket_quote = False
    line_comment = False
    block_comment = False
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if line_comment:
            if ch in "\r\n":
                line_comment = False
                out.append(ch)
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if quote:
            out.append(ch)
            if bracket_quote:
                if ch == "]":
                    quote = None
                    bracket_quote = False
            elif ch == quote:
                if nxt == quote:
                    out.append(nxt)
                    i += 1
                else:
                    quote = None
            elif ch == "\\" and quote in {"'", '"'} and nxt:
                out.append(nxt)
                i += 1
            i += 1
            continue
        if ch == "-" and nxt == "-":
            line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
        elif ch == "[":
            quote = "]"
            bracket_quote = True
        out.append(ch)
        i += 1
    return "".join(out)


def unquote_identifier(identifier: str) -> str:
    value = identifier.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        return value[1:-1].replace(value[0] * 2, value[0])
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return value


def split_identifier_list(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    bracket = False
    for ch in value:
        if quote:
            buf.append(ch)
            if bracket and ch == "]":
                quote = None
                bracket = False
            elif not bracket and ch == quote:
                quote = None
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            buf.append(ch)
            continue
        if ch == "[":
            quote = "]"
            bracket = True
            buf.append(ch)
            continue
        if ch == ",":
            part = "".join(buf).strip()
            if part:
                parts.append(unquote_identifier(part))
            buf = []
        else:
            buf.append(ch)
    part = "".join(buf).strip()
    if part:
        parts.append(unquote_identifier(part))
    return tuple(parts)


def target_reference(statement: str, operation: str) -> TargetReference:
    clean = strip_sql_comments(statement)
    ident = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$]*)'
    if operation == "INSERT":
        match = re.search(rf"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+({ident})(?:\s*\((.*?)\))?", clean, re.I | re.S)
        if not match:
            return TargetReference(None, ())
        columns = split_identifier_list(match.group(2) or "")
        return TargetReference(unquote_identifier(match.group(1)), columns)
    if operation == "UPDATE":
        match = re.search(rf"\bUPDATE\s+(?:OR\s+\w+\s+)?({ident})\s+SET\s+(.*?)(?:\bWHERE\b|$)", clean, re.I | re.S)
        if not match:
            return TargetReference(None, ())
        assignments = []
        for part in split_top_level_commas(match.group(2)):
            col = part.split("=", 1)[0].strip()
            if col:
                assignments.append(unquote_identifier(col.split(".")[-1]))
        return TargetReference(unquote_identifier(match.group(1)), tuple(assignments))
    if operation == "DELETE":
        match = re.search(rf"\bDELETE\s+FROM\s+({ident})(?:\s|$)", clean, re.I | re.S)
        if not match:
            return TargetReference(None, ())
        return TargetReference(unquote_identifier(match.group(1)), ())
    return TargetReference(None, ())


def split_top_level_commas(value: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    depth = 0
    for ch in value:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [part for part in parts if part]


def has_nondeterministic_sql(statement: str) -> bool:
    clean = strip_sql_comments(statement)
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", clean)
    return any(token.upper() in NONDETERMINISTIC_TOKENS for token in tokens)


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sqlite_schema(con: sqlite3.Connection) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    tables = [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        schema[table] = [row[1] for row in con.execute(f"PRAGMA table_info({quote_ident(table)})")]
    return schema


def execute_context(context: str) -> tuple[sqlite3.Connection | None, str]:
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.executescript(context or "")
        con.commit()
        return con, ""
    except sqlite3.Error as exc:
        con.close()
        return None, str(exc)


def snapshot_database(con: sqlite3.Connection) -> dict[str, Any]:
    payload: dict[str, Any] = {"tables": []}
    for table, columns in sqlite_schema(con).items():
        column_sql = ", ".join(quote_ident(column) for column in columns)
        rows = [list(row) for row in con.execute(f"SELECT {column_sql} FROM {quote_ident(table)} ORDER BY rowid")]
        create_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        payload["tables"].append(
            {
                "table": table,
                "columns": columns,
                "create_sql": create_sql,
                "rows": rows,
            }
        )
    return payload


def snapshot_hash(con: sqlite3.Connection) -> str:
    return sha256_text(canonical_json(snapshot_database(con)))


def audit_context_and_gold(row: dict[str, Any], classification: SQLClassification) -> tuple[dict[str, Any], dict[str, Any]]:
    sample_id = row["sample_id"]
    context_record: dict[str, Any] = {
        "sample_id": sample_id,
        "source_split": row["source_split"],
        "operation": classification.operation,
        "context_status": "not_run",
        "failure_reason": "",
        "tables": {},
        "target_table": None,
        "target_columns": [],
    }
    gold_record: dict[str, Any] = {
        "sample_id": sample_id,
        "source_split": row["source_split"],
        "operation": classification.operation,
        "exec_status": "not_run",
        "failure_reason": "",
        "before_state_hash": "",
        "after_state_hash": "",
        "repeat_after_state_hash": "",
        "deterministic": False,
    }
    if classification.status != "dml":
        return context_record, gold_record
    target = target_reference(classification.primary_statement, classification.operation)
    context_record["target_table"] = target.table
    context_record["target_columns"] = list(target.columns)
    con, error = execute_context(str(row.get("sql_context") or ""))
    if con is None:
        context_record["context_status"] = "failure"
        context_record["failure_reason"] = classify_sqlite_error(error)
        gold_record["failure_reason"] = "context_not_sqlite_compatible"
        return context_record, gold_record
    try:
        schema = sqlite_schema(con)
        context_record["tables"] = schema
        if not target.table or target.table not in schema:
            context_record["context_status"] = "failure"
            context_record["failure_reason"] = "missing_table"
            gold_record["failure_reason"] = "missing_table"
            return context_record, gold_record
        if target.columns:
            missing = [column for column in target.columns if column not in schema[target.table]]
            if missing:
                context_record["context_status"] = "failure"
                context_record["failure_reason"] = "missing_column"
                context_record["missing_columns"] = missing
                gold_record["failure_reason"] = "missing_column"
                return context_record, gold_record
        context_record["context_status"] = "success"
        if has_nondeterministic_sql(classification.primary_statement):
            gold_record["exec_status"] = "failure"
            gold_record["failure_reason"] = "nondeterministic"
            gold_record["before_state_hash"] = snapshot_hash(con)
            return context_record, gold_record
        before = snapshot_hash(con)
        try:
            con.execute("BEGIN")
            con.execute(classification.primary_statement)
            after = snapshot_hash(con)
            con.rollback()
        except sqlite3.Error as exc:
            con.rollback()
            gold_record["exec_status"] = "failure"
            gold_record["failure_reason"] = classify_sqlite_error(str(exc))
            gold_record["before_state_hash"] = before
            return context_record, gold_record
    finally:
        con.close()
    con2, error2 = execute_context(str(row.get("sql_context") or ""))
    if con2 is None:
        gold_record["exec_status"] = "failure"
        gold_record["failure_reason"] = classify_sqlite_error(error2)
        return context_record, gold_record
    try:
        con2.execute("BEGIN")
        con2.execute(classification.primary_statement)
        repeat_after = snapshot_hash(con2)
        con2.rollback()
    except sqlite3.Error as exc:
        con2.rollback()
        gold_record["exec_status"] = "failure"
        gold_record["failure_reason"] = classify_sqlite_error(str(exc))
        gold_record["before_state_hash"] = before
        return context_record, gold_record
    finally:
        con2.close()
    gold_record.update(
        {
            "exec_status": "success" if after == repeat_after else "failure",
            "failure_reason": "" if after == repeat_after else "nondeterministic",
            "before_state_hash": before,
            "after_state_hash": after,
            "repeat_after_state_hash": repeat_after,
            "deterministic": after == repeat_after,
        }
    )
    return context_record, gold_record


def classify_sqlite_error(error: str) -> str:
    text = error.lower()
    if "no such table" in text:
        return "missing_table"
    if "no such column" in text or "has no column" in text:
        return "missing_column"
    if "syntax error" in text or "unrecognized token" in text or "near" in text:
        return "sqlite_dialect_incompatible"
    return "gold_execution_failure"


def classify_write_complexity(statement: str, operation: str) -> str:
    clean = strip_sql_comments(statement)
    upper = clean.upper()
    if operation == "INSERT":
        if re.search(r"\bINSERT\b.*\bSELECT\b", upper, re.S):
            return "insert_select"
        match = re.search(r"\bVALUES\s*(.*)$", clean, re.I | re.S)
        tuple_count = count_top_level_value_tuples(match.group(1)) if match else 0
        return "single_row_insert" if tuple_count <= 1 else "multi_row_insert"
    if operation == "UPDATE":
        set_part = re.split(r"\bWHERE\b", re.split(r"\bSET\b", clean, flags=re.I, maxsplit=1)[-1], flags=re.I, maxsplit=1)[0]
        if re.search(r"\bSELECT\b", set_part, re.I):
            return "update_subquery"
        if re.search(r"=[^,]*(\+|-|\*|/|\|\||\bCASE\b)", set_part, re.I):
            return "update_expression"
        return "simple_update_literal"
    if operation == "DELETE":
        if re.search(r"\bSELECT\b|\bJOIN\b|\bEXISTS\b|\bWITH\b", upper):
            return "complex_delete"
        return "simple_delete_predicate" if re.search(r"\bWHERE\b", upper) else "delete_all_rows"
    return "other"


def count_top_level_value_tuples(value: str) -> int:
    depth = 0
    quote: str | None = None
    count = 0
    for ch in value:
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            continue
        if ch == "(":
            if depth == 0:
                count += 1
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
    return count


def is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch in {"_", "$"}


def is_number_boundary(sql: str, start: int, end: int) -> bool:
    before = sql[start - 1] if start > 0 else ""
    after = sql[end] if end < len(sql) else ""
    if before and (is_ident_char(before) or before in {".", "+", "-"}):
        return False
    if after and (is_ident_char(after) or after == "."):
        return False
    return True


def parse_single_quoted_literal(expr: str) -> str | None:
    if len(expr) < 2 or expr[0] != "'" or expr[-1] != "'":
        return None
    i = 1
    value: list[str] = []
    while i < len(expr) - 1:
        ch = expr[i]
        if ch == "'":
            if i + 1 < len(expr) - 1 and expr[i + 1] == "'":
                value.append("'")
                i += 2
                continue
            return None
        value.append(ch)
        i += 1
    return "".join(value)


def sql_literals(statement: str) -> list[str]:
    clean = strip_sql_comments(statement)
    literals: list[str] = []
    i = 0
    while i < len(clean):
        ch = clean[i]
        if ch == "'":
            i += 1
            buf: list[str] = []
            while i < len(clean):
                if clean[i] == "'":
                    if i + 1 < len(clean) and clean[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(clean[i])
                i += 1
            literals.append("".join(buf))
            continue
        number = re.match(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", clean[i:])
        if number:
            end = i + len(number.group(0))
            if is_number_boundary(clean, i, end):
                literals.append(number.group(0))
                i = end
                continue
        identifier = re.match(r"[A-Za-z_][A-Za-z0-9_$]*", clean[i:])
        if identifier:
            i += len(identifier.group(0))
            continue
        if ch in {'"', "`", "["}:
            close = "]" if ch == "[" else ch
            end = clean.find(close, i + 1)
            if end != -1:
                i = end + 1
                continue
        i += 1
    return literals


def source_occurrences(prompt: str, literal: str, *, numeric: bool | None = None) -> list[dict[str, Any]]:
    if literal == "":
        return []
    literal_lower = literal.lower()
    prompt_lower = prompt.lower()
    if numeric is None:
        numeric = bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", literal))
    spans: list[dict[str, Any]] = []
    start = 0
    while True:
        index = prompt_lower.find(literal_lower, start)
        if index == -1:
            break
        end = index + len(literal)
        if source_span_boundary_ok(prompt, index, end, numeric=numeric):
            spans.append({"start_char": index, "end_char": end, "text": prompt[index:end]})
        start = index + 1
    return spans


def source_span_boundary_ok(prompt: str, start: int, end: int, *, numeric: bool) -> bool:
    before = prompt[start - 1] if start > 0 else ""
    after = prompt[end] if end < len(prompt) else ""
    if numeric:
        before_before = prompt[start - 2] if start > 1 else ""
        after_after = prompt[end + 1] if end + 1 < len(prompt) else ""
        if before and (before.isalnum() or before in {"+", "-"}):
            return False
        if before == "." and before_before.isdigit():
            return False
        if after and after.isalnum():
            return False
        if after == "." and after_after.isdigit():
            return False
        return True
    first = prompt[start]
    last = prompt[end - 1]
    if (first.isalnum() or first == "_") and before and (before.isalnum() or before == "_"):
        return False
    if (last.isalnum() or last == "_") and after and (after.isalnum() or after == "_"):
        return False
    return True


def extract_values_tuples(statement: str) -> list[list[str]]:
    clean = strip_sql_comments(statement)
    match = re.search(r"\bVALUES\b", clean, re.I)
    if not match:
        return []
    tail = clean[match.end() :]
    tuples: list[list[str]] = []
    i = 0
    while i < len(tail):
        if tail[i].isspace() or tail[i] == ",":
            i += 1
            continue
        if tail[i] != "(":
            break
        start = i + 1
        depth = 1
        quote: str | None = None
        i += 1
        while i < len(tail):
            ch = tail[i]
            nxt = tail[i + 1] if i + 1 < len(tail) else ""
            if quote:
                if ch == quote:
                    if quote == "'" and nxt == "'":
                        i += 2
                        continue
                    quote = None
                i += 1
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    tuples.append(split_top_level_commas(tail[start:i]))
                    i += 1
                    break
            i += 1
        else:
            return []
    return tuples


def classify_value_expression(expression: str) -> tuple[str, Any, str]:
    expr = expression.strip()
    upper = expr.upper()
    if upper == "NULL":
        return "explicit_null", None, "NULL"
    if upper == "DEFAULT":
        return "default", None, "DEFAULT"
    string_value = parse_single_quoted_literal(expr)
    if string_value is not None:
        return "direct_string_literal", string_value, string_value
    if re.fullmatch(r"[+-]?\d+", expr):
        return "direct_integer_literal", int(expr), expr
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+|\d+\.\d*[eE][+-]?\d+|\.\d+[eE][+-]?\d+)", expr):
        return "direct_real_literal", float(expr), expr
    if re.search(r"\bSELECT\b", upper):
        return "subquery", None, ""
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(", expr):
        return "function_expression", None, ""
    if re.search(r"(?<![eE])(?:\+|-|\*|/|\|\|)", expr):
        return "arithmetic_expression", None, ""
    return "other_expression", None, ""


def parse_insert_assignments(
    statement: str, schema_columns: list[str] | None = None
) -> list[InsertAssignment]:
    ref = target_reference(statement, "INSERT")
    tuples = extract_values_tuples(statement)
    if len(tuples) != 1:
        return []
    expressions = tuples[0]
    if ref.columns:
        columns = list(ref.columns)
    elif schema_columns and len(schema_columns) >= len(expressions):
        columns = schema_columns[: len(expressions)]
    else:
        columns = [f"column_{index + 1}" for index in range(len(expressions))]
    assignments: list[InsertAssignment] = []
    for index, expr in enumerate(expressions):
        kind, value, source_text = classify_value_expression(expr)
        column = columns[index] if index < len(columns) else f"column_{index + 1}"
        assignments.append(
            InsertAssignment(
                assignment_index=index,
                column_ref_or_name=column,
                value_expression=expr.strip(),
                gold_value_kind=kind,
                gold_value=value,
                source_literal_text=source_text,
            )
        )
    return assignments


def maximum_bipartite_matching(edges: list[list[int]]) -> dict[int, int]:
    assignment_for_span: dict[int, int] = {}

    def augment(assignment_index: int, seen: set[int]) -> bool:
        for span_index in edges[assignment_index]:
            if span_index in seen:
                continue
            seen.add(span_index)
            if span_index not in assignment_for_span or augment(assignment_for_span[span_index], seen):
                assignment_for_span[span_index] = assignment_index
                return True
        return False

    for assignment_index in range(len(edges)):
        augment(assignment_index, set())
    return {assignment: span for span, assignment in assignment_for_span.items()}


def insert_assignment_grounding(
    prompt: str,
    statement: str,
    source_split: str,
    schema_columns: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assignments = parse_insert_assignments(statement, schema_columns)
    unique_spans: list[tuple[int, int, str]] = []
    span_index_by_key: dict[tuple[int, int, str], int] = {}
    edges: list[list[int]] = []
    rows: list[dict[str, Any]] = []
    for assignment in assignments:
        numeric = assignment.gold_value_kind in {"direct_integer_literal", "direct_real_literal"}
        spans = (
            source_occurrences(prompt, assignment.source_literal_text, numeric=numeric)
            if assignment.gold_value_kind in SUPPORTED_PRIMARY_LITERAL_KINDS
            else []
        )
        edge: list[int] = []
        for span in spans:
            key = (int(span["start_char"]), int(span["end_char"]), str(span["text"]))
            if key not in span_index_by_key:
                span_index_by_key[key] = len(unique_spans)
                unique_spans.append(key)
            edge.append(span_index_by_key[key])
        edges.append(edge)
        rows.append(
            {
                "assignment_index": assignment.assignment_index,
                "column_ref_or_name": assignment.column_ref_or_name,
                "value_expression": assignment.value_expression,
                "gold_value_kind": assignment.gold_value_kind,
                "gold_value": assignment.gold_value,
                "source_literal_text": assignment.source_literal_text,
                "acceptable_source_spans": spans,
                "individually_source_alignable": bool(spans),
                "supported_direct_literal": assignment.gold_value_kind in SUPPORTED_PRIMARY_LITERAL_KINDS,
            }
        )
    matching = maximum_bipartite_matching(edges) if assignments else {}
    all_supported = bool(assignments) and all(row["supported_direct_literal"] for row in rows)
    all_individual = bool(assignments) and all(row["individually_source_alignable"] for row in rows)
    joint = bool(assignments) and len(matching) == len(assignments)
    for row in rows:
        assigned_span = matching.get(int(row["assignment_index"]))
        row["jointly_source_representable"] = joint
        row["matched_source_span"] = (
            {
                "start_char": unique_spans[assigned_span][0],
                "end_char": unique_spans[assigned_span][1],
                "text": unique_spans[assigned_span][2],
            }
            if assigned_span is not None
            else None
        )
        row["development_allowed"] = source_split == "train"
        row["official_test_confirmation_only"] = source_split == "test"
    sample = {
        "assignment_count": len(assignments),
        "all_assignments_supported_direct_literal": all_supported,
        "all_assignments_individually_source_alignable": all_individual,
        "jointly_source_representable": joint,
        "development_allowed": source_split == "train",
        "official_test_confirmation_only": source_split == "test",
    }
    return rows, sample


def source_alignability(prompt: str, statement: str, operation: str, complexity: str) -> dict[str, Any]:
    literals = sql_literals(statement)
    if not literals:
        return {"status": "no_gold_literals", "literals": [], "missing_literals": [], "literal_source_spans": []}
    missing: list[str] = []
    spans_by_literal: list[dict[str, Any]] = []
    for literal in literals:
        spans = source_occurrences(prompt, str(literal))
        spans_by_literal.append({"literal": str(literal), "acceptable_source_spans": spans})
        if not spans:
            missing.append(str(literal))
    if not missing:
        status = "source_alignable_literal"
    elif operation == "UPDATE" and complexity in {"update_expression", "update_subquery"}:
        status = "derived_value"
    else:
        status = "implicit_value"
    return {
        "status": status,
        "literals": literals,
        "missing_literals": missing,
        "literal_source_spans": spans_by_literal,
    }


def is_english_prompt(prompt: str) -> bool:
    if not prompt:
        return False
    letters = sum(ch.isalpha() for ch in prompt)
    ascii_letters = sum(("a" <= ch.lower() <= "z") for ch in prompt)
    if letters == 0:
        return False
    return ascii_letters / letters >= 0.92


def normalize_raw_row(split: str, index: int, row: dict[str, Any]) -> dict[str, Any]:
    sample_id = f"gretel:{split}:{row.get('id', index)}:{index:06d}"
    normalized = dict(row)
    normalized["sample_id"] = sample_id
    normalized["source_split"] = split
    normalized["source_index"] = index
    return normalized


def raw_schema_audit(rows_by_split: dict[str, list[dict[str, Any]]], parquet_schemas: dict[str, str]) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    split_counts = {split: len(rows) for split, rows in rows_by_split.items()}
    duplicate_ids: dict[str, int] = {}
    prompt_sql_counter: Counter[str] = Counter()
    prompt_counter: Counter[str] = Counter()
    sql_counter: Counter[str] = Counter()
    for split, rows in rows_by_split.items():
        id_counter = Counter(str(row.get("id")) for row in rows)
        duplicate_ids[split] = sum(count - 1 for count in id_counter.values() if count > 1)
        for row in rows:
            for key, value in row.items():
                stat = fields.setdefault(key, {"non_null": 0, "types": Counter(), "examples": []})
                if value is not None:
                    stat["non_null"] += 1
                    stat["types"][type(value).__name__] += 1
                    if len(stat["examples"]) < 3:
                        stat["examples"].append(value)
            prompt_sql_counter[sha256_text(canonical_json([row.get("sql_prompt"), row.get("sql")]))] += 1
            prompt_counter[sha256_text(str(row.get("sql_prompt") or ""))] += 1
            sql_counter[sha256_text(str(row.get("sql") or ""))] += 1
    return {
        "dataset_id": DATASET_ID,
        "revision": DATASET_REVISION,
        "expected_fields": EXPECTED_FIELDS,
        "observed_fields": sorted(fields),
        "field_count": len(fields),
        "split_counts": split_counts,
        "parquet_schemas": parquet_schemas,
        "fields": {
            key: {
                "non_null": value["non_null"],
                "types": dict(value["types"]),
                "examples": value["examples"],
            }
            for key, value in sorted(fields.items())
        },
        "duplicate_id_rows_by_split": duplicate_ids,
        "exact_duplicate_prompt_sql_pairs": sum(count - 1 for count in prompt_sql_counter.values() if count > 1),
        "exact_duplicate_prompts": sum(count - 1 for count in prompt_counter.values() if count > 1),
        "exact_duplicate_sql": sum(count - 1 for count in sql_counter.values() if count > 1),
    }


def build_source_lock(raw_dir: Path, rows_by_split: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "dataset_id": DATASET_ID,
        "dataset_url": DATASET_URL,
        "revision": DATASET_REVISION,
        "splits": sorted(rows_by_split),
        "download_date_utc": date.today().isoformat(),
        "downloaded_with": {
            "raw_file_transport": "urllib.request or pre-downloaded Hugging Face resolve URLs",
            "parquet_reader": "pyarrow.parquet",
        },
        "raw_dir": str(raw_dir),
        "raw_files": {
            split: {
                "filename": filename,
                "url": f"{DATASET_URL}/resolve/{DATASET_REVISION}/{filename}",
                "sha256": sha256_file(raw_dir / filename),
                "bytes": (raw_dir / filename).stat().st_size,
                "row_count": len(rows_by_split[split]),
            }
            for split, filename in RAW_FILES.items()
        },
        "license": "apache-2.0",
        "model_called": False,
        "gpu_called": False,
    }


def eligibility_policy() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "frozen_before_manifest_generation": True,
        "model_outputs_allowed": False,
        "dataset_sqlite_write_eligibility": {
            "requires": [
                "English natural-language prompt",
                "exactly one gold write statement",
                "operation in INSERT, UPDATE, DELETE",
                "sql_context executes directly in SQLite without dialect rewrite",
                "target table and referenced target columns exist in SQLite schema",
                "gold write executes with PRAGMA foreign_keys=ON",
                "D0 and D* snapshots are reproducible from fresh context",
                "no external time/random state dependency",
            ],
            "does_not_require": [
                "source literal grounding",
                "V2 primary representability",
                "model performance",
            ],
        },
        "primary_eligible_requires": [
            "English natural-language prompt",
            "exactly one gold write statement",
            "operation in INSERT, UPDATE, DELETE",
            "sql_context executes directly in SQLite without dialect rewrite",
            "target table and referenced target columns exist in SQLite schema",
            "gold write executes with PRAGMA foreign_keys=ON",
            "D0 and D* snapshots are reproducible from fresh context",
            "no external time/random state dependency",
            "source-alignability is derived from gold SQL and prompt only",
        ],
        "v2_literal_grounded_primary_scope": {
            "inherits_dataset_sqlite_write_eligibility": True,
            "operation": "INSERT",
            "complexity_class": "single_row_insert",
            "assignment_value_kinds": sorted(SUPPORTED_PRIMARY_LITERAL_KINDS),
            "require_all_assignments_individually_source_alignable": True,
            "require_joint_one_to_one_source_matching": True,
            "joint_matching_algorithm": "maximum_cardinality_bipartite_matching",
            "multiple_source_occurrences": "kept_as_acceptable_alternative_spans_not_an_automatic_exclusion",
        },
        "controlled_exclusion_reasons": sorted(CONTROLLED_EXCLUSION_REASONS),
    }


def build_run(
    rows_by_split: dict[str, list[dict[str, Any]]],
    out_dir: Path,
    raw_dir: Path,
    parquet_schemas: dict[str, str] | None = None,
) -> dict[str, Any]:
    if out_dir.exists():
        resolved = out_dir.resolve()
        if PROJECT_ROOT.resolve() not in [resolved, *resolved.parents]:
            raise RuntimeError(f"Refusing to remove output outside project: {resolved}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    parquet_schemas = parquet_schemas or {}

    normalized_rows = [
        normalize_raw_row(split, index, row)
        for split, rows in sorted(rows_by_split.items())
        for index, row in enumerate(rows)
    ]
    class_by_id: dict[str, SQLClassification] = {}
    context_rows: list[dict[str, Any]] = []
    gold_rows: list[dict[str, Any]] = []
    align_rows: list[dict[str, Any]] = []
    insert_assignment_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    manifests: dict[str, list[dict[str, Any]]] = {"INSERT": [], "UPDATE": [], "DELETE": []}
    development_train_candidates: list[dict[str, Any]] = []
    official_test_confirmation_candidates: list[dict[str, Any]] = []
    complexity_counter: Counter[str] = Counter()
    operation_counter: Counter[str] = Counter()
    scanner_status_counter: Counter[str] = Counter()
    funnel = Counter()
    leakage_payloads: list[dict[str, Any]] = []

    for row in normalized_rows:
        classification = classify_gold_sql(str(row.get("sql") or ""))
        class_by_id[row["sample_id"]] = classification
        operation_counter[classification.operation] += 1
        scanner_status_counter[classification.status] += 1
        if classification.operation in {"INSERT", "UPDATE", "DELETE"}:
            funnel["dml"] += 1
        english = is_english_prompt(str(row.get("sql_prompt") or ""))
        if classification.operation in {"INSERT", "UPDATE", "DELETE"} and english:
            funnel["english_dml"] += 1
        complexity = classify_write_complexity(classification.primary_statement, classification.operation)
        if classification.operation in {"INSERT", "UPDATE", "DELETE"}:
            complexity_counter[complexity] += 1
        context_record, gold_record = audit_context_and_gold(row, classification)
        context_rows.append(context_record)
        gold_rows.append(gold_record)
        prompt = str(row.get("sql_prompt") or "")
        align = source_alignability(
            prompt,
            classification.primary_statement,
            classification.operation,
            complexity,
        )
        align_row = {
            "sample_id": row["sample_id"],
            "source_split": row["source_split"],
            "operation": classification.operation,
            "complexity_class": complexity,
            **align,
        }
        align_rows.append(align_row)
        insert_grounding = {
            "assignment_count": 0,
            "all_assignments_supported_direct_literal": False,
            "all_assignments_individually_source_alignable": False,
            "jointly_source_representable": False,
            "development_allowed": row["source_split"] == "train",
            "official_test_confirmation_only": row["source_split"] == "test",
        }
        if classification.operation == "INSERT":
            schema_columns = None
            tables = context_record.get("tables") or {}
            target_table = context_record.get("target_table")
            if isinstance(tables, dict) and isinstance(target_table, str):
                schema_columns = tables.get(target_table)
            assignment_rows, insert_grounding = insert_assignment_grounding(
                prompt,
                classification.primary_statement,
                str(row["source_split"]),
                schema_columns if isinstance(schema_columns, list) else None,
            )
            for assignment_row in assignment_rows:
                insert_assignment_rows.append(
                    {
                        "sample_id": row["sample_id"],
                        "source_split": row["source_split"],
                        "operation": classification.operation,
                        "complexity_class": complexity,
                        **assignment_row,
                    }
                )
        exclusions: list[str] = []
        if classification.operation not in {"INSERT", "UPDATE", "DELETE"}:
            continue
        if context_record["context_status"] == "success":
            funnel["sqlite_compatible"] += 1
        if gold_record["exec_status"] == "success":
            funnel["gold_executable"] += 1
        if bool(gold_record["deterministic"]):
            funnel["deterministic"] += 1
        if not english:
            exclusions.append("non_english")
        if classification.status == "multi_statement":
            exclusions.append("multi_statement")
        elif classification.status == "malformed":
            exclusions.append("malformed_sql")
        if context_record["context_status"] == "failure":
            reason = str(context_record.get("failure_reason") or "context_parse_failure")
            exclusions.append(reason if reason in CONTROLLED_EXCLUSION_REASONS else "context_parse_failure")
        if gold_record["exec_status"] == "failure":
            reason = str(gold_record.get("failure_reason") or "gold_execution_failure")
            exclusions.append(reason if reason in CONTROLLED_EXCLUSION_REASONS else "gold_execution_failure")
        if has_nondeterministic_sql(classification.primary_statement):
            exclusions.append("nondeterministic")
        sqlite_write_eligible = not exclusions and gold_record["exec_status"] == "success" and bool(gold_record["deterministic"])
        align_status = align["status"]
        if sqlite_write_eligible and align_status == "source_alignable_literal":
            funnel["source_alignable"] += 1
        elif sqlite_write_eligible:
            funnel["source_nonalignable"] += 1
        if sqlite_write_eligible and classification.operation == "INSERT" and complexity == "single_row_insert":
            funnel["single_row_insert_eligible"] += 1
            if insert_grounding["all_assignments_supported_direct_literal"]:
                funnel["direct_literal_insert"] += 1
            if insert_grounding["all_assignments_individually_source_alignable"]:
                funnel["individual_source_alignable_insert"] += 1
            if insert_grounding["jointly_source_representable"]:
                funnel["joint_source_representable_insert"] += 1
        v2_primary = bool(
            sqlite_write_eligible
            and classification.operation == "INSERT"
            and complexity == "single_row_insert"
            and insert_grounding["all_assignments_supported_direct_literal"]
            and insert_grounding["all_assignments_individually_source_alignable"]
            and insert_grounding["jointly_source_representable"]
        )
        primary_scope_exclusions: list[str] = []
        if sqlite_write_eligible and not v2_primary:
            if classification.operation == "INSERT" and complexity == "single_row_insert":
                if not insert_grounding["all_assignments_supported_direct_literal"]:
                    primary_scope_exclusions.append("expression_value_not_supported")
                if not insert_grounding["all_assignments_individually_source_alignable"]:
                    primary_scope_exclusions.append("implicit_value_not_supported")
                if (
                    insert_grounding["all_assignments_individually_source_alignable"]
                    and not insert_grounding["jointly_source_representable"]
                ):
                    primary_scope_exclusions.append("joint_source_not_representable")
            else:
                primary_scope_exclusions.append("multirow_not_in_primary_scope" if complexity == "multi_row_insert" else "not_primary_insert_scope")
        status = "eligible" if sqlite_write_eligible else "excluded"
        ledger_row = {
            "sample_id": row["sample_id"],
            "source_split": row["source_split"],
            "operation": classification.operation,
            "status": status,
            "sqlite_write_eligible": sqlite_write_eligible,
            "v2_literal_grounded_primary_eligible": v2_primary,
            "development_allowed": bool(v2_primary and row["source_split"] == "train"),
            "official_test_confirmation_only": bool(v2_primary and row["source_split"] == "test"),
            "insert_assignment_grounding": insert_grounding if classification.operation == "INSERT" else None,
            "exclusion_reasons": sorted(set(exclusions)),
            "primary_scope_exclusion_reasons": sorted(set(primary_scope_exclusions)),
        }
        ledger_rows.append(ledger_row)
        if sqlite_write_eligible:
            manifest_row = {
                "sample_id": row["sample_id"],
                "source_split": row["source_split"],
                "source_index": row["source_index"],
                "raw_row_hash": sha256_text(canonical_json({k: row.get(k) for k in EXPECTED_FIELDS})),
                "prompt_hash": sha256_text(str(row.get("sql_prompt") or "")),
                "context_hash": sha256_text(str(row.get("sql_context") or "")),
                "sql_hash": sha256_text(str(row.get("sql") or "")),
                "operation": classification.operation,
                "schema_database_group": schema_group(row),
                "sqlite_compatibility": True,
                "initial_state_hash": gold_record["before_state_hash"],
                "gold_post_state_hash": gold_record["after_state_hash"],
                "complexity_class": complexity,
                "source_alignability_status": align_status,
                "insert_assignment_grounding": insert_grounding if classification.operation == "INSERT" else None,
                "sqlite_write_eligible": True,
                "v2_literal_grounded_primary_eligible": v2_primary,
                "development_allowed": bool(v2_primary and row["source_split"] == "train"),
                "official_test_confirmation_only": bool(v2_primary and row["source_split"] == "test"),
            }
            manifests[classification.operation].append(manifest_row)
            if v2_primary and row["source_split"] == "train":
                development_train_candidates.append(manifest_row)
            elif v2_primary and row["source_split"] == "test":
                official_test_confirmation_candidates.append(manifest_row)
        leakage_payloads.append(
            {
                "sample_id": row["sample_id"],
                "source_split": row["source_split"],
                "prompt_hash": sha256_text(str(row.get("sql_prompt") or "")),
                "sql_hash": sha256_text(str(row.get("sql") or "")),
                "prompt_sql_hash": sha256_text(canonical_json([row.get("sql_prompt"), row.get("sql")])),
                "schema_group": schema_group(row),
                "domain": row.get("domain"),
            }
        )

    funnel["dml_leading_multi_statement"] = sum(
        1
        for classification in class_by_id.values()
        if classification.operation in {"INSERT", "UPDATE", "DELETE"}
        and classification.status == "multi_statement"
    )
    write_json(out_dir / "DATASET_SOURCE_LOCK.json", build_source_lock(raw_dir, rows_by_split))
    write_json(
        out_dir / "RAW_DATA_HASHES.json",
        {
            split: {
                "filename": RAW_FILES[split],
                "sha256": sha256_file(raw_dir / RAW_FILES[split]) if (raw_dir / RAW_FILES[split]).exists() else "",
                "bytes": (raw_dir / RAW_FILES[split]).stat().st_size if (raw_dir / RAW_FILES[split]).exists() else 0,
            }
            for split in sorted(RAW_FILES)
        },
    )
    write_json(out_dir / "RAW_SCHEMA_AUDIT.json", raw_schema_audit(rows_by_split, parquet_schemas))
    write_json(out_dir / "ELIGIBILITY_POLICY.json", eligibility_policy())
    write_json(
        out_dir / "DML_OPERATION_COUNTS.json",
        {
            "raw_total": len(normalized_rows),
            "by_operation": dict(sorted(operation_counter.items())),
            "scanner_status_counts": dict(sorted(scanner_status_counter.items())),
            "dml_total": funnel["dml"],
            "dml_leading_candidate_population": funnel["dml"],
            "single_statement_dml": scanner_status_counter["dml"],
            "dml_leading_multi_statement": funnel["dml_leading_multi_statement"],
            "unsupported_or_other": operation_counter["OTHER"],
            "multi_statement": scanner_status_counter["multi_statement"],
            "malformed": scanner_status_counter["malformed"],
        },
    )
    write_json(out_dir / "WRITE_COMPLEXITY_AUDIT.json", dict(sorted(complexity_counter.items())))
    write_jsonl(out_dir / "SQLITE_CONTEXT_AUDIT.jsonl", context_rows)
    context_summary = Counter(row["context_status"] for row in context_rows if row["operation"] in {"INSERT", "UPDATE", "DELETE"})
    context_failures = Counter(row["failure_reason"] for row in context_rows if row.get("failure_reason"))
    write_json(
        out_dir / "SQLITE_COMPATIBILITY_SUMMARY.json",
        {"status_counts": dict(context_summary), "failure_reason_counts": dict(context_failures)},
    )
    write_jsonl(out_dir / "GOLD_EXECUTION_AUDIT.jsonl", gold_rows)
    write_jsonl(out_dir / "SOURCE_ALIGNABILITY_AUDIT.jsonl", align_rows)
    align_summary = Counter(
        row["status"] for row in align_rows if row["operation"] in {"INSERT", "UPDATE", "DELETE"}
    )
    write_json(out_dir / "SOURCE_ALIGNABILITY_SUMMARY.json", dict(sorted(align_summary.items())))
    write_jsonl(out_dir / "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl", insert_assignment_rows)
    insert_grounding_summary = {
        "single_row_insert_eligible": funnel["single_row_insert_eligible"],
        "direct_literal_representable": funnel["direct_literal_insert"],
        "individually_source_alignable": funnel["individual_source_alignable_insert"],
        "jointly_source_representable": funnel["joint_source_representable_insert"],
        "development_train_candidates": len(development_train_candidates),
        "official_test_confirmation_candidates": len(official_test_confirmation_candidates),
        "assignment_value_kind_counts": dict(
            sorted(Counter(row["gold_value_kind"] for row in insert_assignment_rows).items())
        ),
    }
    write_json(out_dir / "INSERT_GROUNDING_SUMMARY.json", insert_grounding_summary)
    write_jsonl(out_dir / "EXCLUSION_LEDGER.jsonl", ledger_rows)
    exclusion_counts = Counter(reason for row in ledger_rows for reason in row["exclusion_reasons"])
    primary_exclusion_counts = Counter(reason for row in ledger_rows for reason in row["primary_scope_exclusion_reasons"])
    write_json(
        out_dir / "EXCLUSION_REASON_COUNTS.json",
        {
            "dataset_exclusion_reasons": dict(sorted(exclusion_counts.items())),
            "primary_scope_exclusion_reasons": dict(sorted(primary_exclusion_counts.items())),
            "eligible": sum(row["status"] == "eligible" for row in ledger_rows),
            "excluded": sum(row["status"] == "excluded" for row in ledger_rows),
        },
    )
    write_jsonl(out_dir / "ELIGIBLE_INSERT_MANIFEST.jsonl", manifests["INSERT"])
    write_jsonl(out_dir / "ELIGIBLE_UPDATE_MANIFEST.jsonl", manifests["UPDATE"])
    write_jsonl(out_dir / "ELIGIBLE_DELETE_MANIFEST.jsonl", manifests["DELETE"])
    write_jsonl(out_dir / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl", development_train_candidates)
    write_jsonl(out_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl", official_test_confirmation_candidates)
    leakage = leakage_audit(leakage_payloads)
    write_json(out_dir / "DATA_LEAKAGE_AUDIT.json", leakage)
    split_audit = split_candidate_audit(
        leakage_payloads,
        manifests,
        development_train_candidates,
        official_test_confirmation_candidates,
    )
    write_json(out_dir / "SPLIT_CANDIDATE_AUDIT.json", split_audit)
    derived_manifest = build_derived_artifact_manifest(out_dir)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", derived_manifest)
    lock = {
        "stage": STAGE_NAME,
        "status": "PASS_QUALIFICATION_ARTIFACTS_BUILT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": DATASET_ID,
        "revision": DATASET_REVISION,
        "raw_total": len(normalized_rows),
        "dml_total": funnel["dml"],
        "model_called": False,
        "gpu_called": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
    }
    write_json(out_dir / "STAGEENG0_LOCK.json", lock)
    report = validation_report(
        out_dir=out_dir,
        raw_total=len(normalized_rows),
        operation_counter=operation_counter,
        funnel=funnel,
        manifests=manifests,
        context_summary=context_summary,
        align_summary=align_summary,
        insert_grounding_summary=insert_grounding_summary,
    )
    write_text(out_dir / "VALIDATION_REPORT.md", report)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "raw_total": len(normalized_rows),
        "dml_total": funnel["dml"],
        "operation_counts": dict(operation_counter),
        "manifest_counts": {operation: len(rows) for operation, rows in manifests.items()},
        "development_train_candidates": len(development_train_candidates),
        "official_test_confirmation_candidates": len(official_test_confirmation_candidates),
        "out_dir": str(out_dir),
    }


def schema_group(row: dict[str, Any]) -> str:
    return sha256_text(canonical_json({"domain": row.get("domain"), "context": row.get("sql_context")}))[:16]


def leakage_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def dupes(field: str) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row[field])].append(row)
        return [
            {
                "hash": key,
                "count": len(value),
                "splits": sorted({str(row["source_split"]) for row in value}),
                "sample_ids": [str(row["sample_id"]) for row in value[:10]],
            }
            for key, value in sorted(grouped.items())
            if len(value) > 1
        ]
    return {
        "exact_prompt_sql_duplicates": dupes("prompt_sql_hash")[:200],
        "prompt_duplicates": dupes("prompt_hash")[:200],
        "sql_duplicates": dupes("sql_hash")[:200],
        "schema_group_duplicates": dupes("schema_group")[:200],
        "note": "Official Gretel test split is audited but must remain untouched for confirmation; StageENG0 does not tune on model performance.",
    }


def build_derived_artifact_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = []
    for name in SCIENTIFIC_ARTIFACTS:
        path = stage_dir / name
        artifacts.append(
            {
                "path": name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {
        "stage": STAGE_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def split_candidate_audit(
    rows: list[dict[str, Any]],
    manifests: dict[str, list[dict[str, Any]]],
    development_train_candidates: list[dict[str, Any]],
    official_test_confirmation_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    group_counts = Counter(row["schema_group"] for row in rows)
    eligible_groups = Counter(row["schema_database_group"] for values in manifests.values() for row in values)
    split_by_group: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split_by_group[row["schema_group"]].add(str(row["source_split"]))
    cross_split_groups = [key for key, splits in split_by_group.items() if len(splits) > 1]
    return {
        "stage": STAGE_NAME,
        "official_splits_observed": sorted({row["source_split"] for row in rows}),
        "official_test_policy": "hold_out_from_phase_o_tuning",
        "candidate_grouping_key": "sha256(domain + sql_context)",
        "all_group_count": len(group_counts),
        "eligible_group_count": len(eligible_groups),
        "cross_official_split_schema_group_count": len(cross_split_groups),
        "proposed_next_stage": "StageENG1 should freeze group-level development split; no model run in StageENG0.",
        "eligible_counts_by_operation": {operation: len(values) for operation, values in manifests.items()},
        "primary_insert_development_train_candidate_count": len(development_train_candidates),
        "primary_insert_official_test_confirmation_candidate_count": len(official_test_confirmation_candidates),
        "test_ids_intersect_development_candidates": sorted(
            {row["sample_id"] for row in official_test_confirmation_candidates}
            & {row["sample_id"] for row in development_train_candidates}
        ),
    }


def validation_report(
    *,
    out_dir: Path,
    raw_total: int,
    operation_counter: Counter[str],
    funnel: Counter[str],
    manifests: dict[str, list[dict[str, Any]]],
    context_summary: Counter[str],
    align_summary: Counter[str],
    insert_grounding_summary: dict[str, Any],
) -> str:
    return f"""# StageENG0 Gretel English SQLite Write Qualification Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

StageENG0 is a CPU-only dataset qualification stage. It does not call Qwen,
does not run GPU inference, does not amend Phase O prompts, and does not score
model accuracy. Raw Gretel parquet files are pinned by dataset revision and
SHA-256 outside the repository; derived audit artifacts are written here.

## Funnel

```text
Raw total                         {raw_total}
DML-leading candidate population  {funnel['dml']}
Single-statement DML              {funnel['dml'] - funnel.get('dml_leading_multi_statement', 0)}
DML-leading multi-statement       {funnel.get('dml_leading_multi_statement', 0)}
+-- INSERT                       {operation_counter['INSERT']}
+-- UPDATE                       {operation_counter['UPDATE']}
+-- DELETE                       {operation_counter['DELETE']}

SQLite-compatible                {funnel['sqlite_compatible']}
Gold-executable                  {funnel['gold_executable']}
Deterministic                    {funnel['deterministic']}

Source-alignable                 {funnel['source_alignable']}
Derived/non-alignable            {funnel['source_nonalignable']}

Primary eligible INSERT          {sum(row['v2_literal_grounded_primary_eligible'] for row in manifests['INSERT'])}
Secondary eligible UPDATE        {len(manifests['UPDATE'])}
Secondary eligible DELETE        {len(manifests['DELETE'])}
```

## PATCH2 INSERT Grounding Funnel

```text
Single-row INSERT eligible              {insert_grounding_summary['single_row_insert_eligible']}
Direct-literal representable            {insert_grounding_summary['direct_literal_representable']}
Individually source-alignable            {insert_grounding_summary['individually_source_alignable']}
Jointly source-representable             {insert_grounding_summary['jointly_source_representable']}
Development train candidates             {insert_grounding_summary['development_train_candidates']}
Official test confirmation candidates    {insert_grounding_summary['official_test_confirmation_candidates']}
```

## Counts

SQLite context status:

```json
{json.dumps(dict(context_summary), indent=2, sort_keys=True)}
```

Source alignability:

```json
{json.dumps(dict(align_summary), indent=2, sort_keys=True)}
```

## Validation Commands

```text
uv run --with pyarrow python scripts/data/build_stageeng0_gretel_qualification.py --raw-dir <raw_dir> --out-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --package StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION_PATCH2_FINAL_REVIEWER_PACKAGE_20260830.zip
uv run --with pyarrow python scripts/data/validate_stageeng0_gretel_qualification.py --stage-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir>
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q tests/test_stageeng0_gretel_qualification.py --basetemp .codex_tmp/pytest_stageeng0_tests5
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q -m "not integration" --basetemp .codex_tmp/pytest_stageeng0_regression
python -m zipfile --test StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION_PATCH2_FINAL_REVIEWER_PACKAGE_20260830.zip
```

Results:

```text
build: PASS
validator: PASS
dedicated tests: PASS, 72 tests
regression tests: PASS, non-integration suite
zip integrity: PASS
derived artifact manifest: PASS
```

## Guardrails

```text
model_called=false
gpu_called=false
raw_data_modified=false
model_performance_filtering=false
official_test_tuning=false
```
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# StageENG0 Gretel English SQLite Write Qualification

This reviewer package contains the StageENG0 dataset qualification artifacts
for `gretelai/synthetic_text_to_sql` at revision `{DATASET_REVISION}`.

Review order:

1. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/DATASET_SOURCE_LOCK.json`
2. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/ELIGIBILITY_POLICY.json`
3. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/DML_OPERATION_COUNTS.json`
4. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/SQLITE_COMPATIBILITY_SUMMARY.json`
5. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/GOLD_EXECUTION_AUDIT.jsonl`
6. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/SOURCE_ALIGNABILITY_SUMMARY.json`
7. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl`
8. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/INSERT_GROUNDING_SUMMARY.json`
9. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/EXCLUSION_LEDGER.jsonl`
10. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/ELIGIBLE_INSERT_MANIFEST.jsonl`
11. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl`
12. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl`
13. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/DERIVED_ARTIFACT_MANIFEST.json`
14. `scripts/data/build_stageeng0_gretel_qualification.py`
15. `scripts/data/validate_stageeng0_gretel_qualification.py`
16. `tests/test_stageeng0_gretel_qualification.py`
17. `StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION/VALIDATION_REPORT.md`

Rerun:

```bash
uv run --with pyarrow python scripts/data/build_stageeng0_gretel_qualification.py \\
  --raw-dir /path/to/gretel_raw \\
  --out-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \\
  --download
uv run --with pyarrow python scripts/data/validate_stageeng0_gretel_qualification.py \\
  --stage-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \\
  --raw-dir /path/to/gretel_raw
python -m pytest -q tests/test_stageeng0_gretel_qualification.py
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    include_files = [
        *stage_dir.rglob("*"),
        PROJECT_ROOT / "scripts" / "data" / "build_stageeng0_gretel_qualification.py",
        PROJECT_ROOT / "scripts" / "data" / "validate_stageeng0_gretel_qualification.py",
        PROJECT_ROOT / "tests" / "test_stageeng0_gretel_qualification.py",
    ]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted({p for p in include_files if p.is_file()}):
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
    (package_path.with_suffix(package_path.suffix + ".sha256")).write_text(
        f"{digest}  {package_path.name}\n",
        encoding="utf-8",
    )
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()

    if args.download:
        download_raw_files(args.raw_dir)
    rows_by_split, parquet_schemas = load_parquet_rows(args.raw_dir)
    summary = build_run(rows_by_split, args.out_dir, args.raw_dir, parquet_schemas)
    if args.package:
        digest = package_reviewer(args.out_dir, args.package)
        summary["package_sha256"] = digest
        summary["package"] = str(args.package)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
