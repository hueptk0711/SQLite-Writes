#!/usr/bin/env python3
"""Validate the Stage 5 revised-method executable freeze package.

The validator is CPU-only. It rejects drift in the overlay config, resolved
effective config, comparator resolved configs, environment lock, base configs,
demonstration bank, protocol lock, and selected method implementation files. It
also enforces the Stage 5 confirmation arms, hypotheses, token policy, and
Stage 4 lifecycle boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE5_ROOT = PROJECT_ROOT / "stage5_method_revision_freeze"
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "stage5" / "mp_fs_plus_vnext_r1.json"
DEFAULT_RESOLVED_CONFIG = (
    PROJECT_ROOT / "configs" / "stage5" / "resolved_mp_fs_plus_vnext_r1.json"
)
DEFAULT_EXECUTABLE_MANIFEST = (
    DEFAULT_STAGE5_ROOT / "EXECUTABLE_FREEZE_MANIFEST.json"
)
DEFAULT_ARM_CONFIGS = DEFAULT_STAGE5_ROOT / "CONFIRMATION_ARM_CONFIGS.json"
DEFAULT_ENVIRONMENT_LOCK = DEFAULT_STAGE5_ROOT / "CONFIRMATION_ENVIRONMENT_LOCK.json"

EXPECTED_COMPONENT_SET = ["D", "F", "G1"]
EXPECTED_METHOD_NAME = "MP-FS+ vNext-R1"
EXPECTED_METHOD_VARIANT = "mp-fs-plus-vnext-r1"
EXPECTED_METHOD_FREEZE_TAG = "stage5-vnext-r1-freeze-patch1"
EXPECTED_METHOD_FREEZE_COMMIT = "79f6a82144ec0407444ef37121f70eed2b20e01c"
EXPECTED_PROTOCOL_TAG = "stage5-vnext-r1-freeze-patch2"
EXPECTED_ARM_CONFIGS_PATH = "stage5_method_revision_freeze/CONFIRMATION_ARM_CONFIGS.json"
EXPECTED_ENVIRONMENT_LOCK_PATH = "stage5_method_revision_freeze/CONFIRMATION_ENVIRONMENT_LOCK.json"
EXPECTED_MODEL_LOCK = {
    "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "snapshot_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "tokenizer_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "aggregate_sha256": "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c",
    "tokenizer_sha256": "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468",
    "model_config_sha256": "326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357",
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
EXPECTED_ARM_CONFIG_HASHES = {
    "configs/stage5/resolved_direct_confirmation.json": "0795d31926345c62d5ba832d8374c9ac067967a3842c45854a2fff9b32c9f826",
    "configs/stage5/resolved_j_fs_confirmation.json": "a4006a423eb62fd37e5b370aca48a3b9337971f49d94700703d634a3d25c0cfe",
    "configs/stage5/resolved_original_mp_fs_plus.json": "ddda333ccb9b307ed3002213dad6572daa959c2dd5deb2e7d4623cb3aeead84d",
    "configs/stage5/resolved_d_g1_control.json": "c7c9c4d54e59662ee8e251af3aea1747fa035cb306213f20c819098e96f1b6ca",
    "configs/stage5/resolved_mp_fs_plus_vnext_r1.json": "b3a946fc977c3ea95d3226dca1361b1885c098fddf4afdc650f4d36f0e1ce9bf",
}
EXPECTED_ENVIRONMENT_FILE_HASHES = {
    "requirements-inference.lock.txt": "861a24b179b5edd1245aba33109402dd4ab82a634098bd8d81fcb666f5bdf9f1",
    "stage4_fresh_7b_protocol/provenance/environment_lock.txt": "474c55042187944d02a5bd4511858786195e0af962750097d405ce316e5c4f20",
    "stage4_fresh_7b_protocol/inference/stage4_qwen25_7b_in28672_out4096.json": "c7f43df89536ac412b73092c78d2cc251084f090dc0790cbe839a4248f6b5e16",
}
EXPECTED_ENVIRONMENT_PREFLIGHT = {
    "python_major_minor": "3.12",
    "torch": "2.6.0+cu124",
    "transformers": "5.5.3",
    "accelerate": "1.14.0",
    "bitsandbytes": "0.47.0",
    "tokenizers": "0.22.2",
    "safetensors": "0.5.3",
    "cuda_wheel_index": "https://download.pytorch.org/whl/cu124",
    "sqlite_runtime_must_be_captured": True,
    "nvidia_driver_must_be_captured": True,
    "gpu_environment_capture_required_before_generation": True,
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Return a stable SHA-256 for repository text artifacts.

    Stage 5 runs on both Windows and Linux. The repository may check text files
    out with CRLF on Windows, so executable-freeze hashes normalize text line
    endings to LF before hashing. Package-level ZIP hashes remain raw-byte
    hashes and are reported separately.
    """

    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def tree_hash(paths: Iterable[Path]) -> str:
    rows = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        rows.append(f"{sha256_file(path)}  {relative}")
    return sha256_text("\n".join(rows) + "\n")


def add_violation(violations: list[dict[str, Any]], rule: str, **details: Any) -> None:
    row: dict[str, Any] = {"rule": rule}
    row.update(details)
    violations.append(row)


def expect_equal(
    violations: list[dict[str, Any]],
    rule: str,
    actual: Any,
    expected: Any,
) -> None:
    if actual != expected:
        add_violation(violations, rule, actual=actual, expected=expected)


def method_parameter_contract() -> dict[str, Any]:
    return {
        "method_id": "MP-FS+",
        "method_variant": EXPECTED_METHOD_VARIANT,
        "method_version": EXPECTED_METHOD_NAME,
        "stage2_interventions": {
            "control_field_roles": False,
            "explicit_conflict_preservation": False,
            "update_column_consistency": False,
        },
        "structured_source_parser": {
            "enabled": True,
            "null_literal_policy": "explicit_only",
            "emit_value_provenance": True,
        },
        "free_text_typed_normalization": {"enabled": False},
        "constrained_reference_repair": {
            "enabled": True,
            "exact_name": True,
            "singleton": True,
            "max_revalidation_attempts": 1,
            "emit_repair_provenance": True,
        },
        "diagnostic_targeted_repair": {
            "enabled": True,
            "evidence_span_boundary": True,
            "evidence_span_selection": False,
            "allowed_terminal_punctuation": [".", ","],
            "max_revalidation_attempts": 1,
            "require_deterministic_diagnostic": True,
            "require_single_diagnosed_slot": True,
            "require_unique_candidate": True,
            "preserve_other_semantics": True,
            "emit_repair_provenance": True,
        },
    }


def validate_config_exact(
    config: dict[str, Any],
    *,
    resolved: bool,
    violations: list[dict[str, Any]],
) -> None:
    for key, expected in method_parameter_contract().items():
        expect_equal(violations, f"config_exact_{key}", config.get(key), expected)
    if resolved:
        if "base_config" in config:
            add_violation(violations, "resolved_config_must_not_have_base_config")
        if "demonstration_bank" in config:
            add_violation(violations, "resolved_config_must_not_load_demo_bank_dynamically")
        demonstrations = config.get("demonstrations") or {}
        expect_equal(
            violations,
            "resolved_demo_count_semi_structured",
            len(demonstrations.get("semi_structured") or []),
            4,
        )
        expect_equal(
            violations,
            "resolved_demo_count_free_text",
            len(demonstrations.get("free_text") or []),
            2,
        )
        expect_equal(
            violations,
            "resolved_demo_ids",
            config.get("resolved_demonstration_ids"),
            {
                "semi_structured": [
                    "semi_plain_insert",
                    "semi_insert_ignore",
                    "semi_upsert_update",
                    "semi_parent_child",
                ],
                "free_text": [
                    "free_plain_insert",
                    "free_conflict_aware",
                ],
            },
        )


def validate_resolved_comparator_config(
    path: Path,
    *,
    role: str,
    violations: list[dict[str, Any]],
) -> None:
    if not path.is_file():
        add_violation(
            violations,
            "resolved_comparator_config_missing",
            role=role,
            path=path.relative_to(PROJECT_ROOT).as_posix(),
        )
        return
    config = read_json(path)
    if "base_config" in config:
        add_violation(violations, "comparator_must_not_have_base_config", role=role)
    if "demonstration_bank" in config:
        add_violation(violations, "comparator_must_not_load_demo_bank_dynamically", role=role)
    demonstrations = config.get("demonstrations") or {}
    expect_equal(
        violations,
        f"{role}_demo_count_semi_structured",
        len(demonstrations.get("semi_structured") or []),
        4,
    )
    expect_equal(
        violations,
        f"{role}_demo_count_free_text",
        len(demonstrations.get("free_text") or []),
        2,
    )


def validate_confirmation_arm_configs(
    arm_configs: dict[str, Any],
    lock: dict[str, Any],
    violations: list[dict[str, Any]],
) -> None:
    expect_equal(
        violations,
        "protocol_arm_config_lock_path",
        lock.get("confirmation_arm_config_lock"),
        EXPECTED_ARM_CONFIGS_PATH,
    )
    expect_equal(
        violations,
        "arm_config_method_freeze_commit",
        arm_configs.get("accepted_method_freeze_commit"),
        EXPECTED_METHOD_FREEZE_COMMIT,
    )
    expect_equal(
        violations,
        "arm_config_method_freeze_tag",
        arm_configs.get("accepted_method_freeze_tag"),
        EXPECTED_METHOD_FREEZE_TAG,
    )
    policy = arm_configs.get("confirmation_run_policy") or {}
    expect_equal(violations, "arm_config_source_policy", policy.get("configuration_source"), "resolved_configs_only")
    expect_equal(
        violations,
        "arm_dynamic_base_resolution_allowed",
        policy.get("dynamic_base_config_resolution_allowed"),
        False,
    )
    expect_equal(
        violations,
        "arm_dynamic_demo_bank_loading_allowed",
        policy.get("dynamic_demonstration_bank_loading_allowed"),
        False,
    )

    arms = arm_configs.get("executable_arms") or {}
    expect_equal(
        violations,
        "arm_config_names",
        sorted(arms),
        ["direct", "j_fs", "original_mp_fs_plus", "shared_mp_fs_plus_generation"],
    )
    expected_generation = {
        "direct": ("configs/stage5/resolved_direct_confirmation.json", "raw_generations/direct.jsonl"),
        "j_fs": ("configs/stage5/resolved_j_fs_confirmation.json", "raw_generations/j_fs.jsonl"),
        "original_mp_fs_plus": (
            "configs/stage5/resolved_original_mp_fs_plus.json",
            "raw_generations/original_mp_fs_plus.jsonl",
        ),
        "shared_mp_fs_plus_generation": (
            "configs/stage5/resolved_d_g1_control.json",
            "raw_generations/shared_mp_fs_plus_generation.jsonl",
        ),
    }
    for arm_name, (config_path, raw_path) in expected_generation.items():
        arm = arms.get(arm_name) or {}
        expect_equal(
            violations,
            f"{arm_name}_generation_config_path",
            arm.get("generation_config_path"),
            config_path,
        )
        expect_equal(
            violations,
            f"{arm_name}_generation_config_sha256",
            arm.get("generation_config_sha256"),
            EXPECTED_ARM_CONFIG_HASHES[config_path],
        )
        expect_equal(
            violations,
            f"{arm_name}_raw_generation_file",
            arm.get("raw_generation_file"),
            raw_path,
        )
        path = PROJECT_ROOT / config_path
        if path.is_file():
            expect_equal(
                violations,
                f"{arm_name}_actual_config_sha256",
                sha256_file(path),
                EXPECTED_ARM_CONFIG_HASHES[config_path],
            )
            validate_resolved_comparator_config(path, role=arm_name, violations=violations)

    shared = arms.get("shared_mp_fs_plus_generation") or {}
    replays = shared.get("deterministic_replays") or []
    expect_equal(
        violations,
        "shared_generation_replay_slugs",
        [row.get("method_slug") for row in replays],
        ["D_G1", "D_F_G1"],
    )
    replay_by_slug = {row.get("method_slug"): row for row in replays}
    expected_replays = {
        "D_G1": "configs/stage5/resolved_d_g1_control.json",
        "D_F_G1": "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
    }
    for slug, config_path in expected_replays.items():
        replay = replay_by_slug.get(slug) or {}
        expect_equal(violations, f"{slug}_replay_config_path", replay.get("replay_config_path"), config_path)
        expect_equal(
            violations,
            f"{slug}_replay_config_sha256",
            replay.get("replay_config_sha256"),
            EXPECTED_ARM_CONFIG_HASHES[config_path],
        )
    forbidden = set(arm_configs.get("forbidden_confirmation_generation_arms") or [])
    for arm_name in ["d_g1_shared_mp_fs_plus", "d_f_g1_shared_mp_fs_plus"]:
        if arm_name in arms:
            add_violation(violations, "forbidden_separate_shared_generation_arm_present", arm=arm_name)
        if arm_name not in forbidden:
            add_violation(violations, "forbidden_shared_generation_arm_not_declared", arm=arm_name)

    h2 = arm_configs.get("H2_identity_requirement") or {}
    expect_equal(
        violations,
        "H2_raw_generation_file",
        h2.get("raw_generation_file"),
        "raw_generations/shared_mp_fs_plus_generation.jsonl",
    )
    expect_equal(
        violations,
        "H2_independent_D_F_G1_generation_allowed",
        h2.get("independent_D_F_G1_generation_allowed"),
        False,
    )
    expect_equal(violations, "H2_F_changes_prompt_surface", h2.get("F_changes_prompt_surface"), False)


def validate_environment_lock(
    environment_lock: dict[str, Any],
    lock: dict[str, Any],
    violations: list[dict[str, Any]],
) -> None:
    expect_equal(
        violations,
        "protocol_environment_lock_path",
        lock.get("confirmation_environment_lock"),
        EXPECTED_ENVIRONMENT_LOCK_PATH,
    )
    expect_equal(
        violations,
        "environment_method_freeze_commit",
        environment_lock.get("accepted_method_freeze_commit"),
        EXPECTED_METHOD_FREEZE_COMMIT,
    )
    expect_equal(
        violations,
        "environment_reuse_policy",
        environment_lock.get("reuse_policy"),
        "reuse_exact_Stage4_validated_dependency_and_inference_locks_before_any_confirmation_gpu_generation",
    )
    expect_equal(
        violations,
        "environment_required_preflight",
        environment_lock.get("required_preflight"),
        EXPECTED_ENVIRONMENT_PREFLIGHT,
    )
    expect_equal(
        violations,
        "environment_anchored_file_hashes",
        environment_lock.get("anchored_files_sha256"),
        EXPECTED_ENVIRONMENT_FILE_HASHES,
    )
    for relative, expected in EXPECTED_ENVIRONMENT_FILE_HASHES.items():
        path = PROJECT_ROOT / relative
        if not path.is_file():
            add_violation(violations, "environment_anchor_file_missing", path=relative)
            continue
        expect_equal(
            violations,
            "environment_anchor_file_sha256",
            sha256_file(path),
            expected,
        )
    preflight = environment_lock.get("confirmation_preflight_policy") or {}
    expect_equal(
        violations,
        "environment_preflight_clean_tree",
        preflight.get("run_from_clean_git_worktree"),
        True,
    )
    expect_equal(
        violations,
        "environment_preflight_method_commit",
        preflight.get("accepted_method_freeze_commit"),
        EXPECTED_METHOD_FREEZE_COMMIT,
    )
    expect_equal(
        violations,
        "environment_preflight_protocol_commit_recording",
        preflight.get("confirmation_protocol_patch_commit_must_be_recorded_in_run_manifest"),
        True,
    )


def validate_manifest(
    manifest: dict[str, Any],
    executable_manifest: dict[str, Any],
    executable_manifest_path: Path,
    violations: list[dict[str, Any]],
) -> None:
    expect_equal(violations, "manifest_model_called", manifest.get("model_called"), False)
    expect_equal(violations, "manifest_gpu_called", manifest.get("gpu_called"), False)
    expect_equal(
        violations,
        "manifest_new_evaluation_results",
        manifest.get("new_evaluation_results"),
        False,
    )
    method = manifest.get("final_method") or {}
    expect_equal(violations, "manifest_method_name", method.get("method_name"), EXPECTED_METHOD_NAME)
    expect_equal(
        violations,
        "manifest_method_variant",
        method.get("method_variant"),
        EXPECTED_METHOD_VARIANT,
    )
    expect_equal(
        violations,
        "manifest_component_set",
        method.get("component_set"),
        EXPECTED_COMPONENT_SET,
    )
    expect_equal(
        violations,
        "manifest_resolved_config_path",
        method.get("resolved_config_path"),
        "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
    )
    evidence = manifest.get("source_evidence") or {}
    expect_equal(
        violations,
        "stage4_must_be_diagnostic_not_confirmatory",
        evidence.get("stage4_interpretation"),
        "diagnostic_used_not_confirmatory_for_D_F_G1",
    )
    if "post-hoc Stage 4" not in str(evidence.get("F") or ""):
        add_violation(violations, "F_lifecycle_must_be_post_hoc_stage4")
    expect_equal(
        violations,
        "manifest_executable_manifest_sha256",
        manifest.get("executable_freeze_manifest_sha256"),
        sha256_file(executable_manifest_path) if executable_manifest_path.is_file() else None,
    )


def validate_protocol_lock(lock: dict[str, Any], violations: list[dict[str, Any]]) -> None:
    expect_equal(
        violations,
        "confirmation_must_be_blocked",
        lock.get("status"),
        "blocked_until_new_untouched_dataset_registered_and_reviewer_accepted",
    )
    expect_equal(
        violations,
        "accepted_executable_tag",
        lock.get("accepted_executable_tag"),
        EXPECTED_METHOD_FREEZE_TAG,
    )
    expect_equal(
        violations,
        "accepted_method_freeze_commit",
        lock.get("accepted_method_freeze_commit"),
        EXPECTED_METHOD_FREEZE_COMMIT,
    )
    method = lock.get("method_under_test") or {}
    expect_equal(violations, "lock_component_set", method.get("component_set"), EXPECTED_COMPONENT_SET)
    expect_equal(
        violations,
        "lock_resolved_config_path",
        method.get("resolved_config_path"),
        "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
    )
    expect_equal(
        violations,
        "confirmation_config_policy",
        method.get("confirmation_config_policy"),
        "use_resolved_config_directly",
    )
    lifecycle = lock.get("lifecycle_boundary") or {}
    expect_equal(
        violations,
        "lock_F_lifecycle",
        lifecycle.get("F"),
        "promoted_after_post_hoc_stage4_failure_analysis",
    )
    expect_equal(
        violations,
        "lock_stage4_role",
        lifecycle.get("stage4_role_after_stage5"),
        "diagnostic_used_not_confirmatory",
    )

    arms = lock.get("confirmation_arms") or {}
    expect_equal(
        violations,
        "included_generation_arms",
        arms.get("included_generation_arms"),
        [
            "direct",
            "j_fs",
            "original_mp_fs_plus",
            "shared_mp_fs_plus_generation",
        ],
    )
    for field, expected in {
        "direct_included": True,
        "j_fs_included": True,
        "original_mp_fs_plus_included": True,
        "d_g1_included": True,
        "d_f_g1_included": True,
        "full_included": False,
    }.items():
        expect_equal(violations, f"arm_{field}", arms.get(field), expected)
    strategy = arms.get("generation_strategy") or {}
    for forbidden_arm in ("d_g1_shared_mp_fs_plus", "d_f_g1_shared_mp_fs_plus"):
        if forbidden_arm in strategy:
            add_violation(violations, "forbidden_generation_strategy_arm_present", arm=forbidden_arm)
    shared = strategy.get("shared_mp_fs_plus_generation") or {}
    expect_equal(
        violations,
        "shared_generation_raw_file",
        shared.get("raw_generation_file"),
        "raw_generations/shared_mp_fs_plus_generation.jsonl",
    )
    expect_equal(
        violations,
        "shared_generation_config_path",
        shared.get("generation_config_path"),
        "configs/stage5/resolved_d_g1_control.json",
    )
    replays = shared.get("deterministic_replays") or []
    replay_slugs = [row.get("method_slug") for row in replays]
    if "D_G1" not in replay_slugs:
        add_violation(violations, "D_G1_replay_arm_missing")
    if "D_F_G1" not in replay_slugs:
        add_violation(violations, "D_F_G1_replay_arm_missing")

    hypotheses = lock.get("hypotheses") or {}
    expect_equal(
        violations,
        "H1_comparison",
        (hypotheses.get("H1_method_level_confirmation") or {}).get("comparison"),
        "D_F_G1_vs_original_mp_fs_plus",
    )
    expect_equal(
        violations,
        "H2_comparison",
        (hypotheses.get("H2_F_incremental_confirmation") or {}).get("comparison"),
        "D_F_G1_vs_D_G1",
    )
    expect_equal(
        violations,
        "hypothesis_family",
        hypotheses.get("declared_family"),
        ["H1_method_level_confirmation", "H2_F_incremental_confirmation"],
    )

    dataset = lock.get("dataset_lock") or {}
    expect_equal(violations, "new_dataset_required", dataset.get("new_untouched_dataset_required"), True)
    expect_equal(
        violations,
        "confirmation_run_must_not_be_allowed_now",
        dataset.get("confirmation_run_allowed_now"),
        False,
    )
    required = set(dataset.get("registration_required_before_gpu_run") or [])
    for field in {
        "dataset_archive_sha256",
        "sample_ids_sha256",
        "gold_plans_sha256",
        "source_group_overlap_audit",
        "input_text_hash_overlap_audit",
        "canonical_content_hash_overlap_audit",
        "two_independent_gold_reviews",
        "gold_review_adjudication_protocol",
        "final_gold_hash",
    }:
        if field not in required:
            add_violation(violations, "dataset_registration_field_missing", field=field)
    selection_required = set(dataset.get("dataset_selection_provenance_required") or [])
    for field in {
        "dataset_selection_policy",
        "candidate_source_registry",
        "selection_date",
        "selection_commit",
        "selection_performed_before_any_model_run",
        "eligible_pool_sha256",
        "sampling_algorithm",
        "sampling_seed",
    }:
        if field not in selection_required:
            add_violation(violations, "dataset_selection_field_missing", field=field)

    expect_equal(
        violations,
        "model_lock_exact",
        (lock.get("model_lock") or {}).get("primary_model"),
        EXPECTED_MODEL_LOCK,
    )
    expect_equal(violations, "generation_lock_exact", lock.get("generation_lock"), EXPECTED_GENERATION_LOCK)

    prompt = lock.get("prompt_lock") or {}
    expect_equal(
        violations,
        "prompt_builder_exact",
        prompt.get("prompt_builder"),
        "nldbwrite_v3.planner.build_planner_prompt",
    )
    expect_equal(
        violations,
        "source_parser_exact",
        prompt.get("source_parser"),
        "nldbwrite_v3.source_parser.parse_source_payload",
    )
    expect_equal(
        violations,
        "prompt_changes_after_freeze_allowed",
        prompt.get("prompt_changes_after_freeze_allowed"),
        False,
    )

    metric = lock.get("metric_lock") or {}
    expect_equal(violations, "primary_metric", metric.get("primary_metric"), "target_state_correct")
    expect_equal(
        violations,
        "denominator_policy",
        metric.get("denominator_policy"),
        "all_registered_confirmation_samples",
    )
    expect_equal(
        violations,
        "output_max_token_policy",
        metric.get("output_max_token_policy"),
        "sample_remains_in_denominator_and_is_scored_by_deterministic_pipeline",
    )

    stats = lock.get("statistics_lock") or {}
    expect_equal(
        violations,
        "primary_comparison",
        stats.get("primary_comparison"),
        "D_F_G1_vs_original_mp_fs_plus",
    )
    expect_equal(
        violations,
        "key_component_comparison",
        stats.get("key_component_comparison"),
        "D_F_G1_vs_D_G1",
    )
    expect_equal(violations, "paired_test", stats.get("paired_test"), "exact_mcnemar_two_sided")
    expect_equal(violations, "cluster_key", stats.get("cluster_key"), "source_group")
    expect_equal(violations, "bootstrap_seed", stats.get("bootstrap_seed"), 240824)
    expect_equal(violations, "bootstrap_replicates", stats.get("bootstrap_replicates"), 10000)

    gate = lock.get("execution_gate") or {}
    for field in (
        "model_called_in_stage5",
        "gpu_required_for_stage5",
        "confirmation_gpu_run_allowed_before_reviewer_acceptance",
        "dataset_or_gold_edits_allowed_after_registration",
        "method_edits_allowed_after_stage5_acceptance",
    ):
        expect_equal(violations, f"execution_gate_{field}", gate.get(field), False)
    expect_equal(
        violations,
        "resolved_config_required_for_confirmation",
        gate.get("resolved_config_required_for_confirmation"),
        True,
    )
    expect_equal(
        violations,
        "exact_arm_config_lock_required_for_confirmation",
        gate.get("exact_arm_config_lock_required_for_confirmation"),
        True,
    )
    expect_equal(
        violations,
        "environment_lock_required_for_confirmation",
        gate.get("environment_lock_required_for_confirmation"),
        True,
    )
    expect_equal(
        violations,
        "execution_gate_accepted_method_freeze_commit",
        gate.get("accepted_method_freeze_commit"),
        EXPECTED_METHOD_FREEZE_COMMIT,
    )


def validate_hashes(
    executable_manifest: dict[str, Any],
    violations: list[dict[str, Any]],
) -> dict[str, str]:
    actual_hashes: dict[str, str] = {}
    expected_files = executable_manifest.get("frozen_files_sha256") or {}
    for relative, expected in sorted(expected_files.items()):
        path = PROJECT_ROOT / relative
        if not path.is_file():
            add_violation(violations, "frozen_file_missing", path=relative)
            continue
        actual = sha256_file(path)
        actual_hashes[relative] = actual
        if actual != expected:
            add_violation(
                violations,
                "frozen_file_hash_mismatch",
                path=relative,
                actual=actual,
                expected=expected,
            )
    implementation_files = [
        PROJECT_ROOT / relative
        for relative in executable_manifest.get("method_implementation_files") or []
    ]
    missing = [path for path in implementation_files if not path.is_file()]
    for path in missing:
        add_violation(
            violations,
            "implementation_file_missing",
            path=path.relative_to(PROJECT_ROOT).as_posix(),
        )
    if not missing:
        actual_tree = tree_hash(implementation_files)
        actual_hashes["method_source_tree_sha256"] = actual_tree
        expect_equal(
            violations,
            "method_source_tree_sha256",
            actual_tree,
            executable_manifest.get("method_source_tree_sha256"),
        )
    return actual_hashes


def git_output(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def validate_git_anchor(require_accepted_tag: bool, violations: list[dict[str, Any]]) -> dict[str, Any]:
    head = git_output("rev-parse", "HEAD")
    method_tag_commit = git_output("rev-list", "-n", "1", EXPECTED_METHOD_FREEZE_TAG)
    protocol_tag_commit = git_output("rev-list", "-n", "1", EXPECTED_PROTOCOL_TAG)
    status = git_output("status", "--porcelain")
    if require_accepted_tag:
        expect_equal(
            violations,
            "method_freeze_tag_points_to_accepted_commit",
            method_tag_commit,
            EXPECTED_METHOD_FREEZE_COMMIT,
        )
        expect_equal(violations, "protocol_patch_tag_points_to_head", protocol_tag_commit, head)
        expect_equal(violations, "git_status_porcelain_clean", status, "")
    return {
        "head": head,
        "accepted_method_freeze_tag": EXPECTED_METHOD_FREEZE_TAG,
        "accepted_method_freeze_commit": method_tag_commit,
        "protocol_patch_tag": EXPECTED_PROTOCOL_TAG,
        "protocol_patch_tag_commit": protocol_tag_commit,
        "status_porcelain": status,
    }


def validate_stage5(
    *,
    stage5_root: Path = DEFAULT_STAGE5_ROOT,
    config_path: Path = DEFAULT_CONFIG,
    resolved_config_path: Path = DEFAULT_RESOLVED_CONFIG,
    executable_manifest_path: Path = DEFAULT_EXECUTABLE_MANIFEST,
    arm_configs_path: Path = DEFAULT_ARM_CONFIGS,
    environment_lock_path: Path = DEFAULT_ENVIRONMENT_LOCK,
    require_accepted_tag: bool = False,
) -> dict[str, Any]:
    stage5_root = stage5_root.resolve()
    config_path = config_path.resolve()
    resolved_config_path = resolved_config_path.resolve()
    executable_manifest_path = executable_manifest_path.resolve()
    arm_configs_path = arm_configs_path.resolve()
    environment_lock_path = environment_lock_path.resolve()
    manifest_path = stage5_root / "provenance" / "freeze_manifest.json"
    lock_path = stage5_root / "CONFIRMATION_PROTOCOL_LOCK.json"
    violations: list[dict[str, Any]] = []

    for rule, path in {
        "freeze_manifest_missing": manifest_path,
        "confirmation_lock_missing": lock_path,
        "executable_manifest_missing": executable_manifest_path,
        "confirmation_arm_configs_missing": arm_configs_path,
        "confirmation_environment_lock_missing": environment_lock_path,
        "overlay_config_missing": config_path,
        "resolved_config_missing": resolved_config_path,
    }.items():
        if not path.is_file():
            add_violation(violations, rule, path=str(path))

    config = read_json(config_path) if config_path.is_file() else {}
    resolved_config = read_json(resolved_config_path) if resolved_config_path.is_file() else {}
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    lock = read_json(lock_path) if lock_path.is_file() else {}
    executable_manifest = (
        read_json(executable_manifest_path) if executable_manifest_path.is_file() else {}
    )
    arm_configs = read_json(arm_configs_path) if arm_configs_path.is_file() else {}
    environment_lock = read_json(environment_lock_path) if environment_lock_path.is_file() else {}

    validate_config_exact(config, resolved=False, violations=violations)
    validate_config_exact(resolved_config, resolved=True, violations=violations)
    validate_manifest(manifest, executable_manifest, executable_manifest_path, violations)
    validate_protocol_lock(lock, violations)
    validate_confirmation_arm_configs(arm_configs, lock, violations)
    validate_environment_lock(environment_lock, lock, violations)
    actual_hashes = validate_hashes(executable_manifest, violations)
    git_anchor = validate_git_anchor(require_accepted_tag, violations)

    return {
        "status": "PASS" if not violations else "FAIL",
        "stage": "Stage5_METHOD_REVISION_FREEZE_PATCH2",
        "method_name": EXPECTED_METHOD_NAME,
        "component_set": EXPECTED_COMPONENT_SET,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "accepted_method_freeze_tag": EXPECTED_METHOD_FREEZE_TAG,
        "accepted_method_freeze_commit": EXPECTED_METHOD_FREEZE_COMMIT,
        "protocol_patch_tag": EXPECTED_PROTOCOL_TAG,
        "actual_hashes": actual_hashes,
        "git_anchor": git_anchor,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage5-root", default=str(DEFAULT_STAGE5_ROOT))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--resolved-config", default=str(DEFAULT_RESOLVED_CONFIG))
    parser.add_argument(
        "--executable-manifest",
        default=str(DEFAULT_EXECUTABLE_MANIFEST),
    )
    parser.add_argument("--arm-configs", default=str(DEFAULT_ARM_CONFIGS))
    parser.add_argument("--environment-lock", default=str(DEFAULT_ENVIRONMENT_LOCK))
    parser.add_argument(
        "--require-accepted-tag",
        action="store_true",
        help="Require accepted_executable_tag to point at HEAD and git status to be clean.",
    )
    args = parser.parse_args(argv)
    report = validate_stage5(
        stage5_root=Path(args.stage5_root),
        config_path=Path(args.config),
        resolved_config_path=Path(args.resolved_config),
        executable_manifest_path=Path(args.executable_manifest),
        arm_configs_path=Path(args.arm_configs),
        environment_lock_path=Path(args.environment_lock),
        require_accepted_tag=args.require_accepted_tag,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
