from __future__ import annotations

import csv
import json
import shutil
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
