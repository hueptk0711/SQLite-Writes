from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from scripts.data.build_stage7a_formal_failure_analysis import (
    FINAL_N,
    INPUTS,
    build_stage7a,
)
from scripts.data.validate_stage7a_formal_failure_analysis import validate

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage7a_formal_failure_analysis"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7a_tests"

EXPECTED_PIPELINE_COUNTS = {"parse": 2, "state_mismatch": 43, "verification": 436}
EXPECTED_VERIFICATION_PREVALENCE = {
    "invalid_reference": 190,
    "normalization": 133,
    "operation_semantics": 204,
    "slot_or_update_completeness": 6,
}
EXPECTED_COMBINATIONS = {
    "invalid_reference": 99,
    "invalid_reference+normalization": 51,
    "invalid_reference+normalization+slot_or_update_completeness": 5,
    "invalid_reference+operation_semantics": 34,
    "invalid_reference+slot_or_update_completeness": 1,
    "normalization": 76,
    "normalization+operation_semantics": 1,
    "operation_semantics": 169,
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.fixture
def workspace_tmp(request) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
    target = TEST_TMP_ROOT / f"{safe_name}_{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=False)
    try:
        yield target
    finally:
        resolved = target.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)


def _copy_stage7a(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "stage7a_formal_failure_analysis"
    shutil.copytree(ARTIFACT_DIR, target)
    return target


def _copy_inputs_root(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "root"
    for rel in INPUTS:
        source = ROOT / rel
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return target


def _copy_self_contained_package(workspace_tmp: Path) -> Path:
    package = workspace_tmp / "Stage7A_FORMAL_FAILURE_ANALYSIS_PATCH_TEST_PACKAGE"
    paths = [
        "scripts/data/build_stage7a_formal_failure_analysis.py",
        "scripts/data/validate_stage7a_formal_failure_analysis.py",
        "tests/test_stage7a_formal_failure_analysis.py",
        "stage7a_formal_failure_analysis",
        *INPUTS,
    ]
    for rel in paths:
        source = ROOT / rel
        dest = package / rel
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    return package


def test_validator_passes_current_artifacts() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["records_recomputed"] is True
    assert report["arm_equivalence_recomputed"] is True


def test_failure_records_cover_481_unique_samples_with_source_group() -> None:
    records = _read_jsonl(ARTIFACT_DIR / "FAILURE_RECORDS.jsonl")
    ids = [row["stage6_sample_id"] for row in records]
    assert len(records) == FINAL_N
    assert len(set(ids)) == FINAL_N
    assert all(row["source_group"] for row in records)
    assert all(row["representative_arm"] == "d_f_g1_vnext" for row in records)


def test_pipeline_failure_counts_are_frozen_acceptance_counts() -> None:
    summary = _read_json(ARTIFACT_DIR / "PIPELINE_FAILURE_SUMMARY.json")
    assert summary["pipeline_failure_counts"] == EXPECTED_PIPELINE_COUNTS
    assert summary["final_n"] == FINAL_N


def test_verification_family_prevalence_and_error_codes_are_recomputed_targets() -> None:
    summary = _read_json(ARTIFACT_DIR / "VERIFICATION_FAILURE_SUMMARY.json")
    assert summary["verification_failure_n"] == 436
    assert summary["all_verification_failures_accounted_for"] is True
    assert summary["root_cause_family_prevalence"] == EXPECTED_VERIFICATION_PREVALENCE
    assert summary["error_code_counts"]["NEEDS_CLARIFICATION"] == 204
    assert summary["error_code_counts"]["UNKNOWN_CONSTRAINT_ID"] == 155


def test_verification_overlap_combinations_match_record_level_multilabel_counts() -> None:
    combinations = _read_json(ARTIFACT_DIR / "FAILURE_COMBINATION_COUNTS.json")
    observed = {row["root_cause_combination"]: row["sample_count"] for row in combinations["combination_counts"]}
    assert observed == EXPECTED_COMBINATIONS
    assert combinations["total"] == 436


def test_overlap_matrix_diagonal_matches_family_prevalence() -> None:
    matrix = _read_json(ARTIFACT_DIR / "FAILURE_OVERLAP_MATRIX.json")["matrix"]
    for label, expected in EXPECTED_VERIFICATION_PREVALENCE.items():
        assert matrix[label][label] == expected
    assert matrix["invalid_reference"]["normalization"] == 56
    assert matrix["invalid_reference"]["operation_semantics"] == 34


def test_mpfs_arm_equivalence_is_semantic_and_complete() -> None:
    audit = _read_json(ARTIFACT_DIR / "MPFS_ARM_EQUIVALENCE_AUDIT.json")
    assert audit["status"] == "PASS"
    assert audit["difference_count"] == 0
    assert audit["checked_sample_count"] == FINAL_N
    assert audit["verification_error_fields_compared"] == ["error_code", "path", "message"]


def test_parse_failures_are_traced_to_raw_generation() -> None:
    rows = _read_jsonl(ARTIFACT_DIR / "PARSE_FAILURE_ANALYSIS.jsonl")
    assert [row["stage6_sample_id"] for row in rows] == ["stage6_crudsql_0102", "stage6_crudsql_0193"]
    assert all(row["parse_failure_type"] == "schema_mismatch_valid_json" for row in rows)
    assert all(row["valid_json_after_fence_strip"] is True for row in rows)
    assert all(row["hit_max_new_tokens"] is False for row in rows)


def test_state_mismatches_all_have_allowed_subtypes_or_explicit_unresolved() -> None:
    rows = _read_jsonl(ARTIFACT_DIR / "STATE_MISMATCH_ANALYSIS.jsonl")
    allowed = {
        "missing_assignment_or_under_write",
        "wrong_value_or_evidence",
        "wrong_target_column",
        "wrong_row_or_cardinality",
        "extra_assignment_or_over_write",
        "wrong_operation_or_conflict_semantics",
        "unresolved_other",
    }
    assert len(rows) == 43
    assert all(row["state_mismatch_subtypes"] for row in rows)
    assert all(set(row["state_mismatch_subtypes"]) <= allowed for row in rows)


def test_design_traceability_is_requirement_only_with_no_v2_code() -> None:
    traceability = _read_json(ARTIFACT_DIR / "DESIGN_REQUIREMENT_TRACEABILITY.json")
    labels = {row["root_cause_label"] for row in traceability["traceability"]}
    assert traceability["status"] == "DESIGN_REQUIREMENTS_ONLY"
    assert traceability["no_v2_implementation"] is True
    assert labels >= set(EXPECTED_VERIFICATION_PREVALENCE)


def test_input_manifest_hashes_include_stage6k_lock() -> None:
    manifest = _read_json(ARTIFACT_DIR / "STAGE7A_INPUT_MANIFEST.json")
    rel = "stage6_frozen_statistical_analysis/STAGE6K_STATISTICAL_LOCK.json"
    assert manifest["input_hashes"][rel] == _sha256_file(ROOT / rel)


def test_validator_detects_artifact_hash_tampering(workspace_tmp: Path) -> None:
    artifact = _copy_stage7a(workspace_tmp)
    summary_path = artifact / "PIPELINE_FAILURE_SUMMARY.json"
    summary = _read_json(summary_path)
    summary["final_n"] = 480
    _write_json(summary_path, summary)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "lock_artifact_hashes_mismatch" in report["violations"]
    assert "pipeline_summary_recompute_mismatch" in report["violations"]


def test_validator_recomputes_records_not_just_hashes(workspace_tmp: Path) -> None:
    artifact = _copy_stage7a(workspace_tmp)
    records_path = artifact / "FAILURE_RECORDS.jsonl"
    records = _read_jsonl(records_path)
    records[0]["failure_stage"] = "verification"
    _write_jsonl(records_path, records)
    lock_path = artifact / "STAGE7A_FAILURE_ANALYSIS_LOCK.json"
    lock = _read_json(lock_path)
    lock["artifact_hashes"]["FAILURE_RECORDS.jsonl"] = _sha256_file(records_path)
    _write_json(lock_path, lock)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "failure_records_recompute_mismatch" in report["violations"]


def test_validator_detects_input_mutation_against_frozen_hashes(workspace_tmp: Path) -> None:
    artifact = _copy_stage7a(workspace_tmp)
    root = _copy_inputs_root(workspace_tmp)
    replay_path = root / "stage6_replay_evaluation" / "replay_outcomes" / "d_f_g1_vnext.jsonl"
    rows = _read_jsonl(replay_path)
    rows[0]["failure_stage"] = "verification"
    _write_jsonl(replay_path, rows)
    report = validate(artifact, root)
    assert report["status"] == "FAIL"
    assert "input_manifest_hashes_mismatch" in report["violations"]
    assert "lock_input_hashes_mismatch" in report["violations"]


def test_builder_does_not_rewrite_frozen_inputs(workspace_tmp: Path) -> None:
    tracked = [ROOT / rel for rel in INPUTS]
    before = {path: _sha256_file(path) for path in tracked}
    build_stage7a(workspace_tmp / "generated_stage7a")
    after = {path: _sha256_file(path) for path in tracked}
    assert after == before


def test_no_model_or_gpu_or_v2_implementation_in_stage7a_scripts() -> None:
    combined = "\n".join(
        [
            (ROOT / "scripts" / "data" / "build_stage7a_formal_failure_analysis.py").read_text(encoding="utf-8").casefold(),
            (ROOT / "scripts" / "data" / "validate_stage7a_formal_failure_analysis.py").read_text(encoding="utf-8").casefold(),
        ]
    )
    forbidden = ("torch", "cuda", "transformers", "openai", "model.generate", "operation_classifier.py")
    assert all(token not in combined for token in forbidden)


def test_self_contained_reviewer_package_clean_extraction_runs(workspace_tmp: Path) -> None:
    if os.environ.get("STAGE7A_SKIP_CLEAN_PACKAGE_TEST") == "1":
        pytest.skip("Nested clean-extraction package test disabled.")
    package = _copy_self_contained_package(workspace_tmp)
    zip_path = workspace_tmp / "stage7a_package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())
    extract_dir = workspace_tmp / "clean_extract"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        archive.extractall(extract_dir)
    env = os.environ.copy()
    env["STAGE7A_SKIP_CLEAN_PACKAGE_TEST"] = "1"
    validation = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7a_formal_failure_analysis.py"],
        cwd=extract_dir,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert '"status": "PASS"' in validation.stdout
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7a_formal_failure_analysis.py"],
        cwd=extract_dir,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "[100%]" in tests.stdout
