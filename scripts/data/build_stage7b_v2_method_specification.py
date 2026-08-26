from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE = "Stage7B_V2_METHOD_SPECIFICATION"
DATE = "20260826"
HASH_POLICY = "text_sha256_canonical_lf"
MODEL_CALLED = False
GPU_CALLED = False
V2_IMPLEMENTED = False
SCHEMA_INVENTORY_EXAMPLE = {
    "table_refs": ["TAB_1"],
    "column_refs": ["COL_1", "COL_2", "COL_3", "COL_4", "COL_5"],
    "evidence_refs": ["EV_1", "EV_2", "EV_3"],
    "constraint_refs": ["CONSTRAINT_1"],
}
PREDICATE_OPERATORS = ["EQ", "NE", "LT", "GT"]
PREDICATE_CONNECTORS = ["AND", "OR"]

STAGE7A_INPUTS = (
    "stage7a_formal_failure_analysis/STAGE7A_FAILURE_ANALYSIS_LOCK.json",
    "stage7a_formal_failure_analysis/STAGE7A_INPUT_MANIFEST.json",
    "stage7a_formal_failure_analysis/FAILURE_TAXONOMY_SPEC.json",
    "stage7a_formal_failure_analysis/PIPELINE_FAILURE_SUMMARY.json",
    "stage7a_formal_failure_analysis/VERIFICATION_FAILURE_SUMMARY.json",
    "stage7a_formal_failure_analysis/FAILURE_OVERLAP_MATRIX.json",
    "stage7a_formal_failure_analysis/FAILURE_COMBINATION_COUNTS.json",
    "stage7a_formal_failure_analysis/DESIGN_REQUIREMENT_TRACEABILITY.json",
    "stage7a_formal_failure_analysis/PARSE_FAILURE_ANALYSIS.jsonl",
    "stage7a_formal_failure_analysis/STATE_MISMATCH_ANALYSIS.jsonl",
)

ARTIFACTS = (
    "STAGE7B_INPUT_MANIFEST.json",
    "V2_DESIGN_RATIONALE.json",
    "V2_ARCHITECTURE_SPEC.json",
    "OPERATION_CONDITIONING_SPEC.json",
    "SLOT_GROUNDED_IR_SPEC.json",
    "REFERENCE_CONSTRAINT_SPEC.json",
    "TYPED_MATERIALIZATION_SPEC.json",
    "COMPLETENESS_VERIFICATION_SPEC.json",
    "REPRESENTATION_CONTRACT_SPEC.json",
    "ABSTENTION_POLICY_SPEC.json",
    "ABLATION_REGISTRATION.json",
    "DEVELOPMENT_DATA_POLICY.json",
    "DESIGN_TO_EVIDENCE_TRACEABILITY.json",
    "schemas/insert_ir.schema.json",
    "schemas/update_ir.schema.json",
    "schemas/delete_ir.schema.json",
    "schemas/upsert_ir.schema.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def reset_output_dir(output_dir: Path, force: bool) -> None:
    default = PROJECT_ROOT / "stage7b_v2_method_specification"
    if output_dir.exists():
        if not force and output_dir == default:
            raise RuntimeError(f"{output_dir} exists; pass --force to rebuild.")
        if default not in (output_dir, *output_dir.parents):
            raise RuntimeError(f"Refusing to remove output outside repository Stage7B path: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def input_hashes() -> dict[str, str]:
    hashes = {}
    for rel in STAGE7A_INPUTS:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(f"Missing Stage7A locked input: {rel}")
        hashes[rel] = sha256_file(path)
    return hashes


def load_stage7a_evidence() -> dict[str, Any]:
    pipeline = read_json(PROJECT_ROOT / "stage7a_formal_failure_analysis" / "PIPELINE_FAILURE_SUMMARY.json")
    verification = read_json(PROJECT_ROOT / "stage7a_formal_failure_analysis" / "VERIFICATION_FAILURE_SUMMARY.json")
    traceability = read_json(PROJECT_ROOT / "stage7a_formal_failure_analysis" / "DESIGN_REQUIREMENT_TRACEABILITY.json")
    combinations = read_json(PROJECT_ROOT / "stage7a_formal_failure_analysis" / "FAILURE_COMBINATION_COUNTS.json")
    state_rows = read_jsonl(PROJECT_ROOT / "stage7a_formal_failure_analysis" / "STATE_MISMATCH_ANALYSIS.jsonl")
    parse_rows = read_jsonl(PROJECT_ROOT / "stage7a_formal_failure_analysis" / "PARSE_FAILURE_ANALYSIS.jsonl")
    return {
        "pipeline": pipeline,
        "verification": verification,
        "traceability": traceability,
        "combinations": combinations,
        "state_rows": state_rows,
        "parse_rows": parse_rows,
    }


def trace_row(evidence: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    rows = {row["design_requirement_id"]: row for row in evidence["traceability"]["traceability"]}
    return rows[requirement_id]


def support_count(row: dict[str, Any], source: str) -> int:
    if row["direct_support"]["source"] == source:
        return int(row["direct_support"]["sample_count"])
    for item in row.get("indicative_support", []):
        if item["source"] == source:
            return int(item["sample_count"])
    raise KeyError(source)


def design_rationale(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "SPECIFICATION_ONLY",
        "method_name": "Operation-Conditioned Slot-Grounded MP-FS",
        "stage7a_evidence_summary": {
            "final_n": evidence["pipeline"]["final_n"],
            "pipeline_counts": evidence["pipeline"]["pipeline_failure_counts"],
            "verification_direct_families": evidence["verification"]["root_cause_family_prevalence"],
            "verification_overlap_combinations": evidence["combinations"]["combination_counts"],
            "state_mismatch_indicative_families": evidence["pipeline"]["state_mismatch_indicative_family_prevalence"],
            "parse_failure_type_prevalence": evidence["pipeline"]["parse_failure_type_prevalence"],
        },
        "design_principles": [
            "minimize model degrees of freedom before SQL compilation",
            "use operation-specific contracts rather than one rich universal JSON object",
            "restrict model references to enumerated finite inventories",
            "derive typed values deterministically from evidence and schema",
            "separate directly observed verifier evidence from indicative executable mismatches",
        ],
        "non_goals": [
            "no V2 implementation in Stage7B",
            "no model call, GPU call, experiment, or performance tuning",
            "no LiveSQLBench ground-truth inspection",
            "no adaptation against the 481 Stage6/Stage7A test samples",
        ],
    }


def architecture_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "method_name": "Operation-Conditioned Slot-Grounded MP-FS",
        "status": "FROZEN_SPECIFICATION",
        "input_contract": ["natural_language_write_request", "schema_inventory", "evidence_inventory"],
        "core_pipeline": [
            {"order": 1, "component": "operation_conditioning", "output": "operation_class"},
            {"order": 2, "component": "slot_grounded_minimal_ir", "output": "operation_specific_ir_with_constrained_references"},
            {"order": 3, "component": "deterministic_typed_materialization", "output": "typed_assignments"},
            {"order": 4, "component": "semantic_completeness_verification", "output": "accepted_or_rejected_ir"},
            {"order": 5, "component": "deterministic_sqlite_compilation_preflight", "output": "sqlite_program_or_rejection"},
        ],
        "representation_contract": "JSON Schema validation surrounds all model-produced IR before semantic verification.",
        "operation_conditioning_protocol": "two_phase_operation_enum_then_schema_conditioned_mapping",
        "registered_model_call_count": {"Phase_O_operation_conditioning": 1, "Phase_M_mapping": 1},
        "primary_v2_depends_on_repair": False,
        "optional_deferred_variant": "V2-R bounded repair may be registered after V2 primary specification, but is not a core Stage7B component.",
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
    }


def operation_conditioning_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "operation_conditioning",
        "operation_classes": ["INSERT", "UPDATE", "DELETE", "UPSERT"],
        "protocol": "two_phase_operation_conditioning",
        "phase_o": {
            "name": "operation_enum_selection",
            "model_call_count": 1,
            "output_contract": {"operation": {"enum": ["INSERT", "UPDATE", "DELETE", "UPSERT"]}},
            "forbidden_inputs": ["gold_operation", "gold_sql", "gold_post_state", "test_performance"],
            "invalid_output_policy": "reject; do not fall back to unified rich plan",
        },
        "phase_m": {
            "name": "schema_conditioned_mapping",
            "model_call_count": 1,
            "schema_selection": "select exactly one operation-specific dynamic-enum JSON Schema from Phase O output",
            "operation_field_policy": "operation is fixed by Phase O and must match the selected schema const",
        },
        "rule": "Determine operation class before producing slot-grounded IR; select a JSON schema by operation.",
        "operation_specific_allowed_fields": {
            "INSERT": ["operation", "table_ref", "assignments"],
            "UPDATE": ["operation", "table_ref", "row_selector", "assignments"],
            "DELETE": ["operation", "table_ref", "row_selector"],
            "UPSERT": ["operation", "table_ref", "conflict_target_ref", "insert_assignments", "update_policy", "update_assignments"],
        },
        "forbidden_insert_fields": ["conflict_target_ref", "update_columns", "upsert_action", "delete_predicate", "row_selector"],
        "failure_policy": "reject rather than silently reinterpret operation-specific schema violations",
    }


def slot_grounded_ir_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "slot_grounded_minimal_ir",
        "phase_o_llm_decisions": ["operation_class"],
        "phase_m_llm_decisions": ["table_ref", "column_ref_to_evidence_ref_mapping", "structured_predicate_mapping_when_required", "conflict_target_ref_only_for_upsert"],
        "not_llm_decisions": ["SQL syntax", "normalization_function", "arbitrary_identifier_creation", "compiler_strategy", "silent_reference_repair"],
        "predicate_ir": {
            "connector": {"enum": PREDICATE_CONNECTORS},
            "predicates": {"items": {"column_ref": "dynamic enum COL_*", "operator": PREDICATE_OPERATORS, "evidence_ref": "dynamic enum EV_*"}},
            "supports_multi_condition": True,
            "opaque_row_ref_allowed": False,
        },
        "insert_example": {
            "operation": "INSERT",
            "table_ref": "TAB_1",
            "assignments": [
                {"column_ref": "COL_2", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                {"column_ref": "COL_5", "evidence_ref": "EV_3", "slot_ref": "SLOT_2"},
            ],
        },
    }


def reference_constraint_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "constrained_reference_selection",
        "selected_mechanism": "dynamic_per_sample_json_schema_enum",
        "rule": "Every identifier emitted by the model must belong to a supplied finite inventory instantiated into the per-sample JSON Schema.",
        "schema_instantiation": {
            "table_ref": "enum(sample.table_refs)",
            "column_ref": "enum(sample.column_refs)",
            "evidence_ref": "enum(sample.evidence_refs)",
            "conflict_target_ref": "enum(sample.constraint_refs)",
        },
        "validation_order": ["operation_phase_o_enum", "instantiate_operation_specific_schema_with_sample_inventory", "json_schema_validation", "inventory_membership_is_enforced_by_enum", "semantic_verification"],
        "example_inventory_for_schema_artifacts": SCHEMA_INVENTORY_EXAMPLE,
        "inventories": {"tables": "TAB_* dynamic enum", "columns": "COL_* dynamic enum", "evidence": "EV_* dynamic enum", "constraints": "CONSTRAINT_* dynamic enum"},
        "invalid_reference_policy": "deterministic rejection; no silent fuzzy correction",
        "unrestricted_reference_ids_allowed": False,
    }


def typed_materialization_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "deterministic_typed_materialization",
        "rule": "Typed values are derived from schema type plus raw evidence via deterministic parsers.",
        "llm_normalization_decisions_allowed": False,
        "affinity_rule_table": [
            {"schema_affinity": "INTEGER", "rule": "strict lossless integer parse", "unsafe_policy": "reject"},
            {"schema_affinity": "REAL", "rule": "strict finite numeric parse", "unsafe_policy": "reject"},
            {"schema_affinity": "TEXT", "rule": "preserve raw evidence string", "unsafe_policy": "not_applicable"},
            {"schema_affinity": "NULL", "rule": "emit NULL only when evidence belongs to frozen null-literal set", "unsafe_policy": "reject otherwise"},
            {"schema_affinity": "BLOB", "rule": "unsupported without a frozen binary representation", "unsafe_policy": "reject"},
            {"schema_affinity": "ambiguous_or_lossy_parse", "rule": "no coercion", "unsafe_policy": "reject"},
        ],
        "null_literal_set": ["NULL", "null", "None", "无", "空"],
        "implicit_date_time_normalization_allowed": False,
        "sqlite_date_note": "SQLite has no native DATE storage class; TEXT columns preserve raw evidence unless a deterministic canonicalization rule is frozen before evaluation.",
        "examples": [
            {"schema_type": "INTEGER", "raw_evidence": "20", "materialized_value": 20},
            {"schema_type": "REAL", "raw_evidence": "12.50", "materialized_value": 12.5},
            {"schema_type": "TEXT", "raw_evidence": "12.50", "materialized_value": "12.50"},
        ],
        "unsafe_materialization_policy": "reject; do not hallucinate conversion",
    }


def completeness_verification_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "semantic_completeness_verification",
        "rule": "Verify that semantic write slots implied by the request are fully represented before SQL compilation.",
        "semantic_slot_inventory": {
            "required_input_to_phase_m": True,
            "slot_schema": {"slot_ref": "SLOT_*", "evidence_ref": "EV_* dynamic enum", "role": ["write_value", "predicate_value", "conflict_key"], "required": "boolean"},
            "creation_policy": {
                "semi_structured_input": "deterministically convert supplied source fields/evidence cells into SLOT_* before mapping",
                "free_text_input": "use a pre-registered evidence span inventory; if span discovery needs an LLM, it is an explicit model-dependent upstream module and cannot be claimed deterministic",
            },
        },
        "set_definitions": {
            "required": "set(SLOT_* where required=true)",
            "allowed": "set(all SLOT_* in semantic_slot_inventory)",
            "mapped": "set(SLOT_* represented by accepted IR assignments and predicates)",
            "missing": "required - mapped",
            "extra": "mapped - allowed",
            "complete_iff": "missing == empty and extra == empty",
        },
        "checks": ["all_required_slots_mapped", "no_unjustified_extra_slots", "operation_required_fields_present", "row_selector_present_for_update_delete", "mapped_slots_are_allowed"],
        "coverage_metric": "semantic_slots_mapped / semantic_slots_required",
        "failure_policy": "reject incomplete or over-selected IR before deterministic compilation",
    }


def representation_contract_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "representation_schema_contract",
        "contract": ["JSON object only", "operation-specific required fields", "additionalProperties=false", "dynamic per-sample enum reference IDs", "schema validation before semantic verification"],
        "constrained_structured_output_preferred": True,
        "fallback": "deterministic JSON parse followed by JSON Schema validation",
    }


def abstention_policy_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "component": "abstention_policy",
        "true_ambiguity_direct_support_from_stage7a": 0,
        "policy": "Abstention is a safety mechanism for independently demonstrated ambiguity, not an explanation for the two Stage7A parse failures.",
        "allowed_reasons": ["schema_nonconformance", "invalid_reference", "unsafe_materialization", "semantic_incompleteness", "independently_audited_true_ambiguity"],
    }


def ablation_registration(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "FROZEN_BEFORE_IMPLEMENTATION",
        "primary_system": "V2-FULL",
        "variants": [
            {"variant": "V2-FULL", "components": ["operation_conditioning", "constrained_references", "deterministic_materialization", "completeness_verification"], "intervention": "none", "everything_else_held_constant": True},
            {"variant": "V2-A", "remove_component": "operation_conditioning", "intervention": "replace two-phase Phase O schema selection with a single operation-unconditioned union prompt while keeping dynamic inventory enums, materialization, completeness, compiler, and data policy unchanged", "everything_else_held_constant": True, "stage7a_direct_support": 204},
            {"variant": "V2-B", "remove_component": "constrained_references", "intervention": "replace per-sample enum membership constraints with ID-shape pattern checks only; downstream invalid IDs are rejected at the same semantic verification boundary, with all other components unchanged", "everything_else_held_constant": True, "stage7a_direct_support": 190},
            {"variant": "V2-C", "remove_component": "deterministic_materialization", "intervention": "replace typed materializer with raw evidence TEXT passthrough for all affinities; mappings, schema, completeness gate, compiler, and data policy unchanged", "everything_else_held_constant": True, "stage7a_direct_support": 133},
            {"variant": "V2-D", "remove_component": "completeness_verification", "intervention": "bypass only the semantic completeness gate after schema and inventory validation; materialization, compiler preflight, prompts, schemas, and data policy unchanged", "everything_else_held_constant": True, "stage7a_direct_support": 6, "stage7a_indicative_support": {"under_write": 41, "over_write": 16}},
        ],
        "selection_metric_policy": "register variants now; measure future failure-family deltas without promising accuracy targets",
        "source_stage7a_final_n": evidence["pipeline"]["final_n"],
    }


def development_data_policy() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "FROZEN_SPECIFICATION",
        "allowed_for_v2_development": ["CRUDSQL train Create"],
        "allowed_for_selection_tuning": ["CRUDSQL dev Create"],
        "forbidden_for_selection_tuning": ["current 481 CRUDSQL Create test analyzed in Stage6/Stage7A"],
        "post_hoc_adaptation_evaluation_only": ["current 481 CRUDSQL Create test analyzed in Stage6/Stage7A"],
        "reserved_until_after_v2_freeze": ["CRUDSQL Update", "CRUDSQL Delete"],
        "untouched_external_benchmark": ["LiveSQLBench SQLite"],
        "live_sql_bench_gt_policy": "do not open or inspect ground truth during V2 development/specification",
        "performance_tuning_on_481_allowed": False,
    }


def design_to_evidence_traceability(evidence: dict[str, Any]) -> dict[str, Any]:
    op = trace_row(evidence, "operation_conditioned_ir")
    ref = trace_row(evidence, "constrained_reference_selection")
    typed = trace_row(evidence, "typed_materialization")
    complete = trace_row(evidence, "semantic_completeness_verification")
    schema = trace_row(evidence, "representation_schema_contract")
    ambiguity = trace_row(evidence, "explicit_abstention_for_true_ambiguity")
    return {
        "stage": STAGE,
        "status": "FROZEN_TRACEABILITY",
        "entries": [
            {"component": "operation_conditioning", "stage7a_direct_support": {"failure_family": "operation_semantics", "sample_count": support_count(op, "verification_errors:operation_semantics")}, "stage7a_indicative_support": [{"failure_family": "row_cardinality", "sample_count": support_count(op, "state_mismatch_family:row_cardinality")}], "design_decision": "condition IR and allowed fields on INSERT/UPDATE/DELETE/UPSERT before downstream mapping", "expected_mechanism": "reduce operation/conflict semantic degrees of freedom", "expected_future_measure": "operation-semantics verification failures"},
            {"component": "constrained_reference_selection", "stage7a_direct_support": {"failure_family": "invalid_reference", "sample_count": support_count(ref, "verification_errors:invalid_reference")}, "stage7a_indicative_support": [{"failure_family": "wrong_target_column", "sample_count": support_count(ref, "state_mismatch_subtype:wrong_target_column")}], "design_decision": "restrict model-produced references to enumerated inventory IDs", "expected_mechanism": "make invalid identifiers deterministically impossible or rejectable", "expected_future_measure": "invalid-reference verification failures"},
            {"component": "deterministic_typed_materialization", "stage7a_direct_support": {"failure_family": "normalization", "sample_count": support_count(typed, "verification_errors:normalization")}, "stage7a_indicative_support": [{"failure_family": "wrong_value_or_evidence", "sample_count": support_count(typed, "state_mismatch_subtype:wrong_value_or_evidence")}], "design_decision": "remove normalization decisions from LLM output", "expected_mechanism": "derive values from schema type and raw evidence with deterministic parsers", "expected_future_measure": "normalization/materialization failures"},
            {"component": "semantic_completeness_verification", "stage7a_direct_support": {"failure_family": "slot_or_update_completeness", "sample_count": support_count(complete, "verification_errors:slot_or_update_completeness")}, "stage7a_indicative_support": [{"failure_family": "missing_assignment_or_under_write", "sample_count": support_count(complete, "state_mismatch_subtype:missing_assignment_or_under_write")}, {"failure_family": "extra_assignment_or_over_write", "sample_count": support_count(complete, "state_mismatch_subtype:extra_assignment_or_over_write")}], "design_decision": "verify semantic slot coverage before SQLite compilation", "expected_mechanism": "reject under-selected and over-selected write mappings", "expected_future_measure": "semantic completeness and executable state-mismatch subtypes"},
            {"component": "representation_schema_contract", "stage7a_direct_support": {"failure_family": "representation_schema_nonconformance", "sample_count": support_count(schema, "parse_failure_type:schema_nonconformance_valid_json")}, "stage7a_indicative_support": [], "design_decision": "validate operation-specific JSON Schema before semantic verification", "expected_mechanism": "turn valid-JSON wrong-shape outputs into deterministic schema failures", "expected_future_measure": "parse/schema nonconformance failures"},
            {"component": "explicit_abstention_for_true_ambiguity", "stage7a_direct_support": {"failure_family": "unsupported_or_true_ambiguity", "sample_count": support_count(ambiguity, "independent_true_ambiguity_audit")}, "stage7a_indicative_support": [], "design_decision": "treat abstention as a safety mechanism unless ambiguity is independently audited", "expected_mechanism": "avoid using parse failures as causal evidence for true ambiguity", "expected_future_measure": "independently audited ambiguity abstentions"},
        ],
        "accuracy_target_registered": False,
    }


def ref_schema(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


def assignment_schema(*, include_slot_ref: bool = True) -> dict[str, Any]:
    properties = {"column_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["column_refs"]), "evidence_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["evidence_refs"])}
    required = ["column_ref", "evidence_ref"]
    if include_slot_ref:
        properties["slot_ref"] = {"type": "string", "pattern": "^SLOT_[0-9]+$"}
        required.append("slot_ref")
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


def row_selector_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["connector", "predicates"],
        "properties": {
            "connector": {"enum": PREDICATE_CONNECTORS},
            "predicates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["column_ref", "operator", "evidence_ref", "slot_ref"],
                    "properties": {
                        "column_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["column_refs"]),
                        "operator": {"enum": PREDICATE_OPERATORS},
                        "evidence_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["evidence_refs"]),
                        "slot_ref": {"type": "string", "pattern": "^SLOT_[0-9]+$"},
                    },
                },
            },
        },
    }


def schemas() -> dict[str, dict[str, Any]]:
    base = {"$schema": "https://json-schema.org/draft/2020-12/schema", "additionalProperties": False, "type": "object"}
    return {
        "schemas/insert_ir.schema.json": {**base, "x-schema-instantiation": "dynamic_per_sample_enum_example", "required": ["operation", "table_ref", "assignments"], "properties": {"operation": {"const": "INSERT"}, "table_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["table_refs"]), "assignments": {"type": "array", "minItems": 1, "items": assignment_schema()}}},
        "schemas/update_ir.schema.json": {**base, "x-schema-instantiation": "dynamic_per_sample_enum_example", "required": ["operation", "table_ref", "row_selector", "assignments"], "properties": {"operation": {"const": "UPDATE"}, "table_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["table_refs"]), "row_selector": row_selector_schema(), "assignments": {"type": "array", "minItems": 1, "items": assignment_schema()}}},
        "schemas/delete_ir.schema.json": {**base, "x-schema-instantiation": "dynamic_per_sample_enum_example", "required": ["operation", "table_ref", "row_selector"], "properties": {"operation": {"const": "DELETE"}, "table_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["table_refs"]), "row_selector": row_selector_schema()}},
        "schemas/upsert_ir.schema.json": {
            **base,
            "x-schema-instantiation": "dynamic_per_sample_enum_example",
            "required": ["operation", "table_ref", "conflict_target_ref", "insert_assignments", "update_policy"],
            "properties": {"operation": {"const": "UPSERT"}, "table_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["table_refs"]), "conflict_target_ref": ref_schema(SCHEMA_INVENTORY_EXAMPLE["constraint_refs"]), "insert_assignments": {"type": "array", "minItems": 1, "items": assignment_schema()}, "update_policy": {"enum": ["DO_NOTHING", "DO_UPDATE"]}, "update_assignments": {"type": "array", "minItems": 1, "items": assignment_schema()}},
            "oneOf": [
                {"properties": {"update_policy": {"const": "DO_NOTHING"}}, "not": {"required": ["update_assignments"]}},
                {"required": ["update_assignments"], "properties": {"update_policy": {"const": "DO_UPDATE"}}},
            ],
        },
    }


def reviewer_readme() -> str:
    return """# Stage7B V2 Method Specification

This package freezes the V2 method specification only. It contains no V2 implementation, no model call, no GPU call, and no experiment results.

Commands:
```bash
python scripts/data/validate_stage7b_v2_method_specification.py
python -m pytest -q tests/test_stage7b_v2_method_specification.py
```

Stage7B uses Stage7A PATCH1 artifacts as locked evidence and separates direct support from indicative support.
"""


def pending_validation_report() -> str:
    return "# Stage7B Validation Report\n\nStatus: PENDING_VALIDATION\n"


def lock(output_dir: Path, hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "BUILT_PENDING_VALIDATION",
        "date": DATE,
        "hash_policy": HASH_POLICY,
        "input_hashes": hashes,
        "artifact_hashes": {rel: sha256_file(output_dir / rel) for rel in ARTIFACTS},
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
        "experiment_run": False,
        "live_sql_bench_gt_opened": False,
    }


def build_stage7b(output_dir: Path, *, force: bool = False) -> dict[str, Any]:
    reset_output_dir(output_dir, force)
    hashes = input_hashes()
    evidence = load_stage7a_evidence()
    write_json(output_dir / "STAGE7B_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "hash_policy": HASH_POLICY, "input_hashes": hashes, "stage7a_locked": True, "model_called": False, "gpu_called": False})
    write_json(output_dir / "V2_DESIGN_RATIONALE.json", design_rationale(evidence))
    write_json(output_dir / "V2_ARCHITECTURE_SPEC.json", architecture_spec())
    write_json(output_dir / "OPERATION_CONDITIONING_SPEC.json", operation_conditioning_spec())
    write_json(output_dir / "SLOT_GROUNDED_IR_SPEC.json", slot_grounded_ir_spec())
    write_json(output_dir / "REFERENCE_CONSTRAINT_SPEC.json", reference_constraint_spec())
    write_json(output_dir / "TYPED_MATERIALIZATION_SPEC.json", typed_materialization_spec())
    write_json(output_dir / "COMPLETENESS_VERIFICATION_SPEC.json", completeness_verification_spec())
    write_json(output_dir / "REPRESENTATION_CONTRACT_SPEC.json", representation_contract_spec())
    write_json(output_dir / "ABSTENTION_POLICY_SPEC.json", abstention_policy_spec())
    write_json(output_dir / "ABLATION_REGISTRATION.json", ablation_registration(evidence))
    write_json(output_dir / "DEVELOPMENT_DATA_POLICY.json", development_data_policy())
    write_json(output_dir / "DESIGN_TO_EVIDENCE_TRACEABILITY.json", design_to_evidence_traceability(evidence))
    for rel, payload in schemas().items():
        write_json(output_dir / rel, payload)
    (output_dir / "VALIDATION_REPORT.md").write_text(pending_validation_report(), encoding="utf-8")
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    write_json(output_dir / "STAGE7B_V2_SPECIFICATION_LOCK.json", lock(output_dir, hashes))
    return {"stage": STAGE, "status": "PASS_BUILT", "stage7a_final_n": evidence["pipeline"]["final_n"], "components": 5, "schemas": 4, "model_called": False, "gpu_called": False, "v2_implemented": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7b_v2_method_specification")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_stage7b(args.output_dir, force=args.force), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
