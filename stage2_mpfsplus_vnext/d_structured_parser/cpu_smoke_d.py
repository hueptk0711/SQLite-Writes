from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.run_method import (
    MAPPING_METHODS,
    PREFLIGHT_METHODS,
    _load_method_config,
    _prompt_for_sample,
)
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.source_parser import parse_source_payload


CONFIG = Path("configs/stage2/v4_structured_parser.json")
FIXTURE = Path("tests/fixtures/stage2_d_stage1_parser_cases.json")


def _profile() -> dict:
    return ensure_reference_ids(
        {
            "db_id": "smoke",
            "tables": [
                {
                    "name": "parent",
                    "columns": [
                        {
                            "name": "id",
                            "type": "TEXT",
                            "is_primary_key": True,
                            "is_insertable": True,
                            "semantic_type": "identifier",
                            "preserve_as_text": True,
                        },
                        {
                            "name": "name",
                            "type": "TEXT",
                            "not_null": True,
                            "is_insertable": True,
                            "semantic_type": "text",
                            "preserve_as_text": True,
                        },
                    ],
                    "required_insert_columns": ["id", "name"],
                    "primary_keys": ["id"],
                    "unique_indexes": [
                        {
                            "name": "PRIMARY_KEY",
                            "columns": ["id"],
                            "origin": "pk",
                            "is_primary_key": True,
                        }
                    ],
                    "foreign_keys": [],
                }
            ],
        }
    )


def main() -> None:
    config, _ = _load_method_config(CONFIG)
    assert config["method_id"] == "MP-FS+"
    assert config["method_id"] in MAPPING_METHODS
    assert config["method_id"] in PREFLIGHT_METHODS
    assert config["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": True,
        "update_column_consistency": True,
    }
    parser_config = config["structured_source_parser"]
    assert parser_config["enabled"] is True

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case_results = []
    for case in fixture["cases"]:
        payload = parse_source_payload(
            case["input_text"],
            structured_parser=parser_config,
        )
        assert len(payload.collections) == 1
        collection = payload.collections[0]
        assert collection.collection_id == case["collection_id"]
        assert len(collection.rows) == case["row_count"]
        if "text_none_count" in case:
            values = [
                row.get("precipitationtype")
                for row in collection.rows
                if "precipitationtype" in row
            ]
            assert values.count("None") == case["text_none_count"]
            assert all(value is not None for value in values)
        case_results.append(
            {
                "sample_id": case["sample_id"],
                "collection_id": collection.collection_id,
                "row_count": len(collection.rows),
                "status": "PASS",
            }
        )

    multi_prefix_request = (
        "operation=plain_insert\n"
        "parent.row_1.id=P1\n"
        "child.row_1.name=C1\n"
        "parent.row_2.id=P2\n"
        "child.row_2.name=C2\n"
    )
    legacy_multi_prefix = parse_source_payload(multi_prefix_request)
    d_multi_prefix = parse_source_payload(
        multi_prefix_request,
        structured_parser=parser_config,
    )
    assert [
        (collection.collection_id, collection.rows)
        for collection in d_multi_prefix.collections
    ] == [
        (collection.collection_id, collection.rows)
        for collection in legacy_multi_prefix.collections
    ]
    assert all(
        not ({"id", "name"} <= set(row))
        for collection in d_multi_prefix.collections
        for row in collection.rows
    )

    invalid_operation_payload = parse_source_payload(
        "operation=login\ntable=audit\nname=Alice",
        structured_parser=parser_config,
    )
    assert invalid_operation_payload.rows == [
        {"table": "audit", "name": "Alice"}
    ]

    provenance_cases = {
        "csv": "id,name\n1,Alpha\n2,Beta",
        "markdown": "| id | name |\n|---|---|\n|1|Alpha|\n|2|Beta|",
        "colon_kv": "id: 1\nname: Alpha",
        "equals_kv": "id=1\nname=Alpha",
        "numbered": "1. id: 1, name: Alpha 2. id: 2, name: Beta",
        "bulleted": "- id: 1, name: Alpha; - id: 2, name: Beta",
    }
    provenance_results = {}
    for name, provenance_request in provenance_cases.items():
        provenance_payload = parse_source_payload(
            provenance_request,
            structured_parser=parser_config,
        )
        assert provenance_payload.collections
        total_cells = 0
        total_traces = 0
        for collection in provenance_payload.collections:
            expected_cells = [
                (row_index, field)
                for row_index, row in enumerate(collection.rows)
                for field in row
            ]
            traces = collection.metadata.get("value_provenance") or []
            observed_cells = [
                (trace["row_index"], trace["field"])
                for trace in traces
            ]
            assert sorted(observed_cells) == sorted(expected_cells)
            assert len(observed_cells) == len(set(observed_cells))
            assert all(trace.get("row_id") for trace in traces)
            total_cells += len(expected_cells)
            total_traces += len(traces)
        provenance_results[name] = {
            "cells": total_cells,
            "traces": total_traces,
            "status": "PASS",
        }

    request = (
        "operation=insert_ignore\n"
        "table=parent\n"
        "row1:\nid=p1\nname=One\n"
        "row2:\nid=p2\nname=Two\n"
    )
    profile = _profile()
    _, prompt_payload = _prompt_for_sample(
        "MP-FS+",
        {"input_text": request},
        profile,
        config,
    )
    assert [row["id"] for row in prompt_payload.rows] == ["p1", "p2"]

    predicted = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection_id": "c1",
                "source_selector_id": "s1",
                "table_id": "t1",
                "field_mapping": {
                    "c1.f1": "t1.c1",
                    "c1.f2": "t1.c2",
                },
                "constants": {},
                "write_semantics": "insert_ignore",
                "conflict_target_id": "t1.u1",
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }
    pipeline = MappingFirstPipeline(
        profile,
        reference_planning=True,
        normalization_mode="lossless",
        stage2_interventions=config.get("stage2_interventions"),
        structured_source_parser=parser_config,
    )
    result = pipeline.run(request, predicted)
    assert result.success, result.to_dict()
    assert [row["id"] for row in result.source_payload.rows] == ["p1", "p2"]
    assert result.program is not None
    assert result.program.statements[0].params == ["p1", "One", "p2", "Two"]

    print(
        json.dumps(
            {
                "status": "PASS",
                "config": {
                    "method_id": config["method_id"],
                    "method_variant": config["method_variant"],
                    "method_version": config["method_version"],
                    "structured_source_parser": parser_config,
                },
                "stage1_diagnostic_cases": case_results,
                "d2_trust_boundary": {
                    "multi_prefix_deferred_without_merge": "PASS",
                    "invalid_operation_preserves_table_payload": "PASS",
                    "provenance_formats": provenance_results,
                },
                "prompt_row_count": len(prompt_payload.rows),
                "pipeline_row_count": len(result.source_payload.rows),
                "compiled_params": result.program.statements[0].params,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
