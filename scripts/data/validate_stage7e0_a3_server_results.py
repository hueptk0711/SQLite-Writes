#!/usr/bin/env python3
"""Validate and classify Stage7E0-A3 server result directories.

This validator is outcome-generic. It reports evidence integrity, protocol
compliance, and the primary gate separately, so a constrained run can be
classified as PASS/PASS/PASS or PASS/PASS/FAIL, while a plain-HF run is
preserved as PASS/FAIL/INVALID_NOT_EVALUATED evidence.
"""

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


STAGE_NAME = "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT"
SERVER_RUN_ID = "server_real_run_20260831_patch3_constrained"
RESULT_DIR_NAME = "stage7e0_a3_english_patch3_constrained_results_20260831"
LEGACY_RESULT_CANDIDATES = (
    ("server_real_run_20260830_220327", "stage7e0_a3_english_real_generation_preflight_results"),
)
EXPECTED_PRIMARY_COUNT = 8
EXPECTED_BACKEND = "incremental_json_schema_grammar"
EXPECTED_SUMMARY_BACKEND = "constrained_hf"
EXPECTED_PHASE_O_MAX_NEW_TOKENS = 512
EXPECTED_PHASE_M_MAX_NEW_TOKENS = 8192
EXPECTED_PROMPT_PATH = "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json"
EXPECTED_MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
EXPECTED_MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
EXPECTED_CHAT_TEMPLATE_SHA256 = "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"
FROZEN_RUNTIME_VERSIONS = {
    "torch_version": "2.6.0+cu124",
    "transformers_version": "5.5.3",
    "tokenizers_version": "0.22.2",
    "accelerate_version": "1.14.0",
    "safetensors_version": "0.5.3",
}
REQUIRED_RESULT_FILES = {
    "primary_summary.json",
    "primary_case_results.jsonl",
    "raw_phase_o_generations.jsonl",
    "raw_phase_m_generations.jsonl",
    "run_manifest.json",
}


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _safe_read_result(result_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_RESULT_FILES):
        if not (result_dir / rel).is_file():
            failures.append(f"missing required result file: {rel}")
    if failures:
        return {}, {}, [], [], [], failures
    try:
        summary = read_json(result_dir / "primary_summary.json")
        manifest = read_json(result_dir / "run_manifest.json")
        cases = read_jsonl(result_dir / "primary_case_results.jsonl")
        raw_o = read_jsonl(result_dir / "raw_phase_o_generations.jsonl")
        raw_m = read_jsonl(result_dir / "raw_phase_m_generations.jsonl")
    except Exception as exc:
        return {}, {}, [], [], [], [f"could not parse result files: {exc}"]
    return summary, manifest, cases, raw_o, raw_m, []


def evidence_integrity_failures(result_dir: Path, summary: dict[str, Any], manifest: dict[str, Any], cases: list[dict[str, Any]], raw_o: list[dict[str, Any]], raw_m: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if summary.get("stage") != STAGE_NAME or manifest.get("stage") != STAGE_NAME:
        failures.append("stage name drifted")
    if len(cases) != EXPECTED_PRIMARY_COUNT:
        failures.append("primary case results must have 8 rows")
    if len(raw_o) != EXPECTED_PRIMARY_COUNT:
        failures.append("Phase O raw generations must have 8 rows")
    if len(raw_m) > EXPECTED_PRIMARY_COUNT:
        failures.append("Phase M raw generations cannot exceed 8 rows")
    observed_pass_count = sum(1 for row in cases if row.get("status") == "PASS")
    if summary.get("primary_pass_count") != f"{observed_pass_count}/{EXPECTED_PRIMARY_COUNT}":
        failures.append("primary_pass_count must match observed case rows")
    if summary.get("required_pass_count") != "8/8":
        failures.append("required_pass_count must remain 8/8")
    if summary.get("seven_of_eight_allowed") is not False:
        failures.append("7/8 acceptance must remain forbidden")
    if summary.get("diagnostics_run") is not False:
        failures.append("diagnostics must not run before primary freeze")
    if summary.get("gretel_pilot_opened") is not False:
        failures.append("Gretel pilot must remain unopened during Stage7E0-A3")
    if manifest.get("phase_o_prompt_spec_path") != EXPECTED_PROMPT_PATH:
        failures.append("server did not use exact A3 Phase O prompt path")
    if manifest.get("retry") != 0 or manifest.get("repair") != "none":
        failures.append("retry/repair contract drifted")
    for rel, key in (
        ("raw_phase_o_generations.jsonl", "raw_phase_o_sha256"),
        ("raw_phase_m_generations.jsonl", "raw_phase_m_sha256"),
        ("primary_case_results.jsonl", "primary_case_results_sha256"),
    ):
        expected = summary.get(key)
        if expected and expected != sha256_file(result_dir / rel):
            failures.append(f"summary hash mismatch: {rel}")
    return failures


def _metadata_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        metadata = row.get("generation_metadata")
        if isinstance(metadata, dict):
            rows.append(metadata)
    return rows


def protocol_compliance_failures(summary: dict[str, Any], manifest: dict[str, Any], raw_o: list[dict[str, Any]], raw_m: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    model = manifest.get("model", {})
    if summary.get("backend") != EXPECTED_SUMMARY_BACKEND:
        failures.append(f"summary backend must be {EXPECTED_SUMMARY_BACKEND}")
    if summary.get("protocol_backend") != EXPECTED_BACKEND:
        failures.append(f"summary protocol_backend must be {EXPECTED_BACKEND}")
    if model.get("backend") != EXPECTED_BACKEND:
        failures.append(f"manifest model backend must be {EXPECTED_BACKEND}")
    if model.get("schema_enforcement_mode") != "transformers_prefix_allowed_tokens_fn":
        failures.append("schema enforcement must use transformers prefix_allowed_tokens_fn")
    for key, expected in {
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "retry": 0,
        "model_called": True,
        "cuda_available": True,
    }.items():
        if model.get(key) != expected:
            failures.append(f"manifest model {key} must be {expected!r}")
    if model.get("model_id") != EXPECTED_MODEL_ID:
        failures.append("model_id drifted")
    if model.get("model_revision") != EXPECTED_MODEL_REVISION:
        failures.append("model_revision drifted")
    if model.get("chat_template_sha256") != EXPECTED_CHAT_TEMPLATE_SHA256:
        failures.append("chat template hash drifted")
    if model.get("quantization") != "none":
        failures.append("quantization must be none")
    if model.get("torch_dtype") != "auto":
        failures.append("torch_dtype must be auto")
    for key, expected in FROZEN_RUNTIME_VERSIONS.items():
        if model.get(key) != expected:
            failures.append(f"runtime {key} must be {expected}")
    if int(summary.get("phase_o_max_new_tokens", -1)) != EXPECTED_PHASE_O_MAX_NEW_TOKENS:
        failures.append("summary Phase O max_new_tokens drifted")
    if int(summary.get("phase_m_max_new_tokens", -1)) != EXPECTED_PHASE_M_MAX_NEW_TOKENS:
        failures.append("summary Phase M max_new_tokens drifted")
    if int(manifest.get("phase_o_max_new_tokens", -1)) != EXPECTED_PHASE_O_MAX_NEW_TOKENS:
        failures.append("manifest Phase O max_new_tokens drifted")
    if int(manifest.get("phase_m_max_new_tokens", -1)) != EXPECTED_PHASE_M_MAX_NEW_TOKENS:
        failures.append("manifest Phase M max_new_tokens drifted")
    metadata_rows = _metadata_rows(raw_o) + _metadata_rows(raw_m)
    if not metadata_rows:
        failures.append("raw generation rows must include generation_metadata")
    for index, metadata in enumerate(metadata_rows):
        for key, expected in {
            "backend": EXPECTED_BACKEND,
            "token_level_enforcement": True,
            "fallback_to_unconstrained": False,
            "finite_complete_object_enumeration": False,
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
            "automatic_repair": False,
            "retry": 0,
        }.items():
            if metadata.get(key) != expected:
                failures.append(f"raw generation metadata row {index} {key} must be {expected!r}")
                break
    return failures


def classify_result(result_dir: Path) -> dict[str, Any]:
    summary, manifest, cases, raw_o, raw_m, read_failures = _safe_read_result(result_dir)
    evidence_failures = list(read_failures)
    if not evidence_failures:
        evidence_failures.extend(evidence_integrity_failures(result_dir, summary, manifest, cases, raw_o, raw_m))
    evidence_status = "PASS" if not evidence_failures else "FAIL"
    protocol_failures = [] if evidence_failures else protocol_compliance_failures(summary, manifest, raw_o, raw_m)
    protocol_status = "PASS" if not protocol_failures else "FAIL"
    if evidence_status != "PASS" or protocol_status != "PASS":
        primary_status = "INVALID_NOT_EVALUATED"
    else:
        primary_status = "PASS" if summary.get("primary_pass_count") == "8/8" and summary.get("status") == "PASS" else "FAIL"
    failure_counts: dict[str, int] = {}
    for row in cases:
        key = str(row.get("failure_stage"))
        failure_counts[key] = failure_counts.get(key, 0) + 1
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if evidence_status == "PASS" else "FAIL",
        "result_dir": str(result_dir),
        "evidence_integrity_status": evidence_status,
        "protocol_compliance_status": protocol_status,
        "primary_gate_status": primary_status,
        "scientific_result_eligible": evidence_status == "PASS" and protocol_status == "PASS",
        "failures": evidence_failures,
        "evidence_failures": evidence_failures,
        "protocol_failures": protocol_failures,
        "primary_pass_count": summary.get("primary_pass_count"),
        "required_pass_count": summary.get("required_pass_count"),
        "model_called": summary.get("model_called"),
        "gpu_called": summary.get("gpu_called"),
        "gretel_pilot_opened": summary.get("gretel_pilot_opened"),
        "diagnostics_run": summary.get("diagnostics_run"),
        "failure_stage_counts": failure_counts,
    }


def validate(stage_dir: Path, tar_path: Path | None = None, result_dir: Path | None = None) -> dict[str, Any]:
    del tar_path
    if result_dir is not None:
        resolved_result_dir = result_dir
    else:
        candidates = [(SERVER_RUN_ID, RESULT_DIR_NAME), *LEGACY_RESULT_CANDIDATES]
        resolved_result_dir = next(
            (stage_dir / run_id / result_name for run_id, result_name in candidates if (stage_dir / run_id / result_name).is_dir()),
            stage_dir / SERVER_RUN_ID / RESULT_DIR_NAME,
        )
    return classify_result(resolved_result_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--result-dir", type=Path, help="Direct path to a Stage7E0-A3 result directory.")
    parser.add_argument("--server-results-tar", type=Path, help="Accepted for CLI compatibility; result dirs are validated after extraction/import.")
    args = parser.parse_args()
    report = validate(args.stage_dir, args.server_results_tar, args.result_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
