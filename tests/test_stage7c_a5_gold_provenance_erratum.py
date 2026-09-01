from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.data.build_stage7c_a5_column_conditioned_phase_o_protocol import case_definitions, find_value_span
from scripts.data.build_stage7c_a5_gold_provenance_erratum import (
    PACKAGE_NAME,
    SERVER_TAR_SHA256,
    STAGE_NAME,
    build_stage,
    package_reviewer,
)
from scripts.data.validate_stage7c_a5_gold_provenance_erratum import validate


ROOT = Path(__file__).resolve().parents[1]
SERVER_TAR = ROOT / "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_duplicate_literal_without_explicit_span_is_forbidden() -> None:
    case = next(row for row in case_definitions() if row["sample_id"] == "stage7c_a5_primary_english_003")
    implicit_case = json.loads(json.dumps(case))
    implicit_case.pop("assigned_value_spans")
    with pytest.raises(ValueError, match="explicit source span required"):
        find_value_span(implicit_case, "passed", "7")


def test_erratum_build_replays_corrected_uet_result(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    summary = build_stage(stage_dir, SERVER_TAR)
    assert summary["source_tar_sha256"] == SERVER_TAR_SHA256
    assert summary["old_gold_primary_pass_count"] == "1/12"
    assert summary["corrected_primary_pass_count"] == "2/12"
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]
    replay_rows = read_jsonl(stage_dir / "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_RESULTS.jsonl")
    pass_ids = [row["sample_id"] for row in replay_rows if row["status"] == "PASS"]
    assert pass_ids == ["stage7c_a5_primary_english_003", "stage7c_a5_primary_english_012"]


def test_corrected_gold_refs_are_locked(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, SERVER_TAR)
    primary = {row["sample_id"]: row for row in read_jsonl(stage_dir / "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl")}
    diagnostic = {row["sample_id"]: row for row in read_jsonl(stage_dir / "CORRECTED_A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl")}
    assert primary["stage7c_a5_primary_english_003"]["label_side_expected"]["phase_o"]["column_span_refs"]["COL_4"] == "SPAN_0030"
    assert primary["stage7c_a5_primary_english_011"]["label_side_expected"]["phase_o"]["column_span_refs"]["COL_2"] == "SPAN_0019"
    assert diagnostic["stage7c_a5_fresh_english_011"]["label_side_expected"]["phase_o"]["column_span_refs"]["COL_2"] == "SPAN_0021"
    audit = read_json(stage_dir / "DUPLICATE_LITERAL_GOLD_AUDIT.json")
    assert audit["duplicate_literal_count"] == 3
    assert audit["implicit_first_occurrence_forbidden_count"] == 0


def test_erratum_package_opens_and_contains_source_tar(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, SERVER_TAR)
    package_path = tmp_path / PACKAGE_NAME
    digest = package_reviewer(stage_dir, SERVER_TAR, package_path)
    assert len(digest) == 64
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
    assert "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz" in names
    assert f"{STAGE_NAME}/OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json" in names
    assert "scripts/data/build_stage7c_a5_gold_provenance_erratum.py" in names
