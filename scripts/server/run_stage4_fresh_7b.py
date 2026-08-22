#!/usr/bin/env python3
"""Authoritative Stage-4 fresh 7B runner.

The runner is intentionally narrow:

1. verify the accepted protocol commit and clean tree;
2. run the exact tokenizer preflight;
3. generate only Direct, J-FS, and Shared MP-FS+;
4. freeze immutable raw generation files;
5. deterministically reprocess Original, D_G1, D_ONLY, FULL, and NO_C from the
   shared MP-FS+ raw file;
6. record checksums and attempt logs.

No semantic retry is allowed.  Completed raw rows are never regenerated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nldbwrite_v3.common import dump_json, iter_jsonl, sha256_file, write_jsonl  # noqa: E402
from nldbwrite_v3.experiments.run_method import run_method  # noqa: E402
from scripts.analysis.run_stage3_causal_replay import write_json  # noqa: E402
from scripts.analysis.run_stage4_fresh_7b_protocol import (  # noqa: E402
    EXPECTED_SAMPLE_COUNT,
    GENERATION_ARMS,
    MODEL_LOCK,
    stage4_hf_inference_config,
)
from scripts.server.run_stage4_gpu_preflight import (  # noqa: E402
    EXPECTED_GPU_PYTHON_MAJOR_MINOR,
    assert_git_execution_lock,
    environment_version_audit,
    read_ids,
    run_preflight,
)


GENERATION_PLAN = [
    {
        "generation_arm": "direct",
        "process_slug": "direct",
        "config": "configs/stage4/direct.json",
        "output_dir": "methods/direct",
        "central_raw": "raw_generations/direct.jsonl",
    },
    {
        "generation_arm": "j_fs",
        "process_slug": "j_fs",
        "config": "configs/stage4/j_fs.json",
        "output_dir": "methods/j_fs",
        "central_raw": "raw_generations/j_fs.jsonl",
    },
    {
        "generation_arm": "mp_fs_plus_shared",
        "process_slug": "original_mp_fs_plus",
        "config": "configs/stage4/original_mp_fs_plus.json",
        "output_dir": "methods/original_mp_fs_plus",
        "central_raw": "raw_generations/mp_fs_plus_shared.jsonl",
    },
]

DETERMINISTIC_REPROCESS_PLAN = [
    {
        "process_slug": "d_g1_primary",
        "config": "configs/stage4/d_g1_primary.json",
        "output_dir": "methods/d_g1_primary",
        "reuse_central_raw": "raw_generations/mp_fs_plus_shared.jsonl",
    },
    {
        "process_slug": "d_only_secondary",
        "config": "configs/stage4/d_only_secondary.json",
        "output_dir": "methods/d_only_secondary",
        "reuse_central_raw": "raw_generations/mp_fs_plus_shared.jsonl",
    },
    {
        "process_slug": "full_secondary",
        "config": "configs/stage4/full_secondary.json",
        "output_dir": "methods/full_secondary",
        "reuse_central_raw": "raw_generations/mp_fs_plus_shared.jsonl",
    },
    {
        "process_slug": "no_c_secondary",
        "config": "configs/stage4/no_c_secondary.json",
        "output_dir": "methods/no_c_secondary",
        "reuse_central_raw": "raw_generations/mp_fs_plus_shared.jsonl",
    },
]


def json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def write_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def json_file_sha256(value: Any) -> str:
    return hashlib.sha256(write_json_bytes(value)).hexdigest()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_environment_manifest(path: Path, dependency_lock: Path, preflight_summary: dict[str, Any]) -> None:
    env = preflight_summary.get("environment") or {}
    status = "gpu_ready" if env.get("cuda_available") is True else "not_gpu_ready"
    version_audit = preflight_summary.get("environment_version_audit") or environment_version_audit(
        environment=env,
        dependency_lock_path=dependency_lock,
        expected_python_major_minor=EXPECTED_GPU_PYTHON_MAJOR_MINOR,
    )
    if version_audit.get("status") != "PASS":
        status = "environment_version_mismatch"
    write_json(
        path,
        {
            "status": status,
            "source": "scripts/server/run_stage4_fresh_7b.py",
            "dependency_lock": {
                "path": str(dependency_lock.resolve()),
                "sha256": sha256_file(dependency_lock),
            },
            "environment_version_audit": version_audit,
            "environment": env,
        },
    )
    if status == "environment_version_mismatch":
        raise SystemExit("STOP: installed GPU package/Python versions do not match the lock")
    if status != "gpu_ready":
        raise SystemExit("STOP: preflight environment did not report cuda_available=true")


def raw_generation_audit(raw_path: Path, ids_path: Path, *, require_complete: bool) -> dict[str, Any]:
    ids = read_ids(ids_path)
    rows = list(iter_jsonl(raw_path))
    row_ids = [str(row.get("sample_id")) for row in rows]
    missing = [sample_id for sample_id in ids if sample_id not in set(row_ids)]
    duplicate_count = len(row_ids) - len(set(row_ids))
    non_success_rows = [
        {
            "sample_id": str(row.get("sample_id")),
            "status": str(row.get("status") or "success"),
            "input_truncated": bool(row.get("input_truncated")),
        }
        for row in rows
        if str(row.get("status") or "success") != "success"
        or bool(row.get("input_truncated"))
    ]
    complete = (
        not missing
        and duplicate_count == 0
        and len(rows) == len(ids)
        and not non_success_rows
    )
    return {
        "path": str(raw_path.resolve()),
        "sha256": sha256_file(raw_path) if raw_path.is_file() else None,
        "rows": len(rows),
        "expected_rows": len(ids),
        "complete": complete,
        "missing_count": len(missing),
        "missing_sample_ids_first10": missing[:10],
        "duplicate_count": duplicate_count,
        "non_success_count": len(non_success_rows),
        "non_success_rows_first10": non_success_rows[:10],
    }


def verify_raw_complete(raw_path: Path, ids_path: Path) -> dict[str, Any]:
    audit = raw_generation_audit(raw_path, ids_path, require_complete=True)
    if not audit["complete"]:
        raise SystemExit(
            "STOP: raw generation is not a complete valid model generation: "
            f"missing={audit['missing_sample_ids_first10']} "
            f"duplicate_count={audit['duplicate_count']} "
            f"non_success={audit['non_success_rows_first10']}"
        )
    return audit


def assert_partial_raw_has_no_failed_rows(raw_path: Path, ids_path: Path) -> None:
    if not raw_path.is_file():
        return
    audit = raw_generation_audit(raw_path, ids_path, require_complete=False)
    if audit["duplicate_count"] or audit["non_success_count"]:
        raise SystemExit(
            "STOP: existing raw checkpoint contains duplicate or non-success "
            f"rows and cannot be resumed as a valid infrastructure resume: {audit}"
        )


def copy_raw_to_central(source_raw: Path, central_raw: Path, ids_path: Path) -> dict[str, Any]:
    summary = verify_raw_complete(source_raw, ids_path)
    central_raw.parent.mkdir(parents=True, exist_ok=True)
    if central_raw.exists():
        if sha256_file(central_raw) != summary["sha256"]:
            raise SystemExit(f"STOP: central raw already exists with different hash: {central_raw}")
    else:
        shutil.copyfile(source_raw, central_raw)
    return verify_raw_complete(central_raw, ids_path)


def build_runner_plan(result_root: Path) -> dict[str, Any]:
    return {
        "stage": "Stage4_FRESH_7B",
        "sample_count": EXPECTED_SAMPLE_COUNT,
        "generation_arms": sorted(GENERATION_ARMS),
        "model_lock": MODEL_LOCK,
        "raw_generation_policy": {
            "completed_raw_output": "immutable_never_regenerate",
            "infra_crash_before_output": "resume_same_config_only",
            "semantic_retry": False,
            "batch_size": 1,
            "resume_key": "generation_arm+sample_id",
            "valid_completion_required": {
                "selected_ids_present_once": True,
                "status_equals_success": True,
                "input_truncated_false": True,
                "hit_max_new_tokens_success_is_immutable_model_behavior": True,
            },
            "failed_generation_status_policy": {
                "oom": "STOP_preserve_attempt_log_do_not_freeze_central_raw",
                "generation_error": "STOP_preserve_attempt_log_do_not_freeze_central_raw",
                "input_truncation_error": "STOP_protocol_inconsistency_after_preflight",
            },
        },
        "resume_policy": {
            "explicit_resume_flag_required": True,
            "result_root_exists_without_resume": "STOP",
            "result_root_exists_with_resume": "validate_stage4_execution_lock_then_continue",
            "drift_checks": [
                "accepted_protocol_commit",
                "runner_plan_sha256",
                "sample_ids_sha256",
                "inference_config_sha256",
                "dependency_lock_sha256",
                "model_identity",
            ],
        },
        "generation_plan": GENERATION_PLAN,
        "deterministic_reprocess_plan": DETERMINISTIC_REPROCESS_PLAN,
        "result_root": str(result_root.resolve()),
    }


def build_execution_lock(
    *,
    result_root: Path,
    accepted_protocol_commit: str,
    ids_path: Path,
    inference_config_sha256: str,
    dependency_lock_path: Path,
    model_name_or_path: str,
) -> dict[str, Any]:
    plan = build_runner_plan(result_root)
    return {
        "stage": "Stage4_FRESH_7B",
        "accepted_protocol_commit": accepted_protocol_commit,
        "runner_plan_sha256": json_sha256(plan),
        "sample_ids_sha256": sha256_file(ids_path),
        "inference_config_sha256": inference_config_sha256,
        "dependency_lock_sha256": sha256_file(dependency_lock_path),
        "model_identity": {
            "model_name_or_path": model_name_or_path,
            "snapshot_revision": MODEL_LOCK["snapshot_revision"],
            "aggregate_sha256": MODEL_LOCK["aggregate_sha256"],
        },
        "resume_policy": "explicit --resume only; validate lock before continuing",
    }


def initialize_or_validate_result_root(
    *,
    result_root: Path,
    resume: bool,
    execution_lock: dict[str, Any],
) -> None:
    lock_path = result_root / "provenance" / "stage4_execution_lock.json"
    if not result_root.exists():
        if resume:
            raise SystemExit("STOP: --resume was supplied but result_root does not exist")
        result_root.mkdir(parents=True, exist_ok=False)
        (result_root / "provenance").mkdir(parents=True, exist_ok=True)
        write_json(lock_path, execution_lock)
        return
    if not resume:
        raise SystemExit("STOP: result_root already exists; use --resume only for explicit resume")
    if not lock_path.is_file():
        raise SystemExit("STOP: cannot resume because provenance/stage4_execution_lock.json is missing")
    existing = json.loads(lock_path.read_text(encoding="utf-8"))
    mismatches = {
        key: {"existing": existing.get(key), "current": execution_lock.get(key)}
        for key in (
            "accepted_protocol_commit",
            "runner_plan_sha256",
            "sample_ids_sha256",
            "inference_config_sha256",
            "dependency_lock_sha256",
            "model_identity",
        )
        if existing.get(key) != execution_lock.get(key)
    }
    if mismatches:
        raise SystemExit(f"STOP: resume rejected because execution lock drifted: {mismatches}")


def assert_result_root_outside_git(result_root: Path) -> None:
    resolved_result = result_root.resolve()
    try:
        resolved_result.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise SystemExit(
        "STOP: result-root must be outside the git checkout so generated files "
        "do not dirty the accepted protocol tree."
    )


def run_stage4(args: argparse.Namespace) -> dict[str, Any]:
    result_root = Path(args.result_root).resolve()
    protocol_root = Path(args.protocol_root).resolve()
    ids_path = protocol_root / "data" / "fresh_sample_ids.txt"
    if len(read_ids(ids_path)) != EXPECTED_SAMPLE_COUNT:
        raise SystemExit("STOP: frozen Stage-4 sample IDs are not 300 rows")
    plan = build_runner_plan(result_root)
    if args.dry_run:
        result_root.mkdir(parents=True, exist_ok=True)
        write_json(result_root / "runner_dry_run_plan.json", plan)
        return {"status": "DRY_RUN_PASS", **plan}

    assert_result_root_outside_git(result_root)
    git_lock = assert_git_execution_lock(args.accepted_protocol_commit)
    provenance_dir = result_root / "provenance"
    raw_dir = result_root / "raw_generations"
    logs_dir = result_root / "logs"
    inference_config = stage4_hf_inference_config(args.model_name_or_path)
    inference_config_sha256 = json_file_sha256(inference_config)
    execution_lock = build_execution_lock(
        result_root=result_root,
        accepted_protocol_commit=args.accepted_protocol_commit,
        ids_path=ids_path,
        inference_config_sha256=inference_config_sha256,
        dependency_lock_path=Path(args.dependency_lock).resolve(),
        model_name_or_path=args.model_name_or_path,
    )
    initialize_or_validate_result_root(
        result_root=result_root,
        resume=args.resume,
        execution_lock=execution_lock,
    )
    provenance_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    attempt_log = logs_dir / "attempt_log.jsonl"
    write_json(provenance_dir / "runner_plan.json", plan)
    inference_config_path = provenance_dir / "stage4_qwen25_7b_in28672_out4096.json"
    write_json(inference_config_path, inference_config)

    preflight_summary = run_preflight(
        protocol_root=protocol_root,
        fresh_source_data=Path(args.fresh_source_data).resolve(),
        profile_dir=Path(args.profile_dir).resolve(),
        model_name_or_path=args.model_name_or_path,
        output_dir=result_root / "gpu_preflight",
        git_lock=git_lock,
        dependency_lock_path=Path(args.dependency_lock).resolve(),
        expected_python_major_minor=EXPECTED_GPU_PYTHON_MAJOR_MINOR,
    )
    environment_manifest = provenance_dir / "environment_manifest.stage4.json"
    write_environment_manifest(
        environment_manifest,
        Path(args.dependency_lock).resolve(),
        preflight_summary,
    )

    for item in GENERATION_PLAN:
        output_dir = result_root / item["output_dir"]
        central_raw = result_root / item["central_raw"]
        method_raw = output_dir / "raw_generations.jsonl"
        if central_raw.is_file():
            raw_summary = verify_raw_complete(central_raw, ids_path)
            append_jsonl(
                attempt_log,
                {
                    "event": "skip_completed_generation_arm",
                    "time_unix": time.time(),
                    "generation_arm": item["generation_arm"],
                    "central_raw": str(central_raw),
                    "raw_summary": raw_summary,
                },
            )
            continue
        assert_partial_raw_has_no_failed_rows(method_raw, ids_path)
        append_jsonl(
            attempt_log,
            {
                "event": "start_generation",
                "time_unix": time.time(),
                "generation_arm": item["generation_arm"],
                "process_slug": item["process_slug"],
            },
        )
        run_method(
            PROJECT_ROOT / item["config"],
            args.fresh_source_data,
            ids_path,
            args.profile_dir,
            args.db_root,
            output_dir,
            gold_plans_path=args.fresh_gold_plans,
            inference_config_path=inference_config_path,
            resume=True,
            stage="robustness",
            dependency_lock_path=args.dependency_lock,
            environment_manifest_path=environment_manifest,
        )
        raw_summary = copy_raw_to_central(
            method_raw,
            central_raw,
            ids_path,
        )
        append_jsonl(
            attempt_log,
            {
                "event": "finish_generation",
                "time_unix": time.time(),
                "generation_arm": item["generation_arm"],
                "raw_summary": raw_summary,
            },
        )

    shared_raw = result_root / "raw_generations" / "mp_fs_plus_shared.jsonl"
    verify_raw_complete(shared_raw, ids_path)
    for item in DETERMINISTIC_REPROCESS_PLAN:
        append_jsonl(
            attempt_log,
            {
                "event": "start_deterministic_reprocess",
                "time_unix": time.time(),
                "process_slug": item["process_slug"],
                "reuse_central_raw": str(shared_raw),
            },
        )
        run_method(
            PROJECT_ROOT / item["config"],
            args.fresh_source_data,
            ids_path,
            args.profile_dir,
            args.db_root,
            result_root / item["output_dir"],
            gold_plans_path=args.fresh_gold_plans,
            resume=True,
            stage="robustness",
            reuse_raw_generations_path=shared_raw,
        )
        append_jsonl(
            attempt_log,
            {
                "event": "finish_deterministic_reprocess",
                "time_unix": time.time(),
                "process_slug": item["process_slug"],
            },
        )

    raw_summaries = {
        path.name: verify_raw_complete(path, ids_path)
        for path in sorted(raw_dir.glob("*.jsonl"))
    }
    final_summary = {
        "status": "COMPLETE",
        "git": git_lock,
        "raw_generations": raw_summaries,
        "attempt_log": str(attempt_log.resolve()),
        "preflight_summary": str((result_root / "gpu_preflight" / "gpu_preflight_summary.json").resolve()),
        "no_semantic_retry": True,
    }
    write_json(result_root / "stage4_runner_summary.json", final_summary)
    return final_summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", default="stage4_fresh_7b_protocol")
    parser.add_argument("--fresh-source-data", required=True)
    parser.add_argument("--fresh-gold-plans", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--model-name-or-path", required=True)
    parser.add_argument("--accepted-protocol-commit", required=True)
    parser.add_argument("--dependency-lock", default="requirements-inference.lock.txt")
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = run_stage4(args)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
