from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from scripts.data.validate_stage6d_corrected_gold_review_setup import (
    validate_stage6d_corrected_gold_review_setup,
)
from scripts.data.create_stage6d_corrected_gold_review_setup import (
    apply_operations_to_plan,
    create_corrected_gold_review_setup,
    normalize_correction_operations,
)


ROOT = Path(__file__).resolve().parents[1]
STAGE6D_DIR = ROOT / "stage6_corrected_gold_review_setup"
STAGE6B_DIR = ROOT / "stage6_crudsql_registration"
SETUP_DIR = ROOT / "stage6_gold_review_setup"
R04_DIR = ROOT / "stage6_gold_review_r04_resolution"


def copy_stage6d(tmp_path: Path) -> Path:
    target = tmp_path / "stage6_corrected_gold_review_setup"
    shutil.copytree(STAGE6D_DIR, target)
    return target


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_tsv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def test_stage6d_corrected_gold_review_setup_validator_passes_repo_artifact() -> None:
    report = validate_stage6d_corrected_gold_review_setup(STAGE6D_DIR, STAGE6B_DIR, SETUP_DIR, R04_DIR)

    assert report["status"] == "PASS"
    assert report["corrected_item_count"] == 21
    assert report["source_invalid_item_count_not_processed_here"] == 19
    assert report["confirmation_run_allowed_now"] is False
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["final_gold_freeze_created"] is False


def test_stage6d_rejects_corrected_value_mutation(tmp_path: Path) -> None:
    stage6d = copy_stage6d(tmp_path)
    path = stage6d / "artifacts" / "corrected_gold_review_items.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["corrected_gold_write_plan"]["values"][0] = "MUTATED"
    write_jsonl(path, rows)

    report = validate_stage6d_corrected_gold_review_setup(stage6d, STAGE6B_DIR, SETUP_DIR, R04_DIR)

    assert report["status"] == "FAIL"
    assert "corrected_gold_review_items_mismatch" in report["violations"]


def test_stage6d_rejects_protocol_mutation(tmp_path: Path) -> None:
    stage6d = copy_stage6d(tmp_path)
    path = stage6d / "CORRECTED_GOLD_REVIEW_PROTOCOL_LOCK.json"
    protocol = json.loads(path.read_text(encoding="utf-8"))
    protocol["reviewer_roles_must_be_distinct"] = False
    path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_stage6d_corrected_gold_review_setup(stage6d, STAGE6B_DIR, SETUP_DIR, R04_DIR)

    assert report["status"] == "FAIL"
    assert "protocol_lock_not_exact_expected" in report["violations"]


def test_stage6d_rejects_prefilled_packet_decision(tmp_path: Path) -> None:
    stage6d = copy_stage6d(tmp_path)
    path = stage6d / "corrected_review_packets" / "stage6d_corrected_gold_review_C01.tsv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0].keys())
    rows[0]["decision"] = "approved"
    write_tsv(path, rows, fields)

    report = validate_stage6d_corrected_gold_review_setup(stage6d, STAGE6B_DIR, SETUP_DIR, R04_DIR)

    assert report["status"] == "FAIL"
    assert any(item.startswith("C01_decision_or_notes_prefilled:") for item in report["violations"])


def test_stage6d_rejects_mutated_authoritative_r04_queue_after_regeneration(tmp_path: Path) -> None:
    r04 = tmp_path / "stage6_gold_review_r04_resolution"
    shutil.copytree(R04_DIR, r04)
    queue_path = r04 / "CORRECTABLE_GOLD_ERROR_QUEUE.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    queue["items"][0]["correction_spec"]["corrected_value"] = "MUTATED_WRONG_VALUE"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        create_corrected_gold_review_setup(STAGE6B_DIR, SETUP_DIR, r04, tmp_path / "mutated_stage6d")
    except RuntimeError as exc:
        assert "R04 correctable queue hash mismatch" in str(exc)
    else:  # pragma: no cover - defensive; creator should fail before writing artifacts.
        report = validate_stage6d_corrected_gold_review_setup(
            tmp_path / "mutated_stage6d", STAGE6B_DIR, SETUP_DIR, r04
        )
        assert report["status"] == "FAIL"
        assert "actual_correctable_queue_not_accepted_hash" in report["violations"]


def test_stage6d_correction_precondition_rejects_registered_value_mismatch() -> None:
    queue = json.loads((R04_DIR / "CORRECTABLE_GOLD_ERROR_QUEUE.json").read_text(encoding="utf-8"))
    items = {
        json.loads(line)["stage6_sample_id"]: json.loads(line)
        for line in (SETUP_DIR / "artifacts" / "gold_review_items.jsonl").read_text(encoding="utf-8").splitlines()
    }
    queue_item = next(item for item in queue["items"] if item["stage6_sample_id"] == "stage6_crudsql_0070")
    item = items[queue_item["stage6_sample_id"]]
    spec = dict(queue_item["correction_spec"])
    spec["registered_value"] = "999"
    operations = normalize_correction_operations(item, spec)

    try:
        apply_operations_to_plan(item, operations)
    except ValueError as exc:
        assert "precondition failed" in str(exc)
    else:  # pragma: no cover - defensive.
        raise AssertionError("precondition mismatch should fail")


def test_stage6d_correction_precondition_rejects_missing_remove_column() -> None:
    queue = json.loads((R04_DIR / "CORRECTABLE_GOLD_ERROR_QUEUE.json").read_text(encoding="utf-8"))
    items = {
        json.loads(line)["stage6_sample_id"]: json.loads(line)
        for line in (SETUP_DIR / "artifacts" / "gold_review_items.jsonl").read_text(encoding="utf-8").splitlines()
    }
    queue_item = next(item for item in queue["items"] if item["stage6_sample_id"] == "stage6_crudsql_0014")
    item = items[queue_item["stage6_sample_id"]]
    operations = normalize_correction_operations(item, queue_item["correction_spec"])
    operations[0]["column_index"] = 1
    operations[0]["column"] = "机构性质"

    try:
        apply_operations_to_plan(item, operations)
    except ValueError as exc:
        assert "column missing from original gold" in str(exc)
    else:  # pragma: no cover - defensive.
        raise AssertionError("missing remove-column precondition should fail")
