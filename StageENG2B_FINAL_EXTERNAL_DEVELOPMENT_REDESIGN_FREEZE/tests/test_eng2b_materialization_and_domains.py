from __future__ import annotations

import json
from pathlib import Path

import pytest

from nldbwrite_v3.experiments.prompts import build_direct_prompt, build_legacy_json_prompt
from nldbwrite_v3.schema.profile import build_profile
from nldbwrite_v3.v2_a1.eng2b_candidate_domains import (
    build_column_specific_domains,
    candidate_kind,
    canonical_boundary_text,
    dynamic_schema_with_column_domains,
)
from nldbwrite_v3.v2_a1.typed_materializer import (
    enforce_unique_non_omit_span_refs,
    materialize_value,
    semantic_materialization_type,
    sqlite_affinity,
)
from nldbwrite_v3.v2_a1.types import V2A1Error


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENG2A_STAGE = PROJECT_ROOT / "StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT"


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


def test_column_specific_domains_filter_by_declared_type_and_boundary() -> None:
    row = {
        "sample_id": "toy",
        "model_side_input": {
            "schema_inventory": {
                "columns": [
                    {"column_ref": "COL_1", "column_name": "id", "source_type": "INTEGER", "nullable": False, "table_ref": "TAB_1"},
                    {"column_ref": "COL_2", "column_name": "start_date", "source_type": "DATE", "nullable": False, "table_ref": "TAB_1"},
                    {"column_ref": "COL_3", "column_name": "status", "source_type": "TEXT", "nullable": True, "table_ref": "TAB_1"},
                ]
            }
        },
        "runtime_constraints": {
            "candidate_inventory": [
                {"span_ref": "SPAN_1", "text": "5"},
                {"span_ref": "SPAN_2", "text": "2024-01-01"},
                {"span_ref": "SPAN_3", "text": "'completed'"},
                {"span_ref": "SPAN_4", "text": "completed"},
                {"span_ref": "SPAN_5", "text": "$30000"},
            ],
            "phase_o_schema": {
                "type": "object",
                "properties": {
                    "column_span_refs": {
                        "type": "object",
                        "properties": {
                            "COL_1": {"type": "string", "enum": ["SPAN_1", "SPAN_2", "SPAN_3", "SPAN_4", "SPAN_5"]},
                            "COL_2": {"type": "string", "enum": ["SPAN_1", "SPAN_2", "SPAN_3", "SPAN_4", "SPAN_5"]},
                            "COL_3": {"type": "string", "enum": ["OMIT", "SPAN_1", "SPAN_2", "SPAN_3", "SPAN_4", "SPAN_5"]},
                        },
                    }
                },
            },
        },
        "label_side_expected": {"phase_o": {"column_span_refs": {}}},
    }
    result = build_column_specific_domains(row)
    assert result["domains"]["COL_1"] == ["SPAN_1"]
    assert result["domains"]["COL_2"] == ["SPAN_2"]
    assert "OMIT" in result["domains"]["COL_3"]
    assert "SPAN_4" in result["domains"]["COL_3"]
    assert "SPAN_3" not in result["domains"]["COL_3"]
    schema = dynamic_schema_with_column_domains(row, result["domains"])
    assert schema["x-eng2b-span-uniqueness"].startswith("prefix decoder")


def test_unique_non_omit_span_refs_enforced() -> None:
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
    direct_config = json.loads((PROJECT_ROOT / "configs/stage5/resolved_direct_confirmation.json").read_text(encoding="utf-8"))
    jfs_config = json.loads((PROJECT_ROOT / "configs/stage5/resolved_j_fs_confirmation.json").read_text(encoding="utf-8"))
    row = json.loads((ENG2A_STAGE / "ENG2A_PILOT_100_FREEZE.jsonl").read_text(encoding="utf-8").splitlines()[0])
    profile = build_profile(ENG2A_STAGE / row["synthetic_db_spec"]["sqlite_db_path"], db_id=row["sample_id"])
    request = row["model_side_input"]["question"]
    direct_prompt = build_direct_prompt(request, profile, direct_config)
    jfs_prompt = build_legacy_json_prompt(request, profile, jfs_config)
    for prompt in [direct_prompt, jfs_prompt]:
        assert prompt.count("EXAMPLE ") == 4
        assert prompt.count("EXAMPLE 1 INPUT:") == 1
        assert prompt.count("EXAMPLE 2 INPUT:") == 1
        assert "free_plain_insert" not in prompt
        assert "free_conflict_aware" not in prompt
