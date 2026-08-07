from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


METHODS = (
    ("d_fs_m", "D-FS-M"),
    ("j_fs_m", "J-FS-M"),
    ("s_fs_v2_m", "S-FS-v2-M"),
    ("mp_fs_m", "MP-FS-M"),
    ("mp_fs_plus", "MP-FS+"),
    ("gold_mp", "Gold-MP"),
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _rate(rows: list[dict[str, Any]], field: str) -> float:
    return sum(bool(row.get(field)) for row in rows) / len(rows)


def _conditional_accuracy(
    rows: list[dict[str, Any]],
    admitted_field: str,
    correct_field: str,
) -> float | None:
    admitted = [row for row in rows if bool(row.get(admitted_field))]
    if not admitted:
        return None
    return sum(bool(row.get(correct_field)) for row in admitted) / len(admitted)


def _database_macro(rows: list[dict[str, Any]]) -> float:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["db_id"])].append(row)
    per_database = [
        _rate(database_rows, "target_state_correct")
        for database_rows in grouped.values()
    ]
    return sum(per_database) / len(per_database)


def _subset_accuracy(rows: list[dict[str, Any]], field: str) -> float | None:
    subset = [row for row in rows if bool(row.get(field))]
    if not subset:
        return None
    return _rate(subset, "target_state_correct")


def _equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= 1e-12


def audit_primary_metrics(
    run_root: Path,
    canonical_report: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Independently recompute primary rates without the production metrics module."""
    output_dir.mkdir(parents=True, exist_ok=True)
    audited: dict[str, dict[str, Any]] = {}
    reference_ids: set[str] | None = None
    fields = (
        "parse_success",
        "plan_validation_success",
        "build_success",
        "execution_success",
        "target_state_accuracy",
        "strict_full_state_accuracy",
        "database_macro_accuracy",
        "state_changing_accuracy",
        "conflict_sensitive_accuracy",
        "side_effect_rate",
        "coverage",
        "accepted_output_accuracy",
    )

    for slug, method_id in METHODS:
        evaluations = _load_jsonl(run_root / slug / "evaluation.jsonl")
        raw = _load_jsonl(run_root / slug / "raw_generations.jsonl")
        ids = {str(row["sample_id"]) for row in evaluations}
        raw_ids = {str(row["sample_id"]) for row in raw}
        if len(evaluations) != 300 or len(ids) != 300 or ids != raw_ids:
            raise ValueError(f"Independent audit found incomplete rows for {method_id}")
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise ValueError(f"Independent audit found cross-method ID drift: {method_id}")

        values = {
            "samples": len(evaluations),
            "parse_success": _rate(evaluations, "parse_success"),
            "plan_validation_success": _rate(
                evaluations, "plan_validation_success"
            ),
            "build_success": _rate(evaluations, "build_success"),
            "execution_success": _rate(evaluations, "execution_success"),
            "target_state_accuracy": _rate(evaluations, "target_state_correct"),
            "strict_full_state_accuracy": _rate(
                evaluations, "strict_full_state_correct"
            ),
            "database_macro_accuracy": _database_macro(evaluations),
            "state_changing_accuracy": _subset_accuracy(
                evaluations, "state_changing"
            ),
            "conflict_sensitive_accuracy": _subset_accuracy(
                evaluations, "conflict_sensitive"
            ),
            "side_effect_rate": _rate(evaluations, "side_effect"),
            "coverage": _rate(evaluations, "accepted_output"),
            "accepted_output_accuracy": _conditional_accuracy(
                evaluations,
                "accepted_output",
                "target_state_correct",
            ),
            "input_truncation_rate": _rate(raw, "input_truncated"),
            "output_limit_hit_rate": _rate(raw, "hit_max_new_tokens"),
        }
        expected = canonical_report["methods"][method_id]
        mismatches = {
            field: {"independent": values[field], "canonical": expected.get(field)}
            for field in fields
            if not _equal(values[field], expected.get(field))
        }
        if mismatches:
            raise ValueError(
                f"Independent primary audit mismatch for {method_id}: {mismatches}"
            )
        audited[method_id] = values

    report = {
        "audit_version": 1,
        "audit_id": "independent_primary_metric_audit_v1",
        "status": "pass",
        "production_metrics_module_imported": False,
        "samples": 300,
        "methods": 6,
        "cross_method_ids_identical": True,
        "all_primary_metrics_match": True,
        "canonical_comparison_fields": list(fields),
        "raw_generation_diagnostics_note": (
            "Input-truncation and output-limit rates are independently derived "
            "from raw_generations.jsonl but are not compared because the frozen "
            "canonical report stores those two diagnostics as null."
        ),
        "results": audited,
    }
    (output_dir / "independent_primary_audit.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with (output_dir / "independent_primary_audit.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        csv_fields = ["method_id", *next(iter(audited.values())).keys()]
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for method_id, values in audited.items():
            writer.writerow({"method_id": method_id, **values})
    return report
