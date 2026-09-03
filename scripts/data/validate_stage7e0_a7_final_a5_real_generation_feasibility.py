#!/usr/bin/env python3
"""Validate Stage7E0-A7 final A5 feasibility freeze and optional results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.data.build_stage7e0_a7_final_a5_real_generation_feasibility import (  # noqa: E402
    EXPECTED_PRIMARY_COUNT,
    EXPECTED_BRANCH_NAME,
    PRIMARY_RESULT_DIR_NAME,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    canonical_json,
    sha256_file,
    sha256_text,
)
from scripts.server.run_stage7e0_a7_english import load_stage7e0_a7_rows  # noqa: E402


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_stage(stage_dir: Path, failures: list[str]) -> dict[str, Any]:
    for rel in SCIENTIFIC_ARTIFACTS:
        if not (stage_dir / rel).is_file():
            failures.append(f"missing_required_file:{rel}")
    if failures:
        return {}
    freeze = read_json(stage_dir / "A7_PROTOCOL_FREEZE.json")
    gate = read_json(stage_dir / "A7_GATE.json")
    manifest = read_json(stage_dir / "A7_PRIMARY_12_MANIFEST.json")
    independence = read_json(stage_dir / "audits" / "data_independence_audit.json")
    leakage = read_json(stage_dir / "audits" / "gold_leakage_audit.json")
    model_calls = read_json(stage_dir / "audits" / "model_call_audit.json")
    retries = read_json(stage_dir / "audits" / "retry_audit.json")
    denominator = read_json(stage_dir / "audits" / "denominator_audit.json")
    candidate_spec = read_json(stage_dir / "protocol" / "candidate_domain_spec.json")
    mock_summary = read_json(stage_dir / "mock_dry_run" / "results" / "summary.json")
    rows = read_jsonl(stage_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")
    if freeze.get("status") != "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN":
        failures.append("protocol_not_frozen_for_server_run")
    if freeze.get("phase_m_removed") is not True:
        failures.append("phase_m_not_removed")
    if freeze.get("branch_name") != EXPECTED_BRANCH_NAME:
        failures.append("branch_name_mismatch")
    if gate.get("primary_n") != EXPECTED_PRIMARY_COUNT or gate.get("required_target_state_correct") != EXPECTED_PRIMARY_COUNT:
        failures.append("gate_primary_count_mismatch")
    if gate.get("allowed_retries") != 0 or gate.get("model_calls_per_sample") != 1:
        failures.append("gate_retry_or_call_count_mismatch")
    if len(rows) != EXPECTED_PRIMARY_COUNT or len(manifest.get("samples", [])) != EXPECTED_PRIMARY_COUNT:
        failures.append("a7_primary_count_mismatch")
    if len({row["sample_id"] for row in rows}) != EXPECTED_PRIMARY_COUNT:
        failures.append("a7_duplicate_sample_id")
    if independence.get("status") != "PASS":
        failures.append("data_independence_not_pass")
    if leakage.get("status") != "PASS" or leakage.get("prompt_hash_invariant_under_gold_mutation") is not True:
        failures.append("gold_leakage_not_pass")
    if model_calls.get("model_calls_per_sample") != 1 or model_calls.get("phase_m_invocations") != 0:
        failures.append("model_call_audit_mismatch")
    if retries.get("retry_implemented") is not False or retries.get("allowed_retries") != 0:
        failures.append("retry_audit_mismatch")
    if denominator.get("silent_skip_count") != 0 or denominator.get("frozen_primary_n") != EXPECTED_PRIMARY_COUNT:
        failures.append("denominator_audit_mismatch")
    if candidate_spec.get("source_patch") != "PATCH1":
        failures.append("candidate_domain_not_a5_patch1")
    if candidate_spec.get("required_columns_forbid_omit_in_schema") is not True:
        failures.append("candidate_domain_required_omit_not_forbidden")
    if mock_summary.get("target_state_accuracy") != "12/12" or mock_summary.get("model_called") is not False:
        failures.append("mock_dry_run_not_clean_12_of_12")
    manifest_payload = read_json(stage_dir / "MANIFEST.json")
    artifacts = manifest_payload.get("artifacts", [])
    if manifest_payload.get("combined_sha256") != sha256_text(canonical_json(artifacts)):
        failures.append("manifest_combined_hash_mismatch")
    for item in artifacts:
        rel = item["path"]
        path = stage_dir / rel
        if not path.is_file():
            failures.append(f"manifest_missing:{rel}")
        elif sha256_file(path) != item.get("sha256"):
            failures.append(f"manifest_hash_mismatch:{rel}")
    return {
        "freeze": freeze,
        "gate": gate,
        "independence": independence,
        "leakage": leakage,
        "mock_summary": mock_summary,
        "rows": rows,
    }


def validate_result(result_dir: Path, failures: list[str]) -> dict[str, Any]:
    required = [
        "run_manifest.json",
        "raw/model_outputs.jsonl",
        "raw/candidate_domains.jsonl",
        "raw/prompts_or_prompt_hashes.jsonl",
        "results/per_sample_results.jsonl",
        "results/summary.json",
        "results/summary.md",
        "results/failure_analysis.json",
        "runtime/environment.json",
        "runtime/token_usage.jsonl",
        "runtime/latency.jsonl",
        "audits/model_call_audit.json",
        "audits/retry_audit.json",
        "audits/denominator_audit.json",
    ]
    for rel in required:
        if not (result_dir / rel).is_file():
            failures.append(f"missing_result_file:{rel}")
    if any(failure.startswith("missing_result_file:") for failure in failures):
        return {}
    summary = read_json(result_dir / "results" / "summary.json")
    cases = read_jsonl(result_dir / "results" / "per_sample_results.jsonl")
    raw = read_jsonl(result_dir / "raw" / "model_outputs.jsonl")
    call_audit = read_json(result_dir / "audits" / "model_call_audit.json")
    retry_audit = read_json(result_dir / "audits" / "retry_audit.json")
    denominator = read_json(result_dir / "audits" / "denominator_audit.json")
    frozen_ids = [row["sample_id"] for row in load_stage7e0_a7_rows(PROJECT_ROOT)]
    if [row.get("sample_id") for row in cases] != frozen_ids:
        failures.append("result_denominator_order_or_ids_mismatch")
    if [row.get("sample_id") for row in raw] != frozen_ids:
        failures.append("raw_denominator_order_or_ids_mismatch")
    if len(cases) != EXPECTED_PRIMARY_COUNT or len(raw) != EXPECTED_PRIMARY_COUNT:
        failures.append("result_primary_count_mismatch")
    if summary.get("target_state_accuracy") != "12/12" or summary.get("target_state_correct_count") != EXPECTED_PRIMARY_COUNT:
        failures.append("official_target_state_gate_not_12_of_12")
    if summary.get("status") != "PASS":
        failures.append("official_summary_not_pass")
    if summary.get("model_calls_per_sample") != 1 or call_audit.get("model_calls_per_sample") != 1:
        failures.append("official_model_call_count_mismatch")
    if summary.get("phase_m_invocations") != 0 or call_audit.get("phase_m_invocations") != 0:
        failures.append("official_phase_m_invocation")
    if summary.get("retry_count") != 0 or retry_audit.get("retry_count") != 0:
        failures.append("official_retry_count_nonzero")
    if denominator.get("silent_skip_count") != 0 or denominator.get("observed_primary_n") != EXPECTED_PRIMARY_COUNT:
        failures.append("official_denominator_audit_mismatch")
    if any(row.get("failure_code") == "unknown" for row in cases):
        failures.append("official_unknown_failure_code")
    return summary


def bundled_official_result_dir(stage_dir: Path) -> Path | None:
    result_dir = stage_dir / "official_results" / "extracted" / PRIMARY_RESULT_DIR_NAME
    return result_dir if result_dir.is_dir() else None


def validate(stage_dir: Path, result_dir: Path | None = None, *, validate_bundled_official_result: bool = True) -> dict[str, Any]:
    failures: list[str] = []
    stage_payload = validate_stage(stage_dir, failures)
    effective_result_dir = result_dir
    if effective_result_dir is None and validate_bundled_official_result:
        effective_result_dir = bundled_official_result_dir(stage_dir)
    result_summary = validate_result(effective_result_dir, failures) if effective_result_dir else None
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "freeze_status": (stage_payload.get("freeze") or {}).get("status"),
        "fresh_primary_count": len(stage_payload.get("rows") or []),
        "data_independence": (stage_payload.get("independence") or {}).get("status"),
        "gold_leakage": (stage_payload.get("leakage") or {}).get("status"),
        "mock_target_state_accuracy": (stage_payload.get("mock_summary") or {}).get("target_state_accuracy"),
        "official_target_state_accuracy": (result_summary or {}).get("target_state_accuracy") if result_summary else None,
        "official_generation_validated": effective_result_dir is not None and not failures,
        "official_result_dir": str(effective_result_dir) if effective_result_dir else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--result-dir", type=Path)
    parser.add_argument("--skip-bundled-official-result", action="store_true")
    args = parser.parse_args()
    report = validate(args.stage_dir, args.result_dir, validate_bundled_official_result=not args.skip_bundled_official_result)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
