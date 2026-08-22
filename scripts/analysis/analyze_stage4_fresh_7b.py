#!/usr/bin/env python3
"""Frozen Stage-4 fresh 7B statistical analysis.

This script is intentionally result-agnostic and must be frozen before the
fresh 7B run is inspected.  It computes sample-weighted metrics, paired counts,
McNemar exact p-values, and the predeclared source-group cluster bootstrap for
the primary Original MP-FS+ vs D_G1 comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def correctness(row: Mapping[str, Any]) -> bool:
    for key in (
        "target_state_correct",
        "strict_full_state_correct",
        "correct",
    ):
        if key in row:
            return truthy(row[key])
    raise KeyError("No correctness field found in evaluation row")


def accuracy(rows: Iterable[Mapping[str, Any]]) -> float:
    rows = list(rows)
    return sum(correctness(row) for row in rows) / len(rows) if rows else math.nan


def paired_counts(
    baseline: Mapping[str, bool],
    method: Mapping[str, bool],
) -> dict[str, int]:
    shared = sorted(set(baseline) & set(method))
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
    shared = sorted(set(baseline) & set(method))
    grouped: dict[str, list[str]] = defaultdict(list)
    for sample_id in shared:
        grouped[str(source_groups[sample_id])].append(sample_id)
    groups = sorted(grouped)
    if not groups:
        return {
            "replicates": replicates,
            "seed": seed,
            "cluster_count": 0,
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


def load_source_groups(protocol_root: Path) -> dict[str, str]:
    manifest_path = protocol_root / "prompt_audit" / "prompt_manifest.csv"
    output: dict[str, str] = {}
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("method_slug") == PRIMARY_METHOD:
                output[str(row["sample_id"])] = str(row["source_group"])
    return output


def load_method_correctness(result_root: Path, method_slug: str) -> dict[str, bool]:
    rows = read_jsonl(result_root / "methods" / method_slug / "evaluation.jsonl")
    return {str(row["sample_id"]): correctness(row) for row in rows}


def analyze_result_root(protocol_root: Path, result_root: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_groups = load_source_groups(protocol_root)
    method_correct: dict[str, dict[str, bool]] = {
        slug: load_method_correctness(result_root, slug)
        for slug in METHOD_SLUGS
        if (result_root / "methods" / slug / "evaluation.jsonl").is_file()
    }
    metric_rows = []
    for slug, rows in sorted(method_correct.items()):
        values = list(rows.values())
        metric_rows.append(
            {
                "method_slug": slug,
                "sample_count": len(values),
                "sample_weighted_accuracy": sum(values) / len(values) if values else math.nan,
            }
        )
    write_csv(output_dir / "variant_metrics.csv", metric_rows, list(metric_rows[0]) if metric_rows else ["method_slug"])

    baseline = method_correct[PRIMARY_BASELINE]
    method = method_correct[PRIMARY_METHOD]
    counts = paired_counts(baseline, method)
    bootstrap = cluster_bootstrap_accuracy_difference(
        baseline=baseline,
        method=method,
        source_groups=source_groups,
        replicates=BOOTSTRAP_REPLICATES,
        seed=BOOTSTRAP_SEED,
    )
    primary = {
        "baseline": PRIMARY_BASELINE,
        "method": PRIMARY_METHOD,
        "paired_counts": counts,
        "mcnemar_exact_pvalue": mcnemar_exact_pvalue(
            counts.get("baseline_only_correct", 0),
            counts.get("method_only_correct", 0),
        ),
        "cluster_bootstrap": bootstrap,
    }
    write_json(output_dir / "primary_paired_analysis.json", primary)
    return {"status": "PASS", "primary": primary, "metric_rows": len(metric_rows)}


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
