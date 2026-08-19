from __future__ import annotations

from pathlib import Path
import json

from nldbwrite_v3.experiments.run_method import (
    MAPPING_METHODS,
    PREFLIGHT_METHODS,
    _load_method_config,
    _prompt_for_sample,
)
from nldbwrite_v3.source_parser import parse_source_payload
from tests.helpers import test_profile


D_CONFIG = {
    "enabled": True,
    "null_literal_policy": "explicit_only",
    "emit_value_provenance": True,
}


def _d(text: str):
    return parse_source_payload(text, structured_parser=D_CONFIG)


def test_v4_config_is_direct_abc_plus_d_ablation() -> None:
    v3, _ = _load_method_config(Path("configs/stage2/v3_update.json"))
    v4, _ = _load_method_config(Path("configs/stage2/v4_structured_parser.json"))
    assert v4["method_id"] == "MP-FS+"
    assert v4["method_id"] in MAPPING_METHODS
    assert v4["method_id"] in PREFLIGHT_METHODS
    assert v4["stage2_interventions"] == v3["stage2_interventions"]
    assert v4["structured_source_parser"] == D_CONFIG
    for key, value in v3.items():
        if key in {"method_variant", "method_version"}:
            continue
        assert v4[key] == value, key


def test_d_off_preserves_frozen_legacy_parser_behavior() -> None:
    request = (
        "operation=plain_insert\n"
        "table=eventsandclub\n"
        "conflict_policy=error\n"
        "row1:\n"
        "id=1\nname=One\n"
        "row2:\n"
        "id=2\nname=Two\n"
    )
    payload = parse_source_payload(request)
    assert len(payload.rows) == 1
    assert payload.rows[0]["id"] == "2"
    assert "structured_source_parser" not in payload.metadata
    null_payload = parse_source_payload("id,note\n1,None")
    assert null_payload.rows[0]["note"] is None


def test_d_row_headings_preserve_all_rows_and_controls() -> None:
    payload = _d(
        "operation=plain_insert\n"
        "table=eventsandclub\n"
        "conflict_policy=error\n"
        "row1:\n"
        "  eventsreg=E1\n"
        "  participation_summary=First\n"
        "row2:\n"
        "  eventsreg=E2\n"
        "  participation_summary=Second\n"
        "row3:\n"
        "  eventsreg=E3\n"
        "  participation_summary=Third\n"
    )
    assert len(payload.collections) == 1
    collection = payload.collections[0]
    assert collection.collection_id == "eventsandclub"
    assert [row["eventsreg"] for row in collection.rows] == ["E1", "E2", "E3"]
    assert collection.metadata["control_metadata"] == [
        {
            "operation": "plain_insert",
            "table": "eventsandclub",
            "conflict_policy": "error",
        }
    ]
    assert collection.metadata["row_ids"] == [
        "SRC_ROW_0001",
        "SRC_ROW_0002",
        "SRC_ROW_0003",
    ]
    assert "row1:" not in payload.instruction_text
    assert "operation=plain_insert" in payload.instruction_text


def test_d_repeated_row_marker_preserves_all_rows() -> None:
    payload = _d(
        "operation = plain_insert\n"
        "policy = all keys are new\n"
        "table = shipments\n"
        "row = 1\n"
        "shipmentregistry = S1\n"
        "routealign = Compliant\n"
        "row = 2\n"
        "shipmentregistry = S2\n"
        "routealign = Under Review\n"
        "row = 3\n"
        "shipmentregistry = S3\n"
        "routealign = Deviation\n"
    )
    assert len(payload.collections) == 1
    assert payload.collections[0].collection_id == "shipments"
    assert [row["shipmentregistry"] for row in payload.rows] == ["S1", "S2", "S3"]
    assert all("row" not in row for row in payload.rows)


def test_d_dotted_rows_do_not_create_bogus_control_collection() -> None:
    payload = _d(
        "operation=insert_ignore\n"
        "table=robot_record\n"
        "conflict_target=recreg\n"
        "update_columns=none\n"
        "policy=R0 exists and R1 is new\n"
        "row_1.recreg=R0\n"
        "row_1.rects=2026-07-30 17:26:01\n"
        "row_1.botcode=A\n"
        "row_2.recreg=R1\n"
        "row_2.rects=2026-07-30 17:26:02\n"
        "row_2.botcode=B\n"
    )
    assert len(payload.collections) == 1
    collection = payload.collections[0]
    assert collection.collection_id == "robot_record"
    assert len(collection.rows) == 2
    assert collection.rows[0]["recreg"] == "R0"
    assert collection.rows[1]["recreg"] == "R1"
    assert "table" not in collection.fields
    assert collection.metadata["control_metadata"][0]["conflict_target"] == "recreg"
    assert collection.metadata["control_metadata"][0]["update_columns"] == "none"


def test_d_colon_control_block_is_metadata_not_data_row() -> None:
    payload = _d(
        "operation: plain_insert\n"
        "table: additionalnotes\n\n"
        "notesreg: NOTE1\n"
        "notesretainpivot: RET1\n"
        "noteinfo: Review the campaign.\n"
    )
    assert len(payload.collections) == 1
    collection = payload.collections[0]
    assert collection.collection_id == "additionalnotes"
    assert collection.rows == [
        {
            "notesreg": "NOTE1",
            "notesretainpivot": "RET1",
            "noteinfo": "Review the campaign.",
        }
    ]
    assert collection.metadata["control_metadata"] == [
        {"operation": "plain_insert", "table": "additionalnotes"}
    ]


def test_d_null_policy_preserves_ambiguous_none_and_records_provenance() -> None:
    payload = _d(
        "id,precipitationtype\n"
        "1,None\n"
        "2,NULL\n"
        "3,nil\n"
    )
    assert [row["precipitationtype"] for row in payload.rows] == ["None", None, "nil"]
    traces = payload.collections[0].metadata["value_provenance"]
    by_row = {
        item["row_index"]: item
        for item in traces
        if item["field"] == "precipitationtype"
    }
    assert by_row[0]["coercion_rule"] == "ambiguous_text_null_preserved"
    assert by_row[0]["raw_value"] == "None"
    assert by_row[0]["normalized_value"] == "None"
    assert by_row[1]["coercion_rule"] == "explicit_text_null_to_null"
    assert by_row[1]["normalized_value"] is None
    assert by_row[0]["row_id"] == "SRC_ROW_0001"


def test_d_quoted_textual_null_marker_remains_text() -> None:
    payload = _d('id,note\n1,"NULL"\n2,"None"')
    assert payload.rows == [
        {"id": "1", "note": "NULL"},
        {"id": "2", "note": "None"},
    ]
    traces = [
        item
        for item in payload.collections[0].metadata["value_provenance"]
        if item["field"] == "note"
    ]
    assert {item["coercion_rule"] for item in traces} == {"quoted_literal_preserved"}


def test_d_typed_json_and_python_nulls_remain_typed_null() -> None:
    json_payload = _d('[{"id":"1","note":null}]')
    python_payload = _d("[{'id': '1', 'note': None}]")
    assert json_payload.rows[0]["note"] is None
    assert python_payload.rows[0]["note"] is None


def test_d_prompt_path_uses_same_parser_config_as_pipeline_config() -> None:
    config, _ = _load_method_config(Path("configs/stage2/v4_structured_parser.json"))
    sample = {
        "input_text": (
            "operation=plain_insert\n"
            "table=parent\n"
            "row1:\nid=1\nname=One\n"
            "row2:\nid=2\nname=Two\n"
        )
    }
    _, payload = _prompt_for_sample("MP-FS+", sample, test_profile(), config)
    assert [row["id"] for row in payload.rows] == ["1", "2"]
    assert payload.metadata["structured_parser_contract"] == "stage2-d-v1"


def test_d_exact_stage1_manual_parser_cases_are_regression_fixtures() -> None:
    fixture = json.loads(
        Path("tests/fixtures/stage2_d_stage1_parser_cases.json").read_text(encoding="utf-8")
    )
    assert len(fixture["cases"]) == 7
    for case in fixture["cases"]:
        payload = _d(case["input_text"])
        assert len(payload.collections) == 1, case["sample_id"]
        collection = payload.collections[0]
        assert collection.collection_id == case["collection_id"], case["sample_id"]
        assert len(collection.rows) == case["row_count"], case["sample_id"]
        if "text_none_count" in case:
            values = [
                row.get("precipitationtype")
                for row in collection.rows
                if "precipitationtype" in row
            ]
            assert values.count("None") == case["text_none_count"], case["sample_id"]
            assert all(value is not None for value in values), case["sample_id"]


def test_d_context_only_table_name_is_not_control_without_strong_signal() -> None:
    payload = _d("table=literal_payload_value\nname=Alpha")
    assert payload.rows == [{"table": "literal_payload_value", "name": "Alpha"}]
    assert payload.collections[0].metadata.get("control_metadata") in (None, [])
