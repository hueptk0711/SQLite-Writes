#!/usr/bin/env python3
"""Validate ingested Stage6F GPU preflight server output."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


EXPECTED_SERVER_ZIP_SHA256 = "004913b1778cc145d44f32aa49a60039438b24cc36d1f17a5c85091e8cc5bd1b"
EXPECTED_EXECUTION_COMMIT = "dfa1be8d9db133f6adb6ec3a796c9750db67b5b9"
EXPECTED_STAGE6E_COMMIT = "f32e8b2c7152e0f31829eab004da0f396084e57e"
EXPECTED_N = 481
EXPECTED_PROMPT_ROWS = 2405
EXPECTED_PROMPT_TOKEN_AUDIT_SHA256 = (
    "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6"
)
EXPECTED_MODEL_AGGREGATE_SHA256 = "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c"
EXPECTED_TOKENIZER_SHA256 = "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468"
EXPECTED_MODEL_CONFIG_SHA256 = "326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357"

SERVER_RELATIVE = Path(
    "server_output_zip_extract_patch2"
) / "stage6f_gpu_preflight_patch2_outputs" / "stage6_gpu_preflight"

REQUIRED_SERVER_FILES = [
    "STAGE6F_GPU_PREFLIGHT_LOCK.json",
    "GPU_ENVIRONMENT_MANIFEST.json",
    "MODEL_ASSET_MANIFEST.json",
    "MODEL_TOKENIZER_ASSET_AUDIT.json",
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def file_manifest(server_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for relative in REQUIRED_SERVER_FILES:
        path = server_dir / relative
        rows.append(
            {
                "path": relative,
                "sha256": sha256_file(path) if path.is_file() else None,
                "bytes": path.stat().st_size if path.is_file() else None,
            }
        )
    return rows


def zip_member_manifest(zip_path: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"Bad ZIP member: {bad}")
        for name in sorted(item for item in archive.namelist() if not item.endswith("/")):
            normalized = name
            for prefix in (
                "stage6f_gpu_preflight_patch2_outputs/stage6_gpu_preflight/",
                "stage6f_gpu_preflight_outputs/stage6_gpu_preflight/",
            ):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix):]
                    break
            manifest[normalized] = hashlib.sha256(archive.read(name)).hexdigest()
    return manifest


def validate(acceptance_dir: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    server_dir = acceptance_dir / SERVER_RELATIVE
    lock_path = acceptance_dir / "STAGE6F_GPU_PREFLIGHT_ACCEPTANCE_LOCK.json"
    server_manifest_path = acceptance_dir / "SERVER_OUTPUT_MANIFEST.json"

    for relative in REQUIRED_SERVER_FILES:
        if not (server_dir / relative).is_file():
            add(violations, "server_artifact_missing", path=relative)
    if not lock_path.is_file():
        add(violations, "acceptance_lock_missing")
    if not server_manifest_path.is_file():
        add(violations, "server_output_manifest_missing")
    if violations:
        return {"status": "FAIL", "violations": violations}

    preflight_lock = read_json(server_dir / "STAGE6F_GPU_PREFLIGHT_LOCK.json")
    prompt_summary = read_json(server_dir / "PROMPT_TOKEN_SUMMARY.json")
    h2 = read_json(server_dir / "H2_SHARED_PROMPT_IDENTITY_AUDIT.json")
    model = read_json(server_dir / "MODEL_ASSET_MANIFEST.json")
    asset_audit = read_json(server_dir / "MODEL_TOKENIZER_ASSET_AUDIT.json")
    tokenizer = read_json(server_dir / "TOKENIZER_MANIFEST.json")
    env = read_json(server_dir / "GPU_ENVIRONMENT_MANIFEST.json")
    smoke = read_json(server_dir / "SYNTHETIC_GPU_SMOKE_REPORT.json")
    run_plan = read_json(server_dir / "CONFIRMATION_RUN_PLAN.json")
    acceptance_lock = read_json(lock_path)
    server_manifest = read_json(server_manifest_path)
    prompt_rows = read_jsonl(server_dir / "PROMPT_TOKEN_AUDIT.jsonl")
    server_zip = acceptance_dir / "server_output_zip" / "Stage6F_GPU_PREFLIGHT_PATCH2_SERVER_OUTPUT_20260825.zip"

    expected_lock = {
        "stage": "Stage6F_GPU_PREFLIGHT_ACCEPTANCE",
        "status": "GPU_PREFLIGHT_ACCEPTED_PENDING_REVIEWER_APPROVAL",
        "server_output_zip_sha256": EXPECTED_SERVER_ZIP_SHA256,
        "server_preflight_status": "PASS_GPU_PREFLIGHT_COMPLETE",
        "gpu_environment_preflight_passed": True,
        "confirmation_predictions_created": False,
        "confirmation_run_allowed_now": False,
        "final_confirmation_n": EXPECTED_N,
        "prompt_token_rows": EXPECTED_PROMPT_ROWS,
        "prompt_token_audit_sha256": EXPECTED_PROMPT_TOKEN_AUDIT_SHA256,
    }
    for key, expected in expected_lock.items():
        if acceptance_lock.get(key) != expected:
            add(
                violations,
                "acceptance_lock_field_mismatch",
                field=key,
                expected=expected,
                actual=acceptance_lock.get(key),
            )

    if server_manifest.get("server_output_zip_sha256") != EXPECTED_SERVER_ZIP_SHA256:
        add(
            violations,
            "server_zip_sha256_mismatch",
            expected=EXPECTED_SERVER_ZIP_SHA256,
            actual=server_manifest.get("server_output_zip_sha256"),
        )
    if not server_zip.is_file():
        add(violations, "nested_server_zip_missing", path=str(server_zip))
    else:
        actual_zip_sha = sha256_file(server_zip)
        if actual_zip_sha != EXPECTED_SERVER_ZIP_SHA256:
            add(
                violations,
                "nested_server_zip_actual_sha256_mismatch",
                expected=EXPECTED_SERVER_ZIP_SHA256,
                actual=actual_zip_sha,
            )
        try:
            zip_manifest = zip_member_manifest(server_zip)
            extracted = {
                row["path"]: row["sha256"]
                for row in file_manifest(server_dir)
                if row["sha256"] is not None
            }
            if zip_manifest != extracted:
                add(violations, "extracted_server_artifacts_do_not_match_zip")
        except Exception as exc:
            add(violations, "nested_server_zip_unreadable", error=str(exc))
    if server_manifest.get("server_file_manifest") != file_manifest(server_dir):
        add(violations, "server_file_manifest_mismatch")

    if preflight_lock.get("status") != "PASS_GPU_PREFLIGHT_COMPLETE":
        add(violations, "server_preflight_status_not_pass", actual=preflight_lock.get("status"))
    if preflight_lock.get("observed_git_head") != EXPECTED_EXECUTION_COMMIT:
        add(violations, "execution_commit_mismatch", actual=preflight_lock.get("observed_git_head"))
    if preflight_lock.get("stage6e_accepted_commit") != EXPECTED_STAGE6E_COMMIT:
        add(violations, "stage6e_commit_mismatch", actual=preflight_lock.get("stage6e_accepted_commit"))
    if preflight_lock.get("confirmation_predictions_created") is not False:
        add(violations, "confirmation_predictions_created")
    if preflight_lock.get("model_generate_called_for_confirmation_samples") is not False:
        add(violations, "model_generate_called_for_confirmation_samples")
    if preflight_lock.get("confirmation_run_allowed_now") is not False:
        add(violations, "confirmation_run_allowed_now_not_false")

    if not env.get("environment_matches_expected"):
        add(violations, "environment_not_expected")
    if model.get("status") != "PASS":
        add(violations, "model_asset_status_not_pass", actual=model.get("status"))
    if model.get("model_generate_called_for_confirmation_samples") is not False:
        add(violations, "model_generate_called_for_confirmation_samples")
    if asset_audit.get("status") != "PASS":
        add(violations, "model_tokenizer_asset_audit_not_pass", actual=asset_audit.get("status"))
    expected_asset_values = {
        "actual_model_aggregate_sha256": EXPECTED_MODEL_AGGREGATE_SHA256,
        "actual_tokenizer_sha256": EXPECTED_TOKENIZER_SHA256,
        "actual_model_config_sha256": EXPECTED_MODEL_CONFIG_SHA256,
    }
    for field, expected in expected_asset_values.items():
        if asset_audit.get(field) != expected:
            add(
                violations,
                "model_tokenizer_asset_field_mismatch",
                field=field,
                expected=expected,
                actual=asset_audit.get(field),
            )
    for field in ("model_aggregate_match", "tokenizer_match", "model_config_match"):
        if asset_audit.get(field) is not True:
            add(violations, "model_tokenizer_asset_match_not_true", field=field)
    if tokenizer.get("status") != "PASS":
        add(violations, "tokenizer_status_not_pass", actual=tokenizer.get("status"))
    if not env.get("sqlite_runtime", {}).get("sqlite_version"):
        add(violations, "sqlite_runtime_missing")
    if "CUDA_VISIBLE_DEVICES" not in (env.get("environment_variables") or {}):
        add(violations, "cuda_visible_devices_not_captured")
    if smoke.get("confirmation_predictions_created") is not False:
        add(violations, "smoke_created_confirmation_predictions")
    if smoke.get("status") != "PASS":
        add(violations, "synthetic_smoke_not_pass", actual=smoke.get("status"))
    if smoke.get("confirmation_samples_used") != 0:
        add(violations, "synthetic_smoke_used_confirmation_samples")
    if smoke.get("model_generate_called_for_synthetic_smoke") is not True:
        add(violations, "synthetic_smoke_generate_not_called")
    if smoke.get("model_generate_called_for_confirmation_samples") is not False:
        add(violations, "synthetic_smoke_called_confirmation_generation")

    if prompt_summary.get("status") != "PASS":
        add(violations, "prompt_summary_not_pass", actual=prompt_summary.get("status"))
    if prompt_summary.get("actual_prompt_rows") != EXPECTED_PROMPT_ROWS:
        add(violations, "prompt_row_count_mismatch", actual=prompt_summary.get("actual_prompt_rows"))
    if len(prompt_rows) != EXPECTED_PROMPT_ROWS:
        add(violations, "prompt_audit_jsonl_row_count_mismatch", actual=len(prompt_rows))
    if prompt_summary.get("input_truncation_error_count") != 0:
        add(violations, "input_truncation_error_count_nonzero")
    if prompt_summary.get("prompt_token_audit_sha256") != EXPECTED_PROMPT_TOKEN_AUDIT_SHA256:
        add(violations, "prompt_token_audit_sha256_mismatch")

    expected_rows_per_arm = {
        "d_f_g1_vnext": EXPECTED_N,
        "d_g1_control": EXPECTED_N,
        "direct": EXPECTED_N,
        "j_fs": EXPECTED_N,
        "original_mp_fs_plus": EXPECTED_N,
    }
    if prompt_summary.get("rows_per_arm") != expected_rows_per_arm:
        add(violations, "rows_per_arm_mismatch", actual=prompt_summary.get("rows_per_arm"))
    if h2.get("status") != "PASS" or h2.get("mismatch_count") != 0:
        add(violations, "h2_shared_prompt_identity_not_pass")
    if h2.get("independent_D_F_G1_generation_allowed") is not False:
        add(violations, "independent_dfg1_generation_allowed")
    if h2.get("F_changes_prompt_surface") is not False:
        add(violations, "f_changes_prompt_surface")

    rows_by_sample: dict[str, dict[str, dict[str, Any]]] = {}
    for row in prompt_rows:
        rows_by_sample.setdefault(str(row["stage6_sample_id"]), {})[str(row["arm"])] = row
    h2_input_id_mismatches = []
    for sample_id, arms in rows_by_sample.items():
        d_g1 = arms.get("d_g1_control")
        d_f_g1 = arms.get("d_f_g1_vnext")
        if not d_g1 or not d_f_g1:
            h2_input_id_mismatches.append({"stage6_sample_id": sample_id, "reason": "missing_h2_arm"})
            continue
        for key in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
            if d_g1.get(key) != d_f_g1.get(key):
                h2_input_id_mismatches.append({"stage6_sample_id": sample_id, "field": key})
    if h2_input_id_mismatches:
        add(
            violations,
            "h2_input_identity_mismatch",
            mismatch_count=len(h2_input_id_mismatches),
            examples=h2_input_id_mismatches[:10],
        )

    streams = run_plan.get("generation_streams") or {}
    shared = streams.get("shared_mp_fs_plus_generation") or {}
    if shared.get("raw_generation_path") != "raw_generations/shared_mp_fs_plus_generation.jsonl":
        add(violations, "shared_generation_path_mismatch")
    if shared.get("deterministic_replay_arms") != ["d_g1_control", "d_f_g1_vnext"]:
        add(violations, "shared_generation_replay_arms_mismatch")

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "server_preflight_status": preflight_lock.get("status"),
        "final_confirmation_n": prompt_summary.get("final_confirmation_n"),
        "prompt_token_rows": len(prompt_rows),
        "confirmation_predictions_created": preflight_lock.get("confirmation_predictions_created"),
        "confirmation_run_allowed_now": preflight_lock.get("confirmation_run_allowed_now"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance-dir", default="stage6_gpu_preflight_acceptance")
    return parser.parse_args()


def main() -> None:
    report = validate(Path(parse_args().acceptance_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
