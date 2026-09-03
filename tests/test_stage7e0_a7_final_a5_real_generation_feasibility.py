from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import canonical_json, render_phase_o_messages
from scripts.data.build_stage7e0_a7_final_a5_real_generation_feasibility import (
    EXPECTED_PRIMARY_COUNT,
    PACKAGE_NAME,
    PRIMARY_RESULT_ARCHIVE_NAME,
    PRIMARY_RESULT_DIR_NAME,
    STAGE_NAME,
    build_stage,
    filtered_candidate_inventory_for_a7_case,
    package_reviewer,
    stage7e0_a7_cases,
)
from scripts.data.validate_stage7e0_a7_final_a5_real_generation_feasibility import validate
import scripts.server.run_stage7e0_a7_english as a7_runner
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


def test_a7_summary_handles_failed_case_with_none_preflight(tmp_path: Path) -> None:
    stage_dir = build_tmp_stage(tmp_path)
    row = read_jsonl(stage_dir / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")[0]
    result_root = tmp_path / "summary_result"
    generator = InvalidOnceGenerator()
    case, raw, _candidate, _prompt = evaluate_case(row, generator, phase_o_max_new_tokens=128)
    assert case["preflight_result"] is None
    a7_runner.write_jsonl(result_root / "results" / "per_sample_results.jsonl", [case])
    a7_runner.write_jsonl(result_root / "raw" / "model_outputs.jsonl", [raw])
    summary = a7_runner.write_summary(result_root, "mock", {"backend": "invalid_once"}, [case], [raw])
    assert summary["status"] == "FAIL"
    assert summary["preflight_pass_count"] == 0


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


def test_a7_build_can_bundle_official_result_archive(tmp_path: Path) -> None:
    freeze_stage = tmp_path / "freeze" / STAGE_NAME
    build_stage(freeze_stage, raw_dir=None)
    source_result = freeze_stage / "mock_dry_run"
    archive_root = tmp_path / "official_archive_root"
    official_result = archive_root / PRIMARY_RESULT_DIR_NAME
    archive_root.mkdir(parents=True)
    shutil.copytree(source_result, official_result)
    archive_path = tmp_path / PRIMARY_RESULT_ARCHIVE_NAME
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(official_result, arcname=PRIMARY_RESULT_DIR_NAME)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(archive_path.suffix + ".sha256").write_text(f"{digest}  {PRIMARY_RESULT_ARCHIVE_NAME}\n", encoding="utf-8")

    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    summary = build_stage(stage_dir, package_path, raw_dir=None, official_result_archive=archive_path)

    assert summary["official_generation_completed"] is True
    assert read_json(stage_dir / "official_results" / "OFFICIAL_RESULT_MANIFEST.json")["raw_model_output_count"] == EXPECTED_PRIMARY_COUNT
    with zipfile.ZipFile(package_path) as archive:
        names = set(archive.namelist())
    assert f"{STAGE_NAME}/official_results/{PRIMARY_RESULT_ARCHIVE_NAME}" in names
    assert f"{STAGE_NAME}/official_results/OFFICIAL_RESULT_MANIFEST.json" in names


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


def test_a7_constrained_runner_defaults_resume_false(tmp_path: Path, monkeypatch) -> None:
    build_tmp_stage(tmp_path)
    rows = read_jsonl(tmp_path / STAGE_NAME / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")

    class FakeConstrainedGenerator:
        def __init__(self, *args, **kwargs) -> None:
            self.mock = a7_runner.LabelMockGenerator(rows)

        def metadata(self) -> dict:
            return {
                "backend": a7_runner.CONSTRAINED_BACKEND_ID,
                "cuda_available": True,
                "mocked_for_test": True,
            }

        def generate(self, **kwargs) -> CallResult:
            return self.mock.generate(**kwargs)

    monkeypatch.setattr(a7_runner, "ConstrainedTransformersChatGenerator", FakeConstrainedGenerator)
    args = argparse.Namespace(
        accepted_protocol_commit="test",
        result_root=str(tmp_path / "constrained_runner_result"),
        backend="constrained_hf",
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
    assert args.resume is False
    assert summary["target_state_accuracy"] == "12/12"


def test_a7_finalize_existing_result_does_not_call_model_again(tmp_path: Path, monkeypatch) -> None:
    build_tmp_stage(tmp_path)
    first_args = argparse.Namespace(
        accepted_protocol_commit="test",
        result_root=str(tmp_path / "existing_runner_result"),
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
    run_stage7e0_a7(first_args)
    (tmp_path / "existing_runner_result" / "results" / "summary.json").unlink()
    (tmp_path / "existing_runner_result" / "results" / "failure_analysis.json").unlink()

    class NoSecondModelCall:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("finalize must not construct a generator")

    monkeypatch.setattr(a7_runner, "ConstrainedTransformersChatGenerator", NoSecondModelCall)
    finalize_args = argparse.Namespace(
        accepted_protocol_commit="test",
        result_root=str(tmp_path / "existing_runner_result"),
        backend="constrained_hf",
        model_name_or_path="unused",
        quantization="none",
        phase_o_max_new_tokens=512,
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        skip_git_assertions=True,
        allow_result_root_inside_git=True,
        finalize_existing_result=True,
        stage_root=tmp_path,
    )
    summary = run_stage7e0_a7(finalize_args)
    assert summary["target_state_accuracy"] == "12/12"
    assert read_json(tmp_path / "existing_runner_result" / "audits" / "model_call_audit.json")["finalized_existing_result"] is True
