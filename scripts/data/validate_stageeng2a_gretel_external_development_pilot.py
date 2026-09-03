#!/usr/bin/env python3
"""Validate Stage ENG2A Gretel external development-pilot package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng2a_gretel_external_development_pilot import (  # noqa: E402
    EXPECTED_PILOT_N,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    sha256_file,
)


METHODS = {"M0_DIRECT_SQL", "M1_J_FS", "M2_FROZEN_A7"}
FORBIDDEN_MODEL_KEYS = {"gold_sql", "gold_assignments", "gold_post_state", "target_state", "evaluator_side_expected", "label_side_expected"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_stage(stage_dir: Path, *, require_mock: bool = True) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for rel in SCIENTIFIC_ARTIFACTS:
        if not (stage_dir / rel).is_file():
            failures.append({"rule": "missing_artifact", "path": rel})
    rows = read_jsonl(stage_dir / "ENG2A_PILOT_100_FREEZE.jsonl")
    manifest = read_jsonl(stage_dir / "ENG2A_PILOT_100_MANIFEST.jsonl")
    if len(rows) != EXPECTED_PILOT_N:
        failures.append({"rule": "frozen_row_count", "observed": len(rows), "expected": EXPECTED_PILOT_N})
    if len(manifest) != EXPECTED_PILOT_N:
        failures.append({"rule": "manifest_row_count", "observed": len(manifest), "expected": EXPECTED_PILOT_N})
    if [row.get("sample_id") for row in rows] != [row.get("sample_id") for row in manifest]:
        failures.append({"rule": "freeze_manifest_order_or_ids"})
    for row in rows:
        model_keys = set((row.get("model_side_input") or {}).keys())
        if model_keys != {"question", "schema_inventory", "candidate_inventory_text"}:
            failures.append({"rule": "model_side_input_keys", "sample_id": row.get("sample_id"), "keys": sorted(model_keys)})
        forbidden = FORBIDDEN_MODEL_KEYS & model_keys
        if forbidden:
            failures.append({"rule": "model_side_gold_leakage", "sample_id": row.get("sample_id"), "keys": sorted(forbidden)})
        if row.get("gretel_source", {}).get("source_split") != "train":
            failures.append({"rule": "pilot_source_split", "sample_id": row.get("sample_id")})
        if row.get("runtime_constraints", {}).get("retry") != 0:
            failures.append({"rule": "retry_not_zero", "sample_id": row.get("sample_id")})
        if row.get("runtime_constraints", {}).get("phase_m_removed") is not True:
            failures.append({"rule": "phase_m_not_removed", "sample_id": row.get("sample_id")})
        db_rel = row.get("synthetic_db_spec", {}).get("sqlite_db_path")
        if not db_rel or not (stage_dir / db_rel).is_file():
            failures.append({"rule": "missing_sqlite_db", "sample_id": row.get("sample_id"), "path": db_rel})
    isolation = read_json(stage_dir / "audits" / "official_test_isolation_audit.json")
    if isolation.get("official_test_overlap") != 0 or isolation.get("development_dev_overlap") != 0:
        failures.append({"rule": "official_or_dev_overlap", "audit": isolation})
    protocol = read_json(stage_dir / "ENG2A_PROTOCOL_FREEZE.json")
    method_ids = {item.get("method_id") for item in protocol.get("methods", [])}
    if method_ids != METHODS:
        failures.append({"rule": "method_set", "observed": sorted(method_ids), "expected": sorted(METHODS)})
    for method_id in METHODS:
        prompt_path = stage_dir / "prompts" / {
            "M0_DIRECT_SQL": "M0_DIRECT_SQL_PROMPTS.jsonl",
            "M1_J_FS": "M1_J_FS_PROMPTS.jsonl",
            "M2_FROZEN_A7": "M2_FROZEN_A7_PROMPTS.jsonl",
        }[method_id]
        prompt_rows = read_jsonl(prompt_path)
        if len(prompt_rows) != EXPECTED_PILOT_N:
            failures.append({"rule": "prompt_row_count", "method_id": method_id, "observed": len(prompt_rows)})
    if require_mock:
        summary = read_json(stage_dir / "mock_dry_run" / "results" / "summary.json")
        if set(summary.get("methods", {})) != METHODS:
            failures.append({"rule": "mock_method_set", "observed": sorted(summary.get("methods", {}))})
        for method_id, item in summary.get("methods", {}).items():
            if item.get("samples") != EXPECTED_PILOT_N:
                failures.append({"rule": "mock_denominator", "method_id": method_id, "observed": item.get("samples")})
        raw = read_jsonl(stage_dir / "mock_dry_run" / "raw" / "model_outputs.jsonl")
        results = read_jsonl(stage_dir / "mock_dry_run" / "results" / "per_sample_results.jsonl")
        if len(raw) != EXPECTED_PILOT_N * len(METHODS) or len(results) != EXPECTED_PILOT_N * len(METHODS):
            failures.append({"rule": "mock_result_rows", "raw": len(raw), "results": len(results)})
    manifest_file = read_json(stage_dir / "MANIFEST.json")
    manifest_hashes = {row["path"]: row["sha256"] for row in manifest_file.get("files", [])}
    for rel, expected in manifest_hashes.items():
        path = stage_dir / rel
        if path.is_file() and sha256_file(path) != expected:
            failures.append({"rule": "manifest_hash_mismatch", "path": rel})
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "pilot_n": len(rows),
        "methods": sorted(METHODS),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--skip-mock", action="store_true")
    args = parser.parse_args()
    result = validate_stage(args.stage_dir.resolve(), require_mock=not args.skip_mock)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
