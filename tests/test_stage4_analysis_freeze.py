from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.analysis.analyze_stage4_fresh_7b import (
    METHOD_SLUGS,
    analyze_result_root,
    cluster_bootstrap_accuracy_difference,
)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


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
        fieldnames = [
            "sample_id",
            "source_group",
            "db_id",
            "method_slug",
            "detected_mode",
            "operation_type",
            "dependency_sensitive",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for sample_id in sample_ids:
            for method_slug in METHOD_SLUGS:
                writer.writerow(
                    {
                        "sample_id": sample_id,
                        "source_group": "cluster_a" if sample_id in {"s1", "s2"} else "cluster_b",
                        "db_id": "db_a" if sample_id != "s3" else "db_b",
                        "method_slug": method_slug,
                        "detected_mode": "free_text" if sample_id != "s2" else "semi_structured",
                        "operation_type": "plain_insert" if sample_id != "s3" else "upsert_update",
                        "dependency_sensitive": "1" if sample_id == "s3" else "0",
                    }
                )
    (protocol_root / "analysis").mkdir(parents=True)
    with (protocol_root / "analysis" / "d_parser_opportunity_audit.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "source_group",
                "legacy_payload_hash",
                "D_payload_hash",
                "changed",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        for sample_id in sample_ids:
            writer.writerow(
                {
                    "sample_id": sample_id,
                    "source_group": "cluster_a",
                    "legacy_payload_hash": "old",
                    "D_payload_hash": "new",
                    "changed": "1" if sample_id == "s2" else "0",
                }
            )


def base_rows(sample_ids: list[str]) -> list[dict[str, object]]:
    template = {
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
        "error_type": "",
    }
    return [{"sample_id": sample_id, **template} for sample_id in sample_ids]


def write_results(
    result_root: Path,
    sample_ids: list[str],
    overrides: dict[str, dict[str, dict[str, object]]] | None = None,
) -> None:
    overrides = overrides or {}
    for method_slug in METHOD_SLUGS:
        rows = base_rows(sample_ids)
        for row in rows:
            row.update(overrides.get(method_slug, {}).get(str(row["sample_id"]), {}))
        write_jsonl(result_root / "methods" / method_slug / "evaluation.jsonl", rows)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_target_and_strict_primary_metrics_are_separate(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "analysis"
    write_protocol(protocol, sample_ids)
    write_results(
        results,
        sample_ids,
        {
            "direct": {
                "s1": {
                    "target_state_correct": True,
                    "strict_full_state_correct": False,
                    "any_off_target_change": True,
                }
            }
        },
    )

    analyze_result_root(protocol, results, output)

    direct = {
        row["method_slug"]: row
        for row in read_csv_rows(output / "variant_metrics.csv")
    }["direct"]
    assert float(direct["target_state_accuracy"]) == pytest.approx(1.0)
    assert float(direct["strict_full_state_accuracy"]) == pytest.approx(2 / 3)
    assert int(direct["off_target_state_change_count"]) == 1


def test_false_accept_and_selective_metrics_are_frozen(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "analysis"
    write_protocol(protocol, sample_ids)
    write_results(
        results,
        sample_ids,
        {
            "direct": {
                "s1": {"target_state_correct": False, "accepted_output": True},
                "s2": {"target_state_correct": True, "accepted_output": False},
            }
        },
    )

    analyze_result_root(protocol, results, output)

    direct = {
        row["method_slug"]: row
        for row in read_csv_rows(output / "variant_metrics.csv")
    }["direct"]
    assert int(direct["false_accept_count"]) == 1
    assert float(direct["false_accept_rate"]) == pytest.approx(1 / 3)
    assert float(direct["coverage"]) == pytest.approx(2 / 3)
    assert float(direct["accepted_output_accuracy"]) == pytest.approx(1 / 2)


def test_missing_sample_stops_instead_of_using_intersection(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "analysis"
    write_protocol(protocol, sample_ids)
    write_results(results, sample_ids)
    write_jsonl(
        results / "methods" / "d_g1_primary" / "evaluation.jsonl",
        base_rows(["s1", "s2"]),
    )

    with pytest.raises(SystemExit, match="STOP: method d_g1_primary"):
        analyze_result_root(protocol, results, output)


def test_duplicate_sample_stops(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "analysis"
    write_protocol(protocol, sample_ids)
    write_results(results, sample_ids)
    duplicate_rows = base_rows(["s1", "s1", "s3"])
    write_jsonl(
        results / "methods" / "direct" / "evaluation.jsonl",
        duplicate_rows,
    )

    with pytest.raises(SystemExit, match="duplicate sample IDs"):
        analyze_result_root(protocol, results, output)


def test_subgroup_metrics_and_intervention_summary(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "analysis"
    write_protocol(protocol, sample_ids)
    write_results(
        results,
        sample_ids,
        {
            "d_g1_primary": {
                "s2": {"target_state_correct": False, "strict_full_state_correct": False}
            }
        },
    )
    write_jsonl(
        results / "methods" / "d_g1_primary" / "materialized_write_plans.jsonl",
        [
            {
                "sample_id": "s2",
                "write_plan": {
                    "reference_trace": {
                        "diagnostic_targeted_repairs": [
                            {
                                "stage2_intervention": "G1_evidence_span_boundary_repair",
                                "repair_rule": "terminal_punctuation_trim",
                                "repair_attempted": True,
                                "repair_applied": True,
                                "repair_succeeded": True,
                            }
                        ]
                    }
                },
            }
        ],
    )

    analyze_result_root(protocol, results, output)

    subgroup_rows = read_csv_rows(output / "subgroup_metrics.csv")
    semi = [
        row
        for row in subgroup_rows
        if row["method_slug"] == "d_g1_primary"
        and row["subgroup_type"] == "input_type"
        and row["subgroup_value"] == "semi_structured"
    ][0]
    assert int(semi["n"]) == 1
    assert float(semi["target_state_accuracy"]) == pytest.approx(0.0)
    intervention = read_csv_rows(output / "intervention_summary.csv")[0]
    assert int(intervention["D_activation_count"]) == 1
    assert int(intervention["G1_attempts"]) == 1
    assert int(intervention["G1_applied"]) == 1
    assert int(intervention["G1_revalidation_success"]) == 1
    assert int(intervention["G1_final_state_incorrect_after_application"]) == 1


def test_primary_paired_analysis_reports_both_metrics(tmp_path: Path) -> None:
    sample_ids = ["s1", "s2", "s3"]
    protocol = tmp_path / "protocol"
    results = tmp_path / "results"
    output = tmp_path / "analysis"
    write_protocol(protocol, sample_ids)
    write_results(
        results,
        sample_ids,
        {
            "original_mp_fs_plus": {"s1": {"target_state_correct": False}},
            "d_g1_primary": {
                "s2": {"strict_full_state_correct": False},
            },
        },
    )

    analyze_result_root(protocol, results, output)

    primary = json.loads((output / "primary_paired_analysis.json").read_text(encoding="utf-8"))
    assert sorted(primary["metrics"]) == [
        "strict_full_state_correct",
        "target_state_correct",
    ]
    assert primary["metrics"]["target_state_correct"]["D_G1_only_correct"] == 1
    assert primary["metrics"]["strict_full_state_correct"]["original_only_correct"] == 1


def test_cluster_bootstrap_fixed_seed_remains_deterministic() -> None:
    baseline = {"s1": True, "s2": False, "s3": False, "s4": True}
    method = {"s1": True, "s2": True, "s3": False, "s4": False}
    source_groups = {"s1": "g1", "s2": "g1", "s3": "g2", "s4": "g3"}
    first = cluster_bootstrap_accuracy_difference(
        baseline=baseline,
        method=method,
        source_groups=source_groups,
        replicates=200,
        seed=240822,
    )
    second = cluster_bootstrap_accuracy_difference(
        baseline=baseline,
        method=method,
        source_groups=source_groups,
        replicates=200,
        seed=240822,
    )
    assert first == second
    assert first["observed_difference"] == 0.0
