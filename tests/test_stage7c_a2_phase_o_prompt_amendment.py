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

from scripts.data.build_stage7c_a2_phase_o_prompt_amendment import (
    A2_PHASE_O_SYSTEM_PROMPT,
    A2_PHASE_O_USER_PROMPT_TEMPLATE,
    LOCK_FILE,
    OUT_DIR,
    PASS_STATUS,
    build,
    fresh_smoke_rows,
    input_hashes,
    prompt_hashes,
    sha256_file,
)
from scripts.data.build_stage7c_a2_prompt_package import build_package
from scripts.data.validate_stage7c_a2_phase_o_prompt_amendment import validate, write_report_and_update_lock


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7c_a2_tests"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
        "stage7c_a2_phase_o_prompt_feasibility_amendment",
        "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json",
        "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json",
        "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
        "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json",
        "stage7c_a1_v2_development_protocol/PROMPT_SERIALIZATION_SPEC.json",
        "stage7c_a1_v2_development_protocol/CHAT_TEMPLATE_RENDERING_SPEC.json",
        "stage7c_a1_v2_development_protocol/QUESTION_OFFSET_GUIDE_SPEC.json",
        "stage7c_a1_v2_development_protocol/PHASE_O_OUTPUT_VALIDATION_SPEC.json",
        "stage7d_v2_a1_implementation/STAGE7D_IMPLEMENTATION_LOCK.json",
        "scripts/data/build_stage7c_a2_phase_o_prompt_amendment.py",
        "scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py",
        "scripts/data/build_stage7c_a2_prompt_package.py",
        "tests/test_stage7c_a2_phase_o_prompt_amendment.py",
        "pyproject.toml",
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
    artifact = package / "stage7c_a2_phase_o_prompt_feasibility_amendment"
    lock = _read_json(artifact / LOCK_FILE)
    lock["artifact_hashes"][rel] = sha256_file(artifact / rel)
    _write_json(artifact / LOCK_FILE, lock)


def test_validator_passes_current_stage7c_a2_artifacts() -> None:
    report = validate(OUT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["fresh_smoke_count"] == 4


def test_lock_is_pass_and_records_no_execution() -> None:
    lock = _read_json(OUT_DIR / LOCK_FILE)
    assert lock["status"] == PASS_STATUS
    assert lock["phase_o_prompt_amended"] is True
    for key in ("model_called", "gpu_called", "train_dev_generation_run", "confirmation_481_evaluated", "live_sql_bench_gt_opened"):
        assert lock[key] is False
    assert lock["phase_m_changed"] is False
    assert lock["backend_changed"] is False
    assert lock["architecture_changed"] is False


def test_input_manifest_hashes_upstream_a1_and_stage7d_locks() -> None:
    manifest = _read_json(OUT_DIR / "STAGE7C_A2_INPUT_MANIFEST.json")
    assert manifest["input_hashes"] == input_hashes(ROOT)
    assert "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json" in manifest["input_hashes"]
    assert "stage7d_v2_a1_implementation/STAGE7D_IMPLEMENTATION_LOCK.json" in manifest["input_hashes"]


def test_phase_o_prompt_is_zero_shot_atomicity_clarification_only() -> None:
    spec = _read_json(OUT_DIR / "PHASE_O_PROMPT_SPEC_A2.json")
    phase_m = _read_json(ROOT / "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")
    assert spec["system_prompt"] == A2_PHASE_O_SYSTEM_PROMPT
    assert spec["user_prompt_template"] == A2_PHASE_O_USER_PROMPT_TEMPLATE
    assert spec["prompt_hashes"] == prompt_hashes(phase_m)
    assert spec["zero_shot"] is True
    assert spec["few_shot_examples_in_prompt"] is False
    assert spec["gold_visible"] is False
    assert any("smallest contiguous source span" in rule for rule in spec["atomicity_rules"])
    assert any("whole clause" in rule for rule in spec["atomicity_rules"])


def test_prompt_change_diff_changes_only_phase_o_prompt() -> None:
    diff = _read_json(OUT_DIR / "PROMPT_CHANGE_DIFF.json")
    assert diff["changed_component"] == "Phase O prompt only"
    assert diff["phase_o_system_prompt"]["old_sha256"] != diff["phase_o_system_prompt"]["new_sha256"]
    assert diff["phase_o_user_prompt_template"]["old_sha256"] != diff["phase_o_user_prompt_template"]["new_sha256"]
    assert diff["phase_m_system_prompt_sha256"]["changed"] is False
    assert diff["phase_m_user_prompt_template_sha256"]["changed"] is False
    assert diff["examples_added_to_prompt"] is False
    assert diff["schemas_changed"] is False
    assert diff["backend_changed"] is False
    assert diff["dataset_or_gold_changed"] is False


def test_fresh_smoke_set_is_locked_and_offsets_extract_expected_texts() -> None:
    rows = _read_jsonl(OUT_DIR / "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    assert rows == fresh_smoke_rows()
    assert len(rows) == 4
    assert sum(len(row["label_side_expected"]["phase_o"]["value_spans"]) == 2 for row in rows) == 2
    assert sum(len(row["label_side_expected"]["phase_o"]["value_spans"]) == 3 for row in rows) == 2
    assert {row["language"] for row in rows} == {"en", "zh"}
    for row in rows:
        assert row["sample_id"] not in {"stage7e0_ascii_smoke_0001", "stage7e0_unicode_smoke_0002"}
        spans = row["label_side_expected"]["phase_o"]["value_spans"]
        question = row["model_side_input"]["question"]
        extracted = [question[span["start_char"] : span["end_char"]] for span in spans]
        schema_columns = row["model_side_input"]["schema_inventory"]["columns"]
        expected_values = [row["label_side_expected"]["target_state"]["inserted_row"][column["column_name"]] for column in schema_columns]
        assert extracted == expected_values
        assert set(row["model_side_input"]) == {"question", "schema_inventory"}
        assert row["label_side_expected"]["model_side_visible"] is False
        assert row["locked_before_model_run"] is True


def test_fresh_smoke_set_locks_model_side_schema_db_and_phase_m_labels() -> None:
    rows = _read_jsonl(OUT_DIR / "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    expected_tables = {
        "stage7c_a2_fresh_en_two_value_0001": ("people", ["name", "salary"], ["TEXT", "INTEGER"]),
        "stage7c_a2_fresh_zh_two_value_0002": ("company", ["company_name", "employee_count"], ["TEXT", "INTEGER"]),
        "stage7c_a2_fresh_en_three_value_0003": ("people", ["name", "age", "city"], ["TEXT", "INTEGER", "TEXT"]),
        "stage7c_a2_fresh_zh_three_value_0004": ("employee", ["name", "age", "city"], ["TEXT", "INTEGER", "TEXT"]),
    }
    for row in rows:
        table_name, column_names, affinities = expected_tables[row["sample_id"]]
        schema = row["model_side_input"]["schema_inventory"]
        db_spec = row["synthetic_db_spec"]
        assert schema["tables"] == [{"table_ref": "TAB_1", "table_name": table_name}]
        assert [column["column_name"] for column in schema["columns"]] == column_names
        assert [column["source_type"] for column in schema["columns"]] == affinities
        assert db_spec["table"] == table_name
        assert [column["name"] for column in db_spec["columns"]] == column_names
        assert [column["source_type"] for column in db_spec["columns"]] == affinities
        assert db_spec["initial_rows"] == []
        expected_assignments = [
            {"slot_ref": f"SLOT_{index}", "evidence_ref": f"EV_{index}", "column_ref": f"COL_{index}"}
            for index in range(1, len(column_names) + 1)
        ]
        assert row["label_side_expected"]["phase_m"] == {"operation": "INSERT", "table_ref": "TAB_1", "assignments": expected_assignments}


def test_smoke_lock_hash_scope_includes_full_model_and_label_fixture() -> None:
    lock = _read_json(OUT_DIR / "SMOKE_SET_LOCK.json")
    assert "model_side_input.question" in lock["full_fixture_hash_scope"]
    assert "model_side_input.schema_inventory" in lock["full_fixture_hash_scope"]
    assert "synthetic_db_spec" in lock["full_fixture_hash_scope"]
    assert "label_side_expected.phase_o" in lock["full_fixture_hash_scope"]
    assert "label_side_expected.phase_m" in lock["full_fixture_hash_scope"]
    assert "label_side_expected.target_state" in lock["full_fixture_hash_scope"]


def test_no_train_dev_tuning_audit_blocks_data_or_label_changes() -> None:
    audit = _read_json(OUT_DIR / "NO_TRAIN_DEV_TUNING_AUDIT.json")
    assert audit["status"] == "PASS"
    for key in ("model_called", "gpu_called", "train_dev_generation_run", "confirmation_481_evaluated", "live_sql_bench_gt_opened"):
        assert audit[key] is False
    for key in ("crudsql_train_outputs_inspected_for_prompt_tuning", "crudsql_dev_outputs_inspected_for_prompt_tuning", "gold_labels_modified", "datasets_modified", "metrics_modified", "backend_modified", "phase_m_modified", "architecture_modified"):
        assert audit[key] is False


def test_patch9_evidence_summary_localizes_phase_o_without_backend_reopen() -> None:
    evidence = _read_json(OUT_DIR / "PATCH9_EVIDENCE_SUMMARY.json")
    assert evidence["backend_status"]["answer_injection_audit_status"] == "PASS"
    assert evidence["backend_status"]["constraint_capacity_audit_status"] == "PASS"
    assert evidence["backend_status"]["phase_m_diagnostic_status"] == "PASS"
    assert evidence["backend_status"]["backend_supports_more_than_two_spans"] is True
    assert evidence["backend_status"]["finite_complete_object_enumeration"] is False
    assert evidence["decision"].startswith("do_not_patch_backend_further")
    assert {row["failure_type"] for row in evidence["observed_failures"]} == {
        "phase_o_semantic_span_not_atomic",
        "phase_o_invalid_or_non_atomic_offsets_after_json_schema_validation",
    }


def test_clean_rebuild_produces_identical_lock(workspace_tmp: Path) -> None:
    output = workspace_tmp / "stage7c_a2_phase_o_prompt_feasibility_amendment"
    build(output, force=True)
    report = validate(output)
    assert report["status"] == "PASS"
    write_report_and_update_lock(output, report)
    assert _read_json(output / LOCK_FILE) == _read_json(OUT_DIR / LOCK_FILE)


def test_builder_creates_pending_lock_before_validator(workspace_tmp: Path) -> None:
    output = workspace_tmp / "stage7c_a2_phase_o_prompt_feasibility_amendment"
    build(output, force=True)
    assert _read_json(output / LOCK_FILE)["status"] == "BUILT_PENDING_VALIDATION"
    report = validate(output)
    assert report["status"] == "PASS"


def test_validator_catches_prompt_atomicity_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "PHASE_O_PROMPT_SPEC_A2.json"
    spec = _read_json(path)
    spec["system_prompt"] = "Return spans."
    _write_json(path, spec)
    _refresh_artifact_hash(package, "PHASE_O_PROMPT_SPEC_A2.json")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "phase_o_system_prompt_text_mismatch" in report["violations"]


def test_validator_catches_fewshot_or_gold_visibility_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "PHASE_O_PROMPT_SPEC_A2.json"
    spec = _read_json(path)
    spec["few_shot_examples_in_prompt"] = True
    spec["gold_visible"] = True
    _write_json(path, spec)
    _refresh_artifact_hash(package, "PHASE_O_PROMPT_SPEC_A2.json")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "few_shot_examples_added" in report["violations"]
    assert "phase_o_gold_visible" in report["violations"]


def test_validator_catches_smoke_set_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "FRESH_SYNTHETIC_SMOKE_SET.jsonl"
    rows = _read_jsonl(path)
    rows[0]["label_side_expected"]["phase_o"]["value_spans"][0]["start_char"] = 0
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "fresh_smoke_rows_mismatch" in report["violations"]


def test_validator_catches_smoke_schema_column_name_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "FRESH_SYNTHETIC_SMOKE_SET.jsonl"
    rows = _read_jsonl(path)
    rows[0]["model_side_input"]["schema_inventory"]["columns"][0]["column_name"] = "full_name"
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "fresh_smoke_rows_mismatch" in report["violations"]


def test_validator_catches_smoke_schema_affinity_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "FRESH_SYNTHETIC_SMOKE_SET.jsonl"
    rows = _read_jsonl(path)
    rows[0]["model_side_input"]["schema_inventory"]["columns"][1]["source_type"] = "TEXT"
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "fresh_smoke_rows_mismatch" in report["violations"]


def test_validator_catches_smoke_db_spec_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "FRESH_SYNTHETIC_SMOKE_SET.jsonl"
    rows = _read_jsonl(path)
    rows[0]["synthetic_db_spec"]["table"] = "people_easy"
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "fresh_smoke_rows_mismatch" in report["violations"]


def test_validator_catches_phase_m_expected_mapping_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "FRESH_SYNTHETIC_SMOKE_SET.jsonl"
    rows = _read_jsonl(path)
    rows[0]["label_side_expected"]["phase_m"]["assignments"][0]["column_ref"] = "COL_2"
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "fresh_smoke_rows_mismatch" in report["violations"]


def test_validator_catches_no_tuning_audit_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "NO_TRAIN_DEV_TUNING_AUDIT.json"
    audit = _read_json(path)
    audit["train_dev_generation_run"] = True
    _write_json(path, audit)
    _refresh_artifact_hash(package, "NO_TRAIN_DEV_TUNING_AUDIT.json")
    report = validate(package / "stage7c_a2_phase_o_prompt_feasibility_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "forbidden_execution_flag:train_dev_generation_run" in report["violations"]


def test_self_contained_reviewer_package_clean_extraction(workspace_tmp: Path) -> None:
    if os.environ.get("STAGE7C_A2_IN_CLEAN_PACKAGE_TEST") == "1":
        return
    package = _copy_package_root(workspace_tmp)
    env = os.environ.copy()
    env["STAGE7C_A2_IN_CLEAN_PACKAGE_TEST"] = "1"
    commands = [
        [sys.executable, "scripts/data/build_stage7c_a2_phase_o_prompt_amendment.py", "--force"],
        [sys.executable, "scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py"],
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7c_a2_phase_o_prompt_amendment.py"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=package, env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr


def test_reviewer_package_zip_can_open(workspace_tmp: Path) -> None:
    result = build_package(0, workspace_tmp)
    reviewer_zip = Path(result["reviewer_zip"])
    assert reviewer_zip.exists()
    assert reviewer_zip.with_suffix(reviewer_zip.suffix + ".sha256").exists()
    with zipfile.ZipFile(reviewer_zip) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
    assert "stage7c_a2_phase_o_prompt_feasibility_amendment/PHASE_O_PROMPT_SPEC_A2.json" in names
    assert "scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py" in names
