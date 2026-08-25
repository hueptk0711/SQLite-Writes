#!/usr/bin/env python3
"""Validate Stage6F GPU preflight artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FILES = [
    "STAGE6F_GPU_PREFLIGHT_LOCK.json",
    "GPU_ENVIRONMENT_MANIFEST.json",
    "MODEL_ASSET_MANIFEST.json",
    "TOKENIZER_MANIFEST.json",
    "FROZEN_ARTIFACT_AUDIT.json",
    "PROMPT_TOKEN_AUDIT.jsonl",
    "PROMPT_TOKEN_SUMMARY.json",
    "H2_SHARED_PROMPT_IDENTITY_AUDIT.json",
    "ORIGINAL_VS_VNEXT_GENERATION_IDENTITY_DECISION.json",
    "SYNTHETIC_GPU_SMOKE_REPORT.json",
    "CONFIRMATION_RUN_PLAN.json",
    "RUN_STAGE6F_ON_SERVER.md",
]

EXPECTED_LOCK_FIELDS = {
    "stage": "Stage6F_GPU_ENVIRONMENT_PREFLIGHT",
    "stage6e_accepted_commit": "f32e8b2c7152e0f31829eab004da0f396084e57e",
    "final_confirmation_n": 481,
    "confirmation_predictions_created": False,
    "model_generate_called_for_confirmation_samples": False,
    "confirmation_run_allowed_now": False,
}

EXPECTED_GENERATION_STREAMS = {
    "direct": "raw_generations/direct.jsonl",
    "j_fs": "raw_generations/j_fs.jsonl",
    "original_mp_fs_plus": "raw_generations/original_mp_fs_plus.jsonl",
    "shared_mp_fs_plus_generation": "raw_generations/shared_mp_fs_plus_generation.jsonl",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def add(violations: list[dict[str, Any]], code: str, **details: Any) -> None:
    violations.append({"code": code, **details})


def validate(preflight_dir: Path, *, require_gpu_pass: bool = False) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for relative in REQUIRED_FILES:
        if not (preflight_dir / relative).is_file():
            add(violations, "required_file_missing", path=relative)
    if violations:
        return {"status": "FAIL", "violations": violations}

    lock = read_json(preflight_dir / "STAGE6F_GPU_PREFLIGHT_LOCK.json")
    frozen = read_json(preflight_dir / "FROZEN_ARTIFACT_AUDIT.json")
    env = read_json(preflight_dir / "GPU_ENVIRONMENT_MANIFEST.json")
    model = read_json(preflight_dir / "MODEL_ASSET_MANIFEST.json")
    tokenizer = read_json(preflight_dir / "TOKENIZER_MANIFEST.json")
    prompt_summary = read_json(preflight_dir / "PROMPT_TOKEN_SUMMARY.json")
    h2 = read_json(preflight_dir / "H2_SHARED_PROMPT_IDENTITY_AUDIT.json")
    original_decision = read_json(preflight_dir / "ORIGINAL_VS_VNEXT_GENERATION_IDENTITY_DECISION.json")
    smoke = read_json(preflight_dir / "SYNTHETIC_GPU_SMOKE_REPORT.json")
    run_plan = read_json(preflight_dir / "CONFIRMATION_RUN_PLAN.json")
    prompt_rows = read_jsonl(preflight_dir / "PROMPT_TOKEN_AUDIT.jsonl")

    for key, expected in EXPECTED_LOCK_FIELDS.items():
        if lock.get(key) != expected:
            add(violations, "lock_field_mismatch", field=key, expected=expected, actual=lock.get(key))
    if frozen.get("status") != "PASS":
        add(violations, "frozen_artifact_audit_not_pass", status=frozen.get("status"))
    if frozen.get("final_counts", {}).get("final_confirmation_n") != 481:
        add(violations, "frozen_final_count_mismatch", actual=frozen.get("final_counts", {}))
    if smoke.get("confirmation_predictions_created") is not False:
        add(violations, "synthetic_smoke_created_confirmation_predictions")
    if model.get("model_generate_called") is not False:
        add(violations, "model_generate_called_during_preflight")

    streams = run_plan.get("generation_streams") or {}
    for stream, expected_path in EXPECTED_GENERATION_STREAMS.items():
        actual = (streams.get(stream) or {}).get("raw_generation_path")
        if actual != expected_path:
            add(
                violations,
                "generation_stream_path_mismatch",
                stream=stream,
                expected=expected_path,
                actual=actual,
            )
    shared = streams.get("shared_mp_fs_plus_generation") or {}
    if shared.get("deterministic_replay_arms") != ["d_g1_control", "d_f_g1_vnext"]:
        add(violations, "shared_generation_replay_arms_mismatch", actual=shared.get("deterministic_replay_arms"))
    if h2.get("independent_D_F_G1_generation_allowed") is not False:
        add(violations, "independent_dfg1_generation_not_forbidden")
    if h2.get("F_changes_prompt_surface") is not False:
        add(violations, "f_changes_prompt_surface_not_forbidden")

    if original_decision.get("d_g1_and_d_f_g1_share_generation") is not True:
        add(violations, "h2_shared_generation_decision_not_locked")
    if original_decision.get("original_mp_fs_plus_uses_independent_generation") is not True:
        add(violations, "original_generation_decision_not_locked")

    gpu_pass = bool(lock.get("gpu_environment_preflight_passed"))
    if require_gpu_pass and not gpu_pass:
        add(violations, "gpu_preflight_pass_required_but_not_present")
    if gpu_pass:
        if lock.get("status") != "PASS_GPU_PREFLIGHT_COMPLETE":
            add(violations, "gpu_pass_status_mismatch", actual=lock.get("status"))
        expected_prompt_rows = 481 * 5
        if prompt_summary.get("status") != "PASS":
            add(violations, "gpu_pass_prompt_summary_not_pass", actual=prompt_summary.get("status"))
        if prompt_summary.get("actual_prompt_rows") != expected_prompt_rows:
            add(
                violations,
                "prompt_row_count_mismatch",
                expected=expected_prompt_rows,
                actual=prompt_summary.get("actual_prompt_rows"),
            )
        if len(prompt_rows) != expected_prompt_rows:
            add(violations, "prompt_audit_jsonl_row_count_mismatch", expected=expected_prompt_rows, actual=len(prompt_rows))
        if h2.get("status") != "PASS":
            add(violations, "h2_shared_prompt_identity_not_pass", actual=h2.get("status"))
        if tokenizer.get("status") != "PASS":
            add(violations, "tokenizer_not_pass", actual=tokenizer.get("status"))
        if model.get("status") not in {"PASS", "PASS_TOKENIZER_ONLY"}:
            add(violations, "model_asset_not_pass", actual=model.get("status"))
        if not env.get("environment_matches_expected"):
            add(violations, "environment_not_expected")
    else:
        if lock.get("status") not in {"PENDING_GPU_EXECUTION", "FAIL_GPU_PREFLIGHT"}:
            add(violations, "non_gpu_pass_status_invalid", actual=lock.get("status"))
        if lock.get("status") == "PENDING_GPU_EXECUTION" and prompt_summary.get("status") != "NOT_RUN":
            add(violations, "pending_prompt_summary_should_be_not_run", actual=prompt_summary.get("status"))

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "lock_status": lock.get("status"),
        "gpu_environment_preflight_passed": gpu_pass,
        "confirmation_predictions_created": lock.get("confirmation_predictions_created"),
        "confirmation_run_allowed_now": lock.get("confirmation_run_allowed_now"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-dir", default="stage6_gpu_preflight")
    parser.add_argument("--require-gpu-pass", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate(Path(args.preflight_dir), require_gpu_pass=args.require_gpu_pass)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
