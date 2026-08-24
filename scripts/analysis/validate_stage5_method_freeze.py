#!/usr/bin/env python3
"""Validate the Stage 5 revised-method executable freeze package.

The validator is CPU-only. It rejects drift in the overlay config, resolved
effective config, base configs, demonstration bank, protocol lock, and selected
method implementation files. It also enforces the Stage 5 confirmation arms,
hypotheses, token policy, and Stage 4 lifecycle boundary.
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

EXPECTED_COMPONENT_SET = ["D", "F", "G1"]
EXPECTED_METHOD_NAME = "MP-FS+ vNext-R1"
EXPECTED_METHOD_VARIANT = "mp-fs-plus-vnext-r1"
EXPECTED_TAG = "stage5-vnext-r1-freeze-patch1"
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
    expect_equal(violations, "accepted_executable_tag", lock.get("accepted_executable_tag"), EXPECTED_TAG)
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
            "d_g1_shared_mp_fs_plus",
            "d_f_g1_shared_mp_fs_plus",
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
    dfg1 = strategy.get("d_f_g1_shared_mp_fs_plus") or {}
    expect_equal(
        violations,
        "F_incremental_shared_raw_generation",
        dfg1.get("shares_raw_generation_with"),
        "d_g1_shared_mp_fs_plus",
    )
    if "D_G1" not in ((strategy.get("d_g1_shared_mp_fs_plus") or {}).get("process_as") or []):
        add_violation(violations, "D_G1_replay_arm_missing")
    if "D_F_G1" not in (dfg1.get("process_as") or []):
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
    tag_commit = git_output("rev-list", "-n", "1", EXPECTED_TAG)
    status = git_output("status", "--porcelain")
    if require_accepted_tag:
        expect_equal(violations, "accepted_tag_points_to_head", tag_commit, head)
        expect_equal(violations, "git_status_porcelain_clean", status, "")
    return {
        "head": head,
        "accepted_tag": EXPECTED_TAG,
        "accepted_tag_commit": tag_commit,
        "status_porcelain": status,
    }


def validate_stage5(
    *,
    stage5_root: Path = DEFAULT_STAGE5_ROOT,
    config_path: Path = DEFAULT_CONFIG,
    resolved_config_path: Path = DEFAULT_RESOLVED_CONFIG,
    executable_manifest_path: Path = DEFAULT_EXECUTABLE_MANIFEST,
    require_accepted_tag: bool = False,
) -> dict[str, Any]:
    stage5_root = stage5_root.resolve()
    config_path = config_path.resolve()
    resolved_config_path = resolved_config_path.resolve()
    executable_manifest_path = executable_manifest_path.resolve()
    manifest_path = stage5_root / "provenance" / "freeze_manifest.json"
    lock_path = stage5_root / "CONFIRMATION_PROTOCOL_LOCK.json"
    violations: list[dict[str, Any]] = []

    for rule, path in {
        "freeze_manifest_missing": manifest_path,
        "confirmation_lock_missing": lock_path,
        "executable_manifest_missing": executable_manifest_path,
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

    validate_config_exact(config, resolved=False, violations=violations)
    validate_config_exact(resolved_config, resolved=True, violations=violations)
    validate_manifest(manifest, executable_manifest, executable_manifest_path, violations)
    validate_protocol_lock(lock, violations)
    actual_hashes = validate_hashes(executable_manifest, violations)
    git_anchor = validate_git_anchor(require_accepted_tag, violations)

    return {
        "status": "PASS" if not violations else "FAIL",
        "stage": "Stage5_METHOD_REVISION_FREEZE_PATCH1",
        "method_name": EXPECTED_METHOD_NAME,
        "component_set": EXPECTED_COMPONENT_SET,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "accepted_executable_tag": EXPECTED_TAG,
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
        require_accepted_tag=args.require_accepted_tag,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
