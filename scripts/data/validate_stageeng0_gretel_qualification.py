#!/usr/bin/env python3
"""Validate StageENG0 Gretel qualification artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng0_gretel_qualification import (
    DATASET_ID,
    DATASET_REVISION,
    RAW_FILES,
    STAGE_NAME,
    sha256_file,
)


REQUIRED_STAGE_FILES = [
    "DATASET_SOURCE_LOCK.json",
    "RAW_DATA_HASHES.json",
    "RAW_SCHEMA_AUDIT.json",
    "ELIGIBILITY_POLICY.json",
    "DML_OPERATION_COUNTS.json",
    "WRITE_COMPLEXITY_AUDIT.json",
    "SQLITE_CONTEXT_AUDIT.jsonl",
    "SQLITE_COMPATIBILITY_SUMMARY.json",
    "GOLD_EXECUTION_AUDIT.jsonl",
    "SOURCE_ALIGNABILITY_AUDIT.jsonl",
    "SOURCE_ALIGNABILITY_SUMMARY.json",
    "EXCLUSION_LEDGER.jsonl",
    "EXCLUSION_REASON_COUNTS.json",
    "DATA_LEAKAGE_AUDIT.json",
    "SPLIT_CANDIDATE_AUDIT.json",
    "ELIGIBLE_INSERT_MANIFEST.jsonl",
    "ELIGIBLE_UPDATE_MANIFEST.jsonl",
    "ELIGIBLE_DELETE_MANIFEST.jsonl",
    "STAGEENG0_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def validate(stage_dir: Path, raw_dir: Path | None = None) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_STAGE_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")

    if failures:
        return {"status": "FAIL", "failures": failures}

    source_lock = read_json(stage_dir / "DATASET_SOURCE_LOCK.json")
    raw_hashes = read_json(stage_dir / "RAW_DATA_HASHES.json")
    schema = read_json(stage_dir / "RAW_SCHEMA_AUDIT.json")
    dml_counts = read_json(stage_dir / "DML_OPERATION_COUNTS.json")
    policy = read_json(stage_dir / "ELIGIBILITY_POLICY.json")
    lock = read_json(stage_dir / "STAGEENG0_LOCK.json")
    context_rows = read_jsonl(stage_dir / "SQLITE_CONTEXT_AUDIT.jsonl")
    gold_rows = read_jsonl(stage_dir / "GOLD_EXECUTION_AUDIT.jsonl")
    align_rows = read_jsonl(stage_dir / "SOURCE_ALIGNABILITY_AUDIT.jsonl")
    ledger_rows = read_jsonl(stage_dir / "EXCLUSION_LEDGER.jsonl")
    manifests = {
        "INSERT": read_jsonl(stage_dir / "ELIGIBLE_INSERT_MANIFEST.jsonl"),
        "UPDATE": read_jsonl(stage_dir / "ELIGIBLE_UPDATE_MANIFEST.jsonl"),
        "DELETE": read_jsonl(stage_dir / "ELIGIBLE_DELETE_MANIFEST.jsonl"),
    }

    if source_lock.get("dataset_id") != DATASET_ID:
        failures.append("dataset_id_mismatch")
    if source_lock.get("revision") != DATASET_REVISION:
        failures.append("dataset_revision_mismatch")
    if lock.get("model_called") is not False or source_lock.get("model_called") is not False:
        failures.append("model_called_not_false")
    if lock.get("gpu_called") is not False or source_lock.get("gpu_called") is not False:
        failures.append("gpu_called_not_false")
    if policy.get("model_outputs_allowed") is not False:
        failures.append("eligibility_policy_allows_model_outputs")

    raw_total = int(dml_counts["raw_total"])
    if sum(schema["split_counts"].values()) != raw_total:
        failures.append("raw_total_does_not_match_split_counts")
    if len(context_rows) != raw_total or len(gold_rows) != raw_total or len(align_rows) != raw_total:
        failures.append("audit_jsonl_rows_do_not_cover_raw_total")

    dml_total = int(dml_counts["dml_total"])
    if len(ledger_rows) != dml_total:
        failures.append("exclusion_ledger_does_not_cover_dml_population")
    ledger_ids = [row["sample_id"] for row in ledger_rows]
    if len(ledger_ids) != len(set(ledger_ids)):
        failures.append("duplicate_sample_id_in_ledger")

    eligible_ledger_ids = {row["sample_id"] for row in ledger_rows if row["status"] == "eligible"}
    excluded_with_no_reason = [
        row["sample_id"]
        for row in ledger_rows
        if row["status"] == "excluded" and not row.get("exclusion_reasons")
    ]
    if excluded_with_no_reason:
        failures.append(f"excluded_without_reason:{len(excluded_with_no_reason)}")

    manifest_ids: list[str] = []
    for operation, rows in manifests.items():
        for row in rows:
            manifest_ids.append(row["sample_id"])
            if row.get("operation") != operation:
                failures.append(f"manifest_operation_mismatch:{row['sample_id']}")
            if not row.get("sqlite_write_eligible"):
                failures.append(f"manifest_contains_ineligible:{row['sample_id']}")
    if len(manifest_ids) != len(set(manifest_ids)):
        failures.append("sample_id_in_multiple_manifests")
    if set(manifest_ids) != eligible_ledger_ids:
        failures.append("manifest_ids_do_not_match_eligible_ledger")

    gold_by_id = {row["sample_id"]: row for row in gold_rows}
    for sample_id in eligible_ledger_ids:
        gold = gold_by_id.get(sample_id)
        if not gold:
            failures.append(f"eligible_missing_gold_audit:{sample_id}")
            continue
        if gold.get("exec_status") != "success" or gold.get("deterministic") is not True:
            failures.append(f"eligible_gold_not_success_deterministic:{sample_id}")
        if not gold.get("before_state_hash") or not gold.get("after_state_hash"):
            failures.append(f"eligible_missing_state_hash:{sample_id}")

    status_counts = Counter(row["status"] for row in ledger_rows)
    if status_counts["eligible"] != len(manifest_ids):
        failures.append("eligible_count_does_not_match_manifest_count")

    if raw_dir is not None:
        for split, filename in RAW_FILES.items():
            path = raw_dir / filename
            if not path.exists():
                failures.append(f"raw_file_missing:{filename}")
                continue
            expected = raw_hashes[split]["sha256"]
            if sha256_file(path) != expected:
                failures.append(f"raw_file_hash_mismatch:{filename}")

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "raw_total": raw_total,
        "dml_total": dml_total,
        "eligible_manifest_counts": {operation: len(rows) for operation, rows in manifests.items()},
        "ledger_status_counts": dict(status_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=Path(STAGE_NAME))
    parser.add_argument("--raw-dir", type=Path)
    args = parser.parse_args()
    result = validate(args.stage_dir, args.raw_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
