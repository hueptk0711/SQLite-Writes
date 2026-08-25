from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.data.validate_stage6b_registration import validate_registration


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_DIR = ROOT / "stage6_crudsql_registration"


def copy_registration(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_crudsql_registration"
    shutil.copytree(REGISTRATION_DIR, target)
    return target


def test_stage6b_registration_validator_passes_repo_artifact() -> None:
    report = validate_registration(REGISTRATION_DIR)

    assert report["status"] == "PASS"
    assert report["sample_count"] == 500
    assert report["table_count"] == 125
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False


def test_stage6b_registration_fails_on_database_id_namespace_overlap(tmp_path: Path) -> None:
    registration = copy_registration(tmp_path)
    overlap_path = registration / "artifacts" / "crudsql_overlap_audit.json"
    overlap = json.loads(overlap_path.read_text(encoding="utf-8"))
    overlap["database_id_namespace_overlap_count"] = 1
    overlap_path.write_text(json.dumps(overlap, ensure_ascii=False), encoding="utf-8")

    report = validate_registration(registration)

    assert report["status"] == "FAIL"
    assert "nonzero_overlap_count:database_id_namespace_overlap_count=1" in report["violations"]


def test_stage6b_registration_fails_on_dataset_archive_hash_mismatch(tmp_path: Path) -> None:
    registration = copy_registration(tmp_path)
    archive = registration / "stage6b_crudsql_confirmation_dataset_20260824.zip"
    archive.write_bytes(archive.read_bytes() + b"mutation")

    report = validate_registration(registration)

    assert report["status"] == "FAIL"
    assert "dataset_archive_sha256_mismatch" in report["violations"]


def test_stage6b_registration_locks_gold_review_before_gpu() -> None:
    gold_protocol = json.loads(
        (REGISTRATION_DIR / "GOLD_REVIEW_PROTOCOL_LOCK.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (REGISTRATION_DIR / "CONFIRMATION_DATASET_MANIFEST.json").read_text(encoding="utf-8")
    )

    assert gold_protocol["two_independent_reviews_required"] is True
    assert gold_protocol["reviewer_must_not_see_model_predictions"] is True
    assert gold_protocol["final_gold_hash_required_before_gpu"] is True
    assert gold_protocol["confirmation_run_allowed_now"] is False
    assert manifest["confirmation_run_allowed_now"] is False
    assert manifest["model_called"] is False
    assert manifest["gpu_called"] is False
