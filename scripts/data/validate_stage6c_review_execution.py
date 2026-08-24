#!/usr/bin/env python3
"""Validate Stage 6C review execution and blind R03 preparation artifacts."""

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

from scripts.data.create_stage6c_gold_review_setup import REVIEW_PACKET_COLUMNS, canonical_json  # noqa: E402
from scripts.data.execute_stage6c_gold_review import ARCHIVE_NAME, R03_ARCHIVE_NAME  # noqa: E402


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


def read_packet(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def validate_execution(execution_dir: Path, setup_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = execution_dir / "REVIEW_EXECUTION_MANIFEST.json"
    agreement_path = execution_dir / "R01_R02_AGREEMENT_REPORT.json"
    r01_path = execution_dir / "submissions" / "stage6c_gold_review_R01.submitted.tsv"
    r02_path = execution_dir / "submissions" / "stage6c_gold_review_R02.submitted.tsv"
    for path in [manifest_path, agreement_path, r01_path, r02_path]:
        if not path.is_file():
            violations.append(f"missing_file:{path.relative_to(execution_dir).as_posix()}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    manifest = read_json(manifest_path)
    agreement = read_json(agreement_path)
    review_items = read_jsonl(setup_dir / "artifacts" / "gold_review_items.jsonl")
    item_by_id = {row["stage6_sample_id"]: row for row in review_items}
    setup_r01_fields, setup_r01_rows = read_packet(setup_dir / "review_packets" / "stage6c_gold_review_R01.tsv")
    setup_r02_fields, setup_r02_rows = read_packet(setup_dir / "review_packets" / "stage6c_gold_review_R02.tsv")
    r01_fields, r01_rows = read_packet(r01_path)
    r02_fields, r02_rows = read_packet(r02_path)

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
    if manifest.get("submission_hashes", {}).get("R01_submission_sha256") != sha256_file(r01_path):
        violations.append("R01_submission_hash_mismatch")
    if manifest.get("submission_hashes", {}).get("R02_submission_sha256") != sha256_file(r02_path):
        violations.append("R02_submission_hash_mismatch")
    if manifest.get("agreement_report_sha256") != sha256_file(agreement_path):
        violations.append("agreement_report_hash_mismatch")
    if manifest.get("gold_review_items_sha256") != sha256_file(setup_dir / "artifacts" / "gold_review_items.jsonl"):
        violations.append("gold_review_items_hash_mismatch")

    template_by_role = {
        "R01": {row["stage6_sample_id"]: row for row in setup_r01_rows},
        "R02": {row["stage6_sample_id"]: row for row in setup_r02_rows},
    }
    for role, fields, rows in [("R01", r01_fields, r01_rows), ("R02", r02_fields, r02_rows)]:
        if fields != REVIEW_PACKET_COLUMNS:
            violations.append(f"{role}_unexpected_columns")
        if len(rows) != 500:
            violations.append(f"{role}_row_count_not_500")
        seen = set()
        for row in rows:
            sample_id = row.get("stage6_sample_id", "")
            seen.add(sample_id)
            template = template_by_role[role].get(sample_id)
            item = item_by_id.get(sample_id)
            if template is None or item is None:
                violations.append(f"{role}_unknown_sample_id:{sample_id}")
                continue
            for field in ["stage6_sample_id", "upstream_sample_locator", "authored_content_sha256", "reviewed_by"]:
                if row.get(field, "") != template.get(field, ""):
                    violations.append(f"{role}_immutable_field_changed:{sample_id}:{field}")
            if row.get("authored_content_sha256", "") != item.get("authored_content_sha256"):
                violations.append(f"{role}_content_hash_mismatch:{sample_id}")
            if row.get("decision") not in {"approved", "rejected"}:
                violations.append(f"{role}_invalid_decision:{sample_id}")
            if row.get("decision") == "rejected" and not row.get("notes", "").strip():
                violations.append(f"{role}_rejected_notes_blank:{sample_id}")
        if seen != set(item_by_id):
            violations.append(f"{role}_sample_id_set_mismatch")

    r01_by_id = {row["stage6_sample_id"]: row for row in r01_rows}
    r02_by_id = {row["stage6_sample_id"]: row for row in r02_rows}
    aa, rr, disagreements = [], [], []
    for sample_id in sorted(item_by_id):
        pair = (r01_by_id[sample_id]["decision"], r02_by_id[sample_id]["decision"])
        if pair == ("approved", "approved"):
            aa.append(sample_id)
        elif pair == ("rejected", "rejected"):
            rr.append(sample_id)
        else:
            disagreements.append(sample_id)
    if agreement.get("agreed_approved_count") != len(aa):
        violations.append("agreed_approved_count_mismatch")
    if agreement.get("agreed_rejected_count") != len(rr):
        violations.append("agreed_rejected_count_mismatch")
    if agreement.get("disagreement_count") != len(disagreements):
        violations.append("disagreement_count_mismatch")
    if manifest.get("agreed_approved_count") != len(aa):
        violations.append("manifest_agreed_approved_count_mismatch")
    if manifest.get("agreed_rejected_count") != len(rr):
        violations.append("manifest_agreed_rejected_count_mismatch")
    if manifest.get("disagreement_count") != len(disagreements):
        violations.append("manifest_disagreement_count_mismatch")

    if disagreements:
        if manifest.get("status") != "PASS_PENDING_BLIND_R03_ADJUDICATION":
            violations.append("status_not_pending_R03_despite_disagreements")
        r03_manifest_path = execution_dir / "r03_blind_packet" / "R03_BLIND_PACKET_MANIFEST.json"
        r03_items_path = execution_dir / "r03_blind_packet" / "r03_blind_review_items.jsonl"
        r03_tsv_path = execution_dir / "r03_blind_packet" / "stage6c_gold_review_R03.tsv"
        r03_archive_path = execution_dir / R03_ARCHIVE_NAME
        for path in [r03_manifest_path, r03_items_path, r03_tsv_path, r03_archive_path]:
            if not path.is_file():
                violations.append(f"missing_R03_artifact:{path.name}")
        if r03_manifest_path.is_file():
            r03_manifest = read_json(r03_manifest_path)
            if r03_manifest.get("contains_R01_R02_votes_or_notes") is not False:
                violations.append("R03_manifest_allows_votes_or_notes")
            for key in [
                "R03_must_not_see_R01_decision",
                "R03_must_not_see_R02_decision",
                "R03_must_not_see_R01_notes",
                "R03_must_not_see_R02_notes",
                "R03_must_not_see_model_predictions",
            ]:
                if r03_manifest.get(key) is not True:
                    violations.append(f"{key}_not_locked")
        if r03_items_path.is_file():
            r03_items = read_jsonl(r03_items_path)
            if {row["stage6_sample_id"] for row in r03_items} != set(disagreements):
                violations.append("R03_item_set_not_exact_disagreements")
            forbidden_keys = {"R01_decision", "R02_decision", "R01_notes", "R02_notes"}
            for item in r03_items:
                if forbidden_keys & set(item):
                    violations.append(f"R03_item_contains_vote_or_note:{item['stage6_sample_id']}")
        if r03_tsv_path.is_file():
            fields, r03_rows = read_packet(r03_tsv_path)
            if fields != REVIEW_PACKET_COLUMNS:
                violations.append("R03_unexpected_columns")
            for row in r03_rows:
                if row.get("reviewed_by") != "R03":
                    violations.append(f"R03_reviewed_by_mismatch:{row.get('stage6_sample_id')}")
                if row.get("decision") or row.get("notes"):
                    violations.append(f"R03_prefilled_decision_or_notes:{row.get('stage6_sample_id')}")
        if r03_archive_path.is_file():
            try:
                with zipfile.ZipFile(r03_archive_path) as archive:
                    bad_member = archive.testzip()
                    names = set(archive.namelist())
                if bad_member:
                    violations.append(f"R03_archive_bad_member:{bad_member}")
                for forbidden in ["R01", "R02", "submitted"]:
                    if any(forbidden in name and "R03" not in name for name in names):
                        violations.append(f"R03_archive_contains_forbidden_name:{forbidden}")
            except zipfile.BadZipFile:
                violations.append("R03_archive_not_openable")
    elif rr:
        if manifest.get("status") != "PASS_FINAL_REJECTED_BLOCKS_CONFIRMATION":
            violations.append("status_not_final_rejected_despite_no_disagreement")
    else:
        if manifest.get("status") != "PASS_ALL_APPROVED_READY_FOR_FINAL_GOLD_FREEZE":
            violations.append("status_not_all_approved_ready")

    archive_info = manifest.get("execution_archive") or {}
    archive_path = execution_dir / archive_info.get("path", ARCHIVE_NAME)
    if not archive_path.is_file():
        violations.append("execution_archive_missing")
    else:
        if archive_info.get("sha256") != sha256_file(archive_path):
            violations.append("execution_archive_hash_mismatch")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip():
                    violations.append("execution_archive_testzip_failed")
        except zipfile.BadZipFile:
            violations.append("execution_archive_not_openable")

    return {
        "status": "FAIL" if violations else "PASS",
        "violations": violations,
        "stage": "Stage6C_REVIEW_EXECUTION_AND_ADJUDICATION_PREP",
        "agreed_approved_count": len(aa),
        "agreed_rejected_count": len(rr),
        "disagreement_count": len(disagreements),
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "final_gold_freeze_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-dir", default="stage6_gold_review_execution")
    parser.add_argument("--setup-dir", default="stage6_gold_review_setup")
    args = parser.parse_args(argv)
    report = validate_execution(Path(args.execution_dir), Path(args.setup_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
