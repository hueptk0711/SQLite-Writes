from __future__ import annotations

import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, iter_jsonl, load_json


def exact_mcnemar(left: list[bool], right: list[bool]) -> dict[str, Any]:
    if len(left) != len(right):
        raise ValueError("McNemar inputs must contain the same number of samples")
    left_only = sum(a and not b for a, b in zip(left, right))
    right_only = sum(b and not a for a, b in zip(left, right))
    discordant = left_only + right_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, index)
            for index in range(min(left_only, right_only) + 1)
        ) / (2**discordant)
        p_value = min(1.0, 2.0 * tail)
    return {
        "left_only_correct": left_only,
        "right_only_correct": right_only,
        "discordant_pairs": discordant,
        "p_value_two_sided_exact": p_value,
    }


def holm_bonferroni(p_values: list[float]) -> list[float]:
    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [1.0] * len(p_values)
    running = 0.0
    count = len(p_values)
    for rank, (original_index, value) in enumerate(indexed):
        candidate = min(1.0, (count - rank) * float(value))
        running = max(running, candidate)
        adjusted[original_index] = running
    return adjusted


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def paired_cluster_bootstrap(
    differences: list[float],
    clusters: list[str],
    *,
    iterations: int = 10_000,
    seed: int = 13,
) -> dict[str, Any]:
    if len(differences) != len(clusters):
        raise ValueError("Bootstrap inputs must contain the same number of samples")
    if iterations < 1:
        raise ValueError("Bootstrap iterations must be positive")
    grouped: dict[str, list[float]] = defaultdict(list)
    for difference, cluster in zip(differences, clusters):
        grouped[str(cluster)].append(float(difference))
    cluster_ids = list(grouped)
    if not cluster_ids:
        return {
            "observed_mean_difference": 0.0,
            "confidence_interval_95": [0.0, 0.0],
            "iterations": iterations,
            "cluster_count": 0,
            "seed": seed,
        }
    generator = random.Random(seed)
    bootstrap_values: list[float] = []
    for _ in range(iterations):
        sampled_values: list[float] = []
        for _ in cluster_ids:
            sampled_values.extend(grouped[generator.choice(cluster_ids)])
        bootstrap_values.append(sum(sampled_values) / len(sampled_values))
    observed = sum(differences) / len(differences) if differences else 0.0
    return {
        "observed_mean_difference": observed,
        "confidence_interval_95": [
            _percentile(bootstrap_values, 0.025),
            _percentile(bootstrap_values, 0.975),
        ],
        "iterations": iterations,
        "cluster_count": len(cluster_ids),
        "seed": seed,
    }


def paired_database_macro_bootstrap(
    differences: list[float],
    database_ids: list[str],
    *,
    iterations: int = 10_000,
    seed: int = 17,
) -> dict[str, Any]:
    """Bootstrap database-level mean differences with equal DB weighting."""
    if len(differences) != len(database_ids):
        raise ValueError(
            "Database bootstrap inputs must contain the same number of samples"
        )
    if iterations < 1:
        raise ValueError("Bootstrap iterations must be positive")
    grouped: dict[str, list[float]] = defaultdict(list)
    for difference, database_id in zip(differences, database_ids):
        grouped[str(database_id)].append(float(difference))
    database_means = {
        database_id: sum(values) / len(values)
        for database_id, values in grouped.items()
    }
    ids = list(database_means)
    if not ids:
        return {
            "observed_database_macro_difference": 0.0,
            "confidence_interval_95": [0.0, 0.0],
            "iterations": iterations,
            "database_count": 0,
            "seed": seed,
        }
    generator = random.Random(seed)
    bootstrap_values = [
        sum(database_means[generator.choice(ids)] for _ in ids) / len(ids)
        for _ in range(iterations)
    ]
    observed = sum(database_means.values()) / len(database_means)
    return {
        "observed_database_macro_difference": observed,
        "confidence_interval_95": [
            _percentile(bootstrap_values, 0.025),
            _percentile(bootstrap_values, 0.975),
        ],
        "iterations": iterations,
        "database_count": len(ids),
        "seed": seed,
    }


def adjust_comparison_family(
    comparisons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply one Holm correction across a pre-registered comparison family."""
    p_values = [
        float(item.get("mcnemar", {}).get("p_value_two_sided_exact", 1.0))
        for item in comparisons
    ]
    adjusted = holm_bonferroni(p_values)
    output: list[dict[str, Any]] = []
    for comparison, adjusted_p in zip(comparisons, adjusted):
        row = dict(comparison)
        row["mcnemar"] = {
            **dict(comparison.get("mcnemar") or {}),
            "p_value_holm_family": adjusted_p,
        }
        output.append(row)
    return output


def _source_group(sample: dict[str, Any]) -> str:
    for key in ("source_group_id", "source_group", "source_id"):
        if sample.get(key) is not None:
            return str(sample[key])
    provenance = sample.get("provenance")
    if isinstance(provenance, dict):
        for key in ("source_group_id", "source_group", "source_id"):
            if provenance.get(key) is not None:
                return str(provenance[key])
    return str(sample.get("id"))


def compare_evaluation_runs(
    left_evaluation: str | Path,
    right_evaluation: str | Path,
    dataset_path: str | Path,
    output_path: str | Path,
    *,
    metric: str = "target_state_correct",
    bootstrap_iterations: int = 10_000,
    seed: int = 13,
) -> dict[str, Any]:
    left = {str(row["sample_id"]): row for row in iter_jsonl(left_evaluation)}
    right = {str(row["sample_id"]): row for row in iter_jsonl(right_evaluation)}
    samples = {str(row["id"]): row for row in load_json(dataset_path)}
    shared = sorted(set(left) & set(right))
    if not shared:
        raise ValueError("The evaluation files have no shared sample_id values")
    left_values = [bool(left[sample_id].get(metric)) for sample_id in shared]
    right_values = [bool(right[sample_id].get(metric)) for sample_id in shared]
    differences = [
        float(right_value) - float(left_value)
        for left_value, right_value in zip(left_values, right_values)
    ]
    clusters = [
        _source_group(samples.get(sample_id, {"id": sample_id}))
        for sample_id in shared
    ]
    database_ids = [
        str(samples.get(sample_id, {}).get("db_id") or "unknown")
        for sample_id in shared
    ]
    mcnemar = exact_mcnemar(left_values, right_values)
    result = {
        "metric": metric,
        "direction": "right_minus_left",
        "paired_samples": len(shared),
        "left_accuracy": sum(left_values) / len(shared),
        "right_accuracy": sum(right_values) / len(shared),
        "mcnemar": mcnemar,
        "paired_cluster_bootstrap": paired_cluster_bootstrap(
            differences,
            clusters,
            iterations=bootstrap_iterations,
            seed=seed,
        ),
        "paired_database_macro_bootstrap": paired_database_macro_bootstrap(
            differences,
            database_ids,
            iterations=bootstrap_iterations,
            seed=seed + 1,
        ),
    }
    dump_json(result, output_path)
    return result
