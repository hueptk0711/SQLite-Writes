#!/usr/bin/env python3
"""Ingest Stage 6C R04 final-rejection technical resolution."""

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
from scripts.data.ingest_stage6c_r03_adjudication import (
    R04_AFTER_R03_COLUMNS,
    STAGE6C_EXECUTION_PATCH2_COMMIT,
)


STAGE6C_R03_DIR = PROJECT_ROOT / "stage6_gold_review_r03_adjudication"
STAGE6C_R04_DIR = PROJECT_ROOT / "stage6_gold_review_r04_resolution"
STAGE6C_R03_PATCH1_COMMIT = "343049339e3f972db613b95662f835d679cc04e5"
ARCHIVE_NAME = "stage6c_r04_resolution_artifacts_20260824.zip"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)
ALLOWED_CLASSES = ["CORRECTABLE_GOLD_ERROR", "SOURCE_TASK_INVALID"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def validate_r04_rows(
    fields: list[str],
    rows: list[dict[str, str]],
    template_rows: dict[str, dict[str, str]],
) -> list[str]:
    violations: list[str] = []
    if fields != R04_AFTER_R03_COLUMNS:
        violations.append("R04_unexpected_columns")
    if len(rows) != 40:
        violations.append("R04_row_count_not_40")
    seen_ids: set[str] = set()
    for row in rows:
        sample_id = row.get("stage6_sample_id", "")
        if sample_id in seen_ids:
            violations.append(f"R04_duplicate_sample_id:{sample_id}")
        seen_ids.add(sample_id)
        template = template_rows.get(sample_id)
        if template is None:
            violations.append(f"R04_unknown_sample_id:{sample_id}")
            continue
        for field in [
            "stage6_sample_id",
            "upstream_sample_locator",
            "authored_content_sha256",
            "final_rejection_source",
            "R01_notes_sha256",
            "R02_notes_sha256",
            "R03_notes_sha256",
            "reviewed_by",
        ]:
            if row.get(field, "") != template.get(field, ""):
                violations.append(f"R04_immutable_field_changed:{sample_id}:{field}")
        classification = row.get("classification", "")
        rationale = row.get("rationale", "")
        correction_spec = row.get("correction_spec", "")
        if classification not in set(ALLOWED_CLASSES):
            violations.append(f"R04_invalid_or_blank_classification:{sample_id}")
        if not rationale.strip():
            violations.append(f"R04_blank_rationale:{sample_id}")
        if classification == "CORRECTABLE_GOLD_ERROR":
            if not correction_spec.strip():
                violations.append(f"R04_correctable_missing_correction_spec:{sample_id}")
            else:
                try:
                    parsed = json.loads(correction_spec)
                except json.JSONDecodeError:
                    violations.append(f"R04_correctable_correction_spec_not_json:{sample_id}")
                else:
                    if not isinstance(parsed, dict):
                        violations.append(f"R04_correctable_correction_spec_not_object:{sample_id}")
        if classification == "SOURCE_TASK_INVALID" and correction_spec.strip():
            violations.append(f"R04_source_invalid_has_correction_spec:{sample_id}")
    if seen_ids != set(template_rows):
        violations.append("R04_sample_id_set_mismatch")
    return violations


def build_resolution(rows: list[dict[str, str]]) -> dict[str, Any]:
    correctable: list[dict[str, Any]] = []
    source_invalid: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item["stage6_sample_id"]):
        item = {
            "stage6_sample_id": row["stage6_sample_id"],
            "upstream_sample_locator": row["upstream_sample_locator"],
            "authored_content_sha256": row["authored_content_sha256"],
            "final_rejection_source": row["final_rejection_source"],
            "rationale": row["rationale"],
        }
        if row["classification"] == "CORRECTABLE_GOLD_ERROR":
            item["correction_spec"] = json.loads(row["correction_spec"])
            correctable.append(item)
        else:
            source_invalid.append(item)
    return {
        "stage": "Stage6C_R04_FINAL_REJECTION_TECHNICAL_RESOLUTION",
        "status": "PASS_PENDING_CORRECTION_AND_REGISTRATION_REVISION_WORKFLOWS",
        "final_rejected_count": len(rows),
        "correctable_gold_error_count": len(correctable),
        "source_task_invalid_count": len(source_invalid),
        "correctable_gold_error_items": correctable,
        "source_task_invalid_items": source_invalid,
    }


def ingest_r04(
    r04_submission: Path,
    r03_dir: Path = STAGE6C_R03_DIR,
    out_dir: Path = STAGE6C_R04_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    submissions = out_dir / "submissions"
    r04_target = submissions / "stage6c_final_rejection_after_R03_R04.submitted.tsv"
    canonicalize_submission(r04_submission, r04_target)

    template_path = r03_dir / "r04_after_r03_resolution_packet" / "stage6c_final_rejection_after_R03_R04.tsv"
    template_fields, template_rows_list = read_packet(template_path)
    r04_fields, r04_rows = read_packet(r04_target)
    template_by_id = {row["stage6_sample_id"]: row for row in template_rows_list}
    violations: list[str] = []
    if template_fields != R04_AFTER_R03_COLUMNS:
        violations.append("R04_template_columns_changed")
    violations.extend(validate_r04_rows(r04_fields, r04_rows, template_by_id))

    resolution = build_resolution(r04_rows) if not violations else {
        "stage": "Stage6C_R04_FINAL_REJECTION_TECHNICAL_RESOLUTION",
        "status": "FAIL_R04_SUBMISSION_VALIDATION",
        "final_rejected_count": 0,
        "correctable_gold_error_count": 0,
        "source_task_invalid_count": 0,
        "correctable_gold_error_items": [],
        "source_task_invalid_items": [],
    }
    write_json(out_dir / "R04_RESOLUTION_REPORT.json", resolution)
    write_json(out_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json", {
        "stage": "Stage6C_CORRECTABLE_GOLD_ERROR_QUEUE",
        "status": "LOCKED_PENDING_DETERMINISTIC_CORRECTION_AND_RE_REVIEW",
        "count": resolution["correctable_gold_error_count"],
        "items": resolution["correctable_gold_error_items"],
        "required_next_action": [
            "deterministically correct registered gold artifacts before model run",
            "recompute gold plan, program, expected inserted row, post-state hash, and authored_content hash",
            "create corrected-item review packet",
            "obtain two independent re-reviews",
        ],
    })
    write_json(out_dir / "SOURCE_TASK_INVALID_QUEUE.json", {
        "stage": "Stage6C_SOURCE_TASK_INVALID_QUEUE",
        "status": "LOCKED_PENDING_STAGE6_REGISTRATION_REVISION",
        "count": resolution["source_task_invalid_count"],
        "items": resolution["source_task_invalid_items"],
        "required_next_action": [
            "revise Stage6 registration before model run",
            "do not silently drop samples",
            "do not replace with train/dev or newly selected samples",
            "record item-level rationale and updated hashes",
        ],
    })
    status = "FAIL_R04_SUBMISSION_VALIDATION" if violations else resolution["status"]
    manifest = {
        "stage": "Stage6C_R04_RESOLUTION_INGEST",
        "status": status,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "final_gold_freeze_created": False,
        "stage6c_execution_patch2_commit": STAGE6C_EXECUTION_PATCH2_COMMIT,
        "stage6c_r03_patch1_commit": STAGE6C_R03_PATCH1_COMMIT,
        "r04_submission_sha256": sha256_file(r04_target),
        "r04_source_mtime": r04_submission.stat().st_mtime,
        "validation_violations": violations,
        "final_rejected_count": resolution["final_rejected_count"],
        "correctable_gold_error_count": resolution["correctable_gold_error_count"],
        "source_task_invalid_count": resolution["source_task_invalid_count"],
        "resolution_report_sha256": sha256_file(out_dir / "R04_RESOLUTION_REPORT.json"),
        "correctable_queue_sha256": sha256_file(out_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json"),
        "source_invalid_queue_sha256": sha256_file(out_dir / "SOURCE_TASK_INVALID_QUEUE.json"),
        "next_steps": [
            "review_R04_resolution_package",
            "deterministic_correction_package_for_correctable_gold_errors",
            "Stage6_registration_revision_for_source_task_invalid_items",
            "do_not_create_final_gold_freeze_until_all_corrected_items_are_re_reviewed_and_registration_revision_is_accepted",
        ],
    }
    write_json(out_dir / "R04_INGEST_MANIFEST.json", manifest)
    validation_report = f"""# Stage 6C R04 Resolution Validation Report

Status: {status}

Validation date: 2026-08-24

- R04 rows: {len(r04_rows)}
- final rejected items: {resolution['final_rejected_count']}
- correctable gold errors: {resolution['correctable_gold_error_count']}
- source task invalid: {resolution['source_task_invalid_count']}
- model_called: false
- gpu_called: false
- confirmation_run_allowed_now: false
- final_gold_freeze_created: false
"""
    reviewer_readme = """# Stage 6C R04 Final-Rejection Resolution Ingest

This package ingests the completed R04 technical classification TSV for the 40
final-rejected items after R03 adjudication.

It records which items are correctable gold errors and which items require a
Stage6 registration revision. It does not correct gold artifacts, drop samples,
run a model, permit GPU preflight, or create a final gold freeze.
"""
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme)
    archive = make_archive(
        out_dir / ARCHIVE_NAME,
        [
            out_dir / "R04_INGEST_MANIFEST.json",
            out_dir / "R04_RESOLUTION_REPORT.json",
            out_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json",
            out_dir / "SOURCE_TASK_INVALID_QUEUE.json",
            out_dir / "VALIDATION_REPORT.md",
            out_dir / "REVIEWER_README.md",
            r04_target,
        ],
        out_dir,
    )
    manifest["archive"] = archive
    write_json(out_dir / "R04_INGEST_MANIFEST.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r04-submission", required=True)
    parser.add_argument("--r03-dir", default=str(STAGE6C_R03_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6C_R04_DIR))
    args = parser.parse_args(argv)
    report = ingest_r04(Path(args.r04_submission), Path(args.r03_dir), Path(args.out_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report.get("validation_violations") else 1


if __name__ == "__main__":
    raise SystemExit(main())
