#!/usr/bin/env python3
"""Validate the Stage6H confirmation execution harness setup."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_STAGE6G_AUTHORIZATION_COMMIT = "ead5015c3efaa174772e8595b7b65a8f5c032166"
EXPECTED_FINAL_N = 481
EXPECTED_PROMPT_TOKEN_AUDIT_SHA256 = "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6"
EXPECTED_GENERATION_LOCK_SHA256 = "890da57fbe137f4b3f64e002a29d76a3c630cdc8290c924ac1d1f9da585a9c14"
EXPECTED_STREAMS = [
    "direct",
    "j_fs",
    "original_mp_fs_plus",
    "shared_mp_fs_plus_generation",
]
EXPECTED_REQUIRED_GUARDS = [
    "verify_stage6g_authorization_with_expected_git_head_and_clean_worktree",
    "reject_missing_expected_git_head_in_gpu_mode",
    "reject_dirty_worktree_override_in_gpu_mode",
    "reject_single_stream_gpu_cli_execution",
    "reject_execution_root_inside_source_checkout_in_gpu_mode",
    "initial_run_requires_absent_run_state",
    "resume_run_requires_existing_started_run_state",
    "resume_run_reuses_existing_run_id",
    "verify_zero_existing_raw_generation_files_for_initial_run",
    "verify_prompt_token_audit_file_sha256_before_parse",
    "write_and_validate_execution_code_manifest_before_model_load",
    "run_level_initial_all_stream_outputs_absent_check",
    "recompute_current_prompt_token_audit_for_stream",
    "map_shared_generation_stream_to_d_g1_control_prompt_audit_arm",
    "verify_d_g1_control_and_d_f_g1_vnext_input_identity_481_of_481",
    "verify_exact_481_unique_frozen_sample_ids_before_generation",
    "compare_prompt_chat_input_ids_and_token_count_481_of_481_before_generation",
    "pass_verified_runtime_request_objects_directly_to_generation_call",
    "tie_stage6e_final_id_set_to_stage6f_audit_and_runtime_ids",
    "execute_all_four_streams_in_fixed_order_with_one_run_id",
    "generate_one_sample_at_a_time_from_integrated_runner",
    "verify_exact_481_unique_frozen_sample_ids_after_generation",
    "verify_generated_rows_report_same_prompt_chat_input_ids_and_token_count",
    "normalize_raw_rows_to_stage6g_schema",
    "write_sample_id_and_stage6_sample_id_for_reuse_runner_compatibility",
    "preserve_generation_status_error_and_latency_fields",
    "write_raw_generation_row_sha256",
    "verify_all_raw_rows_share_the_locked_run_id",
    "write_incremental_stream_checkpoint_manifest",
    "write_and_verify_checkpoint_run_id",
    "verify_resume_checkpoint_before_any_resume",
    "write_run_state_manifest_after_each_stream",
    "retain_one_run_id_across_run_state_updates",
    "resume_run_verifies_prior_streams_before_continuation",
    "write_shared_replay_row_sha256_for_d_g1_and_d_f_g1",
    "write_actual_d_g1_and_d_f_g1_replay_provenance_after_shared_stream_completion",
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def add(violations: list[dict[str, Any]], code: str, **details: Any) -> None:
    violations.append({"code": code, **details})


def validate(harness_dir: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    lock_path = harness_dir / "STAGE6H_EXECUTION_LOCK.json"
    plan_path = harness_dir / "CONFIRMATION_EXECUTION_PLAN.json"
    if not lock_path.is_file():
        add(violations, "execution_lock_missing")
    if not plan_path.is_file():
        add(violations, "execution_plan_missing")
    if violations:
        return {"status": "FAIL", "violations": violations}

    lock = read_json(lock_path)
    plan = read_json(plan_path)
    expected_plan_hash = sha256_text(canonical_json(plan))

    expected_lock = {
        "stage": "Stage6H_CONFIRMATION_EXECUTION_HARNESS_SETUP",
        "status": "HARNESS_SETUP_LOCKED_PENDING_REVIEWER_ACCEPTANCE",
        "model_called": False,
        "gpu_called": False,
        "confirmation_predictions_created": False,
        "confirmation_run_started": False,
        "stage6g_authorization_commit": EXPECTED_STAGE6G_AUTHORIZATION_COMMIT,
        "final_confirmation_n": EXPECTED_FINAL_N,
        "execution_plan_sha256": expected_plan_hash,
        "required_harness_guards": EXPECTED_REQUIRED_GUARDS,
    }
    for key, expected in expected_lock.items():
        if lock.get(key) != expected:
            add(
                violations,
                "execution_lock_field_mismatch",
                field=key,
                expected=expected,
                actual=lock.get(key),
            )

    if plan.get("stage") != "Stage6H_CONFIRMATION_EXECUTION_HARNESS":
        add(violations, "plan_stage_mismatch", actual=plan.get("stage"))
    if plan.get("status") != "HARNESS_READY_PENDING_REVIEWER_ACCEPTANCE":
        add(violations, "plan_status_mismatch", actual=plan.get("status"))
    for field in ("model_called_in_stage6h_setup", "gpu_called_in_stage6h_setup", "confirmation_predictions_created"):
        if plan.get(field) is not False:
            add(violations, "plan_forbidden_execution_flag", field=field, actual=plan.get(field))
    if plan.get("stage6g_authorization_commit") != EXPECTED_STAGE6G_AUTHORIZATION_COMMIT:
        add(violations, "plan_stage6g_commit_mismatch")
    if plan.get("final_confirmation_n") != EXPECTED_FINAL_N:
        add(violations, "plan_final_n_mismatch")
    if plan.get("prompt_token_audit_sha256") != EXPECTED_PROMPT_TOKEN_AUDIT_SHA256:
        add(violations, "plan_prompt_audit_hash_mismatch")
    if plan.get("generation_lock_sha256") != EXPECTED_GENERATION_LOCK_SHA256:
        add(violations, "plan_generation_lock_hash_mismatch")
    if plan.get("generation_stream_order") != EXPECTED_STREAMS:
        add(violations, "plan_stream_order_mismatch")
    streams = plan.get("generation_streams") or {}
    if sorted(streams) != sorted(EXPECTED_STREAMS):
        add(violations, "plan_stream_set_mismatch", actual=sorted(streams))
    shared = streams.get("shared_mp_fs_plus_generation") or {}
    if shared.get("prompt_audit_arm") != "d_g1_control":
        add(violations, "plan_shared_prompt_audit_arm_mismatch", actual=shared.get("prompt_audit_arm"))
    if shared.get("identity_audit_arm") != "d_f_g1_vnext":
        add(violations, "plan_shared_identity_audit_arm_mismatch", actual=shared.get("identity_audit_arm"))
    if shared.get("deterministic_replay_arms") != ["d_g1_control", "d_f_g1_vnext"]:
        add(violations, "plan_shared_replay_arms_mismatch")
    modes = plan.get("execution_modes") or {}
    if sorted(modes) != ["initial", "resume"]:
        add(violations, "plan_execution_modes_mismatch", actual=sorted(modes))
    if (modes.get("initial") or {}).get("existing_raw_generation_rows_allowed") is not False:
        add(violations, "plan_initial_mode_raw_policy_mismatch")
    resume = modes.get("resume") or {}
    for key in ("existing_raw_generation_rows_allowed", "required_existing_checkpoint", "completed_rows_are_immutable", "only_unfinished_ids_may_be_generated"):
        if resume.get(key) is not True:
            add(violations, "plan_resume_mode_policy_mismatch", field=key, actual=resume.get(key))
    runtime_inputs = plan.get("runtime_input_locks") or {}
    for key in ("stage6f_prompt_token_audit", "stage6g_authorization", "final_confirmation_manifest", "final_gold_corpus", "stage6f_gpu_environment_manifest", "stage6_crudsql_isolated_db_root"):
        if key not in runtime_inputs:
            add(violations, "plan_runtime_input_lock_missing", field=key)
    if (runtime_inputs.get("stage6f_prompt_token_audit") or {}).get("sha256") != EXPECTED_PROMPT_TOKEN_AUDIT_SHA256:
        add(violations, "plan_runtime_prompt_audit_hash_mismatch")
    expected_prompt_path = (
        "stage6_gpu_preflight_acceptance/server_output_zip_extract_patch2/"
        "stage6f_gpu_preflight_patch2_outputs/stage6_gpu_preflight/PROMPT_TOKEN_AUDIT.jsonl"
    )
    if (runtime_inputs.get("stage6f_prompt_token_audit") or {}).get("path") != expected_prompt_path:
        add(violations, "plan_runtime_prompt_audit_path_mismatch")
    expected_gpu_env_path = (
        "stage6_gpu_preflight_acceptance/server_output_zip_extract_patch2/"
        "stage6f_gpu_preflight_patch2_outputs/stage6_gpu_preflight/GPU_ENVIRONMENT_MANIFEST.json"
    )
    if (runtime_inputs.get("stage6f_gpu_environment_manifest") or {}).get("path") != expected_gpu_env_path:
        add(violations, "plan_runtime_gpu_environment_path_mismatch")
    if (runtime_inputs.get("final_confirmation_manifest") or {}).get("sha256") != "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7":
        add(violations, "plan_runtime_final_manifest_hash_mismatch")
    if (runtime_inputs.get("final_gold_corpus") or {}).get("sha256") != "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16":
        add(violations, "plan_runtime_final_gold_corpus_hash_mismatch")
    controller = plan.get("run_level_controller") or {}
    expected_controller = {
        "execute_all_flag": "--execute-all",
        "resume_flag": "--resume-run",
        "fixed_stream_order": EXPECTED_STREAMS,
        "default_execution_root": "../stage6_confirmation_run_outputs",
        "single_stream_gpu_cli_allowed": False,
        "expected_git_head_required": True,
        "dirty_worktree_bypass_allowed_in_gpu_mode": False,
        "execution_root_must_be_outside_repo_in_gpu_mode": True,
        "authorization_boundary_checked_once_per_initial_run": True,
        "model_loaded_once_per_execute_all": True,
        "initial_run_state_must_be_absent": True,
        "initial_run_creates_one_run_id": True,
        "resume_requires_existing_run_state": True,
        "resume_reuses_existing_run_id": True,
        "raw_rows_require_single_run_id": True,
        "checkpoints_store_run_id": True,
        "run_state_manifest": "CONFIRMATION_RUN_STATE.json",
        "execution_code_manifest": "EXECUTION_CODE_MANIFEST.json",
    }
    if controller != expected_controller:
        add(violations, "plan_run_level_controller_mismatch", expected=expected_controller, actual=controller)
    guards = list(plan.get("pre_generation_phases") or []) + list(plan.get("post_generation_phases") or [])
    if guards != EXPECTED_REQUIRED_GUARDS:
        add(violations, "plan_required_guards_mismatch", actual=guards)

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "stage6g_authorization_commit": lock.get("stage6g_authorization_commit"),
        "final_confirmation_n": lock.get("final_confirmation_n"),
        "confirmation_predictions_created": lock.get("confirmation_predictions_created"),
        "confirmation_run_started": lock.get("confirmation_run_started"),
        "required_guard_count": len(lock.get("required_harness_guards") or []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness-dir", default="stage6_confirmation_execution")
    return parser.parse_args()


def main() -> None:
    report = validate(Path(parse_args().harness_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
