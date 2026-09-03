from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import canonical_json, render_phase_o_messages
from scripts.data.build_stage7e0_a7_final_a5_real_generation_feasibility import (
    EXPECTED_PRIMARY_COUNT,
    PACKAGE_NAME,
    STAGE_NAME,
    build_stage,
    filtered_candidate_inventory_for_a7_case,
    package_reviewer,
    stage7e0_a7_cases,
)
from scripts.data.validate_stage7e0_a7_final_a5_real_generation_feasibility import validate
from scripts.server.run_stage7e0_a7_english import evaluate_case, run_stage7e0_a7
from scripts.server.run_stage7e0_a6_english import CallResult


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_tmp_stage(tmp_path: Path) -> Path:
    stage_dir = tmp_path / STAGE_NAME
    summary = build_stage(stage_dir, raw_dir=None)
    assert summary["status"] == "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN"
    return stage_dir


def candidate_texts_for_case(sample_id: str) -> set[str]:
    case = next(item for item in stage7e0_a7_cases() if item["sample_id"] == sample_id)
    _full, filtered, _reasons, _aliases = filtered_candidate_inventory_for_a7_case(case)
    return {candidate.text for candidate in filtered}


def test_a7_atomic_boundary_prefers_clean_values() -> None:
    texts = candidate_texts_for_case("stage7e0_a7_fresh_english_001")
    assert "Alice" in texts
    assert "22" in texts
    assert "Alice," not in texts
    assert "age 22" not in texts
    assert "22." not in texts


def test_a7_quotes_materialize_to_text_without_surface_quotes(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    rows = read_jsonl(stage_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")
    row = next(item for item in rows if item["sample_id"] == "stage7e0_a7_fresh_english_002")
    result = read_jsonl(stage_dir / "mock_dry_run" / "results" / "per_sample_results.jsonl")
    case = next(item for item in result if item["sample_id"] == row["sample_id"])
    assert case["target_state_correct"] is True
    assert "New York" in case["parameters"]
    assert '"New York"' not in case["parameters"]


def test_a7_required_columns_do_not_allow_omit_and_optional_columns_do(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    row = read_jsonl(stage_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")[0]
    properties = row["runtime_constraints"]["phase_o_schema"]["properties"]["column_span_refs"]["properties"]
    assert "OMIT" not in properties["COL_1"]["enum"]
    assert "OMIT" not in properties["COL_2"]["enum"]
    assert "OMIT" in properties["COL_3"]["enum"]


def test_a7_optional_default_column_omit_materializes_default(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    result = read_jsonl(stage_dir / "mock_dry_run" / "results" / "per_sample_results.jsonl")
    row = next(item for item in result if item["sample_id"] == "stage7e0_a7_fresh_english_004")
    assert row["target_state_correct"] is True
    assert row["parameters"] == ["SKU-77", 19.95]


def test_a7_typed_values_materialize_integer_real_and_text(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    result = read_jsonl(stage_dir / "mock_dry_run" / "results" / "per_sample_results.jsonl")
    row1 = next(item for item in result if item["sample_id"] == "stage7e0_a7_fresh_english_001")
    row3 = next(item for item in result if item["sample_id"] == "stage7e0_a7_fresh_english_003")
    assert 22 in row1["parameters"]
    assert 3.14 in row3["parameters"]
    assert "DEV-42" in row3["parameters"]


def test_a7_mock_runner_is_one_call_without_phase_m(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    summary = read_json(stage_dir / "mock_dry_run" / "results" / "summary.json")
    call_audit = read_json(stage_dir / "mock_dry_run" / "audits" / "model_call_audit.json")
    assert summary["model_calls_total"] == EXPECTED_PRIMARY_COUNT
    assert summary["model_calls_per_sample"] == 1
    assert summary["phase_m_invocations"] == 0
    assert call_audit["phase_m_invocations"] == 0


@dataclass
class InvalidOnceGenerator:
    calls: int = 0

    def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict) -> CallResult:
        del messages, max_new_tokens, row
        self.calls += 1
        return CallResult(sample_id=sample_id, phase="phase_o", raw_output="{not-json")

    def metadata(self) -> dict:
        return {"backend": "invalid_once", "model_called": False}


def test_a7_invalid_output_fails_without_retry(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    row = read_jsonl(stage_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")[0]
    generator = InvalidOnceGenerator()
    result, _raw, _candidate, _prompt = evaluate_case(row, generator, phase_o_max_new_tokens=128)
    assert result["status"] == "FAIL"
    assert result["failure_code"] == "MODEL_PARSE_FAILURE"
    assert generator.calls == 1


def test_a7_prompt_hash_is_gold_isolated(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    row = read_jsonl(stage_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")[0]
    _messages, _user, original_hash = render_phase_o_messages(row)
    mutated = json.loads(canonical_json(row))
    mutated["label_side_expected"]["phase_o"]["column_span_refs"] = {
        column: "OMIT" for column in mutated["label_side_expected"]["phase_o"]["column_span_refs"]
    }
    _messages2, _user2, mutated_hash = render_phase_o_messages(mutated)
    assert original_hash == mutated_hash


def test_a7_build_validator_and_clean_package_pytest(tmp_path: Path) -> None:
    if os.environ.get("A7_SKIP_NESTED_PACKAGE_PYTEST") == "1":
        return
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    summary = build_stage(stage_dir, package_path, raw_dir=None)
    assert summary["fresh_primary_count"] == EXPECTED_PRIMARY_COUNT
    assert validate(stage_dir)["status"] == "PASS"
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        archive.extractall(tmp_path / "extract")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "src"])
    env["A7_SKIP_NESTED_PACKAGE_PYTEST"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7e0_a7_final_a5_real_generation_feasibility.py"],
        cwd=tmp_path / "extract",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a7_runner_writes_expected_result_layout(tmp_path: Path, monkeypatch) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    target_stage = ROOT / STAGE_NAME
    if target_stage.exists():
        monkeypatch.setattr("scripts.server.run_stage7e0_a7_english.PROJECT_ROOT", tmp_path)
    args = argparse.Namespace(
        accepted_protocol_commit="test",
        result_root=str(tmp_path / "runner_result"),
        backend="mock",
        model_name_or_path="unused",
        quantization="none",
        phase_o_max_new_tokens=512,
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        skip_git_assertions=True,
        allow_result_root_inside_git=True,
        stage_root=tmp_path,
    )
    summary = run_stage7e0_a7(args)
    assert summary["target_state_accuracy"] == "12/12"
    assert (tmp_path / "runner_result" / "raw" / "model_outputs.jsonl").is_file()
    assert (tmp_path / "runner_result" / "results" / "per_sample_results.jsonl").is_file()
