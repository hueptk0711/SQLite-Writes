#!/usr/bin/env python3
"""Validate Stage 6C R04 final-rejection technical resolution artifacts."""

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

from scripts.data.ingest_stage6c_r03_adjudication import R04_AFTER_R03_COLUMNS
from scripts.data.ingest_stage6c_r04_resolution import ALLOWED_CLASSES, ARCHIVE_NAME


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_packet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def validate_r04_resolution(resolution_dir: Path, r03_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = resolution_dir / "R04_INGEST_MANIFEST.json"
    report_path = resolution_dir / "R04_RESOLUTION_REPORT.json"
    correctable_path = resolution_dir / "CORRECTABLE_GOLD_ERROR_QUEUE.json"
    source_invalid_path = resolution_dir / "SOURCE_TASK_INVALID_QUEUE.json"
    r04_path = resolution_dir / "submissions" / "stage6c_final_rejection_after_R03_R04.submitted.tsv"
    template_path = r03_dir / "r04_after_r03_resolution_packet" / "stage6c_final_rejection_after_R03_R04.tsv"
    for path in [manifest_path, report_path, correctable_path, source_invalid_path, r04_path, template_path]:
        if not path.is_file():
            violations.append(f"missing_file:{path.name}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    manifest = read_json(manifest_path)
    report = read_json(report_path)
    correctable_queue = read_json(correctable_path)
    source_invalid_queue = read_json(source_invalid_path)
    template_fields, template_rows = read_packet(template_path)
    r04_fields, r04_rows = read_packet(r04_path)
    template_by_id = {row["stage6_sample_id"]: row for row in template_rows}

    for label, value in {
        "model_called": manifest.get("model_called"),
        "gpu_called": manifest.get("gpu_called"),
        "confirmation_run_allowed_now": manifest.get("confirmation_run_allowed_now"),
        "final_gold_freeze_created": manifest.get("final_gold_freeze_created"),
    }.items():
        if value is not False:
            violations.append(f"{label}_not_false")
    if manifest.get("validation_violations"):
        violations.append("manifest_contains_validation_violations")
    if manifest.get("status") != "PASS_PENDING_CORRECTION_AND_REGISTRATION_REVISION_WORKFLOWS":
        violations.append("manifest_status_not_pending_downstream_workflows")
    if template_fields != R04_AFTER_R03_COLUMNS or r04_fields != R04_AFTER_R03_COLUMNS:
        violations.append("R04_columns_changed")
    if len(r04_rows) != 40:
        violations.append("R04_row_count_not_40")

    correctable: list[dict[str, Any]] = []
    source_invalid: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in r04_rows:
        sample_id = row.get("stage6_sample_id", "")
        seen_ids.add(sample_id)
        template = template_by_id.get(sample_id)
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
        if classification not in ALLOWED_CLASSES:
            violations.append(f"R04_invalid_or_blank_classification:{sample_id}")
        if not rationale.strip():
            violations.append(f"R04_blank_rationale:{sample_id}")
        base = {
            "stage6_sample_id": sample_id,
            "upstream_sample_locator": row.get("upstream_sample_locator", ""),
            "authored_content_sha256": row.get("authored_content_sha256", ""),
            "final_rejection_source": row.get("final_rejection_source", ""),
            "rationale": rationale,
        }
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
                    base["correction_spec"] = parsed
            correctable.append(base)
        elif classification == "SOURCE_TASK_INVALID":
            if correction_spec.strip():
                violations.append(f"R04_source_invalid_has_correction_spec:{sample_id}")
            source_invalid.append(base)
    if seen_ids != set(template_by_id):
        violations.append("R04_sample_id_set_mismatch")

    correctable = sorted(correctable, key=lambda item: item["stage6_sample_id"])
    source_invalid = sorted(source_invalid, key=lambda item: item["stage6_sample_id"])
    if report.get("final_rejected_count") != 40:
        violations.append("report_final_rejected_count_changed")
    if report.get("correctable_gold_error_count") != len(correctable):
        violations.append("report_correctable_count_mismatch")
    if report.get("source_task_invalid_count") != len(source_invalid):
        violations.append("report_source_invalid_count_mismatch")
    if report.get("correctable_gold_error_items") != correctable:
        violations.append("report_correctable_items_mismatch")
    if report.get("source_task_invalid_items") != source_invalid:
        violations.append("report_source_invalid_items_mismatch")
    if correctable_queue.get("count") != len(correctable) or correctable_queue.get("items") != correctable:
        violations.append("correctable_queue_mismatch")
    if source_invalid_queue.get("count") != len(source_invalid) or source_invalid_queue.get("items") != source_invalid:
        violations.append("source_invalid_queue_mismatch")
    if correctable_queue.get("status") != "LOCKED_PENDING_DETERMINISTIC_CORRECTION_AND_RE_REVIEW":
        violations.append("correctable_queue_status_changed")
    if source_invalid_queue.get("status") != "LOCKED_PENDING_STAGE6_REGISTRATION_REVISION":
        violations.append("source_invalid_queue_status_changed")
    if manifest.get("r04_submission_sha256") != sha256_file(r04_path):
        violations.append("manifest_R04_submission_hash_mismatch")
    if manifest.get("resolution_report_sha256") != sha256_file(report_path):
        violations.append("manifest_resolution_report_hash_mismatch")
    if manifest.get("correctable_queue_sha256") != sha256_file(correctable_path):
        violations.append("manifest_correctable_queue_hash_mismatch")
    if manifest.get("source_invalid_queue_sha256") != sha256_file(source_invalid_path):
        violations.append("manifest_source_invalid_queue_hash_mismatch")
    if manifest.get("correctable_gold_error_count") != len(correctable):
        violations.append("manifest_correctable_count_mismatch")
    if manifest.get("source_task_invalid_count") != len(source_invalid):
        violations.append("manifest_source_invalid_count_mismatch")
    if manifest.get("final_rejected_count") != 40:
        violations.append("manifest_final_rejected_count_changed")

    archive_info = manifest.get("archive") or {}
    archive_path = resolution_dir / archive_info.get("path", ARCHIVE_NAME)
    if not archive_path.is_file():
        violations.append("r04_resolution_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("r04_resolution_archive_hash_mismatch")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip():
                    violations.append("r04_resolution_archive_testzip_failed")
        except zipfile.BadZipFile:
            violations.append("r04_resolution_archive_not_openable")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6C_R04_RESOLUTION_INGEST",
        "final_rejected_count": 40,
        "correctable_gold_error_count": len(correctable),
        "source_task_invalid_count": len(source_invalid),
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "final_gold_freeze_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution-dir", default="stage6_gold_review_r04_resolution")
    parser.add_argument("--r03-dir", default="stage6_gold_review_r03_adjudication")
    args = parser.parse_args(argv)
    report = validate_r04_resolution(Path(args.resolution_dir), Path(args.r03_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
