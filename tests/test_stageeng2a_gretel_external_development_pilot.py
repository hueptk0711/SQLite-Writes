from __future__ import annotations

import sqlite3
import shutil
import uuid
import json
from pathlib import Path

from scripts.data.build_stageeng2a_gretel_external_development_pilot import EXPECTED_PILOT_N, STAGE_NAME
from scripts.data.validate_stageeng2a_gretel_external_development_pilot import validate_stage
from scripts.server.run_stageeng2a_gretel_pilot import evaluate_sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_stageeng2a_package_validates() -> None:
    result = validate_stage(PROJECT_ROOT / STAGE_NAME)
    assert result["status"] == "PASS", result
    assert result["pilot_n"] == EXPECTED_PILOT_N
    assert result["methods"] == ["M0_DIRECT_SQL", "M1_J_FS", "M2_FROZEN_A7"]


def test_model_side_inputs_do_not_expose_gold() -> None:
    rows = (PROJECT_ROOT / STAGE_NAME / "ENG2A_PILOT_100_FREEZE.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == EXPECTED_PILOT_N
    forbidden = {"gold_sql", "gold_post_state", "target_state", "label_side_expected", "evaluator_side_expected"}
    for line in rows:
        assert not any(token in line.split('"model_side_input":', 1)[1].split('"runtime_constraints":', 1)[0] for token in forbidden)


def test_official_server_results_are_frozen_and_validated() -> None:
    summary_path = PROJECT_ROOT / STAGE_NAME / "official_server_run" / "results" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["backend"] == "hf"
    assert summary["status"] == "PASS"
    assert summary["pilot_n"] == EXPECTED_PILOT_N
    assert summary["model_calls_total"] == EXPECTED_PILOT_N * 3
    assert summary["model_calls_per_sample_per_method"] == 1
    assert summary["retry_count"] == 0
    assert summary["methods"]["M0_DIRECT_SQL"]["target_state_accuracy"] == "96/100"
    assert summary["methods"]["M1_J_FS"]["target_state_accuracy"] == "87/100"
    assert summary["methods"]["M2_FROZEN_A7"]["target_state_accuracy"] == "50/100"
    assert summary["generation_metadata"]["constrained"]["model_called"] is True
    assert summary["generation_metadata"]["unconstrained"]["model_called"] is True


def test_off_target_delta_detects_extra_persistent_table_write() -> None:
    tmp_path = PROJECT_ROOT / ".test_tmp_stageeng2a" / uuid.uuid4().hex
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        _assert_extra_persistent_table_write(tmp_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def _assert_extra_persistent_table_write(tmp_path: Path) -> None:
    db_path = tmp_path / "case.sqlite"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE target(id INT, name TEXT)")
        con.execute("CREATE TABLE audit(id INT, note TEXT)")
        con.commit()
    row = {
        "sample_id": "s1",
        "synthetic_db_spec": {"sqlite_db_path": "case.sqlite"},
        "evaluator_side_expected": {
            "gold_sql": ["INSERT INTO target(id, name) VALUES (1, 'ok')"],
            "gold_target_table": "target",
        },
    }
    result = evaluate_sql(
        row,
        tmp_path,
        ["INSERT INTO target(id, name) VALUES (1, 'ok')", "INSERT INTO audit(id, note) VALUES (99, 'extra')"],
    )
    assert result["execution_success"] is True
    assert result["target_state_correct"] is True
    assert result["strict_full_state_correct"] is False
    assert result["any_off_target_change"] is True
    assert result["off_target_mismatched_tables"] == ["audit"]


def test_off_target_delta_detects_wrong_target_extra_delta() -> None:
    tmp_path = PROJECT_ROOT / ".test_tmp_stageeng2a" / uuid.uuid4().hex
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        _assert_wrong_target_extra_delta(tmp_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def _assert_wrong_target_extra_delta(tmp_path: Path) -> None:
    db_path = tmp_path / "case.sqlite"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE target(id INT, name TEXT)")
        con.commit()
    row = {
        "sample_id": "s2",
        "synthetic_db_spec": {"sqlite_db_path": "case.sqlite"},
        "evaluator_side_expected": {
            "gold_sql": ["INSERT INTO target(id, name) VALUES (1, 'gold')"],
            "gold_target_table": "target",
        },
    }
    result = evaluate_sql(row, tmp_path, ["INSERT INTO target(id, name) VALUES (1, 'wrong')"])
    assert result["execution_success"] is True
    assert result["target_state_correct"] is False
    assert result["any_off_target_change"] is True
    assert result["off_target_mismatched_tables"] == ["target"]
