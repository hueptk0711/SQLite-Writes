#!/usr/bin/env python3
"""Stage6H confirmatory execution harness.

This harness is an orchestration/provenance layer around the frozen method. It
does not change the method implementation. Its setup mode is CPU-only; later GPU
execution must use the same guard functions before any call to model.generate().
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]

AUTHORIZATION_DIR = "stage6_confirmation_run_authorization"
STAGE6H_DIR = "stage6_confirmation_execution"
STAGE6G_AUTHORIZATION_COMMIT = "ead5015c3efaa174772e8595b7b65a8f5c032166"
FINAL_CONFIRMATION_N = 481
PROMPT_TOKEN_AUDIT_SHA256 = "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6"
GENERATION_LOCK_SHA256 = "890da57fbe137f4b3f64e002a29d76a3c630cdc8290c924ac1d1f9da585a9c14"
MODEL_SHA256 = "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c"
TOKENIZER_SHA256 = "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"

STREAMS = {
    "direct": {
        "method_id": "D-FS-M",
        "prompt_audit_arm": "direct",
        "config_path": "configs/stage5/resolved_direct_confirmation.json",
        "raw_generation_path": "raw_generations/direct.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/direct",
    },
    "j_fs": {
        "method_id": "J-FS-M",
        "prompt_audit_arm": "j_fs",
        "config_path": "configs/stage5/resolved_j_fs_confirmation.json",
        "raw_generation_path": "raw_generations/j_fs.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/j_fs",
    },
    "original_mp_fs_plus": {
        "method_id": "MP-FS+",
        "prompt_audit_arm": "original_mp_fs_plus",
        "config_path": "configs/stage5/resolved_original_mp_fs_plus.json",
        "raw_generation_path": "raw_generations/original_mp_fs_plus.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/original_mp_fs_plus",
    },
    "shared_mp_fs_plus_generation": {
        "method_id": "MP-FS+",
        "prompt_audit_arm": "d_g1_control",
        "identity_audit_arm": "d_f_g1_vnext",
        "config_path": "configs/stage5/resolved_d_g1_control.json",
        "raw_generation_path": "raw_generations/shared_mp_fs_plus_generation.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/shared_mp_fs_plus_generation",
        "deterministic_replay_arms": ["d_g1_control", "d_f_g1_vnext"],
    },
}

REQUIRED_AUDIT_FIELDS = [
    "stage6_sample_id",
    "arm",
    "prompt_sha256",
    "chat_prompt_sha256",
    "input_ids_sha256",
    "input_token_count",
]

REQUIRED_RAW_ROW_FIELDS = [
    "sample_id",
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
    "generation_status",
    "generation_error",
    "run_id",
    "raw_generation_row_sha256",
]

REQUIRED_REPLAY_FIELDS = [
    "stage6_sample_id",
    "replay_arm",
    "source_raw_generation_stream",
    "shared_raw_generation_row_sha256",
]


class HarnessError(RuntimeError):
    """Raised when a Stage6 execution guard blocks generation."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_row_hash(row: dict[str, Any]) -> str:
    value = {key: item for key, item in row.items() if key != "raw_generation_row_sha256"}
    return sha256_text(canonical_json(value))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


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


def load_stage6g_validator(repo_root: Path):
    path = repo_root / "scripts" / "data" / "validate_stage6g_confirmation_authorization.py"
    spec = importlib.util.spec_from_file_location("validate_stage6g_confirmation_authorization", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def verify_authorization_boundary(
    repo_root: Path,
    *,
    expected_git_head: str,
    require_git_clean: bool = True,
) -> dict[str, Any]:
    validator = load_stage6g_validator(repo_root)
    report = validator.validate(
        repo_root / AUTHORIZATION_DIR,
        repo_root=repo_root,
        expected_git_head=expected_git_head,
        require_git_clean=require_git_clean,
        check_absent_raw_generations=True,
    )
    if report["status"] != "PASS":
        raise HarnessError(f"Stage6G authorization boundary failed: {report['violations']}")
    return report


def load_prompt_audit(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = read_jsonl(path)
    if len(rows) != FINAL_CONFIRMATION_N * 5:
        raise HarnessError(f"Prompt audit must contain 2405 rows, got {len(rows)}")
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        for field in REQUIRED_AUDIT_FIELDS:
            if field not in row:
                raise HarnessError(f"Prompt audit row missing {field}")
        key = (str(row["arm"]), str(row["stage6_sample_id"]))
        if key in index:
            raise HarnessError(f"Duplicate prompt audit key: {key}")
        index[key] = row
    return index


def row_sample_id(row: dict[str, Any]) -> str:
    sample_id = row.get("sample_id", row.get("stage6_sample_id"))
    if sample_id is None:
        raise HarnessError("Row is missing sample_id/stage6_sample_id")
    return str(sample_id)


def assert_exact_sample_coverage(
    rows: list[dict[str, Any]],
    expected_sample_ids: set[str],
    *,
    context: str,
    allow_partial: bool = False,
) -> None:
    actual_ids = [row_sample_id(row) for row in rows]
    if len(actual_ids) != len(rows):
        raise HarnessError(f"{context} sample ID extraction failed")
    duplicate_count = len(actual_ids) - len(set(actual_ids))
    if duplicate_count:
        raise HarnessError(f"{context} has {duplicate_count} duplicate sample IDs")
    actual_set = set(actual_ids)
    unexpected = sorted(actual_set - expected_sample_ids)
    if unexpected:
        raise HarnessError(f"{context} has unexpected sample IDs: {unexpected[:10]}")
    if not allow_partial:
        missing = sorted(expected_sample_ids - actual_set)
        if missing:
            raise HarnessError(f"{context} is missing expected sample IDs: {missing[:10]}")
        if len(rows) != FINAL_CONFIRMATION_N:
            raise HarnessError(f"{context} must contain 481 rows, got {len(rows)}")


def prompt_audit_arm_for_stream(stream: str) -> str:
    if stream not in STREAMS:
        raise HarnessError(f"Unknown generation stream: {stream}")
    return str(STREAMS[stream]["prompt_audit_arm"])


def rows_for_stream(index: dict[tuple[str, str], dict[str, Any]], stream: str) -> list[dict[str, Any]]:
    arm_name = prompt_audit_arm_for_stream(stream)
    rows = [row for (arm, _sample_id), row in index.items() if arm == arm_name]
    if len(rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{stream}/{arm_name} prompt audit must contain 481 rows, got {len(rows)}")
    expected_ids = {str(row["stage6_sample_id"]) for row in rows}
    assert_exact_sample_coverage(rows, expected_ids, context=f"{stream}/{arm_name} prompt audit")
    return sorted(rows, key=lambda row: str(row["stage6_sample_id"]))


def verify_shared_stream_audit_identity(index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    left_rows = rows_for_stream(index, "shared_mp_fs_plus_generation")
    right_arm = str(STREAMS["shared_mp_fs_plus_generation"]["identity_audit_arm"])
    right_rows = [row for (arm, _sample_id), row in index.items() if arm == right_arm]
    if len(right_rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{right_arm} prompt audit must contain 481 rows, got {len(right_rows)}")
    left = {str(row["stage6_sample_id"]): row for row in left_rows}
    right = {str(row["stage6_sample_id"]): row for row in right_rows}
    if set(left) != set(right):
        raise HarnessError("D_G1 and D_F_G1 audit sample IDs differ")
    checked = 0
    for sample_id, left_row in left.items():
        right_row = right[sample_id]
        for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
            if left_row.get(field) != right_row.get(field):
                raise HarnessError(f"H2 shared audit identity mismatch for {sample_id}: {field}")
        checked += 1
    return {
        "status": "PASS",
        "checked_pairs": checked,
        "prompt_audit_arm": "d_g1_control",
        "identity_audit_arm": right_arm,
    }


def verify_stream_input_identity(
    *,
    stream: str,
    expected_index: dict[tuple[str, str], dict[str, Any]],
    current_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if stream == "shared_mp_fs_plus_generation":
        verify_shared_stream_audit_identity(expected_index)
    expected_rows = {str(row["stage6_sample_id"]): row for row in rows_for_stream(expected_index, stream)}
    assert_exact_sample_coverage(
        current_rows,
        set(expected_rows),
        context=f"{stream} current runtime inputs",
    )
    for row in current_rows:
        sample_id = row_sample_id(row)
        expected = expected_rows.get(sample_id)
        if expected is None:
            raise HarnessError(f"{stream} current audit has unknown sample {sample_id}")
        for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
            if row.get(field) != expected.get(field):
                raise HarnessError(
                    f"{stream} input identity mismatch for {sample_id}: {field}"
                )
    return sorted(current_rows, key=row_sample_id)


def check_clean_initial_outputs(repo_root: Path) -> None:
    existing = [
        stream["raw_generation_path"]
        for stream in STREAMS.values()
        if (repo_root / stream["raw_generation_path"]).exists()
    ]
    if existing:
        raise HarnessError(f"Pre-existing raw generation files block initial execution: {existing}")


def validate_model_identity(metadata: dict[str, Any]) -> None:
    expected = {
        "model_revision": MODEL_REVISION,
        "model_sha256": MODEL_SHA256,
        "tokenizer_sha256": TOKENIZER_SHA256,
        "generation_lock_sha256": GENERATION_LOCK_SHA256,
    }
    mismatches = {
        key: {"expected": expected[key], "actual": metadata.get(key)}
        for key in expected
        if metadata.get(key) != expected[key]
    }
    if mismatches:
        raise HarnessError(f"Model/generation identity mismatch: {mismatches}")


def normalize_raw_generation_row(
    *,
    stream: str,
    audit_row: dict[str, Any],
    generation_row: dict[str, Any],
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    validate_model_identity(metadata)
    sample_id = str(audit_row["stage6_sample_id"])
    if row_sample_id(generation_row) != sample_id:
        raise HarnessError(f"{stream} generation row sample mismatch for {sample_id}")
    if generation_row.get("stage6_sample_id") is not None and str(generation_row["stage6_sample_id"]) != sample_id:
        raise HarnessError(f"{stream} generation row stage6_sample_id mismatch for {sample_id}")
    for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
        actual = generation_row.get(field, generation_row.get(f"actual_{field}"))
        if actual != audit_row.get(field):
            raise HarnessError(f"{stream} actual generation input mismatch for {sample_id}: {field}")
    raw_output = str(generation_row.get("raw_output") or "")
    row = {
        "sample_id": sample_id,
        "stage6_sample_id": sample_id,
        "generation_stream": stream,
        "prompt_sha256": audit_row["prompt_sha256"],
        "chat_prompt_sha256": audit_row["chat_prompt_sha256"],
        "input_ids_sha256": audit_row["input_ids_sha256"],
        "input_token_count": audit_row["input_token_count"],
        "raw_output": raw_output,
        "raw_output_sha256": sha256_text(raw_output),
        "output_token_count": generation_row.get("output_token_count", generation_row.get("output_tokens")),
        "hit_max_new_tokens": bool(generation_row.get("hit_max_new_tokens")),
        "model_revision": metadata["model_revision"],
        "model_sha256": metadata["model_sha256"],
        "tokenizer_sha256": metadata["tokenizer_sha256"],
        "generation_lock_sha256": metadata["generation_lock_sha256"],
        "generation_status": generation_row.get("generation_status", generation_row.get("status", "success")),
        "generation_error": generation_row.get("generation_error", generation_row.get("error")),
        "latency_sec": generation_row.get("latency_sec"),
        "run_id": run_id,
    }
    row["raw_generation_row_sha256"] = canonical_row_hash(row)
    validate_raw_generation_rows([row])
    return row


def validate_raw_generation_rows(
    rows: list[dict[str, Any]],
    *,
    expected_sample_ids: set[str] | None = None,
    full_completion: bool = False,
) -> None:
    if expected_sample_ids is not None:
        assert_exact_sample_coverage(
            rows,
            expected_sample_ids,
            context="raw generation rows",
            allow_partial=not full_completion,
        )
    for row in rows:
        missing = [field for field in REQUIRED_RAW_ROW_FIELDS if field not in row]
        if missing:
            raise HarnessError(f"Raw generation row missing required fields: {missing}")
        if str(row["sample_id"]) != str(row["stage6_sample_id"]):
            raise HarnessError("Raw generation row sample_id and stage6_sample_id differ")
        expected_row_hash = canonical_row_hash(row)
        if row.get("raw_generation_row_sha256") != expected_row_hash:
            raise HarnessError("Raw generation row hash mismatch")


def write_checkpoint(
    raw_path: Path,
    rows: list[dict[str, Any]],
    *,
    expected_sample_ids: set[str] | None = None,
    full_completion: bool = False,
) -> Path:
    validate_raw_generation_rows(
        rows,
        expected_sample_ids=expected_sample_ids,
        full_completion=full_completion,
    )
    checkpoint = raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")
    payload = {
        "raw_generation_path": raw_path.as_posix(),
        "row_count": len(rows),
        "rows": {
            str(row["stage6_sample_id"]): row["raw_generation_row_sha256"]
            for row in rows
        },
    }
    write_json(checkpoint, payload)
    return checkpoint


def verify_resume_checkpoint(
    raw_path: Path,
    checkpoint_path: Path,
    *,
    expected_sample_ids: set[str] | None = None,
    full_completion: bool = False,
) -> None:
    rows = read_jsonl(raw_path)
    validate_raw_generation_rows(
        rows,
        expected_sample_ids=expected_sample_ids,
        full_completion=full_completion,
    )
    checkpoint = read_json(checkpoint_path)
    expected = checkpoint.get("rows") or {}
    if checkpoint.get("row_count") != len(rows):
        raise HarnessError("Resume checkpoint row_count does not match raw rows")
    if len(expected) != len(rows):
        raise HarnessError("Resume checkpoint rows map size does not match raw rows")
    actual = {
        str(row["stage6_sample_id"]): row["raw_generation_row_sha256"]
        for row in rows
    }
    if actual != expected:
        raise HarnessError("Existing raw rows do not match resume checkpoint")


def write_rows_incrementally(
    raw_path: Path,
    rows: list[dict[str, Any]],
    *,
    existing_rows: list[dict[str, Any]] | None = None,
    expected_sample_ids: set[str] | None = None,
) -> None:
    accumulated = list(existing_rows or [])
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        accumulated.append(row)
        write_jsonl(raw_path, accumulated)
        write_checkpoint(raw_path, accumulated, expected_sample_ids=expected_sample_ids, full_completion=False)
    if expected_sample_ids is not None and len(accumulated) == len(expected_sample_ids):
        write_checkpoint(raw_path, accumulated, expected_sample_ids=expected_sample_ids, full_completion=True)


def run_stream_with_guard(
    *,
    stream: str,
    expected_index: dict[tuple[str, str], dict[str, Any]],
    current_rows: list[dict[str, Any]],
    generation_callable: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    output_root: Path,
    run_id: str,
    metadata: dict[str, Any],
    mode: str = "initial",
) -> list[dict[str, Any]]:
    if stream not in STREAMS:
        raise HarnessError(f"Unknown generation stream: {stream}")
    if mode not in {"initial", "resume"}:
        raise HarnessError(f"Unknown execution mode: {mode}")
    verified_rows = verify_stream_input_identity(
        stream=stream,
        expected_index=expected_index,
        current_rows=current_rows,
    )
    expected_sample_ids = {str(row["stage6_sample_id"]) for row in rows_for_stream(expected_index, stream)}
    raw_path = output_root / STREAMS[stream]["raw_generation_path"]
    checkpoint_path = raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")
    existing_rows: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    if mode == "initial":
        if raw_path.exists() or checkpoint_path.exists():
            raise HarnessError(f"{stream} initial execution requires no existing raw/checkpoint file")
    else:
        if not raw_path.exists() or not checkpoint_path.exists():
            raise HarnessError(f"{stream} resume execution requires existing raw and checkpoint files")
        verify_resume_checkpoint(
            raw_path,
            checkpoint_path,
            expected_sample_ids=expected_sample_ids,
            full_completion=False,
        )
        existing_rows = read_jsonl(raw_path)
        completed_ids = {str(row["stage6_sample_id"]) for row in existing_rows}
    rows_to_generate = [row for row in verified_rows if str(row["stage6_sample_id"]) not in completed_ids]
    generated = generation_callable(rows_to_generate)
    if len(generated) != len(rows_to_generate):
        raise HarnessError(
            f"{stream} generation produced {len(generated)} rows, expected {len(rows_to_generate)}"
        )
    assert_exact_sample_coverage(
        generated,
        {str(row["stage6_sample_id"]) for row in rows_to_generate},
        context=f"{stream} generated rows",
        allow_partial=True,
    )
    audit_by_id = {str(row["stage6_sample_id"]): row for row in rows_to_generate}
    normalized = [
        normalize_raw_generation_row(
            stream=stream,
            audit_row=audit_by_id[str(row.get("sample_id") or row.get("stage6_sample_id"))],
            generation_row=row,
            run_id=run_id,
            metadata=metadata,
        )
        for row in generated
    ]
    combined = existing_rows + normalized
    validate_raw_generation_rows(
        combined,
        expected_sample_ids=expected_sample_ids,
        full_completion=len(combined) == FINAL_CONFIRMATION_N,
    )
    write_rows_incrementally(
        raw_path,
        normalized,
        existing_rows=existing_rows,
        expected_sample_ids=expected_sample_ids,
    )
    return combined


def make_replay_provenance_rows(
    shared_rows: list[dict[str, Any]],
    *,
    replay_arm: str,
) -> list[dict[str, Any]]:
    if replay_arm not in {"d_g1_control", "d_f_g1_vnext"}:
        raise HarnessError("Only D_G1 and D_F_G1 deterministic replays may use shared rows")
    return [
        {
            "sample_id": row["stage6_sample_id"],
            "stage6_sample_id": row["stage6_sample_id"],
            "replay_arm": replay_arm,
            "source_raw_generation_stream": "shared_mp_fs_plus_generation",
            "shared_raw_generation_row_sha256": row["raw_generation_row_sha256"],
        }
        for row in shared_rows
    ]


def validate_shared_replay_provenance(
    d_g1_rows: list[dict[str, Any]],
    d_f_g1_rows: list[dict[str, Any]],
) -> None:
    def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(row["stage6_sample_id"]): row for row in rows}

    left = by_id(d_g1_rows)
    right = by_id(d_f_g1_rows)
    if set(left) != set(right):
        raise HarnessError("D_G1 and D_F_G1 replay provenance sample IDs differ")
    for sample_id in left:
        for row in (left[sample_id], right[sample_id]):
            missing = [field for field in REQUIRED_REPLAY_FIELDS if field not in row]
            if missing:
                raise HarnessError(f"Replay provenance row missing fields: {missing}")
        if left[sample_id]["shared_raw_generation_row_sha256"] != right[sample_id]["shared_raw_generation_row_sha256"]:
            raise HarnessError(f"Shared replay row SHA differs for {sample_id}")


def execution_plan() -> dict[str, Any]:
    return {
        "stage": "Stage6H_CONFIRMATION_EXECUTION_HARNESS",
        "status": "HARNESS_READY_PENDING_REVIEWER_ACCEPTANCE",
        "model_called_in_stage6h_setup": False,
        "gpu_called_in_stage6h_setup": False,
        "confirmation_predictions_created": False,
        "stage6g_authorization_commit": STAGE6G_AUTHORIZATION_COMMIT,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "prompt_token_audit_sha256": PROMPT_TOKEN_AUDIT_SHA256,
        "generation_lock_sha256": GENERATION_LOCK_SHA256,
        "generation_stream_order": [
            "direct",
            "j_fs",
            "original_mp_fs_plus",
            "shared_mp_fs_plus_generation",
        ],
        "generation_streams": STREAMS,
        "execution_modes": {
            "initial": {
                "existing_raw_generation_rows_allowed": False,
                "required_existing_checkpoint": False,
            },
            "resume": {
                "existing_raw_generation_rows_allowed": True,
                "required_existing_checkpoint": True,
                "completed_rows_are_immutable": True,
                "only_unfinished_ids_may_be_generated": True,
            },
        },
        "runtime_input_locks": {
            "stage6f_prompt_token_audit": {
                "path": "stage6f_gpu_preflight_acceptance/server_output/PROMPT_TOKEN_AUDIT.jsonl",
                "sha256": PROMPT_TOKEN_AUDIT_SHA256,
            },
            "stage6g_authorization": {
                "directory": AUTHORIZATION_DIR,
                "commit": STAGE6G_AUTHORIZATION_COMMIT,
            },
            "final_confirmation_manifest": {
                "path": "stage6_final_registration_revision/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
                "sha256": "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7",
            },
            "final_gold_corpus": {
                "path": "stage6_final_registration_revision/FINAL_GOLD_CORPUS.jsonl",
                "sha256": "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16",
            },
            "stage6f_gpu_environment_manifest": {
                "path": "stage6f_gpu_preflight_acceptance/server_output/GPU_ENVIRONMENT_MANIFEST.json",
            },
        },
        "pre_generation_phases": [
            "verify_stage6g_authorization_with_expected_git_head_and_clean_worktree",
            "verify_zero_existing_raw_generation_files_for_initial_run",
            "recompute_current_prompt_token_audit_for_stream",
            "map_shared_generation_stream_to_d_g1_control_prompt_audit_arm",
            "verify_d_g1_control_and_d_f_g1_vnext_input_identity_481_of_481",
            "verify_exact_481_unique_frozen_sample_ids_before_generation",
            "compare_prompt_chat_input_ids_and_token_count_481_of_481_before_generation",
            "pass_verified_runtime_request_objects_directly_to_generation_call",
        ],
        "post_generation_phases": [
            "verify_exact_481_unique_frozen_sample_ids_after_generation",
            "verify_generated_rows_report_same_prompt_chat_input_ids_and_token_count",
            "normalize_raw_rows_to_stage6g_schema",
            "write_sample_id_and_stage6_sample_id_for_reuse_runner_compatibility",
            "preserve_generation_status_error_and_latency_fields",
            "write_raw_generation_row_sha256",
            "write_incremental_stream_checkpoint_manifest",
            "verify_resume_checkpoint_before_any_resume",
            "write_shared_replay_row_sha256_for_d_g1_and_d_f_g1",
        ],
    }


def create_setup(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = execution_plan()
    lock = {
        "stage": "Stage6H_CONFIRMATION_EXECUTION_HARNESS_SETUP",
        "status": "HARNESS_SETUP_LOCKED_PENDING_REVIEWER_ACCEPTANCE",
        "model_called": False,
        "gpu_called": False,
        "confirmation_predictions_created": False,
        "confirmation_run_started": False,
        "stage6g_authorization_commit": STAGE6G_AUTHORIZATION_COMMIT,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "execution_plan_sha256": sha256_text(canonical_json(plan)),
        "required_harness_guards": plan["pre_generation_phases"] + plan["post_generation_phases"],
    }
    write_json(output_dir / "CONFIRMATION_EXECUTION_PLAN.json", plan)
    write_json(output_dir / "STAGE6H_EXECUTION_LOCK.json", lock)
    (output_dir / "REVIEWER_README.md").write_text(
        "# Stage6H Confirmation Execution Harness Setup\n\n"
        "CPU-only setup for the confirmatory execution harness. This package does "
        "not run the model and does not create predictions.\n",
        encoding="utf-8",
    )
    (output_dir / "VALIDATION_REPORT.md").write_text(
        "# Stage6H Validation Report\n\nExpected setup validator status: PASS.\n",
        encoding="utf-8",
    )
    return lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=STAGE6H_DIR)
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--mode", choices=["initial", "resume"], default="initial")
    parser.add_argument("--execute-stream", choices=sorted(STREAMS))
    parser.add_argument("--enable-gpu-execution", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.setup_only:
        if args.execute_stream and not args.enable_gpu_execution:
            raise SystemExit(
                "Stage6H execution CLI is present but GPU execution requires "
                "--enable-gpu-execution after reviewer acceptance."
            )
        raise SystemExit(
            "Stage6H GPU execution is intentionally not enabled in setup package; "
            "rerun with --setup-only for CPU-only artifact creation."
        )
    lock = create_setup(Path(args.output_dir))
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
