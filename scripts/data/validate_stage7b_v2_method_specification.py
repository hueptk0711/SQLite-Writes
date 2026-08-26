from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE = "Stage7B_V2_METHOD_SPECIFICATION"
HASH_POLICY = "text_sha256_canonical_lf"

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
HASHED_ARTIFACTS = tuple(rel for rel in ARTIFACTS if rel != "STAGE7B_V2_SPECIFICATION_LOCK.json")
LOCK_FILE = "STAGE7B_V2_SPECIFICATION_LOCK.json"

EXPECTED_PIPELINE = {"parse": 2, "state_mismatch": 43, "verification": 436}
EXPECTED_VERIFICATION = {"invalid_reference": 190, "normalization": 133, "operation_semantics": 204, "slot_or_update_completeness": 6}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def input_hashes(root: Path, violations: list[str]) -> dict[str, str]:
    hashes = {}
    for rel in STAGE7A_INPUTS:
        path = root / rel
        if not path.is_file():
            violations.append(f"missing_stage7a_input:{rel}")
            continue
        hashes[rel] = sha256_file(path)
    return hashes


def require(condition: bool, violations: list[str], code: str) -> None:
    if not condition:
        violations.append(code)


def trace_by_component(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row.get("component"): row for row in trace.get("entries", [])}


def schema_operation(schema: dict[str, Any]) -> str | None:
    return schema.get("properties", {}).get("operation", {}).get("const")


def validate_schemas(output_dir: Path, violations: list[str]) -> None:
    schemas = {
        "INSERT": read_json(output_dir / "schemas/insert_ir.schema.json"),
        "UPDATE": read_json(output_dir / "schemas/update_ir.schema.json"),
        "DELETE": read_json(output_dir / "schemas/delete_ir.schema.json"),
        "UPSERT": read_json(output_dir / "schemas/upsert_ir.schema.json"),
    }
    for op, schema in schemas.items():
        require(schema_operation(schema) == op, violations, f"schema_operation_mismatch:{op}")
        require(schema.get("additionalProperties") is False, violations, f"schema_allows_additional_properties:{op}")
        text = json.dumps(schema, sort_keys=True)
        require("normalization" not in text, violations, f"schema_contains_normalization:{op}")
    insert_props = set(schemas["INSERT"].get("properties", {}))
    require(not (insert_props & {"conflict_target_ref", "update_columns", "upsert_action", "delete_predicate", "row_selector"}), violations, "insert_schema_has_non_insert_fields")
    update_required = set(schemas["UPDATE"].get("required", []))
    delete_required = set(schemas["DELETE"].get("required", []))
    upsert_required = set(schemas["UPSERT"].get("required", []))
    require({"operation", "table_ref", "row_selector", "assignments"} <= update_required, violations, "update_schema_missing_required_fields")
    require({"operation", "table_ref", "row_selector"} <= delete_required and "assignments" not in delete_required, violations, "delete_schema_contract_invalid")
    require({"operation", "table_ref", "conflict_target_ref", "insert_assignments", "update_policy"} <= upsert_required, violations, "upsert_schema_missing_required_fields")


def validate(output_dir: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    violations: list[str] = []
    checks = {
        "stage7a_hashes_recomputed": False,
        "architecture_spec_validated": False,
        "ir_schemas_validated": False,
        "traceability_validated": False,
        "ablation_registration_validated": False,
        "development_policy_validated": False,
    }

    for rel in ARTIFACTS + (LOCK_FILE,):
        if not (output_dir / rel).is_file():
            violations.append(f"missing_artifact:{rel}")
    if violations:
        return {"stage": STAGE, "status": "FAIL", "violations": violations, "model_called": False, "gpu_called": False, **checks}

    hashes = input_hashes(root, violations)
    checks["stage7a_hashes_recomputed"] = True
    manifest = read_json(output_dir / "STAGE7B_INPUT_MANIFEST.json")
    lock = read_json(output_dir / LOCK_FILE)
    require(manifest.get("hash_policy") == HASH_POLICY, violations, "manifest_hash_policy_mismatch")
    require(lock.get("hash_policy") == HASH_POLICY, violations, "lock_hash_policy_mismatch")
    require(manifest.get("input_hashes") == hashes, violations, "manifest_input_hashes_mismatch")
    require(lock.get("input_hashes") == hashes, violations, "lock_input_hashes_mismatch")
    current_hashes = {rel: sha256_file(output_dir / rel) for rel in ARTIFACTS}
    require(lock.get("artifact_hashes") == current_hashes, violations, "lock_artifact_hashes_mismatch")
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        require(lock.get(key) is False, violations, f"forbidden_flag_not_false:{key}")

    stage7a_lock = read_json(root / "stage7a_formal_failure_analysis/STAGE7A_FAILURE_ANALYSIS_LOCK.json")
    require(stage7a_lock.get("status") == "PASS_FAILURE_ANALYSIS_LOCKED", violations, "stage7a_not_locked_pass")
    require(stage7a_lock.get("model_called") is False and stage7a_lock.get("gpu_called") is False, violations, "stage7a_model_gpu_flag_invalid")
    stage7a_pipeline = read_json(root / "stage7a_formal_failure_analysis/PIPELINE_FAILURE_SUMMARY.json")
    stage7a_verification = read_json(root / "stage7a_formal_failure_analysis/VERIFICATION_FAILURE_SUMMARY.json")
    require(stage7a_pipeline.get("pipeline_failure_counts") == EXPECTED_PIPELINE, violations, "stage7a_pipeline_counts_changed")
    require(stage7a_verification.get("root_cause_family_prevalence") == EXPECTED_VERIFICATION, violations, "stage7a_verification_counts_changed")

    architecture = read_json(output_dir / "V2_ARCHITECTURE_SPEC.json")
    require(architecture.get("status") == "FROZEN_SPECIFICATION", violations, "architecture_not_frozen")
    require(len(architecture.get("core_pipeline", [])) == 5, violations, "architecture_core_component_count_not_5")
    require(architecture.get("primary_v2_depends_on_repair") is False, violations, "primary_v2_depends_on_repair")
    require(architecture.get("v2_implemented") is False, violations, "architecture_v2_implemented")
    checks["architecture_spec_validated"] = True

    op_spec = read_json(output_dir / "OPERATION_CONDITIONING_SPEC.json")
    require(op_spec.get("operation_classes") == ["INSERT", "UPDATE", "DELETE", "UPSERT"], violations, "operation_classes_not_frozen")
    require("conflict_target_ref" not in op_spec.get("operation_specific_allowed_fields", {}).get("INSERT", []), violations, "insert_allows_conflict_target")
    ref_spec = read_json(output_dir / "REFERENCE_CONSTRAINT_SPEC.json")
    require(ref_spec.get("unrestricted_reference_ids_allowed") is False, violations, "unrestricted_reference_ids_allowed")
    typed_spec = read_json(output_dir / "TYPED_MATERIALIZATION_SPEC.json")
    require(typed_spec.get("llm_normalization_decisions_allowed") is False, violations, "llm_normalization_allowed")
    complete_spec = read_json(output_dir / "COMPLETENESS_VERIFICATION_SPEC.json")
    require("all_required_slots_mapped" in complete_spec.get("checks", []), violations, "completeness_missing_required_slot_check")
    rep_spec = read_json(output_dir / "REPRESENTATION_CONTRACT_SPEC.json")
    require("additionalProperties=false" in rep_spec.get("contract", []), violations, "representation_contract_allows_extra_fields")
    abstain = read_json(output_dir / "ABSTENTION_POLICY_SPEC.json")
    require(abstain.get("true_ambiguity_direct_support_from_stage7a") == 0, violations, "true_ambiguity_support_not_zero")
    validate_schemas(output_dir, violations)
    checks["ir_schemas_validated"] = True

    trace = read_json(output_dir / "DESIGN_TO_EVIDENCE_TRACEABILITY.json")
    entries = trace_by_component(trace)
    require(trace.get("accuracy_target_registered") is False, violations, "accuracy_target_registered")
    require(entries.get("operation_conditioning", {}).get("stage7a_direct_support", {}).get("sample_count") == 204, violations, "operation_direct_support_not_204")
    require(entries.get("constrained_reference_selection", {}).get("stage7a_direct_support", {}).get("sample_count") == 190, violations, "reference_direct_support_not_190")
    require(entries.get("deterministic_typed_materialization", {}).get("stage7a_direct_support", {}).get("sample_count") == 133, violations, "typed_direct_support_not_133")
    require(entries.get("semantic_completeness_verification", {}).get("stage7a_direct_support", {}).get("sample_count") == 6, violations, "completeness_direct_support_not_6")
    require(entries.get("representation_schema_contract", {}).get("stage7a_direct_support", {}).get("sample_count") == 2, violations, "schema_contract_support_not_2")
    require(entries.get("explicit_abstention_for_true_ambiguity", {}).get("stage7a_direct_support", {}).get("sample_count") == 0, violations, "true_ambiguity_direct_support_not_0")
    for entry in entries.values():
        require("expected accuracy" not in json.dumps(entry, ensure_ascii=False).casefold(), violations, f"accuracy_forecast_present:{entry.get('component')}")
    checks["traceability_validated"] = True

    ablation = read_json(output_dir / "ABLATION_REGISTRATION.json")
    variants = {row.get("variant") for row in ablation.get("variants", [])}
    require(ablation.get("status") == "FROZEN_BEFORE_IMPLEMENTATION", violations, "ablation_not_frozen_before_implementation")
    require({"V2-FULL", "V2-A", "V2-B", "V2-C", "V2-D"} == variants, violations, "ablation_variant_set_invalid")
    checks["ablation_registration_validated"] = True

    policy = read_json(output_dir / "DEVELOPMENT_DATA_POLICY.json")
    require(policy.get("performance_tuning_on_481_allowed") is False, violations, "481_tuning_allowed")
    require("current 481 CRUDSQL Create test analyzed in Stage6/Stage7A" in policy.get("forbidden_for_selection_tuning", []), violations, "481_not_forbidden_for_selection")
    require("LiveSQLBench SQLite" in policy.get("untouched_external_benchmark", []), violations, "external_benchmark_not_protected")
    checks["development_policy_validated"] = True

    return {"stage": STAGE, "status": "PASS" if not violations else "FAIL", "violations": violations, "model_called": False, "gpu_called": False, **checks}


def validation_report_text(report: dict[str, Any]) -> str:
    lines = ["# Stage7B Validation Report", "", f"Status: {report['status']}", "", f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}", ""]
    for key in ("stage7a_hashes_recomputed", "architecture_spec_validated", "ir_schemas_validated", "traceability_validated", "ablation_registration_validated", "development_policy_validated"):
        lines.append(f"{key}: {str(report[key]).lower()}")
    lines.extend(["", f"model_called: {str(report['model_called']).lower()}", f"gpu_called: {str(report['gpu_called']).lower()}"])
    return "\n".join(lines) + "\n"


def write_report_and_update_lock(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = output_dir / "VALIDATION_REPORT.md"
    report_path.write_text(validation_report_text(report), encoding="utf-8")
    lock_path = output_dir / LOCK_FILE
    lock = read_json(lock_path)
    artifact_hashes = dict(lock.get("artifact_hashes") or {})
    artifact_hashes["VALIDATION_REPORT.md"] = sha256_file(report_path)
    lock["artifact_hashes"] = artifact_hashes
    write_json(lock_path, lock)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7b_v2_method_specification")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
