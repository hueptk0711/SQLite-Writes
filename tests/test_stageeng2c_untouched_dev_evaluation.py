from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.data.validate_stageeng2c_untouched_dev_evaluation import validate_stage
from scripts.server.run_stageeng2c_dev100_evaluation import METHODS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGE_NAME = "StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION"
STAGE_DIR = PROJECT_ROOT / STAGE_NAME


pytestmark = pytest.mark.skipif(not STAGE_DIR.exists(), reason="Stage ENG2C artifacts have not been built yet")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stageeng2c_preflight_package_validates_without_official_results() -> None:
    result = validate_stage(STAGE_DIR, skip_official=True, require_official=False, official_result_root=None)
    assert result["status"] == "PASS", result["failures"]
    assert result["dev100_n"] == 100
    assert result["methods"] == list(METHODS)


def test_stageeng2c_protocol_freezes_dev100_and_four_arms() -> None:
    protocol = read_json(STAGE_DIR / "ENG2C_PROTOCOL_FREEZE.json")
    assert protocol["status"] == "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN"
    assert protocol["dataset"]["denominator"] == 100
    assert protocol["primary_metric"] == "strict_full_state_accuracy"
    assert [method["method_id"] for method in protocol["methods"]] == list(METHODS)
    assert protocol["model"]["model_revision"] == "c03e6d358207e414f1eca0bb1891e29f1db0e242"
    assert protocol["model"]["do_sample"] is False
    assert protocol["model"]["best_of_n"] == 1
    assert protocol["model"]["self_consistency"] == "none"
    assert protocol["no_method_changes_after_official_eng2c_run"] is True


def test_stageeng2c_split_isolation_and_gold_blind_model_side() -> None:
    isolation = read_json(STAGE_DIR / "audits" / "split_isolation.json")
    assert isolation["status"] == "PASS"
    assert isolation["development_train_overlap_total"] == 0
    assert isolation["eng2a_pilot_overlap_total"] == 0
    assert isolation["official51_overlap_total"] == 0
    assert isolation["official_raw_question_context_sql_opened"] is False
    rows = read_jsonl(STAGE_DIR / "ENG2C_DEV100_FREEZE.jsonl")
    assert len(rows) == 100
    for row in rows:
        assert set(row["model_side_input"]) == {"question", "schema_inventory", "candidate_inventory_text"}
        assert row["external_development_dev"] is True
        assert row["external_development_pilot"] is False
        assert row["runtime_constraints"]["retry"] == 0


def test_stageeng2c_mock_dry_run_covers_all_methods() -> None:
    summary = read_json(STAGE_DIR / "mock_dry_run" / "results" / "aggregate_results.json")
    assert summary["status"] == "PASS"
    assert summary["denominator"] == 100
    assert summary["primary_metric"] == "strict_full_state_accuracy"
    assert summary["model_calls_total"] == 400
    for method_id in METHODS:
        assert summary["methods"][method_id]["samples"] == 100
        assert summary["methods"][method_id]["model_calls"] == 100
        assert "strict_full_state_accuracy" in summary["methods"][method_id]


def test_stageeng2c_runner_cli_and_live_dry_config() -> None:
    help_proc = subprocess.run(
        [sys.executable, "scripts/server/run_stageeng2c_dev100_evaluation.py", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--backend" in help_proc.stdout
    assert "M2_FINAL_ENG2B" in Path("scripts/server/run_stageeng2c_dev100_evaluation.py").read_text(encoding="utf-8")
    dry_proc = subprocess.run(
        [
            sys.executable,
            "scripts/server/run_stageeng2c_dev100_evaluation.py",
            "--stage-dir",
            STAGE_NAME,
            "--result-root",
            "tmp_stageeng2c_dry_config_test",
            "--dry-run-live-config",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry_proc.returncode == 0, dry_proc.stderr
    config = json.loads(dry_proc.stdout)
    assert config["method_id"] == "M2_FINAL_ENG2B"
    assert config["model_revision"] == "c03e6d358207e414f1eca0bb1891e29f1db0e242"
    assert config["generation_settings"]["retry"] == 0
