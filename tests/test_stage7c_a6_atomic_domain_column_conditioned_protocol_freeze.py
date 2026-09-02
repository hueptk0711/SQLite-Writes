from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import (
    PACKAGE_NAME,
    PHASE_O_SYSTEM_PROMPT,
    PHASE_O_USER_PROMPT_TEMPLATE,
    STAGE_NAME,
    STAGE7B_SELECTED_VARIANT,
    build_stage,
    canonical_json,
    oracle_column_conditioned_path,
    package_reviewer,
    prompt_spec,
    read_json,
    render_phase_o_messages,
    sha256_text,
)
from scripts.data.validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import validate


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def built_stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stage = tmp_path_factory.mktemp("stage7c_a6_patch1") / STAGE_NAME
    build_stage(stage)
    return stage


def test_prompt_spec_freezes_column_conditioned_one_call_contract() -> None:
    spec = prompt_spec()
    assert spec["system_prompt"] == PHASE_O_SYSTEM_PROMPT
    assert spec["user_prompt_template"] == PHASE_O_USER_PROMPT_TEMPLATE
    assert spec["prompt_hashes"]["phase_o_system_prompt_sha256"] == sha256_text(PHASE_O_SYSTEM_PROMPT)
    assert spec["prompt_hashes"]["phase_o_user_prompt_template_sha256"] == sha256_text(PHASE_O_USER_PROMPT_TEMPLATE)
    assert spec["model_selects_table_ref"] is True
    assert spec["model_selects_column_span_refs"] is True
    assert spec["model_generates_character_offsets"] is False
    assert spec["model_generates_values"] is False
    assert spec["model_generates_free_length_span_set"] is False
    assert spec["model_generates_slot_refs"] is False
    assert spec["model_generates_phase_m"] is False
    assert spec["examples"] == []
    assert spec["retry"] == 0
    assert spec["repair"] == "none"


def test_stage_has_12_fresh_cases_with_required_surfaces(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")
    ids = [row["sample_id"] for row in rows]
    tags = {tag for row in rows for tag in row["coverage_tags"]}
    assert len(rows) == 12
    assert len(set(ids)) == 12
    assert all(sample_id.startswith("stage7c_a6_primary_english_") for sample_id in ids)
    assert not any(sample_id.startswith("stage7c_a4_") or sample_id.startswith("gretel:") for sample_id in ids)
    assert {"3_assigned_columns", "4_assigned_columns", "5_assigned_columns"} <= tags
    assert {"true_omit", "many_nullable_columns", "quoted_multiword", "three_word_value", "overlapping_candidates"} <= tags
    assert {"multi_table", "oneOf", "email", "identifier", "hex_identifier", "date", "percent", "integer", "real"} <= tags
    assert {"schema_alias", "generic_stoplist", "legitimate_omission_literals", "datetime", "possessive_text", "ordinal_phrase"} <= tags


def test_model_side_input_contains_no_labels_offsets_or_gold(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl"):
        assert set(row["model_side_input"]) == {"question", "schema_inventory", "candidate_inventory_text"}
        assert "start_char" not in row["model_side_input"]["candidate_inventory_text"]
        assert "end_char" not in row["model_side_input"]["candidate_inventory_text"]
        assert "target_state" not in row["model_side_input"]
        assert row["label_side_expected"]["model_side_visible"] is False
        assert "sqlite_db_sha256" not in row["synthetic_db_spec"]


def test_dynamic_schema_domain_and_required_columns_are_exact(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")
    multi_table_rows = 0
    for row in rows:
        schema = row["runtime_constraints"]["phase_o_schema"]
        candidates = row["runtime_constraints"]["candidate_inventory"]
        phase_o = row["label_side_expected"]["phase_o"]
        assert "pattern" not in canonical_json(schema)
        if "oneOf" in schema:
            multi_table_rows += 1
            branch = next(branch for branch in schema["oneOf"] if branch["properties"]["table_ref"]["const"] == phase_o["table_ref"])
            column_schema = branch["properties"]["column_span_refs"]
        else:
            column_schema = schema["properties"]["column_span_refs"]
        assert list(phase_o["column_span_refs"]) == column_schema["required"]
        assert set(phase_o["column_span_refs"]) == set(column_schema["properties"])
        for column_ref in column_schema["required"]:
            assert column_schema["properties"][column_ref]["enum"] == ["OMIT", *[candidate["span_ref"] for candidate in candidates]]
    assert multi_table_rows == 2


def test_candidate_domain_filter_is_frozen_and_gold_blind(built_stage: Path) -> None:
    runtime_freeze = read_json(built_stage / "CANDIDATE_DOMAIN_RUNTIME_FREEZE_A6.json")
    domain_audit = read_json(built_stage / "A6_ORACLE_CANDIDATE_DOMAIN_AUDIT.json")
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")
    assert runtime_freeze["source_patch"] == "PATCH2"
    assert runtime_freeze["manual_alias_additions_allowed_after_a6_outputs"] is False
    assert runtime_freeze["forbidden_runtime_inputs"] == ["gold_sql", "gold_values", "gold_offsets", "target_state", "model_outputs"]
    assert "method failure" in runtime_freeze["candidate_miss_policy"]
    assert domain_audit["status"] == "PASS"
    assert domain_audit["gold_suppressed_total"] == 0
    assert domain_audit["suppressed_candidate_total"] > 0
    assert domain_audit["unfiltered_candidate_total"] > domain_audit["filtered_candidate_total"]
    assert all(row["runtime_constraints"]["candidate_domain_filter_enabled"] is True for row in rows)
    assert all(row["candidate_domain_audit"]["gold_suppressed_count"] == 0 for row in rows)


def test_phase_o_output_uses_only_column_conditioned_surface(built_stage: Path) -> None:
    assigned_count = 0
    omit_count = 0
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl"):
        phase_o = row["label_side_expected"]["phase_o"]
        assert sorted(phase_o) == ["column_span_refs", "operation", "table_ref"]
        assert phase_o["operation"] == "INSERT"
        assert "span_refs" not in phase_o
        assert "phase_m" not in row["label_side_expected"]
        for span_ref in phase_o["column_span_refs"].values():
            if span_ref == "OMIT":
                omit_count += 1
            else:
                assigned_count += 1
    assert assigned_count == 52
    assert omit_count == 14


def test_gold_refs_slice_exact_values_and_cover_candidate_inventory(built_stage: Path) -> None:
    total = 0
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl"):
        question = row["model_side_input"]["question"]
        candidate_refs = {candidate["span_ref"] for candidate in row["runtime_constraints"]["candidate_inventory"]}
        for gold in row["label_side_expected"]["gold_column_span_ref_oracle"]:
            assert question[gold["start_char"] : gold["end_char"]] == gold["text"]
            assert gold["candidate_span_ref"] in candidate_refs
            assert row["label_side_expected"]["phase_o"]["column_span_refs"][gold["column_ref"]] == gold["candidate_span_ref"]
            total += 1
    assert total == 52


def test_rendered_prompt_contains_column_omit_and_candidate_inventory(built_stage: Path) -> None:
    row = read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")[0]
    messages, user, digest = render_phase_o_messages(row)
    assert len(messages) == 2
    assert row["model_side_input"]["question"] in user
    assert "Choose exactly one SPAN reference or OMIT" in user
    assert "Use each non-OMIT SPAN reference for at most one column." in user
    assert "Schema inventory:" in user
    assert "Candidate span inventory:" in user
    assert "SPAN_" in user
    assert len(digest) == 64


def test_oracle_path_admits_all_cases_without_phase_m_model_surface(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")
    results = [oracle_column_conditioned_path(row, built_stage / row["synthetic_db_spec"]["sqlite_db_path"]) for row in rows]
    assert sum(result["preflight"] == "ADMITTED" for result in results) == 12
    assert all(result["canonical_target_state_exact"] for result in results)
    assert all(result["phase_m_model_call_removed"] for result in results)
    assert all(result["model_generated_phase_m"] is False for result in results)
    assert all(result["model_generated_slot_refs"] is False for result in results)


def test_a4_derived_cases_are_diagnostics_only_after_primary(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "A5_OBSERVED_REGRESSION_DIAGNOSTICS_A6.jsonl")
    results = read_jsonl(built_stage / "ORACLE_A5_OBSERVED_DIAGNOSTIC_RESULTS.jsonl")
    assert len(rows) == 12
    assert len(results) == 12
    assert all(row["sample_id"].startswith("stage7c_a5_primary_english_") for row in rows)
    assert all(row["diagnostic_role"] == "diagnostic_only_after_primary" for row in rows)
    assert all(row["diagnostic_source"] == "corrected_a5_gold_provenance_erratum" for row in rows)
    assert sum(result["preflight"] == "ADMITTED" for result in results) == 12
    assert all(result["canonical_target_state_exact"] for result in results)


def test_a6_method_stress_cases_are_diagnostics_only_after_primary(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "A6_METHOD_STRESS_REGRESSION_DIAGNOSTICS_A6.jsonl")
    results = read_jsonl(built_stage / "ORACLE_A6_METHOD_STRESS_DIAGNOSTIC_RESULTS.jsonl")
    assert len(rows) == 12
    assert len(results) == 12
    assert all(row["sample_id"].startswith("stage7c_a6_method_stress_english_") for row in rows)
    assert all(row["diagnostic_role"] == "diagnostic_only_after_primary" for row in rows)
    assert all(row["diagnostic_source"] == "a6_patch0_method_stress_regression" for row in rows)
    assert sum(result["preflight"] == "ADMITTED" for result in results) == 12
    assert all(result["canonical_target_state_exact"] for result in results)


def test_primary_independence_and_evaluator_semantics_are_frozen(built_stage: Path) -> None:
    audit = read_json(built_stage / "A6_PRIMARY_INDEPENDENCE_AUDIT.json")
    prior_audit = read_json(built_stage / "PRIOR_DESIGN_EVIDENCE_INDEPENDENCE_AUDIT_A6.json")
    evaluator = read_json(built_stage / "EVALUATOR_SEMANTICS_A6.json")
    output = read_json(built_stage / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_OUTPUT_SPEC_A6.json")
    assert audit["status"] == "PASS"
    assert audit["exact_prior_design_literal_reuse_case_count"] == 0
    assert audit["exact_synthetic_fixture_reuse_case_count"] == 0
    assert audit["known_development_example_reuse_case_count"] == 0
    assert prior_audit["status"] == "PASS"
    assert prior_audit["exact_prior_design_literal_reuse_case_count"] == 0
    assert prior_audit["exact_synthetic_fixture_reuse_case_count"] == 0
    assert evaluator["column_span_refs_mapping_equality"] == "order_insensitive_by_object_key"
    assert evaluator["json_object_key_order_has_semantics"] is False
    assert evaluator["duplicate_span_reuse_outcome"] == "method_failure"
    assert output["non_omit_span_refs_unique_across_columns"] is True


def test_lock_and_policy_close_gpu_gretel_and_type_pruning(built_stage: Path) -> None:
    lock = read_json(built_stage / "STAGE7C_A6_LOCK.json")
    policy = read_json(built_stage / "OMIT_AND_CANDIDATE_MISS_FAILURE_POLICY_A6.json")
    no_phase_m = read_json(built_stage / "NO_PHASE_M_PRIMARY_PIPELINE_SPEC_A6.json")
    assert lock["phase_m_primary_pipeline_removed"] is True
    assert lock["model_called"] is False
    assert lock["gpu_called"] is False
    assert lock["gretel_pilot_opened"] is False
    assert lock["development_dev_used"] is False
    assert lock["official_test_used"] is False
    assert lock["source_stage7c_a5_erratum_status"] is not None
    assert lock["type_based_candidate_pruning_enabled"] is False
    assert lock["candidate_domain_filter_enabled"] is True
    assert lock["candidate_domain_gold_suppressed_total"] == 0
    assert lock["candidate_domain_suppressed_candidate_total"] > 0
    assert lock["exact_prior_design_literal_reuse_case_count"] == 0
    assert lock["exact_synthetic_fixture_reuse_case_count"] == 0
    assert lock["primary_acceptance_precedes_diagnostics"] is True
    assert lock["diagnostics_can_compensate_primary_failure"] is False
    assert lock["duplicate_span_reuse_is_method_failure"] is True
    assert lock["column_span_refs_mapping_equality"] == "order_insensitive_by_object_key"
    assert policy["omit_allowed_for_candidate_miss"] is False
    assert policy["candidate_miss_is_method_failure"] is True
    assert policy["duplicate_span_reuse_is_method_failure"] is True
    assert no_phase_m["primary_pipeline_phase_m_removed"] is True


def test_validator_accepts_frozen_generated_artifacts() -> None:
    report = validate(ROOT / STAGE_NAME)
    assert report["status"] == "PASS", report["failures"]
    assert report["fresh_english_case_count"] == 12
    assert report["assigned_column_decision_count"] == 52
    assert report["omit_column_decision_count"] == 14
    assert report["multi_table_oneof_case_count"] == 2
    assert report["a4_derived_regression_diagnostic_count"] == 12
    assert report["a6_method_stress_regression_diagnostic_count"] == 12


def test_semantic_rebuild_requires_tokenizer_after_token_audit_pass() -> None:
    token_audit = read_json(ROOT / STAGE_NAME / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
    if token_audit["tokenizer_status"] != "PASS":
        pytest.skip("frozen tokenizer audit is rebuilt by the stage validation command")
    report = validate(ROOT / STAGE_NAME, rebuild=True)
    assert report["status"] == "FAIL"
    assert "TOKENIZER_REQUIRED_FOR_REBUILD" in report["failures"]


def test_validator_rejects_phase_m_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    rows = read_jsonl(stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")
    rows[0]["label_side_expected"]["phase_m"] = {"operation": "INSERT", "assignments": []}
    (stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl").write_text(
        "\n".join(canonical_json(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any("removed_phase_m_surface" in failure for failure in report["failures"])


def test_validator_rejects_unknown_span_ref_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    rows = read_jsonl(stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")
    first_column = next(iter(rows[0]["label_side_expected"]["phase_o"]["column_span_refs"]))
    rows[0]["label_side_expected"]["phase_o"]["column_span_refs"][first_column] = "SPAN_9999"
    (stage / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl").write_text(
        "\n".join(canonical_json(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any("selected_span_ref_missing_from_inventory" in failure or "oracle_exception" in failure for failure in report["failures"])


def test_validator_rejects_prompt_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    spec = read_json(stage / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROMPT_SPEC_A6_ENGLISH.json")
    spec["user_prompt_template"] = spec["user_prompt_template"].replace("Use OMIT only", "Use OMIT whenever unsure")
    write_json(stage / "ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROMPT_SPEC_A6_ENGLISH.json", spec)
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert "prompt_user_template_mismatch" in report["failures"]


def test_reviewer_package_opens_and_contains_stage_files(tmp_path: Path, built_stage: Path) -> None:
    package_path = tmp_path / PACKAGE_NAME
    digest = package_reviewer(built_stage, package_path)
    assert len(digest) == 64
    assert package_path.with_suffix(package_path.suffix + ".sha256").is_file()
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
    assert f"{STAGE_NAME}/VALIDATION_REPORT.md" in names
    assert f"{STAGE_NAME}/A6_METHOD_STRESS_REGRESSION_DIAGNOSTICS_A6.jsonl" in names
    assert f"{STAGE_NAME}/PRIOR_DESIGN_EVIDENCE_INDEPENDENCE_AUDIT_A6.json" in names
    assert "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py" in names
    assert "scripts/data/build_stageeng0_gretel_qualification.py" in names
    assert "tests/test_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py" in names
    assert "Stage7C_A5_PRIMARY_GOLD_PROVENANCE_ERRATUM_PATCH0/ERRATUM_LOCK.json" in names
    assert "Stage7C_A5_PRIMARY_GOLD_PROVENANCE_ERRATUM_PATCH0/CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl" in names


