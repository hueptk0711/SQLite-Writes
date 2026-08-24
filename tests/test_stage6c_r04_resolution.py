from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from scripts.data.validate_stage6c_r04_resolution import validate_r04_resolution


ROOT = Path(__file__).resolve().parents[1]
RESOLUTION_DIR = ROOT / "stage6_gold_review_r04_resolution"
R03_DIR = ROOT / "stage6_gold_review_r03_adjudication"


def copy_resolution(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_gold_review_r04_resolution"
    shutil.copytree(RESOLUTION_DIR, target)
    return target


def write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def test_stage6c_r04_resolution_validator_passes_repo_artifact() -> None:
    report = validate_r04_resolution(RESOLUTION_DIR, R03_DIR)

    assert report["status"] == "PASS"
    assert report["final_rejected_count"] == 40
    assert report["correctable_gold_error_count"] == 21
    assert report["source_task_invalid_count"] == 19
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["final_gold_freeze_created"] is False


def test_stage6c_r04_resolution_rejects_immutable_mutation(tmp_path: Path) -> None:
    resolution = copy_resolution(tmp_path)
    path = resolution / "submissions" / "stage6c_final_rejection_after_R03_R04.submitted.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    rows[0]["authored_content_sha256"] = "0" * 64
    write_rows(path, rows, fields)

    report = validate_r04_resolution(resolution, R03_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R04_immutable_field_changed:") for item in report["violations"])


def test_stage6c_r04_resolution_rejects_blank_rationale(tmp_path: Path) -> None:
    resolution = copy_resolution(tmp_path)
    path = resolution / "submissions" / "stage6c_final_rejection_after_R03_R04.submitted.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    rows[0]["rationale"] = ""
    write_rows(path, rows, fields)

    report = validate_r04_resolution(resolution, R03_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R04_blank_rationale:") for item in report["violations"])


def test_stage6c_r04_resolution_rejects_bad_correctable_json(tmp_path: Path) -> None:
    resolution = copy_resolution(tmp_path)
    path = resolution / "submissions" / "stage6c_final_rejection_after_R03_R04.submitted.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    for row in rows:
        if row["classification"] == "CORRECTABLE_GOLD_ERROR":
            row["correction_spec"] = "{not json"
            break
    write_rows(path, rows, fields)

    report = validate_r04_resolution(resolution, R03_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R04_correctable_correction_spec_not_json:") for item in report["violations"])


def test_stage6c_r04_resolution_rejects_source_invalid_correction_spec(tmp_path: Path) -> None:
    resolution = copy_resolution(tmp_path)
    path = resolution / "submissions" / "stage6c_final_rejection_after_R03_R04.submitted.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    for row in rows:
        if row["classification"] == "SOURCE_TASK_INVALID":
            row["correction_spec"] = json.dumps({"unexpected": True})
            break
    write_rows(path, rows, fields)

    report = validate_r04_resolution(resolution, R03_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R04_source_invalid_has_correction_spec:") for item in report["violations"])
