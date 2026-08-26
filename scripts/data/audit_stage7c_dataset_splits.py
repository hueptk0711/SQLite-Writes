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
    return {
        "stage": STAGE,
        "status": "PASS" if not leaks and contamination["status"] == "PASS" else "FAIL",
        "source_commit": source_manifest["source"]["commit"],
        "included_splits": source_manifest["included_splits"],
        "excluded_splits": source_manifest["excluded_splits"],
        "source_split_counts": {"train": source_split_counts(output_dir, "train"), "dev": source_split_counts(output_dir, "dev")},
        "manifest_counts": {"train_create": len(train_rows), "dev_create": len(dev_rows)},
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
