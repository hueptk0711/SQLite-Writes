from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path

from scripts.data.validate_stage6c_r03_adjudication import validate_r03_adjudication


ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION_DIR = ROOT / "stage6_gold_review_r03_adjudication"
EXECUTION_DIR = ROOT / "stage6_gold_review_execution"


def copy_adjudication(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_gold_review_r03_adjudication"
    shutil.copytree(ADJUDICATION_DIR, target)
    return target


def test_stage6c_r03_adjudication_validator_passes_repo_artifact() -> None:
    report = validate_r03_adjudication(ADJUDICATION_DIR, EXECUTION_DIR)

    assert report["status"] == "PASS"
    assert report["R03_approved_count"] == 29
    assert report["R03_rejected_count"] == 23
    assert report["final_approved_count_after_R03"] == 460
    assert report["final_rejected_count_after_R03"] == 40
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["final_gold_freeze_created"] is False


def test_stage6c_r03_adjudication_rejects_immutable_mutation(tmp_path: Path) -> None:
    adjudication = copy_adjudication(tmp_path)
    path = adjudication / "submissions" / "stage6c_gold_review_R03.submitted.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    rows[0]["authored_content_sha256"] = "0" * 64
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    report = validate_r03_adjudication(adjudication, EXECUTION_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R03_immutable_field_changed:") for item in report["violations"])


def test_stage6c_r03_adjudication_rejects_blank_rejected_notes(tmp_path: Path) -> None:
    adjudication = copy_adjudication(tmp_path)
    path = adjudication / "submissions" / "stage6c_gold_review_R03.submitted.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    for row in rows:
        if row["decision"] == "rejected":
            row["notes"] = ""
            break
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    report = validate_r03_adjudication(adjudication, EXECUTION_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("R03_rejected_notes_blank:") for item in report["violations"])


def test_stage6c_r04_after_r03_packet_is_isolated_to_final_rejections() -> None:
    archive_path = ADJUDICATION_DIR / "Stage6C_R04_final_rejection_resolution_after_R03_packet_20260824.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert names == {
        "FINAL_REJECTION_RESOLUTION_QUEUE_AFTER_R03.json",
        "r04_after_r03_resolution_packet/R04_AFTER_R03_PACKET_MANIFEST.json",
        "r04_after_r03_resolution_packet/r04_after_R03_resolution_items.jsonl",
        "r04_after_r03_resolution_packet/official_table_metadata.jsonl",
        "r04_after_r03_resolution_packet/stage6c_final_rejection_after_R03_R04.tsv",
    }

    queue = json.loads((ADJUDICATION_DIR / "FINAL_REJECTION_RESOLUTION_QUEUE_AFTER_R03.json").read_text(encoding="utf-8"))
    items = [
        json.loads(line)
        for line in (ADJUDICATION_DIR / "r04_after_r03_resolution_packet" / "r04_after_R03_resolution_items.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    with (ADJUDICATION_DIR / "r04_after_r03_resolution_packet" / "stage6c_final_rejection_after_R03_R04.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert queue["final_rejected_count"] == len(items) == len(rows) == 40
    assert {item["stage6_sample_id"] for item in items} == {row["stage6_sample_id"] for row in rows}
    for item in items:
        scope = item["R04_resolution_scope"]
        for role in ["R01", "R02", "R03"]:
            if scope[f"{role}_decision"] == "rejected":
                assert scope[f"{role}_notes"]
                assert scope[f"{role}_notes_sha256"]
    assert all(row["reviewed_by"] == "R04" for row in rows)
    assert all(not row["classification"] and not row["rationale"] and not row["correction_spec"] for row in rows)


def test_stage6c_r03_adjudication_rejects_queue_semantics_mutation(tmp_path: Path) -> None:
    adjudication = copy_adjudication(tmp_path)
    path = adjudication / "FINAL_REJECTION_RESOLUTION_QUEUE_AFTER_R03.json"
    queue = json.loads(path.read_text(encoding="utf-8"))
    queue["class_rules"]["CORRECTABLE_GOLD_ERROR"]["required_action"] = ["do anything"]
    path.write_text(json.dumps(queue, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_r03_adjudication(adjudication, EXECUTION_DIR)

    assert report["status"] == "FAIL"
    assert "queue_class_rules_changed" in report["violations"]


def test_stage6c_r03_adjudication_rejects_missing_r01_r02_reason_text(tmp_path: Path) -> None:
    adjudication = copy_adjudication(tmp_path)
    path = adjudication / "r04_after_r03_resolution_packet" / "r04_after_R03_resolution_items.jsonl"
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    items[0]["R04_resolution_scope"]["R01_notes"] = ""
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items) + "\n", encoding="utf-8")

    report = validate_r03_adjudication(adjudication, EXECUTION_DIR)

    assert report["status"] == "FAIL"
    assert any(
        item.startswith("R04_after_R03_missing_R01_rejection_reason:")
        for item in report["violations"]
    )
