#!/usr/bin/env python3
"""Validate Stage-3 causal replay artifacts without rerunning SQLite replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


VARIANTS = [f"V{index}" for index in range(9)]
COMPONENTS = ["A", "B", "C", "D", "E", "F", "G1", "G2"]
EXPECTED_G2_COMMIT = "b752867312727e9932dcf48af99c02b4b2af36cf"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_results(root: Path) -> dict[str, Any]:
    required = [
        "results/causal_replay_sample_level.csv",
        "results/variant_metrics.csv",
        "results/rescue_regression_matrix.csv",
        "results/failure_stage_transitions.csv",
        "results/failure_taxonomy_V0_V8.csv",
        "results/intervention_activation_summary.csv",
        "results/repair_rule_summary.csv",
        "traces/A_to_G2_intervention_traces.jsonl",
        "validation/replay_invariants.json",
        "validation/evaluator_checks.json",
        "provenance/run_lock.json",
        "provenance/run_manifest.json",
    ]
    required.extend(f"configs/{variant}_" for variant in VARIANTS)
    violations: list[dict[str, Any]] = []
    for relative in required[:12]:
        if not (root / relative).is_file():
            violations.append({"rule": "required_file", "path": relative})
    for prefix in required[12:]:
        if len(list((root / "configs").glob(f"{Path(prefix).name}*.json"))) != 1:
            violations.append({"rule": "variant_config", "prefix": prefix})

    run_lock = load_json(root / "provenance" / "run_lock.json")
    if run_lock.get("sample_count") != 300:
        violations.append({"rule": "run_lock_sample_count", "actual": run_lock.get("sample_count")})
    if run_lock.get("variant_order") != VARIANTS:
        violations.append({"rule": "run_lock_variant_order"})
    if run_lock.get("component_order") != COMPONENTS:
        violations.append({"rule": "run_lock_component_order"})
    if run_lock.get("model_called") is not False or run_lock.get("gpu_required") is not False:
        violations.append({"rule": "cpu_only_no_model"})
    if run_lock.get("frozen_g2_commit") != EXPECTED_G2_COMMIT:
        violations.append({"rule": "frozen_g2_commit"})
    if (run_lock.get("baseline_equivalence") or {}).get("mismatches") != 0:
        violations.append({"rule": "v0_baseline_equivalence"})

    manifest = load_json(root / "provenance" / "run_manifest.json")
    for relative, metadata in (manifest.get("files") or {}).items():
        path = root / relative
        if not path.is_file():
            violations.append({"rule": "manifest_missing", "path": relative})
            continue
        actual = sha256_file(path)
        if actual != metadata.get("sha256"):
            violations.append({"rule": "manifest_hash", "path": relative})
        if path.stat().st_size != metadata.get("size_bytes"):
            violations.append({"rule": "manifest_size", "path": relative})

    sample_rows = read_csv(root / "results" / "causal_replay_sample_level.csv")
    sample_ids = [row.get("sample_id", "") for row in sample_rows]
    if len(sample_rows) != 300 or len(set(sample_ids)) != 300:
        violations.append({"rule": "sample_rows", "rows": len(sample_rows), "unique": len(set(sample_ids))})
    for variant in VARIANTS:
        for suffix in ("correct", "strict_correct", "first_failure"):
            column = f"{variant}_{suffix}"
            if sample_rows and column not in sample_rows[0]:
                violations.append({"rule": "sample_column", "column": column})
    for component in COMPONENTS:
        column = f"{component}_activated"
        if sample_rows and column not in sample_rows[0]:
            violations.append({"rule": "activation_column", "column": column})

    metrics = {row["variant"]: row for row in read_csv(root / "results" / "variant_metrics.csv")}
    if set(metrics) != set(VARIANTS):
        violations.append({"rule": "metric_variants"})
    for variant in VARIANTS:
        correct = sum(int(row[f"{variant}_correct"]) for row in sample_rows)
        strict = sum(int(row[f"{variant}_strict_correct"]) for row in sample_rows)
        if int(metrics[variant]["target_state_correct"]) != correct:
            violations.append({"rule": "metric_target_correct", "variant": variant})
        if int(metrics[variant]["strict_full_state_correct"]) != strict:
            violations.append({"rule": "metric_strict_correct", "variant": variant})

    rescue_rows = read_csv(root / "results" / "rescue_regression_matrix.csv")
    if [row["intervention"] for row in rescue_rows] != COMPONENTS:
        violations.append({"rule": "rescue_component_order"})
    for index, row in enumerate(rescue_rows, start=1):
        previous, current = VARIANTS[index - 1], VARIANTS[index]
        rescued = sum(
            int(sample[f"{previous}_correct"]) == 0
            and int(sample[f"{current}_correct"]) == 1
            for sample in sample_rows
        )
        regressed = sum(
            int(sample[f"{previous}_correct"]) == 1
            and int(sample[f"{current}_correct"]) == 0
            for sample in sample_rows
        )
        if int(row["rescued"]) != rescued or int(row["regressed"]) != regressed:
            violations.append({"rule": "rescue_recompute", "intervention": row["intervention"]})
        if int(row["net_gain"]) != rescued - regressed:
            violations.append({"rule": "net_gain", "intervention": row["intervention"]})

    trace_path = root / "traces" / "A_to_G2_intervention_traces.jsonl"
    with trace_path.open(encoding="utf-8") as handle:
        traces = [json.loads(line) for line in handle if line.strip()]
    trace_ids = [str(row.get("sample_id") or "") for row in traces]
    if len(traces) != 300 or set(trace_ids) != set(sample_ids):
        violations.append({"rule": "trace_identity", "rows": len(traces), "unique": len(set(trace_ids))})

    invariants = load_json(root / "validation" / "replay_invariants.json")
    evaluator = load_json(root / "validation" / "evaluator_checks.json")
    if invariants.get("status") != "PASS" or invariants.get("violations"):
        violations.append({"rule": "replay_invariants"})
    if evaluator.get("status") != "PASS" or evaluator.get("v0_frozen_equivalence_mismatches") != 0:
        violations.append({"rule": "evaluator_checks"})
    report = {
        "status": "PASS" if not violations else "FAIL",
        "sample_rows": len(sample_rows),
        "trace_rows": len(traces),
        "variants": len(metrics),
        "manifest_entries_verified": len(manifest.get("files") or {}),
        "violations": violations,
    }
    if violations:
        raise ValueError(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True)
    args = parser.parse_args()
    report = validate_results(Path(args.results_root).resolve())
    print("STAGE3_VALIDATION: PASS")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
