#!/usr/bin/env python3
"""Validate and classify Stage7E0-A4 server result directories."""

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


STAGE_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
RESULT_DIR_NAME = "stage7e0_a4_english_candidate_span_constrained_results_20260831"
EXPECTED_PRIMARY_COUNT = 10
EXPECTED_BACKEND = "incremental_json_schema_grammar"
EXPECTED_SUMMARY_BACKEND = "constrained_hf"
EXPECTED_PHASE_O_MAX_NEW_TOKENS = 512
EXPECTED_PHASE_M_MAX_NEW_TOKENS = 8192
EXPECTED_PROMPT_PATH = "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/PHASE_O_PROMPT_SPEC_A4_ENGLISH.json"
EXPECTED_MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
EXPECTED_MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
EXPECTED_CHAT_TEMPLATE_SHA256 = "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"
ALLOWED_RUNTIME_PROFILES = [
    {
        "profile_id": "uet_server_cuda124",
        "versions": {
            "torch_version": ["2.6.0+cu124"],
            "transformers_version": ["5.5.3"],
            "tokenizers_version": ["0.22.2"],
            "accelerate_version": ["1.14.0"],
            "safetensors_version": ["0.5.3"],
        },
        "cuda_runtime": "12.4",
    },
    {
        "profile_id": "kaggle_t4x2_cuda130",
        "versions": {
            "torch_version": ["2.13.0+cu130", "2.13.0"],
            "transformers_version": ["5.5.3"],
            "tokenizers_version": ["0.22.2"],
            "accelerate_version": ["1.14.0"],
            "safetensors_version": ["0.5.3"],
        },
        "cuda_runtime": "13.0",
        "gpu_count": 2,
        "gpu_device_substring": "Tesla T4",
    },
]
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _safe_read_result(result_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_RESULT_FILES):
        if not (result_dir / rel).is_file():
            failures.append(f"missing_required_result_file:{rel}")
    if failures:
        return {}, {}, [], [], [], failures
    try:
        return (
            read_json(result_dir / "primary_summary.json"),
            read_json(result_dir / "run_manifest.json"),
            read_jsonl(result_dir / "primary_case_results.jsonl"),
            read_jsonl(result_dir / "raw_phase_o_generations.jsonl"),
            read_jsonl(result_dir / "raw_phase_m_generations.jsonl"),
            [],
        )
    except Exception as exc:
        return {}, {}, [], [], [], [f"could_not_parse_result_files:{exc}"]


def evidence_integrity_failures(result_dir: Path, summary: dict[str, Any], manifest: dict[str, Any], cases: list[dict[str, Any]], raw_o: list[dict[str, Any]], raw_m: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if summary.get("stage") != STAGE_NAME or manifest.get("stage") != STAGE_NAME:
        failures.append("stage_name_drifted")
    if len(cases) != EXPECTED_PRIMARY_COUNT:
        failures.append("primary_case_results_must_have_10_rows")
    if len(raw_o) != EXPECTED_PRIMARY_COUNT:
        failures.append("phase_o_raw_generations_must_have_10_rows")
    if len(raw_m) > EXPECTED_PRIMARY_COUNT:
        failures.append("phase_m_raw_generations_cannot_exceed_10_rows")
    observed_pass_count = sum(1 for row in cases if row.get("status") == "PASS")
    if summary.get("primary_pass_count") != f"{observed_pass_count}/{EXPECTED_PRIMARY_COUNT}":
        failures.append("primary_pass_count_must_match_observed_case_rows")
    if summary.get("required_pass_count") != "10/10":
        failures.append("required_pass_count_must_remain_10_of_10")
    if summary.get("nine_of_ten_allowed") is not False:
        failures.append("9_of_10_acceptance_must_remain_forbidden")
    if summary.get("diagnostics_run") is not False:
        failures.append("diagnostics_must_not_run_before_primary_freeze")
    if summary.get("gretel_pilot_opened") is not False:
        failures.append("gretel_pilot_must_remain_unopened_during_stage7e0_a4")
    if manifest.get("phase_o_prompt_spec_path") != EXPECTED_PROMPT_PATH:
        failures.append("server_did_not_use_exact_a4_phase_o_prompt_path")
    if manifest.get("retry") != 0 or manifest.get("repair") != "none":
        failures.append("retry_repair_contract_drifted")
    for rel, key in (
        ("raw_phase_o_generations.jsonl", "raw_phase_o_sha256"),
        ("raw_phase_m_generations.jsonl", "raw_phase_m_sha256"),
        ("primary_case_results.jsonl", "primary_case_results_sha256"),
    ):
        expected = summary.get(key)
        if expected and expected != sha256_file(result_dir / rel):
            failures.append(f"summary_hash_mismatch:{rel}")
    return failures


def _metadata_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row["generation_metadata"] for row in raw_rows if isinstance(row.get("generation_metadata"), dict)]


def _runtime_profile_match(model: dict[str, Any]) -> tuple[str | None, dict[str, dict[str, Any]]]:
    profile_mismatches: dict[str, dict[str, Any]] = {}
    for profile in ALLOWED_RUNTIME_PROFILES:
        mismatches: dict[str, Any] = {}
        for key, allowed_values in profile["versions"].items():
            if model.get(key) not in allowed_values:
                mismatches[key] = {"allowed": allowed_values, "observed": model.get(key)}
        declared_profile_id = model.get("runtime_profile_id")
        if declared_profile_id is not None and declared_profile_id != profile["profile_id"]:
            mismatches["runtime_profile_id"] = {"expected": profile["profile_id"], "observed": declared_profile_id}
        cuda_runtime = profile.get("cuda_runtime")
        if cuda_runtime is not None and model.get("cuda_runtime") != cuda_runtime:
            mismatches["cuda_runtime"] = {"expected": cuda_runtime, "observed": model.get("cuda_runtime")}
        gpu_count = profile.get("gpu_count")
        if gpu_count is not None and model.get("gpu_count") != gpu_count:
            mismatches["gpu_count"] = {"expected": gpu_count, "observed": model.get("gpu_count")}
        gpu_device_substring = profile.get("gpu_device_substring")
        if gpu_device_substring is not None:
            gpu_devices = model.get("gpu_devices")
            if not isinstance(gpu_devices, list) or len(gpu_devices) != gpu_count or any(gpu_device_substring not in str(device) for device in gpu_devices):
                mismatches["gpu_devices"] = {"expected": f"{gpu_count} devices containing {gpu_device_substring}", "observed": gpu_devices}
        if not mismatches:
            return str(profile["profile_id"]), {}
        profile_mismatches[str(profile["profile_id"])] = mismatches
    return None, profile_mismatches


def protocol_compliance_failures(summary: dict[str, Any], manifest: dict[str, Any], raw_o: list[dict[str, Any]], raw_m: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    model = manifest.get("model", {})
    if summary.get("backend") != EXPECTED_SUMMARY_BACKEND:
        failures.append(f"summary_backend_must_be_{EXPECTED_SUMMARY_BACKEND}")
    if summary.get("protocol_backend") != EXPECTED_BACKEND:
        failures.append(f"summary_protocol_backend_must_be_{EXPECTED_BACKEND}")
    if model.get("backend") != EXPECTED_BACKEND:
        failures.append(f"manifest_model_backend_must_be_{EXPECTED_BACKEND}")
    if model.get("schema_enforcement_mode") != "transformers_prefix_allowed_tokens_fn":
        failures.append("schema_enforcement_must_use_transformers_prefix_allowed_tokens_fn")
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
            failures.append(f"manifest_model_{key}_must_be_{expected!r}")
    if model.get("model_id") != EXPECTED_MODEL_ID:
        failures.append("model_id_drifted")
    if model.get("model_revision") != EXPECTED_MODEL_REVISION:
        failures.append("model_revision_drifted")
    if model.get("chat_template_sha256") != EXPECTED_CHAT_TEMPLATE_SHA256:
        failures.append("chat_template_hash_drifted")
    if model.get("quantization") != "none":
        failures.append("quantization_must_be_none")
    if model.get("torch_dtype") != "auto":
        failures.append("torch_dtype_must_be_auto")
    runtime_profile_id, runtime_mismatches = _runtime_profile_match(model)
    if runtime_profile_id is None:
        failures.append(f"runtime_profile_mismatch:{json.dumps(runtime_mismatches, ensure_ascii=False, sort_keys=True)}")
    if int(summary.get("phase_o_max_new_tokens", -1)) != EXPECTED_PHASE_O_MAX_NEW_TOKENS:
        failures.append("summary_phase_o_max_new_tokens_drifted")
    if int(summary.get("phase_m_max_new_tokens", -1)) != EXPECTED_PHASE_M_MAX_NEW_TOKENS:
        failures.append("summary_phase_m_max_new_tokens_drifted")
    if int(manifest.get("phase_o_max_new_tokens", -1)) != EXPECTED_PHASE_O_MAX_NEW_TOKENS:
        failures.append("manifest_phase_o_max_new_tokens_drifted")
    if int(manifest.get("phase_m_max_new_tokens", -1)) != EXPECTED_PHASE_M_MAX_NEW_TOKENS:
        failures.append("manifest_phase_m_max_new_tokens_drifted")
    metadata_rows = _metadata_rows(raw_o) + _metadata_rows(raw_m)
    if not metadata_rows:
        failures.append("raw_generation_rows_must_include_generation_metadata")
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
                failures.append(f"raw_generation_metadata_row_{index}_{key}_must_be_{expected!r}")
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
        primary_status = "PASS" if summary.get("primary_pass_count") == "10/10" and summary.get("status") == "PASS" else "FAIL"
    failure_counts: dict[str, int] = {}
    for row in cases:
        key = str(row.get("failure_stage"))
        failure_counts[key] = failure_counts.get(key, 0) + 1
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if evidence_status == "PASS" and protocol_status == "PASS" else "FAIL",
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


def validate(result_dir: Path) -> dict[str, Any]:
    return classify_result(result_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, default=PROJECT_ROOT / RESULT_DIR_NAME)
    args = parser.parse_args()
    report = validate(args.result_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
