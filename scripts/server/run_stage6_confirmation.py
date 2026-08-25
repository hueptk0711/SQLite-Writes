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
        "config_path": "configs/stage5/resolved_direct_confirmation.json",
        "raw_generation_path": "raw_generations/direct.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/direct",
    },
    "j_fs": {
        "method_id": "J-FS-M",
        "config_path": "configs/stage5/resolved_j_fs_confirmation.json",
        "raw_generation_path": "raw_generations/j_fs.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/j_fs",
    },
    "original_mp_fs_plus": {
        "method_id": "MP-FS+",
        "config_path": "configs/stage5/resolved_original_mp_fs_plus.json",
        "raw_generation_path": "raw_generations/original_mp_fs_plus.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/original_mp_fs_plus",
    },
    "shared_mp_fs_plus_generation": {
        "method_id": "MP-FS+",
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


def rows_for_stream(index: dict[tuple[str, str], dict[str, Any]], stream: str) -> list[dict[str, Any]]:
    rows = [row for (arm, _sample_id), row in index.items() if arm == stream]
    if len(rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{stream} prompt audit must contain 481 rows, got {len(rows)}")
    return sorted(rows, key=lambda row: str(row["stage6_sample_id"]))


def verify_stream_input_identity(
    *,
    stream: str,
    expected_index: dict[tuple[str, str], dict[str, Any]],
    current_rows: list[dict[str, Any]],
) -> None:
    expected_rows = {str(row["stage6_sample_id"]): row for row in rows_for_stream(expected_index, stream)}
    if len(current_rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{stream} current audit must contain 481 rows before generation")
    for row in current_rows:
        sample_id = str(row.get("stage6_sample_id"))
        expected = expected_rows.get(sample_id)
        if expected is None:
            raise HarnessError(f"{stream} current audit has unknown sample {sample_id}")
        for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
            if row.get(field) != expected.get(field):
                raise HarnessError(
                    f"{stream} input identity mismatch for {sample_id}: {field}"
                )


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
    if str(generation_row.get("sample_id") or generation_row.get("stage6_sample_id")) != sample_id:
        raise HarnessError(f"{stream} generation row sample mismatch for {sample_id}")
    raw_output = str(generation_row.get("raw_output") or "")
    row = {
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
        "run_id": run_id,
    }
    row["raw_generation_row_sha256"] = canonical_row_hash(row)
    validate_raw_generation_rows([row])
    return row


def validate_raw_generation_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        missing = [field for field in REQUIRED_RAW_ROW_FIELDS if field not in row]
        if missing:
            raise HarnessError(f"Raw generation row missing required fields: {missing}")
        expected_row_hash = canonical_row_hash(row)
        if row.get("raw_generation_row_sha256") != expected_row_hash:
            raise HarnessError("Raw generation row hash mismatch")


def write_checkpoint(raw_path: Path, rows: list[dict[str, Any]]) -> Path:
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


def verify_resume_checkpoint(raw_path: Path, checkpoint_path: Path) -> None:
    rows = read_jsonl(raw_path)
    validate_raw_generation_rows(rows)
    checkpoint = read_json(checkpoint_path)
    expected = checkpoint.get("rows") or {}
    actual = {
        str(row["stage6_sample_id"]): row["raw_generation_row_sha256"]
        for row in rows
    }
    if actual != expected:
        raise HarnessError("Existing raw rows do not match resume checkpoint")


def run_stream_with_guard(
    *,
    stream: str,
    expected_index: dict[tuple[str, str], dict[str, Any]],
    current_rows: list[dict[str, Any]],
    generation_callable: Callable[[], list[dict[str, Any]]],
    output_root: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    if stream not in STREAMS:
        raise HarnessError(f"Unknown generation stream: {stream}")
    verify_stream_input_identity(stream=stream, expected_index=expected_index, current_rows=current_rows)
    generated = generation_callable()
    if len(generated) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{stream} generation produced {len(generated)} rows, expected 481")
    audit_by_id = {str(row["stage6_sample_id"]): row for row in current_rows}
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
    raw_path = output_root / STREAMS[stream]["raw_generation_path"]
    write_jsonl(raw_path, normalized)
    write_checkpoint(raw_path, normalized)
    return normalized


def make_replay_provenance_rows(
    shared_rows: list[dict[str, Any]],
    *,
    replay_arm: str,
) -> list[dict[str, Any]]:
    if replay_arm not in {"d_g1_control", "d_f_g1_vnext"}:
        raise HarnessError("Only D_G1 and D_F_G1 deterministic replays may use shared rows")
    return [
        {
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
        "pre_generation_phases": [
            "verify_stage6g_authorization_with_expected_git_head_and_clean_worktree",
            "verify_zero_existing_raw_generation_files_for_initial_run",
            "recompute_current_prompt_token_audit_for_stream",
            "compare_prompt_chat_input_ids_and_token_count_481_of_481_before_generation",
        ],
        "post_generation_phases": [
            "normalize_raw_rows_to_stage6g_schema",
            "write_raw_generation_row_sha256",
            "write_stream_checkpoint_manifest",
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.setup_only:
        raise SystemExit(
            "Stage6H GPU execution is intentionally not enabled in setup package; "
            "rerun with --setup-only for CPU-only artifact creation."
        )
    lock = create_setup(Path(args.output_dir))
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
