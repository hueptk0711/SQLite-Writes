from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

from scripts.data.import_stage7e0_a4_server_results import (
    PACKAGE_NAME,
    SERVER_RESULT_CLASSIFICATION_NAME,
    SERVER_RESULT_VALIDATION_REPORT_NAME,
    import_server_results,
    package_reviewer,
    status_from_classification,
)
from scripts.data.validate_stage7e0_a4_server_results import validate

ROOT = Path(__file__).resolve().parents[1]
STAGE_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
SERVER_TAR = ROOT / "stage7e0_a4_patch3_kaggle_primary_result_20260901_064812.tar.gz"
RESULT_DIR_NAME = "stage7e0_a4_english_candidate_span_kaggle_t4x2_results_20260901"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_kaggle_primary_tar_validates_as_protocol_compliant_primary_fail(tmp_path: Path) -> None:
    with tarfile.open(SERVER_TAR, "r:gz") as archive:
        archive.extractall(tmp_path)
    result_dir = tmp_path / RESULT_DIR_NAME
    report = validate(result_dir)
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "FAIL"
    assert report["primary_pass_count"] == "6/10"
    assert report["scientific_result_eligible"] is True


def test_import_kaggle_primary_result_freezes_fail_without_gretel(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    report = import_server_results(stage_dir, SERVER_TAR)
    status = status_from_classification(report["server_result_classification"])
    assert status == "REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL"
    assert report["result"]["primary_pass_count"] == "6/10"
    assert report["result"]["failure_stage_counts"] == {"acceptance_gate": 3, "materialization_failure": 1}
    assert read_json(stage_dir / SERVER_RESULT_CLASSIFICATION_NAME)["primary_gate_status"] == "FAIL"
    lock = read_json(stage_dir / "STAGE7E0_A4_SERVER_RESULT_LOCK.json")
    assert lock["gretel_pilot_opened"] is False
    assert lock["primary_gate_status"] == "FAIL"
    assert "do not open Gretel" in lock["decision"]
    assert "primary_pass_count=6/10" in (stage_dir / SERVER_RESULT_VALIDATION_REPORT_NAME).read_text(encoding="utf-8")
    assert "Stage7E0-A4 English Kaggle Primary Result PATCH4" in (stage_dir / "REVIEWER_README.md").read_text(encoding="utf-8")


def test_patch4_reviewer_package_opens(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    import_server_results(stage_dir, SERVER_TAR)
    package_path = tmp_path / PACKAGE_NAME
    digest = package_reviewer(stage_dir, SERVER_TAR, package_path)
    assert len(digest) == 64
    assert package_path.with_suffix(package_path.suffix + ".sha256").is_file()
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
    assert "stage7e0_a4_patch3_kaggle_primary_result_20260901_064812.tar.gz" in names
    assert f"{STAGE_NAME}/SERVER_RESULT_FAILURE_ANALYSIS.md" in names
