#!/usr/bin/env python3
"""Validate Stage7E0-A4 candidate-span preflight artifacts."""

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

from scripts.data.build_stage7e0_a4_english_preflight import FRESH_CONSTRAINED_RESULT_DIR_NAME, KAGGLE_REQUIREMENTS_LOCK  # noqa: E402
from scripts.data.validate_stage7c_a4_candidate_span_phase_o_protocol import validate as validate_stage7c_a4  # noqa: E402
from scripts.server.run_stage7e0_a4_english import (  # noqa: E402
    A4_PROMPT_SPEC_REL,
    ALLOWED_FROZEN_RUNTIME_PROFILES,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    EXPECTED_PRIMARY_COUNT,
    FROZEN_RUNTIME_VERSIONS,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_M_MAX_NEW_TOKENS,
    PHASE_O_MAX_NEW_TOKENS,
    HISTORICAL_RUNTIME_PROFILE_IDS,
    PRIMARY_RUNTIME_PROFILE_ID,
    STAGE7C_A4_DIR,
    build_phase_o_span_ref_constraint_grammar,
    load_stage7c_a4_rows,
    render_phase_o_a4_messages,
    runtime_profile_by_id,
)


STAGE_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
REQUIRED_FILES = {
    "STAGE7E0_A4_INPUT_MANIFEST.json",
    "RUNNER_PROTOCOL_A4.json",
    "PRIMARY_ACCEPTANCE_POLICY_A4.json",
    "CONSTRAINT_INDEPENDENCE_AUDIT_A4.json",
    "SERVER_RUN_COMMANDS.md",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7E0_A4_LOCK.json",
    "mock_dry_run/run_manifest.json",
    "mock_dry_run/primary_summary.json",
    "mock_dry_run/primary_case_results.jsonl",
    "mock_dry_run/raw_phase_o_generations.jsonl",
    "mock_dry_run/raw_phase_m_generations.jsonl",
}
PACKAGE_ROOT_REQUIRED_FILES = {
    KAGGLE_REQUIREMENTS_LOCK,
    "scripts/server/preflight_runtime.py",
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


def validate(stage_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_FILES):
        if not (stage_dir / rel).is_file():
            failures.append(f"missing_required_artifact:{rel}")
    package_root = stage_dir.parent
    for rel in sorted(PACKAGE_ROOT_REQUIRED_FILES):
        if not (package_root / rel).is_file() and not (PROJECT_ROOT / rel).is_file():
            failures.append(f"missing_required_package_file:{rel}")
    if failures:
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}

    upstream = validate_stage7c_a4(PROJECT_ROOT / STAGE7C_A4_DIR)
    if upstream.get("status") != "PASS":
        failures.append("stage7c_a4_upstream_validation_failed")

    protocol = read_json(stage_dir / "RUNNER_PROTOCOL_A4.json")
    inputs = read_json(stage_dir / "STAGE7E0_A4_INPUT_MANIFEST.json")
    policy = read_json(stage_dir / "PRIMARY_ACCEPTANCE_POLICY_A4.json")
    independence = read_json(stage_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A4.json")
    lock = read_json(stage_dir / "STAGE7E0_A4_LOCK.json")
    mock_summary = read_json(stage_dir / "mock_dry_run" / "primary_summary.json")
    mock_cases = read_jsonl(stage_dir / "mock_dry_run" / "primary_case_results.jsonl")
    raw_o = read_jsonl(stage_dir / "mock_dry_run" / "raw_phase_o_generations.jsonl")
    raw_m = read_jsonl(stage_dir / "mock_dry_run" / "raw_phase_m_generations.jsonl")

    model = protocol.get("model", {})
    if model.get("model_id") != MODEL_ID:
        failures.append("runner_protocol_model_id_drifted")
    if model.get("model_revision") != MODEL_REVISION:
        failures.append("runner_protocol_model_revision_drifted")
    if model.get("default_server_model_path") != DEFAULT_MODEL_PATH:
        failures.append("runner_protocol_server_model_path_drifted")
    if model.get("expected_chat_template_sha256") != EXPECTED_CHAT_TEMPLATE_SHA256:
        failures.append("runner_protocol_chat_template_hash_drifted")
    if model.get("quantization_default") != "none" or model.get("quantization_allowed") is not False:
        failures.append("runner_protocol_quantization_not_frozen_none")
    if model.get("frozen_runtime_versions") != FROZEN_RUNTIME_VERSIONS:
        failures.append("frozen_runtime_versions_drifted")
    if model.get("allowed_frozen_runtime_profiles") != ALLOWED_FROZEN_RUNTIME_PROFILES:
        failures.append("allowed_frozen_runtime_profiles_drifted")
    if model.get("primary_runtime_profile_id") != PRIMARY_RUNTIME_PROFILE_ID:
        failures.append("primary_runtime_profile_id_drifted")
    if model.get("primary_runtime_profile") != runtime_profile_by_id(PRIMARY_RUNTIME_PROFILE_ID):
        failures.append("primary_runtime_profile_drifted")
    if model.get("historical_runtime_profile_ids") != HISTORICAL_RUNTIME_PROFILE_IDS:
        failures.append("historical_runtime_profile_ids_drifted")
    if model.get("historical_runtime_profiles") != [runtime_profile_by_id(profile_id) for profile_id in HISTORICAL_RUNTIME_PROFILE_IDS]:
        failures.append("historical_runtime_profiles_drifted")
    if model.get("kaggle_requirements_lock") != KAGGLE_REQUIREMENTS_LOCK:
        failures.append("kaggle_requirements_lock_drifted")

    prompt_contract = protocol.get("prompt_contract", {})
    if prompt_contract.get("phase_o_prompt_spec_path") != A4_PROMPT_SPEC_REL:
        failures.append("phase_o_prompt_spec_path_drifted")
    if prompt_contract.get("phase_o_output_keys") != ["operation", "span_refs"]:
        failures.append("phase_o_output_keys_drifted")
    if prompt_contract.get("phase_o_numeric_offsets_forbidden") is not True:
        failures.append("phase_o_numeric_offsets_not_forbidden")
    if prompt_contract.get("phase_m_changed") is not False:
        failures.append("phase_m_must_remain_unchanged")

    generation = protocol.get("generation_contract", {})
    expected_generation = {
        "phase_o_calls": 1,
        "phase_m_calls": 1,
        "retry": 0,
        "repair": "none",
        "backend": "incremental_json_schema_grammar",
        "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "resume_allowed": False,
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
        "single_primary_runtime_profile": True,
        "runtime_profile_switch_after_completed_generation_allowed": False,
        "gpu_topology_fail_fast_before_model_load": True,
        "device_map": "auto",
        "max_memory": None,
    }
    for key, expected in expected_generation.items():
        if generation.get(key) != expected:
            failures.append(f"generation_contract_{key}_drifted")
    if "do not pass --resume" not in generation.get("interrupted_run_policy", ""):
        failures.append("interrupted_run_policy_must_forbid_resume")

    if policy.get("required_pass_count") != "10/10":
        failures.append("primary_acceptance_must_require_10_of_10")
    if policy.get("nine_of_ten_allowed") is not False or policy.get("averaging_allowed") is not False:
        failures.append("primary_acceptance_must_forbid_9_of_10_and_averaging")

    if inputs.get("phase_o_prompt_spec_path") != A4_PROMPT_SPEC_REL:
        failures.append("input_manifest_prompt_path_drifted")
    if inputs.get("fresh_primary_case_count") != EXPECTED_PRIMARY_COUNT:
        failures.append("input_manifest_case_count_drifted")
    if inputs.get("phase_o_prompt_spec_sha256") != sha256_file(PROJECT_ROOT / A4_PROMPT_SPEC_REL):
        failures.append("input_manifest_prompt_hash_mismatch")
    if inputs.get("fresh_primary_set_sha256") != sha256_file(PROJECT_ROOT / STAGE7C_A4_DIR / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"):
        failures.append("input_manifest_primary_set_hash_mismatch")

    rows = load_stage7c_a4_rows(PROJECT_ROOT)
    if [row["sample_id"] for row in rows] != inputs.get("fresh_primary_case_ids"):
        failures.append("primary_case_order_drifted")
    messages, _digest = render_phase_o_a4_messages(rows[0], root=PROJECT_ROOT)
    if "Candidate span inventory:" not in messages[1]["content"] or "SPAN_" not in messages[1]["content"]:
        failures.append("rendered_phase_o_prompt_missing_candidate_inventory")
    if "start_char" in messages[1]["content"] or "end_char" in messages[1]["content"]:
        failures.append("rendered_phase_o_prompt_exposes_offsets")
    grammar = build_phase_o_span_ref_constraint_grammar(rows[0]["runtime_constraints"]["phase_o_schema"])
    if grammar.span_ref_choices != [candidate["span_ref"] for candidate in rows[0]["runtime_constraints"]["candidate_inventory"]]:
        failures.append("phase_o_constraint_grammar_not_exact_dynamic_enum")

    if mock_summary.get("backend") != "mock" or mock_summary.get("status") != "PASS":
        failures.append("mock_dry_run_must_pass")
    if mock_summary.get("primary_pass_count") != "10/10":
        failures.append("mock_dry_run_must_prove_10_of_10_wiring")
    if mock_summary.get("model_called") is not False or mock_summary.get("gpu_called") is not False:
        failures.append("mock_dry_run_must_not_call_model_or_gpu")
    if mock_summary.get("mock_uses_label_side_expected") is not True:
        failures.append("mock_dry_run_must_disclose_label_side_use")
    if len(mock_cases) != EXPECTED_PRIMARY_COUNT or any(row.get("status") != "PASS" for row in mock_cases):
        failures.append("mock_case_results_must_contain_10_pass_rows")
    if len(raw_o) != EXPECTED_PRIMARY_COUNT or len(raw_m) != EXPECTED_PRIMARY_COUNT:
        failures.append("mock_raw_generation_row_count_drifted")

    if independence.get("status") != "PASS" or independence.get("case_count") != EXPECTED_PRIMARY_COUNT:
        failures.append("constraint_independence_audit_must_pass_10_cases")
    if independence.get("label_side_data_used_for_constraints") is not False:
        failures.append("constraint_independence_must_not_use_label_side_data")
    for row in independence.get("rows", []):
        if row.get("phase_o_label_independent") is not True or row.get("phase_m_label_independent") is not True:
            failures.append(f"constraint_independence_failed:{row.get('sample_id')}")

    if lock.get("status") != "PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT":
        failures.append("stage_lock_status_drifted")
    if lock.get("primary_acceptance") != "10/10 required; no average and no 9/10 acceptance":
        failures.append("stage_lock_primary_acceptance_drifted")
    for key, expected in {
        "primary_case_count": EXPECTED_PRIMARY_COUNT,
        "backend": "incremental_json_schema_grammar",
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "quantization": "none",
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
        "resume_allowed": False,
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
    }.items():
        if lock.get(key) != expected:
            failures.append(f"stage_lock_{key}_drifted")
    if lock.get("fresh_constrained_result_root") != f"/kaggle/working/{FRESH_CONSTRAINED_RESULT_DIR_NAME}":
        failures.append("stage_lock_fresh_result_root_drifted")
    if lock.get("frozen_runtime_versions") != FROZEN_RUNTIME_VERSIONS:
        failures.append("stage_lock_frozen_runtime_versions_drifted")
    if lock.get("allowed_frozen_runtime_profiles") != ALLOWED_FROZEN_RUNTIME_PROFILES:
        failures.append("stage_lock_allowed_frozen_runtime_profiles_drifted")
    if lock.get("primary_runtime_profile_id") != PRIMARY_RUNTIME_PROFILE_ID:
        failures.append("stage_lock_primary_runtime_profile_id_drifted")
    if lock.get("primary_runtime_profile") != runtime_profile_by_id(PRIMARY_RUNTIME_PROFILE_ID):
        failures.append("stage_lock_primary_runtime_profile_drifted")
    if lock.get("historical_runtime_profile_ids") != HISTORICAL_RUNTIME_PROFILE_IDS:
        failures.append("stage_lock_historical_runtime_profile_ids_drifted")
    if lock.get("historical_runtime_profiles") != [runtime_profile_by_id(profile_id) for profile_id in HISTORICAL_RUNTIME_PROFILE_IDS]:
        failures.append("stage_lock_historical_runtime_profiles_drifted")
    for key, expected in {
        "kaggle_requirements_lock": KAGGLE_REQUIREMENTS_LOCK,
        "single_primary_runtime_profile": True,
        "runtime_profile_switch_after_completed_generation_allowed": False,
        "gpu_topology_fail_fast_before_model_load": True,
        "device_map": "auto",
        "max_memory": None,
    }.items():
        if lock.get(key) != expected:
            failures.append(f"stage_lock_{key}_drifted")
    if lock.get("constraint_independence_audit_sha256") != sha256_file(stage_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A4.json"):
        failures.append("stage_lock_constraint_independence_hash_mismatch")

    commands = (stage_dir / "SERVER_RUN_COMMANDS.md").read_text(encoding="utf-8")
    for needle in ("Kaggle", "requirements-inference-kaggle-t4x2.lock.txt", "preflight_runtime.py", PRIMARY_RUNTIME_PROFILE_ID, "run_stage7e0_a4_english.py", "--backend constrained_hf", "--quantization none", "--phase-m-max-new-tokens 8192", FRESH_CONSTRAINED_RESULT_DIR_NAME, "Do not use `--resume`"):
        if needle not in commands:
            failures.append(f"server_command_missing:{needle}")
    if "gretel" in commands.lower() and "pilot" in commands.lower() and "open" in commands.lower():
        failures.append("server_commands_must_not_open_gretel")

    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    for item in manifest.get("artifacts", []):
        rel = item["path"]
        path = stage_dir / rel
        if not path.is_file():
            failures.append(f"manifested_artifact_missing:{rel}")
        elif item.get("sha256") != sha256_file(path):
            failures.append(f"manifest_hash_mismatch:{rel}")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("stage_lock_derived_manifest_hash_mismatch")

    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "primary_case_count": len(rows),
        "mock_primary_pass_count": mock_summary.get("primary_pass_count"),
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    args = parser.parse_args()
    report = validate(args.stage_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
