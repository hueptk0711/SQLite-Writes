#!/usr/bin/env python3
"""Create Stage 6D corrected-gold re-review setup for R04-correctable items."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.audit_crudsql_stage6a import (
    expected_row_after_insert,
    table_fingerprint,
    table_info,
)
from scripts.data.create_stage6c_gold_review_setup import canonical_json


STAGE6B_DIR = PROJECT_ROOT / "stage6_crudsql_registration"
STAGE6C_SETUP_DIR = PROJECT_ROOT / "stage6_gold_review_setup"
STAGE6C_R04_DIR = PROJECT_ROOT / "stage6_gold_review_r04_resolution"
STAGE6D_DIR = PROJECT_ROOT / "stage6_corrected_gold_review_setup"
STAGE6C_R04_COMMIT = "7f0950184ee40afb53581bd7b2127862fd581cde"
ARCHIVE_NAME = "stage6d_corrected_gold_review_setup_artifacts_20260824.zip"
C01_ARCHIVE_NAME = "Stage6D_C01_corrected_review_packet_20260824.zip"
C02_ARCHIVE_NAME = "Stage6D_C02_corrected_review_packet_20260824.zip"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)
REVIEW_PACKET_COLUMNS = [
    "stage6_sample_id",
    "upstream_sample_locator",
    "corrected_authored_content_sha256",
    "decision",
    "notes",
    "reviewed_by",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def column_index_for_header(item: dict[str, Any], header_name: str) -> int:
    headers = item["official_table_metadata"]["header"]
    matches = [index for index, value in enumerate(headers) if value == header_name]
    if len(matches) != 1:
        raise ValueError(f"{item['stage6_sample_id']}: header not unique: {header_name}")
    return matches[0]


def normalize_correction_operations(item: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if "column" in spec:
        operations.append(
            {
                "operation": "SET_VALUE",
                "column": spec["column"],
                "column_index": column_index_for_header(item, spec["column"]),
                "registered_value": spec.get("registered_value"),
                "corrected_value": spec["corrected_value"],
                "normalization": spec.get("normalization"),
                "evidence": spec.get("evidence", ""),
            }
        )
    if "changes" in spec:
        for change in spec["changes"]:
            operations.append(
                {
                    "operation": "SET_VALUE",
                    "column": change["column"],
                    "column_index": column_index_for_header(item, change["column"]),
                    "registered_value": change.get("registered_value"),
                    "corrected_value": change["corrected_value"],
                    "normalization": spec.get("normalization") or spec.get("unit_rules"),
                    "evidence": spec.get("evidence", ""),
                }
            )
    if "remove_column" in spec:
        operations.append(
            {
                "operation": "REMOVE_COLUMN",
                "column": spec["remove_column"],
                "column_index": column_index_for_header(item, spec["remove_column"]),
                "registered_value": spec.get("registered_value"),
                "evidence": spec.get("evidence", ""),
            }
        )
    if "remove" in spec:
        remove = spec["remove"]
        operations.append(
            {
                "operation": "REMOVE_COLUMN",
                "column": remove["column"],
                "column_index": column_index_for_header(item, remove["column"]),
                "registered_value": remove.get("registered_value"),
                "evidence": spec.get("evidence", ""),
            }
        )
    if "add" in spec:
        add = spec["add"]
        operations.append(
            {
                "operation": "SET_VALUE",
                "column": add["column"],
                "column_index": column_index_for_header(item, add["column"]),
                "registered_value": None,
                "corrected_value": add["corrected_value"],
                "normalization": spec.get("normalization"),
                "evidence": spec.get("evidence", ""),
            }
        )
    if not operations:
        raise ValueError(f"{item['stage6_sample_id']}: unsupported correction spec")
    return operations


def apply_operations_to_plan(
    item: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = deepcopy(item["gold_write_plan"])
    table_id = item["source"]["table_id"]
    current_by_index = {
        int(index): value
        for index, value in zip(plan["column_indexes"], plan["values"])
    }
    for operation in operations:
        index = int(operation["column_index"])
        if operation["operation"] == "SET_VALUE":
            current_by_index[index] = operation["corrected_value"]
        elif operation["operation"] == "REMOVE_COLUMN":
            current_by_index.pop(index, None)
        else:  # pragma: no cover - protected by normalizer.
            raise ValueError(operation["operation"])

    column_indexes = sorted(current_by_index)
    columns = [f"col_{index + 1}" for index in column_indexes]
    values = [current_by_index[index] for index in column_indexes]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql_template = f'INSERT INTO "Table_{table_id}" ({quoted_columns}) VALUES ({placeholders})'

    plan["column_indexes"] = column_indexes
    plan["columns"] = columns
    plan["values"] = values
    plan["expected_inserted_row"] = []

    program = {
        "sqlite_parameter_style": "qmark",
        "sql_template": sql_template,
        "parameters": values,
    }
    return plan, program


def execute_corrected_insert(
    stage6b_dir: Path,
    item: dict[str, Any],
    plan: dict[str, Any],
    program: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    table_id = item["source"]["table_id"]
    table_name = f"Table_{table_id}"
    source = sqlite3.connect(stage6b_dir / f"isolated_table_dbs/crudsql_db_{table_id}.sqlite")
    con = sqlite3.connect(":memory:")
    try:
        source.backup(con)
    finally:
        source.close()
    try:
        info = table_info(con, table_name)
        before = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        initial_fp = table_fingerprint(con, table_name)
        cursor = con.execute(program["sql_template"], program["parameters"])
        inserted_rowid = cursor.lastrowid
        inserted = list(con.execute(f'SELECT * FROM "{table_name}" WHERE rowid=?', (inserted_rowid,)).fetchone())
        after = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        expected = expected_row_after_insert(info, plan["column_indexes"], plan["values"])
        post_fp = table_fingerprint(con, table_name)
    finally:
        con.close()
    if after != before + 1:
        raise RuntimeError(f"{item['stage6_sample_id']}: corrected insert row count did not increment by one")
    if inserted != expected:
        raise RuntimeError(f"{item['stage6_sample_id']}: corrected inserted row did not match expected")
    if any(value is not None for idx, value in enumerate(inserted) if idx not in set(plan["column_indexes"])):
        raise RuntimeError(f"{item['stage6_sample_id']}: corrected insert set unspecified column")
    plan["expected_inserted_row"] = expected
    program["expected_inserted_row"] = expected
    hashes = {
        "schema_sha256": initial_fp["schema_sha256"],
        "initial_state_sha256": initial_fp["initial_state_sha256"],
        "corrected_post_state_sha256": post_fp["initial_state_sha256"],
        "corrected_gold_write_plan_sha256": sha256_text(canonical_json(plan)),
        "corrected_gold_program_sha256": sha256_text(canonical_json(program)),
    }
    execution = {
        "pre_insert_row_count": before,
        "post_insert_row_count": after,
        "inserted_rowid": inserted_rowid,
        "actual_inserted_row": inserted,
        "expected_inserted_row": expected,
    }
    return hashes, execution


def write_review_packet(path: Path, rows: list[dict[str, Any]], reviewer_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_PACKET_COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "stage6_sample_id": row["stage6_sample_id"],
                    "upstream_sample_locator": row["upstream_sample_locator"],
                    "corrected_authored_content_sha256": row["corrected_authored_content_sha256"],
                    "decision": "",
                    "notes": "",
                    "reviewed_by": reviewer_id,
                }
            )


def make_archive(archive_path: Path, members: list[Path], root: Path) -> dict[str, Any]:
    if archive_path.exists():
        archive_path.unlink()
    member_rows: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(members, key=lambda item: item.relative_to(root).as_posix()):
            rel = member.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, member.read_bytes())
            member_rows.append({"path": rel, "sha256": sha256_file(member)})
    return {
        "path": archive_path.relative_to(root).as_posix(),
        "sha256": sha256_file(archive_path),
        "member_count": len(member_rows),
        "members": member_rows,
    }


def create_corrected_gold_review_setup(
    stage6b_dir: Path = STAGE6B_DIR,
    setup_dir: Path = STAGE6C_SETUP_DIR,
    r04_dir: Path = STAGE6C_R04_DIR,
    out_dir: Path = STAGE6D_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = out_dir / "artifacts"
    packets = out_dir / "corrected_review_packets"
    artifacts.mkdir(parents=True, exist_ok=True)
    packets.mkdir(parents=True, exist_ok=True)

    queue = read_json(r04_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json")
    if queue.get("count") != 21:
        raise SystemExit("Expected exactly 21 R04-correctable items")
    setup_items = {
        item["stage6_sample_id"]: item
        for item in read_jsonl(setup_dir / "artifacts" / "gold_review_items.jsonl")
    }
    table_metadata = read_jsonl(setup_dir / "artifacts" / "official_table_metadata.jsonl")

    corrected_items: list[dict[str, Any]] = []
    corrected_plans: list[dict[str, Any]] = []
    corrected_programs: list[dict[str, Any]] = []
    corrected_post_hashes: list[dict[str, Any]] = []
    operation_rows: list[dict[str, Any]] = []
    for queue_item in queue["items"]:
        sample_id = queue_item["stage6_sample_id"]
        item = setup_items[sample_id]
        operations = normalize_correction_operations(item, queue_item["correction_spec"])
        plan, program = apply_operations_to_plan(item, operations)
        hashes, execution = execute_corrected_insert(stage6b_dir, item, plan, program)
        corrected_plan = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": item["upstream_sample_locator"],
            "table_id": item["source"]["table_id"],
            "operation": "INSERT",
            "column_indexes": plan["column_indexes"],
            "columns": plan["columns"],
            "values": plan["values"],
            "expected_inserted_row": plan["expected_inserted_row"],
            "fresh_db_per_sample": True,
            "source_correctable_authored_content_sha256": queue_item["authored_content_sha256"],
        }
        corrected_program = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": item["upstream_sample_locator"],
            "table_id": item["source"]["table_id"],
            "sqlite_parameter_style": "qmark",
            "sql_template": program["sql_template"],
            "parameters": program["parameters"],
            "expected_inserted_row": program["expected_inserted_row"],
            "source_correctable_authored_content_sha256": queue_item["authored_content_sha256"],
        }
        content = {
            "stage": "Stage6D_CORRECTED_GOLD_REVIEW_ITEM",
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": item["upstream_sample_locator"],
            "source": item["source"],
            "human_re_review_scope": [
                "R04-correctable item was corrected deterministically before any model run",
                "Chinese NL plus official CRUDSQL annotation supports the corrected value mapping",
                "corrected INSERT columns and values reflect the intended write",
                "omitted fields remain unsupported by the NL instruction",
            ],
            "question": item["question"],
            "official_annotation": item["official_annotation"],
            "official_table_metadata": item["official_table_metadata"],
            "original_authored_content_sha256": item["authored_content_sha256"],
            "original_gold_write_plan": item["gold_write_plan"],
            "original_gold_program": item["gold_program"],
            "original_hashes": item["hashes"],
            "r04_resolution": {
                "classification": "CORRECTABLE_GOLD_ERROR",
                "rationale": queue_item["rationale"],
                "correction_spec": queue_item["correction_spec"],
                "canonical_correction_operations": operations,
            },
            "corrected_gold_write_plan": {
                "operation": "INSERT",
                "columns": corrected_plan["columns"],
                "column_indexes": corrected_plan["column_indexes"],
                "values": corrected_plan["values"],
                "expected_inserted_row": corrected_plan["expected_inserted_row"],
                "fresh_db_per_sample": True,
            },
            "corrected_gold_program": {
                "sqlite_parameter_style": "qmark",
                "sql_template": corrected_program["sql_template"],
                "parameters": corrected_program["parameters"],
            },
            "corrected_execution_audit": execution,
            "corrected_hashes": {
                **hashes,
                "official_annotation_sha256": item["hashes"]["official_annotation_sha256"],
                "official_table_metadata_sha256": item["hashes"]["official_table_metadata_sha256"],
            },
        }
        corrected_item = content | {
            "corrected_authored_content_sha256": sha256_text(canonical_json(content))
        }
        corrected_items.append(corrected_item)
        corrected_plans.append(corrected_plan | {"corrected_gold_write_plan_sha256": hashes["corrected_gold_write_plan_sha256"]})
        corrected_programs.append(corrected_program | {"corrected_gold_program_sha256": hashes["corrected_gold_program_sha256"]})
        corrected_post_hashes.append(
            {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": item["upstream_sample_locator"],
                "table_id": item["source"]["table_id"],
                "initial_state_sha256": hashes["initial_state_sha256"],
                "corrected_post_state_sha256": hashes["corrected_post_state_sha256"],
            }
        )
        operation_rows.append(
            {
                "stage6_sample_id": sample_id,
                "r04_rationale": queue_item["rationale"],
                "correction_spec": queue_item["correction_spec"],
                "canonical_correction_operations": operations,
            }
        )

    corrected_items = sorted(corrected_items, key=lambda row: row["stage6_sample_id"])
    corrected_plans = sorted(corrected_plans, key=lambda row: row["stage6_sample_id"])
    corrected_programs = sorted(corrected_programs, key=lambda row: row["stage6_sample_id"])
    corrected_post_hashes = sorted(corrected_post_hashes, key=lambda row: row["stage6_sample_id"])
    operation_rows = sorted(operation_rows, key=lambda row: row["stage6_sample_id"])

    write_jsonl(artifacts / "corrected_gold_review_items.jsonl", corrected_items)
    write_jsonl(artifacts / "corrected_gold_write_plans.jsonl", corrected_plans)
    write_jsonl(artifacts / "corrected_gold_programs.jsonl", corrected_programs)
    write_jsonl(artifacts / "corrected_gold_post_state_hashes.jsonl", corrected_post_hashes)
    write_jsonl(artifacts / "correction_operations.jsonl", operation_rows)
    write_jsonl(artifacts / "official_table_metadata.jsonl", table_metadata)

    protocol = {
        "stage": "Stage6D_CORRECTED_GOLD_RE_REVIEW_SETUP",
        "status": "LOCKED_PENDING_CORRECTED_ITEM_HUMAN_RE_REVIEW",
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": False,
        "scope": "21_R04_CORRECTABLE_GOLD_ERROR_ITEMS_ONLY",
        "forbidden_actions": [
            "do_not_modify_the_19_SOURCE_TASK_INVALID_items_in_this_patch",
            "do_not_drop_or_replace_any_registered_sample",
            "do_not_create_final_gold_freeze_from_corrected_items_before_re_review_acceptance",
            "do_not_permit_gpu_preflight_before_all_corrected_items_and_registration_revision_are_resolved",
        ],
        "reviewer_roles": ["C01", "C02"],
        "reviewer_roles_must_be_distinct": True,
        "reviewers_must_not_see_model_predictions": True,
        "allowed_decisions_after_execution": ["approved", "rejected"],
        "notes_required_for_rejected": True,
        "reviewer_isolation": {
            "C01_must_not_see_C02_decisions_or_notes_before_submission": True,
            "C02_must_not_see_C01_decisions_or_notes_before_submission": True,
            "cross_reviewer_discussion_before_submission": False,
            "each_reviewer_receives_only_own_decision_packet": True,
            "reviewer_outputs_are_sealed_until_both_submitted": True,
        },
        "final_decision_rule": [
            {"C01": "approved", "C02": "approved", "action": "corrected_item_accepted_pending_final_gold_freeze"},
            {"C01": "rejected", "C02": "rejected", "action": "correction_rejected_confirmation_blocked"},
            {"C01": "approved", "C02": "rejected", "action": "blind_C03_adjudication"},
            {"C01": "rejected", "C02": "approved", "action": "blind_C03_adjudication"},
            {"C03": "approved", "action": "corrected_item_accepted_pending_final_gold_freeze"},
            {"C03": "rejected", "action": "correction_rejected_confirmation_blocked"},
        ],
    }
    write_json(out_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json", protocol)

    c01_tsv = packets / "stage6d_corrected_gold_review_C01.tsv"
    c02_tsv = packets / "stage6d_corrected_gold_review_C02.tsv"
    write_review_packet(c01_tsv, corrected_items, "C01")
    write_review_packet(c02_tsv, corrected_items, "C02")
    c01_manifest = packets / "Stage6D_C01_PACKET_MANIFEST.json"
    c02_manifest = packets / "Stage6D_C02_PACKET_MANIFEST.json"
    write_json(c01_manifest, packet_manifest(out_dir, c01_tsv, "C01"))
    write_json(c02_manifest, packet_manifest(out_dir, c02_tsv, "C02"))
    c01_archive = make_archive(
        out_dir / C01_ARCHIVE_NAME,
        [
            out_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json",
            artifacts / "corrected_gold_review_items.jsonl",
            artifacts / "official_table_metadata.jsonl",
            c01_manifest,
            c01_tsv,
        ],
        out_dir,
    )
    c02_archive = make_archive(
        out_dir / C02_ARCHIVE_NAME,
        [
            out_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json",
            artifacts / "corrected_gold_review_items.jsonl",
            artifacts / "official_table_metadata.jsonl",
            c02_manifest,
            c02_tsv,
        ],
        out_dir,
    )

    manifest = {
        "stage": "Stage6D_CORRECTED_GOLD_RE_REVIEW_SETUP",
        "status": "PASS_PENDING_CORRECTED_ITEM_RE_REVIEW",
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": False,
        "stage6c_r04_resolution_commit": STAGE6C_R04_COMMIT,
        "corrected_item_count": len(corrected_items),
        "source_invalid_item_count_not_processed_here": 19,
        "correctable_queue_sha256": sha256_file(r04_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json"),
        "source_invalid_queue_sha256": sha256_file(r04_dir / "SOURCE_TASK_INVALID_QUEUE.json"),
        "corrected_gold_review_items_sha256": sha256_file(artifacts / "corrected_gold_review_items.jsonl"),
        "corrected_gold_write_plans_sha256": sha256_file(artifacts / "corrected_gold_write_plans.jsonl"),
        "corrected_gold_programs_sha256": sha256_file(artifacts / "corrected_gold_programs.jsonl"),
        "corrected_gold_post_state_hashes_sha256": sha256_file(artifacts / "corrected_gold_post_state_hashes.jsonl"),
        "correction_operations_sha256": sha256_file(artifacts / "correction_operations.jsonl"),
        "official_table_metadata_sha256": sha256_file(artifacts / "official_table_metadata.jsonl"),
        "protocol_lock_sha256": sha256_file(out_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json"),
        "C01_packet_archive": c01_archive,
        "C02_packet_archive": c02_archive,
        "next_steps": [
            "send_C01_and_C02_corrected_review_packets_to_distinct_reviewers",
            "ingest_completed_C01_C02_decisions",
            "perform_blind_C03_adjudication_if_needed",
            "do_not_create_final_gold_freeze_until_corrected_review_and_stage6_registration_revision_are_accepted",
        ],
    }
    write_json(out_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json", manifest)
    write_text(
        out_dir / "VALIDATION_REPORT.md",
        f"""# Stage 6D Corrected Gold Review Setup Validation Report

Status: PASS

Validation date: 2026-08-24

- corrected R04-correctable items: {len(corrected_items)}
- source-invalid items processed here: 0
- model_called: false
- gpu_called: false
- confirmation_run_allowed_now: false
- final_gold_freeze_created: false
""",
    )
    write_text(
        out_dir / "REVIEWER_README.md",
        """# Stage 6D Corrected Gold Re-Review Setup

This package deterministically applies the 21 R04 CORRECTABLE_GOLD_ERROR
specifications and prepares isolated C01/C02 human re-review packets.

It does not process the 19 SOURCE_TASK_INVALID items, does not revise the
registered dataset, does not create a final gold freeze, and does not permit
GPU preflight.
""",
    )
    archive = make_archive(
        out_dir / ARCHIVE_NAME,
        [
            out_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json",
            out_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json",
            out_dir / "VALIDATION_REPORT.md",
            out_dir / "REVIEWER_README.md",
            artifacts / "corrected_gold_review_items.jsonl",
            artifacts / "corrected_gold_write_plans.jsonl",
            artifacts / "corrected_gold_programs.jsonl",
            artifacts / "corrected_gold_post_state_hashes.jsonl",
            artifacts / "correction_operations.jsonl",
            artifacts / "official_table_metadata.jsonl",
            c01_manifest,
            c01_tsv,
            c02_manifest,
            c02_tsv,
        ],
        out_dir,
    )
    manifest["archive"] = archive
    write_json(out_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json", manifest)
    return manifest


def packet_manifest(out_dir: Path, tsv_path: Path, reviewer_id: str) -> dict[str, Any]:
    return {
        "stage": "Stage6D_CORRECTED_GOLD_REVIEW_ISOLATED_PACKET",
        "status": "READY_FOR_INDEPENDENT_CORRECTED_ITEM_RE_REVIEW",
        "reviewer_id": reviewer_id,
        "contains_only_own_decision_packet": True,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "corrected_gold_review_items_sha256": sha256_file(out_dir / "artifacts" / "corrected_gold_review_items.jsonl"),
        "official_table_metadata_sha256": sha256_file(out_dir / "artifacts" / "official_table_metadata.jsonl"),
        "protocol_lock_sha256": sha256_file(out_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json"),
        "decision_packet": {
            "path": tsv_path.relative_to(out_dir).as_posix(),
            "sha256": sha256_file(tsv_path),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage6b-dir", default=str(STAGE6B_DIR))
    parser.add_argument("--setup-dir", default=str(STAGE6C_SETUP_DIR))
    parser.add_argument("--r04-dir", default=str(STAGE6C_R04_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6D_DIR))
    args = parser.parse_args(argv)
    report = create_corrected_gold_review_setup(
        Path(args.stage6b_dir),
        Path(args.setup_dir),
        Path(args.r04_dir),
        Path(args.out_dir),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
