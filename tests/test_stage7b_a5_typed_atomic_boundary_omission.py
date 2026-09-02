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

from scripts.data.build_stage7b_a5_typed_atomic_boundary_omission import (  # noqa: E402
    PACKAGE_NAME,
    STAGE_NAME,
    a5_suppression_reasons,
    boundary_quality_reason,
    build_stage,
    detect_omission_constructions,
    generate_candidate_inventory,
    omission_construction_region_reason,
    package_reviewer,
    schema_label_alias_index,
    typed_complete_literal_reason,
)
from scripts.data.validate_stage7b_a5_typed_atomic_boundary_omission import validate  # noqa: E402


RAW_DIR = ROOT.parent.parent / "external_sources" / "gretel_synthetic_text_to_sql_740ab236"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_dir_or_skip() -> Path:
    if not (RAW_DIR / "synthetic_text_to_sql_train.snappy.parquet").is_file():
        pytest.skip("local Gretel parquet raw_dir is not available")
    pytest.importorskip("pyarrow")
    return RAW_DIR


def by_text(question: str) -> dict[str, object]:
    return {candidate.text: candidate for candidate in generate_candidate_inventory(question)}


def test_typed_complete_literal_suppresses_numeric_percent_child() -> None:
    question = "Insert hydration_pct 68% and proof_minutes 42."
    inventory = generate_candidate_inventory(question)
    candidates = by_text(question)

    reason = typed_complete_literal_reason(candidates["68"], inventory)
    assert reason is not None
    assert reason["complete_literal_text"] == "68%"
    assert typed_complete_literal_reason(candidates["42"], inventory) is None


def test_full_omission_construction_region_suppresses_broad_spans_but_keeps_quoted_literal() -> None:
    question = 'Insert record. Field memo absent. Status "Absent".'
    inventory = generate_candidate_inventory(question)
    aliases = schema_label_alias_index({"field memo", "status"})
    detections = detect_omission_constructions(question, aliases)
    candidates = by_text(question)
    reasons = a5_suppression_reasons(inventory, aliases, detections, include_a4=False)

    assert omission_construction_region_reason(candidates["memo absent"], detections)["rule"] == "FULL_OMISSION_CONSTRUCTION_REGION"
    assert candidates["memo absent"].span_ref in reasons
    assert candidates["absent"].span_ref in reasons
    assert candidates["Absent"].span_ref not in reasons


def test_boundary_quality_suppresses_unbalanced_quote_and_schema_label_partial_value() -> None:
    quote_question = 'Insert scanner "flatbed nine" and job_id JOB-9.'
    quote_inventory = generate_candidate_inventory(quote_question)
    quote_candidates = by_text(quote_question)
    aliases = schema_label_alias_index({"scanner name", "job id"})
    assert boundary_quality_reason(quote_candidates['scanner "flatbed'], quote_inventory, aliases)["rule"] == "BOUNDARY_UNBALANCED_DOUBLE_QUOTE"

    partial_question = "Insert docket_stage second review and intake_id INT-7."
    partial_inventory = generate_candidate_inventory(partial_question)
    partial_candidates = by_text(partial_question)
    partial_aliases = schema_label_alias_index({"docket stage", "intake id"})
    reason = boundary_quality_reason(partial_candidates["docket_stage second"], partial_inventory, partial_aliases)
    assert reason is not None
    assert reason["rule"] == "BOUNDARY_SCHEMA_LABEL_PREFIX_PARTIAL_VALUE"
    assert partial_candidates["second review"].span_ref not in a5_suppression_reasons(partial_inventory, partial_aliases, [], include_a4=False)


def test_build_and_validator_pass_on_728_design_train(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    summary = build_stage(stage_dir, raw_dir_or_skip())
    report = validate(stage_dir)
    assert summary["status"] == "PASS_READY_FOR_REVIEW"
    assert report["status"] == "PASS", report["failures"]

    baseline = read_json(stage_dir / "DESIGN_TRAIN_BASELINE_A4_DOMAIN_AUDIT.json")
    a5 = read_json(stage_dir / "DESIGN_TRAIN_STAGE7B_A5_DOMAIN_AUDIT.json")
    false = read_json(stage_dir / "FALSE_SUPPRESSION_AUDIT.json")
    a6 = read_json(stage_dir / "A6_OBSERVED_ERROR_COUNTERFACTUAL_AUDIT.json")

    assert baseline["covered_assignment_count"] == 2252
    assert baseline["full_sample_covered_count"] == 724
    assert a5["covered_assignment_count"] == baseline["covered_assignment_count"]
    assert a5["full_sample_covered_count"] == baseline["full_sample_covered_count"]
    assert false["additional_assignment_losses"] == 0
    assert false["additional_full_sample_losses"] == 0
    assert a6["case_exact_pass_count"] == "2/12"
    assert a6["wrong_decision_count"] == 15
    assert a6["stage7b_a5_correct_gold_suppressed"] == 0


def test_validator_rejects_false_suppression_tamper(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    false_path = stage_dir / "FALSE_SUPPRESSION_AUDIT.json"
    false = read_json(false_path)
    false["additional_assignment_losses"] = 1
    false_path.write_text(json.dumps(false, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate(stage_dir)
    assert report["status"] == "FAIL"
    assert "additional_assignment_losses_nonzero" in report["failures"]
    assert "derived_manifest_hash_mismatch:FALSE_SUPPRESSION_AUDIT.json" in report["failures"]


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
        [sys.executable, "scripts/data/validate_stage7b_a5_typed_atomic_boundary_omission.py", "--stage-dir", STAGE_NAME],
        cwd=extract,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
