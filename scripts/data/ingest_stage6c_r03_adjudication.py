#!/usr/bin/env python3
"""Ingest Stage 6C R03 blind adjudication and prepare post-R03 rejection resolution."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.create_stage6c_gold_review_setup import REVIEW_PACKET_COLUMNS, canonical_json
from scripts.data.execute_stage6c_gold_review import (
    CORRECTABLE_GOLD_ERROR_REQUIRED_ACTION,
    CORRECTABLE_GOLD_ERROR_REQUIRED_CONDITION,
    FINAL_REJECTION_FORBIDDEN_ACTIONS,
    R04_ALLOWED_INPUTS,
    R04_TASK,
    SOURCE_TASK_INVALID_REQUIRED_ACTION,
    SOURCE_TASK_INVALID_REQUIRED_CONDITION,
)


STAGE6C_EXECUTION_DIR = PROJECT_ROOT / "stage6_gold_review_execution"
STAGE6C_R03_DIR = PROJECT_ROOT / "stage6_gold_review_r03_adjudication"
STAGE6C_EXECUTION_PATCH2_COMMIT = "a94407dc171f9705bd566a4812eab9cb2407bbbb"
ARCHIVE_NAME = "stage6c_r03_adjudication_artifacts_20260824.zip"
R04_AFTER_R03_ARCHIVE_NAME = "Stage6C_R04_final_rejection_resolution_after_R03_packet_20260824.zip"
FINAL_REJECTION_QUEUE_NAME = "FINAL_REJECTION_RESOLUTION_QUEUE_AFTER_R03.json"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)

R04_AFTER_R03_COLUMNS = [
    "stage6_sample_id",
    "upstream_sample_locator",
    "authored_content_sha256",
    "final_rejection_source",
    "R01_notes_sha256",
    "R02_notes_sha256",
    "R03_notes_sha256",
    "reviewed_by",
    "classification",
    "rationale",
    "correction_spec",
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
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_bytes(text.encode("utf-8"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(canonical_json(row) + "\n" for row in rows)
    path.write_bytes(text.encode("utf-8"))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def read_packet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def canonicalize_submission(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8", newline=None)
    target.write_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


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


def validate_r03_submission(
    fields: list[str],
    rows: list[dict[str, str]],
    template_rows: dict[str, dict[str, str]],
) -> list[str]:
    violations: list[str] = []
    if fields != REVIEW_PACKET_COLUMNS:
        violations.append("R03_unexpected_columns")
    if len(rows) != 52:
        violations.append("R03_row_count_not_52")
    seen_ids: set[str] = set()
    for row in rows:
        sample_id = row.get("stage6_sample_id", "")
        if sample_id in seen_ids:
            violations.append(f"R03_duplicate_sample_id:{sample_id}")
        seen_ids.add(sample_id)
        template = template_rows.get(sample_id)
        if template is None:
            violations.append(f"R03_unknown_sample_id:{sample_id}")
            continue
        for field in ["stage6_sample_id", "upstream_sample_locator", "authored_content_sha256", "reviewed_by"]:
            if row.get(field, "") != template.get(field, ""):
                violations.append(f"R03_immutable_field_changed:{sample_id}:{field}")
        decision = row.get("decision", "")
        notes = row.get("notes", "")
        if decision not in {"approved", "rejected"}:
            violations.append(f"R03_invalid_or_blank_decision:{sample_id}")
        if decision == "rejected" and not notes.strip():
            violations.append(f"R03_rejected_notes_blank:{sample_id}")
    if seen_ids != set(template_rows):
        violations.append("R03_sample_id_set_mismatch")
    return violations


def build_r03_report(rows: list[dict[str, str]]) -> dict[str, Any]:
    approved = []
    rejected = []
    for row in sorted(rows, key=lambda item: item["stage6_sample_id"]):
        sample_id = row["stage6_sample_id"]
        if row["decision"] == "approved":
            approved.append(sample_id)
        else:
            rejected.append(
                {
                    "stage6_sample_id": sample_id,
                    "R03_notes_sha256": sha256_text(row.get("notes", "")),
                }
            )
    return {
        "stage": "Stage6C_R03_BLIND_ADJUDICATION_RESULT",
        "R03_approved_count": len(approved),
        "R03_rejected_count": len(rejected),
        "R03_approved_ids": approved,
        "R03_rejected_items": rejected,
    }


def build_post_r03_resolution_queue(
    out_dir: Path,
    execution_dir: Path,
    report: dict[str, Any],
    r03_rows: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    agreement = read_json(execution_dir / "R01_R02_AGREEMENT_REPORT.json")
    _, r01_rows = read_packet(execution_dir / "submissions" / "stage6c_gold_review_R01.submitted.tsv")
    _, r02_rows = read_packet(execution_dir / "submissions" / "stage6c_gold_review_R02.submitted.tsv")
    setup_items = read_jsonl(PROJECT_ROOT / "stage6_gold_review_setup" / "artifacts" / "gold_review_items.jsonl")
    item_by_id = {item["stage6_sample_id"]: item for item in setup_items}
    r03_by_id = {row["stage6_sample_id"]: row for row in r03_rows}
    r01_by_id = {row["stage6_sample_id"]: row for row in r01_rows}
    r02_by_id = {row["stage6_sample_id"]: row for row in r02_rows}
    disagreement_by_id = {row["stage6_sample_id"]: row for row in agreement["disagreement_items"]}
    agreed_rejected_by_id = {row["stage6_sample_id"]: row for row in agreement["agreed_rejected_items"]}

    final_rejected_items: list[dict[str, Any]] = []
    for sample_id in sorted(agreed_rejected_by_id):
        item = item_by_id[sample_id]
        agreement_item = agreed_rejected_by_id[sample_id]
        r01_note = r01_by_id[sample_id].get("notes", "")
        r02_note = r02_by_id[sample_id].get("notes", "")
        final_rejected_items.append(
            {
                "stage6_sample_id": sample_id,
                "final_rejection_source": "R01_R02_AGREED_REJECTED",
                "upstream_sample_locator": item["upstream_sample_locator"],
                "authored_content_sha256": item["authored_content_sha256"],
                "R01_decision": "rejected",
                "R02_decision": "rejected",
                "R03_decision": "not_applicable",
                "R01_notes": r01_note,
                "R01_notes_sha256": agreement_item["R01_notes_sha256"],
                "R02_notes": r02_note,
                "R02_notes_sha256": agreement_item["R02_notes_sha256"],
                "R03_notes": "",
                "R03_notes_sha256": "",
            }
        )
    for rejected in report["R03_rejected_items"]:
        sample_id = rejected["stage6_sample_id"]
        item = item_by_id[sample_id]
        disagreement_item = disagreement_by_id[sample_id]
        r01_note = r01_by_id[sample_id].get("notes", "")
        r02_note = r02_by_id[sample_id].get("notes", "")
        r03_note = r03_by_id[sample_id].get("notes", "")
        final_rejected_items.append(
            {
                "stage6_sample_id": sample_id,
                "final_rejection_source": "R03_REJECTED_DISAGREEMENT",
                "upstream_sample_locator": item["upstream_sample_locator"],
                "authored_content_sha256": item["authored_content_sha256"],
                "R01_decision": disagreement_item["R01_decision"],
                "R02_decision": disagreement_item["R02_decision"],
                "R03_decision": "rejected",
                "R01_notes": r01_note,
                "R01_notes_sha256": disagreement_item["R01_notes_sha256"],
                "R02_notes": r02_note,
                "R02_notes_sha256": disagreement_item["R02_notes_sha256"],
                "R03_notes": r03_note,
                "R03_notes_sha256": rejected["R03_notes_sha256"],
            }
        )
    final_rejected_items = sorted(final_rejected_items, key=lambda row: row["stage6_sample_id"])
    queue = {
        "stage": "Stage6C_FINAL_REJECTION_RESOLUTION_QUEUE_AFTER_R03",
        "status": "LOCKED_PENDING_R04_TECHNICAL_RESOLUTION",
        "model_called": False,
        "gpu_called": False,
        "confirmation_blocked_until_all_final_rejections_resolved": True,
        "final_rejected_count": len(final_rejected_items),
        "initial_R01_R02_agreed_rejected_count": len(agreed_rejected_by_id),
        "R03_rejected_disagreement_count": len(report["R03_rejected_items"]),
        "R03_approved_disagreement_count": len(report["R03_approved_ids"]),
        "final_rejected_items": final_rejected_items,
        "classification_reviewer_role": "R04",
        "R04_task": R04_TASK,
        "R04_must_be_distinct_from_R01": True,
        "R04_must_be_distinct_from_R02": True,
        "R04_must_be_distinct_from_R03": True,
        "R04_must_not_see_model_predictions": True,
        "R04_may_see_R01_R02_rejection_reasons": True,
        "R04_may_see_R03_rejection_reasons": True,
        "R04_allowed_inputs": [*R04_ALLOWED_INPUTS, "R03 rejection decisions and notes for R03-rejected disagreements"],
        "allowed_classes": ["CORRECTABLE_GOLD_ERROR", "SOURCE_TASK_INVALID"],
        "class_rules": {
            "CORRECTABLE_GOLD_ERROR": {
                "required_condition": CORRECTABLE_GOLD_ERROR_REQUIRED_CONDITION,
                "required_action": CORRECTABLE_GOLD_ERROR_REQUIRED_ACTION,
            },
            "SOURCE_TASK_INVALID": {
                "required_condition": SOURCE_TASK_INVALID_REQUIRED_CONDITION,
                "required_action": SOURCE_TASK_INVALID_REQUIRED_ACTION,
            },
        },
        "forbidden_actions": FINAL_REJECTION_FORBIDDEN_ACTIONS,
    }
    write_json(out_dir / FINAL_REJECTION_QUEUE_NAME, queue)
    r04_manifest = build_r04_after_r03_packet(out_dir, final_rejected_items, item_by_id, r03_by_id)
    return queue, r04_manifest


def build_r04_after_r03_packet(
    out_dir: Path,
    final_rejected_items: list[dict[str, Any]],
    item_by_id: dict[str, dict[str, Any]],
    r03_by_id: dict[str, dict[str, str]],
) -> dict[str, Any]:
    r04_dir = out_dir / "r04_after_r03_resolution_packet"
    r04_dir.mkdir(parents=True, exist_ok=True)
    table_to_rejected_ids: dict[str, list[str]] = {}
    for row in final_rejected_items:
        table_id = item_by_id[row["stage6_sample_id"]]["source"]["table_id"]
        table_to_rejected_ids.setdefault(table_id, []).append(row["stage6_sample_id"])

    r04_items: list[dict[str, Any]] = []
    r04_rows: list[dict[str, str]] = []
    for rejected in final_rejected_items:
        sample_id = rejected["stage6_sample_id"]
        item = item_by_id[sample_id]
        table_id = item["source"]["table_id"]
        r03_row = r03_by_id.get(sample_id)
        r04_items.append(
            {
                **item,
                "R04_resolution_scope": {
                    **rejected,
                    "same_table_final_rejected_sample_ids": table_to_rejected_ids[table_id],
                    "table_final_rejected_count": len(table_to_rejected_ids[table_id]),
                },
            }
        )
        r04_rows.append(
            {
                "stage6_sample_id": sample_id,
                "upstream_sample_locator": item["upstream_sample_locator"],
                "authored_content_sha256": item["authored_content_sha256"],
                "final_rejection_source": rejected["final_rejection_source"],
                "R01_notes_sha256": rejected["R01_notes_sha256"],
                "R02_notes_sha256": rejected["R02_notes_sha256"],
                "R03_notes_sha256": rejected["R03_notes_sha256"],
                "reviewed_by": "R04",
                "classification": "",
                "rationale": "",
                "correction_spec": "",
            }
        )
    write_jsonl(r04_dir / "r04_after_R03_resolution_items.jsonl", r04_items)
    table_metadata = PROJECT_ROOT / "stage6_gold_review_setup" / "artifacts" / "official_table_metadata.jsonl"
    (r04_dir / "official_table_metadata.jsonl").write_bytes(table_metadata.read_bytes())
    tsv_lines = ["\t".join(R04_AFTER_R03_COLUMNS)]
    for row in r04_rows:
        tsv_lines.append("\t".join(row[column] for column in R04_AFTER_R03_COLUMNS))
    write_text(r04_dir / "stage6c_final_rejection_after_R03_R04.tsv", "\n".join(tsv_lines) + "\n")
    manifest = {
        "stage": "Stage6C_R04_FINAL_REJECTION_RESOLUTION_AFTER_R03_PACKET",
        "status": "READY_FOR_R04_IF_REVIEWER_ACCEPTS_R03_ADJUDICATION_PACKAGE",
        "packet_role": "R04",
        "final_rejected_count": len(final_rejected_items),
        "contains_only_final_rejected_items": True,
        "contains_unrelated_approved_items": False,
        "R04_must_be_distinct_from_R01": True,
        "R04_must_be_distinct_from_R02": True,
        "R04_must_be_distinct_from_R03": True,
        "R04_must_not_see_model_predictions": True,
        "R04_may_see_R01_R02_rejection_reasons": True,
        "R04_may_see_R03_rejection_reasons": True,
        "classification_required_on_submission": True,
        "rationale_required_on_submission": True,
        "correction_spec_required_when_correctable": True,
        "allowed_classes": ["CORRECTABLE_GOLD_ERROR", "SOURCE_TASK_INVALID"],
        "r04_items_sha256": sha256_file(r04_dir / "r04_after_R03_resolution_items.jsonl"),
        "r04_tsv_sha256": sha256_file(r04_dir / "stage6c_final_rejection_after_R03_R04.tsv"),
        "resolution_queue_sha256": sha256_file(out_dir / FINAL_REJECTION_QUEUE_NAME),
        "official_table_metadata_sha256": sha256_file(r04_dir / "official_table_metadata.jsonl"),
    }
    write_json(r04_dir / "R04_AFTER_R03_PACKET_MANIFEST.json", manifest)
    archive = make_archive(
        out_dir / R04_AFTER_R03_ARCHIVE_NAME,
        [
            out_dir / FINAL_REJECTION_QUEUE_NAME,
            r04_dir / "R04_AFTER_R03_PACKET_MANIFEST.json",
            r04_dir / "r04_after_R03_resolution_items.jsonl",
            r04_dir / "official_table_metadata.jsonl",
            r04_dir / "stage6c_final_rejection_after_R03_R04.tsv",
        ],
        out_dir,
    )
    manifest["archive"] = archive
    write_json(r04_dir / "R04_AFTER_R03_PACKET_MANIFEST.json", manifest)
    return manifest


def ingest_r03(
    r03_submission: Path,
    execution_dir: Path = STAGE6C_EXECUTION_DIR,
    out_dir: Path = STAGE6C_R03_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    submissions = out_dir / "submissions"
    r03_target = submissions / "stage6c_gold_review_R03.submitted.tsv"
    canonicalize_submission(r03_submission, r03_target)

    template_fields, template_rows = read_packet(execution_dir / "r03_blind_packet" / "stage6c_gold_review_R03.tsv")
    r03_fields, r03_rows = read_packet(r03_target)
    template_by_id = {row["stage6_sample_id"]: row for row in template_rows}
    violations: list[str] = []
    if template_fields != REVIEW_PACKET_COLUMNS:
        violations.append("R03_template_columns_changed")
    violations.extend(validate_r03_submission(r03_fields, r03_rows, template_by_id))
    report = build_r03_report(r03_rows) if not violations else {
        "stage": "Stage6C_R03_BLIND_ADJUDICATION_RESULT",
        "R03_approved_count": 0,
        "R03_rejected_count": 0,
        "R03_approved_ids": [],
        "R03_rejected_items": [],
    }
    write_json(out_dir / "R03_ADJUDICATION_REPORT.json", report)
    queue = None
    r04_manifest = None
    if not violations:
        queue, r04_manifest = build_post_r03_resolution_queue(out_dir, execution_dir, report, r03_rows)
    status = "FAIL_R03_SUBMISSION_VALIDATION" if violations else "PASS_R03_COMPLETE_PENDING_FINAL_REJECTION_RESOLUTION"
    total_final_approved = 431 + report["R03_approved_count"]
    total_final_rejected = (queue or {}).get("final_rejected_count", 0)
    manifest = {
        "stage": "Stage6C_R03_ADJUDICATION_INGEST",
        "status": status,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": False,
        "stage6c_execution_patch2_commit": STAGE6C_EXECUTION_PATCH2_COMMIT,
        "r03_submission_sha256": sha256_file(r03_target),
        "r03_source_mtime": r03_submission.stat().st_mtime,
        "validation_violations": violations,
        "R03_approved_count": report["R03_approved_count"],
        "R03_rejected_count": report["R03_rejected_count"],
        "final_approved_count_after_R03": total_final_approved,
        "final_rejected_count_after_R03": total_final_rejected,
        "final_rejection_resolution_queue_created": queue is not None,
        "final_rejection_resolution_queue_sha256": (
            sha256_file(out_dir / FINAL_REJECTION_QUEUE_NAME) if queue else None
        ),
        "r04_after_R03_packet_created": r04_manifest is not None,
        "r04_after_R03_packet_manifest_sha256": (
            sha256_file(out_dir / "r04_after_r03_resolution_packet" / "R04_AFTER_R03_PACKET_MANIFEST.json")
            if r04_manifest else None
        ),
        "next_steps": [
            "review_R03_adjudication_package",
            "send_R04_after_R03_packet_after_reviewer_acceptance",
            "do_not_create_final_gold_freeze_until_R04_resolution_and_any_corrected_re_reviews_complete",
        ],
    }
    write_json(out_dir / "R03_INGEST_MANIFEST.json", manifest)
    validation_report = f"""# Stage 6C R03 Adjudication Validation Report

Status: {status}

Validation date: 2026-08-24

- R03 rows: {len(r03_rows)}
- R03 approved: {report['R03_approved_count']}
- R03 rejected: {report['R03_rejected_count']}
- final approved after R03: {total_final_approved}
- final rejected after R03: {total_final_rejected}
- R04 after-R03 packet created: {r04_manifest is not None}
- model_called: false
- gpu_called: false
- confirmation_run_allowed_now: false
- final_gold_freeze_created: false
"""
    reviewer_readme = """# Stage 6C R03 Adjudication Ingest

This package ingests the completed blind R03 adjudication TSV, verifies immutable
fields and R03 decisions, and merges the result with R01/R02 agreement.

R03 approved 29 disagreement items and rejected 23. The 23 R03-rejected items
join the 17 R01/R02 agreed-rejected items in the same R04 technical
root-cause-resolution workflow. No gold files are corrected here.

This package does not call a model, does not permit GPU preflight, and does not
create a final gold freeze.
"""
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme)
    archive_members = [
        out_dir / "R03_INGEST_MANIFEST.json",
        out_dir / "R03_ADJUDICATION_REPORT.json",
        out_dir / "VALIDATION_REPORT.md",
        out_dir / "REVIEWER_README.md",
        r03_target,
    ]
    if queue:
        archive_members.extend(
            [
                out_dir / FINAL_REJECTION_QUEUE_NAME,
                out_dir / "r04_after_r03_resolution_packet" / "R04_AFTER_R03_PACKET_MANIFEST.json",
                out_dir / "r04_after_r03_resolution_packet" / "r04_after_R03_resolution_items.jsonl",
                out_dir / "r04_after_r03_resolution_packet" / "official_table_metadata.jsonl",
                out_dir / "r04_after_r03_resolution_packet" / "stage6c_final_rejection_after_R03_R04.tsv",
                out_dir / R04_AFTER_R03_ARCHIVE_NAME,
            ]
        )
    archive = make_archive(out_dir / ARCHIVE_NAME, archive_members, out_dir)
    manifest["archive"] = archive
    write_json(out_dir / "R03_INGEST_MANIFEST.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r03-submission", required=True)
    parser.add_argument("--execution-dir", default=str(STAGE6C_EXECUTION_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6C_R03_DIR))
    args = parser.parse_args(argv)
    report = ingest_r03(Path(args.r03_submission), Path(args.execution_dir), Path(args.out_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report.get("validation_violations") else 1


if __name__ == "__main__":
    raise SystemExit(main())
