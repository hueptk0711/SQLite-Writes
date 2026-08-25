#!/usr/bin/env python3
"""Validate the Stage6G confirmation-run authorization boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


EXPECTED_STAGE6G_STATIC_FIELDS = {
    "stage": "Stage6G_CONFIRMATION_RUN_AUTHORIZATION_AND_EXECUTION_LOCK",
    "authorization_status": "AUTHORIZED_FOR_CONFIRMATORY_GENERATION",
    "status": "AUTHORIZED_FOR_CONFIRMATORY_GENERATION_PENDING_EXECUTION",
    "patch": "PATCH1_EXECUTION_BOUNDARY_HARDENING",
    "confirmation_run_allowed_now": True,
    "confirmation_predictions_created": False,
    "model_called_in_stage6g": False,
    "gpu_called_in_stage6g": False,
    "required_execution_git_head_source": "reviewer_accepted_stage6g_patch1_commit_recorded_in_GIT_INFO",
    "required_git_worktree_clean": True,
    "final_confirmation_n": 481,
    "stage6e_commit": "f32e8b2c7152e0f31829eab004da0f396084e57e",
    "stage6f_execution_commit": "dfa1be8d9db133f6adb6ec3a796c9750db67b5b9",
    "stage6f_acceptance_commit": "6cdbff685a16aff7eb9b6647937aaa7fe740ee35",
    "server_preflight_zip_sha256": "004913b1778cc145d44f32aa49a60039438b24cc36d1f17a5c85091e8cc5bd1b",
    "final_gold_corpus_sha256": "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16",
    "final_confirmation_manifest_sha256": "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7",
    "model_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "model_sha256": "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c",
    "tokenizer_sha256": "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468",
    "model_config_sha256": "326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357",
    "stage5_generation_lock_sha256": "890da57fbe137f4b3f64e002a29d76a3c630cdc8290c924ac1d1f9da585a9c14",
    "raw_generation_stream_count": 4,
    "initial_raw_generation_clean_start_required": True,
    "next_step": "run_confirmatory_generation_under_this_authorization_lock",
}

EXPECTED_STAGE6F_ACCEPTANCE_ROOTS = {
    "acceptance_lock_sha256": "bfca33214efd17a951a5bb968f998285bcd9d7fa23e50ef407a3ec59918b160e",
    "server_output_manifest_sha256": "bc002e4352063159de9326219fb5c26934fda6fae54722c2cc95fc4217499765",
    "server_preflight_lock_sha256": "1f044f58879fd5caa34449de4da77f6cdfe33325187f0497bd614bf5de119427",
    "confirmation_run_plan_sha256": "f8423d025b37ac346ea3977218b078b465184a225ba40546f8896598afe503b2",
    "model_tokenizer_asset_audit_sha256": "26b0ff29b902031457d0f60a95f482a5c900b792e18a40c79e4544c071fde84e",
    "prompt_token_audit_sha256": "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6",
}

EXPECTED_STAGE5_CONFIG_HASHES = {
    "configs/stage5/resolved_direct_confirmation.json": "0795d31926345c62d5ba832d8374c9ac067967a3842c45854a2fff9b32c9f826",
    "configs/stage5/resolved_j_fs_confirmation.json": "a4006a423eb62fd37e5b370aca48a3b9337971f49d94700703d634a3d25c0cfe",
    "configs/stage5/resolved_original_mp_fs_plus.json": "ddda333ccb9b307ed3002213dad6572daa959c2dd5deb2e7d4623cb3aeead84d",
    "configs/stage5/resolved_d_g1_control.json": "c7c9c4d54e59662ee8e251af3aea1747fa035cb306213f20c819098e96f1b6ca",
    "configs/stage5/resolved_mp_fs_plus_vnext_r1.json": "b3a946fc977c3ea95d3226dca1361b1885c098fddf4afdc650f4d36f0e1ce9bf",
}

EXPECTED_STAGE5_BOUNDARY_ROOTS = {
    "confirmation_protocol_lock_sha256": "fca32f3566ab830981f4f5b5c6e364932f79ef3c8f82881b45e4a5c19d85df3c",
    "confirmation_arm_configs_sha256": "cfa36ffb1a230c53920fd2e45356e48b45b236cbf2454fa328e470dbc5fd3682",
    "confirmation_environment_lock_sha256": "bac701451b8027a189b579c6766d690e4756a3a49458bf85f623fec1a97ba2c1",
    "generation_lock_sha256": "890da57fbe137f4b3f64e002a29d76a3c630cdc8290c924ac1d1f9da585a9c14",
    "method_source_tree_sha256": "78901f8fec28b2aa6e75166283415926da2b5af1dec48c4ba37e71be3d73d67b",
    "resolved_config_hashes": EXPECTED_STAGE5_CONFIG_HASHES,
}

EXPECTED_GENERATION_LOCK = {
    "backend": "hf",
    "framework": "transformers",
    "batch_size": 1,
    "context_length": 32768,
    "max_input_tokens": 28672,
    "max_new_tokens": 4096,
    "input_truncation_policy": "error_before_confirmation_run",
    "output_max_new_tokens_policy": (
        "record_hit_max_new_tokens_continue_evaluation_score_invalid_or_wrong_state"
        "_as_false_keep_sample_in_denominator"
    ),
    "quantization": "4bit",
    "bitsandbytes_config": {
        "load_in_4bit": True,
        "bnb_4bit_quant_type": "fp4",
        "bnb_4bit_use_double_quant": False,
        "bnb_4bit_compute_dtype": "float16",
        "bnb_4bit_quant_storage": "uint8",
    },
    "compute_dtype": "float16",
    "device_map": "auto",
    "do_sample": False,
    "temperature": None,
    "top_p": None,
    "top_k": None,
    "seed": 42,
    "trust_remote_code": False,
    "raw_generation_retry_policy": (
        "completed_success_rows_are_immutable; infrastructure_resume_only_with_lock_checks"
    ),
    "token_budget_after_failure_policy": (
        "do_not_increase_budget_after_seeing_confirmation_outputs"
    ),
}

EXPECTED_PROMPT_TOKEN_AUDIT = {
    "rows": 2405,
    "max_input_tokens": 28672,
    "max_observed_input_tokens": 2253,
    "input_truncation_error_count": 0,
}

EXPECTED_STREAMS = {
    "direct": {
        "config_arm": "direct",
        "raw_generation_path": "raw_generations/direct.jsonl",
        "role": "secondary_baseline",
    },
    "j_fs": {
        "config_arm": "j_fs",
        "raw_generation_path": "raw_generations/j_fs.jsonl",
        "role": "secondary_baseline",
    },
    "original_mp_fs_plus": {
        "config_arm": "original_mp_fs_plus",
        "raw_generation_path": "raw_generations/original_mp_fs_plus.jsonl",
        "role": "H1_comparator",
    },
    "shared_mp_fs_plus_generation": {
        "config_arm": "d_g1_control",
        "raw_generation_path": "raw_generations/shared_mp_fs_plus_generation.jsonl",
        "role": "H2_shared_raw_generation",
        "deterministic_replay_arms": ["d_g1_control", "d_f_g1_vnext"],
    },
}

EXPECTED_ABSENT_RAW_GENERATION_FILES = [
    "raw_generations/direct.jsonl",
    "raw_generations/j_fs.jsonl",
    "raw_generations/original_mp_fs_plus.jsonl",
    "raw_generations/shared_mp_fs_plus_generation.jsonl",
]

EXPECTED_EXTERNAL_HASHES = {
    "stage6_gpu_preflight_acceptance/STAGE6F_GPU_PREFLIGHT_ACCEPTANCE_LOCK.json": (
        "bfca33214efd17a951a5bb968f998285bcd9d7fa23e50ef407a3ec59918b160e"
    ),
    "stage6_gpu_preflight_acceptance/SERVER_OUTPUT_MANIFEST.json": (
        "bc002e4352063159de9326219fb5c26934fda6fae54722c2cc95fc4217499765"
    ),
    "stage6_gpu_preflight_acceptance/server_output_zip/Stage6F_GPU_PREFLIGHT_PATCH2_SERVER_OUTPUT_20260825.zip": (
        "004913b1778cc145d44f32aa49a60039438b24cc36d1f17a5c85091e8cc5bd1b"
    ),
    "stage6_final_registration_revision/artifacts/FINAL_GOLD_CORPUS.jsonl": (
        "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16"
    ),
    "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl": (
        "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7"
    ),
    "stage5_method_revision_freeze/CONFIRMATION_PROTOCOL_LOCK.json": (
        "fca32f3566ab830981f4f5b5c6e364932f79ef3c8f82881b45e4a5c19d85df3c"
    ),
    "stage5_method_revision_freeze/CONFIRMATION_ARM_CONFIGS.json": (
        "cfa36ffb1a230c53920fd2e45356e48b45b236cbf2454fa328e470dbc5fd3682"
    ),
    "stage5_method_revision_freeze/CONFIRMATION_ENVIRONMENT_LOCK.json": (
        "bac701451b8027a189b579c6766d690e4756a3a49458bf85f623fec1a97ba2c1"
    ),
    **EXPECTED_STAGE5_CONFIG_HASHES,
}

EXPECTED_STATISTICS = {
    "primary_metric": "target_state_correct",
    "paired_test": "exact_two_sided_McNemar",
    "family_correction": "Holm_over_H1_H2",
    "cluster_key": "source_group",
    "cluster_bootstrap_replicates": 10000,
    "confidence_interval": "cluster_bootstrap_percentile_95",
    "seed": 240824,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def add(violations: list[dict[str, Any]], code: str, **details: Any) -> None:
    violations.append({"code": code, **details})


def check_equal(
    violations: list[dict[str, Any]],
    code: str,
    actual: Any,
    expected: Any,
    **details: Any,
) -> None:
    if actual != expected:
        add(violations, code, actual=actual, expected=expected, **details)


def git_output(repo_root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def tree_hash(repo_root: Path, paths: Iterable[str]) -> str:
    rows = []
    for relative in sorted(paths):
        rows.append(f"{sha256_file(repo_root / relative)}  {relative}")
    return sha256_text("\n".join(rows) + "\n")


def method_source_tree_hash(repo_root: Path) -> str | None:
    manifest_path = repo_root / "stage5_method_revision_freeze" / "EXECUTABLE_FREEZE_MANIFEST.json"
    if not manifest_path.is_file():
        return None
    manifest = read_json(manifest_path)
    files = manifest.get("method_implementation_files") or []
    if any(not (repo_root / relative).is_file() for relative in files):
        return None
    return tree_hash(repo_root, files)


def validate(
    authorization_dir: Path,
    repo_root: Path | None = None,
    *,
    expected_git_head: str | None = None,
    require_git_clean: bool = False,
    check_absent_raw_generations: bool = True,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    lock_path = authorization_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    if not lock_path.is_file():
        add(violations, "authorization_lock_missing", path=str(lock_path))
        return {"status": "FAIL", "violations": violations}

    lock = read_json(lock_path)

    for key, expected in EXPECTED_STAGE6G_STATIC_FIELDS.items():
        check_equal(violations, "authorization_lock_field_mismatch", lock.get(key), expected, field=key)

    if lock.get("generation_lock") != EXPECTED_GENERATION_LOCK:
        add(violations, "generation_lock_exact_mismatch")
    generation_lock_sha = sha256_text(canonical_json(lock.get("generation_lock")))
    check_equal(
        violations,
        "generation_lock_sha256_mismatch",
        generation_lock_sha,
        EXPECTED_STAGE6G_STATIC_FIELDS["stage5_generation_lock_sha256"],
    )
    check_equal(
        violations,
        "generation_lock_temperature_mismatch",
        (lock.get("generation_lock") or {}).get("temperature"),
        None,
    )

    check_equal(
        violations,
        "stage6f_acceptance_roots_mismatch",
        lock.get("stage6f_acceptance_roots"),
        EXPECTED_STAGE6F_ACCEPTANCE_ROOTS,
    )
    check_equal(
        violations,
        "stage5_execution_boundary_roots_mismatch",
        lock.get("stage5_execution_boundary_roots"),
        EXPECTED_STAGE5_BOUNDARY_ROOTS,
    )
    check_equal(
        violations,
        "prompt_token_audit_mismatch",
        lock.get("prompt_token_audit"),
        EXPECTED_PROMPT_TOKEN_AUDIT,
    )
    check_equal(violations, "raw_generation_streams_mismatch", lock.get("raw_generation_streams"), EXPECTED_STREAMS)
    check_equal(violations, "statistics_lock_mismatch", lock.get("statistics"), EXPECTED_STATISTICS)
    check_equal(
        violations,
        "expected_absent_raw_generation_files_mismatch",
        lock.get("expected_absent_raw_generation_files_before_initial_execution"),
        EXPECTED_ABSENT_RAW_GENERATION_FILES,
    )

    invariants = lock.get("run_invariants") or {}
    for key, expected in {
        "do_not_change_prompt_or_config_after_authorization": True,
        "do_not_change_token_budget_after_authorization": True,
        "do_not_drop_failures_from_denominator": True,
        "do_not_rerun_max_token_hits_with_larger_budget": True,
        "independent_D_F_G1_generation_allowed": False,
        "F_changes_prompt_surface": False,
        "confirmation_outputs_must_be_written_under_raw_generations": True,
    }.items():
        check_equal(violations, "run_invariant_mismatch", invariants.get(key), expected, field=key)

    resume = lock.get("resume_policy") or {}
    for key in (
        "completed_success_rows_are_immutable",
        "infrastructure_resume_only",
        "verify_existing_row_hashes_before_resume",
        "do_not_regenerate_completed_rows",
        "do_not_regenerate_semantic_or_model_failures",
        "do_not_change_config_prompt_model_or_token_budget",
    ):
        check_equal(violations, "resume_policy_mismatch", resume.get(key), True, field=key)
    check_equal(
        violations,
        "resume_policy_string_mismatch",
        resume.get("policy"),
        EXPECTED_GENERATION_LOCK["raw_generation_retry_policy"],
    )

    prompt_guard = lock.get("runtime_prompt_input_identity_guard") or {}
    check_equal(
        violations,
        "runtime_prompt_guard_audit_hash_mismatch",
        prompt_guard.get("stage6f_prompt_token_audit_sha256"),
        EXPECTED_STAGE6F_ACCEPTANCE_ROOTS["prompt_token_audit_sha256"],
    )
    required_prompt_checks = [
        "stage6_sample_id",
        "generation_stream",
        "prompt_sha256",
        "chat_prompt_sha256",
        "input_ids_sha256",
        "input_token_count",
    ]
    check_equal(
        violations,
        "runtime_prompt_guard_fields_mismatch",
        prompt_guard.get("required_per_row_checks_before_generate"),
        required_prompt_checks,
    )
    check_equal(
        violations,
        "runtime_prompt_guard_on_mismatch_mismatch",
        prompt_guard.get("on_mismatch"),
        "stop_run_before_generation_for_that_sample",
    )

    raw_fields = set(lock.get("required_raw_generation_row_fields") or [])
    for field in {
        "stage6_sample_id",
        "generation_stream",
        "prompt_sha256",
        "chat_prompt_sha256",
        "input_ids_sha256",
        "input_token_count",
        "raw_output",
        "raw_output_sha256",
        "output_token_count",
        "hit_max_new_tokens",
        "model_revision",
        "model_sha256",
        "tokenizer_sha256",
        "generation_lock_sha256",
        "run_id",
    }:
        if field not in raw_fields:
            add(violations, "required_raw_generation_row_field_missing", field=field)

    replay_fields = set(lock.get("required_replay_provenance_fields") or [])
    for field in {
        "stage6_sample_id",
        "replay_arm",
        "source_raw_generation_stream",
        "shared_raw_generation_row_sha256",
    }:
        if field not in replay_fields:
            add(violations, "required_replay_provenance_field_missing", field=field)

    if repo_root is not None:
        for relative, expected in EXPECTED_EXTERNAL_HASHES.items():
            path = repo_root / relative
            if not path.is_file():
                add(violations, "external_anchor_missing", path=relative)
                continue
            actual = sha256_file(path)
            if actual != expected:
                add(
                    violations,
                    "external_anchor_hash_mismatch",
                    path=relative,
                    expected=expected,
                    actual=actual,
                )

        actual_tree_hash = method_source_tree_hash(repo_root)
        check_equal(
            violations,
            "method_source_tree_sha256_mismatch",
            actual_tree_hash,
            EXPECTED_STAGE5_BOUNDARY_ROOTS["method_source_tree_sha256"],
        )

        if check_absent_raw_generations:
            existing = [
                relative
                for relative in EXPECTED_ABSENT_RAW_GENERATION_FILES
                if (repo_root / relative).exists()
            ]
            if existing:
                add(violations, "preexisting_raw_generation_files", files=existing)

        if expected_git_head:
            actual_head = git_output(repo_root, "rev-parse", "HEAD")
            check_equal(
                violations,
                "git_head_mismatch",
                actual_head,
                expected_git_head,
            )
        if require_git_clean:
            status = git_output(repo_root, "status", "--porcelain", "--", ":!reviewer_packages")
            check_equal(violations, "git_status_not_clean", status, "")

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "authorization_status": lock.get("authorization_status"),
        "confirmation_run_allowed_now": lock.get("confirmation_run_allowed_now"),
        "confirmation_predictions_created": lock.get("confirmation_predictions_created"),
        "final_confirmation_n": lock.get("final_confirmation_n"),
        "raw_generation_stream_count": lock.get("raw_generation_stream_count"),
        "generation_lock_temperature": (lock.get("generation_lock") or {}).get("temperature"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization-dir", default="stage6_confirmation_run_authorization")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--skip-external-anchor-checks", action="store_true")
    parser.add_argument("--expected-git-head")
    parser.add_argument("--require-git-clean", action="store_true")
    parser.add_argument("--allow-existing-raw-generations", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = None if args.skip_external_anchor_checks else Path(args.repo_root)
    report = validate(
        Path(args.authorization_dir),
        repo_root=repo_root,
        expected_git_head=args.expected_git_head,
        require_git_clean=args.require_git_clean,
        check_absent_raw_generations=not args.allow_existing_raw_generations,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
