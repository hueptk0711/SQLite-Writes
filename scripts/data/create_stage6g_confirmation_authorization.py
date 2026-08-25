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

STAGE6F_ACCEPTANCE_LOCK_SHA256 = "5365e07bf7135b32bc0d911aa59ace0b33c97e77e037632fa2aa16c45121b3c0"
STAGE6F_SERVER_MANIFEST_SHA256 = "5b7cedd0c0472ba96b5541b17db61ac6dffb33f5c2348b79cac29beaa5e80893"
STAGE6F_SERVER_PREFLIGHT_LOCK_SHA256 = "1f044f58879fd5caa34449de4da77f6cdfe33325187f0497bd614bf5de119427"
STAGE6F_CONFIRMATION_RUN_PLAN_SHA256 = "f8423d025b37ac346ea3977218b078b465184a225ba40546f8896598afe503b2"
STAGE6F_MODEL_TOKENIZER_ASSET_AUDIT_SHA256 = "26b0ff29b902031457d0f60a95f482a5c900b792e18a40c79e4544c071fde84e"

PROMPT_TOKEN_AUDIT_SHA256 = "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6"
PROMPT_TOKEN_ROWS = 2405
MAX_INPUT_TOKENS = 28672
MAX_NEW_TOKENS = 4096
MAX_OBSERVED_INPUT_TOKENS = 2253

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
        "confirmation_run_allowed_now": True,
        "confirmation_predictions_created": False,
        "model_called_in_stage6g": False,
        "gpu_called_in_stage6g": False,
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
        "prompt_token_audit": {
            "rows": PROMPT_TOKEN_ROWS,
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_observed_input_tokens": MAX_OBSERVED_INPUT_TOKENS,
            "input_truncation_error_count": 0,
        },
        "generation_parameters": {
            "decoding": "greedy",
            "temperature": 0,
            "max_input_tokens": MAX_INPUT_TOKENS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "output_max_token_hit_policy": (
                "preserve_raw_output_continue_processing_keep_sample_in_denominator"
            ),
        },
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


def create_authorization(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = authorization_lock()
    write_json(output_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json", lock)

    readme = """# Stage6G Confirmation Run Authorization Lock

This CPU-only stage authorizes the frozen Stage6 final confirmation set for
confirmatory generation after Stage6F reviewer acceptance. It does not create
predictions and does not call the model or GPU.

The confirmatory run must use exactly four LLM generation streams:

1. `direct` -> `raw_generations/direct.jsonl`
2. `j_fs` -> `raw_generations/j_fs.jsonl`
3. `original_mp_fs_plus` -> `raw_generations/original_mp_fs_plus.jsonl`
4. `shared_mp_fs_plus_generation` -> `raw_generations/shared_mp_fs_plus_generation.jsonl`

The shared MP-FS+ generation must be replayed deterministically as both
`d_g1_control` and `d_f_g1_vnext`.
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
