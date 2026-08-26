from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATE = "20260826"
STAGE = "Stage7A_FORMAL_FAILURE_ANALYSIS"
FINAL_N = 481
REPRESENTATIVE_ARM = "d_f_g1_vnext"
EQUIVALENT_ARMS = ("original_mp_fs_plus", "d_g1_control", "d_f_g1_vnext")
MODEL_CALLED = False
GPU_CALLED = False
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
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)

ERROR_CODE_TO_ROOT = {
    "NEEDS_CLARIFICATION": "operation_semantics",
    "UNKNOWN_CONSTRAINT_ID": "invalid_reference",
    "UNKNOWN_EVIDENCE_ID": "invalid_reference",
    "UNKNOWN_COLUMN_ID": "invalid_reference",
    "LOSSY_NORMALIZATION_REJECTED": "normalization",
    "MISSING_UPDATE_COLUMN_IDS": "slot_or_update_completeness",
}

STATE_SUBTYPE_TO_ROOT = {
    "missing_assignment_or_under_write": "slot_or_update_completeness",
    "wrong_value_or_evidence": "invalid_reference",
    "wrong_target_column": "invalid_reference",
    "extra_assignment_or_over_write": "invalid_reference",
    "wrong_row_or_cardinality": "operation_semantics",
    "wrong_operation_or_conflict_semantics": "operation_semantics",
    "unresolved_other": "other",
}


class Stage7AError(RuntimeError):
    pass


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def reset_output_dir(path: Path, *, force: bool) -> None:
    if path.exists():
        if not force:
            raise Stage7AError(f"Output directory already exists: {path}. Use --force.")
        if path.resolve() != (PROJECT_ROOT / "stage7a_formal_failure_analysis").resolve():
            raise Stage7AError(f"Refusing to remove unexpected output dir: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def input_hashes() -> dict[str, str]:
    hashes = {}
    for rel in INPUTS:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            raise Stage7AError(f"Missing frozen input: {rel}")
        hashes[rel] = sha256_file(path)
    return hashes


def taxonomy_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "classification_scope": "formal deterministic analysis of frozen Stage6J representative MP-FS arm",
        "pipeline_stage_taxonomy": ["parse", "verification", "execution", "state_mismatch"],
        "root_cause_labels": [
            "operation_semantics",
            "invalid_reference",
            "normalization",
            "slot_or_update_completeness",
            "unsupported_or_true_ambiguity",
            "other",
        ],
        "verification_error_code_mapping": ERROR_CODE_TO_ROOT,
        "state_mismatch_subtype_mapping": STATE_SUBTYPE_TO_ROOT,
        "frozen_before_classification": True,
        "multi_label": True,
        "model_called": False,
        "gpu_called": False,
    }


def outcome_maps() -> dict[str, dict[str, dict[str, Any]]]:
    root = PROJECT_ROOT / "stage6_replay_evaluation" / "replay_outcomes"
    return {arm: {row["stage6_sample_id"]: row for row in read_jsonl(root / f"{arm}.jsonl")} for arm in EQUIVALENT_ARMS}


def load_by_id(rel: str) -> dict[str, dict[str, Any]]:
    return {row["stage6_sample_id"]: row for row in read_jsonl(PROJECT_ROOT / rel)}


def normalized_errors(row: dict[str, Any]) -> list[dict[str, Any]]:
    errors = []
    for err in row.get("verification_errors") or []:
        errors.append(
            {
                "error_code": err.get("error_code"),
                "path": err.get("path"),
                "message": err.get("message"),
            }
        )
    return sorted(errors, key=lambda item: (str(item["error_code"]), str(item["path"]), str(item["message"])))


def arm_equivalence_audit(outcomes: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    ids = sorted(set(outcomes[REPRESENTATIVE_ARM]))
    fields = [
        "target_state_correct",
        "failure_stage",
        "parse_status",
        "verification_status",
        "execution_status",
        "admission_status",
        "candidate_program_sha256",
        "predicted_post_state_sha256",
        "gold_post_state_sha256",
        "failure_reason",
    ]
    differences = []
    for sample_id in ids:
        ref = outcomes[REPRESENTATIVE_ARM][sample_id]
        ref_payload = {field: ref.get(field) for field in fields}
        ref_payload["verification_errors"] = normalized_errors(ref)
        for arm in EQUIVALENT_ARMS:
            row = outcomes[arm].get(sample_id)
            payload = {field: row.get(field) for field in fields} if row else {}
            payload["verification_errors"] = normalized_errors(row or {})
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


def root_labels_from_errors(errors: list[dict[str, Any]]) -> list[str]:
    labels = sorted({ERROR_CODE_TO_ROOT.get(str(err.get("error_code")), "other") for err in errors})
    return labels or ["other"]


def evidence_paths(errors: list[dict[str, Any]]) -> list[str]:
    return sorted({str(err.get("path")) for err in errors if err.get("path")})


def extract_candidate_assignments(row: dict[str, Any]) -> list[dict[str, Any]]:
    candidate = row.get("candidate_program") or {}
    program = candidate.get("program") or {}
    assignments = []
    for statement in program.get("statements") or []:
        sql = str(statement.get("sql") or "")
        columns = re.findall(r'"(col_\d+)"', sql)
        params = list(statement.get("params") or [])
        assignments.append(
            {
                "columns": columns,
                "values": params,
                "row_count": statement.get("row_count"),
                "sql": sql,
            }
        )
    return assignments


def state_mismatch_subtypes(row: dict[str, Any], gold_plan: dict[str, Any]) -> list[str]:
    assignments = extract_candidate_assignments(row)
    gold_columns = list(gold_plan.get("columns") or [])
    gold_values = list(gold_plan.get("values") or [])
    if not assignments:
        return ["unresolved_other"]
    subtypes: set[str] = set()
    if len(assignments) != 1 or assignments[0].get("row_count") != 1:
        subtypes.add("wrong_row_or_cardinality")
    columns = assignments[0]["columns"]
    values = assignments[0]["values"]
    if "INSERT" not in str(assignments[0].get("sql", "")).upper() or gold_plan.get("operation") != "INSERT":
        subtypes.add("wrong_operation_or_conflict_semantics")
    if set(columns) - set(gold_columns):
        subtypes.add("extra_assignment_or_over_write")
    if set(gold_columns) - set(columns):
        subtypes.add("missing_assignment_or_under_write")
    shared = [column for column in columns if column in gold_columns]
    for column in shared:
        c_index = columns.index(column)
        g_index = gold_columns.index(column)
        if c_index < len(values) and g_index < len(gold_values) and values[c_index] != gold_values[g_index]:
            subtypes.add("wrong_value_or_evidence")
    if not shared and columns and gold_columns:
        subtypes.add("wrong_target_column")
    return sorted(subtypes) or ["unresolved_other"]


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


def parse_failure_rows(representative_rows: dict[str, dict[str, Any]], raw_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sample_id, row in sorted(representative_rows.items()):
        if row.get("failure_stage") != "parse":
            continue
        raw = raw_rows[sample_id]
        valid_json, json_error = parse_json_from_fenced(str(raw.get("raw_output") or ""))
        classification = "schema_mismatch_valid_json" if valid_json else "malformed_json"
        rows.append(
            {
                "stage6_sample_id": sample_id,
                "raw_generation_stream": "shared_mp_fs_plus_generation",
                "raw_generation_row_sha256": raw.get("raw_generation_row_sha256"),
                "raw_output_sha256": raw.get("raw_output_sha256"),
                "hit_max_new_tokens": bool(raw.get("hit_max_new_tokens")),
                "output_token_count": raw.get("output_token_count"),
                "valid_json_after_fence_strip": valid_json,
                "json_parse_error": json_error,
                "parse_failure_type": classification,
                "classification_mode": "deterministic_raw_json_schema_check",
            }
        )
    return rows


def state_mismatch_rows(representative_rows: dict[str, dict[str, Any]], gold_plans: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sample_id, row in sorted(representative_rows.items()):
        if row.get("failure_stage") != "state_mismatch":
            continue
        gold = gold_plans[sample_id]
        subtypes = state_mismatch_subtypes(row, gold)
        roots = sorted({STATE_SUBTYPE_TO_ROOT.get(subtype, "other") for subtype in subtypes})
        rows.append(
            {
                "stage6_sample_id": sample_id,
                "candidate_program_sha256": row.get("candidate_program_sha256"),
                "gold_write_plan_sha256": hashlib.sha256(canonical_json(gold).encode("utf-8")).hexdigest(),
                "candidate_assignments": extract_candidate_assignments(row),
                "gold_columns": gold.get("columns") or [],
                "gold_values": gold.get("values") or [],
                "state_mismatch_subtypes": subtypes,
                "root_cause_labels": roots,
                "unresolved": "unresolved_other" in subtypes,
                "classification_mode": "deterministic_candidate_vs_gold_write_plan",
            }
        )
    return rows


def failure_records(
    samples: dict[str, dict[str, Any]],
    representative_rows: dict[str, dict[str, Any]],
    gold_programs: dict[str, dict[str, Any]],
    gold_plans: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    state_by_id = {row["stage6_sample_id"]: row for row in state_mismatch_rows(representative_rows, gold_plans)}
    records = []
    for sample_id in sorted(samples):
        sample = samples[sample_id]
        row = representative_rows[sample_id]
        stage = str(row.get("failure_stage"))
        errors = normalized_errors(row)
        if stage == "verification":
            labels = root_labels_from_errors(errors)
            subtypes: list[str] = []
            mode = "deterministic_verifier_error_code"
            unresolved = False
        elif stage == "parse":
            labels = ["unsupported_or_true_ambiguity"]
            subtypes = []
            mode = "deterministic_raw_parse_analysis"
            unresolved = False
        elif stage == "state_mismatch":
            state = state_by_id[sample_id]
            labels = state["root_cause_labels"]
            subtypes = state["state_mismatch_subtypes"]
            mode = "deterministic_candidate_vs_gold_write_plan"
            unresolved = bool(state["unresolved"])
        else:
            labels = ["other"]
            subtypes = []
            mode = "deterministic_pipeline_stage_fallback"
            unresolved = True
        records.append(
            {
                "stage6_sample_id": sample_id,
                "source_group": sample["source_group"],
                "representative_arm": REPRESENTATIVE_ARM,
                "target_state_correct": bool(row.get("target_state_correct")),
                "failure_stage": stage,
                "error_codes": sorted({str(err.get("error_code")) for err in errors if err.get("error_code")}),
                "root_cause_labels": labels,
                "evidence_paths": evidence_paths(errors),
                "state_mismatch_subtypes": subtypes,
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
    return {
        "stage": STAGE,
        "representative_arm": REPRESENTATIVE_ARM,
        "n": len(records),
        "final_n": len(records),
        "pipeline_failure_counts": dict(sorted(counts.items())),
        "target_state_correct": sum(1 for row in records if row["target_state_correct"]),
        "target_state_incorrect": sum(1 for row in records if not row["target_state_correct"]),
    }


def verification_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    verification = [row for row in records if row["failure_stage"] == "verification"]
    family_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()
    for row in verification:
        family_counts.update(row["root_cause_labels"])
        code_counts.update(row["error_codes"])
    return {
        "stage": STAGE,
        "verification_failure_n": len(verification),
        "all_verification_failures_accounted_for": len(verification) == 436,
        "root_cause_family_prevalence": dict(sorted(family_counts.items())),
        "error_code_counts": dict(sorted(code_counts.items())),
        "multi_label": True,
    }


def overlap_matrix(records: list[dict[str, Any]]) -> dict[str, Any]:
    labels = taxonomy_spec()["root_cause_labels"]
    verification = [row for row in records if row["failure_stage"] == "verification"]
    matrix = {
        left: {right: sum(1 for row in verification if left in row["root_cause_labels"] and right in row["root_cause_labels"]) for right in labels}
        for left in labels
    }
    return {"stage": STAGE, "scope": "verification_failures", "labels": labels, "matrix": matrix}


def combination_counts(records: list[dict[str, Any]]) -> dict[str, Any]:
    verification = [row for row in records if row["failure_stage"] == "verification"]
    counts = Counter("+".join(row["root_cause_labels"]) for row in verification)
    return {
        "stage": STAGE,
        "scope": "verification_failures",
        "combination_counts": [{"root_cause_combination": key, "sample_count": value} for key, value in sorted(counts.items())],
        "total": sum(counts.values()),
    }


def traceability(records: list[dict[str, Any]]) -> dict[str, Any]:
    requirements = {
        "operation_semantics": "operation-conditioned IR and conflict/update semantics gate",
        "invalid_reference": "constrained enumerated reference selection",
        "normalization": "deterministic typed materialization",
        "slot_or_update_completeness": "semantic completeness verification for write slots/update columns",
        "unsupported_or_true_ambiguity": "explicit abstention path for unresolved source ambiguity",
        "other": "manual audit queue before V2 method changes",
    }
    rows = []
    for label, requirement in requirements.items():
        matching = [row["stage6_sample_id"] for row in records if label in row["root_cause_labels"]]
        rows.append({"root_cause_label": label, "design_requirement": requirement, "supporting_sample_count": len(matching), "example_sample_ids": matching[:10]})
    return {"stage": STAGE, "status": "DESIGN_REQUIREMENTS_ONLY", "no_v2_implementation": True, "traceability": rows}


def reviewer_readme() -> str:
    return f"""# Stage7A Formal Failure Analysis

This package analyzes frozen V1 failures only. It does not implement V2.

Commands:
```bash
python scripts/data/build_stage7a_formal_failure_analysis.py --force
python scripts/data/validate_stage7a_formal_failure_analysis.py
python -m pytest -q tests/test_stage7a_formal_failure_analysis.py
```

Representative arm: `{REPRESENTATIVE_ARM}`

Hash policy: `{HASH_POLICY}`.

Frozen scope:
- no model calls
- no GPU calls
- no prompt or heuristic changes
- no sample, gold, or replay changes
"""


def pending_validation_report() -> str:
    return "# Stage7A Validation Report\n\nStatus: PENDING_VALIDATION\n\nRun `python scripts/data/validate_stage7a_formal_failure_analysis.py` to generate the final report.\n"


def lock(output_dir: Path, hashes: dict[str, str]) -> dict[str, Any]:
    artifact_hashes = {rel: sha256_file(output_dir / rel) for rel in ARTIFACTS}
    return {
        "stage": STAGE,
        "status": "PASS_FAILURE_ANALYSIS_LOCKED",
        "date": DATE,
        "final_n": FINAL_N,
        "representative_arm": REPRESENTATIVE_ARM,
        "hash_policy": HASH_POLICY,
        "input_hashes": hashes,
        "artifact_hashes": artifact_hashes,
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "stage6j_modified": False,
        "stage6k_modified": False,
        "gold_modified": False,
        "v2_implemented": False,
    }


def build_stage7a(output_dir: Path, *, force: bool = False) -> dict[str, Any]:
    reset_output_dir(output_dir, force=force)
    hashes = input_hashes()
    samples = load_by_id("stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl")
    gold_programs = load_by_id("stage6_final_registration_revision/artifacts/FINAL_GOLD_PROGRAMS.jsonl")
    gold_plans = load_by_id("stage6_final_registration_revision/artifacts/FINAL_GOLD_WRITE_PLANS.jsonl")
    raw_rows = load_by_id("stage6_replay_evaluation/stage6i_generation_inputs/stage6_confirmation_run_outputs/raw_generations/shared_mp_fs_plus_generation.jsonl")
    outcomes = outcome_maps()
    representative_rows = outcomes[REPRESENTATIVE_ARM]
    if len(samples) != FINAL_N or set(samples) != set(representative_rows):
        raise Stage7AError("Stage6E denominator and representative arm IDs must match exactly.")
    records = failure_records(samples, representative_rows, gold_programs, gold_plans)
    parse_rows = parse_failure_rows(representative_rows, raw_rows)
    state_rows = state_mismatch_rows(representative_rows, gold_plans)

    write_json(output_dir / "STAGE7A_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "final_n": FINAL_N, "hash_policy": HASH_POLICY, "input_hashes": hashes, "model_called": False, "gpu_called": False})
    write_json(output_dir / "FAILURE_TAXONOMY_SPEC.json", taxonomy_spec())
    write_json(output_dir / "MPFS_ARM_EQUIVALENCE_AUDIT.json", arm_equivalence_audit(outcomes))
    write_jsonl(output_dir / "FAILURE_RECORDS.jsonl", records)
    write_json(output_dir / "PIPELINE_FAILURE_SUMMARY.json", pipeline_summary(records))
    write_json(output_dir / "VERIFICATION_FAILURE_SUMMARY.json", verification_summary(records))
    write_json(output_dir / "FAILURE_OVERLAP_MATRIX.json", overlap_matrix(records))
    write_json(output_dir / "FAILURE_COMBINATION_COUNTS.json", combination_counts(records))
    write_jsonl(output_dir / "PARSE_FAILURE_ANALYSIS.jsonl", parse_rows)
    write_jsonl(output_dir / "STATE_MISMATCH_ANALYSIS.jsonl", state_rows)
    write_json(output_dir / "DESIGN_REQUIREMENT_TRACEABILITY.json", traceability(records))
    (output_dir / "VALIDATION_REPORT.md").write_text(pending_validation_report(), encoding="utf-8")
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    write_json(output_dir / "STAGE7A_FAILURE_ANALYSIS_LOCK.json", lock(output_dir, hashes))
    return {
        "stage": STAGE,
        "status": "PASS_BUILT",
        "final_n": len(records),
        "pipeline_failure_counts": pipeline_summary(records)["pipeline_failure_counts"],
        "verification_failure_n": verification_summary(records)["verification_failure_n"],
        "parse_failure_n": len(parse_rows),
        "state_mismatch_n": len(state_rows),
        "arm_equivalence_status": arm_equivalence_audit(outcomes)["status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7a_formal_failure_analysis")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_stage7a(args.output_dir, force=args.force), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
