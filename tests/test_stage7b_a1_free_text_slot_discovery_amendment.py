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
    inventory_from_phase_o_spans,
    materializable_slot_audit,
    phase_o_schema,
    sha256_file,
    source_span_oracle_audit,
    validate_phase_o_spans,
    validate_question_identity,
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


def _schema_accepts(schema: dict, instance) -> bool:
    def check(node: dict, value) -> bool:
        if "enum" in node and value not in node["enum"]:
            return False
        expected_type = node.get("type")
        if expected_type == "object":
            if not isinstance(value, dict):
                return False
            required = set(node.get("required", []))
            if not required.issubset(value):
                return False
            allowed = set(node.get("properties", {}))
            if node.get("additionalProperties") is False and not set(value).issubset(allowed):
                return False
            return all(check(node["properties"][key], value[key]) for key in value if key in node.get("properties", {}))
        if expected_type == "array":
            if not isinstance(value, list):
                return False
            if len(value) < node.get("minItems", 0):
                return False
            return all(check(node.get("items", {}), item) for item in value)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool) and value >= node.get("minimum", -(10**18))
        if expected_type == "string":
            if not isinstance(value, str):
                return False
            pattern = node.get("pattern")
            return pattern is None or re.fullmatch(pattern, value) is not None
        return True

    return check(schema, instance)


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


def test_source_span_oracle_audit_recomputes_ceiling() -> None:
    oracle = _read_json(ARTIFACT_DIR / "SOURCE_SPAN_ORACLE_AUDIT.json")
    assert oracle == source_span_oracle_audit()
    assert oracle["train"]["source_selectable_gold_value_count"] == 5295
    assert oracle["train"]["gold_assignment_count"] == 5407
    assert oracle["train"]["source_selectable_gold_value_rate"] == 0.979286
    assert oracle["train"]["samples_with_at_least_one_non_source_alignable_value"] == 93


def test_source_span_oracle_dev_recomputes_ceiling() -> None:
    oracle = _read_json(ARTIFACT_DIR / "SOURCE_SPAN_ORACLE_AUDIT.json")
    assert oracle["dev"]["source_selectable_gold_value_count"] == 810
    assert oracle["dev"]["gold_assignment_count"] == 845
    assert oracle["dev"]["source_selectable_gold_value_rate"] == 0.95858
    assert oracle["dev"]["samples_with_at_least_one_non_source_alignable_value"] == 33


def test_nonalignable_samples_are_retained_in_primary_denominator() -> None:
    oracle = _read_json(ARTIFACT_DIR / "SOURCE_SPAN_ORACLE_AUDIT.json")
    policy = _read_json(ARTIFACT_DIR / "NONALIGNABLE_SOURCE_SPAN_POLICY.json")
    assert oracle["dev"]["sample_count"] == 240
    assert oracle["dev"]["samples_with_at_least_one_non_source_alignable_value"] == 33
    assert oracle["nonalignable_policy"]["retain_in_primary_dev_denominator"] is True
    assert policy["retain_in_primary_dev_denominator"] is True
    assert policy["dev_eligible"] is True
    assert policy["diagnostic_flag"] == "source_gold_nonalignable_under_frozen_materializer"
    assert policy["modify_gold"] is False
    assert policy["add_post_hoc_normalization"] is False


def test_rationale_reopens_stage7b_not_stage7c_regex_patch() -> None:
    rationale = _read_json(ARTIFACT_DIR / "STAGE7B_A1_AMENDMENT_RATIONALE.json")
    assert rationale["not_a_stage7c_regex_patch"] is True
    assert "operation plus grounded atomic semantic span selection" in rationale["decision"]
    assert rationale["empirical_trigger_from_stage7c_patch2"]["materializable_dev_coverage"]["rate"] == 0.304142
    assert rationale["source_span_oracle_ceiling"]["dev"]["source_selectable"] == 810


def test_phase_o_schema_is_offsets_only_without_model_value_text() -> None:
    schema = _read_json(ARTIFACT_DIR / "PHASE_O_JSON_SCHEMA.json")
    assert schema == phase_o_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["operation", "value_spans"]
    assert schema["properties"]["value_spans"]["minItems"] == 1
    span_item = schema["properties"]["value_spans"]["items"]
    assert span_item["required"] == ["start_char", "end_char"]
    assert "span_ref" not in span_item["properties"]
    text = canonical_json(schema)
    assert '"span_ref"' not in text
    assert '"text"' not in text
    assert '"value"' not in text
    assert '"raw_value"' not in text


def test_phase_o_schema_rejects_model_generated_span_ref() -> None:
    schema = phase_o_schema()
    instance = {"operation": "INSERT", "value_spans": [{"span_ref": "SPAN_1", "start_char": 0, "end_char": 1}]}
    assert not _schema_accepts(schema, instance)


def test_phase_o_schema_rejects_empty_value_spans() -> None:
    schema = phase_o_schema()
    assert not _schema_accepts(schema, {"operation": "INSERT", "value_spans": []})


def test_phase_o_schema_accepts_offset_only_nonempty_spans() -> None:
    schema = phase_o_schema()
    assert _schema_accepts(schema, {"operation": "INSERT", "value_spans": [{"start_char": 0, "end_char": 1}]})


def test_span_validation_rejects_duplicate_nested_and_overlap() -> None:
    spec = _read_json(ARTIFACT_DIR / "SPAN_VALIDATION_SPEC.json")
    assert spec["span_text_source"] == "question[start_char:end_char] only"
    assert spec["model_emitted_text_allowed"] is False
    assert spec["model_generated_span_ids_allowed"] is False
    assert spec["offset_coordinate_system"] == "Python Unicode code-point indexing"
    assert spec["range_convention"] == "[start_char, end_char)"
    assert spec["phase_o_question_string"] == "exact original question string Q"
    assert spec["normalization_before_offset_validation"] == "none"
    assert spec["duplicate_span_policy"] == "reject"
    assert spec["nested_span_policy"] == "reject"
    assert spec["partial_overlap_policy"] == "reject"
    assert spec["same_span_selected_twice_policy"] == "reject"
    assert spec["inventory_assignment_order"] == "sort_by_start_char_then_end_char"


def test_duplicate_exact_offsets_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate_exact_span_offsets"):
        validate_phase_o_spans("abcdef", [{"start_char": 1, "end_char": 3}, {"start_char": 1, "end_char": 3}])


def test_nested_offsets_are_rejected() -> None:
    with pytest.raises(ValueError, match="nested_or_partially_overlapping_spans"):
        validate_phase_o_spans("abcdef", [{"start_char": 1, "end_char": 5}, {"start_char": 2, "end_char": 3}])


def test_partial_overlap_offsets_are_rejected() -> None:
    with pytest.raises(ValueError, match="nested_or_partially_overlapping_spans"):
        validate_phase_o_spans("abcdef", [{"start_char": 1, "end_char": 4}, {"start_char": 3, "end_char": 5}])


def test_unicode_chinese_codepoint_slicing_is_frozen() -> None:
    question = "昆明中心假日酒店位于云南省"
    start = question.index("中心")
    spans = validate_phase_o_spans(question, [{"start_char": start, "end_char": start + len("中心")}])
    assert spans[0]["text"] == "中心"


def test_mixed_ascii_chinese_codepoint_slicing_is_frozen() -> None:
    question = "Alice住在北京20岁"
    start = question.index("北京")
    spans = validate_phase_o_spans(question, [{"start_char": start, "end_char": start + len("北京")}])
    assert spans[0]["text"] == "北京"


def test_out_of_order_spans_create_deterministic_slot_refs() -> None:
    inventory = inventory_from_phase_o_spans(
        "Alice住在北京20岁",
        [{"start_char": 9, "end_char": 11}, {"start_char": 0, "end_char": 5}, {"start_char": 7, "end_char": 9}],
    )
    evidence = inventory["evidence_inventory"]["evidence"]
    slots = inventory["semantic_slot_inventory"]["slots"]
    assert [entry["text"] for entry in evidence] == ["Alice", "北京", "20"]
    assert [entry["span_ref"] for entry in evidence] == ["SPAN_1", "SPAN_2", "SPAN_3"]
    assert [slot["slot_ref"] for slot in slots] == ["SLOT_1", "SLOT_2", "SLOT_3"]
    assert all(slot["required"] is True for slot in slots)


def test_input_normalization_change_is_rejected() -> None:
    with pytest.raises(ValueError, match="exact original question"):
        validate_question_identity("  北京", "北京")


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
    assert ablation["everything_else_held_constant"] is True
    assert ablation["hidden_third_model_call_allowed"] is False
    assert "V2-D_MINUS_COMPLETENESS_VERIFICATION" in ablation["amended_variants"]
    assert "V2-O_MINUS_SPAN_SELECTION" in ablation["amended_variants"]
    assert not (ARTIFACT_DIR / "ABALATION_AMENDMENT.json").exists()


def test_v2a_a1_intervention_is_fully_frozen() -> None:
    ablation = _read_json(ARTIFACT_DIR / "ABLATION_AMENDMENT.json")
    v2a = ablation["amended_variants"]["V2-A_MINUS_OPERATION_CONDITIONING"]
    assert v2a["phase_o_a_responsibilities"] == ["semantic_span_selection"]
    assert v2a["phase_m_a_responsibilities"] == ["operation_prediction", "slot_to_column_or_predicate_mapping"]
    assert v2a["total_model_calls"] == 2
    assert v2a["schemas"] == "single unified operation-unconditioned Phase M schema"
    assert "typed_materializer" in v2a["unchanged_from_v2_full_a1"]


def test_span_selection_diagnostic_is_not_confirmatory_ablation() -> None:
    ablation = _read_json(ARTIFACT_DIR / "ABLATION_AMENDMENT.json")
    v2o = ablation["amended_variants"]["V2-O_MINUS_SPAN_SELECTION"]
    assert v2o["diagnostic_only"] is True
    assert v2o["confirmatory_ablation_family_member"] is False
    assert v2o["p_value_baseline_allowed"] is False
    assert "V2-O_MINUS_SPAN_SELECTION" not in ablation["confirmatory_ablation_family"]


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
    assert "ablation_allows_hidden_third_call" in report["violations"]


def test_validator_catches_oracle_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "SOURCE_SPAN_ORACLE_AUDIT.json"
    oracle = _read_json(path)
    oracle["dev"]["source_selectable_gold_value_count"] = 845
    _write_json(path, oracle)
    _refresh_artifact_hash(package, "SOURCE_SPAN_ORACLE_AUDIT.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "source_span_oracle_audit_mismatch" in report["violations"]
    assert "dev_oracle_source_selectable_count_changed" in report["violations"]


def test_validator_catches_nonalignable_policy_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "NONALIGNABLE_SOURCE_SPAN_POLICY.json"
    policy = _read_json(path)
    policy["retain_in_primary_dev_denominator"] = False
    policy["add_post_hoc_normalization"] = True
    _write_json(path, policy)
    _refresh_artifact_hash(package, "NONALIGNABLE_SOURCE_SPAN_POLICY.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "nonalignable_dev_denominator_not_retained" in report["violations"]
    assert "nonalignable_policy_allows_post_hoc_normalization" in report["violations"]


def test_validator_catches_offset_contract_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7b_a1_free_text_slot_discovery_amendment" / "SPAN_VALIDATION_SPEC.json"
    spec = _read_json(path)
    spec["offset_coordinate_system"] = "UTF-16 code units"
    spec["normalization_policy"]["unicode_nfkc"] = True
    _write_json(path, spec)
    _refresh_artifact_hash(package, "SPAN_VALIDATION_SPEC.json")
    report = validate(package / "stage7b_a1_free_text_slot_discovery_amendment", root=package)
    assert report["status"] == "FAIL"
    assert "offset_coordinate_system_not_frozen" in report["violations"]
    assert "normalization_policy_not_all_false" in report["violations"]


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
