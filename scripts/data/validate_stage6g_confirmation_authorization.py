#!/usr/bin/env python3
"""Validate the Stage6G confirmation-run authorization lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_LOCK = {
    "stage": "Stage6G_CONFIRMATION_RUN_AUTHORIZATION_AND_EXECUTION_LOCK",
    "authorization_status": "AUTHORIZED_FOR_CONFIRMATORY_GENERATION",
    "status": "AUTHORIZED_FOR_CONFIRMATORY_GENERATION_PENDING_EXECUTION",
    "confirmation_run_allowed_now": True,
    "confirmation_predictions_created": False,
    "model_called_in_stage6g": False,
    "gpu_called_in_stage6g": False,
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
    "stage6f_acceptance_roots": {
        "acceptance_lock_sha256": "5365e07bf7135b32bc0d911aa59ace0b33c97e77e037632fa2aa16c45121b3c0",
        "server_output_manifest_sha256": "5b7cedd0c0472ba96b5541b17db61ac6dffb33f5c2348b79cac29beaa5e80893",
        "server_preflight_lock_sha256": "1f044f58879fd5caa34449de4da77f6cdfe33325187f0497bd614bf5de119427",
        "confirmation_run_plan_sha256": "f8423d025b37ac346ea3977218b078b465184a225ba40546f8896598afe503b2",
        "model_tokenizer_asset_audit_sha256": "26b0ff29b902031457d0f60a95f482a5c900b792e18a40c79e4544c071fde84e",
        "prompt_token_audit_sha256": "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6",
    },
    "prompt_token_audit": {
        "rows": 2405,
        "max_input_tokens": 28672,
        "max_observed_input_tokens": 2253,
        "input_truncation_error_count": 0,
    },
    "generation_parameters": {
        "decoding": "greedy",
        "temperature": 0,
        "max_input_tokens": 28672,
        "max_new_tokens": 4096,
        "output_max_token_hit_policy": "preserve_raw_output_continue_processing_keep_sample_in_denominator",
    },
    "raw_generation_stream_count": 4,
    "raw_generation_streams": {
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
    },
    "hypotheses": {
        "H1": "d_f_g1_vnext_vs_original_mp_fs_plus",
        "H2": "d_f_g1_vnext_vs_d_g1_control_shared_raw_generation",
    },
    "statistics": {
        "primary_metric": "target_state_correct",
        "paired_test": "exact_two_sided_McNemar",
        "family_correction": "Holm_over_H1_H2",
        "cluster_key": "source_group",
        "cluster_bootstrap_replicates": 10000,
        "seed": 240824,
    },
    "run_invariants": {
        "do_not_change_prompt_or_config_after_authorization": True,
        "do_not_change_token_budget_after_authorization": True,
        "do_not_drop_failures_from_denominator": True,
        "do_not_rerun_max_token_hits_with_larger_budget": True,
        "independent_D_F_G1_generation_allowed": False,
        "F_changes_prompt_surface": False,
        "confirmation_outputs_must_be_written_under_raw_generations": True,
    },
    "forbidden_confirmation_arms": [
        "FULL",
        "D_ONLY",
        "NO_C",
        "G2",
        "second_model",
        "post_hoc_token_budget_increase",
    ],
    "next_step": "run_confirmatory_generation_under_this_authorization_lock",
}

EXPECTED_EXTERNAL_HASHES = {
    "stage6_gpu_preflight_acceptance/STAGE6F_GPU_PREFLIGHT_ACCEPTANCE_LOCK.json": (
        "5365e07bf7135b32bc0d911aa59ace0b33c97e77e037632fa2aa16c45121b3c0"
    ),
    "stage6_gpu_preflight_acceptance/SERVER_OUTPUT_MANIFEST.json": (
        "5b7cedd0c0472ba96b5541b17db61ac6dffb33f5c2348b79cac29beaa5e80893"
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
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def add(violations: list[dict[str, Any]], code: str, **details: Any) -> None:
    violations.append({"code": code, **details})


def validate(authorization_dir: Path, repo_root: Path | None = None) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    lock_path = authorization_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    if not lock_path.is_file():
        add(violations, "authorization_lock_missing", path=str(lock_path))
        return {"status": "FAIL", "violations": violations}

    lock = read_json(lock_path)
    if lock != EXPECTED_LOCK:
        add(violations, "authorization_lock_exact_mismatch")
        for key, expected in EXPECTED_LOCK.items():
            if lock.get(key) != expected:
                add(
                    violations,
                    "authorization_lock_field_mismatch",
                    field=key,
                    expected=expected,
                    actual=lock.get(key),
                )

    streams = lock.get("raw_generation_streams") or {}
    if len(streams) != 4 or lock.get("raw_generation_stream_count") != 4:
        add(violations, "raw_generation_stream_count_mismatch")
    shared = streams.get("shared_mp_fs_plus_generation") or {}
    if shared.get("deterministic_replay_arms") != ["d_g1_control", "d_f_g1_vnext"]:
        add(violations, "shared_generation_replay_arms_mismatch")
    if lock.get("confirmation_predictions_created") is not False:
        add(violations, "authorization_contains_predictions")
    if lock.get("model_called_in_stage6g") is not False:
        add(violations, "model_called_in_authorization_stage")
    if lock.get("gpu_called_in_stage6g") is not False:
        add(violations, "gpu_called_in_authorization_stage")

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

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "authorization_status": lock.get("authorization_status"),
        "confirmation_run_allowed_now": lock.get("confirmation_run_allowed_now"),
        "confirmation_predictions_created": lock.get("confirmation_predictions_created"),
        "final_confirmation_n": lock.get("final_confirmation_n"),
        "raw_generation_stream_count": lock.get("raw_generation_stream_count"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization-dir", default="stage6_confirmation_run_authorization")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--skip-external-anchor-checks", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = None if args.skip_external_anchor_checks else Path(args.repo_root)
    report = validate(Path(args.authorization_dir), repo_root=repo_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
