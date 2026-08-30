from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.data.build_stageeng0_gretel_qualification import (
    RAW_FILES,
    build_run,
    classify_gold_sql,
    classify_write_complexity,
    has_nondeterministic_sql,
    source_alignability,
    sql_literals,
    sql_statements,
    target_reference,
)
from scripts.data.validate_stageeng0_gretel_qualification import validate


@pytest.mark.parametrize(
    ("sql", "operation", "status", "statement_count"),
    [
        ("INSERT INTO people(name) VALUES ('Ann');", "INSERT", "dml", 1),
        ("  -- ignore\nUPDATE people SET age = 2 WHERE name = 'Ann'", "UPDATE", "dml", 1),
        ("/* ignore DELETE */ DELETE FROM people WHERE id = 1", "DELETE", "dml", 1),
        ("SELECT 'insert into t values (1)'", "OTHER", "other", 1),
        ("INSERT INTO t VALUES ('a; b')", "INSERT", "dml", 1),
        ("INSERT INTO t VALUES (1); UPDATE t SET a=2", "INSERT", "multi_statement", 2),
        ("", "OTHER", "malformed", 0),
        ("/* unterminated", "OTHER", "malformed", 0),
        ("CREATE TABLE t(id int);", "OTHER", "other", 1),
        ("DELETE FROM logs WHERE note='semi;colon';", "DELETE", "dml", 1),
        ("-- comment with insert\nSELECT 1", "OTHER", "other", 1),
        ("`insert`", "OTHER", "malformed", 1),
    ],
)
def test_sql_classifier_is_lexical(sql: str, operation: str, status: str, statement_count: int) -> None:
    result = classify_gold_sql(sql)
    assert result.operation == operation
    assert result.status == status
    assert result.statement_count == statement_count


def test_statement_splitter_ignores_semicolons_inside_literals_and_comments() -> None:
    sql = "INSERT INTO t VALUES ('a;b'); -- ;\nDELETE FROM t WHERE note='c;d';"

    assert sql_statements(sql) == [
        "INSERT INTO t VALUES ('a;b')",
        "DELETE FROM t WHERE note='c;d')".removesuffix(")"),
    ]


@pytest.mark.parametrize(
    ("statement", "operation", "table", "columns"),
    [
        ("INSERT INTO people(name, age) VALUES ('Ann', 2)", "INSERT", "people", ("name", "age")),
        ('INSERT INTO "order" ("id", "desc") VALUES (1, "x")', "INSERT", "order", ("id", "desc")),
        ("UPDATE people SET age = 3, name = 'Bob' WHERE id = 1", "UPDATE", "people", ("age", "name")),
        ("UPDATE OR REPLACE [people] SET [name] = 'Bob'", "UPDATE", "people", ("name",)),
        ("DELETE FROM people WHERE id = 1", "DELETE", "people", ()),
        ("DELETE FROM `audit_log`", "DELETE", "audit_log", ()),
    ],
)
def test_target_reference_extracts_write_target(
    statement: str, operation: str, table: str, columns: tuple[str, ...]
) -> None:
    ref = target_reference(statement, operation)
    assert ref.table == table
    assert ref.columns == columns


@pytest.mark.parametrize(
    ("statement", "operation", "expected"),
    [
        ("INSERT INTO t(a) VALUES (1)", "INSERT", "single_row_insert"),
        ("INSERT INTO t(a) VALUES (1), (2)", "INSERT", "multi_row_insert"),
        ("INSERT INTO t(a) SELECT a FROM s", "INSERT", "insert_select"),
        ("UPDATE t SET a = 1 WHERE id = 2", "UPDATE", "simple_update_literal"),
        ("UPDATE t SET a = a + 1 WHERE id = 2", "UPDATE", "update_expression"),
        ("UPDATE t SET a = (SELECT max(a) FROM s)", "UPDATE", "update_subquery"),
        ("DELETE FROM t WHERE id = 2", "DELETE", "simple_delete_predicate"),
        ("DELETE FROM t WHERE id IN (SELECT id FROM s)", "DELETE", "complex_delete"),
        ("DELETE FROM t", "DELETE", "delete_all_rows"),
    ],
)
def test_write_complexity_classes(statement: str, operation: str, expected: str) -> None:
    assert classify_write_complexity(statement, operation) == expected


@pytest.mark.parametrize(
    ("prompt", "statement", "operation", "complexity", "expected"),
    [
        ("Add Bob age 25.", "INSERT INTO people(name, age) VALUES ('Bob', 25)", "INSERT", "single_row_insert", "source_alignable_literal"),
        ("Set Bob age to 25.", "UPDATE people SET age = 25 WHERE name = 'Bob'", "UPDATE", "simple_update_literal", "source_alignable_literal"),
        ("Increase salary by 10 percent.", "UPDATE people SET salary = salary * 1.1", "UPDATE", "update_expression", "derived_value"),
        ("Add the default row.", "INSERT INTO t(flag) VALUES (1)", "INSERT", "single_row_insert", "implicit_value"),
        ("Delete Bob Bob duplicate.", "DELETE FROM people WHERE name = 'Bob'", "DELETE", "simple_delete_predicate", "ambiguous_multiple_occurrences"),
    ],
)
def test_source_alignability_is_deterministic(
    prompt: str, statement: str, operation: str, complexity: str, expected: str
) -> None:
    assert source_alignability(prompt, statement, operation, complexity)["status"] == expected


def test_sql_literals_skip_comments_and_unescape_strings() -> None:
    assert sql_literals("-- 'hidden' 7\nINSERT INTO t VALUES ('Bob''s', -2.5)") == ["Bob's", "-2.5"]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO t(ts) VALUES (CURRENT_TIMESTAMP)",
        "UPDATE t SET token = random()",
        "INSERT INTO t(x) VALUES (randomblob(4))",
    ],
)
def test_nondeterministic_tokens_are_detected(statement: str) -> None:
    assert has_nondeterministic_sql(statement)


def _raw_dir(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    for filename in RAW_FILES.values():
        (raw / filename).write_bytes(b"synthetic parquet placeholder for tests")
    return raw


def _row(
    sample_id: int,
    prompt: str,
    context: str,
    sql: str,
    task_type: str = "data manipulation",
) -> dict[str, object]:
    return {
        "id": sample_id,
        "domain": "unit test",
        "domain_description": "Synthetic rows for tests.",
        "sql_complexity": "basic SQL",
        "sql_complexity_description": "basic SQL",
        "sql_task_type": task_type,
        "sql_task_type_description": "inserting, updating, or deleting records",
        "sql_prompt": prompt,
        "sql_context": context,
        "sql": sql,
        "sql_explanation": "test",
    }


def test_build_run_writes_manifests_and_validator_passes(tmp_path: Path) -> None:
    context = "CREATE TABLE people(id INT, name TEXT, age INT); INSERT INTO people VALUES (1, 'Ann', 20);"
    rows = {
        "train": [
            _row(1, "Add Bob age 25.", context, "INSERT INTO people(id, name, age) VALUES (2, 'Bob', 25);"),
            _row(2, "Set Ann age to 21.", context, "UPDATE people SET age = 21 WHERE name = 'Ann';"),
            _row(3, "Delete Ann.", context, "DELETE FROM people WHERE name = 'Ann';"),
            _row(4, "Find Ann.", context, "SELECT * FROM people WHERE name = 'Ann';", "analytics and reporting"),
        ],
        "test": [
            _row(5, "Add Cara.", context, "INSERT INTO people(name) VALUES ('Cara'); INSERT INTO people(name) VALUES ('Dana');"),
            _row(6, "Add Moe.", "CREATE TABLE pets(id INT);", "INSERT INTO people(name) VALUES ('Moe');"),
            _row(7, "Add now.", context, "INSERT INTO people(name) VALUES (CURRENT_TIMESTAMP);"),
        ],
    }

    summary = build_run(rows, tmp_path / "stage", _raw_dir(tmp_path), {"train": "fixture", "test": "fixture"})
    result = validate(tmp_path / "stage", tmp_path / "raw")

    assert result["status"] == "PASS"
    assert summary["manifest_counts"] == {"INSERT": 1, "UPDATE": 1, "DELETE": 1}
    assert (tmp_path / "stage" / "ELIGIBLE_INSERT_MANIFEST.jsonl").is_file()


def test_exclusion_ledger_records_all_dml_failures(tmp_path: Path) -> None:
    context = "CREATE TABLE people(id INT, name TEXT);"
    rows = {
        "train": [
            _row(1, "Add Bob.", context, "INSERT INTO missing(name) VALUES ('Bob');"),
            _row(2, "Add now.", context, "INSERT INTO people(name) VALUES (CURRENT_TIMESTAMP);"),
        ],
        "test": [],
    }

    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    ledger = [
        json.loads(line)
        for line in (tmp_path / "stage" / "EXCLUSION_LEDGER.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(ledger) == 2
    reasons = {reason for row in ledger for reason in row["exclusion_reasons"]}
    assert {"missing_table", "nondeterministic"} <= reasons


def test_validator_rejects_mutated_raw_hash(tmp_path: Path) -> None:
    context = "CREATE TABLE people(name TEXT);"
    rows = {"train": [_row(1, "Add Bob.", context, "INSERT INTO people(name) VALUES ('Bob');")], "test": []}

    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    (tmp_path / "raw" / RAW_FILES["train"]).write_bytes(b"mutated")

    assert validate(tmp_path / "stage", tmp_path / "raw")["status"] == "FAIL"
