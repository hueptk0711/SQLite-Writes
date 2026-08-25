#!/usr/bin/env python3
"""Create the Stage6G confirmation-run authorization lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


STAGE6E_COMMIT = "f32e8b2c7152e0f31829eab004da0f396084e57e"
STAGE6F_EXECUTION_COMMIT = "dfa1be8d9db133f6adb6ec3a796c9750db67b5b9"
STAGE6F_ACCEPTANCE_COMMIT = "6cdbff685a16aff7eb9b6647937aaa7fe740ee35"
STAGE6F_SERVER_ZIP_SHA256 = "004913b1778cc145d44f32aa49a60039438b24cc36d1f17a5c85091e8cc5bd1b"

FINAL_CONFIRMATION_N = 481
FINAL_GOLD_CORPUS_SHA256 = "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16"
FINAL_CONFIRMATION_MANIFEST_SHA256 = "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7"

MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
MODEL_SHA256 = "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c"
TOKENIZER_SHA256 = "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468"
MODEL_CONFIG_SHA256 = "326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357"

STAGE6F_ACCEPTANCE_LOCK_SHA256 = "bfca33214efd17a951a5bb968f998285bcd9d7fa23e50ef407a3ec59918b160e"
STAGE6F_SERVER_MANIFEST_SHA256 = "bc002e4352063159de9326219fb5c26934fda6fae54722c2cc95fc4217499765"
STAGE6F_SERVER_PREFLIGHT_LOCK_SHA256 = "1f044f58879fd5caa34449de4da77f6cdfe33325187f0497bd614bf5de119427"
STAGE6F_CONFIRMATION_RUN_PLAN_SHA256 = "f8423d025b37ac346ea3977218b078b465184a225ba40546f8896598afe503b2"
STAGE6F_MODEL_TOKENIZER_ASSET_AUDIT_SHA256 = "26b0ff29b902031457d0f60a95f482a5c900b792e18a40c79e4544c071fde84e"

PROMPT_TOKEN_AUDIT_SHA256 = "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6"
PROMPT_TOKEN_ROWS = 2405
MAX_INPUT_TOKENS = 28672
MAX_NEW_TOKENS = 4096
MAX_OBSERVED_INPUT_TOKENS = 2253
STAGE5_GENERATION_LOCK_SHA256 = "890da57fbe137f4b3f64e002a29d76a3c630cdc8290c924ac1d1f9da585a9c14"
STAGE5_CONFIRMATION_PROTOCOL_LOCK_SHA256 = "fca32f3566ab830981f4f5b5c6e364932f79ef3c8f82881b45e4a5c19d85df3c"
STAGE5_CONFIRMATION_ARM_CONFIGS_SHA256 = "cfa36ffb1a230c53920fd2e45356e48b45b236cbf2454fa328e470dbc5fd3682"
STAGE5_CONFIRMATION_ENVIRONMENT_LOCK_SHA256 = "bac701451b8027a189b579c6766d690e4756a3a49458bf85f623fec1a97ba2c1"
METHOD_SOURCE_TREE_SHA256 = "78901f8fec28b2aa6e75166283415926da2b5af1dec48c4ba37e71be3d73d67b"

STAGE5_GENERATION_LOCK = {
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

STAGE5_CONFIG_HASHES = {
    "configs/stage5/resolved_direct_confirmation.json": "0795d31926345c62d5ba832d8374c9ac067967a3842c45854a2fff9b32c9f826",
    "configs/stage5/resolved_j_fs_confirmation.json": "a4006a423eb62fd37e5b370aca48a3b9337971f49d94700703d634a3d25c0cfe",
    "configs/stage5/resolved_original_mp_fs_plus.json": "ddda333ccb9b307ed3002213dad6572daa959c2dd5deb2e7d4623cb3aeead84d",
    "configs/stage5/resolved_d_g1_control.json": "c7c9c4d54e59662ee8e251af3aea1747fa035cb306213f20c819098e96f1b6ca",
    "configs/stage5/resolved_mp_fs_plus_vnext_r1.json": "b3a946fc977c3ea95d3226dca1361b1885c098fddf4afdc650f4d36f0e1ce9bf",
}

RAW_GENERATION_STREAMS = {
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def authorization_lock() -> dict[str, Any]:
    return {
        "stage": "Stage6G_CONFIRMATION_RUN_AUTHORIZATION_AND_EXECUTION_LOCK",
        "authorization_status": "AUTHORIZED_FOR_CONFIRMATORY_GENERATION",
        "status": "AUTHORIZED_FOR_CONFIRMATORY_GENERATION_PENDING_EXECUTION",
        "patch": "PATCH1_EXECUTION_BOUNDARY_HARDENING",
        "confirmation_run_allowed_now": True,
        "confirmation_predictions_created": False,
        "model_called_in_stage6g": False,
        "gpu_called_in_stage6g": False,
        "stage6g_authorization_commit_policy": (
            "server execution must pass the reviewer-accepted Stage6G PATCH1 commit "
            "as --expected-git-head and must run from that exact clean checkout"
        ),
        "required_execution_git_head_source": "reviewer_accepted_stage6g_patch1_commit_recorded_in_GIT_INFO",
        "required_git_worktree_clean": True,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "stage6e_commit": STAGE6E_COMMIT,
        "stage6f_execution_commit": STAGE6F_EXECUTION_COMMIT,
        "stage6f_acceptance_commit": STAGE6F_ACCEPTANCE_COMMIT,
        "server_preflight_zip_sha256": STAGE6F_SERVER_ZIP_SHA256,
        "final_gold_corpus_sha256": FINAL_GOLD_CORPUS_SHA256,
        "final_confirmation_manifest_sha256": FINAL_CONFIRMATION_MANIFEST_SHA256,
        "model_revision": MODEL_REVISION,
        "model_sha256": MODEL_SHA256,
        "tokenizer_sha256": TOKENIZER_SHA256,
        "model_config_sha256": MODEL_CONFIG_SHA256,
        "stage6f_acceptance_roots": {
            "acceptance_lock_sha256": STAGE6F_ACCEPTANCE_LOCK_SHA256,
            "server_output_manifest_sha256": STAGE6F_SERVER_MANIFEST_SHA256,
            "server_preflight_lock_sha256": STAGE6F_SERVER_PREFLIGHT_LOCK_SHA256,
            "confirmation_run_plan_sha256": STAGE6F_CONFIRMATION_RUN_PLAN_SHA256,
            "model_tokenizer_asset_audit_sha256": STAGE6F_MODEL_TOKENIZER_ASSET_AUDIT_SHA256,
            "prompt_token_audit_sha256": PROMPT_TOKEN_AUDIT_SHA256,
        },
        "stage5_execution_boundary_roots": {
            "confirmation_protocol_lock_sha256": STAGE5_CONFIRMATION_PROTOCOL_LOCK_SHA256,
            "confirmation_arm_configs_sha256": STAGE5_CONFIRMATION_ARM_CONFIGS_SHA256,
            "confirmation_environment_lock_sha256": STAGE5_CONFIRMATION_ENVIRONMENT_LOCK_SHA256,
            "generation_lock_sha256": STAGE5_GENERATION_LOCK_SHA256,
            "method_source_tree_sha256": METHOD_SOURCE_TREE_SHA256,
            "resolved_config_hashes": STAGE5_CONFIG_HASHES,
        },
        "prompt_token_audit": {
            "rows": PROMPT_TOKEN_ROWS,
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_observed_input_tokens": MAX_OBSERVED_INPUT_TOKENS,
            "input_truncation_error_count": 0,
        },
        "stage5_generation_lock_sha256": STAGE5_GENERATION_LOCK_SHA256,
        "generation_lock": STAGE5_GENERATION_LOCK,
        "raw_generation_stream_count": 4,
        "raw_generation_streams": RAW_GENERATION_STREAMS,
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
            "confidence_interval": "cluster_bootstrap_percentile_95",
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
        "initial_raw_generation_clean_start_required": True,
        "expected_absent_raw_generation_files_before_initial_execution": [
            "raw_generations/direct.jsonl",
            "raw_generations/j_fs.jsonl",
            "raw_generations/original_mp_fs_plus.jsonl",
            "raw_generations/shared_mp_fs_plus_generation.jsonl",
        ],
        "resume_policy": {
            "policy": STAGE5_GENERATION_LOCK["raw_generation_retry_policy"],
            "completed_success_rows_are_immutable": True,
            "infrastructure_resume_only": True,
            "verify_existing_row_hashes_before_resume": True,
            "do_not_regenerate_completed_rows": True,
            "do_not_regenerate_semantic_or_model_failures": True,
            "do_not_change_config_prompt_model_or_token_budget": True,
        },
        "runtime_prompt_input_identity_guard": {
            "stage6f_prompt_token_audit_sha256": PROMPT_TOKEN_AUDIT_SHA256,
            "required_per_row_checks_before_generate": [
                "stage6_sample_id",
                "generation_stream",
                "prompt_sha256",
                "chat_prompt_sha256",
                "input_ids_sha256",
                "input_token_count",
            ],
            "on_mismatch": "stop_run_before_generation_for_that_sample",
        },
        "required_raw_generation_row_fields": [
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
        ],
        "required_replay_provenance_fields": [
            "stage6_sample_id",
            "replay_arm",
            "source_raw_generation_stream",
            "shared_raw_generation_row_sha256",
        ],
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


def create_authorization(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = authorization_lock()
    write_json(output_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json", lock)

    readme = """# Stage6G Confirmation Run Authorization Lock

This CPU-only stage authorizes the frozen Stage6 final confirmation set for
confirmatory generation after Stage6F reviewer acceptance. It does not create
predictions and does not call the model or GPU.

PATCH1 hardens the execution boundary:

- copies the exact Stage5 `generation_lock` object, including `temperature: null`;
- locks Stage5 protocol, arm-config, environment, resolved-config, and method-source hashes;
- requires an exact reviewer-accepted Stage6G PATCH1 Git HEAD and clean worktree at execution;
- requires zero pre-existing raw generation files before initial execution;
- locks runtime prompt/input-ID identity checks against the Stage6F prompt audit;
- locks infrastructure-only resume semantics.

The confirmatory run must use exactly four LLM generation streams:

1. `direct` -> `raw_generations/direct.jsonl`
2. `j_fs` -> `raw_generations/j_fs.jsonl`
3. `original_mp_fs_plus` -> `raw_generations/original_mp_fs_plus.jsonl`
4. `shared_mp_fs_plus_generation` -> `raw_generations/shared_mp_fs_plus_generation.jsonl`

The shared MP-FS+ generation must be replayed deterministically as both
`d_g1_control` and `d_f_g1_vnext`.

Before generation on the GPU server, run:

```bash
python scripts/data/validate_stage6g_confirmation_authorization.py \
  --authorization-dir stage6_confirmation_run_authorization \
  --repo-root . \
  --expected-git-head <REVIEWER_ACCEPTED_STAGE6G_PATCH1_COMMIT> \
  --require-git-clean
```
"""
    (output_dir / "REVIEWER_README.md").write_text(readme, encoding="utf-8")

    report = """# Stage6G Validation Report

Expected status after validation:

```text
authorization_status = AUTHORIZED_FOR_CONFIRMATORY_GENERATION
confirmation_run_allowed_now = true
confirmation_predictions_created = false
model_called_in_stage6g = false
gpu_called_in_stage6g = false
final_confirmation_n = 481
raw_generation_stream_count = 4
generation_lock.temperature = null
existing_confirmation_raw_generation_files = 0
```
"""
    (output_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    return lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="stage6_confirmation_run_authorization")
    return parser.parse_args()


def main() -> None:
    lock = create_authorization(Path(parse_args().output_dir))
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
