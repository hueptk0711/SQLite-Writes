from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a1_free_text_slot_discovery_amendment import (
    ARTIFACTS,
    HASH_POLICY,
    LOCK_FILE,
    PHASE_O_MAX_NEW_TOKENS,
    STAGE,
    STAGE7B_INPUTS,
    STAGE7C_PATCH2_INPUTS,
    artifact_hashes,
    canonical_json,
    input_hashes,
    materializable_slot_audit,
    phase_o_schema,
    read_json,
    sha256_file,
    write_json,
)


PASS_STATUS = "PASS_STAGE7B_A1_FREE_TEXT_SLOT_DISCOVERY_AMENDMENT_LOCKED"


def require(condition: bool, violations: list[str], code: str) -> None:
    if not condition:
        violations.append(code)


def schema_has_model_text_field(schema: dict[str, Any]) -> bool:
    text = canonical_json(schema)
    return '"text"' in text or '"value"' in text or '"raw_value"' in text


def validate(output_dir: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    violations: list[str] = []
    checks = {
        "input_hashes_recomputed": False,
        "materializable_audit_recomputed": False,
        "phase_o_schema_validated": False,
        "span_validation_validated": False,
        "evidence_slot_separation_validated": False,
        "completeness_amendment_validated": False,
        "ablation_amendment_validated": False,
        "capacity_amendment_validated": False,
    }
    for rel in ARTIFACTS + (LOCK_FILE,):
        if not (output_dir / rel).is_file():
            violations.append(f"missing_artifact:{rel}")
    if violations:
        return {"stage": STAGE, "status": "FAIL", "violations": violations, "model_called": False, "gpu_called": False, **checks}

    hashes = {}
    for rel in STAGE7B_INPUTS + STAGE7C_PATCH2_INPUTS:
        path = root / rel
        if not path.is_file():
            violations.append(f"missing_input:{rel}")
        else:
            hashes[rel] = sha256_file(path)
    checks["input_hashes_recomputed"] = True

    manifest = read_json(output_dir / "STAGE7B_A1_INPUT_MANIFEST.json")
    lock = read_json(output_dir / LOCK_FILE)
    require(manifest.get("hash_policy") == HASH_POLICY, violations, "manifest_hash_policy_mismatch")
    require(lock.get("hash_policy") == HASH_POLICY, violations, "lock_hash_policy_mismatch")
    require(manifest.get("input_hashes") == hashes, violations, "manifest_input_hashes_mismatch")
    require(lock.get("input_hashes") == hashes, violations, "lock_input_hashes_mismatch")
    require(lock.get("artifact_hashes") == artifact_hashes(output_dir), violations, "lock_artifact_hashes_mismatch")
    require(lock.get("status") in {"BUILT_PENDING_VALIDATION", PASS_STATUS}, violations, "lock_status_invalid")
    for key in ("model_called", "gpu_called", "v2_implemented", "experiment_run", "live_sql_bench_gt_opened"):
        require(lock.get(key) is False, violations, f"forbidden_flag_not_false:{key}")

    stage7b_lock = read_json(root / "stage7b_v2_method_specification" / "STAGE7B_V2_SPECIFICATION_LOCK.json")
    stage7c_lock = read_json(root / "stage7c_v2_development_data_protocol" / "STAGE7C_DATA_PROTOCOL_LOCK.json")
    require(stage7b_lock.get("status") == "PASS_V2_METHOD_SPECIFICATION_LOCKED", violations, "stage7b_base_not_locked")
    require(stage7c_lock.get("model_called") is False and stage7c_lock.get("gpu_called") is False, violations, "stage7c_model_gpu_flags_invalid")

    audit = read_json(output_dir / "MATERIALIZABLE_SLOT_AUDIT.json")
    recomputed_audit = materializable_slot_audit()
    require(audit == recomputed_audit, violations, "materializable_slot_audit_mismatch")
    require(audit["dev"]["substring_candidate_coverage_rate"] >= 0.95, violations, "dev_substring_coverage_not_reproduced")
    require(audit["dev"]["materializable_candidate_coverage_count"] == 257, violations, "dev_materializable_count_changed")
    require(audit["dev"]["gold_assignment_count"] == 845, violations, "dev_gold_assignment_count_changed")
    require(audit["dev"]["materializable_candidate_coverage_rate"] < 0.5, violations, "dev_materializable_coverage_not_low_enough_to_trigger_amendment")
    require(audit["dev"]["samples_missing_materializable_candidate"] == 217, violations, "dev_missing_materializable_sample_count_changed")
    require(audit["dev"]["required_slot_count"] == 4, violations, "dev_required_slot_count_changed")
    require(audit["dev"]["required_slots_per_gold_assignment"] < 0.01, violations, "dev_required_slot_ratio_not_near_zero")
    require(audit["train"]["materializable_candidate_coverage_count"] == 2366, violations, "train_materializable_count_changed")
    checks["materializable_audit_recomputed"] = True

    rationale = read_json(output_dir / "STAGE7B_A1_AMENDMENT_RATIONALE.json")
    require(rationale.get("decision") == "reopen Stage7B by amending Phase O from operation-only to operation plus grounded atomic semantic span selection", violations, "rationale_decision_changed")
    require(rationale.get("not_a_stage7c_regex_patch") is True, violations, "rationale_allows_regex_patch")

    schema = read_json(output_dir / "PHASE_O_JSON_SCHEMA.json")
    require(schema == phase_o_schema(), violations, "phase_o_schema_changed")
    require(schema.get("additionalProperties") is False, violations, "phase_o_schema_allows_extra_fields")
    require(schema.get("required") == ["operation", "value_spans"], violations, "phase_o_required_fields_changed")
    require(schema["properties"]["operation"]["enum"] == ["INSERT", "UPDATE", "DELETE", "UPSERT"], violations, "phase_o_operation_enum_changed")
    span_item = schema["properties"]["value_spans"]["items"]
    require(span_item.get("required") == ["span_ref", "start_char", "end_char"], violations, "phase_o_span_required_fields_changed")
    require(not schema_has_model_text_field(schema), violations, "phase_o_schema_allows_model_emitted_text")
    checks["phase_o_schema_validated"] = True

    span_spec = read_json(output_dir / "SPAN_VALIDATION_SPEC.json")
    require(span_spec.get("span_text_source") == "question[start_char:end_char] only", violations, "span_text_not_offset_derived")
    require(span_spec.get("model_emitted_text_allowed") is False, violations, "span_model_text_allowed")
    require(span_spec.get("duplicate_span_policy") == "reject", violations, "duplicate_span_policy_not_reject")
    require(span_spec.get("nested_span_policy") == "reject", violations, "nested_span_policy_not_reject")
    require(span_spec.get("partial_overlap_policy") == "reject", violations, "partial_overlap_policy_not_reject")
    checks["span_validation_validated"] = True

    separation = read_json(output_dir / "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json")
    require(separation.get("forbidden_mapping") == "do_not_convert_every_context_evidence_span_into_SLOT", violations, "evidence_slot_forbidden_mapping_missing")
    require(separation.get("semantic_slots_from_phase_o_only") is True, violations, "semantic_slots_not_phase_o_only")
    require(separation.get("broad_context_evidence_required") is False, violations, "broad_context_evidence_marked_required")
    checks["evidence_slot_separation_validated"] = True

    completeness = read_json(output_dir / "COMPLETENESS_AMENDED_SPEC.json")
    require(completeness.get("required_set") == "all SLOT_* created from accepted Phase O value_spans", violations, "completeness_required_set_changed")
    require("required_set - mapped_set" in completeness.get("missing", ""), violations, "completeness_missing_formula_changed")
    require("mapped_set - allowed_slot_set" in completeness.get("extra", ""), violations, "completeness_extra_formula_changed")
    checks["completeness_amendment_validated"] = True

    ablation = read_json(output_dir / "ABLATION_AMENDMENT.json")
    require(ablation == read_json(output_dir / "ABALATION_AMENDMENT.json"), violations, "ablation_alias_mismatch")
    require("V2-D_MINUS_COMPLETENESS_VERIFICATION" in ablation.get("amended_variants", {}), violations, "v2d_ablation_missing")
    require("V2-O_MINUS_SPAN_SELECTION" in ablation.get("amended_variants", {}), violations, "span_selection_diagnostic_missing")
    require(ablation.get("hidden_third_model_call_allowed") is False, violations, "ablation_allows_hidden_third_call")
    checks["ablation_amendment_validated"] = True

    capacity = read_json(output_dir / "GENERATION_CAPACITY_AMENDMENT.json")
    require(capacity.get("old_phase_o_max_new_tokens") == 32, violations, "old_phase_o_cap_changed")
    require(capacity.get("new_phase_o_max_new_tokens") == PHASE_O_MAX_NEW_TOKENS, violations, "new_phase_o_cap_changed")
    require(capacity.get("phase_m_max_new_tokens") == 8192, violations, "phase_m_cap_changed")
    require("c03e6d358207e414f1eca0bb1891e29f1db0e242" in capacity.get("model_revision_unchanged", ""), violations, "model_revision_not_preserved")
    checks["capacity_amendment_validated"] = True

    return {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "dev_materializable_candidate_coverage": audit["dev"]["materializable_candidate_coverage_rate"],
        "dev_required_slots_per_gold_assignment": audit["dev"]["required_slots_per_gold_assignment"],
        "model_called": False,
        "gpu_called": False,
        "v2_implemented": False,
        "experiment_run": False,
        "live_sql_bench_gt_opened": False,
        **checks,
    }


def validation_report_text(report: dict[str, Any]) -> str:
    lines = [
        "# Stage7B A1 Validation Report",
        "",
        f"Status: {report['status']}",
        "",
        f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}",
        "",
        f"dev_materializable_candidate_coverage: {report.get('dev_materializable_candidate_coverage')}",
        f"dev_required_slots_per_gold_assignment: {report.get('dev_required_slots_per_gold_assignment')}",
        "",
    ]
    for key in (
        "input_hashes_recomputed",
        "materializable_audit_recomputed",
        "phase_o_schema_validated",
        "span_validation_validated",
        "evidence_slot_separation_validated",
        "completeness_amendment_validated",
        "ablation_amendment_validated",
        "capacity_amendment_validated",
    ):
        lines.append(f"{key}: {str(report.get(key)).lower()}")
    lines.extend(
        [
            "",
            f"model_called: {str(report.get('model_called')).lower()}",
            f"gpu_called: {str(report.get('gpu_called')).lower()}",
            f"v2_implemented: {str(report.get('v2_implemented')).lower()}",
            f"experiment_run: {str(report.get('experiment_run')).lower()}",
            f"live_sql_bench_gt_opened: {str(report.get('live_sql_bench_gt_opened')).lower()}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report_and_update_lock(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = output_dir / "VALIDATION_REPORT.md"
    report_path.write_text(validation_report_text(report), encoding="utf-8")
    lock_path = output_dir / LOCK_FILE
    lock = read_json(lock_path)
    if report["status"] == "PASS":
        lock["status"] = PASS_STATUS
    hashes = artifact_hashes(output_dir)
    hashes["VALIDATION_REPORT.md"] = sha256_file(report_path)
    lock["artifact_hashes"] = hashes
    write_json(lock_path, lock)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7b_a1_free_text_slot_discovery_amendment")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
