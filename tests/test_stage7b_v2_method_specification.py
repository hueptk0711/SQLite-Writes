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

from scripts.data.build_stage7b_v2_method_specification import STAGE7A_INPUTS, build_stage7b
from scripts.data.validate_stage7b_v2_method_specification import validate

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage7b_v2_method_specification"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7b_tests"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _copy_stage7b(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "stage7b_v2_method_specification"
    shutil.copytree(ARTIFACT_DIR, target)
    return target


def _copy_inputs_root(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "root"
    for rel in STAGE7A_INPUTS:
        source = ROOT / rel
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return target


def _copy_self_contained_package(workspace_tmp: Path) -> Path:
    package = workspace_tmp / "Stage7B_V2_METHOD_SPECIFICATION_PATCH_TEST_PACKAGE"
    paths = [
        "scripts/data/build_stage7b_v2_method_specification.py",
        "scripts/data/validate_stage7b_v2_method_specification.py",
        "tests/test_stage7b_v2_method_specification.py",
        "stage7b_v2_method_specification",
        *STAGE7A_INPUTS,
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


def _refresh_lock_hash(artifact: Path, rel: str) -> None:
    lock_path = artifact / "STAGE7B_V2_SPECIFICATION_LOCK.json"
    lock = _read_json(lock_path)
    lock["artifact_hashes"][rel] = _sha256_file(artifact / rel)
    _write_json(lock_path, lock)


def test_validator_passes_current_stage7b_artifacts() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["stage7a_hashes_recomputed"] is True
    assert report["traceability_validated"] is True


def test_input_manifest_locks_stage7a_patch1_artifacts() -> None:
    manifest = _read_json(ARTIFACT_DIR / "STAGE7B_INPUT_MANIFEST.json")
    assert manifest["stage7a_locked"] is True
    assert manifest["hash_policy"] == "text_sha256_canonical_lf"
    assert manifest["input_hashes"]["stage7a_formal_failure_analysis/STAGE7A_FAILURE_ANALYSIS_LOCK.json"] == _sha256_file(
        ROOT / "stage7a_formal_failure_analysis" / "STAGE7A_FAILURE_ANALYSIS_LOCK.json"
    )


def test_design_rationale_uses_stage7a_evidence_counts_without_accuracy_target() -> None:
    rationale = _read_json(ARTIFACT_DIR / "V2_DESIGN_RATIONALE.json")
    evidence = rationale["stage7a_evidence_summary"]
    assert evidence["final_n"] == 481
    assert evidence["pipeline_counts"] == {"parse": 2, "state_mismatch": 43, "verification": 436}
    assert evidence["verification_direct_families"] == {
        "invalid_reference": 190,
        "normalization": 133,
        "operation_semantics": 204,
        "slot_or_update_completeness": 6,
    }
    assert all("expected accuracy" not in item.casefold() for item in rationale["design_principles"])


def test_architecture_freezes_five_core_components_and_no_repair_dependency() -> None:
    spec = _read_json(ARTIFACT_DIR / "V2_ARCHITECTURE_SPEC.json")
    components = [row["component"] for row in spec["core_pipeline"]]
    assert components == [
        "operation_conditioning",
        "slot_grounded_minimal_ir",
        "deterministic_typed_materialization",
        "semantic_completeness_verification",
        "deterministic_sqlite_compilation_preflight",
    ]
    assert spec["primary_v2_depends_on_repair"] is False
    assert spec["v2_implemented"] is False


def test_operation_conditioning_has_operation_specific_allowed_fields() -> None:
    spec = _read_json(ARTIFACT_DIR / "OPERATION_CONDITIONING_SPEC.json")
    assert spec["operation_classes"] == ["INSERT", "UPDATE", "DELETE", "UPSERT"]
    assert "conflict_target_ref" not in spec["operation_specific_allowed_fields"]["INSERT"]
    assert "assignments" not in spec["operation_specific_allowed_fields"]["DELETE"]
    assert "conflict_target_ref" in spec["operation_specific_allowed_fields"]["UPSERT"]


def test_slot_grounded_ir_keeps_llm_decisions_minimal() -> None:
    spec = _read_json(ARTIFACT_DIR / "SLOT_GROUNDED_IR_SPEC.json")
    assert "column_ref_to_evidence_ref_mapping" in spec["llm_decisions"]
    assert "SQL syntax" in spec["not_llm_decisions"]
    assert "normalization_function" in spec["not_llm_decisions"]


def test_reference_constraint_forbids_unrestricted_identifiers_and_silent_repair() -> None:
    spec = _read_json(ARTIFACT_DIR / "REFERENCE_CONSTRAINT_SPEC.json")
    assert spec["unrestricted_reference_ids_allowed"] is False
    assert spec["invalid_reference_policy"] == "deterministic rejection; no silent fuzzy correction"
    assert set(spec["inventories"]) >= {"tables", "columns", "evidence"}


def test_typed_materialization_removes_normalization_from_llm_output() -> None:
    spec = _read_json(ARTIFACT_DIR / "TYPED_MATERIALIZATION_SPEC.json")
    assert spec["llm_normalization_decisions_allowed"] is False
    assert spec["unsafe_materialization_policy"] == "reject; do not hallucinate conversion"
    assert {"schema_type": "TEXT", "raw_evidence": "12.50", "materialized_value": "12.50"} in spec["examples"]


def test_completeness_verification_defines_semantic_slot_coverage() -> None:
    spec = _read_json(ARTIFACT_DIR / "COMPLETENESS_VERIFICATION_SPEC.json")
    assert "all_required_slots_mapped" in spec["checks"]
    assert "no_unjustified_extra_slots" in spec["checks"]
    assert spec["coverage_metric"] == "semantic_slots_mapped / semantic_slots_required"


def test_representation_contract_uses_operation_specific_json_schema() -> None:
    spec = _read_json(ARTIFACT_DIR / "REPRESENTATION_CONTRACT_SPEC.json")
    assert "operation-specific required fields" in spec["contract"]
    assert "additionalProperties=false" in spec["contract"]
    assert spec["constrained_structured_output_preferred"] is True


def test_abstention_policy_does_not_claim_stage7a_true_ambiguity_support() -> None:
    spec = _read_json(ARTIFACT_DIR / "ABSTENTION_POLICY_SPEC.json")
    assert spec["true_ambiguity_direct_support_from_stage7a"] == 0
    assert "schema_nonconformance" in spec["allowed_reasons"]


def test_ablation_registration_freezes_full_and_four_component_removals() -> None:
    spec = _read_json(ARTIFACT_DIR / "ABLATION_REGISTRATION.json")
    variants = {row["variant"] for row in spec["variants"]}
    assert variants == {"V2-FULL", "V2-A", "V2-B", "V2-C", "V2-D"}
    assert spec["status"] == "FROZEN_BEFORE_IMPLEMENTATION"
    assert any(row.get("remove_component") == "operation_conditioning" and row.get("stage7a_direct_support") == 204 for row in spec["variants"])


def test_development_data_policy_protects_481_and_external_benchmark() -> None:
    policy = _read_json(ARTIFACT_DIR / "DEVELOPMENT_DATA_POLICY.json")
    assert policy["allowed_for_v2_development"] == ["CRUDSQL train Create"]
    assert policy["allowed_for_selection_tuning"] == ["CRUDSQL dev Create"]
    assert policy["performance_tuning_on_481_allowed"] is False
    assert "current 481 CRUDSQL Create test analyzed in Stage6/Stage7A" in policy["forbidden_for_selection_tuning"]
    assert policy["untouched_external_benchmark"] == ["LiveSQLBench SQLite"]


def test_design_traceability_maps_every_core_component_to_stage7a_evidence() -> None:
    trace = _read_json(ARTIFACT_DIR / "DESIGN_TO_EVIDENCE_TRACEABILITY.json")
    rows = {row["component"]: row for row in trace["entries"]}
    assert trace["accuracy_target_registered"] is False
    assert rows["operation_conditioning"]["stage7a_direct_support"]["sample_count"] == 204
    assert rows["constrained_reference_selection"]["stage7a_direct_support"]["sample_count"] == 190
    assert rows["deterministic_typed_materialization"]["stage7a_direct_support"]["sample_count"] == 133
    assert rows["semantic_completeness_verification"]["stage7a_direct_support"]["sample_count"] == 6
    assert rows["representation_schema_contract"]["stage7a_direct_support"]["sample_count"] == 2
    assert rows["explicit_abstention_for_true_ambiguity"]["stage7a_direct_support"]["sample_count"] == 0


def test_ir_schemas_are_operation_specific_and_closed() -> None:
    for name, operation in {
        "insert_ir.schema.json": "INSERT",
        "update_ir.schema.json": "UPDATE",
        "delete_ir.schema.json": "DELETE",
        "upsert_ir.schema.json": "UPSERT",
    }.items():
        schema = _read_json(ARTIFACT_DIR / "schemas" / name)
        assert schema["properties"]["operation"]["const"] == operation
        assert schema["additionalProperties"] is False
        assert "normalization" not in json.dumps(schema, sort_keys=True).casefold()
    insert_schema = _read_json(ARTIFACT_DIR / "schemas" / "insert_ir.schema.json")
    assert "conflict_target_ref" not in insert_schema["properties"]
    delete_schema = _read_json(ARTIFACT_DIR / "schemas" / "delete_ir.schema.json")
    assert "assignments" not in delete_schema["properties"]


def test_validator_detects_traceability_tampering_even_if_hash_updated(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "DESIGN_TO_EVIDENCE_TRACEABILITY.json"
    payload = _read_json(path)
    payload["entries"][1]["stage7a_direct_support"]["sample_count"] = 233
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "DESIGN_TO_EVIDENCE_TRACEABILITY.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "reference_direct_support_not_190" in report["violations"]


def test_validator_detects_schema_that_allows_insert_conflict_target(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "schemas" / "insert_ir.schema.json"
    payload = _read_json(path)
    payload["properties"]["conflict_target_ref"] = {"type": "string"}
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "schemas/insert_ir.schema.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "insert_schema_has_non_insert_fields" in report["violations"]


def test_validator_detects_llm_normalization_allowed(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "TYPED_MATERIALIZATION_SPEC.json"
    payload = _read_json(path)
    payload["llm_normalization_decisions_allowed"] = True
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "TYPED_MATERIALIZATION_SPEC.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "llm_normalization_allowed" in report["violations"]


def test_validator_detects_481_tuning_allowed(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "DEVELOPMENT_DATA_POLICY.json"
    payload = _read_json(path)
    payload["performance_tuning_on_481_allowed"] = True
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "DEVELOPMENT_DATA_POLICY.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "481_tuning_allowed" in report["violations"]


def test_validator_detects_stage7a_input_hash_mutation(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    root = _copy_inputs_root(workspace_tmp)
    path = root / "stage7a_formal_failure_analysis" / "PIPELINE_FAILURE_SUMMARY.json"
    payload = _read_json(path)
    payload["pipeline_failure_counts"]["parse"] = 3
    _write_json(path, payload)
    report = validate(artifact, root)
    assert report["status"] == "FAIL"
    assert "manifest_input_hashes_mismatch" in report["violations"]
    assert "lock_input_hashes_mismatch" in report["violations"]
    assert "stage7a_pipeline_counts_changed" in report["violations"]


def test_builder_does_not_rewrite_stage7a_inputs(workspace_tmp: Path) -> None:
    tracked = [ROOT / rel for rel in STAGE7A_INPUTS]
    before = {path: _sha256_file(path) for path in tracked}
    build_stage7b(workspace_tmp / "generated_stage7b")
    after = {path: _sha256_file(path) for path in tracked}
    assert after == before


def test_no_model_gpu_experiment_or_v2_implementation_in_stage7b_scripts() -> None:
    combined = "\n".join(
        [
            (ROOT / "scripts" / "data" / "build_stage7b_v2_method_specification.py").read_text(encoding="utf-8").casefold(),
            (ROOT / "scripts" / "data" / "validate_stage7b_v2_method_specification.py").read_text(encoding="utf-8").casefold(),
        ]
    )
    forbidden = ("torch", "cuda", "transformers", "model.generate", "operation_classifier.py", "load_livesqlbench", "accuracy =")
    assert all(token not in combined for token in forbidden)


def test_self_contained_reviewer_package_clean_extraction_runs(workspace_tmp: Path) -> None:
    if os.environ.get("STAGE7B_SKIP_CLEAN_PACKAGE_TEST") == "1":
        pytest.skip("Nested clean-extraction package test disabled.")
    package = _copy_self_contained_package(workspace_tmp)
    zip_path = workspace_tmp / "stage7b_package.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())
    extract_dir = workspace_tmp / "clean_extract"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        archive.extractall(extract_dir)
    env = os.environ.copy()
    env["STAGE7B_SKIP_CLEAN_PACKAGE_TEST"] = "1"
    validation = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7b_v2_method_specification.py"],
        cwd=extract_dir,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert '"status": "PASS"' in validation.stdout
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7b_v2_method_specification.py"],
        cwd=extract_dir,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "[100%]" in tests.stdout
