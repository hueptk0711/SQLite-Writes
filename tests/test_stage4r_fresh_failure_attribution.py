from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.analysis.analyze_stage4_fresh_7b import METHOD_SLUGS
from scripts.analysis.run_stage4r_fresh_failure_attribution import run_stage4r


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_protocol(protocol_root: Path, sample_ids: list[str]) -> None:
    (protocol_root / "data").mkdir(parents=True)
    (protocol_root / "data" / "fresh_sample_ids.txt").write_text(
        "\n".join(sample_ids) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (protocol_root / "prompt_audit").mkdir(parents=True)
    with (protocol_root / "prompt_audit" / "prompt_manifest.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "source_group",
                "db_id",
                "method_slug",
                "detected_mode",
                "operation_type",
                "dependency_sensitive",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        for sample_id in sample_ids:
            for method_slug in METHOD_SLUGS:
                writer.writerow(
                    {
                        "sample_id": sample_id,
                        "source_group": "g1" if sample_id != "s3" else "g2",
                        "db_id": "db_alpha" if sample_id != "s3" else "db_beta",
                        "method_slug": method_slug,
                        "detected_mode": "semi_structured"
                        if sample_id == "s2"
                        else "free_text",
                        "operation_type": "plain_insert"
                        if sample_id != "s3"
                        else "insert_ignore",
                        "dependency_sensitive": "1" if sample_id == "s3" else "0",
                    }
                )


def base_rows(sample_ids: list[str]) -> list[dict[str, object]]:
    return [
        {
            "sample_id": sample_id,
            "target_state_correct": True,
            "strict_full_state_correct": True,
            "accepted_output": True,
            "execution_success": True,
            "parse_success": True,
            "build_success": True,
            "preflight_accepted": True,
            "any_off_target_change": False,
            "generation_status": "success",
            "input_truncated": False,
            "hit_max_new_tokens": False,
            "output_tokens": 12,
            "input_tokens": 100,
            "error_type": "",
        }
        for sample_id in sample_ids
    ]


def write_results(result_root: Path, sample_ids: list[str]) -> None:
    for method_slug in METHOD_SLUGS:
        rows = base_rows(sample_ids)
        for row in rows:
            sample_id = str(row["sample_id"])
            if method_slug == "d_g1_primary" and sample_id == "s2":
                row.update(
                    {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "build_success": False,
                        "error_type": "UNKNOWN_COLUMN_ID",
                    }
                )
            if method_slug == "d_g1_primary" and sample_id == "s3":
                row.update(
                    {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "parse_success": False,
                        "hit_max_new_tokens": True,
                        "output_tokens": 4096,
                        "error_type": "PARSE_ERROR",
                    }
                )
            if method_slug == "full_secondary" and sample_id == "s3":
                row.update(
                    {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "parse_success": False,
                        "hit_max_new_tokens": True,
                        "output_tokens": 4096,
                        "error_type": "PARSE_ERROR",
                    }
                )
        write_jsonl(result_root / "methods" / method_slug / "evaluation.jsonl", rows)
    write_jsonl(
        result_root / "methods" / "full_secondary" / "materialized_write_plans.jsonl",
        [
            {
                "sample_id": "s2",
                "write_plan": {
                    "reference_trace": {
                        "constrained_reference_repairs": [
                            {
                                "repair_attempted": True,
                                "repair_applied": True,
                                "repair_succeeded": True,
                                "reference_kind": "column",
                                "slot_path": "/target_groups/0/field_mapping/c1.f1",
                                "original_reference": "t1.amount",
                                "replacement_reference": "t1.c2",
                                "candidate_set": ["t1.c1", "t1.c2"],
                                "candidate_count": 2,
                                "repair_rule": "unique_exact_identifier_name",
                                "validation_before": "UNKNOWN_COLUMN_ID",
                                "validation_after": "PASS",
                            }
                        ]
                    }
                },
            }
        ],
    )


def test_stage4r_outputs_f_repair_and_failure_attribution(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "stage4r"
    write_protocol(protocol, sample_ids)
    write_results(results, sample_ids)

    summary = run_stage4r(
        protocol_root=protocol,
        result_root=results,
        output_dir=output,
    )

    assert summary["model_called"] is False
    assert summary["F_activation_sample_count"] == 1
    assert summary["F_exact_name_repair_count"] == 1
    assert summary["F_rescue_count"] == 1
    assert summary["FULL_vs_D_G1_paired_counts"]["rescue"] == 1
    assert summary["hit_max_new_tokens_by_method"]["d_g1_primary"] == 1
    f_rows = read_csv_rows(output / "f_activation_sample_level.csv")
    assert f_rows[0]["sample_id"] == "s2"
    assert f_rows[0]["FULL_vs_D_G1_outcome"] == "rescue"
    failure_rows = read_csv_rows(output / "d_g1_failure_sample_level.csv")
    assert {row["sample_id"] for row in failure_rows} == {"s2", "s3"}
    assert {
        row["error_family"] for row in failure_rows
    } == {"schema_reference_grounding", "output_length"}
    hit_rows = read_csv_rows(output / "hit_max_new_tokens_samples.csv")
    assert any(
        row["method_slug"] == "d_g1_primary" and row["sample_id"] == "s3"
        for row in hit_rows
    )
