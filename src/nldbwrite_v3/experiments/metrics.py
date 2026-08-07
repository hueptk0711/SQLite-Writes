from __future__ import annotations

from collections import Counter
from collections import defaultdict
from typing import Any


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    return (
        sum(bool(row.get(key)) for row in rows) / len(rows)
        if rows
        else 0.0
    )


def _available_rate(
    rows: list[dict[str, Any]],
    key: str,
) -> float | None:
    available = [row for row in rows if row.get(key) is not None]
    return _rate(available, key) if available else None


def _available_mean(
    rows: list[dict[str, Any]],
    key: str,
) -> float | None:
    values = [
        float(row[key])
        for row in rows
        if row.get(key) is not None
    ]
    return sum(values) / len(values) if values else None


def _end_to_end_plan_mean(
    rows: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
    key: str,
    *,
    applicable: bool,
) -> float | None:
    """Score missing plans as zero while preserving N/A for direct methods."""
    if not applicable or not rows:
        return None
    return sum(float(row.get(key) or 0.0) for row in plan_rows) / len(rows)


def _summary_core(rows: list[dict[str, Any]]) -> dict[str, Any]:
    plan_rows = [
        row for row in rows if row.get("plan_metrics_available")
    ]
    selective_rows = [
        row for row in rows if row.get("accepted_output") is not None
    ]
    accepted_rows = [
        row for row in selective_rows if bool(row.get("accepted_output"))
    ]
    coverage = (
        len(accepted_rows) / len(selective_rows)
        if selective_rows
        else None
    )
    accepted_accuracy = (
        _rate(accepted_rows, "target_state_correct")
        if accepted_rows
        else None
    )
    methods = {str(row.get("method")) for row in rows if row.get("method")}
    direct_only = bool(methods) and methods <= {"D-FS-M"}
    legacy_builder_only = bool(methods) and methods <= {"S-FS-v2-M"}
    oracle_only = bool(methods) and methods <= {"Gold-MP"}
    plan_metrics_applicable = (
        not direct_only
        and (
            bool(plan_rows)
            or any("plan_metrics_available" in row for row in rows)
        )
    )
    validation_applicable = not (direct_only or legacy_builder_only)
    execution_rows = [row for row in rows if bool(row.get("execution_success"))]
    generation_success = [
        row
        for row in rows
        if row.get("generation_status") == "success"
        or (str(row.get("method")) == "Gold-MP")
    ]
    preflight_applicable = any(
        row.get("preflight_accepted") is not None for row in rows
    )
    admission_boundary = (
        "transactional_preflight"
        if preflight_applicable
        else ("oracle_build" if oracle_only else "successful_build")
    )
    database_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("db_id") is not None:
            database_rows[str(row["db_id"])].append(row)
    database_macro_accuracy = (
        sum(
            _rate(database_group, "target_state_correct")
            for database_group in database_rows.values()
        )
        / len(database_rows)
        if database_rows
        else None
    )

    def subset_accuracy(flag: str) -> float | None:
        subset = [row for row in rows if row.get(flag) is True]
        return _rate(subset, "target_state_correct") if subset else None

    return {
        "samples": len(rows),
        "generation_coverage": (
            len(generation_success) / len(rows) if rows else 0.0
        ),
        "parse_coverage": _rate(rows, "parse_success"),
        "validation_coverage": (
            _rate(rows, "plan_validation_success")
            if validation_applicable
            else None
        ),
        "build_coverage": _rate(rows, "build_success"),
        "execution_coverage": _rate(rows, "execution_success"),
        "execution_conditional_accuracy": (
            _rate(execution_rows, "target_state_correct")
            if execution_rows
            else None
        ),
        "parse_success": _rate(rows, "parse_success"),
        "plan_validation_success": (
            _rate(rows, "plan_validation_success")
            if validation_applicable
            else None
        ),
        "build_success": _rate(rows, "build_success"),
        "execution_success": _rate(rows, "execution_success"),
        "target_state_accuracy": _rate(rows, "target_state_correct"),
        "strict_full_state_accuracy": _rate(
            rows,
            "strict_full_state_correct",
        ),
        "database_macro_accuracy": database_macro_accuracy,
        "original_request_accuracy": subset_accuracy("is_original_request"),
        "state_changing_accuracy": subset_accuracy("state_changing"),
        "conflict_sensitive_accuracy": subset_accuracy(
            "conflict_sensitive"
        ),
        # ``side_effect_rate`` is retained as a compatibility alias, but from
        # reporting amendment v2.3 it denotes any off-target state change,
        # even when the requested target state is also wrong.
        "side_effect_rate": _rate(rows, "any_off_target_change"),
        "any_off_target_change_rate": _rate(
            rows,
            "any_off_target_change",
        ),
        "target_correct_with_side_effect_rate": _rate(
            rows,
            "target_correct_with_side_effect",
        ),
        "coverage": coverage,
        "accepted_output_accuracy": accepted_accuracy,
        "admission_boundary": admission_boundary,
        "method_specific_admission_coverage": coverage,
        "method_specific_admitted_output_accuracy": accepted_accuracy,
        "abstention_rate": (
            1.0 - coverage if coverage is not None else None
        ),
        "selective_risk": (
            1.0 - accepted_accuracy
            if accepted_accuracy is not None
            else None
        ),
        "preflight_accept_rate": _available_rate(
            rows,
            "preflight_accepted",
        ),
        "mean_preflight_latency_sec": _available_mean(
            rows,
            "preflight_latency_sec",
        ),
        "input_truncation_rate": _rate(rows, "input_truncated"),
        "output_limit_hit_rate": _rate(rows, "hit_max_new_tokens"),
        "source_parse_row_count_accuracy": _available_rate(
            rows,
            "source_parse_row_count_exact",
        ),
        "plan_metric_coverage": (
            len(plan_rows) / len(rows)
            if plan_metrics_applicable and rows
            else None
        ),
        "row_count_exact_accuracy": (
            _rate(plan_rows, "row_count_exact") if plan_rows else None
        ),
        "row_coverage": _available_mean(plan_rows, "row_coverage"),
        "row_exact_match_accuracy": (
            _rate(plan_rows, "row_exact_match") if plan_rows else None
        ),
        "cell_value_f1": _available_mean(plan_rows, "cell_value_f1"),
        "payload_copy_integrity": _available_mean(
            plan_rows,
            "payload_copy_integrity",
        ),
        "conflict_action_accuracy": (
            _rate(plan_rows, "conflict_action_correct")
            if plan_rows
            else None
        ),
        "conflict_target_exact_accuracy": (
            _rate(plan_rows, "conflict_target_exact")
            if plan_rows
            else None
        ),
        "conflict_update_column_f1": _available_mean(
            plan_rows,
            "conflict_update_column_f1",
        ),
        "conflict_full_exact_accuracy": (
            _rate(plan_rows, "conflict_full_exact")
            if plan_rows
            else None
        ),
        "mapping_table_accuracy": (
            _rate(plan_rows, "table_exact") if plan_rows else None
        ),
        "target_column_f1": _available_mean(
            plan_rows,
            "target_column_f1",
        ),
        "conditional_row_count_exact_accuracy": (
            _rate(plan_rows, "row_count_exact") if plan_rows else None
        ),
        "conditional_row_exact_match_accuracy": (
            _rate(plan_rows, "row_exact_match") if plan_rows else None
        ),
        "conditional_cell_value_f1": _available_mean(
            plan_rows,
            "cell_value_f1",
        ),
        "conditional_conflict_target_exact_accuracy": (
            _rate(plan_rows, "conflict_target_exact") if plan_rows else None
        ),
        "conditional_mapping_table_accuracy": (
            _rate(plan_rows, "table_exact") if plan_rows else None
        ),
        "conditional_target_column_f1": _available_mean(
            plan_rows,
            "target_column_f1",
        ),
        "end_to_end_row_count_exact_accuracy": _end_to_end_plan_mean(
            rows,
            plan_rows,
            "row_count_exact",
            applicable=plan_metrics_applicable,
        ),
        "end_to_end_row_exact_match_accuracy": _end_to_end_plan_mean(
            rows,
            plan_rows,
            "row_exact_match",
            applicable=plan_metrics_applicable,
        ),
        "end_to_end_cell_value_f1": _end_to_end_plan_mean(
            rows,
            plan_rows,
            "cell_value_f1",
            applicable=plan_metrics_applicable,
        ),
        "end_to_end_conflict_target_exact_accuracy": _end_to_end_plan_mean(
            rows,
            plan_rows,
            "conflict_target_exact",
            applicable=plan_metrics_applicable,
        ),
        "end_to_end_mapping_table_accuracy": _end_to_end_plan_mean(
            rows,
            plan_rows,
            "table_exact",
            applicable=plan_metrics_applicable,
        ),
        "end_to_end_target_column_f1": _end_to_end_plan_mean(
            rows,
            plan_rows,
            "target_column_f1",
            applicable=plan_metrics_applicable,
        ),
        "mean_input_tokens": (
            sum(int(row.get("input_tokens") or 0) for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        "mean_output_tokens": (
            sum(int(row.get("output_tokens") or 0) for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        "mean_latency_sec": (
            sum(float(row.get("latency_sec") or 0.0) for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        "repair_eligible_samples": sum(
            bool(row.get("repair_eligible")) for row in rows
        ),
        "repair_attempt_rate_eligible": (
            sum(bool(row.get("repair_attempted")) for row in rows)
            / sum(bool(row.get("repair_eligible")) for row in rows)
            if any(bool(row.get("repair_eligible")) for row in rows)
            else None
        ),
        "repair_accept_rate_attempted": (
            sum(bool(row.get("repair_accepted")) for row in rows)
            / sum(bool(row.get("repair_attempted")) for row in rows)
            if any(bool(row.get("repair_attempted")) for row in rows)
            else None
        ),
    }


def summarize_run(rows: list[dict[str, Any]]) -> dict[str, Any]:
    error_counts = Counter(
        str(row.get("error_type") or "none") for row in rows
    )
    labels = sorted(
        {
            str(label)
            for row in rows
            for label in row.get("slice_labels") or []
        }
    )
    return {
        **_summary_core(rows),
        "errors": dict(sorted(error_counts.items())),
        "slices": {
            label: _summary_core(
                [
                    row
                    for row in rows
                    if label in (row.get("slice_labels") or [])
                ]
            )
            for label in labels
        },
    }


def error_taxonomy_row(row: dict[str, Any]) -> dict[str, Any]:
    error_type = str(row.get("error_type") or "")
    if not row.get("parse_success"):
        category = "E1_invalid_output"
    elif error_type in {"UNKNOWN_TABLE", "UNKNOWN_TABLE_ID", "wrong_table"}:
        category = "E2_wrong_table"
    elif error_type in {
        "UNKNOWN_COLUMN",
        "UNKNOWN_COLUMN_ID",
        "NON_INSERTABLE_COLUMN",
        "unknown_column",
    }:
        category = "E3_unknown_column"
    elif error_type in {"MISSING_REQUIRED_COLUMN", "missing_column"}:
        category = "E4_missing_column"
    elif error_type in {
        "INVALID_FIELD_MAPPING",
        "MISSING_SOURCE_FIELD",
        "UNKNOWN_SOURCE_FIELD_ID",
        "UNRESOLVED_SOURCE_FIELD",
        "INVALID_SOURCE_SELECTOR",
        "wrong_mapping",
    }:
        category = "E5_wrong_mapping"
    elif error_type in {
        "SOURCE_ROW_COVERAGE_MISMATCH",
        "MISSING_ROWS",
        "MISSING_WRITE_GROUPS",
        "missing_row",
    }:
        category = "E6_missing_row"
    elif error_type in {
        "DUPLICATE_ROW",
        "DUPLICATE_GROUP_ID",
        "DUPLICATE_COLUMN",
        "DUPLICATE_TARGET_COLUMN_AFTER_EVIDENCE_GROUNDING",
        "duplicate_row",
    }:
        category = "E7_duplicate"
    elif error_type in {
        "UNSUPPORTED_EXTRACTED_VALUE",
        "UNSUPPORTED_CONSTANT",
        "VALUE_EVIDENCE_MISMATCH",
        "PROVENANCE_MISMATCH",
        "MISSING_VALUE_PROVENANCE",
        "INVALID_VALUE_PROVENANCE",
        "wrong_value",
        "LOSSY_NORMALIZATION_REJECTED",
    }:
        category = "E8_wrong_or_hallucinated_value"
    elif error_type in {"INVALID_ROW", "type_error"}:
        category = "E9_type_or_shape"
    elif error_type in {
        "INVALID_CONFLICT_ACTION",
        "MISSING_CONFLICT_POLICY",
        "NEEDS_CLARIFICATION",
    }:
        category = "E10_conflict_semantics"
    elif (
        "CONFLICT" in error_type.upper()
        or error_type
        in {
            "MISSING_UPDATE_COLUMNS",
            "MISSING_UPDATE_COLUMN_IDS",
            "UNEXPECTED_UPDATE_COLUMNS",
            "UNKNOWN_CONSTRAINT_ID",
        }
    ):
        category = "E11_conflict_target_or_mask"
    elif "DEPENDENCY" in error_type.upper():
        category = "E12_dependency_order"
    elif "foreign key" in str(
        row.get("error_message") or ""
    ).lower():
        category = "E13_dependency_fk"
    elif error_type in {"builder_error", "COMPILER_ERROR"}:
        category = "E14_compiler"
    elif error_type in {"execution_error", "unsafe_sql"}:
        category = "E15_execution"
    elif error_type == "preflight_abstention":
        category = "E15_preflight_abstention"
    elif error_type == "wrong_state":
        category = "E16_state_mismatch"
    elif error_type == "wrong_state_with_off_target_change":
        category = "E17_state_mismatch_with_off_target_change"
    elif error_type == "unintended_side_effect":
        category = "E18_unintended_side_effect"
    else:
        category = "correct" if row.get("target_state_correct") else "other"
    if category == "E1_invalid_output":
        stage = "parsing"
    elif category in {
        "E2_wrong_table",
        "E3_unknown_column",
        "E10_conflict_semantics",
        "E11_conflict_target_or_mask",
    }:
        stage = "interpretation"
    elif category in {
        "E5_wrong_mapping",
        "E6_missing_row",
        "E8_wrong_or_hallucinated_value",
    }:
        stage = "source_grounding"
    elif category in {
        "E4_missing_column",
        "E7_duplicate",
        "E9_type_or_shape",
        "E12_dependency_order",
        "E14_compiler",
    }:
        stage = "compilation"
    elif category in {
        "E13_dependency_fk",
        "E15_execution",
        "E15_preflight_abstention",
    }:
        stage = "execution"
    elif category in {
        "E16_state_mismatch",
        "E17_state_mismatch_with_off_target_change",
        "E18_unintended_side_effect",
    }:
        stage = "state"
    else:
        stage = "correct" if category == "correct" else "other"
    return {
        "sample_id": row.get("sample_id"),
        "db_id": row.get("db_id"),
        "method": row.get("method"),
        "error_category": category,
        "error_stage": stage,
        "error_type": row.get("error_type"),
        "error_message": row.get("error_message"),
        "detected_mode": row.get("detected_mode"),
        "detected_format": row.get("detected_format"),
    }
