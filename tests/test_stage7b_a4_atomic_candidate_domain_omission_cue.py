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

from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import (  # noqa: E402
    PACKAGE_NAME,
    STAGE_NAME,
    build_stage,
    contains_omission_cue,
    detect_omission_constructions,
    generate_candidate_inventory,
    generic_atomic_dominance_reason,
    is_exact_omission_cue,
    package_reviewer,
    schema_label_aware_dominance_reason,
    suppressible_span_refs,
)
from scripts.data.validate_stage7b_a4_atomic_candidate_domain_omission_cue import validate  # noqa: E402


RAW_DIR = ROOT.parent.parent / "external_sources" / "gretel_synthetic_text_to_sql_740ab236"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def raw_dir_or_skip() -> Path:
    if not (RAW_DIR / "synthetic_text_to_sql_train.snappy.parquet").is_file():
        pytest.skip("local Gretel parquet raw_dir is not available")
    pytest.importorskip("pyarrow")
    return RAW_DIR


def by_text(question: str) -> dict[str, object]:
    return {candidate.text: candidate for candidate in generate_candidate_inventory(question)}


def test_atomic_dominance_suppresses_label_plus_identifier_but_keeps_atomic_child() -> None:
    question = "Insert loan_id LOAN-842, mass 0.42, station_id 0xA5C0DE."
    inventory = generate_candidate_inventory(question)
    candidates = by_text(question)
    reasons = suppressible_span_refs(inventory, {"loan id", "mass", "station id"}, [])

    broad = candidates["loan_id LOAN-842"]
    child = candidates["LOAN-842"]
    assert broad.span_ref in reasons
    assert reasons[broad.span_ref]["rule"] == "SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE"
    assert reasons[broad.span_ref]["dominant_child_text"] == "LOAN-842"
    assert child.span_ref not in reasons
    assert schema_label_aware_dominance_reason(candidates["mass 0.42"], inventory, {"mass"})["dominant_child_text"] == "0.42"
    assert schema_label_aware_dominance_reason(candidates["station_id 0xA5C0DE"], inventory, {"station id"})["dominant_child_text"] == "0xA5C0DE"


def test_atomic_dominance_does_not_drop_single_token_alphanumeric_values() -> None:
    question = "Insert quarter Q2, license 789B, sensor S102."
    inventory = generate_candidate_inventory(question)
    candidates = by_text(question)

    assert generic_atomic_dominance_reason(candidates["Q2"], inventory) is None
    assert generic_atomic_dominance_reason(candidates["789B"], inventory) is None
    assert generic_atomic_dominance_reason(candidates["S102"], inventory) is None


def test_schema_label_aware_rule_preserves_legitimate_compound_values() -> None:
    question = "Insert category Children's Rights, timestamp 2023-02-18 10:00:00, century 20th Century."
    inventory = generate_candidate_inventory(question)
    candidates = by_text(question)
    labels = {"category", "timestamp", "century"}

    assert schema_label_aware_dominance_reason(candidates["Children's Rights"], inventory, labels) is None
    assert schema_label_aware_dominance_reason(candidates["2023-02-18 10:00:00"], inventory, labels) is None
    assert schema_label_aware_dominance_reason(candidates["20th Century"], inventory, labels) is None


def test_omission_cue_detection_is_exact_for_suppression() -> None:
    assert is_exact_omission_cue(" omitted. ")
    assert is_exact_omission_cue('"not provided"')
    assert contains_omission_cue("contact omitted")
    assert not is_exact_omission_cue("contact omitted")
    assert not is_exact_omission_cue("blanket")


def test_context_aware_omission_preserves_quoted_cue_literals() -> None:
    question = 'Insert status "missing". phone not provided.'
    inventory = generate_candidate_inventory(question)
    detections = detect_omission_constructions(question, {"status", "phone"})
    reasons = suppressible_span_refs(inventory, {"status", "phone"}, detections)
    suppressed = {candidate.text for candidate in inventory if candidate.span_ref in reasons}

    assert "not provided" in suppressed
    assert "missing" not in suppressed


def test_build_and_validator_pass_on_728_design_train(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]

    current = read_json(stage_dir / "CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json")
    patch0 = read_json(stage_dir / "PATCH0_GENERIC_ATOMIC_DOMAIN_AUDIT.json")
    filtered = read_json(stage_dir / "SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json")
    comparison = read_json(stage_dir / "DOMAIN_COMPARISON_AUDIT.json")
    false_suppression = read_json(stage_dir / "FALSE_SUPPRESSION_AUDIT.json")
    cue = read_json(stage_dir / "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json")
    synthetic = read_json(stage_dir / "SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json")
    rows = read_jsonl(stage_dir / "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl")

    assert current["covered_assignment_count"] == 2252
    assert current["full_sample_covered_count"] == 724
    assert patch0["covered_assignment_count"] == 2249
    assert patch0["full_sample_covered_count"] == 721
    assert filtered["covered_assignment_count"] == 2252
    assert filtered["full_sample_covered_count"] == 724
    assert filtered["assignment_representability"] >= 0.99
    assert filtered["full_sample_representability"] >= 0.99
    assert filtered["suppressed_candidate_total"] > 0
    assert false_suppression["additional_assignment_losses"] == 0
    assert false_suppression["additional_full_sample_losses"] == 0
    assert comparison["threshold_decision"] == "PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW"
    assert comparison["method_freeze_authorized"] is False
    assert comparison["candidate_count_p95_delta"] < 0
    assert comparison["broader_containing_gold_total_delta"] < 0
    assert cue["true_assigned_value_exact_cue_count"] == 0
    assert cue["true_assigned_value_contains_cue_count"] == 0
    assert synthetic["status"] == "PASS"
    assert len(rows) == 728


def test_validator_rejects_threshold_tamper(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    filtered_path = stage_dir / "SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json"
    filtered = read_json(filtered_path)
    filtered["full_sample_covered_count"] = 700
    filtered["full_sample_representability"] = 700 / filtered["design_sample_count"]
    filtered_path.write_text(json.dumps(filtered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = validate(stage_dir)
    assert report["status"] == "FAIL"
    assert "schema_aware_full_sample_representability_below_threshold" in report["failures"]
    assert "derived_manifest_hash_mismatch:SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json" in report["failures"]


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
            "scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py",
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
