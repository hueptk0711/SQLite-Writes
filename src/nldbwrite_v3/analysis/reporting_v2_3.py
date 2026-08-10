from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tarfile
from collections import Counter
from pathlib import Path, PureWindowsPath
from statistics import mean, median
from typing import Any, Iterable

from nldbwrite_v3.analysis.figures_v2_2 import build_figures
from nldbwrite_v3.analysis.independent_audit import audit_primary_metrics
from nldbwrite_v3.experiments.metrics import error_taxonomy_row, summarize_run


METHODS = (
    ("d_fs_m", "D-FS-M", False),
    ("j_fs_m", "J-FS-M", True),
    ("s_fs_v2_m", "S-FS-v2-M", True),
    ("mp_fs_m", "MP-FS-M", True),
    ("mp_fs_plus", "MP-FS+", True),
    ("gold_mp", "Gold-MP", True),
)

COMPARISONS = (
    ("D-FS-M", "MP-FS+", "MP-FS+ vs D-FS-M"),
    ("J-FS-M", "MP-FS+", "MP-FS+ vs J-FS-M"),
    ("MP-FS-M", "MP-FS+", "MP-FS+ vs MP-FS-M"),
    ("D-FS-M", "J-FS-M", "J-FS-M vs D-FS-M"),
)

PRIMARY_INVARIANTS = (
    "target_state_accuracy",
    "strict_full_state_accuracy",
    "database_macro_accuracy",
    "state_changing_accuracy",
    "conflict_sensitive_accuracy",
    "accepted_output_accuracy",
    "coverage",
    "abstention_rate",
    "selective_risk",
)

EXPECTED_OFF_TARGET_COUNTS = {
    "D-FS-M": 1,
    "J-FS-M": 0,
    "S-FS-v2-M": 0,
    "MP-FS-M": 1,
    "MP-FS+": 0,
    "Gold-MP": 0,
}

STAGE_FIELDS = (
    "generation_coverage",
    "parse_coverage",
    "validation_coverage",
    "build_coverage",
    "execution_coverage",
    "execution_conditional_accuracy",
    "target_state_accuracy",
    "method_specific_admission_coverage",
    "method_specific_admitted_output_accuracy",
    "preflight_accept_rate",
)

PLAN_FIELDS = (
    "plan_metric_coverage",
    "conditional_row_count_exact_accuracy",
    "end_to_end_row_count_exact_accuracy",
    "conditional_row_exact_match_accuracy",
    "end_to_end_row_exact_match_accuracy",
    "conditional_cell_value_f1",
    "end_to_end_cell_value_f1",
    "conditional_conflict_target_exact_accuracy",
    "end_to_end_conflict_target_exact_accuracy",
    "conditional_mapping_table_accuracy",
    "end_to_end_mapping_table_accuracy",
    "conditional_target_column_f1",
    "end_to_end_target_column_f1",
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remove_legacy_side_effect_rate(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("side_effect_rate", None)
        for nested in value.values():
            remove_legacy_side_effect_rate(nested)
    elif isinstance(value, list):
        for nested in value:
            remove_legacy_side_effect_rate(nested)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(fields)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            {field: row.get(field) for field in fieldnames} for row in rows
        )


def target_columns(plan: dict[str, Any] | None) -> set[str]:
    if not plan:
        return set()
    return {
        f"{group.get('table')}.{column}"
        for group in plan.get("write_groups") or []
        for row in group.get("rows") or []
        for column in row
    }


def target_tables(plan: dict[str, Any] | None) -> set[str]:
    if not plan:
        return set()
    return {
        str(group.get("table"))
        for group in plan.get("write_groups") or []
        if group.get("table")
    }


def portable_filename(raw_path: str) -> str:
    """Return a basename for either POSIX or Windows path text.

    ``pathlib.Path`` follows the host platform.  A Windows absolute path stored
    in provenance metadata would otherwise be treated as one literal filename
    on POSIX.  Reproduction uses only this portable basename.
    """
    raw_path = str(raw_path or "")
    if not raw_path:
        return ""
    return (
        PureWindowsPath(raw_path).name
        if "\\" in raw_path
        else Path(raw_path).name
    )


def set_f1(predicted: set[str], gold: set[str]) -> float:
    if not predicted and not gold:
        return 1.0
    overlap = len(predicted & gold)
    precision = overlap / len(predicted) if predicted else 0.0
    recall = overlap / len(gold) if gold else 0.0
    return (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


def plan_map(path: Path) -> dict[str, dict[str, Any] | None]:
    rows = load_jsonl(path)
    result = {
        str(row["sample_id"]): row.get("write_plan")
        for row in rows
    }
    if len(rows) != len(result):
        raise ValueError(f"Duplicate plan rows in {path}")
    return result


def corrected_evaluations(
    rows: list[dict[str, Any]],
    predicted_plans: dict[str, dict[str, Any] | None],
    gold_plans: dict[str, dict[str, Any] | None],
    *,
    plan_metrics_applicable: bool,
) -> list[dict[str, Any]]:
    corrected: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        sample_id = str(row["sample_id"])
        predicted = predicted_plans.get(sample_id)
        gold = gold_plans.get(sample_id)
        if gold is None:
            raise ValueError(f"Missing Gold-MP plan for {sample_id}")
        targets = target_tables(gold)
        strict_mismatches = [
            str(table) for table in row.get("strict_mismatched_tables") or []
        ]
        target_mismatches = [
            str(table) for table in row.get("target_mismatched_tables") or []
        ]
        off_target_mismatches = [
            table for table in strict_mismatches if table not in targets
        ]
        row["off_target_mismatched_tables"] = off_target_mismatches
        row["any_off_target_change"] = bool(off_target_mismatches)
        row["target_correct_with_side_effect"] = bool(
            not target_mismatches and off_target_mismatches
        )
        row["side_effect"] = row["any_off_target_change"]
        if target_mismatches and off_target_mismatches:
            row["error_type"] = "wrong_state_with_off_target_change"
        elif not target_mismatches and off_target_mismatches:
            row["error_type"] = "unintended_side_effect"
        if not plan_metrics_applicable:
            row["target_column_f1"] = None
            row["target_column_metric_source"] = "not_applicable_direct_sql"
        elif bool(row.get("plan_metrics_available")):
            if predicted is None:
                raise ValueError(
                    f"Plan metrics marked available but plan is missing: {sample_id}"
                )
            row["target_column_f1"] = set_f1(
                target_columns(predicted),
                target_columns(gold),
            )
            row["target_column_metric_source"] = "derived_from_gold_plan_v2_3"
        else:
            row["target_column_f1"] = None
            row["target_column_metric_source"] = "no_predicted_plan"
        corrected.append(row)
    return corrected


def _float_equal(left: Any, right: Any, tolerance: float = 1e-12) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(float(left) - float(right)) <= tolerance


def _numeric_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    return [
        float(row[field])
        for row in rows
        if row.get(field) is not None
    ]


def efficiency_row(method_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    inputs = _numeric_values(rows, "input_tokens")
    outputs = _numeric_values(rows, "output_tokens")
    generation_latency = _numeric_values(rows, "latency_sec")
    preflight_latency = _numeric_values(rows, "preflight_latency_sec")
    return {
        "method_id": method_id,
        "samples": len(rows),
        "mean_input_tokens": mean(inputs) if inputs else None,
        "median_input_tokens": median(inputs) if inputs else None,
        "mean_output_tokens": mean(outputs) if outputs else None,
        "median_output_tokens": median(outputs) if outputs else None,
        "mean_generation_latency_sec": (
            mean(generation_latency) if generation_latency else None
        ),
        "median_generation_latency_sec": (
            median(generation_latency) if generation_latency else None
        ),
        "mean_preflight_latency_sec": (
            mean(preflight_latency) if preflight_latency else None
        ),
        "output_limit_hit_rate": (
            sum(bool(row.get("hit_max_new_tokens")) for row in rows) / len(rows)
            if rows
            else None
        ),
        "deterministic_processing_latency_sec": None,
        "end_to_end_latency_sec": None,
        "latency_scope_note": (
            "latency_sec is model generation latency; deterministic parser/compiler/"
            "database time was not instrumented end to end"
        ),
    }


def verify_primary_invariants(
    method_id: str,
    enhanced: dict[str, Any],
    canonical: dict[str, Any],
) -> None:
    for field in PRIMARY_INVARIANTS:
        if not _float_equal(enhanced.get(field), canonical.get(field)):
            raise ValueError(
                f"Primary result changed for {method_id}.{field}: "
                f"{canonical.get(field)!r} -> {enhanced.get(field)!r}"
            )


def _input_format(row: dict[str, Any]) -> str:
    for label in row.get("slice_labels") or []:
        if str(label).startswith("input_format:"):
            return str(label).split(":", 1)[1]
    return str(row.get("detected_format") or "unknown")


def _complexity(row: dict[str, Any]) -> str:
    labels = {str(label) for label in row.get("slice_labels") or []}
    if "multi_table" in labels:
        return "multi_table"
    if "single_table" in labels:
        return "single_table"
    return "unknown"


def taxonomy_tables(
    evaluations: dict[str, list[dict[str, Any]]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    overall: list[dict[str, Any]] = []
    by_format: list[dict[str, Any]] = []
    by_complexity: list[dict[str, Any]] = []
    for method_id, rows in evaluations.items():
        classified = [error_taxonomy_row(row) for row in rows]
        counts = Counter(
            (item["error_category"], item["error_stage"])
            for item in classified
        )
        if sum(counts.values()) != len(rows):
            raise ValueError(f"Incomplete taxonomy for {method_id}")
        for (category, stage), count in sorted(counts.items()):
            overall.append(
                {
                    "method_id": method_id,
                    "error_category": category,
                    "error_stage": stage,
                    "count": count,
                    "proportion": count / len(rows),
                }
            )

        format_groups: dict[str, list[dict[str, Any]]] = {}
        for source_row, item in zip(rows, classified):
            format_groups.setdefault(_input_format(source_row), []).append(item)
        for input_format, items in sorted(format_groups.items()):
            format_counts = Counter(
                (item["error_category"], item["error_stage"])
                for item in items
            )
            for (category, stage), count in sorted(format_counts.items()):
                by_format.append(
                    {
                        "method_id": method_id,
                        "input_format": input_format,
                        "format_samples": len(items),
                        "error_category": category,
                        "error_stage": stage,
                        "count": count,
                        "proportion_within_format": count / len(items),
                    }
                )

        complexity_groups: dict[str, list[dict[str, Any]]] = {}
        for source_row, item in zip(rows, classified):
            complexity_groups.setdefault(_complexity(source_row), []).append(item)
        for complexity, items in sorted(complexity_groups.items()):
            complexity_counts = Counter(
                (item["error_category"], item["error_stage"])
                for item in items
            )
            for (category, stage), count in sorted(complexity_counts.items()):
                by_complexity.append(
                    {
                        "method_id": method_id,
                        "complexity": complexity,
                        "complexity_samples": len(items),
                        "error_category": category,
                        "error_stage": stage,
                        "count": count,
                        "proportion_within_complexity": count / len(items),
                    }
                )
    return overall, by_format, by_complexity


def slice_failure_decomposition(
    evaluations: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    dimensions = {
        "input_format": _input_format,
        "complexity": _complexity,
        "database": lambda row: str(row.get("db_id") or "unknown"),
        "operation": lambda row: str(
            row.get("operation_semantics") or "unknown"
        ),
    }
    for method_id, rows in evaluations.items():
        for dimension, resolver in dimensions.items():
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                groups.setdefault(resolver(row), []).append(row)
            for value, group in sorted(groups.items()):
                summary = summarize_run(group)
                output.append(
                    {
                        "method_id": method_id,
                        "dimension": dimension,
                        "value": value,
                        "samples": len(group),
                        "parse_coverage": summary["parse_coverage"],
                        "validation_coverage": summary["validation_coverage"],
                        "build_coverage": summary["build_coverage"],
                        "execution_coverage": summary["execution_coverage"],
                        "target_state_accuracy": summary["target_state_accuracy"],
                        "admission_boundary": summary["admission_boundary"],
                        "method_specific_admission_coverage": summary[
                            "method_specific_admission_coverage"
                        ],
                        "method_specific_admitted_output_accuracy": summary[
                            "method_specific_admitted_output_accuracy"
                        ],
                    }
                )
    return output


def leave_one_database_out(
    evaluations: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    indexed = {
        method: {str(row["sample_id"]): row for row in rows}
        for method, rows in evaluations.items()
    }
    reference = indexed["Gold-MP"]
    databases = sorted({str(row["db_id"]) for row in reference.values()})
    results: list[dict[str, Any]] = []
    for left_id, right_id, label in COMPARISONS:
        for excluded in [None, *databases]:
            sample_ids = sorted(
                sample_id
                for sample_id, row in reference.items()
                if excluded is None or str(row["db_id"]) != excluded
            )
            left_values = [
                bool(indexed[left_id][sample_id].get("target_state_correct"))
                for sample_id in sample_ids
            ]
            right_values = [
                bool(indexed[right_id][sample_id].get("target_state_correct"))
                for sample_id in sample_ids
            ]
            left_accuracy = sum(left_values) / len(sample_ids)
            right_accuracy = sum(right_values) / len(sample_ids)
            results.append(
                {
                    "comparison": label,
                    "direction": f"{right_id}_minus_{left_id}",
                    "excluded_database": excluded or "none_all_databases",
                    "samples": len(sample_ids),
                    "left_accuracy": left_accuracy,
                    "right_accuracy": right_accuracy,
                    "absolute_difference": right_accuracy - left_accuracy,
                }
            )
    return results


def _validate_archive_members(
    members: Iterable[tarfile.TarInfo],
    destination: Path,
) -> None:
    resolved_destination = destination.resolve()
    for member in members:
        target = (resolved_destination / member.name).resolve()
        if target != resolved_destination and resolved_destination not in target.parents:
            raise ValueError(f"Unsafe path in final archive: {member.name}")


def _extract_final_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        _validate_archive_members(members, destination)
        archive.extractall(destination.resolve(), members=members, filter="data")


def _discover_extracted_root(
    workspace: Path,
    import_report: dict[str, Any],
    archive_path: Path,
) -> Path:
    extracted_parent = workspace / "04_results" / "01_extracted_archive"
    imported_name = str(import_report.get("extracted_directory_name") or "")
    if not imported_name:
        imported_name = portable_filename(
            str(import_report.get("extracted_to") or "")
        )
    candidate = extracted_parent / imported_name
    if imported_name and candidate.is_dir():
        return candidate
    extracted_parent.mkdir(parents=True, exist_ok=True)
    candidates = sorted(path for path in extracted_parent.iterdir() if path.is_dir())
    if not candidates and imported_name:
        _extract_final_archive(archive_path, candidate)
        return candidate
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one extracted final archive, found {len(candidates)}"
        )
    return candidates[0]


def _discover_run_root(extracted_root: Path) -> Path:
    parent = extracted_root / "experiments" / "external_holdout"
    candidates = sorted(path for path in parent.iterdir() if path.is_dir())
    if len(candidates) != 1:
        raise ValueError(f"Expected one final result root, found {len(candidates)}")
    return candidates[0]


def _summary_markdown(
    methods: dict[str, dict[str, Any]],
    lodo: list[dict[str, Any]],
    taxonomy: list[dict[str, Any]],
    efficiency: list[dict[str, Any]],
) -> str:
    lines = [
        "# Reporting amendment v2.3 summary",
        "",
        "- Status: **PASS**",
        "- Predictions modified: `false`",
        "- Database executions repeated: `false`",
        "- Primary results changed: `false`",
        "- Side-effect definition corrected: `side_effect_rate` now means any off-target state modification.",
        "- GPU required: `false`",
        "- `coverage` is retained for backward compatibility and is now named method-specific admission coverage.",
        "",
        "## Stage funnel",
        "",
        "| Method | Generation | Parse | Validation | Build | Execution | Correct given execution | Target | Admission boundary | Admitted | Correct given admitted |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for _slug, method_id, _applicable in METHODS:
        item = methods[method_id]
        fmt = lambda value: "n/a" if value is None else f"{float(value):.4f}"
        lines.append(
            f"| {method_id} | {fmt(item['generation_coverage'])} | "
            f"{fmt(item['parse_coverage'])} | {fmt(item['validation_coverage'])} | "
            f"{fmt(item['build_coverage'])} | {fmt(item['execution_coverage'])} | "
            f"{fmt(item['execution_conditional_accuracy'])} | "
            f"{fmt(item['target_state_accuracy'])} | {item['admission_boundary']} | "
            f"{fmt(item['method_specific_admission_coverage'])} | "
            f"{fmt(item['method_specific_admitted_output_accuracy'])} |"
        )

    lines.extend(
        [
            "",
            "## Off-target state modifications",
            "",
            "| Method | Count | Rate | Target also wrong |",
            "|---|---:|---:|---:|",
        ]
    )
    for _slug, method_id, _applicable in METHODS:
        item = methods[method_id]
        lines.append(
            f"| {method_id} | {int(round(float(item['any_off_target_change_rate']) * item['samples']))} | "
            f"{float(item['any_off_target_change_rate']):.4f} | "
            f"{int(round((float(item['any_off_target_change_rate']) - float(item['target_correct_with_side_effect_rate'])) * item['samples']))} |"
        )
    lines.extend(
        [
            "",
            "The compatibility field `side_effect_rate` is identical to `any_off_target_change_rate`. The narrower `target_correct_with_side_effect_rate` is reported separately.",
        ]
    )

    lines.extend(
        [
            "",
            "## Recorded efficiency",
            "",
            "| Method | Mean/median input tokens | Mean output tokens | Mean generation latency (s) | Mean preflight latency (s) | Output-limit hit |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in efficiency:
        fmt = lambda value, digits=2: "n/a" if value is None else f"{float(value):.{digits}f}"
        lines.append(
            f"| {row['method_id']} | {fmt(row['mean_input_tokens'], 1)} / {fmt(row['median_input_tokens'], 1)} | "
            f"{fmt(row['mean_output_tokens'], 1)} | {fmt(row['mean_generation_latency_sec'])} | "
            f"{fmt(row['mean_preflight_latency_sec'], 4)} | {fmt(row['output_limit_hit_rate'], 4)} |"
        )
    lines.extend(
        [
            "",
            "Generation latency is reported separately. Deterministic parser/compiler/database end-to-end latency was not instrumented and is not inferred.",
        ]
    )
    lines.extend(
        [
            "",
            "## Corrected plan-level reporting",
            "",
            "| Method | Plan coverage | Conditional target-column F1 | End-to-end target-column F1 | Conditional table accuracy | End-to-end table accuracy |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _slug, method_id, _applicable in METHODS:
        item = methods[method_id]
        fmt = lambda value: "n/a" if value is None else f"{float(value):.4f}"
        lines.append(
            f"| {method_id} | {fmt(item['plan_metric_coverage'])} | "
            f"{fmt(item['conditional_target_column_f1'])} | "
            f"{fmt(item['end_to_end_target_column_f1'])} | "
            f"{fmt(item['conditional_mapping_table_accuracy'])} | "
            f"{fmt(item['end_to_end_mapping_table_accuracy'])} |"
        )

    lines.extend(["", "## Leave-one-database-out sensitivity", ""])
    for _left, _right, label in COMPARISONS:
        rows = [
            row
            for row in lodo
            if row["comparison"] == label
            and row["excluded_database"] != "none_all_databases"
        ]
        differences = [float(row["absolute_difference"]) for row in rows]
        lines.append(
            f"- {label}: leave-one-database-out difference range "
            f"[{min(differences):.4f}, {max(differences):.4f}]."
        )

    other = sum(
        int(row["count"])
        for row in taxonomy
        if row["error_category"] == "other"
    )
    lines.extend(
        [
            "",
            "## Error taxonomy",
            "",
            f"- Rows remaining in `other`: `{other}`.",
            "- MP-FS+ ID, clarification, normalization, duplicate-target, and conflict-mask failures are now explicitly categorized.",
            "",
            "## Risk–coverage limitation",
            "",
            "The locked artifacts contain a binary, method-specific admission decision but no continuous confidence score or pre-registered threshold family. A risk–coverage curve or AURC cannot be reconstructed without inventing a post-hoc ranking. This amendment therefore reports the observed operating point only. A future protocol must freeze a confidence score and threshold grid before evaluation.",
        ]
    )
    return "\n".join(lines) + "\n"


def reproduce(workspace: Path, output_dir: Path) -> dict[str, Any]:
    import_path = (
        workspace / "07_reproducibility" / "server_final_run" / "IMPORT_REPORT.json"
    )
    import_report = load_json(import_path)
    if import_report.get("status") != "pass" or not import_report.get(
        "paper_result_eligible"
    ):
        raise ValueError("Canonical import report is not paper-result eligible")

    incoming = workspace / "04_results" / "00_incoming_from_server"
    archive_name = str(import_report.get("archive_filename") or "")
    if not archive_name:
        archive_name = portable_filename(str(import_report["archive"]))
    if not archive_name:
        raise ValueError("Import report does not identify a final result archive")
    archive_path = incoming / archive_name
    archive_sha256 = sha256_file(archive_path)
    if archive_sha256 != import_report.get("archive_sha256"):
        raise ValueError("Final result archive checksum mismatch")

    protocol_path = (
        workspace / "07_reproducibility" / "server_final_run" / "final_protocol.json"
    )
    protocol_sha256 = sha256_file(protocol_path)
    if protocol_sha256 != import_report.get("final_protocol_sha256"):
        raise ValueError("Final protocol checksum mismatch")

    canonical_report_path = (
        workspace
        / "04_results"
        / "02_paper_ready"
        / "reports"
        / "final_matrix_results.json"
    )
    canonical_report = load_json(canonical_report_path)
    if canonical_report.get("status") != "pass":
        raise ValueError("Canonical final report is not PASS")

    extracted_root = _discover_extracted_root(
        workspace,
        import_report,
        archive_path,
    )
    run_root = _discover_run_root(extracted_root)
    independent_audit = audit_primary_metrics(
        run_root,
        canonical_report,
        output_dir,
    )
    gold_plans = plan_map(run_root / "gold_mp" / "materialized_write_plans.jsonl")
    if len(gold_plans) != 300:
        raise ValueError("Gold-MP must contain exactly 300 plans")

    enhanced_methods: dict[str, dict[str, Any]] = {}
    corrected_by_method: dict[str, list[dict[str, Any]]] = {}
    reference_ids: set[str] | None = None
    artifact_hashes: dict[str, str] = {}

    for slug, method_id, plan_metrics_applicable in METHODS:
        method_root = run_root / slug
        paths = {
            "raw": method_root / "raw_generations.jsonl",
            "evaluation": method_root / "evaluation.jsonl",
            "plans": method_root / "materialized_write_plans.jsonl",
        }
        for label, path in paths.items():
            if not path.is_file():
                raise ValueError(f"Missing {method_id} {label}: {path}")
            artifact_hashes[f"{slug}/{path.name}"] = sha256_file(path)
        raw = load_jsonl(paths["raw"])
        evaluations = load_jsonl(paths["evaluation"])
        predicted_plans = plan_map(paths["plans"])
        raw_ids = [str(row["sample_id"]) for row in raw]
        evaluation_ids = [str(row["sample_id"]) for row in evaluations]
        if (
            len(raw_ids) != 300
            or len(set(raw_ids)) != 300
            or len(evaluation_ids) != 300
            or len(set(evaluation_ids)) != 300
        ):
            raise ValueError(f"Incomplete or duplicate rows for {method_id}")
        current_ids = set(evaluation_ids)
        if set(raw_ids) != current_ids:
            raise ValueError(f"Raw/evaluation ID mismatch for {method_id}")
        if reference_ids is None:
            reference_ids = current_ids
        elif current_ids != reference_ids:
            raise ValueError(f"Cross-method sample mismatch for {method_id}")

        corrected = corrected_evaluations(
            evaluations,
            predicted_plans,
            gold_plans,
            plan_metrics_applicable=plan_metrics_applicable,
        )
        enhanced = summarize_run(corrected)
        verify_primary_invariants(
            method_id,
            enhanced,
            canonical_report["methods"][method_id],
        )
        enhanced_methods[method_id] = enhanced
        corrected_by_method[method_id] = corrected

    observed_off_target_counts = {
        method_id: sum(bool(row.get("any_off_target_change")) for row in rows)
        for method_id, rows in corrected_by_method.items()
    }
    if observed_off_target_counts != EXPECTED_OFF_TARGET_COUNTS:
        raise ValueError(
            "Off-target audit mismatch: "
            f"expected {EXPECTED_OFF_TARGET_COUNTS}, observed {observed_off_target_counts}"
        )

    gold_target_f1 = enhanced_methods["Gold-MP"][
        "conditional_target_column_f1"
    ]
    if not _float_equal(gold_target_f1, 1.0):
        raise ValueError(f"Corrected Gold-MP target-column F1 is {gold_target_f1}")

    taxonomy, taxonomy_by_format, taxonomy_by_complexity = taxonomy_tables(
        corrected_by_method
    )
    if any(row["error_category"] == "other" for row in taxonomy):
        unknown = [row for row in taxonomy if row["error_category"] == "other"]
        raise ValueError(f"Unclassified error rows remain: {unknown}")
    lodo = leave_one_database_out(corrected_by_method)
    failure_slices = slice_failure_decomposition(corrected_by_method)

    stage_rows = [
        {
            "method_id": method_id,
            "admission_boundary": enhanced_methods[method_id]["admission_boundary"],
            **{
                field: enhanced_methods[method_id].get(field)
                for field in STAGE_FIELDS
            },
        }
        for _slug, method_id, _applicable in METHODS
    ]
    plan_rows = [
        {
            "method_id": method_id,
            **{
                field: enhanced_methods[method_id].get(field)
                for field in PLAN_FIELDS
            },
        }
        for _slug, method_id, _applicable in METHODS
    ]
    selective_rows = [
        {
            "method_id": method_id,
            "admission_boundary": enhanced_methods[method_id]["admission_boundary"],
            "target_state_accuracy": enhanced_methods[method_id][
                "target_state_accuracy"
            ],
            "method_specific_admission_coverage": enhanced_methods[method_id][
                "method_specific_admission_coverage"
            ],
            "method_specific_admitted_output_accuracy": enhanced_methods[method_id][
                "method_specific_admitted_output_accuracy"
            ],
            "selective_risk": enhanced_methods[method_id]["selective_risk"],
            "side_effect_rate": enhanced_methods[method_id]["side_effect_rate"],
            "any_off_target_change_rate": enhanced_methods[method_id][
                "any_off_target_change_rate"
            ],
            "target_correct_with_side_effect_rate": enhanced_methods[method_id][
                "target_correct_with_side_effect_rate"
            ],
        }
        for _slug, method_id, _applicable in METHODS
    ]
    off_target_rows = [
        {
            "method_id": method_id,
            "sample_id": row["sample_id"],
            "db_id": row.get("db_id"),
            "target_state_correct": bool(row.get("target_state_correct")),
            "target_mismatched_tables": ",".join(
                row.get("target_mismatched_tables") or []
            ),
            "off_target_mismatched_tables": ",".join(
                row.get("off_target_mismatched_tables") or []
            ),
            "error_type": row.get("error_type"),
        }
        for method_id, rows in corrected_by_method.items()
        for row in rows
        if row.get("any_off_target_change")
    ]
    efficiency_rows = [
        efficiency_row(method_id, corrected_by_method[method_id])
        for _slug, method_id, _applicable in METHODS
    ]
    efficiency_by_format_rows = []
    for method_id, rows in corrected_by_method.items():
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(_input_format(row), []).append(row)
        for input_format, grouped_rows in sorted(grouped.items()):
            efficiency_by_format_rows.append(
                {
                    **efficiency_row(method_id, grouped_rows),
                    "input_format": input_format,
                    "target_state_accuracy": sum(
                        bool(row.get("target_state_correct"))
                        for row in grouped_rows
                    )
                    / len(grouped_rows),
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_dir / "stage_funnel.csv",
        stage_rows,
        ["method_id", "admission_boundary", *STAGE_FIELDS],
    )
    write_csv(
        output_dir / "plan_metrics_with_denominators.csv",
        plan_rows,
        ["method_id", *PLAN_FIELDS],
    )
    write_csv(
        output_dir / "error_taxonomy.csv",
        taxonomy,
        [
            "method_id",
            "error_category",
            "error_stage",
            "count",
            "proportion",
        ],
    )
    write_csv(
        output_dir / "error_taxonomy_by_format.csv",
        taxonomy_by_format,
        [
            "method_id",
            "input_format",
            "format_samples",
            "error_category",
            "error_stage",
            "count",
            "proportion_within_format",
        ],
    )
    write_csv(
        output_dir / "error_taxonomy_by_complexity.csv",
        taxonomy_by_complexity,
        [
            "method_id",
            "complexity",
            "complexity_samples",
            "error_category",
            "error_stage",
            "count",
            "proportion_within_complexity",
        ],
    )
    write_csv(
        output_dir / "slice_failure_decomposition.csv",
        failure_slices,
        [
            "method_id",
            "dimension",
            "value",
            "samples",
            "parse_coverage",
            "validation_coverage",
            "build_coverage",
            "execution_coverage",
            "target_state_accuracy",
            "admission_boundary",
            "method_specific_admission_coverage",
            "method_specific_admitted_output_accuracy",
        ],
    )
    write_csv(
        output_dir / "leave_one_database_out.csv",
        lodo,
        [
            "comparison",
            "direction",
            "excluded_database",
            "samples",
            "left_accuracy",
            "right_accuracy",
            "absolute_difference",
        ],
    )
    write_csv(
        output_dir / "selective_operating_points.csv",
        selective_rows,
        [
            "method_id",
            "admission_boundary",
            "target_state_accuracy",
            "method_specific_admission_coverage",
            "method_specific_admitted_output_accuracy",
            "selective_risk",
            "side_effect_rate",
            "any_off_target_change_rate",
            "target_correct_with_side_effect_rate",
        ],
    )
    write_csv(
        output_dir / "off_target_changes.csv",
        off_target_rows,
        [
            "method_id",
            "sample_id",
            "db_id",
            "target_state_correct",
            "target_mismatched_tables",
            "off_target_mismatched_tables",
            "error_type",
        ],
    )
    audit_path = output_dir / "off_target_metric_audit.json"
    dump_json(
        audit_path,
        {
            "status": "pass",
            "definition": "any state mismatch on a table outside the Gold-MP target-table set",
            "legacy_definition": "target state correct and at least one strict-state mismatch",
            "counts": observed_off_target_counts,
            "affected_samples": off_target_rows,
            "primary_target_state_metrics_changed": False,
            "database_execution_repeated": False,
        },
    )
    canonical_copy = json.loads(json.dumps(canonical_report))
    remove_legacy_side_effect_rate(canonical_copy)
    corrected_matrix = {
        "report_version": 4,
        "status": "pass",
        "canonical_for_off_target_reporting": True,
        "supersedes_for_off_target_reporting": "final_matrix_results.json",
        "correction_type": "off_target_metric_definition_correction",
        "primary_target_state_metrics_modified": False,
        "database_execution_repeated": False,
        "legacy_metric": (
            "side_effect_rate based on target-correct-with-side-effect "
            "was deprecated"
        ),
        "off_target_metric_definition": (
            "any state mismatch on a table outside "
            "the Gold-MP target-table set"
        ),
        "historical_base_source": {
            "path": (
                "04_results/02_paper_ready/reports/"
                "final_matrix_results.json"
            ),
            "sha256": sha256_file(canonical_report_path),
        },
        "off_target_correction_source": {
            "path": (
                "04_results/03_analysis_work/"
                "reporting_v2_3_20260801/"
                "off_target_metric_audit.json"
            ),
            "sha256": sha256_file(audit_path),
        },
    }
    corrected_matrix.update(
        {
            key: value
            for key, value in canonical_copy.items()
            if key not in {"report_version", "status"}
        }
    )
    for _slug, method_id, _applicable in METHODS:
        method_record = corrected_matrix["methods"][method_id]
        rows = corrected_by_method[method_id]
        if len(rows) != 300:
            raise ValueError(f"{method_id}: expected 300 evaluation rows")
        target_correct_count = sum(
            bool(row.get("target_state_correct")) for row in rows
        )
        off_target_count = sum(
            bool(row.get("any_off_target_change")) for row in rows
        )
        if off_target_count != EXPECTED_OFF_TARGET_COUNTS[method_id]:
            raise ValueError(
                f"{method_id}: unexpected off-target count {off_target_count}"
            )
        if not _float_equal(
            target_correct_count / len(rows),
            method_record.get("target_state_accuracy"),
        ):
            raise ValueError(
                f"{method_id}: corrected target-state accuracy changed"
            )
        method_record.pop("side_effect_rate", None)
        method_record["target_state_correct_count"] = target_correct_count
        method_record["off_target_event_count"] = off_target_count
        method_record["off_target_modification_rate"] = (
            off_target_count / len(rows)
        )
    corrected_matrix_path = output_dir / "final_matrix_results_corrected.json"
    dump_json(corrected_matrix_path, corrected_matrix)
    efficiency_fields = [
        "method_id",
        "samples",
        "mean_input_tokens",
        "median_input_tokens",
        "mean_output_tokens",
        "median_output_tokens",
        "mean_generation_latency_sec",
        "median_generation_latency_sec",
        "mean_preflight_latency_sec",
        "output_limit_hit_rate",
        "deterministic_processing_latency_sec",
        "end_to_end_latency_sec",
        "latency_scope_note",
    ]
    write_csv(
        output_dir / "efficiency_summary.csv",
        efficiency_rows,
        efficiency_fields,
    )
    write_csv(
        output_dir / "efficiency_by_format.csv",
        efficiency_by_format_rows,
        [
            "method_id",
            "input_format",
            "target_state_accuracy",
            *efficiency_fields[1:],
        ],
    )

    report = {
        "report_version": 1,
        "reporting_amendment_id": "reporting_metric_integrity_v2_3",
        "status": "pass",
        "analysis_class": "post_hoc_deterministic_reporting",
        "paper_result_eligible": True,
        "predictions_modified": False,
        "database_executions_repeated": False,
        "gpu_inference_rerun": False,
        "primary_results_changed": False,
        "base_reporting_protocol_id": import_report.get("reporting_protocol_id"),
        "base_protocol_sha256": protocol_sha256,
        "base_result_archive_sha256": archive_sha256,
        "sample_count": 300,
        "method_count": 6,
        "changes": [
            "derive target columns from Gold-MP write plans",
            "classify MP-FS+ ID, clarification, normalization, duplicate, and conflict-mask errors",
            "report a cross-method stage funnel and execution-conditional accuracy",
            "rename coverage as method-specific admission coverage while retaining compatibility aliases",
            "report plan-metric coverage plus conditional and end-to-end plan metrics",
            "add leave-one-database-out sensitivity",
            "decompose failures by input format, table complexity, database, and operation",
            "generate deterministic SVG figures for the main matrix, format slices, and error taxonomy",
            "document why a risk-coverage curve is not identifiable from a binary admission artifact",
            "independently recompute primary metrics without importing the production metrics module",
            "correct side-effect reporting to count every off-target table modification",
            "separate any off-target change from target-correct-with-side-effect",
            "make archive and extraction provenance paths portable across Windows and POSIX",
            "report mean and median token/generation latency without labeling it end-to-end latency",
        ],
        "methods": enhanced_methods,
        "off_target_metric_audit": {
            "status": "pass",
            "counts": observed_off_target_counts,
        },
        "efficiency": {
            "scope": "recorded model generation and preflight fields only",
            "deterministic_end_to_end_latency_available": False,
            "rows": efficiency_rows,
        },
        "leave_one_database_out": lodo,
        "input_artifact_sha256": dict(sorted(artifact_hashes.items())),
    }
    report_path = output_dir / "reporting_v2_3_results.json"
    dump_json(report_path, report)
    summary_path = output_dir / "reporting_v2_3_summary.md"
    summary_path.write_text(
        _summary_markdown(enhanced_methods, lodo, taxonomy, efficiency_rows),
        encoding="utf-8",
        newline="\n",
    )
    build_figures(enhanced_methods, taxonomy, output_dir)

    reporting_outputs = (
        "stage_funnel.csv",
        "plan_metrics_with_denominators.csv",
        "error_taxonomy.csv",
        "error_taxonomy_by_format.csv",
        "error_taxonomy_by_complexity.csv",
        "slice_failure_decomposition.csv",
        "leave_one_database_out.csv",
        "selective_operating_points.csv",
        "reporting_v2_3_results.json",
        "reporting_v2_3_summary.md",
        "off_target_changes.csv",
        "off_target_metric_audit.json",
        "final_matrix_results_corrected.json",
        "efficiency_summary.csv",
        "efficiency_by_format.csv",
        "independent_primary_audit.json",
        "independent_primary_audit.csv",
        "figures/main_accuracy_coverage.svg",
        "figures/input_format_accuracy.svg",
        "figures/mp_fs_plus_error_taxonomy.svg",
    )
    output_hashes = {
        name: sha256_file(output_dir / name)
        for name in reporting_outputs
    }
    source_root = Path(__file__).resolve().parents[3]
    reporting_source_files = (
        "reproduce_paper.py",
        "src/nldbwrite_v3/analysis/reporting_v2_3.py",
        "src/nldbwrite_v3/evaluator/state.py",
        "src/nldbwrite_v3/experiments/metrics.py",
        "src/nldbwrite_v3/experiments/run_method.py",
        "tests/test_metrics.py",
        "tests/test_reporting_v2_3.py",
        "tests/test_gold_execution_repair.py",
        "src/nldbwrite_v3/analysis/independent_audit.py",
        "src/nldbwrite_v3/analysis/figures_v2_2.py",
        "tests/test_independent_audit.py",
        "tests/test_figures_v2_2.py",
    )
    source_hashes = {
        name: sha256_file(source_root / name)
        for name in reporting_source_files
    }
    amendment = {
        "amendment_id": "reporting_metric_integrity_v2_3",
        "status": "frozen_post_hoc_reporting_amendment",
        "amendment_date_utc": "2026-08-01",
        "base_protocol_sha256": protocol_sha256,
        "base_result_archive_sha256": archive_sha256,
        "prediction_artifacts_modified": False,
        "primary_results_modified": False,
        "requires_gpu": False,
        "source_sha256": source_hashes,
        "output_sha256": output_hashes,
    }
    amendment_path = output_dir / "REPORTING_AMENDMENT_V2_3.json"
    dump_json(amendment_path, amendment)
    reproduction = {
        "status": "pass",
        "artifact": "final_release",
        "verified_archive_sha256": archive_sha256,
        "verified_protocol_sha256": protocol_sha256,
        "verified_samples": 300,
        "verified_methods": 6,
        "verified_primary_results_unchanged": True,
        "corrected_gold_target_column_f1": gold_target_f1,
        "uncategorized_error_rows": 0,
        "off_target_metric_audit_status": "pass",
        "off_target_counts": observed_off_target_counts,
        "independent_primary_audit_status": independent_audit["status"],
        "reporting_amendment_sha256": sha256_file(amendment_path),
    }
    dump_json(output_dir / "REPRODUCTION_PASS.json", reproduction)
    return reproduction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce reporting amendment v2.3 from immutable final artifacts; "
            "no model inference or database execution is performed."
        )
    )
    parser.add_argument("--artifact", default="final_release", choices=["final_release"])
    parser.add_argument("--workspace-root")
    parser.add_argument("--output-dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    default_workspace = Path(__file__).resolve().parents[6]
    workspace = Path(args.workspace_root).resolve() if args.workspace_root else default_workspace
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else workspace
        / "04_results"
        / "03_analysis_work"
        / "reporting_v2_3_20260801"
    )
    result = reproduce(workspace, output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
