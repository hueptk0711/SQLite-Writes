#!/usr/bin/env python3
"""Ingest official Stage ENG2A server results into the reviewer package tree."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng2a_gretel_external_development_pilot import (
    EXPECTED_PILOT_N,
    MODEL_REVISION,
    PATCH_NAME,
    SERVER_ARCHIVE,
    SERVER_RESULT_DIR,
    STAGE_NAME,
    reviewer_readme,
    sha256_file,
    write_json,
    write_package_integrity,
    write_text,
)


METHODS = ("M0_DIRECT_SQL", "M1_J_FS", "M2_FROZEN_A7")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_sha_from_file(path: Path) -> str:
    for token in path.read_text(encoding="utf-8").split():
        if len(token) == 64 and all(char in "0123456789abcdefABCDEF" for char in token):
            return token.lower()
    raise SystemExit(f"STOP: could not read SHA-256 digest from {path}")


def safe_extract_tar(archive_path: Path, extract_dir: Path) -> Path:
    extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            target = (root / member.name).resolve()
            if root != target and root not in target.parents:
                raise SystemExit(f"STOP: tar member escapes extract dir: {member.name}")
        archive.extractall(root)
    extracted_root = root / SERVER_RESULT_DIR
    if not extracted_root.is_dir():
        raise SystemExit(f"STOP: expected result root missing from tar: {SERVER_RESULT_DIR}")
    return extracted_root


def validate_official_result_tree(result_dir: Path) -> dict[str, Any]:
    summary = read_json(result_dir / "results" / "summary.json")
    failures: list[dict[str, Any]] = []
    if summary.get("stage") != STAGE_NAME:
        failures.append({"rule": "stage_name", "observed": summary.get("stage")})
    if summary.get("status") != "PASS" or summary.get("backend") != "hf":
        failures.append({"rule": "official_status_backend", "status": summary.get("status"), "backend": summary.get("backend")})
    if summary.get("pilot_n") != EXPECTED_PILOT_N:
        failures.append({"rule": "pilot_n", "observed": summary.get("pilot_n")})
    if summary.get("model_calls_total") != EXPECTED_PILOT_N * len(METHODS):
        failures.append({"rule": "model_calls_total", "observed": summary.get("model_calls_total")})
    if summary.get("model_calls_per_sample_per_method") != 1:
        failures.append({"rule": "model_calls_per_sample_per_method", "observed": summary.get("model_calls_per_sample_per_method")})
    if summary.get("retry_count") != 0:
        failures.append({"rule": "retry_count", "observed": summary.get("retry_count")})
    if set(summary.get("methods", {})) != set(METHODS):
        failures.append({"rule": "method_set", "observed": sorted(summary.get("methods", {}))})
    for method_id in METHODS:
        item = summary.get("methods", {}).get(method_id, {})
        if item.get("samples") != EXPECTED_PILOT_N or item.get("model_calls") != EXPECTED_PILOT_N:
            failures.append({"rule": "method_denominator", "method_id": method_id, "item": item})
    runtime = summary.get("generation_metadata", {})
    constrained = runtime.get("constrained") or {}
    unconstrained = runtime.get("unconstrained") or {}
    if constrained.get("model_called") is not True or unconstrained.get("model_called") is not True:
        failures.append({"rule": "model_called", "constrained": constrained.get("model_called"), "unconstrained": unconstrained.get("model_called")})
    if constrained.get("model_revision") != MODEL_REVISION:
        failures.append({"rule": "model_revision", "observed": constrained.get("model_revision"), "expected": MODEL_REVISION})
    if constrained.get("runtime_lock", {}).get("status") != "PASS" or unconstrained.get("runtime_lock", {}).get("status") != "PASS":
        failures.append({"rule": "runtime_lock_status"})
    for rel, expected_rows in {
        "raw/model_outputs.jsonl": EXPECTED_PILOT_N * len(METHODS),
        "raw/parsed_outputs.jsonl": EXPECTED_PILOT_N * len(METHODS),
        "results/per_sample_results.jsonl": EXPECTED_PILOT_N * len(METHODS),
        "efficiency/token_usage.jsonl": EXPECTED_PILOT_N * len(METHODS),
        "efficiency/latency.jsonl": EXPECTED_PILOT_N * len(METHODS),
    }.items():
        observed = len(read_jsonl(result_dir / rel))
        if observed != expected_rows:
            failures.append({"rule": "row_count", "path": rel, "observed": observed, "expected": expected_rows})
    for rel in ["audits/denominator_audit.json", "audits/model_call_audit.json", "audits/retry_audit.json"]:
        audit = read_json(result_dir / rel)
        if audit.get("status") != "PASS":
            failures.append({"rule": "audit_status", "path": rel, "audit": audit})
    if failures:
        raise SystemExit(json.dumps({"status": "FAIL", "failures": failures}, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def official_report(summary: dict[str, Any], archive_sha256: str) -> str:
    rows = []
    for method_id in METHODS:
        item = summary["methods"][method_id]
        total_tokens = int(item.get("input_tokens", 0)) + int(item.get("output_tokens", 0))
        rows.append(
            f"| {method_id} | {item['target_state_accuracy']} | {item['execution_success_rate']} | "
            f"{item['admission_rate']} | {item['accepted_write_correctness']} | "
            f"{item['off_target_state_change']} | {item['model_calls']} | {total_tokens} |"
        )
    return f"""# Stage ENG2A Official Server Result Report

stage={STAGE_NAME}
patch={PATCH_NAME}
backend={summary['backend']}
status={summary['status']}
pilot_n={summary['pilot_n']}
model_calls_total={summary['model_calls_total']}
model_calls_per_sample_per_method={summary['model_calls_per_sample_per_method']}
retry_count={summary['retry_count']}
runtime_profile={summary['generation_metadata']['constrained']['runtime_profile_id']}
model_revision={summary['generation_metadata']['constrained']['model_revision']}
server_result_archive={SERVER_ARCHIVE}
server_result_archive_sha256={archive_sha256}

| Method | Target State | Exec. Success | Admission | Accepted Correct | Off-target | Calls | Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

Notes:
- These are official HF model outputs from the one-off UET RTX 4090 server run.
- The bundled mock dry-run remains a wiring check only and is not used as a scientific result.
- No retry or automatic repair was used.
"""


def validation_report(summary: dict[str, Any], archive_sha256: str) -> str:
    methods = ",".join(METHODS)
    metrics = ";".join(f"{method_id}:{summary['methods'][method_id]['target_state_accuracy']}" for method_id in METHODS)
    return f"""# Validation Report

stage={STAGE_NAME}
patch={PATCH_NAME}
pilot_n={EXPECTED_PILOT_N}
mock_methods={methods}
mock_model_called=false
official_generation_validated=true
official_backend={summary['backend']}
official_model_calls_total={summary['model_calls_total']}
official_retry_count={summary['retry_count']}
official_target_state_accuracy={metrics}
server_result_archive_sha256={archive_sha256}
status=OFFICIAL_SERVER_RUN_VALIDATED
"""


def update_protocol_patch(stage_dir: Path) -> None:
    protocol_path = stage_dir / "ENG2A_PROTOCOL_FREEZE.json"
    protocol = read_json(protocol_path)
    protocol["patch"] = PATCH_NAME
    write_json(protocol_path, protocol)


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    stage_dir = Path(args.stage_dir).resolve()
    archive_path = Path(args.archive).resolve()
    sha_path = Path(args.sha256).resolve()
    if not stage_dir.is_dir():
        raise SystemExit(f"STOP: missing stage directory: {stage_dir}")
    observed_sha = sha256_file(archive_path)
    expected_sha = expected_sha_from_file(sha_path)
    if observed_sha != expected_sha:
        raise SystemExit(f"STOP: archive SHA-256 mismatch: observed={observed_sha} expected={expected_sha}")
    extract_root = stage_dir / "_official_result_extract_tmp"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    result_root = safe_extract_tar(archive_path, extract_root)
    summary = validate_official_result_tree(result_root)
    official_dir = stage_dir / "official_server_run"
    if official_dir.exists():
        shutil.rmtree(official_dir)
    shutil.copytree(result_root, official_dir)
    shutil.rmtree(extract_root)
    write_text(stage_dir / "official_server_run" / "SERVER_RESULT_ARCHIVE.sha256", sha_path.read_text(encoding="utf-8"))
    write_json(
        stage_dir / "official_server_run" / "SERVER_RESULT_ARCHIVE_MANIFEST.json",
        {
            "archive": archive_path.name,
            "archive_bytes": archive_path.stat().st_size,
            "archive_sha256": observed_sha,
            "sha256_file": sha_path.name,
            "stage": STAGE_NAME,
        },
    )
    write_text(stage_dir / "OFFICIAL_RESULT_REPORT.md", official_report(summary, observed_sha))
    write_text(stage_dir / "VALIDATION_REPORT.md", validation_report(summary, observed_sha))
    write_text(stage_dir / "REVIEWER_README.md", reviewer_readme())
    update_protocol_patch(stage_dir)
    write_package_integrity(stage_dir)
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS",
        "archive_sha256": observed_sha,
        "official_summary": summary["methods"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--archive", type=Path, default=PROJECT_ROOT / SERVER_ARCHIVE)
    parser.add_argument("--sha256", type=Path, default=PROJECT_ROOT / f"{SERVER_ARCHIVE}.sha256")
    args = parser.parse_args()
    print(json.dumps(finalize(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
