#!/usr/bin/env python3
"""CPU-only Stage-4R fresh failure attribution audit.

This post-hoc audit is intentionally read-only with respect to Stage-4 fresh
raw generations, evaluations, configs, and protocol artifacts. It consumes the
accepted frozen Stage-4 result root and emits focused attribution tables for:

* FULL-vs-D_G1 paired rescues/regressions;
* constrained reference repair (F) activations and exact-name repairs;
* D_G1 fresh failure taxonomy by family, input type, operation, and database;
* max-new-token output-length failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.analyze_stage4_fresh_7b import (  # noqa: E402
    METHOD_SLUGS,
    PRIMARY_METHOD,
    first_failure_stage,
    load_frozen_sample_ids,
    load_method_rows,
    load_sample_metadata,
    metric_bool,
    read_jsonl,
    rate,
    truthy,
    unique_dicts,
)
from scripts.analysis.run_stage3_causal_replay import write_csv, write_json  # noqa: E402

FULL_METHOD = "full_secondary"
F_REPAIR_RULE = "unique_exact_identifier_name"
STAGE4R_ARTIFACTS = (
    "stage4r_summary.json",
    "analysis_manifest.json",
    "f_activation_sample_level.csv",
    "f_exact_name_repairs.csv",
    "full_vs_dg1_paired_summary.csv",
    "full_vs_dg1_paired_sample_level.csv",
    "d_g1_failure_taxonomy.csv",
    "d_g1_failure_sample_level.csv",
    "hit_max_new_tokens_summary.csv",
    "hit_max_new_tokens_samples.csv",
)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def canonical_error_family(row: Mapping[str, Any]) -> str:
    error_type = str(row.get("error_type") or "").strip()
    stage = first_failure_stage(row)
    if truthy(row.get("hit_max_new_tokens")):
        return "output_length"
    if error_type in {"UNKNOWN_COLUMN_ID", "UNKNOWN_EVIDENCE_ID"}:
        return "schema_reference_grounding"
    if error_type == "UNRESOLVED_SOURCE_FIELD":
        return "source_field_grounding"
    if error_type == "LOSSY_NORMALIZATION_REJECTED":
        return "normalization"
    if error_type in {"MISSING_REQUIRED_COLUMN", "MISSING_UPDATE_COLUMN_IDS"}:
        return "missing_write_semantics"
    if error_type == "NEEDS_CLARIFICATION":
        return "ambiguity_or_insufficient_information"
    if error_type in {
        "foreign_key_violation",
        "unique_violation",
        "not_null_violation",
        "check_violation",
        "unclassified_constraint_or_execution_error",
    }:
        return "constraint_or_execution"
    if stage == "parse":
        return "parse_or_format"
    if stage == "build":
        return "build_or_materialization"
    if stage == "preflight":
        return "preflight_rejection"
    if stage == "execution":
        return "execution_failure"
    if stage == "state_mismatch":
        return "accepted_state_mismatch"
    return "other_or_unclassified"


def iter_constrained_reference_repair_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "constrained_reference_repairs" and isinstance(nested, list):
                yield from (item for item in nested if isinstance(item, dict))
            yield from iter_constrained_reference_repair_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_constrained_reference_repair_dicts(nested)


def iter_f_repair_traces(method_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    # Use the materialized write plans only. Verification artifacts only retain
    # samples that survive later stages, so scanning both would overstate the
    # number of FULL/F constrained-reference repair activations.
    path = method_dir / "materialized_write_plans.jsonl"
    if not path.is_file():
        return
    for row in read_jsonl(path):
        sample_id = str(row.get("sample_id") or "")
        candidates = [
            item
            for item in iter_constrained_reference_repair_dicts(row)
            if item.get("repair_rule") == F_REPAIR_RULE
            and "original_reference" in item
            and "replacement_reference" in item
            and "repair_attempted" in item
            and "repair_applied" in item
            and "repair_succeeded" in item
        ]
        for candidate in unique_dicts(candidates):
            yield sample_id, candidate


def f_repair_rows(result_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    method_dir = result_root / "methods" / FULL_METHOD
    for sample_id, trace in iter_f_repair_traces(method_dir):
        key = (
            sample_id,
            str(trace.get("slot_path") or ""),
            str(trace.get("original_reference") or ""),
            str(trace.get("replacement_reference") or ""),
            str(trace.get("repair_rule") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "sample_id": sample_id,
                "repair_rule": str(trace.get("repair_rule") or ""),
                "reference_kind": str(trace.get("reference_kind") or ""),
                "slot_path": str(trace.get("slot_path") or ""),
                "original_reference": str(trace.get("original_reference") or ""),
                "replacement_reference": str(trace.get("replacement_reference") or ""),
                "candidate_count": trace.get("candidate_count", ""),
                "candidate_set": "|".join(str(item) for item in trace.get("candidate_set") or []),
                "repair_attempted": int(truthy(trace.get("repair_attempted"))),
                "repair_applied": int(truthy(trace.get("repair_applied"))),
                "repair_succeeded": int(truthy(trace.get("repair_succeeded"))),
                "validation_before": str(trace.get("validation_before") or ""),
                "validation_after": str(trace.get("validation_after") or ""),
            }
        )
    return sorted(rows, key=lambda row: (row["sample_id"], row["slot_path"]))


def full_vs_dg1_rows(
    *,
    dg1_rows: Sequence[Mapping[str, Any]],
    full_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    dg1_by_id = {str(row["sample_id"]): row for row in dg1_rows}
    full_by_id = {str(row["sample_id"]): row for row in full_rows}
    if set(dg1_by_id) != set(full_by_id):
        raise SystemExit("STOP: FULL and D_G1 sample IDs differ")
    rows: list[dict[str, Any]] = []
    counts = Counter()
    for sample_id in sorted(dg1_by_id):
        dg1 = dg1_by_id[sample_id]
        full = full_by_id[sample_id]
        dg1_correct = metric_bool(dg1, "target_state_correct")
        full_correct = metric_bool(full, "target_state_correct")
        if dg1_correct and full_correct:
            outcome = "both_correct"
        elif dg1_correct and not full_correct:
            outcome = "regression"
        elif full_correct and not dg1_correct:
            outcome = "rescue"
        else:
            outcome = "both_wrong"
        counts[outcome] += 1
        if outcome in {"rescue", "regression"}:
            rows.append(
                {
                    "sample_id": sample_id,
                    "paired_outcome": outcome,
                    "d_g1_target_state_correct": int(dg1_correct),
                    "full_target_state_correct": int(full_correct),
                    "d_g1_first_failure_stage": first_failure_stage(dg1),
                    "full_first_failure_stage": first_failure_stage(full),
                    "d_g1_error_type": str(dg1.get("error_type") or ""),
                    "full_error_type": str(full.get("error_type") or ""),
                    "d_g1_accepted_output": int(truthy(dg1.get("accepted_output"))),
                    "full_accepted_output": int(truthy(full.get("accepted_output"))),
                    "full_false_accept": int(
                        truthy(full.get("accepted_output")) and not full_correct
                    ),
                }
            )
    for key in ("both_correct", "both_wrong", "rescue", "regression"):
        counts.setdefault(key, 0)
    counts["paired_sample_count"] = len(dg1_by_id)
    return rows, dict(counts)


def build_f_activation_sample_rows(
    *,
    f_repairs: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Mapping[str, Any]],
    dg1_rows: Sequence[Mapping[str, Any]],
    full_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_sample: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in f_repairs:
        by_sample[str(row["sample_id"])].append(row)
    dg1_by_id = {str(row["sample_id"]): row for row in dg1_rows}
    full_by_id = {str(row["sample_id"]): row for row in full_rows}
    output: list[dict[str, Any]] = []
    for sample_id in sorted(by_sample):
        repairs = by_sample[sample_id]
        base = metadata[sample_id]
        dg1_correct = metric_bool(dg1_by_id[sample_id], "target_state_correct")
        full_correct = metric_bool(full_by_id[sample_id], "target_state_correct")
        output.append(
            {
                "sample_id": sample_id,
                "db_id": base["db_id"],
                "input_type": base["input_type"],
                "operation_type": base["operation_type"],
                "dependency_sensitive": base["dependency_sensitive"],
                "F_exact_name_repair_count": len(repairs),
                "F_applied_count": sum(truthy(row["repair_applied"]) for row in repairs),
                "F_succeeded_count": sum(truthy(row["repair_succeeded"]) for row in repairs),
                "D_G1_target_state_correct": int(dg1_correct),
                "FULL_target_state_correct": int(full_correct),
                "FULL_vs_D_G1_outcome": (
                    "rescue"
                    if full_correct and not dg1_correct
                    else "regression"
                    if dg1_correct and not full_correct
                    else "both_correct"
                    if full_correct
                    else "fail_closed"
                ),
                "original_references": "|".join(
                    sorted({str(row["original_reference"]) for row in repairs})
                ),
                "replacement_references": "|".join(
                    sorted({str(row["replacement_reference"]) for row in repairs})
                ),
            }
        )
    return output


def build_dg1_failure_sample_rows(
    *,
    dg1_rows: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in dg1_rows:
        if metric_bool(row, "target_state_correct"):
            continue
        sample_id = str(row["sample_id"])
        base = metadata[sample_id]
        output.append(
            {
                "sample_id": sample_id,
                "source_group": base["source_group"],
                "db_id": base["db_id"],
                "input_type": base["input_type"],
                "operation_type": base["operation_type"],
                "dependency_sensitive": base["dependency_sensitive"],
                "first_failure_stage": first_failure_stage(row),
                "error_type": str(row.get("error_type") or ""),
                "error_family": canonical_error_family(row),
                "accepted_output": int(truthy(row.get("accepted_output"))),
                "false_accept": int(
                    truthy(row.get("accepted_output"))
                    and not metric_bool(row, "target_state_correct")
                ),
                "hit_max_new_tokens": int(truthy(row.get("hit_max_new_tokens"))),
                "output_tokens": row.get("output_tokens", ""),
                "input_tokens": row.get("input_tokens", ""),
            }
        )
    return output


def grouped_taxonomy_rows(
    failure_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    total = len(failure_rows)
    counts = Counter(
        (
            str(row["error_family"]),
            str(row["error_type"]),
            str(row["first_failure_stage"]),
            str(row["input_type"]),
            str(row["operation_type"]),
            str(row["db_id"]),
        )
        for row in failure_rows
    )
    output = []
    for (
        error_family,
        error_type,
        first_failure,
        input_type,
        operation_type,
        db_id,
    ), count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        output.append(
            {
                "error_family": error_family,
                "error_type": error_type,
                "first_failure_stage": first_failure,
                "input_type": input_type,
                "operation_type": operation_type,
                "db_id": db_id,
                "count": count,
                "rate_among_d_g1_failures": rate(count, total),
            }
        )
    return output


def hit_max_token_rows(
    *,
    method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = []
    samples = []
    for method_slug in METHOD_SLUGS:
        rows = method_rows[method_slug]
        hit_rows = [row for row in rows if truthy(row.get("hit_max_new_tokens"))]
        incorrect = [
            row for row in hit_rows if not metric_bool(row, "target_state_correct")
        ]
        parse_failures = [
            row for row in hit_rows if first_failure_stage(row) == "parse"
        ]
        summary.append(
            {
                "method_slug": method_slug,
                "n": len(rows),
                "hit_max_new_tokens_count": len(hit_rows),
                "hit_max_new_tokens_rate": rate(len(hit_rows), len(rows)),
                "incorrect_after_hit_count": len(incorrect),
                "parse_failure_after_hit_count": len(parse_failures),
            }
        )
        for row in hit_rows:
            sample_id = str(row["sample_id"])
            base = metadata[sample_id]
            samples.append(
                {
                    "method_slug": method_slug,
                    "sample_id": sample_id,
                    "db_id": base["db_id"],
                    "input_type": base["input_type"],
                    "operation_type": base["operation_type"],
                    "dependency_sensitive": base["dependency_sensitive"],
                    "target_state_correct": int(
                        metric_bool(row, "target_state_correct")
                    ),
                    "first_failure_stage": first_failure_stage(row),
                    "error_type": str(row.get("error_type") or ""),
                    "output_tokens": row.get("output_tokens", ""),
                }
            )
    return summary, samples


def write_manifest(output_dir: Path) -> None:
    files = {}
    for name in STAGE4R_ARTIFACTS:
        path = output_dir / name
        files[name] = {
            "exists": path.is_file() or name == "analysis_manifest.json",
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }
    write_json(
        output_dir / "analysis_manifest.json",
        {
            "stage": "Stage4R_FRESH_FAILURE_ATTRIBUTION",
            "model_called": False,
            "raw_generations_modified": False,
            "evaluations_modified": False,
            "artifacts": files,
        },
    )


def run_stage4r(
    *,
    protocol_root: Path,
    result_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen_ids = load_frozen_sample_ids(protocol_root)
    metadata = load_sample_metadata(protocol_root, frozen_ids)
    method_rows = {
        method_slug: load_method_rows(result_root, method_slug, frozen_ids)
        for method_slug in METHOD_SLUGS
    }
    dg1_rows = method_rows[PRIMARY_METHOD]
    full_rows = method_rows[FULL_METHOD]
    f_repairs = f_repair_rows(result_root)
    f_samples = build_f_activation_sample_rows(
        f_repairs=f_repairs,
        metadata=metadata,
        dg1_rows=dg1_rows,
        full_rows=full_rows,
    )
    paired_rows, paired_counts = full_vs_dg1_rows(
        dg1_rows=dg1_rows,
        full_rows=full_rows,
    )
    failure_sample_rows = build_dg1_failure_sample_rows(
        dg1_rows=dg1_rows,
        metadata=metadata,
    )
    taxonomy_rows = grouped_taxonomy_rows(failure_sample_rows)
    hit_summary, hit_samples = hit_max_token_rows(
        method_rows=method_rows,
        metadata=metadata,
    )

    f_rescue_count = sum(row["FULL_vs_D_G1_outcome"] == "rescue" for row in f_samples)
    f_fail_closed_count = sum(
        row["FULL_vs_D_G1_outcome"] == "fail_closed" for row in f_samples
    )
    f_regression_count = sum(
        row["FULL_vs_D_G1_outcome"] == "regression" for row in f_samples
    )
    summary = {
        "stage": "Stage4R_FRESH_FAILURE_ATTRIBUTION",
        "model_called": False,
        "fresh_sample_count": len(frozen_ids),
        "D_G1_incorrect_count": len(failure_sample_rows),
        "F_method_analyzed": FULL_METHOD,
        "F_exact_name_repair_rule": F_REPAIR_RULE,
        "F_activation_sample_count": len(f_samples),
        "F_exact_name_repair_count": len(f_repairs),
        "F_rescue_count": f_rescue_count,
        "F_fail_closed_count": f_fail_closed_count,
        "F_regression_count": f_regression_count,
        "FULL_vs_D_G1_paired_counts": paired_counts,
        "hit_max_new_tokens_by_method": {
            row["method_slug"]: row["hit_max_new_tokens_count"]
            for row in hit_summary
        },
    }
    write_json(output_dir / "stage4r_summary.json", summary)
    write_csv(
        output_dir / "f_activation_sample_level.csv",
        f_samples,
        list(f_samples[0]) if f_samples else [
            "sample_id",
            "db_id",
            "input_type",
            "operation_type",
            "dependency_sensitive",
            "F_exact_name_repair_count",
            "F_applied_count",
            "F_succeeded_count",
            "D_G1_target_state_correct",
            "FULL_target_state_correct",
            "FULL_vs_D_G1_outcome",
            "original_references",
            "replacement_references",
        ],
    )
    write_csv(
        output_dir / "f_exact_name_repairs.csv",
        f_repairs,
        list(f_repairs[0]) if f_repairs else [
            "sample_id",
            "repair_rule",
            "reference_kind",
            "slot_path",
            "original_reference",
            "replacement_reference",
            "candidate_count",
            "candidate_set",
            "repair_attempted",
            "repair_applied",
            "repair_succeeded",
            "validation_before",
            "validation_after",
        ],
    )
    write_csv(
        output_dir / "full_vs_dg1_paired_summary.csv",
        [{**paired_counts, "metric": "target_state_correct"}],
        [
            "metric",
            "paired_sample_count",
            "both_correct",
            "both_wrong",
            "rescue",
            "regression",
        ],
    )
    write_csv(
        output_dir / "full_vs_dg1_paired_sample_level.csv",
        paired_rows,
        list(paired_rows[0]) if paired_rows else [
            "sample_id",
            "paired_outcome",
            "d_g1_target_state_correct",
            "full_target_state_correct",
            "d_g1_first_failure_stage",
            "full_first_failure_stage",
            "d_g1_error_type",
            "full_error_type",
            "d_g1_accepted_output",
            "full_accepted_output",
            "full_false_accept",
        ],
    )
    write_csv(
        output_dir / "d_g1_failure_sample_level.csv",
        failure_sample_rows,
        list(failure_sample_rows[0]),
    )
    write_csv(
        output_dir / "d_g1_failure_taxonomy.csv",
        taxonomy_rows,
        list(taxonomy_rows[0]) if taxonomy_rows else [
            "error_family",
            "error_type",
            "first_failure_stage",
            "input_type",
            "operation_type",
            "db_id",
            "count",
            "rate_among_d_g1_failures",
        ],
    )
    write_csv(
        output_dir / "hit_max_new_tokens_summary.csv",
        hit_summary,
        list(hit_summary[0]),
    )
    write_csv(
        output_dir / "hit_max_new_tokens_samples.csv",
        hit_samples,
        list(hit_samples[0]) if hit_samples else [
            "method_slug",
            "sample_id",
            "db_id",
            "input_type",
            "operation_type",
            "dependency_sensitive",
            "target_state_correct",
            "first_failure_stage",
            "error_type",
            "output_tokens",
        ],
    )
    write_manifest(output_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary = run_stage4r(
        protocol_root=Path(args.protocol_root).resolve(),
        result_root=Path(args.result_root).resolve(),
        output_dir=Path(args.output_dir).resolve(),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
