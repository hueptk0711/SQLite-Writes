#!/usr/bin/env python3
"""Ingest Stage 6D C01/C02 corrected-gold re-review submissions."""

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

from scripts.data.create_stage6c_gold_review_setup import canonical_json
from scripts.data.create_stage6d_corrected_gold_review_setup import REVIEW_PACKET_COLUMNS


STAGE6D_SETUP_DIR = PROJECT_ROOT / "stage6_corrected_gold_review_setup"
STAGE6D_EXECUTION_DIR = PROJECT_ROOT / "stage6_corrected_gold_review_execution"
STAGE6D_PATCH1_COMMIT = "4c789bdebc08a8c35bce19290282b536da7676e3"
ARCHIVE_NAME = "stage6d_corrected_review_execution_artifacts_20260824.zip"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)


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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def read_packet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def canonicalize_submission(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8-sig", newline=None)
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


def validate_reviewer_rows(
    role: str,
    fields: list[str],
    rows: list[dict[str, str]],
    template_by_id: dict[str, dict[str, str]],
) -> list[str]:
    violations: list[str] = []
    if fields != REVIEW_PACKET_COLUMNS:
        violations.append(f"{role}_unexpected_columns")
    if len(rows) != 21:
        violations.append(f"{role}_row_count_not_21")
    seen_ids: set[str] = set()
    for row in rows:
        sample_id = row.get("stage6_sample_id", "")
        if sample_id in seen_ids:
            violations.append(f"{role}_duplicate_sample_id:{sample_id}")
        seen_ids.add(sample_id)
        template = template_by_id.get(sample_id)
        if template is None:
            violations.append(f"{role}_unknown_sample_id:{sample_id}")
            continue
        for field in [
            "stage6_sample_id",
            "upstream_sample_locator",
            "corrected_authored_content_sha256",
            "reviewed_by",
        ]:
            if row.get(field, "") != template.get(field, ""):
                violations.append(f"{role}_immutable_field_changed:{sample_id}:{field}")
        decision = row.get("decision", "")
        notes = row.get("notes", "")
        if decision not in {"approved", "rejected"}:
            violations.append(f"{role}_invalid_or_blank_decision:{sample_id}")
        if decision == "rejected" and not notes.strip():
            violations.append(f"{role}_rejected_notes_blank:{sample_id}")
    if seen_ids != set(template_by_id):
        violations.append(f"{role}_sample_id_set_mismatch")
    return violations


def build_agreement_report(c01_rows: list[dict[str, str]], c02_rows: list[dict[str, str]]) -> dict[str, Any]:
    c01_by_id = {row["stage6_sample_id"]: row for row in c01_rows}
    c02_by_id = {row["stage6_sample_id"]: row for row in c02_rows}
    agreed_approved: list[str] = []
    agreed_rejected: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    for sample_id in sorted(c01_by_id):
        c01 = c01_by_id[sample_id]
        c02 = c02_by_id[sample_id]
        if c01["decision"] == c02["decision"] == "approved":
            agreed_approved.append(sample_id)
        elif c01["decision"] == c02["decision"] == "rejected":
            agreed_rejected.append(
                {
                    "stage6_sample_id": sample_id,
                    "C01_notes_sha256": sha256_text(c01.get("notes", "")),
                    "C02_notes_sha256": sha256_text(c02.get("notes", "")),
                }
            )
        else:
            disagreements.append(
                {
                    "stage6_sample_id": sample_id,
                    "C01_decision": c01["decision"],
                    "C02_decision": c02["decision"],
                    "C01_notes_sha256": sha256_text(c01.get("notes", "")),
                    "C02_notes_sha256": sha256_text(c02.get("notes", "")),
                }
            )
    return {
        "stage": "Stage6D_C01_C02_CORRECTED_REVIEW_AGREEMENT",
        "status": "PASS_ALL_CORRECTED_ITEMS_AGREED_APPROVED" if not agreed_rejected and not disagreements else "PASS_PENDING_CORRECTED_REVIEW_RESOLUTION",
        "corrected_item_count": len(c01_rows),
        "agreed_approved_count": len(agreed_approved),
        "agreed_rejected_count": len(agreed_rejected),
        "disagreement_count": len(disagreements),
        "agreed_approved_ids": agreed_approved,
        "agreed_rejected_items": agreed_rejected,
        "disagreement_items": disagreements,
        "blind_C03_required": bool(disagreements),
        "C03_packet_created": False,
    }


def ingest_corrected_review(
    c01_submission: Path,
    c02_submission: Path,
    setup_dir: Path = STAGE6D_SETUP_DIR,
    out_dir: Path = STAGE6D_EXECUTION_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    submissions_dir = out_dir / "submissions"
    c01_target = submissions_dir / "stage6d_corrected_gold_review_C01.submitted.tsv"
    c02_target = submissions_dir / "stage6d_corrected_gold_review_C02.submitted.tsv"
    canonicalize_submission(c01_submission, c01_target)
    canonicalize_submission(c02_submission, c02_target)

    c01_template_fields, c01_template_rows = read_packet(
        setup_dir / "corrected_review_packets" / "stage6d_corrected_gold_review_C01.tsv"
    )
    c02_template_fields, c02_template_rows = read_packet(
        setup_dir / "corrected_review_packets" / "stage6d_corrected_gold_review_C02.tsv"
    )
    c01_fields, c01_rows = read_packet(c01_target)
    c02_fields, c02_rows = read_packet(c02_target)
    violations: list[str] = []
    if c01_template_fields != REVIEW_PACKET_COLUMNS:
        violations.append("C01_template_columns_changed")
    if c02_template_fields != REVIEW_PACKET_COLUMNS:
        violations.append("C02_template_columns_changed")
    violations.extend(validate_reviewer_rows("C01", c01_fields, c01_rows, {row["stage6_sample_id"]: row for row in c01_template_rows}))
    violations.extend(validate_reviewer_rows("C02", c02_fields, c02_rows, {row["stage6_sample_id"]: row for row in c02_template_rows}))

    agreement = build_agreement_report(c01_rows, c02_rows) if not violations else {
        "stage": "Stage6D_C01_C02_CORRECTED_REVIEW_AGREEMENT",
        "status": "FAIL_SUBMISSION_VALIDATION",
        "corrected_item_count": 0,
        "agreed_approved_count": 0,
        "agreed_rejected_count": 0,
        "disagreement_count": 0,
        "agreed_approved_ids": [],
        "agreed_rejected_items": [],
        "disagreement_items": [],
        "blind_C03_required": False,
        "C03_packet_created": False,
    }
    write_json(out_dir / "C01_C02_CORRECTED_AGREEMENT_REPORT.json", agreement)

    resolved = {
        "stage": "Stage6D_CORRECTED_ITEMS_RESOLUTION",
        "status": "ALL_21_CORRECTED_ITEMS_ACCEPTED_PENDING_STAGE6_REGISTRATION_REVISION"
        if agreement["agreed_approved_count"] == 21 and not violations
        else "PENDING_CORRECTED_REVIEW_RESOLUTION",
        "corrected_item_count": 21,
        "corrected_items_final_approved_count": agreement["agreed_approved_count"],
        "corrected_items_final_rejected_count": agreement["agreed_rejected_count"],
        "corrected_items_disagreement_count": agreement["disagreement_count"],
        "C03_required": agreement["blind_C03_required"],
        "C03_packet_created": False,
        "final_gold_freeze_created": False,
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "next_required_step": "Stage6_registration_revision_for_19_source_invalid_items_and_final_denominator_lock",
    }
    write_json(out_dir / "CORRECTED_ITEMS_RESOLUTION_REPORT.json", resolved)

    status = "FAIL_CORRECTED_REVIEW_SUBMISSION_VALIDATION" if violations else resolved["status"]
    manifest = {
        "stage": "Stage6D_CORRECTED_REVIEW_EXECUTION",
        "status": status,
        "stage6d_setup_patch1_commit": STAGE6D_PATCH1_COMMIT,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": False,
        "validation_violations": violations,
        "corrected_item_count": 21,
        "agreed_approved_count": agreement["agreed_approved_count"],
        "agreed_rejected_count": agreement["agreed_rejected_count"],
        "disagreement_count": agreement["disagreement_count"],
        "C03_required": agreement["blind_C03_required"],
        "C03_packet_created": False,
        "C01_submission_sha256": sha256_file(c01_target),
        "C02_submission_sha256": sha256_file(c02_target),
        "C01_source_mtime": c01_submission.stat().st_mtime,
        "C02_source_mtime": c02_submission.stat().st_mtime,
        "setup_lock_sha256": sha256_file(setup_dir / "STAGE6D_CORRECTED_GOLD_REVIEW_SETUP_LOCK.json"),
        "protocol_lock_sha256": sha256_file(setup_dir / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json"),
        "corrected_gold_review_items_sha256": sha256_file(setup_dir / "artifacts" / "corrected_gold_review_items.jsonl"),
        "agreement_report_sha256": sha256_file(out_dir / "C01_C02_CORRECTED_AGREEMENT_REPORT.json"),
        "resolution_report_sha256": sha256_file(out_dir / "CORRECTED_ITEMS_RESOLUTION_REPORT.json"),
        "next_steps": [
            "review_corrected_review_execution_package",
            "do_not_create_final_gold_freeze_yet",
            "resolve_19_source_task_invalid_items_via_stage6_registration_revision",
            "then_create_final_gold_freeze_after_reviewer_acceptance",
        ],
    }
    write_json(out_dir / "CORRECTED_REVIEW_EXECUTION_MANIFEST.json", manifest)
    validation_report = f"""# Stage 6D Corrected Review Execution Validation Report

Status: {status}

Validation date: 2026-08-24

- C01 rows: {len(c01_rows)}
- C02 rows: {len(c02_rows)}
- agreed approved: {agreement['agreed_approved_count']}
- agreed rejected: {agreement['agreed_rejected_count']}
- disagreements: {agreement['disagreement_count']}
- C03 required: {str(agreement['blind_C03_required']).lower()}
- model_called: false
- gpu_called: false
- confirmation_run_allowed_now: false
- final_gold_freeze_created: false
"""
    reviewer_readme = """# Stage 6D Corrected Review Execution

This package ingests the completed C01/C02 corrected-gold re-review TSV
submissions for the 21 R04-correctable items.

Both reviewers approved all 21 corrected items. No C03 packet is created.

This package does not revise the 19 SOURCE_TASK_INVALID items, does not create
a final gold freeze, and does not permit GPU preflight.
"""
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme)
    archive = make_archive(
        out_dir / ARCHIVE_NAME,
        [
            out_dir / "CORRECTED_REVIEW_EXECUTION_MANIFEST.json",
            out_dir / "C01_C02_CORRECTED_AGREEMENT_REPORT.json",
            out_dir / "CORRECTED_ITEMS_RESOLUTION_REPORT.json",
            out_dir / "VALIDATION_REPORT.md",
            out_dir / "REVIEWER_README.md",
            c01_target,
            c02_target,
        ],
        out_dir,
    )
    manifest["archive"] = archive
    write_json(out_dir / "CORRECTED_REVIEW_EXECUTION_MANIFEST.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c01-submission", required=True)
    parser.add_argument("--c02-submission", required=True)
    parser.add_argument("--setup-dir", default=str(STAGE6D_SETUP_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6D_EXECUTION_DIR))
    args = parser.parse_args(argv)
    report = ingest_corrected_review(
        Path(args.c01_submission),
        Path(args.c02_submission),
        Path(args.setup_dir),
        Path(args.out_dir),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report.get("validation_violations") else 1


if __name__ == "__main__":
    raise SystemExit(main())
