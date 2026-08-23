#!/usr/bin/env python3
"""Frozen Stage-4 fresh 7B statistical analysis.

This script is intentionally result-agnostic and must be frozen before the
fresh 7B run is inspected. It hard-validates the frozen sample identity for all
predeclared Stage-4 methods, then emits the complete predeclared primary,
safety, diagnostic, subgroup, and sample-level analysis artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.run_stage3_causal_replay import write_csv, write_json  # noqa: E402
from scripts.analysis.run_stage4_fresh_7b_protocol import (  # noqa: E402
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
)


PRIMARY_BASELINE = "original_mp_fs_plus"
PRIMARY_METHOD = "d_g1_primary"
METHOD_SLUGS = (
    "direct",
    "j_fs",
    "original_mp_fs_plus",
    "d_g1_primary",
    "d_only_secondary",
    "full_secondary",
    "no_c_secondary",
)
PRIMARY_METRICS = ("target_state_correct", "strict_full_state_correct")
SUBGROUPS = (
    ("input_type", "input_type"),
    ("operation_type", "operation_type"),
    ("database", "db_id"),
    ("dependency_sensitive", "dependency_sensitive"),
)
CONSTRAINT_ERROR_TYPES = {
    "foreign_key_violation",
    "unique_violation",
    "not_null_violation",
    "check_violation",
    "unclassified_constraint_or_execution_error",
}
ANALYSIS_ARTIFACTS = (
    "variant_metrics.csv",
    "primary_paired_analysis.json",
    "subgroup_metrics.csv",
    "failure_stage_summary.csv",
    "intervention_summary.csv",
    "sample_level_analysis.csv",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return truthy(value)


def rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else math.nan


def metric_bool(row: Mapping[str, Any], key: str) -> bool:
    if key not in row:
        raise KeyError(f"Required metric field {key!r} missing from evaluation row")
    return truthy(row[key])


def correctness(row: Mapping[str, Any]) -> bool:
    """Backward-compatible default correctness used by earlier imports."""
    if "target_state_correct" in row:
        return truthy(row["target_state_correct"])
    if "strict_full_state_correct" in row:
        return truthy(row["strict_full_state_correct"])
    if "correct" in row:
        return truthy(row["correct"])
    raise KeyError("No correctness field found in evaluation row")


def accuracy(
    rows: Iterable[Mapping[str, Any]],
    metric_key: str = "target_state_correct",
) -> float:
    rows = list(rows)
    return (
        sum(metric_bool(row, metric_key) for row in rows) / len(rows)
        if rows
        else math.nan
    )


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_dicts(nested)


def unique_dicts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = canonical(row)
        if key not in seen:
            output.append(deepcopy(row))
            seen.add(key)
    return output


def paired_counts(
    baseline: Mapping[str, bool],
    method: Mapping[str, bool],
) -> dict[str, int]:
    if set(baseline) != set(method):
        missing_from_method = sorted(set(baseline) - set(method))[:5]
        missing_from_baseline = sorted(set(method) - set(baseline))[:5]
        raise ValueError(
            "Paired analysis requires identical sample IDs; "
            f"missing_from_method={missing_from_method}, "
            f"missing_from_baseline={missing_from_baseline}"
        )
    shared = sorted(baseline)
    counts = Counter()
    for sample_id in shared:
        left = bool(baseline[sample_id])
        right = bool(method[sample_id])
        if left and right:
            counts["both_correct"] += 1
        elif left and not right:
            counts["baseline_only_correct"] += 1
        elif right and not left:
            counts["method_only_correct"] += 1
        else:
            counts["both_wrong"] += 1
    counts["paired_sample_count"] = len(shared)
    return dict(counts)


def mcnemar_exact_pvalue(baseline_only: int, method_only: int) -> float:
    discordant = baseline_only + method_only
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, k)
        for k in range(0, min(baseline_only, method_only) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def cluster_bootstrap_accuracy_difference(
    *,
    baseline: Mapping[str, bool],
    method: Mapping[str, bool],
    source_groups: Mapping[str, str],
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    if set(baseline) != set(method):
        raise ValueError("Cluster bootstrap requires identical paired sample IDs")
    shared = sorted(baseline)
    grouped: dict[str, list[str]] = defaultdict(list)
    for sample_id in shared:
        if sample_id not in source_groups:
            raise KeyError(f"Missing source_group for sample_id={sample_id}")
        grouped[str(source_groups[sample_id])].append(sample_id)
    groups = sorted(grouped)
    if not groups:
        return {
            "replicates": replicates,
            "seed": seed,
            "cluster_count": 0,
            "sample_count": 0,
            "observed_difference": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
        }
    observed = (
        sum(method[sample_id] for sample_id in shared)
        - sum(baseline[sample_id] for sample_id in shared)
    ) / len(shared)
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(replicates):
        sampled_ids: list[str] = []
        for _group_index in groups:
            sampled_group = rng.choice(groups)
            sampled_ids.extend(grouped[sampled_group])
        deltas.append(
            (
                sum(method[sample_id] for sample_id in sampled_ids)
                - sum(baseline[sample_id] for sample_id in sampled_ids)
            )
            / len(sampled_ids)
        )
    deltas.sort()

    def percentile(sorted_values: list[float], fraction: float) -> float:
        if len(sorted_values) == 1:
            return sorted_values[0]
        position = fraction * (len(sorted_values) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return sorted_values[lower]
        weight = position - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight

    return {
        "replicates": replicates,
        "seed": seed,
        "cluster_count": len(groups),
        "sample_count": len(shared),
        "observed_difference": observed,
        "ci_low": percentile(deltas, 0.025),
        "ci_high": percentile(deltas, 0.975),
    }


def load_frozen_sample_ids(protocol_root: Path) -> list[str]:
    path = protocol_root / "data" / "fresh_sample_ids.txt"
    ids = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(ids) != len(set(ids)):
        duplicates = [sample_id for sample_id, count in Counter(ids).items() if count > 1]
        raise SystemExit(f"STOP: frozen sample ID list contains duplicates: {duplicates[:5]}")
    if not ids:
        raise SystemExit("STOP: frozen sample ID list is empty")
    return ids


def load_sample_metadata(
    protocol_root: Path,
    frozen_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    manifest_path = protocol_root / "prompt_audit" / "prompt_manifest.csv"
    metadata: dict[str, dict[str, Any]] = {}
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("method_slug") != PRIMARY_METHOD:
                continue
            sample_id = str(row["sample_id"])
            metadata[sample_id] = {
                "sample_id": sample_id,
                "source_group": str(row.get("source_group") or sample_id),
                "db_id": str(row.get("db_id") or ""),
                "input_type": str(row.get("detected_mode") or ""),
                "operation_type": str(row.get("operation_type") or ""),
                "dependency_sensitive": int(truthy(row.get("dependency_sensitive"))),
            }
    frozen_set = set(frozen_ids)
    if set(metadata) != frozen_set:
        raise SystemExit(
            "STOP: prompt manifest metadata does not match frozen IDs; "
            f"metadata_count={len(metadata)}, frozen_count={len(frozen_set)}, "
            f"missing_metadata={sorted(frozen_set - set(metadata))[:5]}, "
            f"extra_metadata={sorted(set(metadata) - frozen_set)[:5]}"
        )
    return metadata


def load_d_activation_ids(protocol_root: Path) -> set[str]:
    path = protocol_root / "analysis" / "d_parser_opportunity_audit.csv"
    if not path.is_file():
        return set()
    output: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if truthy(row.get("changed")):
                output.add(str(row["sample_id"]))
    return output


def validate_method_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    method_slug: str,
    frozen_ids: Sequence[str],
) -> None:
    expected = set(frozen_ids)
    actual_ids = [str(row.get("sample_id") or "") for row in rows]
    duplicates = sorted(
        sample_id for sample_id, count in Counter(actual_ids).items() if count > 1
    )
    if duplicates:
        raise SystemExit(
            f"STOP: method {method_slug} has duplicate sample IDs: {duplicates[:10]}"
        )
    actual = set(actual_ids)
    if len(rows) != len(frozen_ids) or actual != expected:
        raise SystemExit(
            f"STOP: method {method_slug} must contain exactly {len(frozen_ids)} "
            "unique frozen sample IDs; "
            f"rows={len(rows)}, unique={len(actual)}, "
            f"missing={sorted(expected - actual)[:10]}, "
            f"extra={sorted(actual - expected)[:10]}"
        )
    for row in rows:
        for key in PRIMARY_METRICS:
            if key not in row:
                raise SystemExit(
                    f"STOP: method {method_slug} sample {row.get('sample_id')} "
                    f"is missing required metric field {key!r}"
                )


def load_method_rows(
    result_root: Path,
    method_slug: str,
    frozen_ids: Sequence[str],
) -> list[dict[str, Any]]:
    path = result_root / "methods" / method_slug / "evaluation.jsonl"
    if not path.is_file():
        raise SystemExit(f"STOP: missing required Stage-4 evaluation artifact: {path}")
    rows = read_jsonl(path)
    validate_method_rows(rows, method_slug=method_slug, frozen_ids=frozen_ids)
    return rows


def accepted_value(row: Mapping[str, Any]) -> bool | None:
    if "accepted_output" in row:
        return optional_bool(row.get("accepted_output"))
    if "preflight_accepted" in row:
        return optional_bool(row.get("preflight_accepted"))
    return None


def is_constraint_failure(row: Mapping[str, Any]) -> bool:
    error_type = str(row.get("error_type") or row.get("error_class") or "").strip()
    if error_type in CONSTRAINT_ERROR_TYPES:
        return True
    message = str(row.get("error_message") or row.get("error") or "").casefold()
    return any(
        needle in message
        for needle in (
            "foreign key constraint failed",
            "unique constraint failed",
            "not null constraint failed",
            "check constraint failed",
            "constraint failed",
        )
    )


def first_failure_stage(row: Mapping[str, Any]) -> str:
    for explicit_key in ("first_failure_stage", "first_failure"):
        if row.get(explicit_key):
            return str(row[explicit_key])
    if not truthy(row.get("parse_success", True)):
        return "parse"
    if not truthy(row.get("build_success", True)):
        return "build"
    preflight = optional_bool(row.get("preflight_accepted"))
    if preflight is False:
        return "preflight"
    if not truthy(row.get("execution_success", False)):
        return "execution"
    if not metric_bool(row, "target_state_correct"):
        return "state_mismatch"
    if not metric_bool(row, "strict_full_state_correct"):
        return "off_target_state_change"
    return "none"


def metric_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    accepted_rows = [row for row in rows if accepted_value(row) is True]
    selective_rows = [row for row in rows if accepted_value(row) is not None]
    accepted_correct = [
        row for row in accepted_rows if metric_bool(row, "target_state_correct")
    ]
    false_accept_count = sum(
        accepted_value(row) is True and not metric_bool(row, "target_state_correct")
        for row in rows
    )
    constraint_failure_count = sum(is_constraint_failure(row) for row in rows)
    off_target_state_change_count = sum(
        truthy(row.get("any_off_target_change")) for row in rows
    )
    return {
        "n": n,
        "target_state_accuracy": rate(
            sum(metric_bool(row, "target_state_correct") for row in rows), n
        ),
        "strict_full_state_accuracy": rate(
            sum(metric_bool(row, "strict_full_state_correct") for row in rows), n
        ),
        "coverage": rate(len(accepted_rows), len(selective_rows)),
        "accepted_output_accuracy": (
            rate(len(accepted_correct), len(accepted_rows)) if accepted_rows else ""
        ),
        "false_accept_count": false_accept_count,
        "false_accept_rate": rate(false_accept_count, n),
        "execution_success_rate": rate(
            sum(truthy(row.get("execution_success")) for row in rows), n
        ),
        "constraint_failure_count": constraint_failure_count,
        "constraint_failure_rate": rate(constraint_failure_count, n),
        "off_target_state_change_count": off_target_state_change_count,
        "off_target_state_change_rate": rate(off_target_state_change_count, n),
    }


def load_source_groups(protocol_root: Path) -> dict[str, str]:
    frozen_ids = load_frozen_sample_ids(protocol_root)
    metadata = load_sample_metadata(protocol_root, frozen_ids)
    return {
        sample_id: str(row["source_group"])
        for sample_id, row in metadata.items()
    }


def load_method_correctness(result_root: Path, method_slug: str) -> dict[str, bool]:
    # Kept for compatibility with existing imports; full analysis uses
    # load_method_rows so it can hard-validate against frozen sample IDs.
    rows = read_jsonl(result_root / "methods" / method_slug / "evaluation.jsonl")
    return {str(row["sample_id"]): correctness(row) for row in rows}


def repair_traces_for_method(method_dir: Path) -> dict[str, list[dict[str, Any]]]:
    traces: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for filename in (
        "materialized_write_plans.jsonl",
        "verification.jsonl",
        "compiled_programs.jsonl",
        "evaluation.jsonl",
    ):
        path = method_dir / filename
        if not path.is_file():
            continue
        for row in read_jsonl(path):
            sample_id = str(row.get("sample_id") or "")
            candidates = [
                item
                for item in walk_dicts(row)
                if item.get("stage2_intervention") == "G1_evidence_span_boundary_repair"
                and "repair_attempted" in item
                and "repair_applied" in item
                and "repair_succeeded" in item
            ]
            if candidates:
                traces[sample_id].extend(unique_dicts(candidates))
    return {sample_id: unique_dicts(items) for sample_id, items in traces.items()}


def build_sample_level_rows(
    *,
    method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    metadata: Mapping[str, Mapping[str, Any]],
    sample_ids: Sequence[str],
    d_activation_ids: set[str],
    g1_traces: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method_slug in METHOD_SLUGS:
        rows_by_id = {str(row["sample_id"]): row for row in method_rows[method_slug]}
        for sample_id in sample_ids:
            row = rows_by_id[sample_id]
            accepted = accepted_value(row)
            traces = (
                list(g1_traces.get(sample_id) or [])
                if method_slug == PRIMARY_METHOD
                else []
            )
            g1_attempted = any(truthy(trace.get("repair_attempted")) for trace in traces)
            g1_applied = any(truthy(trace.get("repair_applied")) for trace in traces)
            g1_succeeded = any(truthy(trace.get("repair_succeeded")) for trace in traces)
            base = metadata[sample_id]
            output.append(
                {
                    "sample_id": sample_id,
                    "source_group": base["source_group"],
                    "db_id": base["db_id"],
                    "input_type": base["input_type"],
                    "operation_type": base["operation_type"],
                    "dependency_sensitive": base["dependency_sensitive"],
                    "method": method_slug,
                    "method_slug": method_slug,
                    "target_state_correct": int(metric_bool(row, "target_state_correct")),
                    "strict_full_state_correct": int(
                        metric_bool(row, "strict_full_state_correct")
                    ),
                    "accepted": "" if accepted is None else int(accepted),
                    "execution_success": int(truthy(row.get("execution_success"))),
                    "first_failure_stage": first_failure_stage(row),
                    "false_accept": int(
                        accepted is True
                        and not metric_bool(row, "target_state_correct")
                    ),
                    "constraint_failure": int(is_constraint_failure(row)),
                    "off_target_state_change": int(
                        truthy(row.get("any_off_target_change"))
                    ),
                    "generation_status": row.get("generation_status") or "",
                    "input_truncated": int(truthy(row.get("input_truncated"))),
                    "hit_max_new_tokens": int(truthy(row.get("hit_max_new_tokens"))),
                    "error_type": row.get("error_type") or "",
                    "D_activated": int(
                        method_slug
                        in {PRIMARY_METHOD, "d_only_secondary", "full_secondary"}
                        and sample_id in d_activation_ids
                    ),
                    "G1_repair_attempted": int(g1_attempted),
                    "G1_repair_applied": int(g1_applied),
                    "G1_repair_succeeded": int(g1_succeeded),
                    "G1_repair_rules": "|".join(
                        sorted({str(trace.get("repair_rule") or "") for trace in traces})
                    ),
                }
            )
    return output


def build_primary_paired_analysis(
    *,
    method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    source_groups: Mapping[str, str],
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "baseline": PRIMARY_BASELINE,
        "method": PRIMARY_METHOD,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "metrics": {},
    }
    baseline_by_id = {
        str(row["sample_id"]): row for row in method_rows[PRIMARY_BASELINE]
    }
    method_by_id = {
        str(row["sample_id"]): row for row in method_rows[PRIMARY_METHOD]
    }
    for metric in PRIMARY_METRICS:
        baseline = {
            sample_id: metric_bool(row, metric)
            for sample_id, row in baseline_by_id.items()
        }
        method = {
            sample_id: metric_bool(row, metric)
            for sample_id, row in method_by_id.items()
        }
        counts = paired_counts(baseline, method)
        bootstrap = cluster_bootstrap_accuracy_difference(
            baseline=baseline,
            method=method,
            source_groups=source_groups,
            replicates=BOOTSTRAP_REPLICATES,
            seed=BOOTSTRAP_SEED,
        )
        baseline_only = counts.get("baseline_only_correct", 0)
        method_only = counts.get("method_only_correct", 0)
        output["metrics"][metric] = {
            "both_correct": counts.get("both_correct", 0),
            "original_only_correct": baseline_only,
            "D_G1_only_correct": method_only,
            "both_wrong": counts.get("both_wrong", 0),
            "paired_sample_count": counts.get("paired_sample_count", 0),
            "accuracy_difference": bootstrap["observed_difference"],
            "cluster_bootstrap_95ci": {
                "ci_low": bootstrap["ci_low"],
                "ci_high": bootstrap["ci_high"],
                "replicates": bootstrap["replicates"],
                "seed": bootstrap["seed"],
                "cluster_count": bootstrap["cluster_count"],
            },
            "mcnemar_exact_p": mcnemar_exact_pvalue(baseline_only, method_only),
        }
    return output


def build_variant_metrics(
    method_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        {"method_slug": method_slug, **metric_summary(method_rows[method_slug])}
        for method_slug in METHOD_SLUGS
    ]


def build_subgroup_metrics(
    sample_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method_slug in METHOD_SLUGS:
        method_subset = [row for row in sample_rows if row["method_slug"] == method_slug]
        for subgroup_type, key in SUBGROUPS:
            values = sorted({str(row[key]) for row in method_subset})
            for value in values:
                subset = [row for row in method_subset if str(row[key]) == value]
                accepted_rows = [row for row in subset if str(row.get("accepted")) == "1"]
                output.append(
                    {
                        "method_slug": method_slug,
                        "subgroup_type": subgroup_type,
                        "subgroup_value": value,
                        "n": len(subset),
                        "target_state_accuracy": rate(
                            sum(truthy(row["target_state_correct"]) for row in subset),
                            len(subset),
                        ),
                        "strict_full_state_accuracy": rate(
                            sum(
                                truthy(row["strict_full_state_correct"])
                                for row in subset
                            ),
                            len(subset),
                        ),
                        "coverage": rate(len(accepted_rows), len(subset)),
                        "accepted_output_accuracy": (
                            rate(
                                sum(
                                    truthy(row["target_state_correct"])
                                    for row in accepted_rows
                                ),
                                len(accepted_rows),
                            )
                            if accepted_rows
                            else ""
                        ),
                        "false_accept_count": sum(
                            truthy(row["false_accept"]) for row in subset
                        ),
                    }
                )
    return output


def build_failure_stage_summary(
    sample_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method_slug in METHOD_SLUGS:
        subset = [row for row in sample_rows if row["method_slug"] == method_slug]
        counts = Counter(str(row["first_failure_stage"]) for row in subset)
        for stage, count in sorted(counts.items()):
            output.append(
                {
                    "method_slug": method_slug,
                    "first_failure_stage": stage,
                    "count": count,
                    "rate": rate(count, len(subset)),
                }
            )
    return output


def build_intervention_summary(
    *,
    sample_rows: Sequence[Mapping[str, Any]],
    d_activation_ids: set[str],
    g1_traces: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    primary_rows = [row for row in sample_rows if row["method_slug"] == PRIMARY_METHOD]
    by_id = {str(row["sample_id"]): row for row in primary_rows}
    trace_sample_ids = sorted(set(g1_traces))
    applied_ids = [
        sample_id
        for sample_id, traces in g1_traces.items()
        if any(truthy(trace.get("repair_applied")) for trace in traces)
    ]
    return [
        {
            "method_slug": PRIMARY_METHOD,
            "D_activation_count": sum(
                sample_id in d_activation_ids for sample_id in by_id
            ),
            "G1_attempts": sum(
                any(truthy(trace.get("repair_attempted")) for trace in traces)
                for traces in g1_traces.values()
            ),
            "G1_applied": len(applied_ids),
            "G1_revalidation_success": sum(
                any(truthy(trace.get("repair_succeeded")) for trace in traces)
                for traces in g1_traces.values()
            ),
            "G1_final_state_correct_after_application": sum(
                truthy(by_id[sample_id]["target_state_correct"])
                for sample_id in applied_ids
                if sample_id in by_id
            ),
            "G1_final_state_incorrect_after_application": sum(
                not truthy(by_id[sample_id]["target_state_correct"])
                for sample_id in applied_ids
                if sample_id in by_id
            ),
            "G1_trace_sample_count": len(trace_sample_ids),
            "G1_trace_sample_ids": "|".join(trace_sample_ids),
        }
    ]


def analyze_result_root(
    protocol_root: Path,
    result_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen_ids = load_frozen_sample_ids(protocol_root)
    metadata = load_sample_metadata(protocol_root, frozen_ids)
    d_activation_ids = load_d_activation_ids(protocol_root)
    method_rows: dict[str, list[dict[str, Any]]] = {
        slug: load_method_rows(result_root, slug, frozen_ids)
        for slug in METHOD_SLUGS
    }
    source_groups = {
        sample_id: str(row["source_group"])
        for sample_id, row in metadata.items()
    }
    g1_traces = repair_traces_for_method(result_root / "methods" / PRIMARY_METHOD)
    sample_rows = build_sample_level_rows(
        method_rows=method_rows,
        metadata=metadata,
        sample_ids=frozen_ids,
        d_activation_ids=d_activation_ids,
        g1_traces=g1_traces,
    )
    variant_metrics = build_variant_metrics(method_rows)
    primary = build_primary_paired_analysis(
        method_rows=method_rows,
        source_groups=source_groups,
    )
    subgroup_metrics = build_subgroup_metrics(sample_rows)
    failure_summary = build_failure_stage_summary(sample_rows)
    intervention_summary = build_intervention_summary(
        sample_rows=sample_rows,
        d_activation_ids=d_activation_ids,
        g1_traces=g1_traces,
    )
    write_csv(output_dir / "variant_metrics.csv", variant_metrics, list(variant_metrics[0]))
    write_json(output_dir / "primary_paired_analysis.json", primary)
    write_csv(output_dir / "subgroup_metrics.csv", subgroup_metrics, list(subgroup_metrics[0]))
    write_csv(output_dir / "failure_stage_summary.csv", failure_summary, list(failure_summary[0]))
    write_csv(output_dir / "intervention_summary.csv", intervention_summary, list(intervention_summary[0]))
    sample_fields = [
        "sample_id",
        "source_group",
        "db_id",
        "input_type",
        "operation_type",
        "dependency_sensitive",
        "method",
        "method_slug",
        "target_state_correct",
        "strict_full_state_correct",
        "accepted",
        "execution_success",
        "first_failure_stage",
        "false_accept",
        "constraint_failure",
        "off_target_state_change",
        "generation_status",
        "input_truncated",
        "hit_max_new_tokens",
        "error_type",
        "D_activated",
        "G1_repair_attempted",
        "G1_repair_applied",
        "G1_repair_succeeded",
        "G1_repair_rules",
    ]
    write_csv(output_dir / "sample_level_analysis.csv", sample_rows, sample_fields)
    return {
        "status": "PASS",
        "frozen_sample_count": len(frozen_ids),
        "methods_analyzed": list(METHOD_SLUGS),
        "artifacts": list(ANALYSIS_ARTIFACTS),
        "primary": primary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", default="stage4_fresh_7b_protocol")
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary = analyze_result_root(
        Path(args.protocol_root).resolve(),
        Path(args.result_root).resolve(),
        Path(args.output_dir).resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
