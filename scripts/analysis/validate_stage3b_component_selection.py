#!/usr/bin/env python3
"""Validate frozen Stage-3B prompt-audit and candidate-selection artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.run_stage3_causal_replay import sha256_file


CANDIDATES = ("FULL", "NO_C", "D_ONLY", "D_G1")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_results(root: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    matrix = read_csv(root / "results" / "prompt_equivalence_matrix.csv")
    summary = read_csv(root / "results" / "prompt_surface_summary.csv")
    sample = read_csv(root / "results" / "candidate_sample_level.csv")
    metrics = read_csv(root / "results" / "candidate_metrics.csv")
    comparisons = read_csv(root / "results" / "candidate_rescue_regression.csv")
    invariants = json.loads((root / "validation" / "stage3b_invariants.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "provenance" / "run_manifest.json").read_text(encoding="utf-8"))
    run_lock = json.loads((root / "provenance" / "run_lock.json").read_text(encoding="utf-8"))

    if len(matrix) != 300 or len(sample) != 300:
        violations.append({"rule": "sample_count", "matrix": len(matrix), "candidate": len(sample)})
    if len(summary) != 24:
        violations.append({"rule": "prompt_summary_rows", "actual": len(summary)})
    if [row["candidate"] for row in metrics] != list(CANDIDATES):
        violations.append({"rule": "candidate_order_metrics"})
    if [row["candidate"] for row in comparisons] != list(CANDIDATES):
        violations.append({"rule": "candidate_order_comparisons"})
    if invariants.get("status") != "PASS" or invariants.get("violations"):
        violations.append({"rule": "run_invariants"})
    if run_lock.get("model_called") is not False or run_lock.get("gpu_required") is not False:
        violations.append({"rule": "cpu_only_no_model"})
    if run_lock.get("candidate_order") != list(CANDIDATES):
        violations.append({"rule": "run_lock_candidate_order"})
    if int(run_lock.get("full_v8_equivalence_mismatches", -1)) != 0:
        violations.append({"rule": "full_v8_equivalence"})

    by_candidate = {row["candidate"]: row for row in metrics}
    for candidate in CANDIDATES:
        correct = sum(int(row[f"{candidate}_correct"]) for row in sample)
        accepted = sum(int(row[f"{candidate}_accepted"]) for row in sample)
        false_accept = sum(int(row[f"{candidate}_false_accept"]) for row in sample)
        metric = by_candidate[candidate]
        if correct != int(metric["target_state_correct"]):
            violations.append({"rule": "metric_correct", "candidate": candidate})
        if accepted != int(metric["accepted_output"]):
            violations.append({"rule": "metric_accepted", "candidate": candidate})
        if false_accept != int(metric["false_accept"]):
            violations.append({"rule": "metric_false_accept", "candidate": candidate})

    all_summary = [row for row in summary if row["input_type"] == "ALL"]
    for row in all_summary:
        if int(row["same_prompt"]) + int(row["changed_prompt"]) != 300:
            violations.append({"rule": "prompt_summary_partition", "pair": f"{row['from_variant']}->{row['to_variant']}"})
    for relative, metadata in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != metadata.get("sha256"):
            violations.append({"rule": "manifest_hash", "path": relative})

    report = {
        "status": "PASS" if not violations else "FAIL",
        "sample_rows": len(sample),
        "prompt_rows": len(matrix),
        "prompt_summary_rows": len(summary),
        "candidates": len(metrics),
        "manifest_entries_verified": len(manifest.get("files", {})),
        "violations": violations,
    }
    if violations:
        raise ValueError(f"Stage 3B output validation failed: {violations[:5]}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True)
    args = parser.parse_args()
    print(json.dumps(validate_results(Path(args.results_root).resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
