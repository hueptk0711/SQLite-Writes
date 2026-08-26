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

from scripts.data.audit_stage7c_dataset_splits import audit
from scripts.data.build_stage7c_v2_development_data_protocol import (
    DEFAULT_CRUDSQL_ROOT,
    EXPECTED_CREATE_COUNTS,
    LOCK_FILE,
    STAGE6_TEST_INPUTS,
    STAGE7B_INPUTS,
    build_stage7c,
    canonical_json,
    sha256_file,
    sha256_text,
)
from scripts.data.validate_stage7c_v2_development_data_protocol import PASS_STATUS, validate


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage7c_v2_development_data_protocol"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7c_tests"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def _copy_stage7c_root(workspace_tmp: Path) -> Path:
    package = workspace_tmp / "root"
    paths = [
        "stage7c_v2_development_data_protocol",
        "scripts/data/build_stage7c_v2_development_data_protocol.py",
        "scripts/data/audit_stage7c_dataset_splits.py",
        "scripts/data/validate_stage7c_v2_development_data_protocol.py",
        "tests/test_stage7c_v2_development_data_protocol.py",
        *STAGE7B_INPUTS,
        *STAGE6_TEST_INPUTS,
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


def _refresh_artifact_hash(package_root: Path, rel: str) -> None:
    lock_path = package_root / "stage7c_v2_development_data_protocol" / LOCK_FILE
    lock = _read_json(lock_path)
    lock["artifact_hashes"][rel] = sha256_file(package_root / "stage7c_v2_development_data_protocol" / rel)
    _write_json(lock_path, lock)


def test_validator_passes_current_stage7c_artifacts() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["train_create_count"] == 1760
    assert report["dev_create_count"] == 240
    assert report["model_called"] is False
    assert report["gpu_called"] is False


def test_lock_is_pass_after_actual_validator() -> None:
    lock = _read_json(ARTIFACT_DIR / LOCK_FILE)
    assert lock["status"] == PASS_STATUS
    assert lock["model_called"] is False
    assert lock["gpu_called"] is False
    assert lock["v2_implemented"] is False
    assert lock["experiment_run"] is False
    assert lock["live_sql_bench_gt_opened"] is False


def test_stage7b_inputs_are_hash_locked() -> None:
    manifest = _read_json(ARTIFACT_DIR / "STAGE7C_INPUT_MANIFEST.json")
    assert manifest["stage7b_locked"] is True
    for rel in STAGE7B_INPUTS:
        assert manifest["input_hashes"][rel] == sha256_file(ROOT / rel)
    stage7b_lock = _read_json(ROOT / "stage7b_v2_method_specification" / "STAGE7B_V2_SPECIFICATION_LOCK.json")
    assert stage7b_lock["status"] == "PASS_V2_METHOD_SPECIFICATION_LOCKED"


def test_raw_crudsql_train_dev_only_are_packaged() -> None:
    source = _read_json(ARTIFACT_DIR / "CRUDSQL_SOURCE_MANIFEST.json")
    assert source["source"]["commit"] == "63bfce67d8391185453a812751e115a499201363"
    assert source["included_splits"] == ["train", "dev"]
    assert source["excluded_splits"] == ["test"]
    assert not (ARTIFACT_DIR / "upstream_crudsql" / "data" / "test").exists()
    assert {row["source_path"] for row in source["files"]} == {
        "data/train/crud_train_sql.json",
        "data/train/crud_train_table.json",
        "data/train/train.db",
        "data/dev/crud_dev_sql.json",
        "data/dev/crud_dev_table.json",
        "data/dev/dev.db",
    }


def test_train_dev_create_counts_are_frozen() -> None:
    train = _read_jsonl(ARTIFACT_DIR / "TRAIN_CREATE_MANIFEST.jsonl")
    dev = _read_jsonl(ARTIFACT_DIR / "DEV_CREATE_MANIFEST.jsonl")
    audit_payload = _read_json(ARTIFACT_DIR / "DATASET_ELIGIBILITY_AUDIT.json")
    assert len(train) == EXPECTED_CREATE_COUNTS["train"] == 1760
    assert len(dev) == EXPECTED_CREATE_COUNTS["dev"] == 240
    assert audit_payload["eligible_create_counts"] == {"train": 1760, "dev": 240}
    assert audit_payload["source_split_counts"]["train"]["total_records"] == 7040
    assert audit_payload["source_split_counts"]["dev"]["total_records"] == 960


def test_model_side_input_excludes_gold_sql_operation_label_and_crudsql_conditions() -> None:
    rows = _read_jsonl(ARTIFACT_DIR / "TRAIN_CREATE_MANIFEST.jsonl")[:50] + _read_jsonl(ARTIFACT_DIR / "DEV_CREATE_MANIFEST.jsonl")[:50]
    forbidden = {"operation", "operation_label", "gold", "gold_sql", "crudsql_sql", "conds", "sel", "agg", "target_state", "post_state_hash", "dev_metric"}
    for row in rows:
        assert row["operation_label_for_evaluation_only"] == "CREATE"
        assert row["operation_label_visible_to_phase_o"] is False
        assert set(row["model_side_input"]) == {"question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"}
        text = canonical_json(row["model_side_input"]).casefold()
        for key in forbidden:
            assert f'"{key.casefold()}"' not in text


def test_semantic_slot_inventory_is_question_only_and_model_free() -> None:
    row = _read_jsonl(ARTIFACT_DIR / "TRAIN_CREATE_MANIFEST.jsonl")[0]
    slots = row["model_side_input"]["semantic_slot_inventory"]
    evidence_refs = {entry["evidence_ref"] for entry in row["model_side_input"]["evidence_inventory"]["evidence"]}
    assert row["semantic_slot_inventory_derivation_inputs"] == ["question"]
    assert slots["uses_gold_sql"] is False
    assert slots["model_call_used"] is False
    assert all(slot["required"] is True for slot in slots["slots"])
    assert all(slot["source"] == "deterministic_question_span" for slot in slots["slots"])
    assert {slot["evidence_ref"] for slot in slots["slots"]} <= evidence_refs


def test_model_side_input_hashes_are_canonical() -> None:
    for path in (ARTIFACT_DIR / "TRAIN_CREATE_MANIFEST.jsonl", ARTIFACT_DIR / "DEV_CREATE_MANIFEST.jsonl"):
        for row in _read_jsonl(path)[:25]:
            assert row["model_side_input_sha256"] == sha256_text(canonical_json(row["model_side_input"]))
            assert row["question_sha256"] == sha256_text(row["question"])


def test_split_contamination_audit_has_zero_overlap() -> None:
    report = _read_json(ARTIFACT_DIR / "SPLIT_CONTAMINATION_AUDIT.json")
    assert report["train_dev_question_hash_overlap"] == 0
    assert report["train_481_question_hash_overlap"] == 0
    assert report["dev_481_question_hash_overlap"] == 0
    assert report["train_dev_sample_id_overlap"] == 0
    assert report["test_question_text_imported"] is False
    assert report["model_input_leakage_counts"] == {}
    assert report["model_input_leakage_status"] == "PASS"


def test_generation_protocol_registers_no_hidden_model_calls_or_run() -> None:
    spec = _read_json(ARTIFACT_DIR / "GENERATION_PROTOCOL_SPEC.json")
    assert spec["core_v2_max_model_calls"] == 2
    assert spec["phase_o_model_calls"] == 1
    assert spec["phase_m_model_calls"] == 1
    assert spec["semantic_slot_inventory_model_call_allowed"] is False
    assert spec["v2_generation_run"] is False


def test_dev_selection_and_reserved_benchmark_policies_are_frozen() -> None:
    dev = _read_json(ARTIFACT_DIR / "DEV_SELECTION_PROTOCOL.json")
    reserved = _read_json(ARTIFACT_DIR / "RESERVED_BENCHMARK_POLICY.json")
    assert dev["primary_metric"] == "Target-State Accuracy"
    assert dev["selection_split"] == "CRUDSQL dev Create"
    assert dev["forbidden_selection_split"] == "current 481 CRUDSQL Create test"
    assert reserved["current_481_crudsql_create"] == "post_hoc_only_not_selection"
    assert reserved["crudsql_update_delete"] == "reserved_until_after_v2_freeze"
    assert reserved["livesqlbench_sqlite"] == "untouched_external_no_gt_access"
    assert reserved["live_sql_bench_gt_opened"] is False


def test_audit_script_payload_matches_protocol() -> None:
    payload = audit(ARTIFACT_DIR)
    assert payload["status"] == "PASS"
    assert payload["manifest_counts"] == {"train_create": 1760, "dev_create": 240}
    assert payload["model_input_leakage_counts"] == {}
    assert payload["contamination"]["train_481_question_hash_overlap"] == 0


def test_builder_creates_pending_lock_before_validator(workspace_tmp: Path) -> None:
    if not DEFAULT_CRUDSQL_ROOT.exists():
        pytest.skip("Frozen CRUDSQL source checkout is not available in this extraction.")
    output = workspace_tmp / "stage7c_v2_development_data_protocol"
    build_stage7c(output, force=True)
    assert _read_json(output / LOCK_FILE)["status"] == "BUILT_PENDING_VALIDATION"
    report = validate(output)
    assert report["status"] == "PASS"


def test_validator_catches_stage7b_hash_drift(workspace_tmp: Path) -> None:
    package = _copy_stage7c_root(workspace_tmp)
    spec_path = package / "stage7b_v2_method_specification" / "REFERENCE_CONSTRAINT_SPEC.json"
    spec = _read_json(spec_path)
    spec["tamper"] = True
    _write_json(spec_path, spec)
    report = validate(package / "stage7c_v2_development_data_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "input_manifest_hashes_mismatch" in report["violations"]
    assert "lock_input_hashes_mismatch" in report["violations"]


def test_validator_catches_train_manifest_count_tamper(workspace_tmp: Path) -> None:
    package = _copy_stage7c_root(workspace_tmp)
    manifest_path = package / "stage7c_v2_development_data_protocol" / "TRAIN_CREATE_MANIFEST.jsonl"
    rows = _read_jsonl(manifest_path)
    manifest_path.write_text("".join(canonical_json(row) + "\n" for row in rows[:-1]), encoding="utf-8")
    _refresh_artifact_hash(package, "TRAIN_CREATE_MANIFEST.jsonl")
    report = validate(package / "stage7c_v2_development_data_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "train_create_manifest_count_not_1760" in report["violations"]


def test_validator_catches_model_side_gold_leakage(workspace_tmp: Path) -> None:
    package = _copy_stage7c_root(workspace_tmp)
    manifest_path = package / "stage7c_v2_development_data_protocol" / "DEV_CREATE_MANIFEST.jsonl"
    rows = _read_jsonl(manifest_path)
    rows[0]["model_side_input"]["conds"] = rows[0]["label_side_bookkeeping"]["gold_annotation_sha256"]
    rows[0]["model_side_input_sha256"] = sha256_text(canonical_json(rows[0]["model_side_input"]))
    manifest_path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "DEV_CREATE_MANIFEST.jsonl")
    report = validate(package / "stage7c_v2_development_data_protocol", root=package)
    assert report["status"] == "FAIL"
    assert any("forbidden_model_key_present" in violation for violation in report["violations"])


def test_validator_catches_semantic_slot_gold_sql_dependency(workspace_tmp: Path) -> None:
    package = _copy_stage7c_root(workspace_tmp)
    manifest_path = package / "stage7c_v2_development_data_protocol" / "DEV_CREATE_MANIFEST.jsonl"
    rows = _read_jsonl(manifest_path)
    rows[0]["semantic_slot_inventory_derivation_inputs"] = ["question", "gold_sql"]
    rows[0]["model_side_input"]["semantic_slot_inventory"]["uses_gold_sql"] = True
    rows[0]["model_side_input_sha256"] = sha256_text(canonical_json(rows[0]["model_side_input"]))
    manifest_path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "DEV_CREATE_MANIFEST.jsonl")
    report = validate(package / "stage7c_v2_development_data_protocol", root=package)
    assert report["status"] == "FAIL"
    assert any("slot_derivation_not_question_only" in violation for violation in report["violations"])
    assert any("slots_not_recomputed" in violation for violation in report["violations"])


def test_validator_catches_hidden_third_model_call(workspace_tmp: Path) -> None:
    package = _copy_stage7c_root(workspace_tmp)
    path = package / "stage7c_v2_development_data_protocol" / "GENERATION_PROTOCOL_SPEC.json"
    spec = _read_json(path)
    spec["core_v2_max_model_calls"] = 3
    spec["semantic_slot_inventory_model_call_allowed"] = True
    _write_json(path, spec)
    _refresh_artifact_hash(package, "GENERATION_PROTOCOL_SPEC.json")
    report = validate(package / "stage7c_v2_development_data_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "hidden_third_model_call_allowed" in report["violations"]
    assert "slot_inventory_model_call_allowed" in report["violations"]


def test_validator_catches_test_split_copy(workspace_tmp: Path) -> None:
    package = _copy_stage7c_root(workspace_tmp)
    test_dir = package / "stage7c_v2_development_data_protocol" / "upstream_crudsql" / "data" / "test"
    test_dir.mkdir(parents=True)
    (test_dir / "crud_test_sql.json").write_text("[]\n", encoding="utf-8")
    report = validate(package / "stage7c_v2_development_data_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "test_split_copied_into_stage7c" in report["violations"]


def test_self_contained_reviewer_package_clean_extraction_runs(workspace_tmp: Path) -> None:
    if os.environ.get("STAGE7C_SKIP_CLEAN_PACKAGE_TEST") == "1":
        pytest.skip("Avoid recursive clean-package test.")
    package = _copy_stage7c_root(workspace_tmp)
    env = os.environ.copy()
    env["STAGE7C_SKIP_CLEAN_PACKAGE_TEST"] = "1"
    validator = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7c_v2_development_data_protocol.py"],
        cwd=package,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validator.returncode == 0, validator.stdout + validator.stderr
    assert '"status": "PASS"' in validator.stdout
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7c_v2_development_data_protocol.py"],
        cwd=package,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr


def test_reviewer_zip_can_open_if_created(workspace_tmp: Path) -> None:
    archive = workspace_tmp / "stage7c_smoke.zip"
    package = _copy_stage7c_root(workspace_tmp)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(package))
    with zipfile.ZipFile(archive) as zf:
        assert zf.testzip() is None
        assert "stage7c_v2_development_data_protocol/STAGE7C_DATA_PROTOCOL_LOCK.json" in zf.namelist()
