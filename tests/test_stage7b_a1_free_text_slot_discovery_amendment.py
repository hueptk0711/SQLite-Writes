from __future__ import annotations

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

from scripts.data.build_stage7b_a1_free_text_slot_discovery_amendment import (
    ARTIFACTS,
    LOCK_FILE,
    PHASE_O_MAX_NEW_TOKENS,
    STAGE7B_INPUTS,
    STAGE7C_PATCH2_INPUTS,
    build_stage7b_a1,
    canonical_json,
    materializable_slot_audit,
    phase_o_schema,
    sha256_file,
)
from scripts.data.validate_stage7b_a1_free_text_slot_discovery_amendment import PASS_STATUS, validate


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage7b_a1_free_text_slot_discovery_amendment"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7b_a1_tests"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _copy_package_root(workspace_tmp: Path) -> Path:
    package = workspace_tmp / "root"
    paths = [
        "stage7b_a1_free_text_slot_discovery_amendment",
        "scripts/data/build_stage7b_a1_free_text_slot_discovery_amendment.py",
        "scripts/data/validate_stage7b_a1_free_text_slot_discovery_amendment.py",
        "tests/test_stage7b_a1_free_text_slot_discovery_amendment.py",
        "pyproject.toml",
        *STAGE7B_INPUTS,
        *STAGE7C_PATCH2_INPUTS,
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


def _refresh_artifact_hash(package: Path, rel: str) -> None:
    artifact = package / "stage7b_a1_free_text_slot_discovery_amendment"
    lock = _read_json(artifact / LOCK_FILE)
    lock["artifact_hashes"][rel] = sha256_file(artifact / rel)
    _write_json(artifact / LOCK_FILE, lock)


def test_validator_passes_current_a1_artifacts() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["dev_materializable_candidate_coverage"] == 0.304142
    assert report["dev_required_slots_per_gold_assignment"] == 0.004734


def test_input_hashes_lock_stage7b_and_stage7c_patch2() -> None:
    manifest = _read_json(ARTIFACT_DIR / "STAGE7B_A1_INPUT_MANIFEST.json")
    for rel in STAGE7B_INPUTS + STAGE7C_PATCH2_INPUTS:
        assert manifest["input_hashes"][rel] == sha256_file(ROOT / rel)


def test_lock_is_pass_and_no_execution_flags() -> None:
    lock = _read_json(ARTIFACT_DIR / LOCK_FILE)
    assert lock["status"] == PASS_STATUS
    assert lock["phase_o_model_call_count"] == 1
    assert lock["phase_m_model_call_count"] == 1
    assert lock["total_model_call_count"] == 2
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        assert lock[key] is False


def test_materializable_audit_recomputes_patch2_limitation() -> None:
    audit = _read_json(ARTIFACT_DIR / "MATERIALIZABLE_SLOT_AUDIT.json")
    assert audit == materializable_slot_audit()
    assert audit["dev"]["substring_candidate_coverage_rate"] == 0.959763
    assert audit["dev"]["materializable_candidate_coverage_count"] == 257
    assert audit["dev"]["gold_assignment_count"] == 845
    assert audit["dev"]["materializable_candidate_coverage_rate"] == 0.304142
    assert audit["dev"]["samples_missing_materializable_candidate"] == 217
    assert audit["dev"]["required_slot_count"] == 4
    assert audit["train"]["materializable_candidate_coverage_count"] == 2366


def test_rationale_reopens_stage7b_not_stage7c_regex_patch() -> None:
    rationale = _read_json(ARTIFACT_DIR / "STAGE7B_A1_AMENDMENT_RATIONALE.json")
    assert rationale["not_a_stage7c_regex_patch"] is True
    assert "operation plus grounded atomic semantic span selection" in rationale["decision"]
    assert rationale["empirical_trigger_from_stage7c_patch2"]["materializable_dev_coverage"]["rate"] == 0.304142


def test_phase_o_schema_is_offsets_only_without_model_value_text() -> None:
    schema = _read_json(ARTIFACT_DIR / "PHASE_O_JSON_SCHEMA.json")
    assert schema == phase_o_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["operation", "value_spans"]
    span_item = schema["properties"]["value_spans"]["items"]
    assert span_item["required"] == ["span_ref", "start_char", "end_char"]
    text = canonical_json(schema)
    assert '"text"' not in text
    assert '"value"' not in text
    assert '"raw_value"' not in text


def test_span_validation_rejects_duplicate_nested_and_overlap() -> None:
    spec = _read_json(ARTIFACT_DIR / "SPAN_VALIDATION_SPEC.json")
    assert spec["span_text_source"] == "question[start_char:end_char] only"
    assert spec["model_emitted_text_allowed"] is False
    assert spec["duplicate_span_policy"] == "reject"
    assert spec["nested_span_policy"] == "reject"
    assert spec["partial_overlap_policy"] == "reject"
    assert spec["same_span_selected_twice_policy"] == "reject"


def test_evidence_and_slot_are_separated() -> None:
    spec = _read_json(ARTIFACT_DIR / "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json")
    assert spec["forbidden_mapping"] == "do_not_convert_every_context_evidence_span_into_SLOT"
    assert spec["broad_context_evidence_allowed"] is True
    assert spec["broad_context_evidence_required"] is False
    assert spec["semantic_slots_from_phase_o_only"] is True


def test_completeness_uses_phase_o_required_slots() -> None:
    spec = _read_json(ARTIFACT_DIR / "COMPLETENESS_AMENDED_SPEC.json")
    assert spec["required_set"] == "all SLOT_* created from accepted Phase O value_spans"
    assert spec["missing"] == "required_set - mapped_set"
    assert spec["extra"] == "mapped_set - allowed_slot_set"
    assert "Phase O misses" in spec["phase_o_span_recall_failure"]


def test_ablation_amendment_preserves_two_calls_and_adds_span_diagnostic() -> None:
    ablation = _read_json(ARTIFACT_DIR / "ABLATION_AMENDMENT.json")
    assert ablation == _read_json(ARTIFACT_DIR / "ABALATION_AMENDMENT.json")
    assert ablation["everything_else_held_constant"] is True
    assert ablation["hidden_third_model_call_allowed"] is False
    assert "V2-D_MINUS_COMPLETENESS_VERIFICATION" in ablation["amended_variants"]
    assert "V2-O_MINUS_SPAN_SELECTION" in ablation["amended_variants"]


def test_generation_capacity_amendment_is_pre_experiment_and_literal() -> None:
    capacity = _read_json(ARTIFACT_DIR / "GENERATION_CAPACITY_AMENDMENT.json")
    assert capacity["old_phase_o_max_new_tokens"] == 32
    assert capacity["new_phase_o_max_new_tokens"] == PHASE_O_MAX_NEW_TOKENS
    assert capacity["phase_m_max_new_tokens"] == 8192
    assert "c03e6d358207e414f1eca0bb1891e29f1db0e242" in capacity["model_revision_unchanged"]
    assert capacity["model_called"] is False
    assert capacity["gpu_called"] is False


def test_builder_creates_pending_lock_before_validator(workspace_tmp: Path) -> None:
    output = workspace_tmp / "stage7b_a1_free_text_slot_discovery_amendment"
    build_stage7b_a1(output, force=True)
    assert _read_json(output / LOCK_FILE)["status"] == "BUILT_PENDING_VALIDATION"
    report = validate(output)
    assert report["status"] == "PASS"


def test_validator_catches_materializable_audit_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "MATERIALIZABLE_SLOT_AUDIT.json"
    audit = _read_json(path)
    audit["dev"]["materializable_candidate_coverage_count"] = 845
    _write_json(path, audit)
    _refresh_artifact_hash(package, "MATERIALIZABLE_SLOT_AUDIT.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "materializable_slot_audit_mismatch" in report["violations"]
    assert "dev_materializable_count_changed" in report["violations"]


def test_validator_catches_phase_o_text_field_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "PHASE_O_JSON_SCHEMA.json"
    schema = _read_json(path)
    schema["properties"]["value_spans"]["items"]["properties"]["text"] = {"type": "string"}
    schema["properties"]["value_spans"]["items"]["required"].append("text")
    _write_json(path, schema)
    _refresh_artifact_hash(package, "PHASE_O_JSON_SCHEMA.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "phase_o_schema_changed" in report["violations"]
    assert "phase_o_schema_allows_model_emitted_text" in report["violations"]


def test_validator_catches_evidence_to_slot_regression(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json"
    spec = _read_json(path)
    spec["semantic_slots_from_phase_o_only"] = False
    spec["broad_context_evidence_required"] = True
    _write_json(path, spec)
    _refresh_artifact_hash(package, "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "semantic_slots_not_phase_o_only" in report["violations"]
    assert "broad_context_evidence_marked_required" in report["violations"]


def test_validator_catches_hidden_third_call_in_ablation(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "ABLATION_AMENDMENT.json"
    ablation = _read_json(path)
    ablation["hidden_third_model_call_allowed"] = True
    _write_json(path, ablation)
    _refresh_artifact_hash(package, "ABLATION_AMENDMENT.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "ablation_alias_mismatch" in report["violations"]
    assert "ablation_allows_hidden_third_call" in report["violations"]


def test_self_contained_reviewer_package_clean_extraction(workspace_tmp: Path) -> None:
    if os.environ.get("STAGE7B_A1_IN_CLEAN_PACKAGE_TEST") == "1":
        return
    package = _copy_package_root(workspace_tmp)
    env = os.environ.copy()
    env["STAGE7B_A1_IN_CLEAN_PACKAGE_TEST"] = "1"
    commands = [
        [sys.executable, "scripts/data/build_stage7b_a1_free_text_slot_discovery_amendment.py", "--force"],
        [sys.executable, "scripts/data/validate_stage7b_a1_free_text_slot_discovery_amendment.py"],
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7b_a1_free_text_slot_discovery_amendment.py"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=package, env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skipped" not in result.stdout.casefold()


def test_reviewer_zip_smoke(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    archive = workspace_tmp / "stage7b_a1.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package))
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        assert "stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json" in zf.namelist()
