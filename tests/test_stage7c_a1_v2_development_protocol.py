from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from scripts.data.build_stage7c_a1_v2_development_protocol import (
    ARTIFACTS,
    FROZEN_MODEL_CONFIG,
    LOCK_FILE,
    PROMPT_SERIALIZATION_CONTRACT,
    REUSED_STAGE7C_INPUTS,
    STAGE7B_A1_INPUTS,
    build_stage7c_a1,
    count_reused_data,
    input_hashes,
    joint_source_span_oracle_audit,
    label_alignment_summary,
    leakage_audit,
    literal_gold_occurrence_audit,
    maximum_span_matching,
    offset_guide,
    prompt_hash_payload,
    rendered_prompt_sha256,
    serialize_prompt_object,
    sha256_file,
    source_span_label_manifest,
    unique_acceptable_source_spans,
)
from scripts.data.validate_stage7c_a1_v2_development_protocol import PASS_STATUS, validate, write_report_and_update_lock


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage7c_a1_v2_development_protocol"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7c_a1_tests"


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
        "stage7c_a1_v2_development_protocol",
        "scripts/data/build_stage7c_a1_v2_development_protocol.py",
        "scripts/data/validate_stage7c_a1_v2_development_protocol.py",
        "scripts/data/audit_stage7c_a1_leakage.py",
        "tests/test_stage7c_a1_v2_development_protocol.py",
        "pyproject.toml",
        *STAGE7B_A1_INPUTS,
        *REUSED_STAGE7C_INPUTS,
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
    artifact = package / "stage7c_a1_v2_development_protocol"
    lock = _read_json(artifact / LOCK_FILE)
    lock["artifact_hashes"][rel] = sha256_file(artifact / rel)
    _write_json(artifact / LOCK_FILE, lock)


def test_validator_passes_current_stage7c_a1_artifacts() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["train_create_count"] == 1760
    assert report["dev_create_count"] == 240
    assert report["dev_source_span_oracle"] == 810
    assert report["dev_source_span_oracle_denominator"] == 845


def test_lock_is_pass_with_no_execution_flags() -> None:
    lock = _read_json(ARTIFACT_DIR / LOCK_FILE)
    assert lock["status"] == PASS_STATUS
    assert lock["train_create_count"] == 1760
    assert lock["dev_create_count"] == 240
    assert lock["phase_o_model_calls"] == 1
    assert lock["phase_m_model_calls"] == 1
    assert lock["total_model_calls"] == 2
    assert lock["phase_o_max_new_tokens"] == 512
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        assert lock[key] is False


def test_input_manifest_hashes_all_upstreams() -> None:
    manifest = _read_json(ARTIFACT_DIR / "STAGE7C_A1_INPUT_MANIFEST.json")
    assert manifest["input_hashes"] == input_hashes(ROOT)
    for rel in STAGE7B_A1_INPUTS + REUSED_STAGE7C_INPUTS:
        assert rel in manifest["input_hashes"]


def test_lf_and_crlf_text_files_have_same_canonical_sha(workspace_tmp: Path) -> None:
    lf = workspace_tmp / "same.json"
    crlf = workspace_tmp / "same_crlf.json"
    lf.write_text('{"a":1,"b":"北京"}\n', encoding="utf-8", newline="\n")
    crlf.write_text('{"a":1,"b":"北京"}\r\n', encoding="utf-8", newline="")
    assert sha256_file(lf) == sha256_file(crlf)


def test_clean_rebuild_produces_identical_canonical_lock(workspace_tmp: Path) -> None:
    output = workspace_tmp / "stage7c_a1_v2_development_protocol"
    build_stage7c_a1(output, force=True)
    report = validate(output)
    assert report["status"] == "PASS"
    write_report_and_update_lock(output, report)
    rebuilt_lock = _read_json(output / LOCK_FILE)
    current_lock = _read_json(ARTIFACT_DIR / LOCK_FILE)
    assert rebuilt_lock == current_lock


def test_all_declared_artifacts_exist() -> None:
    for rel in ARTIFACTS:
        assert (ARTIFACT_DIR / rel).is_file(), rel


def test_reused_data_manifest_preserves_stage7c_patch2_counts() -> None:
    manifest = _read_json(ARTIFACT_DIR / "REUSED_DATA_PROTOCOL_MANIFEST.json")
    assert manifest["counts"] == count_reused_data(ROOT)
    assert manifest["counts"]["train_create_count"] == 1760
    assert manifest["counts"]["dev_create_count"] == 240
    assert manifest["counts"]["gold_derivation"]["train_pass"] == 1760
    assert manifest["counts"]["gold_derivation"]["dev_pass"] == 240
    assert manifest["counts"]["gold_derivation"]["train_failures"] == 0
    assert manifest["counts"]["gold_derivation"]["dev_failures"] == 0
    assert manifest["counts"]["operation_mapping_type0"] == "INSERT"
    assert "deterministic regex semantic_slot_inventory" in manifest["superseded_for_v2_a1_primary"]


def test_reused_data_manifest_preserves_contamination_and_oracle_counts() -> None:
    counts = _read_json(ARTIFACT_DIR / "REUSED_DATA_PROTOCOL_MANIFEST.json")["counts"]
    contamination = counts["contamination"]
    for key in (
        "train_dev_question_hash_overlap",
        "train_481_question_hash_overlap",
        "dev_481_question_hash_overlap",
        "train_dev_table_id_overlap",
        "train_confirmation_table_id_overlap",
        "dev_confirmation_table_id_overlap",
    ):
        assert contamination[key] == 0
    assert contamination["train_table_id_count"] == 440
    assert contamination["dev_table_id_count"] == 60
    assert contamination["confirmation_table_id_count"] == 121
    assert counts["source_span_oracle"]["train_source_selectable"] == 5295
    assert counts["source_span_oracle"]["dev_source_selectable"] == 810
    assert counts["source_span_oracle"]["dev_samples_with_gap"] == 33


def test_phase_o_input_spec_excludes_gold_and_old_regex_slots() -> None:
    spec = _read_json(ARTIFACT_DIR / "PHASE_O_INPUT_SPEC.json")
    assert spec["model_call_count"] == 1
    assert spec["exact_original_question_preserved"] is True
    assert spec["question_normalization_before_prompt"] == "none"
    assert "semantic_slot_inventory" in spec["excluded_stage7c_fields"]
    assert "label_side_bookkeeping" in spec["excluded_stage7c_fields"]
    assert "gold_sql" in spec["forbidden_inputs"]
    assert "sql.conds" in spec["forbidden_inputs"]


def test_phase_o_prompt_spec_freezes_hashes_and_offset_instructions() -> None:
    spec = _read_json(ARTIFACT_DIR / "PHASE_O_PROMPT_SPEC.json")
    assert spec["prompt_hashes"] == prompt_hash_payload()
    assert spec["few_shot_policy"] == "zero_shot_no_examples_before_stage7d_unless_formally_amended_before_generation"
    assert spec["gold_visible"] is False
    assert "Python Unicode code-point offsets" in spec["character_offset_instructions"]
    assert spec["schema_sha256"] == sha256_file(ROOT / "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json")


def test_prompt_serialization_contract_is_canonical_utf8_lf() -> None:
    spec = _read_json(ARTIFACT_DIR / "PROMPT_SERIALIZATION_SPEC.json")
    assert spec["contract"] == PROMPT_SERIALIZATION_CONTRACT
    assert spec["contract"]["ensure_ascii"] is False
    assert spec["contract"]["sort_keys"] is True
    assert spec["contract"]["separators"] == [",", ":"]
    assert spec["contract"]["indent"] is None
    assert spec["ensure_ascii_true_allowed"] is False
    assert spec["indentation_allowed"] is False
    assert serialize_prompt_object({"b": "北京", "a": 1}) == '{"a":1,"b":"北京"}'
    assert rendered_prompt_sha256("Q\r\n北京") == rendered_prompt_sha256("Q\n北京")


def test_chat_template_rendering_is_frozen_without_misnamed_hash() -> None:
    spec = _read_json(ARTIFACT_DIR / "CHAT_TEMPLATE_RENDERING_SPEC.json")
    assert spec["rendering_call"] == "tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)"
    assert spec["add_generation_prompt"] is True
    assert spec["tokenize"] is False
    assert spec["contract"]["tokenizer_config_file_sha256"] == FROZEN_MODEL_CONFIG["tokenizer_config_file_sha256"]
    assert spec["free_choice_in_stage7d_allowed"] is False
    protocol = _read_json(ARTIFACT_DIR / "GENERATION_PROTOCOL_A1.json")
    assert "chat_template_sha256" not in protocol
    assert protocol["tokenizer_config_file_sha256_for_chat_template"] == FROZEN_MODEL_CONFIG["tokenizer_config_file_sha256"]


def test_question_offset_guide_uses_python_codepoints() -> None:
    assert offset_guide("A北京B上海C") == "0\tA\n1\t北\n2\t京\n3\tB\n4\t上\n5\t海\n6\tC"
    spec = _read_json(ARTIFACT_DIR / "QUESTION_OFFSET_GUIDE_SPEC.json")
    assert spec["example_offset_guide"] == offset_guide("A北京B上海C")
    assert spec["coordinate_system"] == "Python Unicode code-point indexing"
    assert spec["normalization_before_guide"] == "none"
    assert spec["guide_uses_gold"] is False


def test_phase_o_output_validation_reuses_stage7b_a1_schema_and_rejects_bad_outputs() -> None:
    spec = _read_json(ARTIFACT_DIR / "PHASE_O_OUTPUT_VALIDATION_SPEC.json")
    assert spec["json_schema_sha256"] == sha256_file(ROOT / "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json")
    assert spec["span_validation_spec_sha256"] == sha256_file(ROOT / "stage7b_a1_free_text_slot_discovery_amendment/SPAN_VALIDATION_SPEC.json")
    assert spec["empty_value_spans_policy"] == "reject"
    assert spec["model_generated_span_ids_policy"] == "reject"
    assert spec["model_generated_value_text_policy"] == "reject"


def test_phase_o_evaluation_protocol_tracks_operation_and_spans() -> None:
    spec = _read_json(ARTIFACT_DIR / "PHASE_O_EVALUATION_PROTOCOL.json")
    assert spec["label_alignment_manifest"] == "SOURCE_SPAN_LABEL_MANIFEST.jsonl"
    assert spec["matching_policy"] == "maximum-cardinality bipartite one-to-one matching"
    assert spec["joint_oracle_audit"] == "JOINT_SOURCE_SPAN_ORACLE_AUDIT.json"
    assert "operation_accuracy" in spec["metrics"]
    assert "exact_span_precision" in spec["metrics"]
    assert "exact_span_recall_source_alignable" in spec["metrics"]
    assert "invalid_offset_rate" in spec["metrics"]
    assert spec["primary_end_to_end_denominator"] == "full train/dev Create denominator; non-alignable samples retained"
    assert spec["source_alignable_subset"] == "diagnostic only"


def test_source_span_label_manifest_recomputes_alignment() -> None:
    manifest = _read_jsonl(ARTIFACT_DIR / "SOURCE_SPAN_LABEL_MANIFEST.jsonl")
    assert manifest == source_span_label_manifest(ROOT)
    summary = label_alignment_summary(manifest)
    assert summary["splits"]["train"]["gold_assignment_count"] == 5407
    assert summary["splits"]["train"]["source_alignable_gold_assignment_count"] == 5295
    assert summary["splits"]["dev"]["gold_assignment_count"] == 845
    assert summary["splits"]["dev"]["source_alignable_gold_assignment_count"] == 810
    assert summary["splits"]["dev"]["samples_with_non_source_alignable_gold"] == 33


def test_literal_multi_occurrence_audit_is_frozen() -> None:
    spec = _read_json(ARTIFACT_DIR / "PHASE_O_LABEL_ALIGNMENT_SPEC.json")
    audit = literal_gold_occurrence_audit(ROOT)
    assert spec["literal_gold_string_multi_occurrence_audit"] == audit
    assert audit["splits"]["train"]["literal_gold_string_multi_occurrence_assignment_count"] == 144
    assert audit["splits"]["train"]["samples_with_literal_gold_string_multi_occurrence"] == 138
    assert audit["splits"]["dev"]["literal_gold_string_multi_occurrence_assignment_count"] == 36
    assert audit["splits"]["dev"]["samples_with_literal_gold_string_multi_occurrence"] == 33


def test_gold_value_with_two_source_occurrences_keeps_both_as_acceptable() -> None:
    manifest = _read_jsonl(ARTIFACT_DIR / "SOURCE_SPAN_LABEL_MANIFEST.jsonl")
    multi = next(record for record in manifest if record["multi_occurrence"] and record["source_alignable"])
    assert len(multi["acceptable_source_spans"]) >= 2
    offsets = {(span["start_char"], span["end_char"]) for span in multi["acceptable_source_spans"]}
    assert len(offsets) == len(multi["acceptable_source_spans"])


def test_oracle_span_construction_freezes_unique_one_to_one_matching() -> None:
    spec = _read_json(ARTIFACT_DIR / "ORACLE_SPAN_CONSTRUCTION_SPEC.json")
    assert spec["diagnostic_only"] is True
    assert spec["label_side_only"] is True
    assert spec["model_side_visible"] is False
    assert "maximum-cardinality one-to-one" in spec["oracle_phase_o_span_set"]
    assert spec["duplicate_source_span_policy"] == "do not duplicate a single source occurrence into multiple oracle SLOTs under A1"
    assert spec["joint_gap_reason_code"] == "shared_source_span_requires_multiple_gold_assignments"


def test_joint_source_span_oracle_audit_recomputes_counts() -> None:
    manifest = _read_jsonl(ARTIFACT_DIR / "SOURCE_SPAN_LABEL_MANIFEST.jsonl")
    audit = _read_json(ARTIFACT_DIR / "JOINT_SOURCE_SPAN_ORACLE_AUDIT.json")
    assert audit == joint_source_span_oracle_audit(manifest)
    assert audit["splits"]["train"]["gold_assignment_count"] == 5407
    assert audit["splits"]["train"]["individually_source_alignable"] == 5295
    assert audit["splits"]["train"]["jointly_representable"] == 5272
    assert audit["splits"]["train"]["joint_reuse_deficit"] == 23
    assert audit["splits"]["train"]["samples_with_joint_reuse_gap"] == 22
    assert audit["splits"]["train"]["source_nonalignable_and_joint_gap_overlap_samples"] == 1
    assert audit["splits"]["train"]["unique_samples_with_any_representational_gap"] == 114
    assert audit["splits"]["dev"]["gold_assignment_count"] == 845
    assert audit["splits"]["dev"]["individually_source_alignable"] == 810
    assert audit["splits"]["dev"]["jointly_representable"] == 809
    assert audit["splits"]["dev"]["joint_reuse_deficit"] == 1
    assert audit["splits"]["dev"]["samples_with_joint_reuse_gap"] == 1
    assert audit["splits"]["dev"]["source_nonalignable_and_joint_gap_overlap_samples"] == 0
    assert audit["splits"]["dev"]["unique_samples_with_any_representational_gap"] == 34


def test_dev_0151_requires_shared_source_span_and_is_joint_gap() -> None:
    manifest = _read_jsonl(ARTIFACT_DIR / "SOURCE_SPAN_LABEL_MANIFEST.jsonl")
    sample_records = [record for record in manifest if record["sample_id"] == "stage7c_crudsql_dev_create_0151"]
    assert len(sample_records) == 3
    assert sum(1 for record in sample_records if record["source_alignable"]) == 3
    unique_spans = unique_acceptable_source_spans(sample_records)
    assert len(unique_spans) == 2
    match = maximum_span_matching(unique_spans, sample_records)
    assert match["matched"] == 2
    audit = _read_json(ARTIFACT_DIR / "JOINT_SOURCE_SPAN_ORACLE_AUDIT.json")
    gap = next(record for record in audit["sample_gap_records"] if record["sample_id"] == "stage7c_crudsql_dev_create_0151")
    assert gap["joint_reuse_deficit"] == 1
    assert gap["diagnostic_reasons"] == ["shared_source_span_requires_multiple_gold_assignments"]


def test_maximum_matching_prevents_one_prediction_matching_two_gold_assignments() -> None:
    labels = [
        {"gold_assignment_id": "A1", "source_alignable": True, "acceptable_source_spans": [{"start_char": 0, "end_char": 2}]},
        {"gold_assignment_id": "A2", "source_alignable": True, "acceptable_source_spans": [{"start_char": 0, "end_char": 2}]},
    ]
    result = maximum_span_matching([{"start_char": 0, "end_char": 2}], labels)
    assert result["matched"] == 1
    assert result["exact_span_precision"] == 1.0
    assert result["exact_span_recall_source_alignable"] == 0.5


def test_maximum_matching_prevents_two_predictions_consuming_one_gold_assignment() -> None:
    labels = [{"gold_assignment_id": "A1", "source_alignable": True, "acceptable_source_spans": [{"start_char": 0, "end_char": 2}]}]
    result = maximum_span_matching([{"start_char": 0, "end_char": 2}, {"start_char": 0, "end_char": 2}], labels)
    assert result["matched"] == 1
    assert result["exact_span_precision"] == 0.5
    assert result["spurious_span_rate"] == 0.5


def test_maximum_bipartite_matching_is_deterministic() -> None:
    labels = [
        {"gold_assignment_id": "A1", "source_alignable": True, "acceptable_source_spans": [{"start_char": 0, "end_char": 2}, {"start_char": 5, "end_char": 7}]},
        {"gold_assignment_id": "A2", "source_alignable": True, "acceptable_source_spans": [{"start_char": 5, "end_char": 7}]},
    ]
    first = maximum_span_matching([{"start_char": 5, "end_char": 7}, {"start_char": 0, "end_char": 2}], labels)
    second = maximum_span_matching([{"start_char": 0, "end_char": 2}, {"start_char": 5, "end_char": 7}], labels)
    assert first["matched"] == 2
    assert first["matches"] == second["matches"]


def test_phase_m_input_uses_predicted_phase_o_not_oracle() -> None:
    spec = _read_json(ARTIFACT_DIR / "PHASE_M_INPUT_SPEC.json")
    assert spec["model_call_count"] == 1
    assert spec["slot_inventory_source"] == "accepted Phase O spans only"
    assert spec["all_phase_o_slots_required"] is True
    assert "no oracle substitution" in spec["when_phase_o_is_wrong"]
    assert "gold_spans" in spec["forbidden_inputs"]
    assert "sql.conds" in spec["forbidden_inputs"]


def test_phase_m_prompt_and_evaluation_are_frozen() -> None:
    prompt = _read_json(ARTIFACT_DIR / "PHASE_M_PROMPT_SPEC.json")
    evaluation = _read_json(ARTIFACT_DIR / "PHASE_M_EVALUATION_PROTOCOL.json")
    assert prompt["prompt_hashes"] == prompt_hash_payload()
    assert prompt["gold_visible"] is False
    assert "dynamic per-sample enum" in prompt["reference_constraint"]
    assert "slot_to_column_grounding_accuracy" in evaluation["metrics"]
    assert "completeness_rejection_rate" in evaluation["metrics"]
    assert evaluation["oracle_phase_o_substitution"] == "diagnostic only and never primary"


def test_oracle_span_diagnostic_is_separate_from_primary() -> None:
    spec = _read_json(ARTIFACT_DIR / "ORACLE_SPAN_DIAGNOSTIC_PROTOCOL.json")
    assert spec["diagnostic_only"] is True
    assert spec["primary_v2_output_source"] == "predicted Phase O spans only"
    assert spec["dev_source_selectable_gold_values"] == 810
    assert spec["dev_gold_value_denominator"] == 845
    assert spec["dev_jointly_representable"] == 809
    assert spec["train_jointly_representable"] == 5272
    assert spec["dev_samples_with_joint_reuse_gap"] == 1
    assert spec["train_samples_with_joint_reuse_gap"] == 22
    assert spec["oracle_span_construction_spec"] == "ORACLE_SPAN_CONSTRUCTION_SPEC.json"
    assert spec["joint_source_span_oracle_audit"] == "JOINT_SOURCE_SPAN_ORACLE_AUDIT.json"
    assert spec["oracle_spans_used_for_training_or_selection"] is False
    assert spec["p_value_baseline_allowed"] is False


def test_nonalignable_policy_retains_denominators() -> None:
    policy = _read_json(ARTIFACT_DIR / "NONALIGNABLE_SAMPLE_POLICY.json")
    assert "source_gold_nonalignable_under_frozen_materializer" in policy["diagnostic_flags"]
    assert "joint_span_reuse_nonrepresentable" in policy["diagnostic_flags"]
    assert policy["dev_samples_with_gap"] == 33
    assert policy["train_samples_with_gap"] == 93
    assert policy["dev_samples_with_joint_reuse_gap"] == 1
    assert policy["train_samples_with_joint_reuse_gap"] == 22
    assert policy["dev_unique_samples_with_any_representational_gap"] == 34
    assert policy["train_unique_samples_with_any_representational_gap"] == 114
    assert policy["retain_in_primary_train_denominator"] is True
    assert policy["retain_in_primary_dev_denominator"] is True
    assert policy["eligible"] is True
    assert policy["exclude_after_model_performance"] is False
    assert policy["duplicate_source_span_post_hoc"] is False
    assert policy["allow_slot_reuse_after_model_performance"] is False
    assert policy["modify_gold"] is False
    assert policy["add_post_hoc_normalization"] is False


def test_generation_protocol_a1_keeps_model_constant_and_hashes_prompts() -> None:
    protocol = _read_json(ARTIFACT_DIR / "GENERATION_PROTOCOL_A1.json")
    assert protocol["model_config"] == FROZEN_MODEL_CONFIG
    assert protocol["model_config"]["model_id"] == "Qwen/Qwen2.5-Coder-7B-Instruct"
    assert protocol["model_config"]["model_revision"] == "c03e6d358207e414f1eca0bb1891e29f1db0e242"
    assert protocol["model_config"]["phase_o_max_new_tokens"] == 512
    assert protocol["model_config"]["phase_m_max_new_tokens"] == 8192
    assert protocol["phase_o_model_calls"] == 1
    assert protocol["phase_m_model_calls"] == 1
    assert protocol["total_model_calls"] == 2
    assert protocol["hidden_third_llm_call_allowed"] is False
    assert protocol["prompt_serialization_spec_sha256"] == sha256_file(ARTIFACT_DIR / "PROMPT_SERIALIZATION_SPEC.json")
    assert protocol["chat_template_rendering_spec_sha256"] == sha256_file(ARTIFACT_DIR / "CHAT_TEMPLATE_RENDERING_SPEC.json")
    assert protocol["tokenizer_config_file_sha256_for_chat_template"] == FROZEN_MODEL_CONFIG["tokenizer_config_file_sha256"]
    assert protocol["phase_o_prompt_sha256"] == prompt_hash_payload()["phase_o_user_prompt_template_sha256"]


def test_dev_selection_and_reserved_policies_protect_test_sets() -> None:
    selection = _read_json(ARTIFACT_DIR / "DEV_SELECTION_PROTOCOL_A1.json")
    reserved = _read_json(ARTIFACT_DIR / "RESERVED_BENCHMARK_POLICY.json")
    assert selection["selection_split"] == "CRUDSQL dev Create"
    assert selection["current_481_test"] == "post_hoc_only_not_selection"
    assert selection["selection_after_481_forbidden"] is True
    assert reserved["current_481_crudsql_create"] == "post_hoc_only_not_selection"
    assert reserved["crudsql_update_delete"] == "reserved_until_after_v2_a1_freeze"
    assert reserved["livesqlbench_sqlite"] == "untouched_external_no_gt_access"
    assert reserved["live_sql_bench_gt_opened"] is False


def test_leakage_audit_recomputes_and_excludes_gold() -> None:
    audit = _read_json(ARTIFACT_DIR / "DATA_LEAKAGE_AUDIT_A1.json")
    assert audit == leakage_audit(ROOT)
    assert audit["status"] == "PASS"
    assert audit["model_side_violation_count"] == 0
    assert audit["gold_in_phase_o_input"] is False
    assert audit["gold_in_phase_m_input"] is False
    assert audit["oracle_spans_in_primary_v2"] is False
    assert audit["old_stage7c_regex_semantic_slot_inventory_status"] == "superseded_not_model_side_input_for_v2_a1_primary"


def test_builder_creates_pending_lock_before_validator(workspace_tmp: Path) -> None:
    output = workspace_tmp / "stage7c_a1_v2_development_protocol"
    build_stage7c_a1(output, force=True)
    assert _read_json(output / LOCK_FILE)["status"] == "BUILT_PENDING_VALIDATION"
    report = validate(output)
    assert report["status"] == "PASS"


def test_audit_script_cli_passes() -> None:
    result = subprocess.run([sys.executable, "scripts/data/audit_stage7c_a1_leakage.py"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"status": "PASS"' in result.stdout


def test_validator_catches_phase_o_prompt_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "PHASE_O_PROMPT_SPEC.json"
    spec = _read_json(path)
    spec["few_shot_policy"] = "examples_allowed"
    _write_json(path, spec)
    _refresh_artifact_hash(package, "PHASE_O_PROMPT_SPEC.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "phase_o_fewshot_policy_changed" in report["violations"]


def test_validator_catches_prompt_serialization_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "PROMPT_SERIALIZATION_SPEC.json"
    spec = _read_json(path)
    spec["contract"]["ensure_ascii"] = True
    spec["contract"]["inventory_ordering"]["columns"] = "original order"
    _write_json(path, spec)
    _refresh_artifact_hash(package, "PROMPT_SERIALIZATION_SPEC.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "prompt_serialization_contract_changed" in report["violations"]


def test_validator_catches_chat_template_rendering_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "CHAT_TEMPLATE_RENDERING_SPEC.json"
    spec = _read_json(path)
    spec["add_generation_prompt"] = False
    spec["rendering_call"] = "tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)"
    _write_json(path, spec)
    _refresh_artifact_hash(package, "CHAT_TEMPLATE_RENDERING_SPEC.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "chat_template_rendering_call_changed" in report["violations"]
    assert "chat_template_add_generation_prompt_not_true" in report["violations"]


def test_validator_catches_label_manifest_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "SOURCE_SPAN_LABEL_MANIFEST.jsonl"
    rows = _read_jsonl(path)
    rows[0]["acceptable_source_spans"] = []
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    _refresh_artifact_hash(package, "SOURCE_SPAN_LABEL_MANIFEST.jsonl")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "source_span_label_manifest_mismatch" in report["violations"]


def test_validator_catches_generation_model_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "GENERATION_PROTOCOL_A1.json"
    protocol = _read_json(path)
    protocol["model_config"]["model_revision"] = "main"
    _write_json(path, protocol)
    _refresh_artifact_hash(package, "GENERATION_PROTOCOL_A1.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "generation_model_config_changed" in report["violations"]
    assert "model_revision_changed" in report["violations"]


def test_validator_catches_oracle_diagnostic_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "ORACLE_SPAN_DIAGNOSTIC_PROTOCOL.json"
    spec = _read_json(path)
    spec["diagnostic_only"] = False
    spec["oracle_spans_used_for_training_or_selection"] = True
    _write_json(path, spec)
    _refresh_artifact_hash(package, "ORACLE_SPAN_DIAGNOSTIC_PROTOCOL.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "oracle_not_diagnostic_only" in report["violations"]
    assert "oracle_spans_used_for_selection" in report["violations"]


def test_validator_catches_joint_oracle_audit_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "JOINT_SOURCE_SPAN_ORACLE_AUDIT.json"
    audit = _read_json(path)
    audit["splits"]["dev"]["jointly_representable"] = 810
    _write_json(path, audit)
    _refresh_artifact_hash(package, "JOINT_SOURCE_SPAN_ORACLE_AUDIT.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "joint_source_span_oracle_audit_mismatch" in report["violations"]
    assert "joint_dev_representable_count_changed" in report["violations"]


def test_validator_catches_oracle_construction_policy_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "ORACLE_SPAN_CONSTRUCTION_SPEC.json"
    spec = _read_json(path)
    spec["duplicate_source_span_policy"] = "duplicate spans when useful"
    _write_json(path, spec)
    _refresh_artifact_hash(package, "ORACLE_SPAN_CONSTRUCTION_SPEC.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "oracle_construction_duplicate_policy_changed" in report["violations"]


def test_validator_catches_leakage_audit_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "DATA_LEAKAGE_AUDIT_A1.json"
    audit = _read_json(path)
    audit["gold_in_phase_o_input"] = True
    _write_json(path, audit)
    _refresh_artifact_hash(package, "DATA_LEAKAGE_AUDIT_A1.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "leakage_audit_mismatch" in report["violations"]
    assert "gold_in_phase_o" in report["violations"]


def test_validator_catches_reserved_policy_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "RESERVED_BENCHMARK_POLICY.json"
    policy = _read_json(path)
    policy["livesqlbench_sqlite"] = "opened"
    policy["live_sql_bench_gt_opened"] = True
    _write_json(path, policy)
    _refresh_artifact_hash(package, "RESERVED_BENCHMARK_POLICY.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "livesqlbench_policy_changed" in report["violations"]
    assert "livesqlbench_opened" in report["violations"]


def test_validator_catches_phase_m_oracle_substitution_tamper(workspace_tmp: Path) -> None:
    package = _copy_package_root(workspace_tmp)
    path = package / "stage7c_a1_v2_development_protocol" / "PHASE_M_INPUT_SPEC.json"
    spec = _read_json(path)
    spec["when_phase_o_is_wrong"] = "substitute oracle spans"
    _write_json(path, spec)
    _refresh_artifact_hash(package, "PHASE_M_INPUT_SPEC.json")
    report = validate(package / "stage7c_a1_v2_development_protocol", root=package)
    assert report["status"] == "FAIL"
    assert "phase_m_allows_oracle_substitution" in report["violations"]


def test_self_contained_reviewer_package_clean_extraction(workspace_tmp: Path) -> None:
    if os.environ.get("STAGE7C_A1_IN_CLEAN_PACKAGE_TEST") == "1":
        return
    package = _copy_package_root(workspace_tmp)
    env = os.environ.copy()
    env["STAGE7C_A1_IN_CLEAN_PACKAGE_TEST"] = "1"
    commands = [
        [sys.executable, "scripts/data/build_stage7c_a1_v2_development_protocol.py", "--force"],
        [sys.executable, "scripts/data/validate_stage7c_a1_v2_development_protocol.py"],
        [sys.executable, "scripts/data/audit_stage7c_a1_leakage.py"],
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7c_a1_v2_development_protocol.py"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=package, env=env, text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skipped" not in result.stdout.casefold()
