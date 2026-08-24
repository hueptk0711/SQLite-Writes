#!/usr/bin/env python3
"""Ingest Stage 6C R01/R02 gold-review submissions and prepare blind R03 if needed."""

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

from scripts.data.create_stage6c_gold_review_setup import (
    REVIEW_PACKET_COLUMNS,
    STAGE6B_COMMIT,
    canonical_json,
)


STAGE6C_SETUP_DIR = PROJECT_ROOT / "stage6_gold_review_setup"
STAGE6C_EXECUTION_DIR = PROJECT_ROOT / "stage6_gold_review_execution"
STAGE6C_SETUP_COMMIT = "cd89a33f41d4be6ba8094789aa6141fafd55b2c8"
ARCHIVE_NAME = "stage6c_review_execution_artifacts_20260824.zip"
R03_ARCHIVE_NAME = "Stage6C_R03_blind_adjudication_packet_20260824.zip"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)


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


def copy_submission(source: Path, target: Path) -> None:
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


def validate_submission(
    role: str,
    rows: list[dict[str, str]],
    fields: list[str],
    template_rows: dict[str, dict[str, str]],
    item_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    violations: list[str] = []
    if fields != REVIEW_PACKET_COLUMNS:
        violations.append(f"{role}_unexpected_columns")
    if len(rows) != 500:
        violations.append(f"{role}_row_count_not_500")
    seen_ids: set[str] = set()
    allowed = {"approved", "rejected"}
    for row in rows:
        sample_id = row.get("stage6_sample_id", "")
        if sample_id in seen_ids:
            violations.append(f"{role}_duplicate_sample_id:{sample_id}")
        seen_ids.add(sample_id)
        template = template_rows.get(sample_id)
        item = item_by_id.get(sample_id)
        if template is None or item is None:
            violations.append(f"{role}_unknown_sample_id:{sample_id}")
            continue
        for field in [
            "stage6_sample_id",
            "upstream_sample_locator",
            "authored_content_sha256",
            "reviewed_by",
        ]:
            if row.get(field, "") != template.get(field, ""):
                violations.append(f"{role}_immutable_field_changed:{sample_id}:{field}")
        if row.get("authored_content_sha256", "") != item.get("authored_content_sha256"):
            violations.append(f"{role}_content_hash_mismatch:{sample_id}")
        decision = row.get("decision", "")
        notes = row.get("notes", "")
        if decision not in allowed:
            violations.append(f"{role}_invalid_or_blank_decision:{sample_id}")
        if decision == "rejected" and not notes.strip():
            violations.append(f"{role}_rejected_notes_blank:{sample_id}")
    if seen_ids != set(item_by_id):
        violations.append(f"{role}_sample_id_set_mismatch")
    return violations


def build_agreement(
    r01_rows: list[dict[str, str]],
    r02_rows: list[dict[str, str]],
) -> dict[str, Any]:
    r01_by_id = {row["stage6_sample_id"]: row for row in r01_rows}
    r02_by_id = {row["stage6_sample_id"]: row for row in r02_rows}
    agreed_approved: list[str] = []
    agreed_rejected: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    for sample_id in sorted(r01_by_id):
        r01 = r01_by_id[sample_id]
        r02 = r02_by_id[sample_id]
        pair = (r01["decision"], r02["decision"])
        if pair == ("approved", "approved"):
            agreed_approved.append(sample_id)
        elif pair == ("rejected", "rejected"):
            agreed_rejected.append(
                {
                    "stage6_sample_id": sample_id,
                    "R01_notes_sha256": sha256_text(r01.get("notes", "")),
                    "R02_notes_sha256": sha256_text(r02.get("notes", "")),
                }
            )
        else:
            disagreements.append(
                {
                    "stage6_sample_id": sample_id,
                    "R01_decision": r01["decision"],
                    "R02_decision": r02["decision"],
                    "R01_notes_sha256": sha256_text(r01.get("notes", "")),
                    "R02_notes_sha256": sha256_text(r02.get("notes", "")),
                    "blind_R03_required": True,
                }
            )
    return {
        "agreed_approved_count": len(agreed_approved),
        "agreed_rejected_count": len(agreed_rejected),
        "disagreement_count": len(disagreements),
        "agreed_approved_ids": agreed_approved,
        "agreed_rejected_items": agreed_rejected,
        "disagreement_items": disagreements,
    }


def build_r03_packet(
    out_dir: Path,
    review_items: list[dict[str, Any]],
    table_metadata_path: Path,
    disagreement_ids: set[str],
) -> dict[str, Any] | None:
    if not disagreement_ids:
        return None
    r03_dir = out_dir / "r03_blind_packet"
    r03_dir.mkdir(parents=True, exist_ok=True)
    selected = [row for row in review_items if row["stage6_sample_id"] in disagreement_ids]
    write_jsonl(r03_dir / "r03_blind_review_items.jsonl", selected)
    target_metadata = r03_dir / "official_table_metadata.jsonl"
    target_metadata.write_bytes(table_metadata_path.read_bytes())
    r03_tsv = r03_dir / "stage6c_gold_review_R03.tsv"
    with r03_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "stage6_sample_id",
                "upstream_sample_locator",
                "authored_content_sha256",
                "reviewed_by",
                "decision",
                "notes",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        for row in selected:
            writer.writerow(
                {
                    "stage6_sample_id": row["stage6_sample_id"],
                    "upstream_sample_locator": row["upstream_sample_locator"],
                    "authored_content_sha256": row["authored_content_sha256"],
                    "reviewed_by": "R03",
                    "decision": "",
                    "notes": "",
                }
            )
    manifest = {
        "stage": "Stage6C_BLIND_R03_ADJUDICATION_PACKET",
        "status": "READY_FOR_BLIND_R03_IF_REVIEWER_ACCEPTS_EXECUTION",
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "disagreement_count": len(selected),
        "R03_must_not_see_R01_decision": True,
        "R03_must_not_see_R02_decision": True,
        "R03_must_not_see_R01_notes": True,
        "R03_must_not_see_R02_notes": True,
        "R03_must_not_see_model_predictions": True,
        "contains_R01_R02_votes_or_notes": False,
        "r03_blind_review_items_sha256": sha256_file(r03_dir / "r03_blind_review_items.jsonl"),
        "official_table_metadata_sha256": sha256_file(target_metadata),
        "r03_tsv_sha256": sha256_file(r03_tsv),
    }
    write_json(r03_dir / "R03_BLIND_PACKET_MANIFEST.json", manifest)
    archive = make_archive(
        out_dir / R03_ARCHIVE_NAME,
        [
            r03_dir / "R03_BLIND_PACKET_MANIFEST.json",
            r03_dir / "r03_blind_review_items.jsonl",
            r03_dir / "official_table_metadata.jsonl",
            r03_tsv,
        ],
        out_dir,
    )
    manifest["r03_archive"] = archive
    write_json(r03_dir / "R03_BLIND_PACKET_MANIFEST.json", manifest)
    return manifest


def execute_review(
    r01_submission: Path,
    r02_submission: Path,
    setup_dir: Path = STAGE6C_SETUP_DIR,
    out_dir: Path = STAGE6C_EXECUTION_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    submissions = out_dir / "submissions"
    submissions.mkdir(parents=True, exist_ok=True)
    r01_target = submissions / "stage6c_gold_review_R01.submitted.tsv"
    r02_target = submissions / "stage6c_gold_review_R02.submitted.tsv"
    copy_submission(r01_submission, r01_target)
    copy_submission(r02_submission, r02_target)

    setup_lock = read_json(setup_dir / "STAGE6C_GOLD_REVIEW_SETUP_LOCK.json")
    protocol = read_json(setup_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json")
    review_items = read_jsonl(setup_dir / "artifacts" / "gold_review_items.jsonl")
    item_by_id = {row["stage6_sample_id"]: row for row in review_items}
    r01_template_fields, r01_template_rows = read_packet(setup_dir / "review_packets" / "stage6c_gold_review_R01.tsv")
    r02_template_fields, r02_template_rows = read_packet(setup_dir / "review_packets" / "stage6c_gold_review_R02.tsv")
    r01_fields, r01_rows = read_packet(r01_target)
    r02_fields, r02_rows = read_packet(r02_target)
    violations: list[str] = []
    if r01_template_fields != REVIEW_PACKET_COLUMNS or r02_template_fields != REVIEW_PACKET_COLUMNS:
        violations.append("template_columns_changed")
    violations.extend(validate_submission("R01", r01_rows, r01_fields, {row["stage6_sample_id"]: row for row in r01_template_rows}, item_by_id))
    violations.extend(validate_submission("R02", r02_rows, r02_fields, {row["stage6_sample_id"]: row for row in r02_template_rows}, item_by_id))
    if protocol.get("reviewer_isolation", {}).get("reviewer_roles_must_be_distinct") is not True:
        violations.append("reviewer_distinctness_not_locked")

    agreement = build_agreement(r01_rows, r02_rows) if not violations else {
        "agreed_approved_count": 0,
        "agreed_rejected_count": 0,
        "disagreement_count": 0,
        "agreed_approved_ids": [],
        "agreed_rejected_items": [],
        "disagreement_items": [],
    }
    write_json(out_dir / "R01_R02_AGREEMENT_REPORT.json", agreement)
    disagreement_ids = {row["stage6_sample_id"] for row in agreement["disagreement_items"]}
    r03_manifest = None if violations else build_r03_packet(
        out_dir,
        review_items,
        setup_dir / "artifacts" / "official_table_metadata.jsonl",
        disagreement_ids,
    )

    if violations:
        status = "FAIL_REVIEW_SUBMISSION_VALIDATION"
    elif agreement["disagreement_count"]:
        status = "PASS_PENDING_BLIND_R03_ADJUDICATION"
    elif agreement["agreed_rejected_count"]:
        status = "PASS_FINAL_REJECTED_BLOCKS_CONFIRMATION"
    else:
        status = "PASS_ALL_APPROVED_READY_FOR_FINAL_GOLD_FREEZE"

    execution_manifest = {
        "stage": "Stage6C_REVIEW_EXECUTION_AND_ADJUDICATION_PREP",
        "status": status,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": False,
        "stage6b_commit": STAGE6B_COMMIT,
        "stage6c_setup_commit": STAGE6C_SETUP_COMMIT,
        "setup_lock_sha256": sha256_file(setup_dir / "STAGE6C_GOLD_REVIEW_SETUP_LOCK.json"),
        "protocol_addendum_sha256": sha256_file(setup_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"),
        "gold_review_items_sha256": sha256_file(setup_dir / "artifacts" / "gold_review_items.jsonl"),
        "official_table_metadata_sha256": sha256_file(setup_dir / "artifacts" / "official_table_metadata.jsonl"),
        "submission_hashes": {
            "R01_submission_sha256": sha256_file(r01_target),
            "R02_submission_sha256": sha256_file(r02_target),
        },
        "submission_timestamps_recorded_from_source_files": {
            "R01_submission_source_mtime": r01_submission.stat().st_mtime,
            "R02_submission_source_mtime": r02_submission.stat().st_mtime,
        },
        "pseudonymous_reviewer_role_ids": ["R01", "R02"],
        "distinct_reviewer_assertion": "required_by_protocol_and_recorded_as_execution_condition",
        "reviewer_isolation_attestation": {
            "R01_R02_decisions_or_notes_shared_before_both_submitted": False,
            "cross_reviewer_discussion_before_submission": False,
            "model_predictions_visible_to_reviewers": False,
        },
        "validation_violations": violations,
        "agreement_report_sha256": sha256_file(out_dir / "R01_R02_AGREEMENT_REPORT.json"),
        "agreed_approved_count": agreement["agreed_approved_count"],
        "agreed_rejected_count": agreement["agreed_rejected_count"],
        "disagreement_count": agreement["disagreement_count"],
        "r03_blind_packet_created": r03_manifest is not None,
        "r03_blind_packet_manifest_sha256": (
            sha256_file(out_dir / "r03_blind_packet" / "R03_BLIND_PACKET_MANIFEST.json")
            if r03_manifest else None
        ),
        "next_step": (
            "send_blind_R03_packet_and_wait_for_adjudication"
            if r03_manifest else
            "resolve_final_rejected_items_before_confirmation"
            if agreement["agreed_rejected_count"] else
            "create_final_gold_freeze_package"
        ),
    }
    write_json(out_dir / "REVIEW_EXECUTION_MANIFEST.json", execution_manifest)
    validation_report = f"""# Stage 6C Review Execution Validation Report

Status: {status}

Validation date: 2026-08-24

- R01 rows: {len(r01_rows)}
- R02 rows: {len(r02_rows)}
- agreed approved: {agreement['agreed_approved_count']}
- agreed rejected: {agreement['agreed_rejected_count']}
- disagreements: {agreement['disagreement_count']}
- R03 blind packet created: {r03_manifest is not None}
- model_called: false
- gpu_called: false
- confirmation_run_allowed_now: false
- final_gold_freeze_created: false

Final gold freeze is not created until all items are resolved under the locked
protocol.
"""
    reviewer_readme = """# Stage 6C Review Execution and Blind Adjudication Prep

This package ingests the completed R01/R02 review TSV submissions, verifies
immutable fields and decisions, computes agreement, and prepares a blind R03
packet when disagreements exist.

It does not call a model, does not permit GPU preflight, and does not create a
final gold freeze while unresolved disagreement or final rejection remains.
"""
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme)

    archive_members = [
        out_dir / "REVIEW_EXECUTION_MANIFEST.json",
        out_dir / "R01_R02_AGREEMENT_REPORT.json",
        out_dir / "VALIDATION_REPORT.md",
        out_dir / "REVIEWER_README.md",
        r01_target,
        r02_target,
    ]
    if r03_manifest:
        archive_members.extend(
            [
                out_dir / "r03_blind_packet" / "R03_BLIND_PACKET_MANIFEST.json",
                out_dir / "r03_blind_packet" / "r03_blind_review_items.jsonl",
                out_dir / "r03_blind_packet" / "official_table_metadata.jsonl",
                out_dir / "r03_blind_packet" / "stage6c_gold_review_R03.tsv",
                out_dir / R03_ARCHIVE_NAME,
            ]
        )
    archive = make_archive(out_dir / ARCHIVE_NAME, archive_members, out_dir)
    execution_manifest["execution_archive"] = archive
    write_json(out_dir / "REVIEW_EXECUTION_MANIFEST.json", execution_manifest)
    return execution_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r01-submission", required=True)
    parser.add_argument("--r02-submission", required=True)
    parser.add_argument("--setup-dir", default=str(STAGE6C_SETUP_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6C_EXECUTION_DIR))
    args = parser.parse_args(argv)
    report = execute_review(
        Path(args.r01_submission),
        Path(args.r02_submission),
        Path(args.setup_dir),
        Path(args.out_dir),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report.get("validation_violations") else 1


if __name__ == "__main__":
    raise SystemExit(main())
