#!/usr/bin/env python3
"""Validate and classify Stage7E0-A6 server result directories."""

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

from scripts.server.run_stage7e0_a6_english import (  # noqa: E402
    A6_PROMPT_SPEC_REL,
    CallResult,
    CONSTRAINED_BACKEND_ID,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    EXPECTED_PRIMARY_COUNT,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_MAX_NEW_TOKENS,
    PRIMARY_RUNTIME_PROFILE_ID,
    STAGE_NAME,
    STAGE7E0_A6_INPUT_MANIFEST_REL,
    build_phase_o_column_conditioned_constraint_grammar,
    evaluate_case,
    load_stage7c_a6_rows,
    match_runtime_profile,
    render_phase_o_messages,
)


REQUIRED_RESULT_FILES = {
    "primary_summary.json",
    "primary_case_results.jsonl",
    "raw_primary_phase_o_generations.jsonl",
    "run_manifest.json",
}

REQUIRED_RAW_PHASE_O_KEYS = {
    "sample_id",
    "phase",
    "status",
    "raw_output",
    "input_tokens",
    "output_tokens",
    "latency_sec",
    "generation_metadata",
    "messages_sha256",
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


def _index_exactly_once(rows: list[dict[str, Any]], *, label: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    failures: list[str] = []
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            failures.append(f"{label}_row_missing_sample_id")
            continue
        if sample_id in indexed:
            failures.append(f"{label}_duplicate_sample_id:{sample_id}")
            continue
        indexed[sample_id] = row
    return indexed, failures


def _load_expected_input_manifest() -> tuple[dict[str, Any], list[str]]:
    path = PROJECT_ROOT / STAGE7E0_A6_INPUT_MANIFEST_REL
    if not path.is_file():
        return {}, [f"missing_STAGE7E0_A6_INPUT_MANIFEST:{STAGE7E0_A6_INPUT_MANIFEST_REL}"]
    try:
        return read_json(path), []
    except Exception as exc:
        return {}, [f"could_not_parse_STAGE7E0_A6_INPUT_MANIFEST:{exc}"]


class RawPhaseOReplayGenerator:
    def __init__(self, raw_by_id: dict[str, dict[str, Any]]):
        self.raw_by_id = raw_by_id

    def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any]) -> CallResult:
        del messages, max_new_tokens, row
        raw = self.raw_by_id[sample_id]
        return CallResult(
            sample_id=sample_id,
            phase=str(raw.get("phase")),
            raw_output=str(raw.get("raw_output")),
            status=str(raw.get("status")),
            error=raw.get("error"),
            input_tokens=raw.get("input_tokens"),
            output_tokens=raw.get("output_tokens"),
            latency_sec=float(raw.get("latency_sec", 0.0)),
            hit_max_new_tokens=bool(raw.get("hit_max_new_tokens", False)),
            generation_metadata=raw.get("generation_metadata"),
        )

    def metadata(self) -> dict[str, Any]:
        return {"backend": "raw_phase_o_replay"}


def _expected_rows() -> tuple[list[dict[str, Any]], list[str]]:
    try:
        rows = load_stage7c_a6_rows(PROJECT_ROOT)
    except SystemExit as exc:
        return [], [f"could_not_load_locked_A6_primary_rows:{exc}"]
    return rows, []


def _safe_read_result(result_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_RESULT_FILES):
        if not (result_dir / rel).is_file():
            failures.append(f"missing_required_result_file:{rel}")
    if failures:
        return {}, {}, [], [], failures
    if (result_dir / "raw_phase_m_generations.jsonl").exists():
        failures.append("phase_m_raw_generations_must_not_exist_for_a6")
    try:
        return (
            read_json(result_dir / "primary_summary.json"),
            read_json(result_dir / "run_manifest.json"),
            read_jsonl(result_dir / "primary_case_results.jsonl"),
            read_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl"),
            failures,
        )
    except Exception as exc:
        return {}, {}, [], [], [*failures, f"could_not_parse_result_files:{exc}"]


def _case_result_matches_replay(case: dict[str, Any], replay_case: dict[str, Any]) -> bool:
    if canonical_text(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) == canonical_text(json.dumps(replay_case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))):
        return True
    legacy_required_omit_bookkeeping = (
        case.get("sample_id") == replay_case.get("sample_id")
        and case.get("status") == replay_case.get("status") == "FAIL"
        and case.get("failure_stage") == "A6_deterministic_oracle"
        and replay_case.get("failure_stage") == "required_column_omitted"
        and case.get("phase_o_messages_sha256") == replay_case.get("phase_o_messages_sha256")
    )
    return bool(legacy_required_omit_bookkeeping)


def evidence_integrity_failures(result_dir: Path, summary: dict[str, Any], manifest: dict[str, Any], cases: list[dict[str, Any]], raw_o: list[dict[str, Any]], frozen_rows: list[dict[str, Any]], expected_input: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    frozen_ids = [row["sample_id"] for row in frozen_rows]
    if summary.get("stage") != STAGE_NAME or manifest.get("stage") != STAGE_NAME:
        failures.append("stage_name_drifted")
    if len(cases) != EXPECTED_PRIMARY_COUNT:
        failures.append("primary_case_results_must_have_12_rows")
    if len(raw_o) != EXPECTED_PRIMARY_COUNT:
        failures.append("phase_o_raw_generations_must_have_12_rows")
    case_by_id, case_index_failures = _index_exactly_once(cases, label="primary_case_results")
    raw_by_id, raw_index_failures = _index_exactly_once(raw_o, label="raw_primary_phase_o_generations")
    failures.extend(case_index_failures)
    failures.extend(raw_index_failures)
    if list(case_by_id) != frozen_ids:
        failures.append("primary_case_result_ids_must_equal_locked_A6_primary_ids")
    if list(raw_by_id) != frozen_ids:
        failures.append("raw_generation_ids_must_equal_locked_A6_primary_ids")
    for raw in raw_o:
        missing = sorted(REQUIRED_RAW_PHASE_O_KEYS - set(raw))
        if missing:
            failures.append(f"raw_generation_missing_required_keys:{raw.get('sample_id')}:{','.join(missing)}")
            continue
        if raw.get("phase") != "phase_o":
            failures.append(f"raw_generation_phase_must_be_phase_o:{raw.get('sample_id')}")
        if raw.get("status") != "success":
            failures.append(f"raw_generation_status_must_be_success:{raw.get('sample_id')}")
        if not isinstance(raw.get("raw_output"), str) or not raw.get("raw_output").strip():
            failures.append(f"raw_generation_raw_output_missing:{raw.get('sample_id')}")
        if not isinstance(raw.get("generation_metadata"), dict):
            failures.append(f"raw_generation_metadata_missing:{raw.get('sample_id')}")
        if not isinstance(raw.get("input_tokens"), int) or raw.get("input_tokens") < 0:
            failures.append(f"raw_generation_input_tokens_invalid:{raw.get('sample_id')}")
        if not isinstance(raw.get("output_tokens"), int) or raw.get("output_tokens") < 0:
            failures.append(f"raw_generation_output_tokens_invalid:{raw.get('sample_id')}")
        if not isinstance(raw.get("latency_sec"), (int, float)) or raw.get("latency_sec") < 0:
            failures.append(f"raw_generation_latency_sec_invalid:{raw.get('sample_id')}")
    if summary.get("required_pass_count") != "12/12":
        failures.append("required_pass_count_must_remain_12_of_12")
    if summary.get("eleven_of_twelve_allowed") is not False:
        failures.append("11_of_12_acceptance_must_remain_forbidden")
    if summary.get("diagnostics_run") is not False:
        failures.append("diagnostics_must_not_run_before_primary_freeze")
    if summary.get("gretel_pilot_opened") is not False:
        failures.append("gretel_pilot_must_remain_unopened_during_Stage7E0_A6")
    if manifest.get("phase_o_prompt_spec_path") != A6_PROMPT_SPEC_REL:
        failures.append("server_did_not_use_exact_A6_phase_o_prompt_path")
    frozen_commit = expected_input.get("accepted_stage7c_a6_commit")
    if manifest.get("accepted_protocol_commit") != frozen_commit:
        failures.append("run_manifest_accepted_protocol_commit_mismatch")
    git_lock = manifest.get("git", {})
    if isinstance(git_lock, dict) and git_lock.get("frozen_accepted_protocol_commit") not in {None, frozen_commit}:
        failures.append("run_manifest_git_frozen_protocol_commit_mismatch")
    observed_inputs = manifest.get("stage7c_a6_inputs", {})
    expected_input_hashes = {
        "stage7c_a6_lock_sha256": expected_input.get("stage7c_a6_lock_sha256"),
        "a6_prompt_spec_sha256": expected_input.get("phase_o_prompt_spec_sha256"),
        "a6_primary_set_sha256": expected_input.get("primary_set_sha256"),
        "a6_diagnostic_set_sha256": expected_input.get("diagnostic_set_sha256"),
        "tokenizer_status": expected_input.get("tokenizer_status"),
        "chat_template_sha256": expected_input.get("chat_template_sha256"),
    }
    for key, expected in expected_input_hashes.items():
        if observed_inputs.get(key) != expected:
            failures.append(f"run_manifest_stage7c_A6_input_mismatch:{key}")
    if manifest.get("primary_case_count") != EXPECTED_PRIMARY_COUNT:
        failures.append("run_manifest_primary_case_count_must_be_12")
    if manifest.get("retry") != 0 or manifest.get("repair") != "none":
        failures.append("retry_repair_contract_drifted")
    if manifest.get("phase_m_removed") is not True or summary.get("phase_m_removed") is not True:
        failures.append("phase_m_removed_flag_missing")
    for rel, key in (
        ("raw_primary_phase_o_generations.jsonl", "raw_primary_phase_o_sha256"),
        ("primary_case_results.jsonl", "primary_case_results_sha256"),
    ):
        expected = summary.get(key)
        if expected and expected != sha256_file(result_dir / rel):
            failures.append(f"summary_hash_mismatch:{rel}")
    if failures:
        return failures
    replay = RawPhaseOReplayGenerator(raw_by_id)
    replay_cases: list[dict[str, Any]] = []
    for row in frozen_rows:
        sample_id = row["sample_id"]
        _messages, _user, message_hash = render_phase_o_messages(row)
        raw = raw_by_id[sample_id]
        case = case_by_id[sample_id]
        if raw.get("messages_sha256") != message_hash:
            failures.append(f"raw_generation_messages_sha256_mismatch:{sample_id}")
        if case.get("phase_o_messages_sha256") != message_hash:
            failures.append(f"case_result_messages_sha256_mismatch:{sample_id}")
        replay_case, _raw_replay = evaluate_case(row, replay, phase_o_max_new_tokens=PHASE_O_MAX_NEW_TOKENS)
        replay_cases.append(replay_case)
        if not _case_result_matches_replay(case, replay_case):
            failures.append(f"case_result_replay_mismatch:{sample_id}")
    replay_pass_count = sum(1 for row in replay_cases if row.get("status") == "PASS")
    if summary.get("primary_pass_count") != f"{replay_pass_count}/{EXPECTED_PRIMARY_COUNT}":
        failures.append("primary_pass_count_must_match_replayed_case_rows")
    if summary.get("status") != ("PASS" if replay_pass_count == EXPECTED_PRIMARY_COUNT else "FAIL"):
        failures.append("primary_summary_status_must_match_replayed_gate")
    return failures


def _metadata_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row["generation_metadata"] for row in raw_rows if isinstance(row.get("generation_metadata"), dict)]


def protocol_compliance_failures(summary: dict[str, Any], manifest: dict[str, Any], raw_o: list[dict[str, Any]], frozen_rows: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    model = manifest.get("model", {})
    if summary.get("backend") != "constrained_hf":
        failures.append("summary_backend_must_be_constrained_hf")
    if summary.get("protocol_backend") != CONSTRAINED_BACKEND_ID:
        failures.append(f"summary_protocol_backend_must_be_{CONSTRAINED_BACKEND_ID}")
    if model.get("backend") != CONSTRAINED_BACKEND_ID:
        failures.append(f"manifest_model_backend_must_be_{CONSTRAINED_BACKEND_ID}")
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
    if model.get("model_id") != MODEL_ID:
        failures.append("model_id_drifted")
    if model.get("model_revision") != MODEL_REVISION:
        failures.append("model_revision_drifted")
    if model.get("chat_template_sha256") != EXPECTED_CHAT_TEMPLATE_SHA256:
        failures.append("chat_template_hash_drifted")
    if model.get("quantization") != "none":
        failures.append("quantization_must_be_none")
    if model.get("torch_dtype") != "auto":
        failures.append("torch_dtype_must_be_auto")
    if model.get("device_map") != "auto":
        failures.append("device_map_must_be_auto")
    if model.get("max_memory") is not None:
        failures.append("max_memory_must_be_null")
    if model.get("primary_runtime_profile_id") != PRIMARY_RUNTIME_PROFILE_ID:
        failures.append(f"primary_runtime_profile_id_must_be_{PRIMARY_RUNTIME_PROFILE_ID}")
    runtime_match = match_runtime_profile(model, allowed_profile_ids=(PRIMARY_RUNTIME_PROFILE_ID,), require_gpu_topology=True)
    if runtime_match["status"] != "PASS":
        failures.append(f"runtime_profile_mismatch:{json.dumps(runtime_match['failures'], ensure_ascii=False, sort_keys=True)}")
    if int(summary.get("phase_o_max_new_tokens", -1)) != PHASE_O_MAX_NEW_TOKENS:
        failures.append("summary_phase_o_max_new_tokens_drifted")
    if int(manifest.get("phase_o_max_new_tokens", -1)) != PHASE_O_MAX_NEW_TOKENS:
        failures.append("manifest_phase_o_max_new_tokens_drifted")
    raw_by_id, index_failures = _index_exactly_once(raw_o, label="raw_primary_phase_o_generations")
    failures.extend(index_failures)
    for row in frozen_rows:
        sample_id = row["sample_id"]
        raw = raw_by_id.get(sample_id, {})
        metadata = raw.get("generation_metadata")
        if not isinstance(metadata, dict):
            failures.append(f"raw_generation_metadata_missing:{sample_id}")
            continue
        for key, expected in {
            "backend": CONSTRAINED_BACKEND_ID,
            "token_level_enforcement": True,
            "fallback_to_unconstrained": False,
            "finite_complete_object_enumeration": False,
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
            "automatic_repair": False,
            "retry": 0,
        }.items():
            if metadata.get(key) != expected:
                failures.append(f"raw_generation_metadata_{sample_id}_{key}_must_be_{expected!r}")
                break
        grammar = build_phase_o_column_conditioned_constraint_grammar(row["runtime_constraints"]["phase_o_schema"])
        if metadata.get("schema_sha256") != grammar.schema_sha256:
            failures.append(f"raw_generation_schema_sha256_mismatch:{sample_id}")
        if metadata.get("constraint_grammar_sha256") != grammar.fingerprint:
            failures.append(f"raw_generation_constraint_grammar_sha256_mismatch:{sample_id}")
        if metadata.get("constraint_fingerprint") not in {None, grammar.fingerprint}:
            failures.append(f"raw_generation_constraint_fingerprint_mismatch:{sample_id}")
    return failures


def classify_result(result_dir: Path) -> dict[str, Any]:
    summary, manifest, cases, raw_o, read_failures = _safe_read_result(result_dir)
    evidence_failures = list(read_failures)
    frozen_rows, frozen_failures = _expected_rows()
    expected_input, input_manifest_failures = _load_expected_input_manifest()
    evidence_failures.extend(frozen_failures)
    evidence_failures.extend(input_manifest_failures)
    if not evidence_failures:
        evidence_failures.extend(evidence_integrity_failures(result_dir, summary, manifest, cases, raw_o, frozen_rows, expected_input))
    evidence_status = "PASS" if not evidence_failures else "FAIL"
    protocol_failures = [] if evidence_failures else protocol_compliance_failures(summary, manifest, raw_o, frozen_rows)
    protocol_status = "PASS" if not protocol_failures else "FAIL"
    if evidence_status != "PASS" or protocol_status != "PASS":
        primary_status = "INVALID_NOT_EVALUATED"
    else:
        primary_status = "PASS" if summary.get("primary_pass_count") == "12/12" and summary.get("status") == "PASS" else "FAIL"
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
    parser.add_argument("--result-dir", type=Path, required=True)
    args = parser.parse_args()
    report = validate(args.result_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
