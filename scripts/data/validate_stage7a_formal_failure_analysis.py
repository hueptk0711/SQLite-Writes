from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STAGE = "Stage7A_FORMAL_FAILURE_ANALYSIS"
FINAL_N = 481
REPRESENTATIVE_ARM = "d_f_g1_vnext"
EQUIVALENT_ARMS = ("original_mp_fs_plus", "d_g1_control", "d_f_g1_vnext")
HASH_POLICY = "text_sha256_canonical_lf"
INPUTS = (
    "stage6_frozen_statistical_analysis/STAGE6K_STATISTICAL_LOCK.json",
    "stage6_replay_evaluation/STAGE6J_REPLAY_EVALUATION_LOCK.json",
    "stage6_replay_evaluation/REPLAY_ARM_MANIFEST.json",
    "stage6_replay_evaluation/replay_outcomes/original_mp_fs_plus.jsonl",
    "stage6_replay_evaluation/replay_outcomes/d_g1_control.jsonl",
    "stage6_replay_evaluation/replay_outcomes/d_f_g1_vnext.jsonl",
    "stage6_replay_evaluation/stage6i_generation_inputs/stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl",
    "stage6_final_registration_revision/STAGE6E_FINAL_REGISTRATION_LOCK.json",
    "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
    "stage6_final_registration_revision/artifacts/FINAL_GOLD_CORPUS.jsonl",
    "stage6_final_registration_revision/artifacts/FINAL_GOLD_PROGRAMS.jsonl",
    "stage6_final_registration_revision/artifacts/FINAL_GOLD_WRITE_PLANS.jsonl",
    "stage6_final_registration_revision/artifacts/FINAL_GOLD_POST_STATE_HASHES.jsonl",
    "stage6_final_registration_revision/artifacts/FINAL_REVIEWED_GOLD_PROVENANCE.jsonl",
)
ARTIFACTS = (
    "STAGE7A_INPUT_MANIFEST.json",
    "FAILURE_TAXONOMY_SPEC.json",
    "MPFS_ARM_EQUIVALENCE_AUDIT.json",
    "FAILURE_RECORDS.jsonl",
    "PIPELINE_FAILURE_SUMMARY.json",
    "VERIFICATION_FAILURE_SUMMARY.json",
    "FAILURE_OVERLAP_MATRIX.json",
    "FAILURE_COMBINATION_COUNTS.json",
    "PARSE_FAILURE_ANALYSIS.jsonl",
    "STATE_MISMATCH_ANALYSIS.jsonl",
    "DESIGN_REQUIREMENT_TRACEABILITY.json",
    "STAGE7A_FAILURE_ANALYSIS_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)
HASHED_ARTIFACTS = tuple(rel for rel in ARTIFACTS if rel != "STAGE7A_FAILURE_ANALYSIS_LOCK.json")

ERROR_CODE_TO_ROOT = {
    "NEEDS_CLARIFICATION": "operation_semantics",
    "UNKNOWN_CONSTRAINT_ID": "invalid_reference",
    "UNKNOWN_EVIDENCE_ID": "invalid_reference",
    "UNKNOWN_COLUMN_ID": "invalid_reference",
    "LOSSY_NORMALIZATION_REJECTED": "normalization",
    "MISSING_UPDATE_COLUMN_IDS": "slot_or_update_completeness",
}
VERIFICATION_ROOT_LABELS = [
    "operation_semantics",
    "invalid_reference",
    "normalization",
    "slot_or_update_completeness",
    "unsupported_or_true_ambiguity",
    "other",
]

PARSE_OBSERVED_LABELS = ["representation_schema_nonconformance"]

STATE_SUBTYPE_TO_INDICATIVE_FAMILY = {
    "missing_assignment_or_under_write": "slot_completeness",
    "wrong_value_or_evidence": "value_grounding_or_materialization",
    "wrong_target_column": "slot_grounding",
    "extra_assignment_or_over_write": "slot_completeness",
    "wrong_row_or_cardinality": "row_cardinality",
    "wrong_operation_or_conflict_semantics": "operation_semantics",
    "unresolved_other": "unresolved",
}

STATE_INDICATIVE_FAMILIES = [
    "slot_completeness",
    "slot_grounding",
    "value_grounding_or_materialization",
    "row_cardinality",
    "operation_semantics",
    "unresolved",
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_file(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def input_hashes(root: Path, violations: list[str]) -> dict[str, str]:
    hashes = {}
    for rel in INPUTS:
        path = root / rel
        if not path.is_file():
            violations.append(f"missing_input:{rel}")
            continue
        hashes[rel] = sha256_file(path)
    return hashes


def load_by_id(root: Path, rel: str, violations: list[str]) -> dict[str, dict[str, Any]]:
    path = root / rel
    if not path.is_file():
        violations.append(f"missing_jsonl:{rel}")
        return {}
    rows = read_jsonl(path)
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    if len(ids) != len(set(ids)):
        violations.append(f"duplicate_ids:{rel}")
    return {str(row["stage6_sample_id"]): row for row in rows if "stage6_sample_id" in row}


def normalized_errors(row: dict[str, Any]) -> list[dict[str, Any]]:
    errors = []
    for err in row.get("verification_errors") or []:
        errors.append({"error_code": err.get("error_code"), "path": err.get("path"), "message": err.get("message")})
    return sorted(errors, key=lambda item: (str(item["error_code"]), str(item["path"]), str(item["message"])))


def arm_equivalence(root: Path, violations: list[str]) -> dict[str, Any]:
    outcomes = {arm: load_by_id(root, f"stage6_replay_evaluation/replay_outcomes/{arm}.jsonl", violations) for arm in EQUIVALENT_ARMS}
    ids = sorted(set(outcomes[REPRESENTATIVE_ARM]))
    fields = ["target_state_correct", "failure_stage", "parse_status", "verification_status", "execution_status", "admission_status", "candidate_program_sha256", "predicted_post_state_sha256", "gold_post_state_sha256", "failure_reason"]
    differences = []
    for sample_id in ids:
        ref = outcomes[REPRESENTATIVE_ARM][sample_id]
        ref_payload = {field: ref.get(field) for field in fields}
        ref_payload["verification_errors"] = normalized_errors(ref)
        for arm in EQUIVALENT_ARMS:
            observed = outcomes[arm].get(sample_id)
            payload = {field: observed.get(field) for field in fields} if observed else {}
            payload["verification_errors"] = normalized_errors(observed or {})
            if payload != ref_payload:
                differences.append({"stage6_sample_id": sample_id, "arm": arm, "representative": ref_payload, "observed": payload})
                break
    return {
        "stage": STAGE,
        "status": "PASS" if not differences else "FAIL",
        "representative_arm": REPRESENTATIVE_ARM,
        "equivalent_arms_checked": list(EQUIVALENT_ARMS),
        "checked_sample_count": len(ids),
        "comparison_fields": fields + ["verification_errors"],
        "verification_error_fields_compared": ["error_code", "path", "message"],
        "verification_error_details_policy": "details are excluded from semantic arm equivalence because Stage2-F may add reference_repair provenance without changing failure code/path/message",
        "difference_count": len(differences),
        "differences": differences[:20],
    }


def extract_assignments(row: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = row.get("candidate_program") or {}
    program = candidate.get("program") or {}
    assignments = []
    for statement in program.get("statements") or []:
        sql = str(statement.get("sql") or "")
        assignments.append({"columns": re.findall(r'"(col_\d+)"', sql), "values": list(statement.get("params") or []), "row_count": statement.get("row_count"), "sql": sql})
    return assignments


def classify_state(row: dict[str, Any], gold: dict[str, Any]) -> tuple[list[str], list[str], bool]:
    assignments = extract_assignments(row)
    gold_columns = list(gold.get("columns") or [])
    gold_values = list(gold.get("values") or [])
    if not assignments:
        return ["unresolved_other"], ["unresolved"], True
    subtypes: set[str] = set()
    if len(assignments) != 1 or assignments[0].get("row_count") != 1:
        subtypes.add("wrong_row_or_cardinality")
    columns = assignments[0]["columns"]
    values = assignments[0]["values"]
    if "INSERT" not in str(assignments[0].get("sql", "")).upper() or gold.get("operation") != "INSERT":
        subtypes.add("wrong_operation_or_conflict_semantics")
    if set(columns) - set(gold_columns):
        subtypes.add("extra_assignment_or_over_write")
    if set(gold_columns) - set(columns):
        subtypes.add("missing_assignment_or_under_write")
    shared = [column for column in columns if column in gold_columns]
    for column in shared:
        ci = columns.index(column)
        gi = gold_columns.index(column)
        if ci < len(values) and gi < len(gold_values) and values[ci] != gold_values[gi]:
            subtypes.add("wrong_value_or_evidence")
    if not shared and columns and gold_columns:
        subtypes.add("wrong_target_column")
    final_subtypes = sorted(subtypes) or ["unresolved_other"]
    families = sorted({STATE_SUBTYPE_TO_INDICATIVE_FAMILY.get(subtype, "unresolved") for subtype in final_subtypes})
    return final_subtypes, families, "unresolved_other" in final_subtypes


def root_labels(errors: list[dict[str, Any]]) -> list[str]:
    labels = sorted({ERROR_CODE_TO_ROOT.get(str(err.get("error_code")), "other") for err in errors})
    return labels or ["other"]


def parse_failure_type_from_raw(raw: dict[str, Any]) -> str:
    valid_json, _json_error = parse_json_from_fenced(str(raw.get("raw_output") or ""))
    return "schema_nonconformance_valid_json" if valid_json else "malformed_json"


def rebuild_records(root: Path, violations: list[str]) -> list[dict[str, Any]]:
    samples = load_by_id(root, "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl", violations)
    outcomes = load_by_id(root, "stage6_replay_evaluation/replay_outcomes/d_f_g1_vnext.jsonl", violations)
    raw_rows = load_by_id(root, "stage6_replay_evaluation/stage6i_generation_inputs/stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl", violations)
    gold_programs = load_by_id(root, "stage6_final_registration_revision/artifacts/FINAL_GOLD_PROGRAMS.jsonl", violations)
    gold_plans = load_by_id(root, "stage6_final_registration_revision/artifacts/FINAL_GOLD_WRITE_PLANS.jsonl", violations)
    if len(samples) != FINAL_N:
        violations.append(f"sample_count_mismatch:{len(samples)}")
    if set(samples) != set(outcomes):
        violations.append("sample_id_set_mismatch")
    records = []
    for sample_id in sorted(samples):
        if sample_id not in outcomes:
            continue
        sample = samples[sample_id]
        row = outcomes[sample_id]
        errors = normalized_errors(row)
        failure_stage = str(row.get("failure_stage"))
        if failure_stage == "verification":
            labels = root_labels(errors)
            subtypes: list[str] = []
            state_families: list[str] = []
            observed_labels = labels
            implication_labels = labels
            parse_failure_type = None
            root_cause_status = "direct_verifier_causal_evidence"
            mode = "deterministic_verifier_error_code"
            unresolved = False
        elif failure_stage == "parse":
            labels = []
            subtypes = []
            state_families = []
            observed_labels = PARSE_OBSERVED_LABELS
            implication_labels = PARSE_OBSERVED_LABELS
            parse_failure_type = parse_failure_type_from_raw(raw_rows[sample_id])
            root_cause_status = "observed_not_causally_resolved"
            mode = "deterministic_raw_parse_analysis"
            unresolved = True
        elif failure_stage == "state_mismatch":
            subtypes, state_families, unresolved = classify_state(row, gold_plans[sample_id])
            labels = []
            observed_labels = subtypes
            implication_labels = state_families
            parse_failure_type = None
            root_cause_status = "observed_difference_not_direct_verifier_causal_label"
            mode = "deterministic_candidate_vs_gold_write_plan"
        else:
            labels = ["other"]
            subtypes = []
            state_families = []
            observed_labels = ["other"]
            implication_labels = ["other"]
            parse_failure_type = None
            root_cause_status = "unresolved"
            mode = "deterministic_pipeline_stage_fallback"
            unresolved = True
        records.append(
            {
                "stage6_sample_id": sample_id,
                "source_group": sample.get("source_group"),
                "representative_arm": REPRESENTATIVE_ARM,
                "target_state_correct": bool(row.get("target_state_correct")),
                "failure_stage": failure_stage,
                "error_codes": sorted({str(err.get("error_code")) for err in errors if err.get("error_code")}),
                "raw_error_occurrence_codes": [str(err.get("error_code")) for err in errors if err.get("error_code")],
                "root_cause_labels": labels,
                "root_cause_status": root_cause_status,
                "observed_failure_labels": observed_labels,
                "architectural_implication_labels": implication_labels,
                "parse_failure_type": parse_failure_type,
                "evidence_paths": sorted({str(err.get("path")) for err in errors if err.get("path")}),
                "state_mismatch_subtypes": subtypes,
                "state_mismatch_families": state_families,
                "classification_mode": mode,
                "unresolved": unresolved,
                "candidate_program_sha256": row.get("candidate_program_sha256"),
                "parsed_plan_sha256": row.get("parsed_plan_sha256"),
                "materialized_plan_sha256": row.get("materialized_plan_sha256"),
                "predicted_post_state_sha256": row.get("predicted_post_state_sha256"),
                "gold_post_state_sha256": row.get("gold_post_state_sha256"),
                "gold_program_sha256": hashlib.sha256(canonical_json(gold_programs[sample_id]).encode("utf-8")).hexdigest(),
            }
        )
    return records


def pipeline_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["failure_stage"] for row in records)
    parse_types = Counter(row["parse_failure_type"] for row in records if row["failure_stage"] == "parse")
    state_subtypes: Counter[str] = Counter()
    state_families: Counter[str] = Counter()
    root_status = Counter(row["root_cause_status"] for row in records)
    for row in records:
        if row["failure_stage"] == "state_mismatch":
            state_subtypes.update(row["state_mismatch_subtypes"])
            state_families.update(row["state_mismatch_families"])
    return {
        "stage": STAGE,
        "representative_arm": REPRESENTATIVE_ARM,
        "n": len(records),
        "final_n": len(records),
        "pipeline_failure_counts": dict(sorted(counts.items())),
        "parse_failure_type_prevalence": dict(sorted(parse_types.items())),
        "state_mismatch_subtype_prevalence": dict(sorted(state_subtypes.items())),
        "state_mismatch_indicative_family_prevalence": dict(sorted(state_families.items())),
        "root_cause_status_counts": dict(sorted(root_status.items())),
        "target_state_correct": sum(1 for row in records if row["target_state_correct"]),
        "target_state_incorrect": sum(1 for row in records if not row["target_state_correct"]),
    }


def verification_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    verification = [row for row in records if row["failure_stage"] == "verification"]
    family_counts: Counter[str] = Counter()
    samples_with_code: Counter[str] = Counter()
    raw_occurrences: Counter[str] = Counter()
    for row in verification:
        family_counts.update(row["root_cause_labels"])
        samples_with_code.update(row["error_codes"])
        raw_occurrences.update(row["raw_error_occurrence_codes"])
    return {
        "stage": STAGE,
        "verification_failure_n": len(verification),
        "all_verification_failures_accounted_for": len(verification) == 436,
        "root_cause_family_prevalence": dict(sorted(family_counts.items())),
        "samples_with_error_code": dict(sorted(samples_with_code.items())),
        "raw_error_occurrence_counts": dict(sorted(raw_occurrences.items())),
        "sample_prevalence_note": "samples_with_error_code counts samples with at least one verifier error code; raw_error_occurrence_counts counts verifier error objects",
        "multi_label": True,
    }


def overlap_matrix(records: list[dict[str, Any]]) -> dict[str, Any]:
    verification = [row for row in records if row["failure_stage"] == "verification"]
    matrix = {left: {right: sum(1 for row in verification if left in row["root_cause_labels"] and right in row["root_cause_labels"]) for right in VERIFICATION_ROOT_LABELS} for left in VERIFICATION_ROOT_LABELS}
    return {"stage": STAGE, "scope": "verification_failures", "labels": VERIFICATION_ROOT_LABELS, "matrix": matrix}


def combination_counts(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter("+".join(row["root_cause_labels"]) for row in records if row["failure_stage"] == "verification")
    return {"stage": STAGE, "scope": "verification_failures", "combination_counts": [{"root_cause_combination": key, "sample_count": value} for key, value in sorted(counts.items())], "total": sum(counts.values())}


def parse_json_from_fenced(raw_output: str) -> tuple[bool, str | None]:
    text = raw_output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        json.loads(text)
        return True, None
    except json.JSONDecodeError as exc:
        return False, str(exc)


def parse_analysis(root: Path, records: list[dict[str, Any]], violations: list[str]) -> list[dict[str, Any]]:
    raw = load_by_id(root, "stage6_replay_evaluation/stage6i_generation_inputs/stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl", violations)
    rows = []
    for record in records:
        if record["failure_stage"] != "parse":
            continue
        raw_row = raw[record["stage6_sample_id"]]
        valid_json, json_error = parse_json_from_fenced(str(raw_row.get("raw_output") or ""))
        rows.append({"stage6_sample_id": record["stage6_sample_id"], "raw_generation_stream": "shared_mp_fs_plus_generation", "raw_generation_row_sha256": raw_row.get("raw_generation_row_sha256"), "raw_output_sha256": raw_row.get("raw_output_sha256"), "hit_max_new_tokens": bool(raw_row.get("hit_max_new_tokens")), "output_token_count": raw_row.get("output_token_count"), "valid_json_after_fence_strip": valid_json, "json_parse_error": json_error, "parse_failure_type": "schema_nonconformance_valid_json" if valid_json else "malformed_json", "root_cause_status": "observed_not_causally_resolved", "observed_failure_labels": PARSE_OBSERVED_LABELS, "classification_mode": "deterministic_raw_json_schema_check"})
    return rows


def state_analysis(root: Path, records: list[dict[str, Any]], violations: list[str]) -> list[dict[str, Any]]:
    outcomes = load_by_id(root, "stage6_replay_evaluation/replay_outcomes/d_f_g1_vnext.jsonl", violations)
    gold_plans = load_by_id(root, "stage6_final_registration_revision/artifacts/FINAL_GOLD_WRITE_PLANS.jsonl", violations)
    rows = []
    for record in records:
        if record["failure_stage"] != "state_mismatch":
            continue
        sample_id = record["stage6_sample_id"]
        row = outcomes[sample_id]
        gold = gold_plans[sample_id]
        subtypes, families, unresolved = classify_state(row, gold)
        rows.append({"stage6_sample_id": sample_id, "candidate_program_sha256": row.get("candidate_program_sha256"), "gold_write_plan_sha256": hashlib.sha256(canonical_json(gold).encode("utf-8")).hexdigest(), "candidate_assignments": extract_assignments(row), "gold_columns": gold.get("columns") or [], "gold_values": gold.get("values") or [], "state_mismatch_subtypes": subtypes, "state_mismatch_families": families, "direct_causal_root_labels": [], "root_cause_status": "observed_difference_not_direct_verifier_causal_label", "unresolved": unresolved, "classification_mode": "deterministic_candidate_vs_gold_write_plan"})
    return rows


def traceability(records: list[dict[str, Any]]) -> dict[str, Any]:
    def ids_for(predicate) -> list[str]:
        return [row["stage6_sample_id"] for row in records if predicate(row)]

    def support(source: str, ids: list[str]) -> dict[str, Any]:
        return {"source": source, "sample_count": len(ids), "example_sample_ids": ids[:10]}

    rows = [
        {"design_requirement_id": "operation_conditioned_ir", "root_cause_label": "operation_semantics", "design_requirement": "operation-conditioned IR and conflict/update semantics gate", "direct_support": support("verification_errors:operation_semantics", ids_for(lambda row: row["failure_stage"] == "verification" and "operation_semantics" in row["root_cause_labels"])), "indicative_support": [support("state_mismatch_family:row_cardinality", ids_for(lambda row: row["failure_stage"] == "state_mismatch" and "row_cardinality" in row["state_mismatch_families"])), support("state_mismatch_family:operation_semantics", ids_for(lambda row: row["failure_stage"] == "state_mismatch" and "operation_semantics" in row["state_mismatch_families"]))]},
        {"design_requirement_id": "constrained_reference_selection", "root_cause_label": "invalid_reference", "design_requirement": "constrained enumerated reference selection", "direct_support": support("verification_errors:invalid_reference", ids_for(lambda row: row["failure_stage"] == "verification" and "invalid_reference" in row["root_cause_labels"])), "indicative_support": [support("state_mismatch_subtype:wrong_target_column", ids_for(lambda row: row["failure_stage"] == "state_mismatch" and "wrong_target_column" in row["state_mismatch_subtypes"]))]},
        {"design_requirement_id": "typed_materialization", "root_cause_label": "normalization", "design_requirement": "deterministic typed materialization", "direct_support": support("verification_errors:normalization", ids_for(lambda row: row["failure_stage"] == "verification" and "normalization" in row["root_cause_labels"])), "indicative_support": [support("state_mismatch_subtype:wrong_value_or_evidence", ids_for(lambda row: row["failure_stage"] == "state_mismatch" and "wrong_value_or_evidence" in row["state_mismatch_subtypes"]))]},
        {"design_requirement_id": "semantic_completeness_verification", "root_cause_label": "slot_or_update_completeness", "design_requirement": "semantic completeness verification for write slots/update columns", "direct_support": support("verification_errors:slot_or_update_completeness", ids_for(lambda row: row["failure_stage"] == "verification" and "slot_or_update_completeness" in row["root_cause_labels"])), "indicative_support": [support("state_mismatch_subtype:missing_assignment_or_under_write", ids_for(lambda row: row["failure_stage"] == "state_mismatch" and "missing_assignment_or_under_write" in row["state_mismatch_subtypes"])), support("state_mismatch_subtype:extra_assignment_or_over_write", ids_for(lambda row: row["failure_stage"] == "state_mismatch" and "extra_assignment_or_over_write" in row["state_mismatch_subtypes"]))]},
        {"design_requirement_id": "representation_schema_contract", "root_cause_label": "representation_schema_nonconformance", "design_requirement": "schema-constrained representation contract between generation and parser", "direct_support": support("parse_failure_type:schema_nonconformance_valid_json", ids_for(lambda row: row["failure_stage"] == "parse" and row["parse_failure_type"] == "schema_nonconformance_valid_json")), "indicative_support": []},
        {"design_requirement_id": "explicit_abstention_for_true_ambiguity", "root_cause_label": "unsupported_or_true_ambiguity", "design_requirement": "explicit abstention path only for independently demonstrated source ambiguity", "direct_support": support("independent_true_ambiguity_audit", ids_for(lambda row: "unsupported_or_true_ambiguity" in row["root_cause_labels"])), "indicative_support": []},
    ]
    return {"stage": STAGE, "status": "DESIGN_REQUIREMENTS_ONLY", "support_model": "direct_support_vs_indicative_support", "no_v2_implementation": True, "traceability": rows}


def taxonomy_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "classification_scope": "formal deterministic analysis of frozen Stage6J representative MP-FS arm",
        "pipeline_stage_taxonomy": ["parse", "verification", "execution", "state_mismatch"],
        "evidence_layers": ["pipeline_stage", "directly_observed_failure", "architectural_implication"],
        "verification_direct_root_cause_labels": VERIFICATION_ROOT_LABELS,
        "parse_observed_failure_labels": PARSE_OBSERVED_LABELS,
        "state_mismatch_indicative_families": STATE_INDICATIVE_FAMILIES,
        "verification_error_code_mapping": ERROR_CODE_TO_ROOT,
        "state_mismatch_subtype_to_indicative_family": STATE_SUBTYPE_TO_INDICATIVE_FAMILY,
        "causal_policy": {
            "verification": "direct verifier error codes are treated as causal evidence",
            "parse": "valid JSON schema nonconformance is observed but not evidence of true task ambiguity",
            "state_mismatch": "executable output differences are observed subtypes and indicative families, not verifier invalid-reference roots",
        },
        "frozen_before_classification": True,
        "multi_label": True,
        "model_called": False,
        "gpu_called": False,
    }


def compare(name: str, saved: Any, rebuilt: Any, violations: list[str]) -> None:
    if saved != rebuilt:
        violations.append(f"{name}_recompute_mismatch")


def validate(output_dir: Path, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    violations: list[str] = []
    checks = {"records_recomputed": False, "pipeline_summary_recomputed": False, "verification_summary_recomputed": False, "overlap_recomputed": False, "parse_failures_recomputed": False, "state_mismatches_recomputed": False, "arm_equivalence_recomputed": False, "traceability_recomputed": False}
    for rel in ARTIFACTS:
        if not (output_dir / rel).is_file():
            violations.append(f"missing_artifact:{rel}")
    if violations:
        return {"status": "FAIL", "violations": violations, "stage": STAGE, "final_n": FINAL_N, "model_called": False, "gpu_called": False, **checks}

    hashes = input_hashes(root, violations)
    records = rebuild_records(root, violations)
    checks["records_recomputed"] = True
    compare("failure_records", read_jsonl(output_dir / "FAILURE_RECORDS.jsonl"), records, violations)
    compare("taxonomy_spec", read_json(output_dir / "FAILURE_TAXONOMY_SPEC.json"), taxonomy_spec(), violations)
    arm_eq = arm_equivalence(root, violations)
    checks["arm_equivalence_recomputed"] = True
    compare("arm_equivalence", read_json(output_dir / "MPFS_ARM_EQUIVALENCE_AUDIT.json"), arm_eq, violations)
    compare("pipeline_summary", read_json(output_dir / "PIPELINE_FAILURE_SUMMARY.json"), pipeline_summary(records), violations)
    checks["pipeline_summary_recomputed"] = True
    compare("verification_summary", read_json(output_dir / "VERIFICATION_FAILURE_SUMMARY.json"), verification_summary(records), violations)
    checks["verification_summary_recomputed"] = True
    compare("overlap_matrix", read_json(output_dir / "FAILURE_OVERLAP_MATRIX.json"), overlap_matrix(records), violations)
    compare("combination_counts", read_json(output_dir / "FAILURE_COMBINATION_COUNTS.json"), combination_counts(records), violations)
    checks["overlap_recomputed"] = True
    compare("parse_analysis", read_jsonl(output_dir / "PARSE_FAILURE_ANALYSIS.jsonl"), parse_analysis(root, records, violations), violations)
    checks["parse_failures_recomputed"] = True
    compare("state_analysis", read_jsonl(output_dir / "STATE_MISMATCH_ANALYSIS.jsonl"), state_analysis(root, records, violations), violations)
    checks["state_mismatches_recomputed"] = True
    compare("traceability", read_json(output_dir / "DESIGN_REQUIREMENT_TRACEABILITY.json"), traceability(records), violations)
    checks["traceability_recomputed"] = True

    input_manifest = read_json(output_dir / "STAGE7A_INPUT_MANIFEST.json")
    if input_manifest.get("hash_policy") != HASH_POLICY:
        violations.append("input_manifest_hash_policy_mismatch")
    if input_manifest.get("input_hashes") != hashes:
        violations.append("input_manifest_hashes_mismatch")
    lock = read_json(output_dir / "STAGE7A_FAILURE_ANALYSIS_LOCK.json")
    if lock.get("hash_policy") != HASH_POLICY:
        violations.append("lock_hash_policy_mismatch")
    if lock.get("input_hashes") != hashes:
        violations.append("lock_input_hashes_mismatch")
    current_hashes = {rel: sha256_file(output_dir / rel) for rel in HASHED_ARTIFACTS}
    if lock.get("artifact_hashes") != current_hashes:
        violations.append("lock_artifact_hashes_mismatch")
    for key in ("model_called", "gpu_called", "stage6j_modified", "stage6k_modified", "gold_modified", "v2_implemented"):
        if lock.get(key) is not False:
            violations.append(f"forbidden_flag_not_false:{key}")

    pipe = pipeline_summary(records)["pipeline_failure_counts"]
    if pipe != {"parse": 2, "state_mismatch": 43, "verification": 436}:
        violations.append(f"pipeline_counts_unexpected:{pipe}")
    if len(records) != FINAL_N or any(not row.get("source_group") for row in records):
        violations.append("failure_records_denominator_or_source_group_invalid")
    if arm_eq.get("status") != "PASS" or arm_eq.get("difference_count") != 0:
        violations.append("mpfs_arm_equivalence_failed")
    if len([r for r in records if r["failure_stage"] == "verification"]) != 436:
        violations.append("verification_failure_n_not_436")
    if len([r for r in records if r["failure_stage"] == "parse"]) != 2:
        violations.append("parse_failure_n_not_2")
    if len([r for r in records if r["failure_stage"] == "state_mismatch"]) != 43:
        violations.append("state_mismatch_n_not_43")
    verification = [row for row in records if row["failure_stage"] == "verification"]
    state = [row for row in records if row["failure_stage"] == "state_mismatch"]
    parse = [row for row in records if row["failure_stage"] == "parse"]
    family_counts: Counter[str] = Counter()
    samples_with_code: Counter[str] = Counter()
    for row in verification:
        family_counts.update(row["root_cause_labels"])
        samples_with_code.update(row["error_codes"])
    if dict(sorted(family_counts.items())) != {"invalid_reference": 190, "normalization": 133, "operation_semantics": 204, "slot_or_update_completeness": 6}:
        violations.append(f"verification_direct_family_counts_unexpected:{dict(sorted(family_counts.items()))}")
    if "error_code_counts" in read_json(output_dir / "VERIFICATION_FAILURE_SUMMARY.json"):
        violations.append("ambiguous_error_code_counts_field_present")
    if any("unsupported_or_true_ambiguity" in row["root_cause_labels"] for row in parse):
        violations.append("parse_failure_promoted_to_true_ambiguity")
    if any(row["parse_failure_type"] != "schema_nonconformance_valid_json" for row in parse):
        violations.append("parse_failure_type_not_schema_nonconformance_valid_json")
    if any(row["root_cause_labels"] for row in state):
        violations.append("state_mismatch_promoted_to_direct_root_cause")
    if any("invalid_reference" in row["root_cause_labels"] for row in state):
        violations.append("state_mismatch_promoted_to_invalid_reference")
    state_subtypes: Counter[str] = Counter()
    for row in state:
        state_subtypes.update(row["state_mismatch_subtypes"])
    if dict(sorted(state_subtypes.items())) != {"extra_assignment_or_over_write": 16, "missing_assignment_or_under_write": 41, "wrong_row_or_cardinality": 7, "wrong_target_column": 8, "wrong_value_or_evidence": 35}:
        violations.append(f"state_subtype_counts_unexpected:{dict(sorted(state_subtypes.items()))}")
    trace = read_json(output_dir / "DESIGN_REQUIREMENT_TRACEABILITY.json")
    trace_by_id = {row.get("design_requirement_id"): row for row in trace.get("traceability", [])}
    if trace_by_id.get("constrained_reference_selection", {}).get("direct_support", {}).get("sample_count") != 190:
        violations.append("direct_invalid_reference_support_not_190")
    if trace_by_id.get("explicit_abstention_for_true_ambiguity", {}).get("direct_support", {}).get("sample_count") != 0:
        violations.append("unsupported_true_ambiguity_direct_support_not_zero")
    if trace_by_id.get("representation_schema_contract", {}).get("direct_support", {}).get("sample_count") != 2:
        violations.append("parse_schema_contract_support_not_two")

    return {"status": "PASS" if not violations else "FAIL", "violations": violations, "stage": STAGE, "final_n": FINAL_N, "model_called": False, "gpu_called": False, **checks}


def validation_report_text(report: dict[str, Any]) -> str:
    lines = ["# Stage7A Validation Report", "", f"Status: {report['status']}", "", f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}", "", f"final_n: {report['final_n']}"]
    for key in ("records_recomputed", "pipeline_summary_recomputed", "verification_summary_recomputed", "overlap_recomputed", "parse_failures_recomputed", "state_mismatches_recomputed", "arm_equivalence_recomputed", "traceability_recomputed"):
        lines.append(f"{key}: {str(report[key]).lower()}")
    lines.extend(["", f"model_called: {str(report['model_called']).lower()}", f"gpu_called: {str(report['gpu_called']).lower()}"])
    return "\n".join(lines) + "\n"


def write_report_and_update_lock(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = output_dir / "VALIDATION_REPORT.md"
    report_path.write_text(validation_report_text(report), encoding="utf-8")
    lock_path = output_dir / "STAGE7A_FAILURE_ANALYSIS_LOCK.json"
    if lock_path.is_file():
        lock = read_json(lock_path)
        artifact_hashes = dict(lock.get("artifact_hashes") or {})
        artifact_hashes["VALIDATION_REPORT.md"] = sha256_file(report_path)
        lock["artifact_hashes"] = artifact_hashes
        write_json(lock_path, lock)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7a_formal_failure_analysis")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
