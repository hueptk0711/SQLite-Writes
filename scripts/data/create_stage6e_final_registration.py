#!/usr/bin/env python3
"""Create Stage 6E final registration revision and denominator lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import zipfile
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.audit_crudsql_stage6a import table_fingerprint, table_info
from scripts.data.create_stage6c_gold_review_setup import canonical_json


STAGE6B_DIR = PROJECT_ROOT / "stage6_crudsql_registration"
STAGE6C_EXEC_DIR = PROJECT_ROOT / "stage6_gold_review_execution"
STAGE6C_R03_DIR = PROJECT_ROOT / "stage6_gold_review_r03_adjudication"
STAGE6C_R04_DIR = PROJECT_ROOT / "stage6_gold_review_r04_resolution"
STAGE6D_SETUP_DIR = PROJECT_ROOT / "stage6_corrected_gold_review_setup"
STAGE6D_EXEC_DIR = PROJECT_ROOT / "stage6_corrected_gold_review_execution"
STAGE6E_DIR = PROJECT_ROOT / "stage6_final_registration_revision"
ARCHIVE_NAME = "stage6e_final_registration_revision_artifacts_20260824.zip"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)

STAGE6D_EXECUTION_PATCH1_COMMIT = "b5b7bd8570290643aa819512e57a673eba2c8f87"
SOURCE_TASK_INVALID_QUEUE_SHA256 = "f606cee0de6108b77b45c72ffdedd4cbfba229716c3f120bcc79f892b19422f1"
CORRECTABLE_GOLD_ERROR_QUEUE_SHA256 = "33fe542a38413f4159bbce6a82318f22d790d36b2a5a40882ac74c8c8da3a336"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("".join(canonical_json(row) + "\n" for row in rows)).encode("utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def sqlite_affinity(declared_type: str) -> str:
    value = (declared_type or "").upper()
    if "INT" in value:
        return "INTEGER"
    if any(token in value for token in ["CHAR", "CLOB", "TEXT"]):
        return "TEXT"
    if "BLOB" in value or not value:
        return "BLOB"
    if any(token in value for token in ["REAL", "FLOA", "DOUB"]):
        return "REAL"
    return "NUMERIC"


def make_archive(archive_path: Path, members: list[Path], root: Path) -> dict[str, Any]:
    if archive_path.exists():
        archive_path.unlink()
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(members, key=lambda path: path.relative_to(root).as_posix()):
            rel = member.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, member.read_bytes())
            rows.append({"path": rel, "sha256": sha256_file(member)})
    return {
        "path": archive_path.relative_to(root).as_posix(),
        "sha256": sha256_file(archive_path),
        "member_count": len(rows),
        "members": rows,
    }


def load_inputs(
    stage6b_dir: Path,
    stage6c_exec_dir: Path,
    stage6c_r03_dir: Path,
    stage6c_r04_dir: Path,
    stage6d_setup_dir: Path,
    stage6d_exec_dir: Path,
) -> dict[str, Any]:
    artifacts = stage6b_dir / "artifacts"
    return {
        "registered_samples": read_jsonl(artifacts / "registered_samples.jsonl"),
        "registered_plans": read_jsonl(artifacts / "gold_write_plans.jsonl"),
        "registered_programs": read_jsonl(artifacts / "gold_programs.jsonl"),
        "registered_post_hashes": read_jsonl(artifacts / "gold_post_state_hashes.jsonl"),
        "db_manifest": read_json(artifacts / "isolated_table_db_manifest.json"),
        "reference_registry": read_json(artifacts / "stage6_seen_reference_registry.json"),
        "r01_r02_agreement": read_json(stage6c_exec_dir / "R01_R02_AGREEMENT_REPORT.json"),
        "r03_report": read_json(stage6c_r03_dir / "R03_ADJUDICATION_REPORT.json"),
        "r04_report": read_json(stage6c_r04_dir / "R04_RESOLUTION_REPORT.json"),
        "source_invalid_queue": read_json(stage6c_r04_dir / "SOURCE_TASK_INVALID_QUEUE.json"),
        "correctable_queue": read_json(stage6c_r04_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json"),
        "corrected_agreement": read_json(stage6d_exec_dir / "C01_C02_CORRECTED_AGREEMENT_REPORT.json"),
        "corrected_resolution": read_json(stage6d_exec_dir / "CORRECTED_ITEMS_RESOLUTION_REPORT.json"),
        "corrected_plans": read_jsonl(stage6d_setup_dir / "artifacts" / "corrected_gold_write_plans.jsonl"),
        "corrected_programs": read_jsonl(stage6d_setup_dir / "artifacts" / "corrected_gold_programs.jsonl"),
        "corrected_post_hashes": read_jsonl(stage6d_setup_dir / "artifacts" / "corrected_gold_post_state_hashes.jsonl"),
    }


def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["stage6_sample_id"]: row for row in rows}


def replay_program(stage6b_dir: Path, program: dict[str, Any]) -> dict[str, Any]:
    table_id = program["table_id"]
    table_name = f"Table_{table_id}"
    source = sqlite3.connect(stage6b_dir / f"isolated_table_dbs/crudsql_db_{table_id}.sqlite")
    con = sqlite3.connect(":memory:")
    try:
        source.backup(con)
    finally:
        source.close()
    try:
        before = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        initial_fp = table_fingerprint(con, table_name)
        cursor = con.execute(program["sql_template"], program["parameters"])
        inserted_rowid = cursor.lastrowid
        inserted = list(con.execute(f'SELECT * FROM "{table_name}" WHERE rowid=?', (inserted_rowid,)).fetchone())
        after = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        post_fp = table_fingerprint(con, table_name)
    finally:
        con.close()
    return {
        "stage6_sample_id": program["stage6_sample_id"],
        "initial_state_sha256": initial_fp["initial_state_sha256"],
        "post_state_sha256": post_fp["initial_state_sha256"],
        "pre_insert_row_count": before,
        "post_insert_row_count": after,
        "actual_inserted_row": inserted,
        "expected_inserted_row": program["expected_inserted_row"],
        "status": "PASS"
        if (
            before + 1 == after
            and inserted == program["expected_inserted_row"]
            and initial_fp["initial_state_sha256"] == program["initial_state_sha256"]
            and post_fp["initial_state_sha256"] == program["post_state_sha256"]
        )
        else "FAIL",
    }


def distribution_report(stage6b_dir: Path, samples: list[dict[str, Any]], plans: list[dict[str, Any]]) -> dict[str, Any]:
    sample_by_id = by_id(samples)
    plan_by_id = by_id(plans)
    table_counts = Counter(row["table_id"] for row in samples)
    table_ids = sorted(table_counts)
    columns_per_table: list[int] = []
    rows_per_table: list[int] = []
    affinity_counts: Counter[str] = Counter()
    for table_id in table_ids:
        table_name = f"Table_{table_id}"
        con = sqlite3.connect(stage6b_dir / f"isolated_table_dbs/crudsql_db_{table_id}.sqlite")
        try:
            info = table_info(con, table_name)
            columns_per_table.append(len(info))
            rows_per_table.append(con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
            decl_by_index = {idx: row["type"] for idx, row in enumerate(info)}
            for sample_id, sample in sample_by_id.items():
                if sample["table_id"] == table_id:
                    for column_index in plan_by_id[sample_id]["column_indexes"]:
                        affinity_counts[sqlite_affinity(decl_by_index[column_index])] += 1
        finally:
            con.close()
    inserted_fields = [len(row["values"]) for row in plans]
    question_lengths = [len(row["question"]) for row in samples]
    removed_table_count = 125 - len(table_ids)
    return {
        "status": "PASS",
        "sample_count": len(samples),
        "table_count": len(table_ids),
        "removed_table_count_vs_stage6b": removed_table_count,
        "samples_per_table": {str(key): value for key, value in sorted(Counter(table_counts.values()).items())},
        "columns_per_table": {
            "min": min(columns_per_table),
            "max": max(columns_per_table),
            "mean": mean(columns_per_table),
            "median": median(columns_per_table),
        },
        "initial_rows_per_table": {
            "min": min(rows_per_table),
            "max": max(rows_per_table),
            "mean": mean(rows_per_table),
            "median": median(rows_per_table),
        },
        "inserted_fields_per_sample": {
            "min": min(inserted_fields),
            "max": max(inserted_fields),
            "mean": mean(inserted_fields),
            "median": median(inserted_fields),
        },
        "question_length_characters": {
            "min": min(question_lengths),
            "max": max(question_lengths),
            "mean": mean(question_lengths),
            "median": median(question_lengths),
        },
        "inserted_sqlite_affinity_counts": dict(sorted(affinity_counts.items())),
        "gold_source_type_counts": dict(sorted(Counter(row["gold_source_type"] for row in samples).items())),
        "language": "Chinese questions retained from official CRUDSQL",
    }


def overlap_audit(samples: list[dict[str, Any]], reference_registry: dict[str, Any]) -> dict[str, Any]:
    forbidden = reference_registry["forbidden_sets"]
    sets = {
        "sample_ids": {row["stage6_sample_id"] for row in samples},
        "input_text_sha256": {row["input_text_sha256"] for row in samples},
        "canonical_content_sha256": {row["canonical_content_sha256"] for row in samples},
        "source_groups": {row["source_group"] for row in samples},
        "database_ids": {row["database_id"] for row in samples},
    }
    counts = {
        "sample_id_overlap_count": len(sets["sample_ids"] & set(forbidden["sample_ids"])),
        "input_text_hash_overlap_count": len(sets["input_text_sha256"] & set(forbidden["input_text_sha256"])),
        "canonical_content_hash_overlap_count": len(
            sets["canonical_content_sha256"] & set(forbidden["canonical_content_sha256"])
        ),
        "source_group_overlap_count": len(sets["source_groups"] & set(forbidden["source_groups"])),
        "database_id_namespace_overlap_count": len(sets["database_ids"] & set(forbidden["database_ids"])),
    }
    return {
        "status": "PASS" if all(value == 0 for value in counts.values()) else "FAIL",
        "final_sample_count": len(samples),
        "reference_registry_sha256_excluding_self": reference_registry.get("registry_sha256_excluding_self"),
        "reference_digest_counts": reference_registry.get("digest_counts"),
        **counts,
    }


def build_stage6e_artifacts(inputs: dict[str, Any], stage6b_dir: Path) -> dict[str, Any]:
    registered_samples_by_id = by_id(inputs["registered_samples"])
    registered_plans_by_id = by_id(inputs["registered_plans"])
    registered_programs_by_id = by_id(inputs["registered_programs"])
    registered_posts_by_id = by_id(inputs["registered_post_hashes"])
    corrected_plans_by_id = by_id(inputs["corrected_plans"])
    corrected_programs_by_id = by_id(inputs["corrected_programs"])
    corrected_posts_by_id = by_id(inputs["corrected_post_hashes"])
    db_manifest_by_table = {row["table_id"]: row for row in inputs["db_manifest"]}

    original_accepted_ids = set(inputs["r01_r02_agreement"]["agreed_approved_ids"]) | set(
        inputs["r03_report"]["R03_approved_ids"]
    )
    corrected_accepted_ids = set(inputs["corrected_agreement"]["agreed_approved_ids"])
    invalid_ids = {row["stage6_sample_id"] for row in inputs["source_invalid_queue"]["items"]}
    all_ids = set(registered_samples_by_id)
    final_ids = original_accepted_ids | corrected_accepted_ids

    violations: list[str] = []
    checks = {
        "original_registered_n_not_500": len(all_ids) != 500,
        "original_accepted_n_not_460": len(original_accepted_ids) != 460,
        "corrected_accepted_n_not_21": len(corrected_accepted_ids) != 21,
        "source_invalid_n_not_19": len(invalid_ids) != 19,
        "final_n_not_481": len(final_ids) != 481,
        "invalid_overlap_final": bool(invalid_ids & final_ids),
        "final_plus_invalid_not_all_500": final_ids | invalid_ids != all_ids,
        "corrected_resolution_not_accepted": inputs["corrected_resolution"]["status"]
        != "ALL_21_CORRECTED_ITEMS_ACCEPTED_PENDING_STAGE6_REGISTRATION_REVISION",
    }
    violations.extend(name for name, failed in checks.items() if failed)

    final_samples: list[dict[str, Any]] = []
    final_plans: list[dict[str, Any]] = []
    final_programs: list[dict[str, Any]] = []
    final_posts: list[dict[str, Any]] = []
    final_corpus: list[dict[str, Any]] = []
    for sample_id in sorted(final_ids):
        source_sample = registered_samples_by_id[sample_id]
        table_id = source_sample["table_id"]
        gold_source_type = (
            "CORRECTED_REVIEW_ACCEPTED" if sample_id in corrected_accepted_ids else "ORIGINAL_REVIEW_ACCEPTED"
        )
        if gold_source_type == "CORRECTED_REVIEW_ACCEPTED":
            corrected_plan = corrected_plans_by_id[sample_id]
            corrected_program = corrected_programs_by_id[sample_id]
            corrected_post = corrected_posts_by_id[sample_id]
            post_state_sha256 = corrected_post["corrected_post_state_sha256"]
            plan = {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": source_sample["upstream_sample_locator"],
                "gold_source_type": gold_source_type,
                "operation": "INSERT",
                "table_id": table_id,
                "isolated_db": source_sample["isolated_db"],
                "schema_sha256": source_sample["schema_sha256"],
                "initial_state_sha256": source_sample["initial_state_sha256"],
                "column_indexes": corrected_plan["column_indexes"],
                "columns": corrected_plan["columns"],
                "values": corrected_plan["values"],
                "expected_inserted_row": corrected_plan["expected_inserted_row"],
                "post_state_sha256": post_state_sha256,
                "source_correctable_authored_content_sha256": corrected_plan[
                    "source_correctable_authored_content_sha256"
                ],
            }
            program = {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": source_sample["upstream_sample_locator"],
                "gold_source_type": gold_source_type,
                "table_id": table_id,
                "isolated_db": source_sample["isolated_db"],
                "schema_sha256": source_sample["schema_sha256"],
                "initial_state_sha256": source_sample["initial_state_sha256"],
                "sql_template": corrected_program["sql_template"],
                "sqlite_parameter_style": corrected_program["sqlite_parameter_style"],
                "parameters": corrected_program["parameters"],
                "expected_inserted_row": corrected_program["expected_inserted_row"],
                "post_state_sha256": post_state_sha256,
            }
        else:
            source_plan = registered_plans_by_id[sample_id]
            source_program = registered_programs_by_id[sample_id]
            source_post = registered_posts_by_id[sample_id]
            post_state_sha256 = source_post["post_state_sha256"]
            plan = {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": source_sample["upstream_sample_locator"],
                "gold_source_type": gold_source_type,
                "operation": "INSERT",
                "table_id": table_id,
                "isolated_db": source_sample["isolated_db"],
                "schema_sha256": source_plan["schema_sha256"],
                "initial_state_sha256": source_plan["initial_state_sha256"],
                "column_indexes": source_plan["column_indexes"],
                "columns": source_plan["columns"],
                "values": source_plan["values"],
                "expected_inserted_row": source_plan["expected_inserted_row"],
                "post_state_sha256": post_state_sha256,
            }
            program = {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": source_sample["upstream_sample_locator"],
                "gold_source_type": gold_source_type,
                "table_id": table_id,
                "isolated_db": source_sample["isolated_db"],
                "schema_sha256": source_program["schema_sha256"],
                "initial_state_sha256": source_program["initial_state_sha256"],
                "sql_template": source_program["sql_template"],
                "sqlite_parameter_style": source_program["sqlite_parameter_style"],
                "parameters": source_program["parameters"],
                "expected_inserted_row": source_program["expected_inserted_row"],
                "post_state_sha256": post_state_sha256,
            }
        post = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": source_sample["upstream_sample_locator"],
            "gold_source_type": gold_source_type,
            "table_id": table_id,
            "initial_state_sha256": source_sample["initial_state_sha256"],
            "post_state_sha256": post_state_sha256,
            "schema_sha256": source_sample["schema_sha256"],
        }
        gold_artifact_sha256 = sha256_text(canonical_json({"plan": plan, "program": program, "post": post}))
        final_sample = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": source_sample["upstream_sample_locator"],
            "table_id": table_id,
            "isolated_db": source_sample["isolated_db"],
            "isolated_db_sha256": db_manifest_by_table[table_id]["isolated_db_sha256"],
            "input_text_sha256": source_sample["question_sha256"],
            "canonical_content_sha256": source_sample["canonical_content_sha256"],
            "source_group": f"crudsql_table:{table_id}",
            "database_id": f"crudsql_table:{table_id}",
            "gold_source_type": gold_source_type,
            "gold_artifact_sha256": gold_artifact_sha256,
            "question": source_sample["question"],
        }
        final_samples.append(final_sample)
        final_plans.append(plan)
        final_programs.append(program)
        final_posts.append(post)
        final_corpus.append({"sample": final_sample, "plan": plan, "program": program, "post_state": post})

    replay_rows = [replay_program(stage6b_dir, program) for program in final_programs]
    replay_report = {
        "stage": "Stage6E_FINAL_GOLD_REPLAY",
        "status": "PASS" if all(row["status"] == "PASS" for row in replay_rows) else "FAIL",
        "sample_count": len(replay_rows),
        "pass_count": sum(row["status"] == "PASS" for row in replay_rows),
        "fail_count": sum(row["status"] != "PASS" for row in replay_rows),
        "rows": replay_rows,
    }
    if replay_report["status"] != "PASS":
        violations.append("final_gold_replay_failed")

    exclusions = []
    for item in sorted(inputs["source_invalid_queue"]["items"], key=lambda row: row["stage6_sample_id"]):
        source_sample = registered_samples_by_id[item["stage6_sample_id"]]
        exclusions.append(
            {
                "stage6_sample_id": item["stage6_sample_id"],
                "upstream_sample_locator": item["upstream_sample_locator"],
                "authored_content_sha256": item["authored_content_sha256"],
                "rationale": item["rationale"],
                "final_rejection_source": item["final_rejection_source"],
                "table_id": source_sample["table_id"],
                "replacement_sample": None,
            }
        )

    distribution = distribution_report(stage6b_dir, final_samples, final_plans)
    overlap = overlap_audit(final_samples, inputs["reference_registry"])
    mcnemar = {
        "stage": "Stage6E_MCNEMAR_THRESHOLD_SENSITIVITY_NOT_POWER_ANALYSIS",
        "final_n": 481,
        "holm_alpha_floor": 0.025,
        "test": "exact_two_sided_McNemar",
        "minimum_favorable_discordant_pairs_with_zero_regressions": 7,
        "exact_two_sided_p_at_threshold": 0.015625,
        "effect_fraction_at_threshold": 7 / 481,
        "hypotheses_unchanged_from_stage5": ["H1_D_F_G1_vs_Original", "H2_D_F_G1_vs_D_G1"],
    }
    lock = {
        "stage": "Stage6E_FINAL_REGISTRATION_REVISION_AND_DENOMINATOR_LOCK",
        "status": "FINAL_CONFIRMATION_REGISTRATION_LOCKED" if not violations else "FAIL",
        "validation_violations": violations,
        "stage6d_execution_patch1_commit": STAGE6D_EXECUTION_PATCH1_COMMIT,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": True,
        "original_registered_n": 500,
        "source_task_invalid_n": 19,
        "replacement_samples": 0,
        "replacement_policy": "NONE",
        "final_confirmation_n": 481,
        "gold_source_type_counts": {"ORIGINAL_REVIEW_ACCEPTED": 460, "CORRECTED_REVIEW_ACCEPTED": 21},
        "source_task_invalid_queue_sha256": SOURCE_TASK_INVALID_QUEUE_SHA256,
        "correctable_gold_error_queue_sha256": CORRECTABLE_GOLD_ERROR_QUEUE_SHA256,
        "next_step": "GPU_environment_preflight_after_reviewer_acceptance",
    }
    return {
        "lock": lock,
        "final_samples": final_samples,
        "final_plans": final_plans,
        "final_programs": final_programs,
        "final_posts": final_posts,
        "final_corpus": final_corpus,
        "exclusions": exclusions,
        "replay_report": replay_report,
        "distribution": distribution,
        "overlap": overlap,
        "mcnemar": mcnemar,
    }


def create_stage6e_final_registration(
    stage6b_dir: Path = STAGE6B_DIR,
    stage6c_exec_dir: Path = STAGE6C_EXEC_DIR,
    stage6c_r03_dir: Path = STAGE6C_R03_DIR,
    stage6c_r04_dir: Path = STAGE6C_R04_DIR,
    stage6d_setup_dir: Path = STAGE6D_SETUP_DIR,
    stage6d_exec_dir: Path = STAGE6D_EXEC_DIR,
    out_dir: Path = STAGE6E_DIR,
) -> dict[str, Any]:
    inputs = load_inputs(stage6b_dir, stage6c_exec_dir, stage6c_r03_dir, stage6c_r04_dir, stage6d_setup_dir, stage6d_exec_dir)
    artifacts = build_stage6e_artifacts(inputs, stage6b_dir)
    out_artifacts = out_dir / "artifacts"
    out_artifacts.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_artifacts / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl", artifacts["exclusions"])
    write_jsonl(out_artifacts / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl", artifacts["final_samples"])
    write_jsonl(out_artifacts / "FINAL_GOLD_WRITE_PLANS.jsonl", artifacts["final_plans"])
    write_jsonl(out_artifacts / "FINAL_GOLD_PROGRAMS.jsonl", artifacts["final_programs"])
    write_jsonl(out_artifacts / "FINAL_GOLD_POST_STATE_HASHES.jsonl", artifacts["final_posts"])
    write_jsonl(out_artifacts / "FINAL_GOLD_CORPUS.jsonl", artifacts["final_corpus"])
    write_json(out_artifacts / "FINAL_GOLD_REPLAY_REPORT.json", artifacts["replay_report"])
    write_json(out_artifacts / "FINAL_DISTRIBUTION_REPORT.json", artifacts["distribution"])
    write_json(out_artifacts / "FINAL_OVERLAP_AUDIT.json", artifacts["overlap"])
    write_json(out_artifacts / "MCNEMAR_THRESHOLD_SENSITIVITY_N481.json", artifacts["mcnemar"])
    lock = artifacts["lock"]
    for field, rel in {
        "final_confirmation_sample_manifest_sha256": out_artifacts / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
        "final_gold_write_plans_sha256": out_artifacts / "FINAL_GOLD_WRITE_PLANS.jsonl",
        "final_gold_programs_sha256": out_artifacts / "FINAL_GOLD_PROGRAMS.jsonl",
        "final_gold_post_state_hashes_sha256": out_artifacts / "FINAL_GOLD_POST_STATE_HASHES.jsonl",
        "final_gold_corpus_sha256": out_artifacts / "FINAL_GOLD_CORPUS.jsonl",
        "source_task_invalid_exclusions_sha256": out_artifacts / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl",
        "final_gold_replay_report_sha256": out_artifacts / "FINAL_GOLD_REPLAY_REPORT.json",
        "final_distribution_report_sha256": out_artifacts / "FINAL_DISTRIBUTION_REPORT.json",
        "final_overlap_audit_sha256": out_artifacts / "FINAL_OVERLAP_AUDIT.json",
        "mcnemar_threshold_sensitivity_sha256": out_artifacts / "MCNEMAR_THRESHOLD_SENSITIVITY_N481.json",
    }.items():
        lock[field] = sha256_file(rel)
    write_json(out_dir / "STAGE6E_FINAL_REGISTRATION_LOCK.json", lock)
    validation_report = f"""# Stage 6E Final Registration Revision Validation Report

Status: {lock['status']}

Validation date: 2026-08-24

- original registered N: 500
- source task invalid exclusions: 19
- replacement samples: 0
- final confirmation N: 481
- original review accepted gold: 460
- corrected review accepted gold: 21
- final gold replay: {artifacts['replay_report']['pass_count']} / 481 PASS
- final gold freeze created: true
- confirmation_run_allowed_now: false
- model_called: false
- gpu_called: false
"""
    readme = """# Stage 6E Final Registration Revision

This package locks the final Stage 6 confirmation denominator after human review.

The original registered set had 500 CRUDSQL official test Create samples. Stage 6E
excludes exactly the 19 SOURCE_TASK_INVALID items identified by the accepted R04
resolution workflow, with no replacement samples. The final confirmation set has
481 samples: 460 original review-accepted gold items plus 21 corrected-and-
re-reviewed accepted gold items.

This stage creates the final gold corpus hash and replays all 481 final gold
programs on fresh isolated SQLite databases. It does not call a model, does not
use GPU, and does not permit confirmation inference. The next stage is GPU
environment preflight after reviewer acceptance.
"""
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report)
    write_text(out_dir / "REVIEWER_README.md", readme)
    members = [
        out_dir / "STAGE6E_FINAL_REGISTRATION_LOCK.json",
        out_dir / "VALIDATION_REPORT.md",
        out_dir / "REVIEWER_README.md",
        *(out_artifacts / name for name in [
            "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl",
            "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
            "FINAL_GOLD_WRITE_PLANS.jsonl",
            "FINAL_GOLD_PROGRAMS.jsonl",
            "FINAL_GOLD_POST_STATE_HASHES.jsonl",
            "FINAL_GOLD_CORPUS.jsonl",
            "FINAL_GOLD_REPLAY_REPORT.json",
            "FINAL_DISTRIBUTION_REPORT.json",
            "FINAL_OVERLAP_AUDIT.json",
            "MCNEMAR_THRESHOLD_SENSITIVITY_N481.json",
        ]),
    ]
    archive = make_archive(out_dir / ARCHIVE_NAME, members, out_dir)
    lock["archive"] = archive
    write_json(out_dir / "STAGE6E_FINAL_REGISTRATION_LOCK.json", lock)
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(STAGE6E_DIR))
    args = parser.parse_args(argv)
    lock = create_stage6e_final_registration(out_dir=Path(args.out_dir))
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if lock["status"] == "FINAL_CONFIRMATION_REGISTRATION_LOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
