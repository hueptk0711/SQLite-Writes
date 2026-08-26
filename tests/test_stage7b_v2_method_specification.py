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
from collections import Counter
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


def _schema_accepts(schema: dict, instance) -> bool:
    if "oneOf" in schema and sum(1 for branch in schema["oneOf"] if _schema_accepts(branch, instance)) != 1:
        return False
    if "not" in schema and _schema_accepts(schema["not"], instance):
        return False
    if "const" in schema and instance != schema["const"]:
        return False
    if "enum" in schema and instance not in schema["enum"]:
        return False
    expected_type = schema.get("type")
    if expected_type == "object" and not isinstance(instance, dict):
        return False
    if expected_type == "array" and not isinstance(instance, list):
        return False
    if expected_type == "string" and not isinstance(instance, str):
        return False
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(key not in properties for key in instance):
            return False
        for key, value in instance.items():
            if key in properties and not _schema_accepts(properties[key], value):
                return False
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            return False
        item_schema = schema.get("items")
        if item_schema and any(not _schema_accepts(item_schema, item) for item in instance):
            return False
    return True


def _assignment(column_ref: str = "COL_1", evidence_ref: str = "EV_1", slot_ref: str = "SLOT_1") -> dict:
    return {"column_ref": column_ref, "evidence_ref": evidence_ref, "slot_ref": slot_ref}


def _predicate(column_ref: str = "COL_1", operator: str = "EQ", evidence_ref: str = "EV_1", slot_ref: str = "SLOT_1") -> dict:
    return {"column_ref": column_ref, "operator": operator, "evidence_ref": evidence_ref, "slot_ref": slot_ref}


def _selector(connector: str = "AND", predicates: list[dict] | None = None) -> dict:
    return {"connector": connector, "predicates": predicates or [_predicate()]}


def _complete(required: set[str], allowed: set[str], mapped_occurrences, assignment_columns=None) -> bool:
    mapped = set(mapped_occurrences)
    counts = Counter(mapped_occurrences)
    duplicate_required = {slot for slot in required if counts[slot] != 1}
    duplicate_mapped = {slot for slot, count in counts.items() if count > 1}
    columns_unique = True if assignment_columns is None else len(assignment_columns) == len(set(assignment_columns))
    return not (required - mapped) and not (mapped - allowed) and not duplicate_required and not duplicate_mapped and columns_unique


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
    assert "semantic_slot_inventory" in spec["input_contract"]
    assert spec["operation_conditioning_protocol"] == "two_phase_operation_enum_then_schema_conditioned_mapping"
    assert spec["registered_model_call_count"] == {"Phase_M_mapping": 1, "Phase_O_operation_conditioning": 1}


def test_operation_conditioning_has_operation_specific_allowed_fields() -> None:
    spec = _read_json(ARTIFACT_DIR / "OPERATION_CONDITIONING_SPEC.json")
    assert spec["operation_classes"] == ["INSERT", "UPDATE", "DELETE", "UPSERT"]
    assert spec["protocol"] == "two_phase_operation_conditioning"
    assert spec["phase_o"]["model_call_count"] == 1
    assert spec["phase_m"]["model_call_count"] == 1
    assert "semantic_slot_inventory" in spec["phase_m"]["input_contract"]
    assert set(spec["phase_o"]["forbidden_inputs"]) >= {"gold_operation", "gold_sql", "gold_post_state"}
    assert spec["phase_o"]["invalid_output_policy"] == "reject; do not fall back to unified rich plan"
    assert "conflict_target_ref" not in spec["operation_specific_allowed_fields"]["INSERT"]
    assert "assignments" not in spec["operation_specific_allowed_fields"]["DELETE"]
    assert "conflict_target_ref" in spec["operation_specific_allowed_fields"]["UPSERT"]


def test_slot_grounded_ir_keeps_llm_decisions_minimal() -> None:
    spec = _read_json(ARTIFACT_DIR / "SLOT_GROUNDED_IR_SPEC.json")
    assert spec["phase_o_llm_decisions"] == ["operation_class"]
    assert "column_ref_to_evidence_ref_mapping" in spec["phase_m_llm_decisions"]
    assert spec["predicate_ir"]["opaque_row_ref_allowed"] is False
    assert spec["predicate_ir"]["connector"]["enum"] == ["AND", "OR"]
    assert spec["predicate_ir"]["predicates"]["items"]["operator"] == ["EQ", "NE", "LT", "GT"]
    assert "SQL syntax" in spec["not_llm_decisions"]
    assert "normalization_function" in spec["not_llm_decisions"]


def test_reference_constraint_forbids_unrestricted_identifiers_and_silent_repair() -> None:
    spec = _read_json(ARTIFACT_DIR / "REFERENCE_CONSTRAINT_SPEC.json")
    assert spec["selected_mechanism"] == "dynamic_per_sample_json_schema_enum"
    assert spec["schema_instantiation"]["column_ref"] == "enum(sample.column_refs)"
    assert spec["schema_instantiation"]["slot_ref"] == "enum(sample.slot_refs)"
    assert spec["example_inventory_for_schema_artifacts"]["slot_refs"] == ["SLOT_1", "SLOT_2", "SLOT_3"]
    assert "instantiate_operation_specific_schema_with_sample_inventory" in spec["validation_order"]
    assert spec["unrestricted_reference_ids_allowed"] is False
    assert spec["invalid_reference_policy"] == "deterministic rejection; no silent fuzzy correction"
    assert set(spec["inventories"]) >= {"tables", "columns", "evidence"}


def test_typed_materialization_removes_normalization_from_llm_output() -> None:
    spec = _read_json(ARTIFACT_DIR / "TYPED_MATERIALIZATION_SPEC.json")
    assert spec["llm_normalization_decisions_allowed"] is False
    assert spec["unsafe_materialization_policy"] == "reject; do not hallucinate conversion"
    rules = {row["sqlite_affinity"]: row["rule"] for row in spec["affinity_rule_table"]}
    assert set(rules) == {"TEXT", "NUMERIC", "INTEGER", "REAL", "BLOB"}
    assert rules["INTEGER"] == "strict lossless integer/numeric handling"
    assert rules["REAL"] == "strict finite real handling"
    assert rules["TEXT"] == "preserve raw evidence text"
    assert "SQLite-aware numeric" in rules["NUMERIC"]
    assert rules["BLOB"] == "unsupported without a frozen binary representation"
    assert "NULL" not in rules
    assert spec["null_value_policy"]["null_is_affinity"] is False
    assert "无" in spec["null_value_policy"]["language_broad_heuristics_forbidden"]
    assert spec["implicit_date_time_normalization_allowed"] is False
    assert {"schema_type": "TEXT", "raw_evidence": "12.50", "materialized_value": "12.50"} in spec["examples"]


def test_completeness_verification_defines_semantic_slot_coverage() -> None:
    spec = _read_json(ARTIFACT_DIR / "COMPLETENESS_VERIFICATION_SPEC.json")
    assert "all_required_slots_mapped" in spec["checks"]
    assert "required_slots_mapped_exactly_once" in spec["checks"]
    assert "no_duplicate_slot_mapping" in spec["checks"]
    assert "assignment_target_columns_unique" in spec["checks"]
    assert "no_unjustified_extra_slots" in spec["checks"]
    assert "mapped_slots_are_allowed" in spec["checks"]
    assert spec["coverage_metric"] == "semantic_slots_mapped / semantic_slots_required"
    assert spec["semantic_slot_inventory"]["required_input_to_phase_m"] is True
    assert "semi_structured_input" in spec["semantic_slot_inventory"]["creation_policy"]
    assert "free_text_input" in spec["semantic_slot_inventory"]["creation_policy"]
    assert spec["set_definitions"]["missing"] == "required - mapped"
    assert spec["set_definitions"]["extra"] == "mapped - allowed"
    assert spec["set_definitions"]["duplicate_required"] == "any required SLOT_i where count(mapped_occurrences[SLOT_i]) != 1"
    assert spec["set_definitions"]["complete_iff"] == "missing == empty and extra == empty and duplicate_required == empty and duplicate_mapped == empty and assignment_target_columns_unique == true"
    assert spec["multiplicity_constraints"]["insert_assignments"] == "column_ref values must be unique within assignments"
    assert spec["multiplicity_constraints"]["upsert_cross_branch_column_reuse"] == "allowed because insert_assignments and update_assignments are separate semantic branches"
    assert spec["multiplicity_constraints"]["predicates"].startswith("column_ref uniqueness is not required")


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
    for row in spec["variants"]:
        assert row["everything_else_held_constant"] is True
        assert row["intervention"]


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
    assert insert_schema["properties"]["table_ref"]["enum"] == ["TAB_1"]
    assignment_props = insert_schema["properties"]["assignments"]["items"]["properties"]
    assert assignment_props["column_ref"]["enum"] == ["COL_1", "COL_2", "COL_3", "COL_4", "COL_5"]
    assert assignment_props["evidence_ref"]["enum"] == ["EV_1", "EV_2", "EV_3"]
    assert assignment_props["slot_ref"]["enum"] == ["SLOT_1", "SLOT_2", "SLOT_3"]
    assert "pattern" not in json.dumps(insert_schema, sort_keys=True)
    delete_schema = _read_json(ARTIFACT_DIR / "schemas" / "delete_ir.schema.json")
    assert "assignments" not in delete_schema["properties"]


def test_insert_schema_rejects_table_ref_outside_inventory() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "insert_ir.schema.json")
    instance = {"operation": "INSERT", "table_ref": "TAB_999", "assignments": [_assignment()]}
    assert not _schema_accepts(schema, instance)


def test_insert_schema_rejects_column_ref_outside_inventory() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "insert_ir.schema.json")
    instance = {"operation": "INSERT", "table_ref": "TAB_1", "assignments": [_assignment(column_ref="COL_999")]}
    assert not _schema_accepts(schema, instance)


def test_insert_schema_rejects_evidence_ref_outside_inventory() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "insert_ir.schema.json")
    instance = {"operation": "INSERT", "table_ref": "TAB_1", "assignments": [_assignment(evidence_ref="EV_999")]}
    assert not _schema_accepts(schema, instance)


def test_insert_schema_rejects_slot_ref_outside_inventory() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "insert_ir.schema.json")
    instance = {"operation": "INSERT", "table_ref": "TAB_1", "assignments": [_assignment(slot_ref="SLOT_999")]}
    assert not _schema_accepts(schema, instance)


def test_insert_schema_rejects_conflict_field() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "insert_ir.schema.json")
    instance = {
        "operation": "INSERT",
        "table_ref": "TAB_1",
        "assignments": [_assignment()],
        "conflict_target_ref": "CONSTRAINT_1",
    }
    assert not _schema_accepts(schema, instance)


def test_update_schema_accepts_two_predicate_selector() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "update_ir.schema.json")
    instance = {
        "operation": "UPDATE",
        "table_ref": "TAB_1",
        "row_selector": _selector(predicates=[_predicate("COL_2", "EQ", "EV_1", "SLOT_1"), _predicate("COL_3", "NE", "EV_2", "SLOT_2")]),
        "assignments": [_assignment("COL_4", "EV_3", "SLOT_3")],
    }
    assert _schema_accepts(schema, instance)


def test_update_schema_rejects_slot_ref_outside_inventory_in_assignment() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "update_ir.schema.json")
    instance = {
        "operation": "UPDATE",
        "table_ref": "TAB_1",
        "row_selector": _selector(),
        "assignments": [_assignment(slot_ref="SLOT_999")],
    }
    assert not _schema_accepts(schema, instance)


def test_update_schema_rejects_slot_ref_outside_inventory_in_predicate() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "update_ir.schema.json")
    instance = {
        "operation": "UPDATE",
        "table_ref": "TAB_1",
        "row_selector": _selector(predicates=[_predicate(slot_ref="SLOT_999")]),
        "assignments": [_assignment("COL_4", "EV_3", "SLOT_3")],
    }
    assert not _schema_accepts(schema, instance)


def test_delete_schema_accepts_and_or_predicate_contract() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "delete_ir.schema.json")
    and_instance = {"operation": "DELETE", "table_ref": "TAB_1", "row_selector": _selector("AND", [_predicate("COL_1", "LT", "EV_1")])}
    or_instance = {
        "operation": "DELETE",
        "table_ref": "TAB_1",
        "row_selector": _selector("OR", [_predicate("COL_1", "GT", "EV_1", "SLOT_1"), _predicate("COL_2", "EQ", "EV_2", "SLOT_2")]),
    }
    assert _schema_accepts(schema, and_instance)
    assert _schema_accepts(schema, or_instance)


def test_delete_schema_rejects_slot_ref_outside_inventory() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "delete_ir.schema.json")
    instance = {"operation": "DELETE", "table_ref": "TAB_1", "row_selector": _selector(predicates=[_predicate(slot_ref="SLOT_999")])}
    assert not _schema_accepts(schema, instance)


def test_update_schema_rejects_malformed_predicate() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "update_ir.schema.json")
    missing_operator = {
        "operation": "UPDATE",
        "table_ref": "TAB_1",
        "row_selector": {"connector": "AND", "predicates": [{"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"}]},
        "assignments": [_assignment()],
    }
    invalid_operator = {
        "operation": "UPDATE",
        "table_ref": "TAB_1",
        "row_selector": _selector(predicates=[_predicate(operator="LIKE")]),
        "assignments": [_assignment()],
    }
    assert not _schema_accepts(schema, missing_operator)
    assert not _schema_accepts(schema, invalid_operator)


def test_delete_schema_rejects_opaque_row_ref_selector() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "delete_ir.schema.json")
    instance = {"operation": "DELETE", "table_ref": "TAB_1", "row_selector": {"row_ref": "ROW_1"}}
    assert not _schema_accepts(schema, instance)


def test_upsert_do_update_requires_assignments() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "upsert_ir.schema.json")
    instance = {
        "operation": "UPSERT",
        "table_ref": "TAB_1",
        "conflict_target_ref": "CONSTRAINT_1",
        "insert_assignments": [_assignment()],
        "update_policy": "DO_UPDATE",
    }
    assert not _schema_accepts(schema, instance)


def test_upsert_rejects_slot_ref_outside_inventory() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "upsert_ir.schema.json")
    instance = {
        "operation": "UPSERT",
        "table_ref": "TAB_1",
        "conflict_target_ref": "CONSTRAINT_1",
        "insert_assignments": [_assignment(slot_ref="SLOT_999")],
        "update_policy": "DO_NOTHING",
    }
    assert not _schema_accepts(schema, instance)


def test_upsert_do_update_rejects_empty_assignment_list() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "upsert_ir.schema.json")
    instance = {
        "operation": "UPSERT",
        "table_ref": "TAB_1",
        "conflict_target_ref": "CONSTRAINT_1",
        "insert_assignments": [_assignment()],
        "update_policy": "DO_UPDATE",
        "update_assignments": [],
    }
    assert not _schema_accepts(schema, instance)


def test_upsert_do_nothing_forbids_update_assignments() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "upsert_ir.schema.json")
    instance = {
        "operation": "UPSERT",
        "table_ref": "TAB_1",
        "conflict_target_ref": "CONSTRAINT_1",
        "insert_assignments": [_assignment()],
        "update_policy": "DO_NOTHING",
        "update_assignments": [_assignment("COL_2", "EV_2", "SLOT_2")],
    }
    assert not _schema_accepts(schema, instance)


def test_upsert_conditional_valid_cases() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "upsert_ir.schema.json")
    do_nothing = {
        "operation": "UPSERT",
        "table_ref": "TAB_1",
        "conflict_target_ref": "CONSTRAINT_1",
        "insert_assignments": [_assignment()],
        "update_policy": "DO_NOTHING",
    }
    do_update = {
        "operation": "UPSERT",
        "table_ref": "TAB_1",
        "conflict_target_ref": "CONSTRAINT_1",
        "insert_assignments": [_assignment()],
        "update_policy": "DO_UPDATE",
        "update_assignments": [_assignment("COL_2", "EV_2", "SLOT_2")],
    }
    assert _schema_accepts(schema, do_nothing)
    assert _schema_accepts(schema, do_update)


def test_completeness_set_logic_rejects_missing_required_slot() -> None:
    assert not _complete(required={"SLOT_1", "SLOT_2"}, allowed={"SLOT_1", "SLOT_2"}, mapped_occurrences=["SLOT_1"])


def test_completeness_set_logic_rejects_extra_slot() -> None:
    assert not _complete(required={"SLOT_1"}, allowed={"SLOT_1"}, mapped_occurrences=["SLOT_1", "SLOT_99"])


def test_completeness_set_logic_accepts_exact_required_allowed_mapping() -> None:
    assert _complete(required={"SLOT_1", "SLOT_2"}, allowed={"SLOT_1", "SLOT_2", "SLOT_3"}, mapped_occurrences=["SLOT_1", "SLOT_2"], assignment_columns=["COL_1", "COL_2"])


def test_completeness_multiplicity_rejects_duplicate_required_slot() -> None:
    assert not _complete(required={"SLOT_1", "SLOT_2"}, allowed={"SLOT_1", "SLOT_2"}, mapped_occurrences=["SLOT_1", "SLOT_1", "SLOT_2"])


def test_completeness_rejects_duplicate_insert_assignment_column() -> None:
    assert not _complete(required={"SLOT_1", "SLOT_2"}, allowed={"SLOT_1", "SLOT_2"}, mapped_occurrences=["SLOT_1", "SLOT_2"], assignment_columns=["COL_1", "COL_1"])


def test_completeness_rejects_duplicate_update_set_column() -> None:
    assert not _complete(required={"SLOT_1", "SLOT_2"}, allowed={"SLOT_1", "SLOT_2"}, mapped_occurrences=["SLOT_1", "SLOT_2"], assignment_columns=["COL_3", "COL_3"])


def test_predicate_contract_does_not_require_unique_columns() -> None:
    schema = _read_json(ARTIFACT_DIR / "schemas" / "delete_ir.schema.json")
    instance = {
        "operation": "DELETE",
        "table_ref": "TAB_1",
        "row_selector": _selector("AND", [_predicate("COL_2", "GT", "EV_1", "SLOT_1"), _predicate("COL_2", "LT", "EV_2", "SLOT_2")]),
    }
    assert _schema_accepts(schema, instance)


def test_ablation_interventions_are_exact_counterfactuals() -> None:
    spec = _read_json(ARTIFACT_DIR / "ABLATION_REGISTRATION.json")
    rows = {row["variant"]: row for row in spec["variants"]}
    assert "single operation-unconditioned union prompt" in rows["V2-A"]["intervention"]
    assert "pattern checks only" in rows["V2-B"]["intervention"]
    assert "same semantic verification boundary" in rows["V2-B"]["intervention"]
    assert "raw evidence TEXT passthrough" in rows["V2-C"]["intervention"]
    assert "bypass only the semantic completeness gate" in rows["V2-D"]["intervention"]


def test_lock_builder_pending_then_validator_promotes_pass(workspace_tmp: Path) -> None:
    artifact = workspace_tmp / "generated_stage7b"
    build_stage7b(artifact)
    assert _read_json(artifact / "STAGE7B_V2_SPECIFICATION_LOCK.json")["status"] == "BUILT_PENDING_VALIDATION"
    validation = subprocess.run(
        [sys.executable, str(ROOT / "scripts/data/validate_stage7b_v2_method_specification.py"), "--output-dir", str(artifact)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert '"status": "PASS"' in validation.stdout
    assert _read_json(artifact / "STAGE7B_V2_SPECIFICATION_LOCK.json")["status"] == "PASS_V2_METHOD_SPECIFICATION_LOCKED"


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


def test_validator_detects_reference_schema_degraded_to_pattern_only(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "schemas" / "insert_ir.schema.json"
    payload = _read_json(path)
    payload["properties"]["assignments"]["items"]["properties"]["column_ref"] = {"type": "string", "pattern": "^COL_[0-9]+$"}
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "schemas/insert_ir.schema.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "insert_assignments_column_ref_not_enum" in report["violations"]


def test_validator_detects_slot_ref_degraded_to_pattern_only(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "schemas" / "insert_ir.schema.json"
    payload = _read_json(path)
    payload["properties"]["assignments"]["items"]["properties"]["slot_ref"] = {"type": "string", "pattern": "^SLOT_[0-9]+$"}
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "schemas/insert_ir.schema.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "schema_uses_shape_reference_constraint:INSERT" in report["violations"]
    assert "insert_assignments_slot_ref_not_enum" in report["violations"]


def test_validator_detects_opaque_row_selector(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "schemas" / "update_ir.schema.json"
    payload = _read_json(path)
    payload["properties"]["row_selector"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["row_ref"],
        "properties": {"row_ref": {"type": "string", "pattern": "^ROW_[0-9]+$"}},
    }
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "schemas/update_ir.schema.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "schema_uses_opaque_row_ref:UPDATE" in report["violations"]
    assert "update_selector_not_structured" in report["violations"]


def test_validator_detects_missing_upsert_conditional_contract(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "schemas" / "upsert_ir.schema.json"
    payload = _read_json(path)
    payload.pop("oneOf")
    payload["properties"]["update_assignments"]["minItems"] = 0
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "schemas/upsert_ir.schema.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "upsert_missing_conditional_contract" in report["violations"]
    assert "upsert_update_assignments_not_nonempty" in report["violations"]


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


def test_validator_detects_incomplete_typed_affinity_table(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "TYPED_MATERIALIZATION_SPEC.json"
    payload = _read_json(path)
    payload["affinity_rule_table"] = [row for row in payload["affinity_rule_table"] if row["sqlite_affinity"] != "NUMERIC"]
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "TYPED_MATERIALIZATION_SPEC.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "typed_affinity_table_not_sqlite_five" in report["violations"]
    assert "numeric_affinity_missing" in report["violations"]


def test_validator_detects_null_listed_as_sqlite_affinity(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "TYPED_MATERIALIZATION_SPEC.json"
    payload = _read_json(path)
    payload["affinity_rule_table"].append({"sqlite_affinity": "NULL", "rule": "bad", "unsafe_policy": "bad"})
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "TYPED_MATERIALIZATION_SPEC.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "typed_affinity_table_not_sqlite_five" in report["violations"]
    assert "null_listed_as_sqlite_affinity" in report["violations"]


def test_validator_detects_missing_semantic_slot_inventory(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "COMPLETENESS_VERIFICATION_SPEC.json"
    payload = _read_json(path)
    payload["semantic_slot_inventory"]["required_input_to_phase_m"] = False
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "COMPLETENESS_VERIFICATION_SPEC.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "semantic_slot_inventory_not_required" in report["violations"]


def test_validator_detects_missing_semantic_slot_architecture_input(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "V2_ARCHITECTURE_SPEC.json"
    payload = _read_json(path)
    payload["input_contract"] = [item for item in payload["input_contract"] if item != "semantic_slot_inventory"]
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "V2_ARCHITECTURE_SPEC.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "semantic_slot_inventory_missing_from_architecture_input" in report["violations"]


def test_validator_detects_missing_duplicate_slot_policy(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "COMPLETENESS_VERIFICATION_SPEC.json"
    payload = _read_json(path)
    payload["checks"] = [item for item in payload["checks"] if item != "required_slots_mapped_exactly_once"]
    payload["set_definitions"].pop("duplicate_required")
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "COMPLETENESS_VERIFICATION_SPEC.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "completeness_multiplicity_check_missing" in report["violations"]
    assert "duplicate_required_not_frozen" in report["violations"]


def test_validator_detects_missing_assignment_column_uniqueness_policy(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "schemas" / "insert_ir.schema.json"
    payload = _read_json(path)
    payload["x-semantic-constraints"].pop("assignment_target_column_uniqueness")
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "schemas/insert_ir.schema.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "insert_assignment_uniqueness_missing" in report["violations"]


def test_validator_detects_missing_ablation_intervention(workspace_tmp: Path) -> None:
    artifact = _copy_stage7b(workspace_tmp)
    path = artifact / "ABLATION_REGISTRATION.json"
    payload = _read_json(path)
    payload["variants"][1]["intervention"] = ""
    payload["variants"][1]["everything_else_held_constant"] = False
    _write_json(path, payload)
    _refresh_lock_hash(artifact, "ABLATION_REGISTRATION.json")
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "ablation_intervention_missing:V2-A" in report["violations"]
    assert "ablation_constant_control_missing:V2-A" in report["violations"]


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
