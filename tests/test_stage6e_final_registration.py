from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.data.validate_stage6e_final_registration import validate_stage6e_final_registration


ROOT = Path(__file__).resolve().parents[1]
STAGE6E_DIR = ROOT / "stage6_final_registration_revision"


def copy_stage6e(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_final_registration_revision"
    shutil.copytree(STAGE6E_DIR, target)
    return target


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_stage6e_final_registration_validator_passes_repo_artifact() -> None:
    report = validate_stage6e_final_registration(STAGE6E_DIR)

    assert report["status"] == "PASS"
    assert report["original_registered_n"] == 500
    assert report["source_task_invalid_n"] == 19
    assert report["replacement_samples"] == 0
    assert report["final_confirmation_n"] == 481
    assert report["original_review_accepted_count"] == 460
    assert report["corrected_review_accepted_count"] == 21
    assert report["final_gold_replay_pass_count"] == 481
    assert report["final_gold_freeze_created"] is True
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False


def test_stage6e_rejects_replacement_policy_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "STAGE6E_FINAL_REGISTRATION_LOCK.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["replacement_samples"] = 1
    write_json(path, lock)

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "lock_replacement_samples_mismatch" in report["violations"]


def test_stage6e_rejects_final_manifest_row_removal(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "final_confirmation_sample_manifest_mismatch" in report["violations"]


def test_stage6e_rejects_invalid_exclusion_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "SOURCE_TASK_INVALID_EXCLUSIONS.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["replacement_sample"] = "stage6_crudsql_0499"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "source_task_invalid_exclusions_mismatch" in report["violations"]


def test_stage6e_rejects_final_gold_program_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "FINAL_GOLD_PROGRAMS.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["parameters"][0] = "MUTATED"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "final_gold_programs_mismatch" in report["violations"]


def test_stage6e_rejects_overlap_audit_mutation(tmp_path: Path) -> None:
    stage6e = copy_stage6e(tmp_path)
    path = stage6e / "artifacts" / "FINAL_OVERLAP_AUDIT.json"
    audit = json.loads(path.read_text(encoding="utf-8"))
    audit["sample_id_overlap_count"] = 1
    write_json(path, audit)

    report = validate_stage6e_final_registration(stage6e)

    assert report["status"] == "FAIL"
    assert "final_overlap_audit_mismatch" in report["violations"]
