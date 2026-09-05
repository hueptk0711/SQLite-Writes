from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from nldbwrite_v3.experiments.prompts import build_direct_prompt, build_legacy_json_prompt
from nldbwrite_v3.schema.profile import build_profile
from nldbwrite_v3.v2_a1.eng2b_candidate_domains import (
    audit_admissibility_runtime_equivalence,
    build_column_specific_domains,
    candidate_kind,
    canonical_boundary_text,
    column_allows_candidate,
    dynamic_schema_with_column_domains,
    filter_dominated_boundaries,
    intervals_overlap,
)
from nldbwrite_v3.v2_a1.eng2b_runtime import build_eng2b_constraint_grammar, prepare_eng2b_runtime_row, sha256_text
from nldbwrite_v3.v2_a1.typed_materializer import (
    enforce_unique_non_omit_span_refs,
    materialize_value,
    semantic_materialization_type,
    sqlite_affinity,
)
from nldbwrite_v3.v2_a1.types import V2A1Error


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_temporal_and_numeric_materialization_semantics() -> None:
    assert sqlite_affinity("DATE") == "TEXT"
    assert semantic_materialization_type("TIMESTAMP") == "DATETIME"
    assert materialize_value("2024-01-01", "DATE").value == "2024-01-01"
    assert materialize_value("2022-03-02 10:30:00", "TIMESTAMP").value == "2022-03-02 10:30:00"
    assert materialize_value("12.5", "FLOAT").value == 12.5
    assert materialize_value("5", "INTEGER").value == 5


@pytest.mark.parametrize(
    "raw,declared",
    [
        ("2024-13-01", "DATE"),
        ("2024-01", "DATE"),
        ("2022-03-02", "TIMESTAMP"),
        ("not-a-number", "FLOAT"),
        ("5.5", "INTEGER"),
        ("$30000", "INTEGER"),
    ],
)
def test_materialization_invalid_cases_fail_closed(raw: str, declared: str) -> None:
    with pytest.raises(V2A1Error):
        materialize_value(raw, declared)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("'completed'", "completed"),
        ("$30000", "30000"),
        ("date 2020-08-10", "2020-08-10"),
        ("India's", "India"),
        ("'Pre-rolls',", "Pre-rolls"),
    ],
)
def test_canonical_boundary_construction(raw: str, expected: str) -> None:
    canonical, rules = canonical_boundary_text(raw)
    assert canonical == expected
    assert rules


def toy_model_side_and_constraints() -> tuple[dict, dict]:
    model_side = {
        "question": "Set id to 5, start date to 2024-01-01, and status to completed.",
        "schema_inventory": {
            "columns": [
                {"column_ref": "COL_1", "column_name": "id", "source_type": "INTEGER", "nullable": False, "table_ref": "TAB_1"},
                {"column_ref": "COL_2", "column_name": "start_date", "source_type": "DATE", "nullable": False, "table_ref": "TAB_1"},
                {"column_ref": "COL_3", "column_name": "status", "source_type": "TEXT", "nullable": True, "table_ref": "TAB_1"},
            ]
        },
    }
    constraints = {
        "candidate_inventory": [
            {"span_ref": "SPAN_1", "text": "5", "start_char": 10, "end_char": 11},
            {"span_ref": "SPAN_2", "text": "2024-01-01", "start_char": 27, "end_char": 37},
            {"span_ref": "SPAN_3", "text": "'completed'", "start_char": 53, "end_char": 62},
            {"span_ref": "SPAN_4", "text": "completed", "start_char": 53, "end_char": 62},
            {"span_ref": "SPAN_5", "text": "$30000", "start_char": 64, "end_char": 70},
        ],
        "phase_o_schema": {
            "type": "object",
            "properties": {
                "column_span_refs": {
                    "type": "object",
                    "required": ["COL_1", "COL_2", "COL_3"],
                    "properties": {
                        "COL_1": {"type": "string", "enum": ["SPAN_1", "SPAN_2", "SPAN_3", "SPAN_4", "SPAN_5"]},
                        "COL_2": {"type": "string", "enum": ["SPAN_1", "SPAN_2", "SPAN_3", "SPAN_4", "SPAN_5"]},
                        "COL_3": {"type": "string", "enum": ["OMIT", "SPAN_1", "SPAN_2", "SPAN_3", "SPAN_4", "SPAN_5"]},
                    },
                },
                "operation": {"const": "INSERT"},
                "table_ref": {"const": "TAB_1"},
            },
        },
    }
    return model_side, constraints


def test_column_specific_domains_filter_by_type_text_label_and_omit_semantics() -> None:
    model_side, constraints = toy_model_side_and_constraints()
    result = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    assert result["domain_construction_uses_gold"] is False
    assert result["domains"]["COL_1"] == ["SPAN_1"]
    assert result["domains"]["COL_2"] == ["SPAN_2"]
    assert result["domains"]["COL_3"] == ["SPAN_4"]
    assert "OMIT" not in result["domains"]["COL_3"]
    status_row = next(row for row in result["audit_rows"] if row["column_ref"] == "COL_3")
    assert status_row["text_label_segment_filter_applied"] is True
    assert status_row["text_domain_restricted_by_label_segment"] is True
    assert status_row["omit_removed_by_strong_evidence"] is True
    schema = dynamic_schema_with_column_domains(model_side_input=model_side, runtime_constraints=constraints, domains=result["domains"])
    assert schema["x-eng2b-span-uniqueness"].startswith("stateful prefix grammar")


def test_boundary_dominance_suppresses_overlapping_atomic_variants() -> None:
    kept, suppressed = filter_dominated_boundaries(
        [
            {"span_ref": "SPAN_QUOTED", "text": "'completed'", "start_char": 10, "end_char": 21, "tags": ["quoted"]},
            {"span_ref": "SPAN_ATOMIC", "text": "completed", "start_char": 11, "end_char": 20, "tags": ["quoted"]},
            {"span_ref": "SPAN_OTHER", "text": "completed", "start_char": 40, "end_char": 49, "tags": ["quoted"]},
        ]
    )
    assert [candidate["span_ref"] for candidate in kept] == ["SPAN_ATOMIC", "SPAN_OTHER"]
    assert [candidate["span_ref"] for candidate in suppressed] == ["SPAN_QUOTED"]


def test_boundary_dominance_suppresses_punctuated_variants() -> None:
    kept, suppressed = filter_dominated_boundaries(
        [
            {"span_ref": "SPAN_PUNCT", "text": "'Pre-rolls',", "start_char": 10, "end_char": 23},
            {"span_ref": "SPAN_ATOMIC", "text": "Pre-rolls", "start_char": 11, "end_char": 20},
        ]
    )
    assert [candidate["span_ref"] for candidate in kept] == ["SPAN_ATOMIC"]
    assert [candidate["span_ref"] for candidate in suppressed] == ["SPAN_PUNCT"]


@pytest.mark.parametrize(
    "noisy,atomic",
    [
        ("'2020-01-01',", "2020-01-01"),
        ('"2022-01-01"', "2022-01-01"),
        ("'completed'", "completed"),
    ],
)
def test_boundary_dominance_uses_canonical_overlap_not_provenance(noisy: str, atomic: str) -> None:
    base = noisy.index(atomic)
    kept, suppressed = filter_dominated_boundaries(
        [
            {"span_ref": "SPAN_NOISY", "text": noisy, "start_char": 10, "end_char": 10 + len(noisy), "provenance_tags": ["whitespace_ngram"]},
            {"span_ref": "SPAN_ATOMIC", "text": atomic, "start_char": 10 + base, "end_char": 10 + base + len(atomic), "provenance_tags": ["quoted_content"]},
        ]
    )
    assert [candidate["span_ref"] for candidate in kept] == ["SPAN_ATOMIC"]
    assert [candidate["span_ref"] for candidate in suppressed] == ["SPAN_NOISY"]


def test_half_open_intervals_do_not_overlap_when_adjacent() -> None:
    assert intervals_overlap((0, 4), (4, 8)) is False
    assert intervals_overlap((0, 5), (4, 8)) is True


def test_default_cue_forces_or_retains_omit_for_omittable_text_column() -> None:
    model_side = {
        "question": "Active should use default.",
        "schema_inventory": {
            "columns": [
                {"column_ref": "COL_ACTIVE", "column_name": "active", "source_type": "TEXT", "nullable": True, "has_default": True, "table_ref": "TAB_1"},
            ]
        },
    }
    constraints = {
        "candidate_inventory": [
            {"span_ref": "SPAN_DEFAULT", "text": "use default", "start_char": 14, "end_char": 25},
            {"span_ref": "SPAN_OTHER", "text": "enabled", "start_char": 30, "end_char": 37},
        ],
        "phase_o_schema": {
            "type": "object",
            "properties": {
                "column_span_refs": {"type": "object", "required": ["COL_ACTIVE"], "properties": {"COL_ACTIVE": {"type": "string", "enum": ["OMIT", "SPAN_DEFAULT", "SPAN_OTHER"]}}},
                "operation": {"const": "INSERT"},
                "table_ref": {"const": "TAB_1"},
            },
        },
    }
    result = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    assert result["domains"]["COL_ACTIVE"] == ["OMIT"]
    row = result["audit_rows"][0]
    assert row["omit_forced_by_default_cue"] is True
    assert row["omit_removed_by_strong_evidence"] is False


def test_explicit_value_removes_omit_but_missing_evidence_retains_it() -> None:
    model_side, constraints = toy_model_side_and_constraints()
    explicit = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    assert explicit["domains"]["COL_3"] == ["SPAN_4"]
    no_local = copy.deepcopy(model_side)
    no_local["question"] = "Set id to 5 and start date to 2024-01-01."
    missing = build_column_specific_domains(model_side_input=no_local, runtime_constraints=constraints)
    assert "OMIT" in missing["domains"]["COL_3"]


def test_identifier_suffix_label_does_not_block_long_text_value() -> None:
    question = (
        "Add a new record to the cybersecurity_strategy table with strategy_id 987, "
        "strategy_name 'Intrusion Detection', strategy_description "
        "'Detailed description of intrusion detection strategy'"
    )
    value = "Detailed description of intrusion detection strategy"
    start = question.index(value)
    model_side = {
        "question": question,
        "schema_inventory": {
            "columns": [
                {"column_ref": "COL_ID", "column_name": "strategy_id", "source_type": "INTEGER", "nullable": False, "table_ref": "TAB_1"},
                {"column_ref": "COL_NAME", "column_name": "strategy_name", "source_type": "TEXT", "nullable": False, "table_ref": "TAB_1"},
                {"column_ref": "COL_DESC", "column_name": "strategy_description", "source_type": "TEXT", "nullable": False, "table_ref": "TAB_1"},
            ]
        },
    }
    constraints = {
        "candidate_inventory": [
            {"span_ref": "SPAN_ID", "text": "987", "start_char": question.index("987"), "end_char": question.index("987") + 3},
            {"span_ref": "SPAN_DESC", "text": value, "start_char": start, "end_char": start + len(value), "tags": ["QUOTED_TEXT"]},
        ],
        "phase_o_schema": {"type": "object", "properties": {"column_span_refs": {"type": "object", "required": ["COL_ID", "COL_NAME", "COL_DESC"], "properties": {}}}},
    }
    result = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    assert result["domains"]["COL_DESC"] == ["SPAN_DESC"]
    desc_row = next(row for row in result["audit_rows"] if row["column_ref"] == "COL_DESC")
    assert desc_row["text_domain_restricted_by_label_segment"] is True


def test_text_domain_falls_back_when_local_candidate_is_only_connector() -> None:
    question = "Insert a new record into events table with comment and severity high."
    and_start = question.index("and")
    high_start = question.index("high")
    model_side = {
        "question": question,
        "schema_inventory": {
            "columns": [
                {"column_ref": "COL_COMMENT", "column_name": "comment", "source_type": "TEXT", "nullable": False, "table_ref": "TAB_1"},
                {"column_ref": "COL_SEVERITY", "column_name": "severity", "source_type": "TEXT", "nullable": False, "table_ref": "TAB_1"},
            ]
        },
    }
    constraints = {
        "candidate_inventory": [
            {"span_ref": "SPAN_AND", "text": "and", "start_char": and_start, "end_char": and_start + 3},
            {"span_ref": "SPAN_HIGH", "text": "high", "start_char": high_start, "end_char": high_start + 4},
        ],
        "phase_o_schema": {"type": "object", "properties": {"column_span_refs": {"type": "object", "required": ["COL_COMMENT", "COL_SEVERITY"], "properties": {}}}},
    }
    result = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    assert set(result["domains"]["COL_COMMENT"]) == {"SPAN_AND", "SPAN_HIGH"}
    comment_row = next(row for row in result["audit_rows"] if row["column_ref"] == "COL_COMMENT")
    assert comment_row["text_label_segment_filter_applied"] is False


def test_gold_blind_domain_hash_invariant() -> None:
    model_side, constraints = toy_model_side_and_constraints()
    baseline = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    mutated_row = {"model_side_input": copy.deepcopy(model_side), "runtime_constraints": copy.deepcopy(constraints), "label_side_expected": {"phase_o": {"column_span_refs": {"COL_1": "SPAN_999"}}}}
    after = build_column_specific_domains(model_side_input=mutated_row["model_side_input"], runtime_constraints=mutated_row["runtime_constraints"])
    assert sha256_text(json.dumps(baseline["domains"], sort_keys=True)) == sha256_text(json.dumps(after["domains"], sort_keys=True))


def test_stateful_uniqueness_is_enforced_by_grammar() -> None:
    schema = {
        "type": "object",
        "properties": {
            "column_span_refs": {
                "type": "object",
                "required": ["COL_1", "COL_2"],
                "properties": {
                    "COL_1": {"type": "string", "enum": ["OMIT", "SPAN_1"]},
                    "COL_2": {"type": "string", "enum": ["OMIT", "SPAN_1", "SPAN_2"]},
                },
            },
            "operation": {"const": "INSERT"},
            "table_ref": {"const": "TAB_1"},
        },
    }
    grammar = build_eng2b_constraint_grammar(schema)
    duplicate = '{"column_span_refs":{"COL_1":"SPAN_1","COL_2":"SPAN_1"},"operation":"INSERT","table_ref":"TAB_1"}'
    distinct = '{"column_span_refs":{"COL_1":"SPAN_1","COL_2":"SPAN_2"},"operation":"INSERT","table_ref":"TAB_1"}'
    omits = '{"column_span_refs":{"COL_1":"OMIT","COL_2":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}'
    assert not grammar.is_complete(duplicate)
    assert not grammar.is_prefix('{"column_span_refs":{"COL_1":"SPAN_1","COL_2":"SPAN_1')
    assert grammar.is_complete(distinct)
    assert grammar.is_complete(omits)


def test_distinct_same_text_occurrences_are_not_collapsed() -> None:
    model_side = {
        "question": "Set x to 7 and y to 7.",
        "schema_inventory": {
            "columns": [
                {"column_ref": "COL_X", "column_name": "x", "source_type": "INTEGER", "nullable": False, "table_ref": "TAB_1"},
                {"column_ref": "COL_Y", "column_name": "y", "source_type": "INTEGER", "nullable": False, "table_ref": "TAB_1"},
            ]
        },
    }
    constraints = {
        "candidate_inventory": [
            {"span_ref": "SPAN_x7", "text": "7", "start_char": 9, "end_char": 10},
            {"span_ref": "SPAN_y7", "text": "7", "start_char": 20, "end_char": 21},
        ],
        "phase_o_schema": {
            "type": "object",
            "properties": {
                "column_span_refs": {
                    "type": "object",
                    "required": ["COL_X", "COL_Y"],
                    "properties": {
                        "COL_X": {"type": "string", "enum": ["SPAN_x7", "SPAN_y7"]},
                        "COL_Y": {"type": "string", "enum": ["SPAN_x7", "SPAN_y7"]},
                    },
                },
                "operation": {"const": "INSERT"},
                "table_ref": {"const": "TAB_1"},
            },
        },
    }
    result = build_column_specific_domains(model_side_input=model_side, runtime_constraints=constraints)
    assert set(result["domains"]["COL_X"]) == {"SPAN_x7", "SPAN_y7"}
    assert set(result["domains"]["COL_Y"]) == {"SPAN_x7", "SPAN_y7"}


def test_domain_admissibility_uses_materializer_rules() -> None:
    date_column = {"source_type": "DATE"}
    timestamp_column = {"source_type": "TIMESTAMP"}
    integer_column = {"source_type": "INTEGER"}
    for column, candidate in [
        (date_column, {"text": "2024-01-01"}),
        (timestamp_column, {"text": "2022-03-02 10:30:00"}),
        (integer_column, {"text": "5"}),
    ]:
        assert materialize_value(candidate["text"], column["source_type"]).value is not None
        assert column_allows_candidate(column, candidate)[0] is True
    with pytest.raises(V2A1Error):
        materialize_value("2022-03-02", "TIMESTAMP")
    assert column_allows_candidate(timestamp_column, {"text": "2022-03-02"})[0] is False
    assert column_allows_candidate(date_column, {"text": "date 2020-08-10"})[0] is False
    assert column_allows_candidate(date_column, {"text": "'2020-01-01',"})[0] is False
    model_side, constraints = toy_model_side_and_constraints()
    equivalence = audit_admissibility_runtime_equivalence(model_side_input=model_side, runtime_constraints=constraints)
    assert equivalence["admissibility_runtime_mismatch"] == 0


def test_prepare_eng2b_runtime_row_replaces_global_schema_with_dynamic_schema() -> None:
    model_side, constraints = toy_model_side_and_constraints()
    row = {"sample_id": "toy", "model_side_input": model_side, "runtime_constraints": constraints}
    row["runtime_constraints"]["phase_o_schema_sha256"] = sha256_text(json.dumps(constraints["phase_o_schema"], sort_keys=True))
    runtime_row, contract = prepare_eng2b_runtime_row(row)
    assert contract["method_id"] == "M2_FINAL_ENG2B"
    assert contract["generation_schema_sha256"] == contract["parser_schema_sha256"]
    assert contract["eng2b_dynamic_schema_sha256"] == runtime_row["runtime_constraints"]["phase_o_schema_sha256"]
    assert contract["global_schema_sha256"] != contract["eng2b_dynamic_schema_sha256"]


def test_unique_non_omit_span_refs_postparse_guard_remains() -> None:
    enforce_unique_non_omit_span_refs({"COL_1": "SPAN_1", "COL_2": "SPAN_2", "COL_3": "OMIT"})
    with pytest.raises(V2A1Error) as exc:
        enforce_unique_non_omit_span_refs({"COL_1": "SPAN_1", "COL_2": "SPAN_1"})
    assert exc.value.reason_code == "duplicate_span_ref"


def test_candidate_kind_classification() -> None:
    assert candidate_kind("2024-01-01") == "date"
    assert candidate_kind("2022-03-02 10:30:00") == "datetime"
    assert candidate_kind("5") == "integer"
    assert candidate_kind("12.5") == "real"


def test_gretel_free_text_prompts_use_two_frozen_demonstrations() -> None:
    db_path = PROJECT_ROOT / "eng2b_prompt_test_toy.sqlite"
    if db_path.exists():
        db_path.unlink()
    try:
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE items (id INTEGER, status TEXT)")
        con.commit()
        con.close()
        direct_config = json.loads((PROJECT_ROOT / "configs/stage5/resolved_direct_confirmation.json").read_text(encoding="utf-8"))
        jfs_config = json.loads((PROJECT_ROOT / "configs/stage5/resolved_j_fs_confirmation.json").read_text(encoding="utf-8"))
        profile = build_profile(db_path, db_id="toy")
        request = "Insert item 1 with status completed."
        direct_prompt = build_direct_prompt(request, profile, direct_config)
        jfs_prompt = build_legacy_json_prompt(request, profile, jfs_config)
        for prompt in [direct_prompt, jfs_prompt]:
            assert prompt.count("EXAMPLE ") == 4
            assert prompt.count("EXAMPLE 1 INPUT:") == 1
            assert prompt.count("EXAMPLE 2 INPUT:") == 1
            assert "free_plain_insert" not in prompt
            assert "free_conflict_aware" not in prompt
    finally:
        if db_path.exists():
            db_path.unlink()
