from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.data.build_stage7b_a2_candidate_span_reference import (
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    PACKAGE_NAME,
    STAGE_NAME,
    build_stage,
    generate_candidate_inventory,
    package_reviewer,
)
from scripts.data.validate_stage7b_a2_candidate_span_reference import validate


RAW_DIR = ROOT.parent.parent / "external_sources" / "gretel_synthetic_text_to_sql_740ab236"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_dir_or_skip() -> Path:
    if not (RAW_DIR / "synthetic_text_to_sql_train.snappy.parquet").is_file():
        pytest.skip("local Gretel parquet raw_dir is not available")
    pytest.importorskip("pyarrow")
    return RAW_DIR


def candidate_map(question: str) -> dict[str, str]:
    return {candidate.text: candidate.span_ref for candidate in generate_candidate_inventory(question)}


def test_candidate_generator_covers_currency_percent_and_tuple_literals() -> None:
    question = "Insert data: (1,1,10,'In Stock'), budget $5000, rate 45%, and price $29.99."
    candidates = candidate_map(question)
    for text in ["1", "10", "In Stock", "5000", "45", "29.99"]:
        assert text in candidates


def test_candidate_refs_are_deterministic_and_offset_backed() -> None:
    question = "Add contact name \"Mina Tran\", email mina.tran@example.com, priority 3."
    first = generate_candidate_inventory(question)
    second = generate_candidate_inventory(question)
    assert [candidate.span_ref for candidate in first] == [candidate.span_ref for candidate in second]
    assert [candidate.text for candidate in first] == [candidate.text for candidate in second]
    target = next(candidate for candidate in first if candidate.text == "mina.tran@example.com")
    assert question[target.start_char : target.end_char] == target.text


def test_build_and_validator_pass_on_728_non_pilot_design_train(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]
    coverage = read_json(stage_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.json")
    assert coverage["design_sample_count"] == EXPECTED_DESIGN_SAMPLE_COUNT
    assert coverage["assignment_count"] == EXPECTED_ASSIGNMENT_COUNT
    assert coverage["covered_assignment_count"] == EXPECTED_ASSIGNMENT_COUNT
    assert coverage["assignment_candidate_coverage"] == 1.0
    assert coverage["full_sample_candidate_coverage"] == 1.0


def test_scope_excludes_pilot_development_dev_and_official_test(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    scope = read_json(stage_dir / "DESIGN_TRAIN_SCOPE_AUDIT.json")
    assert scope["design_train_non_pilot_count"] == 728
    assert scope["development_pilot_pool_count"] == 100
    assert scope["development_dev_count"] == 100
    assert scope["official_test_confirmation_count"] == 51
    assert scope["pilot_ids_in_design_train"] == []
    assert scope["development_dev_ids_in_design_train"] == []
    assert scope["official_test_ids_in_design_train"] == []


def test_phase_o_schema_uses_span_refs_not_numeric_offsets(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    schema = read_json(stage_dir / "PHASE_O_SPAN_REFERENCE_SCHEMA.json")
    assert schema["required"] == ["operation", "span_refs"]
    assert "span_refs" in schema["properties"]
    assert "start_char" not in schema["properties"]
    assert "end_char" not in schema["properties"]
    protocol = read_json(stage_dir / "PHASE_O_SPAN_REFERENCE_PROTOCOL.json")
    assert protocol["model_generates_character_offsets"] is False
    assert protocol["pilot_usage_allowed"] is False


def test_validator_rejects_coverage_tamper(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    coverage_path = stage_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.json"
    coverage = read_json(coverage_path)
    coverage["covered_assignment_count"] -= 1
    coverage["assignment_candidate_coverage"] = coverage["covered_assignment_count"] / coverage["assignment_count"]
    coverage_path.write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = validate(stage_dir)
    assert report["status"] == "FAIL"
    assert "assignment_coverage_not_complete" in report["failures"]


def test_reviewer_package_clean_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    digest = package_reviewer(stage_dir, package_path)
    assert len(digest) == 64
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        archive.extractall(extract)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "tests/support/windows_py314_pytest_tempdir"])
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7b_a2_candidate_span_reference.py",
            "--stage-dir",
            STAGE_NAME,
        ],
        cwd=extract,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
