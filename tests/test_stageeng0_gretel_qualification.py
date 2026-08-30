from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.data.build_stageeng0_gretel_qualification import (
    RAW_FILES,
    build_run,
    classify_gold_sql,
    classify_write_complexity,
    eligibility_policy,
    has_nondeterministic_sql,
    insert_assignment_grounding,
    parse_insert_assignments,
    source_occurrences,
    source_alignability,
    sql_literals,
    sql_statements,
    target_reference,
)
from scripts.data.validate_stageeng0_gretel_qualification import expected_primary_insert, validate


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
        ("Delete Bob Bob duplicate.", "DELETE FROM people WHERE name = 'Bob'", "DELETE", "simple_delete_predicate", "source_alignable_literal"),
    ],
)
def test_source_alignability_is_deterministic(
    prompt: str, statement: str, operation: str, complexity: str, expected: str
) -> None:
    assert source_alignability(prompt, statement, operation, complexity)["status"] == expected


def test_sql_literals_skip_comments_and_unescape_strings() -> None:
    assert sql_literals("-- 'hidden' 7\nINSERT INTO t VALUES ('Bob''s', -2.5)") == ["Bob's", "-2.5"]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("INSERT INTO table_2024 (col1, name) VALUES (10, 'Bob')", ["10", "Bob"]),
        ("UPDATE t SET score2 = 5 WHERE id1 = 10", ["5", "10"]),
        ("DELETE FROM t2024 WHERE col9 = 1", ["1"]),
        ("INSERT INTO t(v) VALUES (1e-3)", ["1e-3"]),
        ("INSERT INTO t(v) VALUES (-2.5)", ["-2.5"]),
    ],
)
def test_sql_literals_do_not_read_numbers_from_identifiers(statement: str, expected: list[str]) -> None:
    assert sql_literals(statement) == expected


@pytest.mark.parametrize(
    ("prompt", "literal"),
    [
        ("set value to 10", "1"),
        ("open a business account", "US"),
        ("use value 1.25", "1"),
        ("item A10 should stay", "10"),
    ],
)
def test_source_occurrences_reject_substring_false_alignment(prompt: str, literal: str) -> None:
    assert source_occurrences(prompt, literal) == []


def test_source_occurrences_returns_exact_offsets_and_all_occurrences() -> None:
    spans = source_occurrences("Add Bob, then Bob again.", "Bob")

    assert spans == [
        {"start_char": 4, "end_char": 7, "text": "Bob"},
        {"start_char": 14, "end_char": 17, "text": "Bob"},
    ]


@pytest.mark.parametrize(
    ("expression", "kind"),
    [
        ("'Bob'", "direct_string_literal"),
        ("25", "direct_integer_literal"),
        ("-2.5", "direct_real_literal"),
        ("NULL", "explicit_null"),
        ("DEFAULT", "default"),
        ("UPPER('bob')", "function_expression"),
        ("5 + 2", "arithmetic_expression"),
        ("CAST('12' AS INTEGER)", "function_expression"),
        ("(SELECT max(id) FROM s)", "subquery"),
    ],
)
def test_parse_insert_assignments_classifies_value_expression(expression: str, kind: str) -> None:
    assignments = parse_insert_assignments(f"INSERT INTO t(a) VALUES ({expression})")

    assert assignments[0].gold_value_kind == kind


def test_insert_assignment_grounding_allows_multiple_occurrences() -> None:
    rows, sample = insert_assignment_grounding(
        "Add Bob and keep Bob visible.",
        "INSERT INTO people(name) VALUES ('Bob')",
        "train",
    )

    assert len(rows[0]["acceptable_source_spans"]) == 2
    assert rows[0]["individually_source_alignable"] is True
    assert sample["jointly_source_representable"] is True


def test_insert_assignment_grounding_rejects_one_span_for_two_assignments() -> None:
    rows, sample = insert_assignment_grounding(
        "Set both fields to 5.",
        "INSERT INTO t(a,b) VALUES (5,5)",
        "train",
    )

    assert [row["individually_source_alignable"] for row in rows] == [True, True]
    assert sample["jointly_source_representable"] is False


def test_insert_assignment_grounding_accepts_two_spans_for_two_assignments() -> None:
    rows, sample = insert_assignment_grounding(
        "Set a to 5 and b to 5.",
        "INSERT INTO t(a,b) VALUES (5,5)",
        "train",
    )

    assert [row["individually_source_alignable"] for row in rows] == [True, True]
    assert sample["jointly_source_representable"] is True


@pytest.mark.parametrize("expression", ["UPPER('Bob')", "5 + 2", "CAST('12' AS INTEGER)"])
def test_expression_insert_values_are_not_primary(tmp_path: Path, expression: str) -> None:
    context = "CREATE TABLE people(name TEXT, age INT);"
    rows = {
        "train": [_row(1, "Add Bob 12.", context, f"INSERT INTO people(name) VALUES ({expression});")],
        "test": [],
    }

    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    ledger = [
        json.loads(line)
        for line in (tmp_path / "stage" / "EXCLUSION_LEDGER.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert ledger[0]["sqlite_write_eligible"] is True
    assert ledger[0]["v2_literal_grounded_primary_eligible"] is False
    assert "expression_value_not_supported" in ledger[0]["primary_scope_exclusion_reasons"]


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
            _row(1, "Add Bob id 2 age 25.", context, "INSERT INTO people(id, name, age) VALUES (2, 'Bob', 25);"),
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
    result = validate(tmp_path / "stage", tmp_path / "raw", rebuild_from_raw=False)

    assert result["status"] == "PASS"
    assert summary["manifest_counts"] == {"INSERT": 1, "UPDATE": 1, "DELETE": 1}
    assert (tmp_path / "stage" / "ELIGIBLE_INSERT_MANIFEST.jsonl").is_file()
    dev_rows = (tmp_path / "stage" / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(dev_rows) == 1


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


def test_validator_rejects_mutated_derived_artifact(tmp_path: Path) -> None:
    context = "CREATE TABLE people(name TEXT);"
    rows = {"train": [_row(1, "Add Bob.", context, "INSERT INTO people(name) VALUES ('Bob');")], "test": []}
    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    path = tmp_path / "stage" / "SOURCE_ALIGNABILITY_AUDIT.jsonl"
    payload = path.read_text(encoding="utf-8")
    path.write_text(payload.replace("source_alignable_literal", "implicit_value", 1), encoding="utf-8")

    result = validate(tmp_path / "stage")

    assert result["status"] == "FAIL"
    assert any("SOURCE_ALIGNABILITY_AUDIT.jsonl" in failure for failure in result["failures"])


def test_validator_rejects_mutated_manifest_state_hash(tmp_path: Path) -> None:
    context = "CREATE TABLE people(name TEXT);"
    rows = {"train": [_row(1, "Add Bob.", context, "INSERT INTO people(name) VALUES ('Bob');")], "test": []}
    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    path = tmp_path / "stage" / "ELIGIBLE_INSERT_MANIFEST.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["gold_post_state_hash"] = "mutated"
    path.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    result = validate(tmp_path / "stage")

    assert result["status"] == "FAIL"
    assert any("ELIGIBLE_INSERT_MANIFEST.jsonl" in failure for failure in result["failures"])


def test_official_test_primary_candidate_is_confirmation_only(tmp_path: Path) -> None:
    context = "CREATE TABLE people(name TEXT);"
    rows = {
        "train": [],
        "test": [_row(1, "Add Bob.", context, "INSERT INTO people(name) VALUES ('Bob');")],
    }
    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))

    dev = (tmp_path / "stage" / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl").read_text(encoding="utf-8")
    confirmation = [
        json.loads(line)
        for line in (tmp_path / "stage" / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert dev == ""
    assert len(confirmation) == 1
    assert confirmation[0]["development_allowed"] is False
    assert confirmation[0]["official_test_confirmation_only"] is True


def test_policy_machine_readable_primary_rule_matches_patch1_logic() -> None:
    policy = eligibility_policy()
    primary = policy["v2_literal_grounded_primary_scope"]

    assert primary["operation"] == "INSERT"
    assert primary["complexity_class"] == "single_row_insert"
    assert set(primary["assignment_value_kinds"]) == {
        "direct_string_literal",
        "direct_integer_literal",
        "direct_real_literal",
    }
    assert primary["require_all_assignments_individually_source_alignable"] is True
    assert primary["require_joint_one_to_one_source_matching"] is True
    assert "ambiguous_multiple_occurrences" not in policy["controlled_exclusion_reasons"]
    assert "not_an_automatic_exclusion" in primary["multiple_source_occurrences"]


def test_expected_primary_insert_recomputes_from_grounding_fields() -> None:
    row = {
        "sqlite_write_eligible": True,
        "operation": "INSERT",
        "complexity_class": "single_row_insert",
        "insert_assignment_grounding": {
            "all_assignments_supported_direct_literal": True,
            "all_assignments_individually_source_alignable": True,
            "jointly_source_representable": True,
        },
    }

    assert expected_primary_insert(row) is True
    row["insert_assignment_grounding"]["jointly_source_representable"] = False
    assert expected_primary_insert(row) is False


def test_generic_source_alignable_but_joint_false_is_not_primary(tmp_path: Path) -> None:
    context = "CREATE TABLE t(a INT, b INT);"
    rows = {
        "train": [_row(1, "Set both fields to 5.", context, "INSERT INTO t(a,b) VALUES (5,5);")],
        "test": [],
    }
    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    manifest_row = json.loads(
        (tmp_path / "stage" / "ELIGIBLE_INSERT_MANIFEST.jsonl").read_text(encoding="utf-8")
    )

    assert manifest_row["source_alignability_status"] == "source_alignable_literal"
    assert manifest_row["insert_assignment_grounding"]["jointly_source_representable"] is False
    assert manifest_row["v2_literal_grounded_primary_eligible"] is False


def test_validator_rejects_primary_flag_inconsistent_with_grounding(tmp_path: Path) -> None:
    context = "CREATE TABLE t(a INT, b INT);"
    rows = {
        "train": [_row(1, "Set both fields to 5.", context, "INSERT INTO t(a,b) VALUES (5,5);")],
        "test": [],
    }
    build_run(rows, tmp_path / "stage", _raw_dir(tmp_path))
    path = tmp_path / "stage" / "ELIGIBLE_INSERT_MANIFEST.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["v2_literal_grounded_primary_eligible"] = True
    path.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    manifest = json.loads((tmp_path / "stage" / "DERIVED_ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact["path"] == "ELIGIBLE_INSERT_MANIFEST.jsonl":
            artifact["sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            artifact["bytes"] = path.stat().st_size
    (tmp_path / "stage" / "DERIVED_ARTIFACT_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lock = json.loads((tmp_path / "stage" / "STAGEENG0_LOCK.json").read_text(encoding="utf-8"))
    lock["derived_artifact_manifest_sha256"] = __import__("hashlib").sha256(
        (tmp_path / "stage" / "DERIVED_ARTIFACT_MANIFEST.json").read_bytes()
    ).hexdigest()
    (tmp_path / "stage" / "STAGEENG0_LOCK.json").write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = validate(tmp_path / "stage")

    assert result["status"] == "FAIL"
    assert "primary_insert_flag_policy_mismatch:gretel:train:1:000000" in result["failures"]
