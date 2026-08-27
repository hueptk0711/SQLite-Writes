#!/usr/bin/env python3
"""Emit a focused Stage7C dataset split and leakage audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7c_v2_development_data_protocol import (
    STAGE,
    contamination_audit,
    leakage_counts,
    read_json,
    read_jsonl,
    source_split_counts,
)


def audit(output_dir: Path = PROJECT_ROOT / "stage7c_v2_development_data_protocol") -> dict:
    train_rows = read_jsonl(output_dir / "TRAIN_CREATE_MANIFEST.jsonl")
    dev_rows = read_jsonl(output_dir / "DEV_CREATE_MANIFEST.jsonl")
    source_manifest = read_json(output_dir / "CRUDSQL_SOURCE_MANIFEST.json")
    contamination = contamination_audit(train_rows, dev_rows)
    leaks = leakage_counts(train_rows + dev_rows)
    slot_audit = read_json(output_dir / "SEMANTIC_SLOT_DERIVATION_AUDIT.json")
    gold_audit = read_json(output_dir / "GOLD_PROGRAM_DERIVATION_AUDIT.json")
    return {
        "stage": STAGE,
        "status": "PASS" if not leaks and contamination["status"] == "PASS" else "FAIL",
        "source_commit": source_manifest["source"]["commit"],
        "included_splits": source_manifest["included_splits"],
        "excluded_splits": source_manifest["excluded_splits"],
        "source_split_counts": {"train": source_split_counts(output_dir, "train"), "dev": source_split_counts(output_dir, "dev")},
        "manifest_counts": {"train_create": len(train_rows), "dev_create": len(dev_rows)},
        "semantic_slot_derivation": {
            "train_candidate_exact_cardinality_match": slot_audit["train"]["candidate_exact_cardinality_match"],
            "train_candidate_gold_value_coverage_rate": slot_audit["train"]["candidate_gold_value_coverage_rate"],
            "train_spurious_required_slot_rate": slot_audit["train"]["spurious_required_slot_rate"],
            "train_required_slot_count": slot_audit["train"]["required_slot_count"],
            "train_optional_slot_count": slot_audit["train"]["optional_slot_count"],
            "dev_candidate_exact_cardinality_match": slot_audit["dev"]["candidate_exact_cardinality_match"],
            "dev_candidate_gold_value_coverage_rate": slot_audit["dev"]["candidate_gold_value_coverage_rate"],
            "dev_spurious_required_slot_rate": slot_audit["dev"]["spurious_required_slot_rate"],
            "dev_required_slot_count": slot_audit["dev"]["required_slot_count"],
            "dev_optional_slot_count": slot_audit["dev"]["optional_slot_count"],
            "quality_acceptance_gate": slot_audit["quality_acceptance_gate"],
            "gold_used_for_model_side_inventory": slot_audit["gold_used_for_model_side_inventory"],
        },
        "gold_program_derivation": {
            "status": gold_audit["status"],
            "train_pass": gold_audit["splits"]["train"]["gold_derivation_pass_count"],
            "dev_pass": gold_audit["splits"]["dev"]["gold_derivation_pass_count"],
            "train_execution_failures": gold_audit["splits"]["train"]["gold_execution_failure_count"],
            "dev_execution_failures": gold_audit["splits"]["dev"]["gold_execution_failure_count"],
        },
        "contamination": contamination,
        "model_input_leakage_counts": dict(leaks),
        "model_called": False,
        "gpu_called": False,
        "v2_implemented": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7c_v2_development_data_protocol")
    args = parser.parse_args()
    print(json.dumps(audit(args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
