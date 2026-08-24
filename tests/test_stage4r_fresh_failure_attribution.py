from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.analysis.analyze_stage4_fresh_7b import METHOD_SLUGS
from scripts.analysis.run_stage4r_fresh_failure_attribution import run_stage4r
from scripts.analysis.run_stage4r2_actual_dfg1_replay import run_stage4r2


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
                        if sample_id in {"s2", "s4"}
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
            if method_slug == "d_g1_primary" and sample_id in {"s2", "s4"}:
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
            if method_slug == "d_g1_primary" and sample_id == "s5":
                row.update(
                    {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "accepted_output": False,
                        "execution_success": False,
                        "preflight_accepted": False,
                        "error_type": "preflight_abstention",
                        "error_message": "UNIQUE constraint failed: db_alpha.items.id",
                        "preflight": {
                            "error_class": "unique_violation",
                            "error": "UNIQUE constraint failed: db_alpha.items.id",
                        },
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
            if method_slug == "full_secondary" and sample_id == "s4":
                row.update(
                    {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "accepted_output": True,
                        "error_type": "STATE_MISMATCH",
                    }
                )
            if method_slug == "full_secondary" and sample_id == "s5":
                row.update(
                    {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "accepted_output": False,
                        "execution_success": False,
                        "preflight_accepted": False,
                        "error_type": "preflight_abstention",
                        "error_message": "UNIQUE constraint failed: db_alpha.items.id",
                        "preflight": {
                            "error_class": "unique_violation",
                            "error": "UNIQUE constraint failed: db_alpha.items.id",
                        },
                    }
                )
        write_jsonl(result_root / "methods" / method_slug / "evaluation.jsonl", rows)
    write_jsonl(
        result_root / "methods" / "full_secondary" / "materialized_write_plans.jsonl",
        [
            {
                "sample_id": "s1",
                "write_plan": {
                    "reference_trace": {
                        "constrained_reference_repairs": [
                            {
                                "repair_attempted": False,
                                "repair_applied": False,
                                "repair_succeeded": False,
                                "reference_kind": "column",
                                "slot_path": "/target_groups/0/field_mapping/c0.f0",
                                "original_reference": "t1.ignored",
                                "replacement_reference": "t1.ignored",
                                "candidate_set": ["t1.ignored"],
                                "candidate_count": 1,
                                "repair_rule": "unique_exact_identifier_name",
                                "validation_before": "PASS",
                                "validation_after": "PASS",
                            }
                        ]
                    }
                },
            },
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
            },
            {
                "sample_id": "s4",
                "write_plan": {
                    "reference_trace": {
                        "constrained_reference_repairs": [
                            {
                                "repair_attempted": True,
                                "repair_applied": True,
                                "repair_succeeded": True,
                                "reference_kind": "column",
                                "slot_path": "/target_groups/0/field_mapping/c3.f3",
                                "original_reference": "t1.price",
                                "replacement_reference": "t1.c4",
                                "candidate_set": ["t1.c4"],
                                "candidate_count": 1,
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


def write_actual_run(actual_run: Path, sample_ids: list[str]) -> None:
    rows = base_rows(sample_ids)
    for row in rows:
        if row["sample_id"] == "s3":
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
    write_jsonl(actual_run / "evaluation.jsonl", rows)
    write_jsonl(actual_run / "raw_generations.jsonl", [
        {"sample_id": sample_id, "text": "{}"} for sample_id in sample_ids
    ])
    write_jsonl(actual_run / "parsed_mapping_plans.jsonl", [
        {"sample_id": sample_id, "parsed": True} for sample_id in sample_ids
    ])
    write_jsonl(
        actual_run / "materialized_write_plans.jsonl",
        [
            {
                "sample_id": "s1",
                "write_plan": {},
            },
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
            },
            {
                "sample_id": "s3",
                "write_plan": {},
            },
        ],
    )
    verification_rows = [{"sample_id": sample_id, "valid": sample_id != "s3"} for sample_id in sample_ids]
    for row in verification_rows:
        if row["sample_id"] == "s2":
            row["warnings"] = [
                {
                    "error_code": "CONSTRAINED_REFERENCE_REPAIR_APPLIED",
                    "details": {
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
                    },
                }
            ]
        if row["sample_id"] == "s3":
            row["errors"] = [
                {
                    "error_code": "NEEDS_CLARIFICATION",
                    "message": "Downstream verifier error after successful F repair.",
                }
            ]
            row["warnings"] = [
                {
                    "error_code": "CONSTRAINED_REFERENCE_REPAIR_APPLIED",
                    "details": {
                        "repair_attempted": True,
                        "repair_applied": True,
                        "repair_succeeded": True,
                        "reference_kind": "column",
                        "slot_path": "/target_groups/0/field_mapping/c9.f9",
                        "original_reference": "t9.operation",
                        "replacement_reference": "t9.c9",
                        "candidate_set": ["t9.c9"],
                        "candidate_count": 1,
                        "repair_rule": "unique_exact_identifier_name",
                        "validation_before": "UNKNOWN_COLUMN_ID",
                        "validation_after": "PASS",
                    },
                }
            ]
    write_jsonl(actual_run / "verification.jsonl", verification_rows)
    write_jsonl(actual_run / "compiled_programs.jsonl", [
        {"sample_id": sample_id, "program": []} for sample_id in sample_ids
    ])
    write_jsonl(actual_run / "execution_logs.jsonl", [
        {
            "sample_id": sample_id,
            "preflight": {"accepted": sample_id != "s3", "error_class": None},
        }
        for sample_id in sample_ids
    ])
    for name in ("metrics.json", "manifest.json", "run_lock.json", "config.json"):
        (actual_run / name).write_text(
            json.dumps({"sample_count": len(sample_ids)}, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )


def test_stage4r_outputs_f_repair_and_failure_attribution(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3", "s4", "s5"]
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
    assert summary["F_activation_sample_count"] == 2
    assert summary["F_exact_name_repair_count"] == 2
    assert summary["F_rescue_count"] == 1
    assert summary["FULL_vs_D_G1_paired_counts"]["rescue"] == 1
    assert summary["D_F_G1_diagnostic"]["D_G1_correct"] == 1
    assert summary["D_F_G1_diagnostic"]["D_F_G1_correct"] == 2
    assert summary["D_F_G1_diagnostic"]["FULL_correct"] == 2
    assert summary["D_F_G1_diagnostic"]["D_G1_to_D_F_G1_rescue"] == 1
    assert summary["D_F_G1_diagnostic"]["D_G1_to_D_F_G1_regression"] == 0
    assert summary["D_F_G1_diagnostic"]["D_F_G1_to_FULL_rescue"] == 0
    assert summary["D_F_G1_diagnostic"]["D_F_G1_to_FULL_regression"] == 0
    assert summary["hit_max_new_tokens_by_method"]["d_g1_primary"] == 1
    f_rows = read_csv_rows(output / "f_activation_sample_level.csv")
    assert "FULL_accepted_output" in f_rows[0]
    outcomes = {row["sample_id"]: row["FULL_vs_D_G1_outcome"] for row in f_rows}
    assert outcomes == {"s2": "rescue", "s4": "false_accept"}
    failure_rows = read_csv_rows(output / "d_g1_failure_sample_level.csv")
    assert {row["sample_id"] for row in failure_rows} == {"s2", "s3", "s4", "s5"}
    assert "dependency_sensitive" in failure_rows[0]
    assert {
        row["error_family"] for row in failure_rows
    } == {
        "schema_reference_grounding",
        "max_token_hit_associated",
        "preflight_rejection",
    }
    assert any(
        row["sample_id"] == "s5"
        and row["preflight_rejection_reason"] == "unique_constraint"
        for row in failure_rows
    )
    manifest = json.loads((output / "analysis_manifest.json").read_text())
    assert "analysis_manifest.json" not in manifest["artifacts"]
    dependency_rows = read_csv_rows(output / "failure_by_dependency_sensitive.csv")
    assert {row["dependency_sensitive"] for row in dependency_rows} == {"0", "1"}
    family_input_rows = read_csv_rows(output / "failure_family_x_input_type.csv")
    assert any(
        row["error_family"] == "schema_reference_grounding"
        and row["input_type"] == "semi_structured"
        for row in family_input_rows
    )
    preflight_rows = read_csv_rows(output / "preflight_rejection_summary.csv")
    assert preflight_rows == [
        {
            "preflight_rejection_reason": "unique_constraint",
            "count": "1",
            "rate_among_preflight_rejections": "1.0",
        }
    ]
    hit_summary = read_csv_rows(output / "hit_max_new_tokens_summary.csv")
    assert {
        row["label"] for row in hit_summary
    } == {"max_token_hit_associated_cases"}
    hit_rows = read_csv_rows(output / "hit_max_new_tokens_samples.csv")
    assert any(
        row["method_slug"] == "d_g1_primary" and row["sample_id"] == "s3"
        for row in hit_rows
    )


def test_stage4r_d_f_g1_diagnostic_config_is_narrow() -> None:
    config = json.loads(
        Path("configs/stage4/d_f_g1_diagnostic.json").read_text(encoding="utf-8")
    )

    assert config["stage2_interventions"] == {
        "control_field_roles": False,
        "explicit_conflict_preservation": False,
        "update_column_consistency": False,
    }
    assert config["structured_source_parser"]["enabled"] is True
    assert config["free_text_typed_normalization"]["enabled"] is False
    assert config["constrained_reference_repair"]["enabled"] is True
    assert config["diagnostic_targeted_repair"]["enabled"] is True
    assert config["diagnostic_targeted_repair"]["evidence_span_boundary"] is True
    assert config["diagnostic_targeted_repair"]["evidence_span_selection"] is False
    assert config["diagnostic_targeted_repair"]["max_revalidation_attempts"] == 1


def test_stage4r2_actual_replay_comparison_from_existing_run(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    actual_run = tmp_path / "actual_run"
    output = tmp_path / "stage4r2"
    config = tmp_path / "d_f_g1_config.json"
    write_protocol(protocol, sample_ids)
    write_results(results, sample_ids)
    write_actual_run(actual_run, sample_ids)
    config.write_text(
        json.dumps({"method_id": "MP-FS+"}, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )

    summary = run_stage4r2(
        protocol_root=protocol,
        result_root=results,
        config_path=config,
        output_dir=output,
        actual_run_dir=actual_run,
        skip_replay=True,
    )

    assert summary["model_called"] is False
    assert summary["D_G1_correct"] == 1
    assert summary["ACTUAL_D_F_G1_correct"] == 2
    assert summary["FULL_correct"] == 2
    assert summary["D_G1_to_ACTUAL_D_F_G1_rescue"] == 1
    assert summary["D_G1_to_ACTUAL_D_F_G1_regression"] == 0
    assert summary["ACTUAL_D_F_G1_to_FULL_rescue"] == 0
    assert summary["ACTUAL_D_F_G1_to_FULL_regression"] == 0
    assert summary["F_attempt_sample_count"] == 2
    assert summary["F_attempt_count"] == 2
    assert summary["F_exact_name_attempt_sample_count"] == 2
    assert summary["F_exact_name_attempt_count"] == 2
    assert summary["F_applied_sample_count"] == 2
    assert summary["F_applied_exact_name_repair_count"] == 2
    assert summary["F_materialized_sample_count"] == 1
    assert summary["F_materialized_exact_name_repair_count"] == 1
    assert summary["F_state_rescue_count"] == 1
    assert summary["F_state_regression_count"] == 0
    assert (output / "d_f_g1_actual_evaluation.jsonl").is_file()
    assert (output / "d_f_g1_actual_preflight.jsonl").is_file()
    attempt_rows = read_csv_rows(output / "f_attempts.csv")
    assert {row["sample_id"] for row in attempt_rows} == {"s2", "s3"}
    applied_rows = read_csv_rows(output / "f_applied_repairs.csv")
    assert {row["sample_id"] for row in applied_rows} == {"s2", "s3"}
    materialized_rows = read_csv_rows(output / "f_materialized_repairs.csv")
    assert [row["sample_id"] for row in materialized_rows] == ["s2"]
    sample_outcomes = read_csv_rows(output / "f_sample_outcomes.csv")
    assert {
        row["sample_id"]: row["D_G1_to_ACTUAL_D_F_G1"] for row in sample_outcomes
    } == {"s2": "rescue", "s3": "both_wrong"}
    repair_rows = read_csv_rows(output / "d_f_g1_actual_f_repairs.csv")
    assert repair_rows[0]["repair_rule"] == "unique_exact_identifier_name"
    assert [row["sample_id"] for row in repair_rows] == ["s2"]
    paired_rows = read_csv_rows(output / "d_g1_actual_full_paired_summary.csv")
    assert paired_rows[0]["comparison"] == "D_G1_to_ACTUAL_D_F_G1"
    assert paired_rows[0]["rescue"] == "1"
