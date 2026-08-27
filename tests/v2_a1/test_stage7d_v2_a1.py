from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program, quote_identifier
from nldbwrite_v3.v2_a1.completeness import verify_completeness
from nldbwrite_v3.v2_a1.diagnostics import primary_pipeline_source_uses_oracle
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory
from nldbwrite_v3.v2_a1.phase_m_output import parse_phase_m_output
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema, validate_phase_m_ir
from nldbwrite_v3.v2_a1.phase_o_output import parse_phase_o_output, validate_phase_o_object
from nldbwrite_v3.v2_a1.pipeline import STATES, run_mocked_pipeline
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite
from nldbwrite_v3.v2_a1.prompt_rendering import offset_guide, rendered_prompt_sha256, render_phase_m_prompt, render_phase_o_prompt, serialize_prompt_object
from nldbwrite_v3.v2_a1.protocol import load_v2_a1_protocol
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans
from nldbwrite_v3.v2_a1.typed_materializer import materialize_value, sqlite_affinity
from nldbwrite_v3.v2_a1.types import V2A1Error


ROOT = Path(__file__).resolve().parents[2]
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7d_v2_a1_tests"


@pytest.fixture
def workspace_tmp(request) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
    target = TEST_TMP_ROOT / f"{safe_name}_{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=False)
    try:
        yield target
    finally:
        resolved = target.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)


def schema_input() -> dict:
    return {
        "question": "ignored",
        "schema_inventory": {
            "tables": [{"table_ref": "TAB_1", "table_name": "people"}],
            "columns": [
                {"column_ref": "COL_2", "column_name": "age", "source_type": "INTEGER"},
                {"column_ref": "COL_1", "column_name": "name", "source_type": "TEXT"},
                {"column_ref": "COL_3", "column_name": "city", "source_type": "TEXT"},
                {"column_ref": "COL_4", "column_name": "score", "source_type": "REAL"},
            ],
            "constraints": [{"constraint_ref": "CONSTRAINT_1", "column_refs": ["COL_1"]}],
        },
    }


def inv():
    return build_schema_inventory(schema_input())


def slots_for(question: str, spans: list[dict[str, int]]):
    return build_slot_bundle(validate_and_sort_spans(question, spans))


def insert_ir() -> dict:
    return {
        "operation": "INSERT",
        "table_ref": "TAB_1",
        "assignments": [
            {"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
            {"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
        ],
    }


def make_db(tmp_path: Path) -> Path:
    path = tmp_path / "fixture.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE people (name TEXT UNIQUE NOT NULL, age INTEGER NOT NULL, city TEXT, score REAL);
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (parent_id INTEGER NOT NULL REFERENCES parent(id));
        INSERT INTO people(name, age, city, score) VALUES ('Bob', 30, 'Hue', 1.5);
        """
    )
    conn.close()
    return path


def raises_code(code: str):
    return pytest.raises(V2A1Error, match=r".*")


def assert_code(excinfo, code: str) -> None:
    assert excinfo.value.reason_code == code


def test_protocol_loader_reads_locked_upstreams() -> None:
    protocol = load_v2_a1_protocol(ROOT)
    assert protocol.phase_o_model_calls == 1
    assert protocol.phase_m_model_calls == 1
    assert protocol.model_revision == "c03e6d358207e414f1eca0bb1891e29f1db0e242"


def test_pipeline_state_machine_contains_frozen_order() -> None:
    assert STATES[0] == "PREPARED"
    assert "PHASE_O_VALIDATED" in STATES
    assert "COMPLETENESS_VERIFIED" in STATES
    assert STATES[-2:] == ("ADMITTED", "REJECTED")


def test_inventory_stable_ordering_and_refs() -> None:
    inventory = inv()
    assert [column.ref for column in inventory.columns] == ["COL_1", "COL_2", "COL_3", "COL_4"]
    assert [table.ref for table in inventory.tables] == ["TAB_1"]
    assert [constraint.ref for constraint in inventory.constraints] == ["CONSTRAINT_1"]


@pytest.mark.parametrize("forbidden", ["conds", "gold_sql", "gold_post_state", "operation_label", "target_state"])
def test_inventory_rejects_gold_leakage(forbidden: str) -> None:
    payload = schema_input()
    payload[forbidden] = "leak"
    with pytest.raises(V2A1Error) as exc:
        build_schema_inventory(payload)
    assert_code(exc, "leakage_boundary_violation")


def test_inventory_requires_schema_lists() -> None:
    with pytest.raises(V2A1Error) as exc:
        build_schema_inventory({"schema_inventory": {"tables": []}})
    assert_code(exc, "schema_inventory_missing")


def test_prompt_serialization_preserves_chinese_and_sorts_keys() -> None:
    assert serialize_prompt_object({"b": "北京", "a": 1}) == '{"a":1,"b":"北京"}'


def test_rendered_prompt_hash_is_lf_canonical() -> None:
    assert rendered_prompt_sha256("a\r\nb") == rendered_prompt_sha256("a\nb")


def test_offset_guide_uses_python_codepoints() -> None:
    assert offset_guide("A北京B") == "0\tA\n1\t北\n2\t京\n3\tB"


def test_phase_o_prompt_contains_exact_question_and_schema() -> None:
    rendered, digest = render_phase_o_prompt("sys", "Alice 20", inv())
    assert "Alice 20" in rendered
    assert "COL_1" in rendered
    assert len(digest) == 64


def test_phase_m_prompt_contains_predicted_slots_not_gold() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    rendered, digest = render_phase_m_prompt("sys", "INSERT", inv(), bundle)
    assert "SLOT_1" in rendered and "EV_2" in rendered
    assert "gold_sql" not in rendered
    assert len(digest) == 64


def test_phase_o_parse_valid_insert() -> None:
    obj = parse_phase_o_output('{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":5}]}')
    assert obj["operation"] == "INSERT"


@pytest.mark.parametrize(
    "payload,code",
    [
        ("not-json", "phase_o_parse"),
        ('[]', "phase_o_schema_failure"),
        ('{"operation":"CREATE","value_spans":[{"start_char":0,"end_char":1}]}', "phase_o_invalid_operation"),
        ('{"operation":"INSERT","value_spans":[]}', "phase_o_empty_spans"),
        ('{"operation":"INSERT","value_spans":"x"}', "phase_o_schema_failure"),
        ('{"operation":"INSERT"}', "phase_o_schema_failure"),
        ('{"operation":"INSERT","value_spans":[{"span_ref":"SPAN_1","start_char":0,"end_char":1}]}', "phase_o_schema_failure"),
        ('{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":1,"text":"A"}]}', "phase_o_schema_failure"),
        ('{"operation":"INSERT","value_spans":[{"start_char":0.0,"end_char":1}]}', "phase_o_invalid_offset"),
    ],
)
def test_phase_o_rejects_invalid_outputs(payload: str, code: str) -> None:
    with pytest.raises(V2A1Error) as exc:
        parse_phase_o_output(payload)
    assert_code(exc, code)


@pytest.mark.parametrize(
    "question,span,expected",
    [
        ("北京上海", {"start_char": 0, "end_char": 2}, "北京"),
        ("Alice 20", {"start_char": 6, "end_char": 8}, "20"),
        ("A北京B上海C", {"start_char": 1, "end_char": 3}, "北京"),
        ("A😀B", {"start_char": 1, "end_char": 2}, "😀"),
        ("e\u0301clair", {"start_char": 0, "end_char": 2}, "e\u0301"),
    ],
)
def test_span_validation_slices_unicode_codepoints(question: str, span: dict[str, int], expected: str) -> None:
    assert validate_and_sort_spans(question, [span])[0].text == expected


@pytest.mark.parametrize(
    "spans,code",
    [
        ([{"start_char": -1, "end_char": 1}], "phase_o_invalid_offset"),
        ([{"start_char": 1, "end_char": 1}], "phase_o_invalid_offset"),
        ([{"start_char": 0, "end_char": 99}], "phase_o_invalid_offset"),
        ([{"start_char": 0, "end_char": 1}, {"start_char": 0, "end_char": 1}], "phase_o_duplicate_span"),
        ([{"start_char": 0, "end_char": 3}, {"start_char": 1, "end_char": 2}], "phase_o_overlap"),
        ([{"start_char": 0, "end_char": 3}, {"start_char": 2, "end_char": 4}], "phase_o_overlap"),
    ],
)
def test_span_validation_rejects_bad_offsets(spans: list[dict[str, int]], code: str) -> None:
    with pytest.raises(V2A1Error) as exc:
        validate_and_sort_spans("ABCDE", spans)
    assert_code(exc, code)


def test_span_validation_sorts_out_of_order_spans() -> None:
    spans = validate_and_sort_spans("Alice 20 Paris", [{"start_char": 9, "end_char": 14}, {"start_char": 0, "end_char": 5}])
    assert [(span.start_char, span.text) for span in spans] == [(0, "Alice"), (9, "Paris")]


def test_span_validation_allows_adjacent_spans() -> None:
    spans = validate_and_sort_spans("ABCDE", [{"start_char": 0, "end_char": 2}, {"start_char": 2, "end_char": 4}])
    assert [span.text for span in spans] == ["AB", "CD"]


def test_slot_inventory_builds_required_ev_slot_ids() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 6, "end_char": 8}, {"start_char": 0, "end_char": 5}])
    assert [item.evidence_ref for item in bundle.evidence] == ["EV_1", "EV_2"]
    assert [item.slot_ref for item in bundle.slots] == ["SLOT_1", "SLOT_2"]
    assert [item.text for item in bundle.slots] == ["Alice", "20"]
    assert all(item.required for item in bundle.slots)


def test_dynamic_schema_lists_exact_runtime_enums() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    schema = dynamic_schema("INSERT", inv(), bundle)
    assert schema["dynamic_enums"]["table_refs"] == ["TAB_1"]
    assert schema["dynamic_enums"]["slot_refs"] == ["SLOT_1", "SLOT_2"]


def test_phase_m_valid_insert() -> None:
    validate_phase_m_ir(insert_ir(), "INSERT", inv(), slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ir: ir.update({"table_ref": "TAB_999"}),
        lambda ir: ir["assignments"][0].update({"column_ref": "COL_999"}),
        lambda ir: ir["assignments"][0].update({"evidence_ref": "EV_999"}),
        lambda ir: ir["assignments"][0].update({"slot_ref": "SLOT_999"}),
        lambda ir: ir.update({"extra": True}),
    ],
)
def test_phase_m_insert_rejects_invalid_refs_and_extra_keys(mutation) -> None:
    ir = insert_ir()
    mutation(ir)
    with pytest.raises(V2A1Error):
        validate_phase_m_ir(ir, "INSERT", inv(), slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))


def test_phase_m_insert_rejects_duplicate_column() -> None:
    ir = insert_ir()
    ir["assignments"][1]["column_ref"] = "COL_1"
    with pytest.raises(V2A1Error) as exc:
        validate_phase_m_ir(ir, "INSERT", inv(), slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))
    assert_code(exc, "completeness_duplicate_column")


def test_phase_m_insert_rejects_duplicate_slot() -> None:
    ir = insert_ir()
    ir["assignments"][1]["slot_ref"] = "SLOT_1"
    with pytest.raises(V2A1Error) as exc:
        validate_phase_m_ir(ir, "INSERT", inv(), slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))
    assert_code(exc, "completeness_duplicate_slot")


def test_phase_m_parse_rejects_malformed_json() -> None:
    with pytest.raises(V2A1Error) as exc:
        parse_phase_m_output("not-json", "INSERT", inv(), slots_for("Alice 20", [{"start_char": 0, "end_char": 5}]))
    assert_code(exc, "phase_m_parse")


def test_update_allows_two_predicates_with_and() -> None:
    bundle = slots_for("Alice 20 Paris", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}, {"start_char": 9, "end_char": 14}])
    ir = {
        "operation": "UPDATE",
        "table_ref": "TAB_1",
        "row_selector": {"connector": "AND", "predicates": [{"column_ref": "COL_1", "operator": "EQ", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}, {"column_ref": "COL_2", "operator": "GT", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"}]},
        "assignments": [{"column_ref": "COL_3", "evidence_ref": "EV_3", "slot_ref": "SLOT_3"}],
    }
    validate_phase_m_ir(ir, "UPDATE", inv(), bundle)


@pytest.mark.parametrize("bad_selector", [{"connector": "X", "predicates": []}, {"connector": "AND", "predicates": []}, {"connector": "AND", "predicates": [{"column_ref": "COL_1", "operator": "LIKE", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}]}])
def test_update_rejects_malformed_predicates(bad_selector: dict) -> None:
    bundle = slots_for("Alice", [{"start_char": 0, "end_char": 5}])
    ir = {"operation": "UPDATE", "table_ref": "TAB_1", "row_selector": bad_selector, "assignments": [{"column_ref": "COL_3", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}]}
    with pytest.raises(V2A1Error):
        validate_phase_m_ir(ir, "UPDATE", inv(), bundle)


def test_delete_allows_or_predicates_and_forbids_assignments() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    ir = {"operation": "DELETE", "table_ref": "TAB_1", "row_selector": {"connector": "OR", "predicates": [{"column_ref": "COL_1", "operator": "EQ", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}, {"column_ref": "COL_2", "operator": "LT", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"}]}}
    validate_phase_m_ir(ir, "DELETE", inv(), bundle)
    ir["assignments"] = []
    with pytest.raises(V2A1Error):
        validate_phase_m_ir(ir, "DELETE", inv(), bundle)


def test_upsert_do_nothing_and_do_update_contracts() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    base = {"operation": "UPSERT", "table_ref": "TAB_1", "conflict_target_ref": "CONSTRAINT_1", "insert_assignments": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}]}
    validate_phase_m_ir({**base, "update_policy": "DO_NOTHING"}, "UPSERT", inv(), bundle)
    validate_phase_m_ir({**base, "update_policy": "DO_UPDATE", "update_assignments": [{"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"}]}, "UPSERT", inv(), bundle)


@pytest.mark.parametrize(
    "ir",
    [
        {"operation": "UPSERT", "table_ref": "TAB_1", "conflict_target_ref": "CONSTRAINT_1", "insert_assignments": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}], "update_policy": "DO_NOTHING", "update_assignments": [{"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"}]},
        {"operation": "UPSERT", "table_ref": "TAB_1", "conflict_target_ref": "CONSTRAINT_1", "insert_assignments": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}], "update_policy": "DO_UPDATE"},
        {"operation": "UPSERT", "table_ref": "TAB_1", "conflict_target_ref": "CONSTRAINT_1", "insert_assignments": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}], "update_policy": "DO_UPDATE", "update_assignments": []},
        {"operation": "UPSERT", "table_ref": "TAB_1", "conflict_target_ref": "CONSTRAINT_999", "insert_assignments": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}], "update_policy": "DO_NOTHING"},
    ],
)
def test_upsert_rejects_invalid_semantics(ir: dict) -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    with pytest.raises(V2A1Error):
        validate_phase_m_ir(ir, "UPSERT", inv(), bundle)


@pytest.mark.parametrize(
    "declared,affinity",
    [("INTEGER", "INTEGER"), ("varchar(10)", "TEXT"), ("REAL", "REAL"), ("DOUBLE", "REAL"), ("", "BLOB"), ("DATE", "NUMERIC")],
)
def test_sqlite_affinity_rules(declared: str, affinity: str) -> None:
    assert sqlite_affinity(declared) == affinity


@pytest.mark.parametrize("raw,expected", [("20", 20), ("+20", 20), ("-20", -20)])
def test_materializes_strict_integers(raw: str, expected: int) -> None:
    assert materialize_value(raw, "INTEGER").value == expected


@pytest.mark.parametrize("raw", ["20.0", "20 years", "1e2", ""])
def test_integer_rejects_lossy_or_non_integer(raw: str) -> None:
    with pytest.raises(V2A1Error) as exc:
        materialize_value(raw, "INTEGER")
    assert_code(exc, "materialization_failure")


@pytest.mark.parametrize("raw,expected", [("1.94", 1.94), ("12", 12.0), (".5", 0.5), ("1e2", 100.0)])
def test_materializes_strict_reals(raw: str, expected: float) -> None:
    assert materialize_value(raw, "REAL").value == expected


@pytest.mark.parametrize("raw", ["1.94%", "68元", "2018年7月1日", "1e309"])
def test_real_rejects_unregistered_normalization(raw: str) -> None:
    with pytest.raises(V2A1Error) as exc:
        materialize_value(raw, "REAL")
    assert_code(exc, "materialization_failure")


def test_text_preserves_raw_evidence_without_date_normalization() -> None:
    assert materialize_value("2018年7月1日", "TEXT").value == "2018年7月1日"


def test_numeric_materializes_integer_or_float_but_not_units() -> None:
    assert materialize_value("100", "NUMERIC").value == 100
    assert materialize_value("1.5", "NUMERIC").value == 1.5
    with pytest.raises(V2A1Error):
        materialize_value("1亿", "NUMERIC")


def test_blob_rejected_without_frozen_representation() -> None:
    with pytest.raises(V2A1Error) as exc:
        materialize_value("abc", "BLOB")
    assert_code(exc, "materialization_failure")


def test_completeness_accepts_all_required_slots() -> None:
    verify_completeness(insert_ir(), slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))


def test_completeness_rejects_missing_slot() -> None:
    ir = insert_ir()
    ir["assignments"] = ir["assignments"][:1]
    with pytest.raises(V2A1Error) as exc:
        verify_completeness(ir, slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))
    assert_code(exc, "completeness_missing_slot")


def test_completeness_rejects_duplicate_slot() -> None:
    ir = insert_ir()
    ir["assignments"][1]["slot_ref"] = "SLOT_1"
    with pytest.raises(V2A1Error) as exc:
        verify_completeness(ir, slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))
    assert_code(exc, "completeness_duplicate_slot")


def test_completeness_rejects_unknown_slot() -> None:
    ir = insert_ir()
    ir["assignments"][1]["slot_ref"] = "SLOT_999"
    with pytest.raises(V2A1Error) as exc:
        verify_completeness(ir, slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}]))
    assert_code(exc, "completeness_unknown_slot")


def test_quote_identifier_escapes_quotes() -> None:
    assert quote_identifier('a"b') == '"a""b"'


def test_compile_insert_parameterized_and_deterministic() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    program = compile_sqlite_program(insert_ir(), inv(), bundle)
    assert program.sql == 'INSERT INTO "people" ("name","age") VALUES (?,?)'
    assert program.parameters == ("Alice", 20)
    assert compile_sqlite_program(insert_ir(), inv(), bundle).normalized == program.normalized


def test_compile_update_and_delete_where_clauses() -> None:
    bundle = slots_for("Alice 20 Paris", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}, {"start_char": 9, "end_char": 14}])
    update = {"operation": "UPDATE", "table_ref": "TAB_1", "row_selector": {"connector": "AND", "predicates": [{"column_ref": "COL_1", "operator": "EQ", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}]}, "assignments": [{"column_ref": "COL_3", "evidence_ref": "EV_3", "slot_ref": "SLOT_3"}]}
    delete = {"operation": "DELETE", "table_ref": "TAB_1", "row_selector": {"connector": "OR", "predicates": [{"column_ref": "COL_2", "operator": "GT", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"}]}}
    assert compile_sqlite_program(update, inv(), bundle).sql == 'UPDATE "people" SET "city"=? WHERE "name" = ?'
    assert compile_sqlite_program(delete, inv(), bundle).sql == 'DELETE FROM "people" WHERE "age" > ?'


def test_compile_upsert_do_nothing_and_update() -> None:
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    base = {"operation": "UPSERT", "table_ref": "TAB_1", "conflict_target_ref": "CONSTRAINT_1", "insert_assignments": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}]}
    nothing = compile_sqlite_program({**base, "update_policy": "DO_NOTHING"}, inv(), bundle)
    update = compile_sqlite_program({**base, "update_policy": "DO_UPDATE", "update_assignments": [{"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"}]}, inv(), bundle)
    assert "DO NOTHING" in nothing.sql
    assert "DO UPDATE SET" in update.sql


def test_preflight_success_and_rollback_preserves_db(workspace_tmp: Path) -> None:
    db = make_db(workspace_tmp)
    bundle = slots_for("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
    result = preflight_sqlite(db, compile_sqlite_program(insert_ir(), inv(), bundle))
    assert result.admitted is True
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM people WHERE name='Alice'").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "program_sql,params",
    [
        ('INSERT INTO "people" ("name","age") VALUES (?,?)', ("Bob", 40)),
        ('INSERT INTO "people" ("name") VALUES (?)', ("NoAge",)),
        ('INSERT INTO "child" ("parent_id") VALUES (?)', (999,)),
    ],
)
def test_preflight_rejects_sqlite_constraint_failures(workspace_tmp: Path, program_sql: str, params: tuple) -> None:
    db = make_db(workspace_tmp)
    from nldbwrite_v3.v2_a1.types import SQLiteProgram

    result = preflight_sqlite(db, SQLiteProgram(operation="INSERT", sql=program_sql, parameters=params, normalized="x"))
    assert result.admitted is False
    assert result.reason_code == "preflight_execution_failure"


def test_mocked_pipeline_perfect_insert_compiles() -> None:
    result = run_mocked_pipeline(
        question="Alice 20",
        model_side_input=schema_input(),
        phase_o_output_json='{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":5},{"start_char":6,"end_char":8}]}',
        phase_m_output_json=json.dumps(insert_ir()),
        phase_o_system_prompt="sys-o",
        phase_m_system_prompt="sys-m",
    )
    assert result.state == "COMPILED"
    assert result.admitted is True
    assert result.sql == 'INSERT INTO "people" ("name","age") VALUES (?,?)'


def test_mocked_pipeline_preflights_with_rollback(workspace_tmp: Path) -> None:
    result = run_mocked_pipeline(
        question="Alice 20",
        model_side_input=schema_input(),
        phase_o_output_json='{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":5},{"start_char":6,"end_char":8}]}',
        phase_m_output_json=json.dumps(insert_ir()),
        phase_o_system_prompt="sys-o",
        phase_m_system_prompt="sys-m",
        db_path=make_db(workspace_tmp),
    )
    assert result.state == "ADMITTED"
    assert result.reason_code == "admitted"


def test_mocked_pipeline_bad_phase_o_rejects_before_phase_m() -> None:
    with pytest.raises(V2A1Error) as exc:
        run_mocked_pipeline(question="Alice", model_side_input=schema_input(), phase_o_output_json='{"operation":"INSERT","value_spans":[]}', phase_m_output_json="{}", phase_o_system_prompt="sys-o", phase_m_system_prompt="sys-m")
    assert_code(exc, "phase_o_empty_spans")


def test_mocked_pipeline_bad_column_mapping_rejects() -> None:
    bad = insert_ir()
    bad["assignments"][0]["column_ref"] = "COL_999"
    with pytest.raises(V2A1Error) as exc:
        run_mocked_pipeline(question="Alice 20", model_side_input=schema_input(), phase_o_output_json='{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":5},{"start_char":6,"end_char":8}]}', phase_m_output_json=json.dumps(bad), phase_o_system_prompt="sys-o", phase_m_system_prompt="sys-m")
    assert_code(exc, "phase_m_invalid_reference")


def test_mocked_pipeline_missing_phase_m_slot_rejects_completeness() -> None:
    missing = insert_ir()
    missing["assignments"] = missing["assignments"][:1]
    with pytest.raises(V2A1Error) as exc:
        run_mocked_pipeline(question="Alice 20", model_side_input=schema_input(), phase_o_output_json='{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":5},{"start_char":6,"end_char":8}]}', phase_m_output_json=json.dumps(missing), phase_o_system_prompt="sys-o", phase_m_system_prompt="sys-m")
    assert_code(exc, "completeness_missing_slot")


def test_primary_pipeline_does_not_import_oracle_provider() -> None:
    assert primary_pipeline_source_uses_oracle(ROOT) is False
