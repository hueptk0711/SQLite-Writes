from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.data.import_stage7e0_a3_server_results import PACKAGE_NAME, SERVER_TAR_NAME, STAGE_NAME, import_server_results, package_reviewer
from scripts.data.validate_stage7e0_a3_server_results import validate


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_imported_server_result_is_preserved_invalid_run_evidence(tmp_path: Path) -> None:
    tar_path = ROOT / SERVER_TAR_NAME
    assert tar_path.is_file()
    stage_dir = tmp_path / STAGE_NAME
    report = import_server_results(stage_dir, tar_path)
    assert report["result"]["backend"] == "hf"
    assert report["result"]["model_called"] is True
    assert report["result"]["gpu_called"] is True
    assert report["result"]["primary_pass_count"] == "0/8"
    assert report["result"]["required_pass_count"] == "8/8"
    assert report["result"]["diagnostics_run"] is False
    assert report["result"]["gretel_pilot_opened"] is False
    assert report["invalid_run_classification"]["evidence_integrity_status"] == "PASS"
    assert report["invalid_run_classification"]["protocol_compliance_status"] == "FAIL"
    assert report["invalid_run_classification"]["primary_gate_status"] == "INVALID_NOT_EVALUATED"
    assert report["invalid_run_classification"]["scientific_result_eligible"] is False


def test_server_result_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    tar_path = ROOT / SERVER_TAR_NAME
    import_server_results(stage_dir, tar_path)
    validation = validate(stage_dir, tar_path)
    assert validation["status"] == "PASS", validation["failures"]
    lock = read_json(stage_dir / "STAGE7E0_A3_SERVER_RESULT_LOCK.json")
    assert lock["status"] == "INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL"
    assert lock["primary_gate_status"] == "INVALID_NOT_EVALUATED"
    assert lock["scientific_result_eligible"] is False
    assert lock["gretel_pilot_opened"] is False


def test_server_result_reviewer_package_clean_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    tar_path = ROOT / SERVER_TAR_NAME
    import_server_results(stage_dir, tar_path)
    package_path = tmp_path / PACKAGE_NAME
    digest = package_reviewer(stage_dir, tar_path, package_path)
    assert len(digest) == 64
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        archive.extractall(tmp_path / "extract")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7e0_a3_server_results.py",
            "--stage-dir",
            STAGE_NAME,
            "--server-results-tar",
            SERVER_TAR_NAME,
        ],
        cwd=tmp_path / "extract",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
