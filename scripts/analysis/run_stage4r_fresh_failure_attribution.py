#!/usr/bin/env python3
"""CPU-only Stage-4R fresh failure attribution audit.

This post-hoc audit is intentionally read-only with respect to Stage-4 fresh
raw generations, evaluations, configs, and protocol artifacts. It consumes the
accepted frozen Stage-4 result root and emits focused attribution tables for:

* FULL-vs-D_G1 paired rescues/regressions;
* D_F_G1 diagnostic projection from frozen D_G1/FULL outputs;
* constrained reference repair (F) activations and exact-name repairs;
* D_G1 fresh failure taxonomy by family, input type, operation, database,
  and dependency sensitivity;
* preflight-abstention drill-down;
* max-token-hit associated failures.
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
    mcnemar_exact_pvalue,
    read_jsonl,
    rate,
    truthy,
    unique_dicts,
)
from scripts.analysis.run_stage3_causal_replay import write_csv, write_json  # noqa: E402

FULL_METHOD = "full_secondary"
DFG1_DIAGNOSTIC_METHOD = "d_f_g1_diagnostic"
F_REPAIR_RULE = "unique_exact_identifier_name"
STAGE4R_ARTIFACTS = (
    "stage4r_summary.json",
    "f_activation_sample_level.csv",
    "f_exact_name_repairs.csv",
    "d_f_g1_diagnostic_sample_level.csv",
    "d_f_g1_diagnostic_paired_summary.csv",
    "component_activation_on_f_rescues.csv",
    "full_vs_dg1_paired_summary.csv",
    "full_vs_dg1_paired_sample_level.csv",
    "d_g1_failure_taxonomy.csv",
    "d_g1_failure_sample_level.csv",
    "failure_family_summary.csv",
    "failure_by_input_type.csv",
    "failure_by_operation.csv",
    "failure_by_database.csv",
    "failure_by_dependency_sensitive.csv",
    "failure_family_x_input_type.csv",
    "failure_family_x_operation.csv",
    "failure_family_x_dependency.csv",
    "preflight_rejection_summary.csv",
    "preflight_rejection_sample_level.csv",
    "hit_max_new_tokens_summary.csv",
    "hit_max_new_tokens_samples.csv",
    "error_family_precedence.json",
)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def canonical_error_family(row: Mapping[str, Any]) -> str:
    """Classify D_G1 failures with explicit precedence.

    Precedence is intentionally documented and exported in
    `error_family_precedence.json`: max-token-hit cases first, then specific
    deterministic validator error types, then first-failure-stage fallbacks.
    """
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


def error_family_precedence() -> list[dict[str, str]]:
    return [
        {
            "priority": "1",
            "rule": "hit_max_new_tokens",
            "family": "output_length",
        },
        {
            "priority": "2",
            "rule": "UNKNOWN_COLUMN_ID or UNKNOWN_EVIDENCE_ID",
            "family": "schema_reference_grounding",
        },
        {
            "priority": "3",
            "rule": "UNRESOLVED_SOURCE_FIELD",
            "family": "source_field_grounding",
        },
        {
            "priority": "4",
            "rule": "LOSSY_NORMALIZATION_REJECTED",
            "family": "normalization",
        },
        {
            "priority": "5",
            "rule": "MISSING_REQUIRED_COLUMN or MISSING_UPDATE_COLUMN_IDS",
            "family": "missing_write_semantics",
        },
        {
            "priority": "6",
            "rule": "NEEDS_CLARIFICATION",
            "family": "ambiguity_or_insufficient_information",
        },
        {
            "priority": "7",
            "rule": "SQLite constraint-like error types",
            "family": "constraint_or_execution",
        },
        {
            "priority": "8",
            "rule": "first_failure_stage fallback",
            "family": "parse/build/preflight/execution/state_mismatch/other",
        },
    ]


def preflight_rejection_reason(row: Mapping[str, Any]) -> str:
    preflight = row.get("preflight") if isinstance(row.get("preflight"), dict) else {}
    error_class = str(preflight.get("error_class") or "").strip()
    message = str(row.get("error_message") or preflight.get("error") or "").casefold()
    if error_class == "blocked_by_semantic_risk_gate" or "semantic-risk gate" in message:
        return "semantic_risk_gate"
    if error_class == "foreign_key_violation" or "foreign key constraint failed" in message:
        return "foreign_key"
    if error_class == "unique_violation" or "unique constraint failed" in message:
        return "unique_constraint"
    if error_class == "not_null_violation" or "not null constraint failed" in message:
        return "not_null"
    if error_class == "check_violation" or "check constraint failed" in message:
        return "check"
    if error_class in {"type_error", "datatype_mismatch"} or "datatype mismatch" in message:
        return "type_or_datatype"
    if "dependency" in message:
        return "missing_dependency"
    if "unsafe" in message or "risk" in message:
        return "unsafe_or_risk_gate"
    return error_class or "other_preflight"


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
        if not truthy(trace.get("repair_attempted")):
            continue
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
        full_accepted = truthy(full_by_id[sample_id].get("accepted_output"))
        if full_correct and not dg1_correct:
            outcome = "rescue"
        elif dg1_correct and not full_correct:
            outcome = "regression"
        elif full_correct:
            outcome = "both_correct"
        elif full_accepted:
            outcome = "false_accept"
        else:
            outcome = "fail_closed"
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
                "FULL_accepted_output": int(full_accepted),
                "FULL_vs_D_G1_outcome": outcome,
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
                "preflight_rejection_reason": (
                    preflight_rejection_reason(row)
                    if first_failure_stage(row) == "preflight"
                    else ""
                ),
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
            str(row["dependency_sensitive"]),
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
        dependency_sensitive,
    ), count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        output.append(
            {
                "error_family": error_family,
                "error_type": error_type,
                "first_failure_stage": first_failure,
                "input_type": input_type,
                "operation_type": operation_type,
                "db_id": db_id,
                "dependency_sensitive": dependency_sensitive,
                "count": count,
                "rate_among_d_g1_failures": rate(count, total),
            }
        )
    return output


def grouped_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_keys: Sequence[str],
) -> list[dict[str, Any]]:
    total = len(rows)
    counts = Counter(
        tuple(
            "" if row.get(key) is None else str(row.get(key))
            for key in group_keys
        )
        for row in rows
    )
    output: list[dict[str, Any]] = []
    for key_values, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        row = {key: value for key, value in zip(group_keys, key_values)}
        row["count"] = count
        row["rate_among_d_g1_failures"] = rate(count, total)
        output.append(row)
    return output


def preflight_rejection_rows(
    failure_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    samples = [
        {
            "sample_id": row["sample_id"],
            "source_group": row["source_group"],
            "db_id": row["db_id"],
            "input_type": row["input_type"],
            "operation_type": row["operation_type"],
            "dependency_sensitive": row["dependency_sensitive"],
            "preflight_rejection_reason": row["preflight_rejection_reason"],
            "error_type": row["error_type"],
        }
        for row in failure_rows
        if row["first_failure_stage"] == "preflight"
    ]
    total = len(samples)
    counts = Counter(str(row["preflight_rejection_reason"]) for row in samples)
    summary = [
        {
            "preflight_rejection_reason": reason,
            "count": count,
            "rate_among_preflight_rejections": rate(count, total),
        }
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return summary, samples


def row_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row["sample_id"]): row for row in rows}


def build_d_f_g1_diagnostic_rows(
    *,
    sample_ids: Sequence[str],
    f_samples: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Mapping[str, Any]],
    dg1_rows: Sequence[Mapping[str, Any]],
    full_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    f_attempted_ids = {
        str(row["sample_id"])
        for row in f_samples
        if int(row.get("F_exact_name_repair_count") or 0) > 0
    }
    dg1_by_id = row_by_id(dg1_rows)
    full_by_id = row_by_id(full_rows)
    rows: list[dict[str, Any]] = []
    counts = {
        "D_G1_to_D_F_G1_rescue": 0,
        "D_G1_to_D_F_G1_regression": 0,
        "D_F_G1_to_FULL_rescue": 0,
        "D_F_G1_to_FULL_regression": 0,
        "D_F_G1_correct": 0,
        "D_G1_correct": 0,
        "FULL_correct": 0,
    }
    for sample_id in sample_ids:
        base = metadata[sample_id]
        dg1 = dg1_by_id[sample_id]
        full = full_by_id[sample_id]
        diagnostic_source = (
            FULL_METHOD if sample_id in f_attempted_ids else PRIMARY_METHOD
        )
        diagnostic = full if diagnostic_source == FULL_METHOD else dg1
        dg1_correct = metric_bool(dg1, "target_state_correct")
        diagnostic_correct = metric_bool(diagnostic, "target_state_correct")
        full_correct = metric_bool(full, "target_state_correct")
        counts["D_G1_correct"] += int(dg1_correct)
        counts["D_F_G1_correct"] += int(diagnostic_correct)
        counts["FULL_correct"] += int(full_correct)
        if diagnostic_correct and not dg1_correct:
            counts["D_G1_to_D_F_G1_rescue"] += 1
        if dg1_correct and not diagnostic_correct:
            counts["D_G1_to_D_F_G1_regression"] += 1
        if full_correct and not diagnostic_correct:
            counts["D_F_G1_to_FULL_rescue"] += 1
        if diagnostic_correct and not full_correct:
            counts["D_F_G1_to_FULL_regression"] += 1
        rows.append(
            {
                "sample_id": sample_id,
                "db_id": base["db_id"],
                "input_type": base["input_type"],
                "operation_type": base["operation_type"],
                "dependency_sensitive": base["dependency_sensitive"],
                "F_attempted": int(sample_id in f_attempted_ids),
                "diagnostic_source": diagnostic_source,
                "D_G1_target_state_correct": int(dg1_correct),
                "D_F_G1_target_state_correct": int(diagnostic_correct),
                "FULL_target_state_correct": int(full_correct),
                "D_G1_first_failure_stage": first_failure_stage(dg1),
                "D_F_G1_first_failure_stage": first_failure_stage(diagnostic),
                "FULL_first_failure_stage": first_failure_stage(full),
                "D_G1_error_type": str(dg1.get("error_type") or ""),
                "D_F_G1_error_type": str(diagnostic.get("error_type") or ""),
                "FULL_error_type": str(full.get("error_type") or ""),
            }
        )
    summary = [
        {
            "comparison": "D_G1_to_D_F_G1_DIAGNOSTIC",
            "paired_sample_count": len(sample_ids),
            "baseline_correct": counts["D_G1_correct"],
            "method_correct": counts["D_F_G1_correct"],
            "rescue": counts["D_G1_to_D_F_G1_rescue"],
            "regression": counts["D_G1_to_D_F_G1_regression"],
            "accuracy_delta": (
                counts["D_F_G1_correct"] - counts["D_G1_correct"]
            )
            / len(sample_ids),
            "mcnemar_exact_p": mcnemar_exact_pvalue(
                counts["D_G1_to_D_F_G1_regression"],
                counts["D_G1_to_D_F_G1_rescue"],
            ),
            "diagnostic_interpretation": "post_hoc_from_frozen_outputs_not_confirmatory",
        },
        {
            "comparison": "D_F_G1_DIAGNOSTIC_to_FULL",
            "paired_sample_count": len(sample_ids),
            "baseline_correct": counts["D_F_G1_correct"],
            "method_correct": counts["FULL_correct"],
            "rescue": counts["D_F_G1_to_FULL_rescue"],
            "regression": counts["D_F_G1_to_FULL_regression"],
            "accuracy_delta": (
                counts["FULL_correct"] - counts["D_F_G1_correct"]
            )
            / len(sample_ids),
            "mcnemar_exact_p": mcnemar_exact_pvalue(
                counts["D_F_G1_to_FULL_regression"],
                counts["D_F_G1_to_FULL_rescue"],
            ),
            "diagnostic_interpretation": "post_hoc_from_frozen_outputs_not_confirmatory",
        },
    ]
    return rows, summary


def materialized_rows_by_id(method_dir: Path) -> dict[str, Mapping[str, Any]]:
    path = method_dir / "materialized_write_plans.jsonl"
    if not path.is_file():
        return {}
    return row_by_id(read_jsonl(path))


def count_stage2_intervention(row: Mapping[str, Any], intervention: str) -> int:
    count = 0

    def walk(value: Any) -> None:
        nonlocal count
        if isinstance(value, dict):
            if value.get("stage2_intervention") == intervention:
                count += 1
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(row)
    return count


def component_activation_on_f_rescues(
    *,
    result_root: Path,
    f_samples: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    full_materialized = materialized_rows_by_id(result_root / "methods" / FULL_METHOD)
    output: list[dict[str, Any]] = []
    for row in f_samples:
        if row["FULL_vs_D_G1_outcome"] != "rescue":
            continue
        sample_id = str(row["sample_id"])
        materialized = full_materialized.get(sample_id) or {}
        output.append(
            {
                "sample_id": sample_id,
                "input_type": row["input_type"],
                "operation_type": row["operation_type"],
                "db_id": row["db_id"],
                "F_attempted": 1,
                "F_exact_name_repair_count": row["F_exact_name_repair_count"],
                "A_control_field_roles_trace_observed": 0,
                "B_conflict_preservation_applicable": int(
                    str(row["operation_type"]).startswith("upsert")
                ),
                "C_update_column_consistency_applicable": int(
                    str(row["operation_type"]).startswith("upsert")
                ),
                "E_free_text_normalization_trace_count": count_stage2_intervention(
                    materialized,
                    "E_free_text_typed_normalization",
                ),
                "G_diagnostic_targeted_repair_trace_count": count_stage2_intervention(
                    materialized,
                    "G1_evidence_span_boundary_repair",
                )
                + count_stage2_intervention(
                    materialized,
                    "G2_evidence_span_selection_repair",
                ),
                "interpretation": (
                    "F trace present; A/B/C not observed or not applicable for "
                    "semi_structured plain_insert; E/G diagnostic traces absent"
                ),
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
                "label": "max_token_hit_associated_cases",
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
            "exists": path.is_file(),
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
    failure_family_summary = grouped_summary(
        failure_sample_rows,
        group_keys=["error_family"],
    )
    failure_by_input = grouped_summary(
        failure_sample_rows,
        group_keys=["input_type"],
    )
    failure_by_operation = grouped_summary(
        failure_sample_rows,
        group_keys=["operation_type"],
    )
    failure_by_database = grouped_summary(
        failure_sample_rows,
        group_keys=["db_id"],
    )
    failure_by_dependency = grouped_summary(
        failure_sample_rows,
        group_keys=["dependency_sensitive"],
    )
    failure_family_x_input = grouped_summary(
        failure_sample_rows,
        group_keys=["error_family", "input_type"],
    )
    failure_family_x_operation = grouped_summary(
        failure_sample_rows,
        group_keys=["error_family", "operation_type"],
    )
    failure_family_x_dependency = grouped_summary(
        failure_sample_rows,
        group_keys=["error_family", "dependency_sensitive"],
    )
    preflight_summary, preflight_samples = preflight_rejection_rows(failure_sample_rows)
    diagnostic_rows, diagnostic_summary = build_d_f_g1_diagnostic_rows(
        sample_ids=frozen_ids,
        f_samples=f_samples,
        metadata=metadata,
        dg1_rows=dg1_rows,
        full_rows=full_rows,
    )
    component_rows = component_activation_on_f_rescues(
        result_root=result_root,
        f_samples=f_samples,
    )
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
        "stage": "Stage4R1_FRESH_FAILURE_ATTRIBUTION_TARGETED_REVISION",
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
        "D_F_G1_diagnostic": {
            "status": "post_hoc_projection_from_frozen_outputs",
            "D_G1_correct": diagnostic_summary[0]["baseline_correct"],
            "D_F_G1_correct": diagnostic_summary[0]["method_correct"],
            "FULL_correct": diagnostic_summary[1]["method_correct"],
            "D_G1_to_D_F_G1_rescue": diagnostic_summary[0]["rescue"],
            "D_G1_to_D_F_G1_regression": diagnostic_summary[0]["regression"],
            "D_F_G1_to_FULL_rescue": diagnostic_summary[1]["rescue"],
            "D_F_G1_to_FULL_regression": diagnostic_summary[1]["regression"],
        },
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
            "FULL_accepted_output",
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
        output_dir / "d_f_g1_diagnostic_sample_level.csv",
        diagnostic_rows,
        list(diagnostic_rows[0]),
    )
    write_csv(
        output_dir / "d_f_g1_diagnostic_paired_summary.csv",
        diagnostic_summary,
        list(diagnostic_summary[0]),
    )
    write_csv(
        output_dir / "component_activation_on_f_rescues.csv",
        component_rows,
        list(component_rows[0]) if component_rows else [
            "sample_id",
            "input_type",
            "operation_type",
            "db_id",
            "F_attempted",
            "F_exact_name_repair_count",
            "A_control_field_roles_trace_observed",
            "B_conflict_preservation_applicable",
            "C_update_column_consistency_applicable",
            "E_free_text_normalization_trace_count",
            "G_diagnostic_targeted_repair_trace_count",
            "interpretation",
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
            "dependency_sensitive",
            "count",
            "rate_among_d_g1_failures",
        ],
    )
    for filename, rows in (
        ("failure_family_summary.csv", failure_family_summary),
        ("failure_by_input_type.csv", failure_by_input),
        ("failure_by_operation.csv", failure_by_operation),
        ("failure_by_database.csv", failure_by_database),
        ("failure_by_dependency_sensitive.csv", failure_by_dependency),
        ("failure_family_x_input_type.csv", failure_family_x_input),
        ("failure_family_x_operation.csv", failure_family_x_operation),
        ("failure_family_x_dependency.csv", failure_family_x_dependency),
        ("preflight_rejection_summary.csv", preflight_summary),
        ("preflight_rejection_sample_level.csv", preflight_samples),
    ):
        write_csv(output_dir / filename, rows, list(rows[0]) if rows else ["count"])
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
    write_json(
        output_dir / "error_family_precedence.json",
        {"precedence": error_family_precedence()},
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
