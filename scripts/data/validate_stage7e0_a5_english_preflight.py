#!/usr/bin/env python3
"""Validate Stage7E0-A5 one-call column-conditioned preflight artifacts."""

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

from scripts.data.build_stage7e0_a5_english_preflight import (  # noqa: E402
    PRIMARY_RESULT_DIR_NAME,
    SERVER_REQUIREMENTS_LOCK,
    SERVER_WORK_ROOT,
)
from scripts.data.validate_stage7c_a5_column_conditioned_phase_o_protocol import validate as validate_stage7c_a5  # noqa: E402
from scripts.server.run_stage7e0_a5_english import (  # noqa: E402
    A5_PROMPT_SPEC_REL,
    ALLOWED_FROZEN_RUNTIME_PROFILES,
    CONSTRAINED_BACKEND_ID,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    EXPECTED_PRIMARY_COUNT,
    FROZEN_RUNTIME_VERSIONS,
    HISTORICAL_RUNTIME_PROFILE_IDS,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_MAX_NEW_TOKENS,
    PRIMARY_RUNTIME_PROFILE_ID,
    STAGE7C_A5_DIR,
    STAGE_NAME,
    build_phase_o_column_conditioned_constraint_grammar,
    load_stage7c_a5_rows,
    render_phase_o_messages,
    runtime_profile_by_id,
    sha256_file,
)
from scripts.data.build_stage7c_a5_column_conditioned_phase_o_protocol import canonical_json, sha256_text  # noqa: E402


REQUIRED_FILES = {
    "STAGE7E0_A5_INPUT_MANIFEST.json",
    "RUNNER_PROTOCOL_A5.json",
    "PRIMARY_ACCEPTANCE_POLICY_A5.json",
    "CONSTRAINT_INDEPENDENCE_AUDIT_A5.json",
    "SERVER_RUN_COMMANDS.md",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7E0_A5_LOCK.json",
    "mock_dry_run/run_manifest.json",
    "mock_dry_run/primary_summary.json",
    "mock_dry_run/primary_case_results.jsonl",
    "mock_dry_run/raw_primary_phase_o_generations.jsonl",
}
PACKAGE_ROOT_REQUIRED_FILES = {
    SERVER_REQUIREMENTS_LOCK,
    "scripts/server/preflight_runtime_stage7e0_a5.py",
    "scripts/server/run_stage7e0_a5_english.py",
    "scripts/data/validate_stage7e0_a5_server_results.py",
}


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

    upstream = validate_stage7c_a5(PROJECT_ROOT / STAGE7C_A5_DIR)
    if upstream.get("status") != "PASS":
        failures.append("stage7c_a5_upstream_validation_failed")

    protocol = read_json(stage_dir / "RUNNER_PROTOCOL_A5.json")
    inputs = read_json(stage_dir / "STAGE7E0_A5_INPUT_MANIFEST.json")
    policy = read_json(stage_dir / "PRIMARY_ACCEPTANCE_POLICY_A5.json")
    independence = read_json(stage_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A5.json")
    lock = read_json(stage_dir / "STAGE7E0_A5_LOCK.json")
    mock_summary = read_json(stage_dir / "mock_dry_run" / "primary_summary.json")
    mock_cases = read_jsonl(stage_dir / "mock_dry_run" / "primary_case_results.jsonl")
    raw_o = read_jsonl(stage_dir / "mock_dry_run" / "raw_primary_phase_o_generations.jsonl")

    model = protocol.get("model", {})
    if model.get("model_id") != MODEL_ID or model.get("model_revision") != MODEL_REVISION:
        failures.append("runner_protocol_model_identity_drifted")
    if model.get("default_model_path") != DEFAULT_MODEL_PATH:
        failures.append("runner_protocol_model_path_drifted")
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
    if model.get("server_requirements_lock") != SERVER_REQUIREMENTS_LOCK:
        failures.append("server_requirements_lock_drifted")

    prompt_contract = protocol.get("prompt_contract", {})
    if prompt_contract.get("phase_o_prompt_spec_path") != A5_PROMPT_SPEC_REL:
        failures.append("phase_o_prompt_spec_path_drifted")
    if prompt_contract.get("phase_o_output_keys") != ["operation", "table_ref", "column_span_refs"]:
        failures.append("phase_o_output_keys_drifted")
    if prompt_contract.get("column_span_refs_mapping_equality") != "order_insensitive_by_object_key":
        failures.append("mapping_equality_not_frozen")
    if prompt_contract.get("duplicate_non_omit_span_reuse") != "method_failure":
        failures.append("duplicate_span_policy_drifted")
    if prompt_contract.get("phase_m_removed") is not True:
        failures.append("phase_m_removed_contract_drifted")

    generation = protocol.get("generation_contract", {})
    expected_generation = {
        "calls_per_primary_case": 1,
        "phase_o_calls": 1,
        "phase_m_calls": 0,
        "retry": 0,
        "repair": "none",
        "backend": CONSTRAINED_BACKEND_ID,
        "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "resume_allowed": False,
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "single_primary_runtime_profile": True,
        "runtime_profile_switch_after_completed_generation_allowed": False,
        "gpu_topology_fail_fast_before_model_load": True,
    }
    for key, expected in expected_generation.items():
        if generation.get(key) != expected:
            failures.append(f"generation_contract_{key}_drifted")
    if "do not pass --resume" not in generation.get("interrupted_run_policy", ""):
        failures.append("interrupted_run_policy_must_forbid_resume")
    completed_failure_policy = generation.get("completed_scientific_primary_failure_policy", "")
    if not all(needle in completed_failure_policy for needle in ("exits 0", "validate", "archive", "hash")):
        failures.append("completed_scientific_primary_failure_policy_must_preserve_result")

    if policy.get("required_pass_count") != "12/12":
        failures.append("primary_acceptance_must_require_12_of_12")
    if policy.get("eleven_of_twelve_allowed") is not False or policy.get("averaging_allowed") is not False:
        failures.append("primary_acceptance_must_forbid_11_of_12_and_averaging")
    if policy.get("primary_before_diagnostics") is not True or policy.get("diagnostics_can_compensate_primary_failure") is not False:
        failures.append("primary_diagnostic_order_policy_drifted")

    if inputs.get("phase_o_prompt_spec_path") != A5_PROMPT_SPEC_REL:
        failures.append("input_manifest_prompt_path_drifted")
    if inputs.get("primary_case_count") != EXPECTED_PRIMARY_COUNT:
        failures.append("input_manifest_primary_case_count_drifted")
    if inputs.get("phase_o_prompt_spec_sha256") != sha256_file(PROJECT_ROOT / A5_PROMPT_SPEC_REL):
        failures.append("input_manifest_prompt_hash_mismatch")
    if inputs.get("primary_set_sha256") != sha256_file(PROJECT_ROOT / STAGE7C_A5_DIR / "FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl"):
        failures.append("input_manifest_primary_set_hash_mismatch")
    if inputs.get("tokenizer_status") != "PASS" or inputs.get("chat_template_sha256") != EXPECTED_CHAT_TEMPLATE_SHA256:
        failures.append("input_manifest_tokenizer_lock_drifted")

    rows = load_stage7c_a5_rows(PROJECT_ROOT)
    if [row["sample_id"] for row in rows] != inputs.get("primary_case_ids"):
        failures.append("primary_case_order_drifted")
    messages, _user, digest = render_phase_o_messages(rows[0])
    if len(digest) != 64 or "Candidate span inventory:" not in messages[1]["content"] or "SPAN_" not in messages[1]["content"]:
        failures.append("rendered_phase_o_prompt_missing_candidate_inventory")
    if "start_char" in messages[1]["content"] or "end_char" in messages[1]["content"]:
        failures.append("rendered_phase_o_prompt_exposes_offsets")
    grammar = build_phase_o_column_conditioned_constraint_grammar(rows[0]["runtime_constraints"]["phase_o_schema"])
    if not grammar.is_complete(canonical_json(rows[0]["label_side_expected"]["phase_o"])):
        failures.append("phase_o_constraint_grammar_does_not_accept_gold")
    metadata = grammar.metadata()
    if metadata["label_side_data_used_for_constraints"] is not False or metadata["finite_complete_object_enumeration"] is not False:
        failures.append("constraint_grammar_metadata_drifted")

    if mock_summary.get("backend") != "mock" or mock_summary.get("status") != "PASS":
        failures.append("mock_dry_run_must_pass")
    if mock_summary.get("primary_pass_count") != "12/12":
        failures.append("mock_dry_run_must_prove_12_of_12_wiring")
    if mock_summary.get("phase_m_removed") is not True:
        failures.append("mock_dry_run_must_remove_phase_m")
    if mock_summary.get("model_called") is not False or mock_summary.get("gpu_called") is not False:
        failures.append("mock_dry_run_must_not_call_model_or_gpu")
    if mock_summary.get("mock_uses_label_side_expected") is not True:
        failures.append("mock_dry_run_must_disclose_label_side_use")
    if len(mock_cases) != EXPECTED_PRIMARY_COUNT or any(row.get("status") != "PASS" for row in mock_cases):
        failures.append("mock_case_results_must_contain_12_pass_rows")
    if len(raw_o) != EXPECTED_PRIMARY_COUNT:
        failures.append("mock_raw_generation_row_count_drifted")

    if independence.get("status") != "PASS" or independence.get("case_count") != EXPECTED_PRIMARY_COUNT:
        failures.append("constraint_independence_audit_must_pass_12_cases")
    if independence.get("label_side_data_used_for_constraints") is not False:
        failures.append("constraint_independence_must_not_use_label_side_data")
    for row in independence.get("rows", []):
        if row.get("label_independent") is not True:
            failures.append(f"constraint_independence_failed:{row.get('sample_id')}")

    if lock.get("status") != "PASS_READY_FOR_REAL_A5_CONSTRAINED_PREFLIGHT":
        failures.append("stage_lock_status_drifted")
    if lock.get("primary_acceptance") != "12/12 required; no average and no 11/12 acceptance":
        failures.append("stage_lock_primary_acceptance_drifted")
    for key, expected in {
        "primary_case_count": EXPECTED_PRIMARY_COUNT,
        "diagnostic_case_count": 12,
        "phase_m_removed": True,
        "calls_per_primary_case": 1,
        "backend": CONSTRAINED_BACKEND_ID,
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "quantization": "none",
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "resume_allowed": False,
        "primary_before_diagnostics": True,
        "diagnostics_can_compensate_primary_failure": False,
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
    }.items():
        if lock.get(key) != expected:
            failures.append(f"stage_lock_{key}_drifted")
    if lock.get("primary_result_root") != f"{SERVER_WORK_ROOT}/{PRIMARY_RESULT_DIR_NAME}":
        failures.append("stage_lock_primary_result_root_drifted")
    if lock.get("frozen_runtime_versions") != FROZEN_RUNTIME_VERSIONS:
        failures.append("stage_lock_frozen_runtime_versions_drifted")
    if lock.get("allowed_frozen_runtime_profiles") != ALLOWED_FROZEN_RUNTIME_PROFILES:
        failures.append("stage_lock_allowed_frozen_runtime_profiles_drifted")
    if lock.get("constraint_independence_audit_sha256") != sha256_file(stage_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A5.json"):
        failures.append("stage_lock_constraint_independence_hash_mismatch")

    commands = (stage_dir / "SERVER_RUN_COMMANDS.md").read_text(encoding="utf-8")
    for needle in ("UET", SERVER_REQUIREMENTS_LOCK, "CUDA_VISIBLE_DEVICES=0", "preflight_runtime_stage7e0_a5.py", PRIMARY_RUNTIME_PROFILE_ID, "run_stage7e0_a5_english.py", "--backend constrained_hf", "--quantization none", "--phase-o-max-new-tokens 512", PRIMARY_RESULT_DIR_NAME, "Do not use `--resume`", "less than 12/12", "validate", "archive", "sha256"):
        if needle not in commands:
            failures.append(f"server_command_missing:{needle}")
    primary_pos = commands.find(PRIMARY_RESULT_DIR_NAME)
    if primary_pos < 0:
        failures.append("server_commands_missing_primary_result_root")
    run_pos = commands.find("python scripts/server/run_stage7e0_a5_english.py")
    validate_pos = commands.find("python scripts/data/validate_stage7e0_a5_server_results.py")
    tar_pos = commands.find("tar -czf")
    sha_pos = commands.find("sha256sum")
    if not (0 <= run_pos < validate_pos < tar_pos < sha_pos):
        failures.append("server_commands_must_run_validate_archive_hash_after_primary_runner")
    if "--run-diagnostics-after-primary-pass" in commands:
        failures.append("server_commands_must_not_include_diagnostic_run_in_primary_preflight")
    commands_lower = commands.lower()
    if not all(needle in commands_lower for needle in ("diagnostics", "not part of this primary preflight", "frozen and reviewed", "12/12")):
        failures.append("server_commands_must_defer_diagnostics_until_primary_review")

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
