from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path

from scripts.data.validate_stage6c_review_execution import validate_execution


ROOT = Path(__file__).resolve().parents[1]
EXECUTION_DIR = ROOT / "stage6_gold_review_execution"
SETUP_DIR = ROOT / "stage6_gold_review_setup"


def copy_execution(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_gold_review_execution"
    shutil.copytree(EXECUTION_DIR, target)
    return target


def test_stage6c_review_execution_validator_passes_repo_artifact() -> None:
    report = validate_execution(EXECUTION_DIR, SETUP_DIR)
    manifest = json.loads(
        (EXECUTION_DIR / "REVIEW_EXECUTION_MANIFEST.json").read_text(encoding="utf-8")
    )
    rejection_lock = json.loads(
        (EXECUTION_DIR / "FINAL_REJECTION_RESOLUTION_LOCK.json").read_text(encoding="utf-8")
    )

    assert report["status"] == "PASS"
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["final_gold_freeze_created"] is False
    assert report["agreed_approved_count"] + report["agreed_rejected_count"] + report["disagreement_count"] == 500
    assert manifest["status"] == "PASS_PENDING_R03_AND_FINAL_REJECTION_RESOLUTION"
    assert "send_blind_R03_packet_and_wait_for_adjudication" in manifest["next_steps"]
    assert "resolve_agreed_rejected_items_under_FINAL_REJECTION_RESOLUTION_LOCK" in manifest["next_steps"]
    assert rejection_lock["classification_reviewer_role"] == "R04"
    assert rejection_lock["agreed_rejected_count"] == report["agreed_rejected_count"] == 17
    assert rejection_lock["R04_must_be_distinct_from_R01"] is True
    assert rejection_lock["R04_must_be_distinct_from_R02"] is True
    assert rejection_lock["R04_must_be_distinct_from_R03"] is True
    assert rejection_lock["allowed_classes"] == [
        "CORRECTABLE_GOLD_ERROR",
        "SOURCE_TASK_INVALID",
    ]
    assert rejection_lock["R03_rejected_items_join_same_resolution_workflow"] is True


def test_stage6c_review_execution_rejects_blank_rejected_notes(tmp_path: Path) -> None:
    execution = copy_execution(tmp_path)
    r01_path = execution / "submissions" / "stage6c_gold_review_R01.submitted.tsv"
    with r01_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    for row in rows:
        if row["decision"] == "rejected":
            row["notes"] = ""
            break
    with r01_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    report = validate_execution(execution, SETUP_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R01_rejected_notes_blank:") for item in report["violations"])


def test_stage6c_review_execution_rejects_immutable_hash_mutation(tmp_path: Path) -> None:
    execution = copy_execution(tmp_path)
    r02_path = execution / "submissions" / "stage6c_gold_review_R02.submitted.tsv"
    with r02_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    rows[0]["authored_content_sha256"] = "0" * 64
    with r02_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    report = validate_execution(execution, SETUP_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R02_immutable_field_changed:") for item in report["violations"])


def test_stage6c_r03_packet_contains_no_r01_r02_submissions() -> None:
    manifest = json.loads(
        (EXECUTION_DIR / "REVIEW_EXECUTION_MANIFEST.json").read_text(encoding="utf-8")
    )
    if not manifest["r03_blind_packet_created"]:
        return
    archive_path = EXECUTION_DIR / "Stage6C_R03_blind_adjudication_packet_20260824.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()

    assert "r03_blind_packet/stage6c_gold_review_R03.tsv" in names
    assert all("R01" not in name and "R02" not in name and "submitted" not in name for name in names)


def test_stage6c_r04_packet_contains_only_final_rejection_resolution_materials() -> None:
    archive_path = EXECUTION_DIR / "Stage6C_R04_final_rejection_resolution_packet_20260824.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert names == {
        "FINAL_REJECTION_RESOLUTION_LOCK.json",
        "r04_resolution_packet/R04_PACKET_MANIFEST.json",
        "r04_resolution_packet/r04_resolution_items.jsonl",
        "r04_resolution_packet/official_table_metadata.jsonl",
        "r04_resolution_packet/stage6c_final_rejection_R04.tsv",
    }

    r04_items = [
        json.loads(line)
        for line in (EXECUTION_DIR / "r04_resolution_packet" / "r04_resolution_items.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    with (EXECUTION_DIR / "r04_resolution_packet" / "stage6c_final_rejection_R04.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert len(r04_items) == len(rows) == 17
    assert {item["stage6_sample_id"] for item in r04_items} == {row["stage6_sample_id"] for row in rows}
    assert all(item["R04_resolution_scope"]["R01_decision"] == "rejected" for item in r04_items)
    assert all(item["R04_resolution_scope"]["R02_decision"] == "rejected" for item in r04_items)
    assert all(row["reviewed_by"] == "R04" for row in rows)
    assert all(not row["classification"] and not row["rationale"] and not row["correction_spec"] for row in rows)


def test_stage6c_review_execution_rejects_old_single_branch_status(tmp_path: Path) -> None:
    execution = copy_execution(tmp_path)
    manifest_path = execution / "REVIEW_EXECUTION_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "PASS_PENDING_BLIND_R03_ADJUDICATION"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_execution(execution, SETUP_DIR)

    assert report["status"] == "FAIL"
    assert "status_not_expected_for_agreement_state" in report["violations"]


def test_stage6c_review_execution_rejects_missing_final_rejection_lock(tmp_path: Path) -> None:
    execution = copy_execution(tmp_path)
    (execution / "FINAL_REJECTION_RESOLUTION_LOCK.json").unlink()

    report = validate_execution(execution, SETUP_DIR)

    assert report["status"] == "FAIL"
    assert "missing_final_rejection_resolution_lock" in report["violations"]


def test_stage6c_review_execution_rejects_mutated_class_semantics(tmp_path: Path) -> None:
    execution = copy_execution(tmp_path)
    lock_path = execution / "FINAL_REJECTION_RESOLUTION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["class_rules"]["CORRECTABLE_GOLD_ERROR"]["required_action"] = ["do anything"]
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_execution(execution, SETUP_DIR)

    assert report["status"] == "FAIL"
    assert "final_rejection_lock_class_rules_changed" in report["violations"]


def test_stage6c_review_execution_rejects_missing_r04_distinctness(tmp_path: Path) -> None:
    execution = copy_execution(tmp_path)
    lock_path = execution / "FINAL_REJECTION_RESOLUTION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["R04_must_be_distinct_from_R02"] = False
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_execution(execution, SETUP_DIR)

    assert report["status"] == "FAIL"
    assert "final_rejection_lock_R04_must_be_distinct_from_R02_not_locked" in report["violations"]
