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
    omittable_schema_aliases_from_inventory,
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


def candidate_for_text_in_region(question: str, text: str, region: str):
    inventory = generate_candidate_inventory(question)
    region_start = question.casefold().index(region.casefold())
    region_end = region_start + len(region)
    return next(
        candidate
        for candidate in inventory
        if candidate.text == text and region_start <= candidate.start_char and candidate.end_char <= region_end
    )


def test_typed_complete_literal_suppresses_numeric_percent_child() -> None:
    question = "Insert hydration_pct 68%, completion_percentage 68 percent, and proof_minutes 42."
    inventory = generate_candidate_inventory(question)
    candidates = by_text(question)

    pct_child = candidate_for_text_in_region(question, "68", "hydration_pct 68%")
    reason = typed_complete_literal_reason(pct_child, inventory)
    assert reason is not None
    assert reason["complete_literal_text"] == "68%"
    percent_phrase = candidate_for_text_in_region(question, "68", "completion_percentage 68 percent")
    phrase_reason = typed_complete_literal_reason(percent_phrase, inventory)
    assert phrase_reason is not None
    assert phrase_reason["complete_literal_text"] == "68 percent"
    assert typed_complete_literal_reason(candidates["42"], inventory) is None


def test_typed_complete_literal_does_not_suppress_generic_alpha_or_unit_suffixes() -> None:
    question = "Insert hydration_pct 68kg, completion_percentage 68abc, weight_kg 68kg, and duration_ms 25ms."
    inventory = generate_candidate_inventory(question)
    aliases = schema_label_alias_index({"hydration pct", "completion percentage", "weight kg", "duration ms"})
    reasons = a5_suppression_reasons(inventory, aliases, [], include_a4=False)

    for text in ["68", "68kg", "68abc", "25", "25ms"]:
        candidate = candidate_for_text_in_region(question, text, text)
        assert typed_complete_literal_reason(candidate, inventory) is None
        assert candidate.span_ref not in reasons


def test_omission_construction_respects_schema_admissibility_and_keeps_literals() -> None:
    question = 'Insert record. Required status absent, required status missing, optional memo absent, status "Absent", and status "Missing".'
    inventory = generate_candidate_inventory(question)
    aliases = schema_label_alias_index({"status", "memo"})
    schema_inventory = {
        "columns": [
            {"column_name": "status", "nullable": False, "has_default": False},
            {"column_name": "memo", "nullable": True, "has_default": False},
        ]
    }
    detections = detect_omission_constructions(question, omittable_schema_aliases_from_inventory(schema_inventory))
    candidates = by_text(question)
    reasons = a5_suppression_reasons(inventory, aliases, detections, include_a4=False)

    optional_memo_absent = candidate_for_text_in_region(question, "memo absent", "optional memo absent")
    optional_absent = candidate_for_text_in_region(question, "absent", "optional memo absent")
    required_status_absent = candidate_for_text_in_region(question, "status absent", "required status absent")
    required_absent = candidate_for_text_in_region(question, "absent", "required status absent")
    required_status_missing = candidate_for_text_in_region(question, "status missing", "required status missing")
    required_missing = candidate_for_text_in_region(question, "missing", "required status missing")

    assert omission_construction_region_reason(optional_memo_absent, detections)["rule"] == "FULL_OMISSION_CONSTRUCTION_REGION"
    assert optional_memo_absent.span_ref in reasons
    assert optional_absent.span_ref in reasons
    assert required_status_absent.span_ref not in reasons
    assert required_absent.span_ref not in reasons
    assert required_status_missing.span_ref not in reasons
    assert required_missing.span_ref not in reasons
    assert candidates["Absent"].span_ref not in reasons
    assert candidates["Missing"].span_ref not in reasons


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
    assert a6["stage7b_a5_wrong_span_choices_suppressed"] == 14
    assert a6["stage7b_a5_wrong_required_omit_structurally_impossible"] == 1
    assert a6["stage7b_a5_observed_wrong_decisions_addressed"] == 15
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
        [sys.executable, "-m", "pytest", "-q"],
        cwd=extract,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
