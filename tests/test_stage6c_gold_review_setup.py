from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path

from scripts.data.validate_stage6c_gold_review_setup import validate_setup


ROOT = Path(__file__).resolve().parents[1]
SETUP_DIR = ROOT / "stage6_gold_review_setup"


def copy_setup(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_gold_review_setup"
    shutil.copytree(SETUP_DIR, target)
    return target


def test_stage6c_gold_review_setup_validator_passes_repo_artifact() -> None:
    report = validate_setup(SETUP_DIR)

    assert report["status"] == "PASS"
    assert report["sample_count"] == 500
    assert report["reviewer_packets"] == 2
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False


def test_stage6c_rejects_ambiguous_adjudication_policy(tmp_path: Path) -> None:
    setup = copy_setup(tmp_path)
    path = setup / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"
    addendum = json.loads(path.read_text(encoding="utf-8"))
    addendum["adjudication_policy"]["disagreement_rule"] = "third_adjudicator_or_joint_discussion"
    path.write_text(json.dumps(addendum, ensure_ascii=False), encoding="utf-8")

    report = validate_setup(setup)

    assert report["status"] == "FAIL"
    assert "adjudication_rule_not_third_independent" in report["violations"]


def test_stage6c_rejects_missing_reviewer_isolation_policy(tmp_path: Path) -> None:
    setup = copy_setup(tmp_path)
    path = setup / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"
    addendum = json.loads(path.read_text(encoding="utf-8"))
    addendum.pop("reviewer_isolation")
    path.write_text(json.dumps(addendum, ensure_ascii=False), encoding="utf-8")

    report = validate_setup(setup)

    assert report["status"] == "FAIL"
    assert "R01_blind_to_R02_review_not_locked" in report["violations"]


def test_stage6c_rejects_review_packet_content_hash_mutation(tmp_path: Path) -> None:
    setup = copy_setup(tmp_path)
    path = setup / "review_packets" / "stage6c_gold_review_R01.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = rows[0].keys()
    rows[0]["authored_content_sha256"] = "0" * 64
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    report = validate_setup(setup)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R01_content_hash_mismatch:") for item in report["violations"])


def test_stage6c_rejects_prefilled_decision_before_execution(tmp_path: Path) -> None:
    setup = copy_setup(tmp_path)
    path = setup / "review_packets" / "stage6c_gold_review_R02.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = rows[0].keys()
    rows[0]["decision"] = "approved"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    report = validate_setup(setup)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R02_decision_not_blank_before_execution:") for item in report["violations"])


def test_stage6c_rejects_r01_archive_containing_r02_packet(tmp_path: Path) -> None:
    setup = copy_setup(tmp_path)
    archive_path = setup / "Stage6C_R01_review_packet_20260824.zip"
    r02_packet = setup / "review_packets" / "stage6c_gold_review_R02.tsv"
    with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(r02_packet, "review_packets/stage6c_gold_review_R02.tsv")

    report = validate_setup(setup)

    assert report["status"] == "FAIL"
    assert "R01_isolated_archive_contains_other_reviewer_tsv" in report["violations"]


def test_stage6c_rejects_final_decision_table_mutation(tmp_path: Path) -> None:
    setup = copy_setup(tmp_path)
    path = setup / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"
    addendum = json.loads(path.read_text(encoding="utf-8"))
    addendum["final_decision_rule"][1]["action"] = "blind_R03_adjudication"
    path.write_text(json.dumps(addendum, ensure_ascii=False), encoding="utf-8")

    report = validate_setup(setup)

    assert report["status"] == "FAIL"
    assert "final_decision_table_R01_R02_rules_mismatch" in report["violations"]
