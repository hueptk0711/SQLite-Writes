#!/usr/bin/env python3
"""Validate the Stage 5 revised-method freeze package.

The validator is CPU-only. It checks that the frozen method is exactly
D+F+G1, that Stage 4 is marked as diagnostic rather than confirmatory, and
that a future confirmation run is blocked until a new untouched dataset is
registered with hashes and overlap audits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE5_ROOT = PROJECT_ROOT / "stage5_method_revision_freeze"
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "stage5" / "mp_fs_plus_vnext_r1.json"

EXPECTED_COMPONENT_SET = ["D", "F", "G1"]
EXPECTED_METHOD_NAME = "MP-FS+ vNext-R1"
EXPECTED_METHOD_VARIANT = "mp-fs-plus-vnext-r1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_violation(violations: list[dict[str, Any]], rule: str, **details: Any) -> None:
    row: dict[str, Any] = {"rule": rule}
    row.update(details)
    violations.append(row)


def validate_config(config: dict[str, Any], violations: list[dict[str, Any]]) -> None:
    if config.get("method_id") != "MP-FS+":
        add_violation(violations, "method_id", actual=config.get("method_id"))
    if config.get("method_variant") != EXPECTED_METHOD_VARIANT:
        add_violation(violations, "method_variant", actual=config.get("method_variant"))
    if config.get("method_version") != EXPECTED_METHOD_NAME:
        add_violation(violations, "method_version", actual=config.get("method_version"))

    if any((config.get("stage2_interventions") or {}).values()):
        add_violation(violations, "A_B_C_must_be_disabled")
    if (config.get("structured_source_parser") or {}).get("enabled") is not True:
        add_violation(violations, "D_must_be_enabled")
    if (config.get("free_text_typed_normalization") or {}).get("enabled") is not False:
        add_violation(violations, "E_must_be_disabled")
    if (config.get("constrained_reference_repair") or {}).get("enabled") is not True:
        add_violation(violations, "F_must_be_enabled")

    g = config.get("diagnostic_targeted_repair") or {}
    if g.get("enabled") is not True or g.get("evidence_span_boundary") is not True:
        add_violation(violations, "G1_must_be_enabled")
    if g.get("evidence_span_selection") is not False:
        add_violation(violations, "G2_must_be_disabled")
    if g.get("max_revalidation_attempts") != 1:
        add_violation(violations, "G1_revalidation_must_be_bounded")


def validate_manifest(
    manifest: dict[str, Any],
    config_path: Path,
    stage5_root: Path,
    violations: list[dict[str, Any]],
) -> None:
    if manifest.get("model_called") is not False or manifest.get("gpu_called") is not False:
        add_violation(violations, "stage5_must_be_cpu_only")
    if manifest.get("new_evaluation_results") is not False:
        add_violation(violations, "stage5_must_not_report_new_results")

    method = manifest.get("final_method") or {}
    if method.get("method_name") != EXPECTED_METHOD_NAME:
        add_violation(violations, "manifest_method_name", actual=method.get("method_name"))
    if method.get("method_variant") != EXPECTED_METHOD_VARIANT:
        add_violation(violations, "manifest_method_variant", actual=method.get("method_variant"))
    if method.get("component_set") != EXPECTED_COMPONENT_SET:
        add_violation(violations, "manifest_component_set", actual=method.get("component_set"))
    if method.get("config_path") != "configs/stage5/mp_fs_plus_vnext_r1.json":
        add_violation(violations, "manifest_config_path", actual=method.get("config_path"))

    evidence = manifest.get("source_evidence") or {}
    if evidence.get("stage4_interpretation") != "diagnostic_used_not_confirmatory_for_D_F_G1":
        add_violation(violations, "stage4_must_be_diagnostic_not_confirmatory")
    if "post-hoc Stage 4" not in str(evidence.get("F") or ""):
        add_violation(violations, "F_lifecycle_must_be_post_hoc_stage4")

    for relative in manifest.get("files") or []:
        path = PROJECT_ROOT / str(relative)
        if not path.is_file():
            add_violation(violations, "manifest_file_missing", path=relative)

    if not config_path.is_file():
        add_violation(violations, "config_missing", path=str(config_path))
    if not (stage5_root / "REVIEWER_README.md").is_file():
        add_violation(violations, "reviewer_readme_missing")
    if not (stage5_root / "VALIDATION_REPORT.md").is_file():
        add_violation(violations, "validation_report_missing")


def validate_protocol_lock(lock: dict[str, Any], violations: list[dict[str, Any]]) -> None:
    if lock.get("status") != "blocked_until_new_untouched_dataset_registered_and_reviewer_accepted":
        add_violation(violations, "confirmation_must_be_blocked", actual=lock.get("status"))

    method = lock.get("method_under_test") or {}
    if method.get("component_set") != EXPECTED_COMPONENT_SET:
        add_violation(violations, "lock_component_set", actual=method.get("component_set"))

    lifecycle = lock.get("lifecycle_boundary") or {}
    if lifecycle.get("F") != "promoted_after_post_hoc_stage4_failure_analysis":
        add_violation(violations, "lock_F_lifecycle", actual=lifecycle.get("F"))
    if lifecycle.get("stage4_role_after_stage5") != "diagnostic_used_not_confirmatory":
        add_violation(violations, "lock_stage4_role", actual=lifecycle.get("stage4_role_after_stage5"))

    dataset = lock.get("dataset_lock") or {}
    if dataset.get("new_untouched_dataset_required") is not True:
        add_violation(violations, "new_dataset_required")
    if dataset.get("confirmation_run_allowed_now") is not False:
        add_violation(violations, "confirmation_run_must_not_be_allowed_now")
    required = set(dataset.get("registration_required_before_gpu_run") or [])
    for field in {
        "dataset_archive_sha256",
        "sample_ids_sha256",
        "gold_plans_sha256",
        "source_group_overlap_audit",
        "input_text_hash_overlap_audit",
        "canonical_content_hash_overlap_audit",
        "two_independent_gold_reviews",
    }:
        if field not in required:
            add_violation(violations, "dataset_registration_field_missing", field=field)

    generation = lock.get("generation_lock") or {}
    if generation.get("max_input_tokens") != 28672 or generation.get("max_new_tokens") != 4096:
        add_violation(violations, "token_budget_lock", actual=generation)
    if generation.get("do_sample") is not False:
        add_violation(violations, "decoding_must_be_greedy")

    metric = lock.get("metric_lock") or {}
    if metric.get("primary_metric") != "target_state_correct":
        add_violation(violations, "primary_metric", actual=metric.get("primary_metric"))
    if metric.get("denominator_policy") != "all_registered_confirmation_samples":
        add_violation(violations, "denominator_policy", actual=metric.get("denominator_policy"))

    stats = lock.get("statistics_lock") or {}
    if stats.get("paired_test") != "exact_mcnemar_two_sided":
        add_violation(violations, "paired_test", actual=stats.get("paired_test"))
    if stats.get("bootstrap_replicates") != 10000:
        add_violation(violations, "bootstrap_replicates", actual=stats.get("bootstrap_replicates"))

    gate = lock.get("execution_gate") or {}
    if gate.get("model_called_in_stage5") is not False or gate.get("gpu_required_for_stage5") is not False:
        add_violation(violations, "execution_gate_cpu_only")
    if gate.get("confirmation_gpu_run_allowed_before_reviewer_acceptance") is not False:
        add_violation(violations, "gpu_run_must_wait_for_reviewer")


def validate_stage5(
    *,
    stage5_root: Path = DEFAULT_STAGE5_ROOT,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    stage5_root = stage5_root.resolve()
    config_path = config_path.resolve()
    manifest_path = stage5_root / "provenance" / "freeze_manifest.json"
    lock_path = stage5_root / "CONFIRMATION_PROTOCOL_LOCK.json"
    violations: list[dict[str, Any]] = []

    if not manifest_path.is_file():
        add_violation(violations, "freeze_manifest_missing", path=str(manifest_path))
    if not lock_path.is_file():
        add_violation(violations, "confirmation_lock_missing", path=str(lock_path))
    if not config_path.is_file():
        add_violation(violations, "config_missing", path=str(config_path))

    config = read_json(config_path) if config_path.is_file() else {}
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    lock = read_json(lock_path) if lock_path.is_file() else {}

    validate_config(config, violations)
    validate_manifest(manifest, config_path, stage5_root, violations)
    validate_protocol_lock(lock, violations)

    files = {
        "final_config": config_path,
        "freeze_manifest": manifest_path,
        "confirmation_protocol_lock": lock_path,
        "method_freeze": stage5_root / "METHOD_FREEZE.md",
        "reviewer_readme": stage5_root / "REVIEWER_README.md",
        "validation_report": stage5_root / "VALIDATION_REPORT.md",
    }
    file_hashes = {
        name: sha256_file(path)
        for name, path in files.items()
        if path.is_file()
    }
    return {
        "status": "PASS" if not violations else "FAIL",
        "stage": "Stage5_METHOD_REVISION_FREEZE",
        "method_name": EXPECTED_METHOD_NAME,
        "component_set": EXPECTED_COMPONENT_SET,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "file_hashes": file_hashes,
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage5-root", default=str(DEFAULT_STAGE5_ROOT))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    report = validate_stage5(
        stage5_root=Path(args.stage5_root),
        config_path=Path(args.config),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
