from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

import sys

for import_root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from nldbwrite_v3.baselines import legacy_record_json_to_write_plan
from nldbwrite_v3.common import sha256_file
from nldbwrite_v3.compiler import (
    check_semantic_risk_gate,
    compile_verified_plan,
    execute_program,
    preflight_program,
)
from nldbwrite_v3.evaluator.state import _execute_direct_sql
from nldbwrite_v3.experiments.run_method import (
    DIRECT_METHODS,
    LEGACY_COMMON_METHODS,
    MAPPING_METHODS,
    PREFLIGHT_METHODS,
    _prompt_for_sample,
)
from nldbwrite_v3.inference.parse_output import extract_json_object, extract_sql_statements
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import parse_llm_plan
from nldbwrite_v3.verifier import verify_write_plan

from scripts.data.audit_crudsql_stage6a import table_fingerprint
from scripts.server.run_stage6_confirmation import (
    STAGE6_CRUDSQL_DB_ROOT,
    STREAMS,
    build_profile_cache,
    load_arm_config_for_stream,
    sample_to_method_row,
)

DATE = "20260826"
FINAL_CONFIRMATION_N = 481
STAGE6I_ZIP_SHA256 = "d606c686d5176424898c75f074ec835c86ff893bc263f0adfbcac8d10445a571"
RUN_STATE_SHA256 = "a6e8fee1af818b186b30a476cba8327323374d451ff43e08dd72f394d90a5ce2"
EXECUTION_CODE_MANIFEST_SHA256 = "f4896552a8eaf7f7b3f6b976b6b0877b06281c926047acc244eebb1e9767c68e"
FINAL_MANIFEST_SHA256 = "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7"
FINAL_GOLD_CORPUS_SHA256 = "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16"
FINAL_GOLD_PROGRAMS_SHA256 = "d34208d3def6434591f05cb396505475f3fd1e5d057326baf8f7207cdceaa3cf"
FINAL_GOLD_POST_STATE_HASHES_SHA256 = "ea2fc586c764592268d9f330651d7c14855a731b4045a2f79d26dd1853b32cc6"

RAW_STREAM_HASHES = {
    "direct": "ef0c0669578c0f2a1645f3119a6273991c941a933d47c7c3b92a4b984b956afb",
    "j_fs": "b0fcdef81cd666f4357998f02827fe64e7be7dc64d79c6d18820be1f812136b9",
    "original_mp_fs_plus": "b10ba74e67c788a3c27df08354464bf51aeb4d60cd1b96b8719a12e62eb7feb6",
    "shared_mp_fs_plus_generation": "13d05a682ba1b1de2d60bd15cb2d206c69f9e9605cbd9cef113b0813bef723d3",
}

EVAL_ARMS = {
    "direct": {
        "method_id": "D-FS-M",
        "config_stream": "direct",
        "raw_stream": "direct",
        "raw_member": "stage6_confirmation_run_outputs/raw_generations/direct.jsonl",
    },
    "j_fs": {
        "method_id": "J-FS-M",
        "config_stream": "j_fs",
        "raw_stream": "j_fs",
        "raw_member": "stage6_confirmation_run_outputs/raw_generations/j_fs.jsonl",
    },
    "original_mp_fs_plus": {
        "method_id": "MP-FS+",
        "config_stream": "original_mp_fs_plus",
        "raw_stream": "original_mp_fs_plus",
        "raw_member": "stage6_confirmation_run_outputs/raw_generations/original_mp_fs_plus.jsonl",
    },
    "d_g1_control": {
        "method_id": "MP-FS+",
        "config_stream": "shared_mp_fs_plus_generation",
        "raw_stream": "shared_mp_fs_plus_generation",
        "raw_member": "stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl",
    },
    "d_f_g1_vnext": {
        "method_id": "MP-FS+",
        "config_stream": "shared_mp_fs_plus_generation",
        "config_path": "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
        "raw_stream": "shared_mp_fs_plus_generation",
        "raw_member": "stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl",
    },
}

FIRST_FAILURE_ORDER = (
    "generation",
    "parse",
    "construction",
    "verification",
    "admission",
    "execution",
    "state_mismatch",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_text(canonical_json(value))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def safe_reset_output_dir(path: Path, *, force: bool) -> None:
    if not path.exists():
        path.mkdir(parents=True)
        return
    if not force:
        raise SystemExit(f"Output directory already exists: {path}. Use --force to overwrite it.")
    resolved = path.resolve()
    repo = PROJECT_ROOT.resolve()
    if repo not in resolved.parents and resolved != repo / "stage6_replay_evaluation":
        raise SystemExit(f"Refusing to remove output outside repository artifact area: {resolved}")
    shutil.rmtree(path)
    path.mkdir(parents=True)


def extract_stage6i_inputs(stage6i_zip: Path, output_dir: Path) -> dict[str, Any]:
    if sha256_file(stage6i_zip) != STAGE6I_ZIP_SHA256:
        raise SystemExit("Stage6I ZIP SHA-256 does not match accepted reviewer package hash.")
    mirror = output_dir / "stage6i_generation_inputs"
    required = {
        "run_state": "stage6_confirmation_run_outputs/CONFIRMATION_RUN_STATE.json",
        "execution_code_manifest": "stage6_confirmation_run_outputs/EXECUTION_CODE_MANIFEST.json",
        **{f"raw_{stream}": spec["raw_member"] for stream, spec in EVAL_ARMS.items() if stream in RAW_STREAM_HASHES},
    }
    # D_G1 and D_F_G1 intentionally share the same raw member; copy it once.
    required.update(
        {
            "raw_shared_mp_fs_plus_generation": "stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl",
            "rprov_d_g1": "stage6_confirmation_run_outputs/replay_provenance/d_g1_control.jsonl",
            "rprov_d_f_g1": "stage6_confirmation_run_outputs/replay_provenance/d_f_g1_vnext.jsonl",
        }
    )
    copied: dict[str, Any] = {}
    with zipfile.ZipFile(stage6i_zip) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"Stage6I ZIP failed testzip at member: {bad}")
        names = set(archive.namelist())
        for key, member in sorted(required.items()):
            if member not in names:
                raise SystemExit(f"Stage6I ZIP is missing required member: {member}")
            target = mirror / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            copied[key] = {
                "zip_member": member,
                "path": target.relative_to(output_dir).as_posix(),
                "sha256": sha256_file(target),
            }
    if copied["run_state"]["sha256"] != RUN_STATE_SHA256:
        raise SystemExit("Stage6I run-state SHA-256 mismatch.")
    if copied["execution_code_manifest"]["sha256"] != EXECUTION_CODE_MANIFEST_SHA256:
        raise SystemExit("Stage6I execution-code manifest SHA-256 mismatch.")
    for stream, expected in RAW_STREAM_HASHES.items():
        key = f"raw_{stream}"
        actual = copied[key]["sha256"] if key in copied else copied["raw_shared_mp_fs_plus_generation"]["sha256"]
        if actual != expected:
            raise SystemExit(f"Raw generation SHA-256 mismatch for {stream}: {actual}")
    return copied


def load_final_gold() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    final_manifest_path = PROJECT_ROOT / "stage6_final_registration_revision" / "artifacts" / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"
    gold_programs_path = PROJECT_ROOT / "stage6_final_registration_revision" / "artifacts" / "FINAL_GOLD_PROGRAMS.jsonl"
    gold_post_path = PROJECT_ROOT / "stage6_final_registration_revision" / "artifacts" / "FINAL_GOLD_POST_STATE_HASHES.jsonl"
    gold_corpus_path = PROJECT_ROOT / "stage6_final_registration_revision" / "artifacts" / "FINAL_GOLD_CORPUS.jsonl"
    expected_hashes = {
        final_manifest_path: FINAL_MANIFEST_SHA256,
        gold_programs_path: FINAL_GOLD_PROGRAMS_SHA256,
        gold_post_path: FINAL_GOLD_POST_STATE_HASHES_SHA256,
        gold_corpus_path: FINAL_GOLD_CORPUS_SHA256,
    }
    for path, expected in expected_hashes.items():
        if sha256_file(path) != expected:
            raise SystemExit(f"Final Stage6E artifact hash mismatch: {path}")
    samples = sorted(read_jsonl(final_manifest_path), key=lambda row: str(row["stage6_sample_id"]))
    gold_programs = {str(row["stage6_sample_id"]): row for row in read_jsonl(gold_programs_path)}
    gold_posts = {str(row["stage6_sample_id"]): row for row in read_jsonl(gold_post_path)}
    if len(samples) != FINAL_CONFIRMATION_N:
        raise SystemExit(f"Final manifest must contain 481 rows, got {len(samples)}")
    sample_ids = [str(row["stage6_sample_id"]) for row in samples]
    if len(set(sample_ids)) != FINAL_CONFIRMATION_N:
        raise SystemExit("Final manifest contains duplicate sample IDs.")
    if set(sample_ids) != set(gold_programs) or set(sample_ids) != set(gold_posts):
        raise SystemExit("Final gold programs/post-state IDs do not match final manifest.")
    return samples, gold_programs, gold_posts


def load_raw_rows(raw_path: Path, expected_hash: str, sample_ids: set[str]) -> dict[str, dict[str, Any]]:
    if sha256_file(raw_path) != expected_hash:
        raise SystemExit(f"Raw generation file SHA-256 mismatch: {raw_path}")
    rows = read_jsonl(raw_path)
    ids = [str(row.get("stage6_sample_id") or row.get("sample_id")) for row in rows]
    if len(rows) != FINAL_CONFIRMATION_N or len(set(ids)) != FINAL_CONFIRMATION_N or set(ids) != sample_ids:
        raise SystemExit(f"Raw generation file does not cover exact 481 final IDs: {raw_path}")
    return {sample_id: row for sample_id, row in zip(ids, rows)}


def first_failure(statuses: dict[str, str], target_state_correct: bool) -> tuple[str, str | None]:
    for stage in FIRST_FAILURE_ORDER:
        status = statuses.get(stage)
        if stage == "state_mismatch":
            if not target_state_correct:
                return stage, "predicted_post_state_sha256 does not match gold_post_state_sha256"
            continue
        if status and status not in {"success", "accepted", "not_applicable"}:
            return stage, status
    return "none", None


def open_fresh_db(sample: dict[str, Any]) -> sqlite3.Connection:
    db_path = PROJECT_ROOT / STAGE6_CRUDSQL_DB_ROOT / str(sample["isolated_db"])
    source = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(":memory:")
    try:
        source.backup(dest)
    finally:
        source.close()
    dest.execute("PRAGMA foreign_keys = ON")
    return dest


def compute_target_state_sha(conn: sqlite3.Connection, table_id: str) -> str:
    return str(table_fingerprint(conn, f"Table_{table_id}")["initial_state_sha256"])


def program_payload(program: Any, direct_sql: list[str] | None) -> dict[str, Any] | None:
    if direct_sql is not None:
        return {"kind": "direct_sql", "statements": list(direct_sql)}
    if program is not None:
        return {"kind": "compiled_program", "program": program.to_dict()}
    return None


def build_candidate(
    *,
    arm: str,
    method: str,
    config: dict[str, Any],
    sample: dict[str, Any],
    profile: dict[str, Any],
    raw_output: str,
) -> dict[str, Any]:
    parse_status = "success"
    construction_status = "not_applicable"
    verification_status = "not_applicable"
    admission_status = "not_applicable"
    parsed_plan: dict[str, Any] | None = None
    materialized_plan: dict[str, Any] | None = None
    verification_dict: dict[str, Any] | None = None
    program = None
    direct_sql: list[str] | None = None
    diagnostics: list[Any] = []
    preflight_artifact: dict[str, Any] | None = None

    method_row = sample_to_method_row(sample)
    _prompt, payload = _prompt_for_sample(method, method_row, profile, config)

    if method in DIRECT_METHODS:
        direct_sql, sql_error = extract_sql_statements(raw_output)
        parse_status = "success" if not sql_error else "parse_error"
        construction_status = "success" if direct_sql and parse_status == "success" else "construction_error"
        verification_status = "not_applicable"
        admission_status = "accepted" if construction_status == "success" else "not_applicable"
        if sql_error:
            diagnostics.append(sql_error)
    elif method in LEGACY_COMMON_METHODS:
        legacy_json, json_error = extract_json_object(raw_output)
        parse_status = "success" if legacy_json is not None else "parse_error"
        if json_error:
            diagnostics.append(json_error)
        if legacy_json is not None:
            parsed_plan = legacy_record_json_to_write_plan(legacy_json, profile)
            verification = verify_write_plan(parsed_plan, profile)
            verification_dict = verification.to_dict()
            verification_status = "success" if verification.valid else "verification_error"
            if verification.valid:
                materialized_plan = verification.normalized_plan
                program = compile_verified_plan(materialized_plan, profile)
                construction_status = str(program.status)
                admission_status = "accepted" if program.status == "success" else "not_applicable"
            else:
                construction_status = "not_applicable"
        else:
            construction_status = "not_applicable"
    elif method in MAPPING_METHODS:
        plan_kind = "mapping" if payload.mode == "semi_structured" else "free_text"
        reference_planning = bool(config.get("reference_planning"))
        parsed = parse_llm_plan(raw_output, plan_kind=plan_kind, reference_mode=reference_planning)
        parse_status = parsed.parse_status if parsed.success else "parse_error"
        parsed_plan = parsed.plan
        diagnostics.extend(parsed.diagnostics or [])
        if parsed.success:
            pipeline_result = MappingFirstPipeline(
                profile,
                normalize_values=bool(config.get("normalize_values")),
                normalization_mode=str(config.get("normalization_mode") or "legacy"),
                reference_planning=reference_planning,
                stage2_interventions=config.get("stage2_interventions"),
                structured_source_parser=config.get("structured_source_parser"),
                free_text_typed_normalization=config.get("free_text_typed_normalization"),
                constrained_reference_repair=config.get("constrained_reference_repair"),
                diagnostic_targeted_repair=config.get("diagnostic_targeted_repair"),
            ).run(str(sample.get("question") or ""), parsed.plan)
            materialized_plan = pipeline_result.write_plan
            verification_dict = pipeline_result.verification.to_dict() if pipeline_result.verification else None
            verification_status = (
                "success"
                if pipeline_result.verification and pipeline_result.verification.valid
                else "verification_error"
            )
            program = pipeline_result.program
            construction_status = str(program.status) if program is not None else "construction_error"
        else:
            construction_status = "not_applicable"
    else:
        raise SystemExit(f"Unsupported Stage6J method for arm {arm}: {method}")

    if method in PREFLIGHT_METHODS and program is not None and program.status == "success":
        semantic_risk = check_semantic_risk_gate(program)
        if not bool(semantic_risk.get("accepted")):
            preflight_artifact = semantic_risk
        else:
            db_path = PROJECT_ROOT / STAGE6_CRUDSQL_DB_ROOT / str(sample["isolated_db"])
            preflight_artifact = preflight_program(db_path, program)
        admission_status = "accepted" if bool(preflight_artifact.get("accepted")) else "rejected"
    elif admission_status == "not_applicable" and (program is not None or direct_sql is not None):
        admission_status = "accepted"

    return {
        "parse_status": parse_status,
        "construction_status": construction_status,
        "verification_status": verification_status,
        "admission_status": admission_status,
        "parsed_plan": parsed_plan,
        "materialized_plan": materialized_plan,
        "verification": verification_dict,
        "preflight": preflight_artifact,
        "program": program,
        "direct_sql": direct_sql,
        "diagnostics": diagnostics,
    }


def evaluate_candidate(
    *,
    sample: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[str, str | None]:
    if candidate["parse_status"] != "success":
        return "not_run", None
    if candidate["construction_status"] != "success":
        return "not_run", None
    if candidate["admission_status"] == "rejected":
        return "not_run", None
    conn = open_fresh_db(sample)
    try:
        if candidate["program"] is not None:
            execution = execute_program(conn, candidate["program"])
        else:
            execution = _execute_direct_sql(conn, list(candidate["direct_sql"] or []))
        if execution.get("status") != "success":
            return str(execution.get("status") or "execution_error"), None
        return "success", compute_target_state_sha(conn, str(sample["table_id"]))
    finally:
        conn.close()


def evaluate_arm(
    *,
    arm: str,
    raw_rows: dict[str, dict[str, Any]],
    samples: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    gold_posts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    spec = EVAL_ARMS[arm]
    method = str(spec["method_id"])
    if spec.get("config_path"):
        config = json.loads((PROJECT_ROOT / str(spec["config_path"])).read_text(encoding="utf-8"))
    else:
        config = load_arm_config_for_stream(str(spec["config_stream"]), repo_root=PROJECT_ROOT)
    outcomes: list[dict[str, Any]] = []
    source_raw_generation_sha256 = RAW_STREAM_HASHES[str(spec["raw_stream"])]
    for sample in samples:
        sample_id = str(sample["stage6_sample_id"])
        raw = raw_rows[sample_id]
        raw_output = str(raw.get("raw_output") or "")
        candidate = build_candidate(
            arm=arm,
            method=method,
            config=config,
            sample=sample,
            profile=profiles[str(sample["table_id"])],
            raw_output=raw_output,
        )
        execution_status, predicted_post_state_sha = evaluate_candidate(sample=sample, candidate=candidate)
        gold_post_state_sha = str(gold_posts[sample_id]["post_state_sha256"])
        target_state_correct = predicted_post_state_sha == gold_post_state_sha
        candidate_program = program_payload(candidate["program"], candidate["direct_sql"])
        statuses = {
            "generation": str(raw.get("generation_status") or "success"),
            "parse": str(candidate["parse_status"]),
            "construction": str(candidate["construction_status"]),
            "verification": str(candidate["verification_status"]),
            "admission": str(candidate["admission_status"]),
            "execution": execution_status,
        }
        failure_stage, failure_reason = first_failure(statuses, target_state_correct)
        shared_raw_row_sha = (
            str(raw["raw_generation_row_sha256"])
            if arm in {"d_g1_control", "d_f_g1_vnext"}
            else None
        )
        outcome = {
            "stage6_sample_id": sample_id,
            "sample_id": sample_id,
            "arm": arm,
            "method_id": method,
            "source_raw_generation_stream": spec["raw_stream"],
            "source_raw_generation_sha256": source_raw_generation_sha256,
            "source_raw_generation_row_sha256": raw["raw_generation_row_sha256"],
            "shared_raw_generation_row_sha256": shared_raw_row_sha,
            "raw_output_sha256": raw.get("raw_output_sha256"),
            "hit_max_new_tokens": bool(raw.get("hit_max_new_tokens")),
            "parse_status": candidate["parse_status"],
            "construction_status": candidate["construction_status"],
            "verification_status": candidate["verification_status"],
            "admission_status": candidate["admission_status"],
            "execution_status": execution_status,
            "candidate_program": candidate_program,
            "candidate_program_sha256": canonical_sha256(candidate_program) if candidate_program is not None else None,
            "predicted_post_state_sha256": predicted_post_state_sha,
            "gold_post_state_sha256": gold_post_state_sha,
            "target_state_correct": bool(target_state_correct),
            "failure_stage": failure_stage,
            "failure_reason": failure_reason,
        }
        outcomes.append(outcome)
    return outcomes


def summarize_outcomes(outcomes_by_arm: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    arm_summaries: dict[str, Any] = {}
    for arm, rows in outcomes_by_arm.items():
        arm_summaries[arm] = {
            "n": len(rows),
            "target_state_correct": sum(1 for row in rows if row["target_state_correct"]),
            "target_state_incorrect": sum(1 for row in rows if not row["target_state_correct"]),
            "parse_status_counts": dict(Counter(str(row["parse_status"]) for row in rows)),
            "construction_status_counts": dict(Counter(str(row["construction_status"]) for row in rows)),
            "verification_status_counts": dict(Counter(str(row["verification_status"]) for row in rows)),
            "admission_status_counts": dict(Counter(str(row["admission_status"]) for row in rows)),
            "execution_status_counts": dict(Counter(str(row["execution_status"]) for row in rows)),
            "failure_stage_counts": dict(Counter(str(row["failure_stage"]) for row in rows)),
            "hit_max_new_tokens_count": sum(1 for row in rows if row["hit_max_new_tokens"]),
        }
    return {
        "stage": "Stage6J_DETERMINISTIC_REPLAY_EVALUATION",
        "status": "PASS_REPLAY_EVALUATION_COMPLETE",
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "arm_count": len(outcomes_by_arm),
        "arms": arm_summaries,
        "statistics_computed": False,
        "significance_tests_computed": False,
        "model_called": False,
        "gpu_called": False,
    }


def h2_shared_audit(outcomes_by_arm: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    left = {row["stage6_sample_id"]: row for row in outcomes_by_arm["d_g1_control"]}
    right = {row["stage6_sample_id"]: row for row in outcomes_by_arm["d_f_g1_vnext"]}
    mismatches = []
    for sample_id in sorted(left):
        lrow = left[sample_id]
        rrow = right[sample_id]
        if lrow["shared_raw_generation_row_sha256"] != rrow["shared_raw_generation_row_sha256"]:
            mismatches.append(sample_id)
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "checked_pairs": len(left),
        "mismatch_count": len(mismatches),
        "mismatched_sample_ids": mismatches[:20],
        "d_g1_arm": "d_g1_control",
        "d_f_g1_arm": "d_f_g1_vnext",
        "same_raw_generation_stream": "shared_mp_fs_plus_generation",
    }


def create_stage6j(stage6i_zip: Path, output_dir: Path, *, force: bool = False) -> dict[str, Any]:
    safe_reset_output_dir(output_dir, force=force)
    input_manifest = extract_stage6i_inputs(stage6i_zip, output_dir)
    samples, gold_programs, gold_posts = load_final_gold()
    sample_ids = {str(row["stage6_sample_id"]) for row in samples}
    profiles = build_profile_cache(samples, repo_root=PROJECT_ROOT)
    raw_root = output_dir / "stage6i_generation_inputs" / "stage6_confirmation_run_outputs" / "raw_generations"
    raw_streams = {
        stream: load_raw_rows(raw_root / f"{stream}.jsonl", expected_hash, sample_ids)
        for stream, expected_hash in RAW_STREAM_HASHES.items()
    }
    outcomes_by_arm: dict[str, list[dict[str, Any]]] = {}
    outcomes_dir = output_dir / "replay_outcomes"
    for arm, spec in EVAL_ARMS.items():
        rows = evaluate_arm(
            arm=arm,
            raw_rows=raw_streams[str(spec["raw_stream"])],
            samples=samples,
            profiles=profiles,
            gold_posts=gold_posts,
        )
        outcomes_by_arm[arm] = rows
        write_jsonl(outcomes_dir / f"{arm}.jsonl", rows)
    summary = summarize_outcomes(outcomes_by_arm)
    h2_audit = h2_shared_audit(outcomes_by_arm)
    denominator = {
        "status": "PASS",
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "arms": {
            arm: {
                "row_count": len(rows),
                "unique_sample_ids": len({row["stage6_sample_id"] for row in rows}),
                "missing_sample_ids": sorted(sample_ids - {row["stage6_sample_id"] for row in rows}),
                "extra_sample_ids": sorted({row["stage6_sample_id"] for row in rows} - sample_ids),
            }
            for arm, rows in outcomes_by_arm.items()
        },
        "no_denominator_drops": True,
    }
    arm_manifest = {
        "stage": "Stage6J_DETERMINISTIC_REPLAY_EVALUATION",
        "date": DATE,
        "eval_arms": {
            arm: {
                **{key: value for key, value in spec.items() if key != "raw_member"},
                "raw_generation_sha256": RAW_STREAM_HASHES[str(spec["raw_stream"])],
                "outcome_path": f"replay_outcomes/{arm}.jsonl",
                "outcome_sha256": sha256_file(outcomes_dir / f"{arm}.jsonl"),
            }
            for arm, spec in EVAL_ARMS.items()
        },
        "raw_stream_hashes": RAW_STREAM_HASHES,
        "source_stage6i_zip_sha256": STAGE6I_ZIP_SHA256,
        "run_state_sha256": RUN_STATE_SHA256,
        "execution_code_manifest_sha256": EXECUTION_CODE_MANIFEST_SHA256,
        "final_stage6e_artifacts": {
            "final_manifest_sha256": FINAL_MANIFEST_SHA256,
            "final_gold_corpus_sha256": FINAL_GOLD_CORPUS_SHA256,
            "final_gold_programs_sha256": FINAL_GOLD_PROGRAMS_SHA256,
            "final_gold_post_state_hashes_sha256": FINAL_GOLD_POST_STATE_HASHES_SHA256,
        },
    }
    lock = {
        "stage": "Stage6J_DETERMINISTIC_REPLAY_EVALUATION",
        "status": "PASS_REPLAY_EVALUATION_COMPLETE",
        "date": DATE,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "input_manifest": input_manifest,
        "arm_manifest_sha256": None,
        "summary_sha256": None,
        "h2_shared_replay_audit_sha256": None,
        "denominator_audit_sha256": None,
        "statistics_computed": False,
        "significance_tests_computed": False,
        "model_called": False,
        "gpu_called": False,
    }
    write_json(output_dir / "REPLAY_EVALUATION_SUMMARY.json", summary)
    write_json(output_dir / "H2_SHARED_REPLAY_PROVENANCE_AUDIT.json", h2_audit)
    write_json(output_dir / "DENOMINATOR_AUDIT.json", denominator)
    write_json(output_dir / "REPLAY_ARM_MANIFEST.json", arm_manifest)
    lock.update(
        {
            "arm_manifest_sha256": sha256_file(output_dir / "REPLAY_ARM_MANIFEST.json"),
            "summary_sha256": sha256_file(output_dir / "REPLAY_EVALUATION_SUMMARY.json"),
            "h2_shared_replay_audit_sha256": sha256_file(output_dir / "H2_SHARED_REPLAY_PROVENANCE_AUDIT.json"),
            "denominator_audit_sha256": sha256_file(output_dir / "DENOMINATOR_AUDIT.json"),
        }
    )
    write_json(output_dir / "STAGE6J_REPLAY_EVALUATION_LOCK.json", lock)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage6i-zip",
        type=Path,
        default=PROJECT_ROOT / "reviewer_packages" / "Stage6I_CONFIRMATION_GENERATION_RESULTS_REVIEWER_PACKAGE_20260826.zip",
    )
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage6_replay_evaluation")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    summary = create_stage6j(args.stage6i_zip, args.output_dir, force=args.force)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
