from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from scripts.data.validate_stage6d_corrected_review_execution import (
    validate_stage6d_corrected_review_execution,
)


ROOT = Path(__file__).resolve().parents[1]
EXECUTION_DIR = ROOT / "stage6_corrected_gold_review_execution"
SETUP_DIR = ROOT / "stage6_corrected_gold_review_setup"


def copy_execution(tmp_path: Path) -> tuple[Path, Path]:
    execution = tmp_path / "stage6_corrected_gold_review_execution"
    setup = tmp_path / "stage6_corrected_gold_review_setup"
    shutil.copytree(EXECUTION_DIR, execution)
    shutil.copytree(SETUP_DIR, setup)
    return execution, setup


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), list(reader)


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def mutate_manifest(execution: Path, key: str, value) -> None:
    path = execution / "CORRECTED_REVIEW_EXECUTION_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if value is None:
        manifest.pop(key, None)
    else:
        manifest[key] = value
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mutate_attestation(execution: Path, key: str, value: bool) -> None:
    path = execution / "CORRECTED_REVIEW_EXECUTION_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["reviewer_isolation_attestation"][key] = value
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_stage6d_corrected_review_execution_validator_passes_repo_artifact() -> None:
    report = validate_stage6d_corrected_review_execution(EXECUTION_DIR, SETUP_DIR)

    assert report["status"] == "PASS"
    assert report["corrected_item_count"] == 21
    assert report["agreed_approved_count"] == 21
    assert report["agreed_rejected_count"] == 0
    assert report["disagreement_count"] == 0
    assert report["C03_required"] is False
    assert report["C03_packet_created"] is False
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["final_gold_freeze_created"] is False


def test_stage6d_corrected_review_execution_rejects_immutable_hash_mutation(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    path = execution / "submissions" / "stage6d_corrected_gold_review_C01.submitted.tsv"
    fields, rows = read_tsv(path)
    rows[0]["corrected_authored_content_sha256"] = "0" * 64
    write_tsv(path, fields, rows)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert any(item.startswith("C01_immutable_field_changed:") for item in report["violations"])


def test_stage6d_corrected_review_execution_rejects_blank_decision(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    path = execution / "submissions" / "stage6d_corrected_gold_review_C01.submitted.tsv"
    fields, rows = read_tsv(path)
    rows[0]["decision"] = ""
    write_tsv(path, fields, rows)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert any(item.startswith("C01_invalid_or_blank_decision:") for item in report["violations"])


def test_stage6d_corrected_review_execution_rejects_rejected_without_notes(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    path = execution / "submissions" / "stage6d_corrected_gold_review_C01.submitted.tsv"
    fields, rows = read_tsv(path)
    rows[0]["decision"] = "rejected"
    rows[0]["notes"] = ""
    write_tsv(path, fields, rows)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert any(item.startswith("C01_rejected_notes_blank:") for item in report["violations"])


def test_stage6d_corrected_review_execution_rejects_stale_agreement_report(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    path = execution / "C01_C02_CORRECTED_AGREEMENT_REPORT.json"
    report_json = json.loads(path.read_text(encoding="utf-8"))
    report_json["agreed_approved_count"] = 20
    path.write_text(json.dumps(report_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "agreement_report_not_reproducible_from_submissions" in report["violations"]


def test_stage6d_corrected_review_execution_requires_distinct_reviewer_assertion(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    mutate_manifest(execution, "distinct_reviewer_assertion", None)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "manifest_distinct_reviewer_assertion_mismatch" in report["violations"]


def test_stage6d_corrected_review_execution_rejects_shared_decisions_attestation(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    mutate_attestation(execution, "C01_C02_decisions_or_notes_shared_before_both_submitted", True)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "manifest_reviewer_isolation_attestation_mismatch" in report["violations"]


def test_stage6d_corrected_review_execution_rejects_cross_reviewer_discussion_attestation(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    mutate_attestation(execution, "cross_reviewer_discussion_before_submission", True)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "manifest_reviewer_isolation_attestation_mismatch" in report["violations"]


def test_stage6d_corrected_review_execution_rejects_model_visibility_attestation(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    mutate_attestation(execution, "model_predictions_visible_to_reviewers", True)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "manifest_reviewer_isolation_attestation_mismatch" in report["violations"]


def test_stage6d_corrected_review_execution_rejects_c01_as_r04_attestation(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    mutate_attestation(execution, "C01_is_R04", True)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "manifest_reviewer_isolation_attestation_mismatch" in report["violations"]


def test_stage6d_corrected_review_execution_rejects_c02_as_r04_attestation(tmp_path: Path) -> None:
    execution, setup = copy_execution(tmp_path)
    mutate_attestation(execution, "C02_is_R04", True)

    report = validate_stage6d_corrected_review_execution(execution, setup)

    assert report["status"] == "FAIL"
    assert "manifest_reviewer_isolation_attestation_mismatch" in report["violations"]
