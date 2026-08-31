#!/usr/bin/env python3
"""Validate imported Stage7E0-A3 real server result evidence."""

from __future__ import annotations

import argparse
import hashlib
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

from scripts.data.import_stage7e0_a3_server_results import (  # noqa: E402
    RESULT_DIR_NAME,
    SERVER_RUN_ID,
    SERVER_TAR_NAME,
    STAGE_NAME,
)


REQUIRED_FILES = {
    "SERVER_RESULT_IMPORT_REPORT.json",
    "SERVER_RESULT_FAILURE_ANALYSIS.md",
    "VALIDATION_REPORT_PATCH1.md",
    "INVALID_RUN_001_CLASSIFICATION.json",
    "STAGE7E0_A3_SERVER_RESULT_LOCK.json",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/primary_summary.json",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/primary_case_results.jsonl",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/raw_phase_o_generations.jsonl",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/raw_phase_m_generations.jsonl",
    f"{SERVER_RUN_ID}/{RESULT_DIR_NAME}/run_manifest.json",
}


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(stage_dir: Path, tar_path: Path | None = None) -> dict[str, Any]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_FILES):
        if not (stage_dir / rel).is_file():
            failures.append(f"missing required artifact: {rel}")
    report = read_json(stage_dir / "SERVER_RESULT_IMPORT_REPORT.json")
    lock = read_json(stage_dir / "STAGE7E0_A3_SERVER_RESULT_LOCK.json")
    invalid_run = read_json(stage_dir / "INVALID_RUN_001_CLASSIFICATION.json")
    extracted = stage_dir / SERVER_RUN_ID / RESULT_DIR_NAME
    summary = read_json(extracted / "primary_summary.json")
    manifest = read_json(extracted / "run_manifest.json")
    cases = read_jsonl(extracted / "primary_case_results.jsonl")
    raw_o = read_jsonl(extracted / "raw_phase_o_generations.jsonl")
    raw_m = read_jsonl(extracted / "raw_phase_m_generations.jsonl")

    if tar_path is None:
        candidate = PROJECT_ROOT / SERVER_TAR_NAME
        tar_path = candidate if candidate.is_file() else None
    if tar_path is not None:
        if report["source_tar"]["sha256"] != sha256_file(tar_path):
            failures.append("source tar sha256 mismatch")

    if report.get("server_run_id") != SERVER_RUN_ID:
        failures.append("server_run_id drifted")
    if summary.get("backend") != "hf":
        failures.append("imported server summary must record the actual plain backend=hf")
    if summary.get("model_called") is not True or summary.get("gpu_called") is not True:
        failures.append("real server result must record model_called/gpu_called true")
    if summary.get("status") not in {"PASS", "FAIL"}:
        failures.append("server summary status must be a concrete observed execution status")
    if summary.get("required_pass_count") != "8/8":
        failures.append("required pass count must remain 8/8")
    if summary.get("seven_of_eight_allowed") is not False:
        failures.append("7/8 acceptance must remain forbidden")
    if summary.get("diagnostics_run") is not False:
        failures.append("diagnostics must not run after primary failure")
    if summary.get("gretel_pilot_opened") is not False:
        failures.append("Gretel pilot must remain unopened")
    if manifest.get("model", {}).get("cuda_available") is not True:
        failures.append("server run manifest must show cuda_available=true")
    if manifest.get("model", {}).get("gpu") != "NVIDIA GeForce RTX 4090":
        failures.append("server GPU identity drifted")
    if manifest.get("phase_o_prompt_spec_path") != "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json":
        failures.append("server did not use exact A3 Phase O prompt path")
    if manifest.get("retry") != 0 or manifest.get("repair") != "none":
        failures.append("retry/repair contract drifted")

    if len(cases) != 8:
        failures.append("primary case results must have 8 rows")
    if len(raw_o) != 8:
        failures.append("Phase O raw generations must have 8 rows")
    if len(raw_m) > 8:
        failures.append("Phase M raw generations cannot exceed the 8 primary cases")
    failure_counts: dict[str, int] = {}
    for row in cases:
        key = str(row.get("failure_stage"))
        failure_counts[key] = failure_counts.get(key, 0) + 1
    observed_pass_count = sum(1 for row in cases if row.get("status") == "PASS")
    if summary.get("primary_pass_count") != f"{observed_pass_count}/8":
        failures.append("server primary pass count must match observed case rows")

    for rel, expected in report.get("server_result_files", {}).items():
        path = extracted / rel
        if not path.is_file():
            failures.append(f"reported server result file missing: {rel}")
        elif sha256_file(path) != expected:
            failures.append(f"reported server result hash mismatch: {rel}")
    if summary.get("raw_phase_o_sha256") != sha256_file(extracted / "raw_phase_o_generations.jsonl"):
        failures.append("primary summary raw Phase O hash mismatch")
    if summary.get("raw_phase_m_sha256") != sha256_file(extracted / "raw_phase_m_generations.jsonl"):
        failures.append("primary summary raw Phase M hash mismatch")
    if summary.get("primary_case_results_sha256") != sha256_file(extracted / "primary_case_results.jsonl"):
        failures.append("primary summary case-results hash mismatch")

    if lock.get("status") != "INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL":
        failures.append("server result lock status drifted")
    if lock.get("evidence_integrity_status") != "PASS":
        failures.append("invalid run evidence integrity must PASS")
    if lock.get("protocol_compliance_status") != "FAIL":
        failures.append("invalid run protocol compliance must FAIL")
    if lock.get("primary_gate_status") != "INVALID_NOT_EVALUATED":
        failures.append("invalid run primary gate must be invalid/not evaluated")
    if lock.get("scientific_result_eligible") is not False:
        failures.append("invalid run must not be scientifically eligible")
    if lock.get("gretel_pilot_opened") is not False:
        failures.append("server result lock must keep Gretel closed")
    if lock.get("server_result_import_report_sha256") != sha256_file(stage_dir / "SERVER_RESULT_IMPORT_REPORT.json"):
        failures.append("server result lock import-report hash mismatch")
    if invalid_run.get("reason") != "backend_protocol_violation":
        failures.append("invalid run classification reason drifted")
    if invalid_run.get("actual_backend") != "plain_hf_unconstrained":
        failures.append("invalid run actual backend label drifted")
    if invalid_run.get("required_backend") != "patch9_incremental_json_schema_grammar":
        failures.append("invalid run required backend label drifted")
    if invalid_run.get("scientific_result_eligible") is not False:
        failures.append("invalid run classification must mark scientific_result_eligible=false")
    if invalid_run.get("primary_gate_status") != "INVALID_NOT_EVALUATED":
        failures.append("invalid run classification must mark primary gate invalid")

    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "evidence_integrity_status": lock.get("evidence_integrity_status"),
        "protocol_compliance_status": lock.get("protocol_compliance_status"),
        "primary_gate_status": lock.get("primary_gate_status"),
        "scientific_result_eligible": lock.get("scientific_result_eligible"),
        "primary_pass_count": summary.get("primary_pass_count"),
        "required_pass_count": summary.get("required_pass_count"),
        "model_called": summary.get("model_called"),
        "gpu_called": summary.get("gpu_called"),
        "gretel_pilot_opened": summary.get("gretel_pilot_opened"),
        "diagnostics_run": summary.get("diagnostics_run"),
        "failure_stage_counts": failure_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--server-results-tar", type=Path)
    args = parser.parse_args()
    report = validate(args.stage_dir, args.server_results_tar)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
