from __future__ import annotations

import csv
import hashlib
import json
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


WORKSPACE = Path(__file__).resolve().parents[3]
RESULT_ARCHIVE = (
    WORKSPACE
    / "04_results"
    / "00_incoming_from_server"
    / "mp_fs_plus_final300_protocol_v2_1_rev2_adjudicated_20260731T121531Z.tar.gz"
)
HOLDOUT_ZIP = (
    WORKSPACE
    / "03_protocol_and_data"
    / "final_holdout_release"
    / "mp_fs_plus_external_holdout_300_20260731.zip"
)
ABLATION_JSONL = (
    WORKSPACE
    / "04_results"
    / "03_analysis_work"
    / "reporting_v2_4_20260801"
    / "downstream_ablation_per_sample.jsonl"
)
OUTPUT_ROOT = WORKSPACE / "04_results" / "mp_fs_plus_failure_analysis_v1"
OUTPUT_DIR = OUTPUT_ROOT / "stage1_mpfsplus_failure_analysis"

RUN_PREFIX = (
    "experiments/external_holdout/"
    "final300_qwen25_7b_protocol_v2_out8192_20260731"
)

METHOD_SLUGS = {
    "direct": "d_fs_m",
    "jfs": "j_fs_m",
    "mpfsplus": "mp_fs_plus",
}

STAGE_ORDER = [
    "generation",
    "parse",
    "reference_resolution",
    "materialization",
    "verification",
    "compilation",
    "semantic_gate",
    "preflight",
    "execution",
    "state_mismatch",
]

STAGE_COLUMNS = [
    "generation_ok",
    "parse_ok",
    "reference_resolution_ok",
    "materialization_ok",
    "verification_ok",
    "compilation_ok",
    "semantic_gate_ok",
    "preflight_ok",
    "admission_ok",
    "execution_ok",
    "state_correct",
]

REFERENCE_CODES = {
    "UNKNOWN_COLUMN_ID",
    "UNKNOWN_SOURCE_FIELD_ID",
    "UNKNOWN_CONSTRAINT_ID",
}
MATERIALIZATION_CODES = {
    "MISSING_SOURCE_FIELD",
    "UNRESOLVED_SOURCE_FIELD",
    "DUPLICATE_TARGET_COLUMN_AFTER_EVIDENCE_GROUNDING",
    "LOSSY_NORMALIZATION_REJECTED",
}
VERIFICATION_CODES = {
    "MISSING_REQUIRED_COLUMN",
    "MISSING_UPDATE_COLUMN_IDS",
    "NEEDS_CLARIFICATION",
}

REASON_MAP = {
    "parse_error": "GEN_UNPARSEABLE_OUTPUT",
    "UNKNOWN_COLUMN_ID": "REF_UNKNOWN_COLUMN",
    "UNKNOWN_SOURCE_FIELD_ID": "REF_INVALID_SOURCE_REF",
    "UNKNOWN_CONSTRAINT_ID": "REF_INVALID_SOURCE_REF",
    "MISSING_SOURCE_FIELD": "VALUE_MISSING",
    "UNRESOLVED_SOURCE_FIELD": "VALUE_MISSING",
    "DUPLICATE_TARGET_COLUMN_AFTER_EVIDENCE_GROUNDING": "VALUE_WRONG_EVIDENCE_SPAN",
    "LOSSY_NORMALIZATION_REJECTED": "VALUE_NORMALIZATION_ERROR",
    "MISSING_REQUIRED_COLUMN": "TARGET_MISSING_COLUMN",
    "MISSING_UPDATE_COLUMN_IDS": "CONFLICT_WRONG_UPDATE_COLUMNS",
    "NEEDS_CLARIFICATION": "CONFLICT_MISSING",
    "preflight_abstention": "PREFLIGHT_CONSTRAINT",
    "wrong_state": "STATE_WRONG_VALUE",
    "execution_error": "PREFLIGHT_SQL_ERROR",
}

ROOT_CAUSE_MAP = {
    "parse_error": "LLM_SEMANTIC_ERROR",
    "UNKNOWN_COLUMN_ID": "GROUNDING_ERROR",
    "UNKNOWN_SOURCE_FIELD_ID": "GROUNDING_ERROR",
    "UNKNOWN_CONSTRAINT_ID": "GROUNDING_ERROR",
    "MISSING_SOURCE_FIELD": "MATERIALIZATION_ERROR",
    "UNRESOLVED_SOURCE_FIELD": "MATERIALIZATION_ERROR",
    "DUPLICATE_TARGET_COLUMN_AFTER_EVIDENCE_GROUNDING": "MATERIALIZATION_ERROR",
    "LOSSY_NORMALIZATION_REJECTED": "MATERIALIZATION_ERROR",
    "MISSING_REQUIRED_COLUMN": "LLM_SEMANTIC_ERROR",
    "MISSING_UPDATE_COLUMN_IDS": "REPRESENTATION_LIMITATION",
    "NEEDS_CLARIFICATION": "REPRESENTATION_LIMITATION",
    "preflight_abstention": "PREFLIGHT_ERROR",
    "wrong_state": "LLM_SEMANTIC_ERROR",
}

ABSTENTION_REASON_BY_STAGE = {
    "generation": "planner_failure",
    "parse": "planner_failure",
    "reference_resolution": "grounding_failure",
    "materialization": "grounding_failure",
    "verification": "verification_reject",
    "semantic_gate": "risk_reject",
    "preflight": "preflight_reject",
    "compilation": "unsupported",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl_tar(bundle: tarfile.TarFile, member: str) -> list[dict[str, Any]]:
    handle = bundle.extractfile(member)
    if handle is None:
        raise ValueError(f"Cannot read tar member: {member}")
    return [
        json.loads(line)
        for line in handle.read().decode("utf-8").splitlines()
        if line.strip()
    ]


def read_json_tar(bundle: tarfile.TarFile, member: str) -> dict[str, Any]:
    handle = bundle.extractfile(member)
    if handle is None:
        raise ValueError(f"Cannot read tar member: {member}")
    return json.loads(handle.read().decode("utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_holdout_samples() -> dict[str, dict[str, Any]]:
    with zipfile.ZipFile(HOLDOUT_ZIP) as archive:
        rows = json.loads(
            archive.read("mp_fs_plus_external_holdout_300_20260731/dataset.final.json")
        )
    return {str(row["id"]): row for row in rows}


def load_run_artifacts() -> dict[str, Any]:
    output: dict[str, Any] = {}
    with tarfile.open(RESULT_ARCHIVE, "r:gz") as bundle:
        for label, slug in METHOD_SLUGS.items():
            prefix = f"{RUN_PREFIX}/{slug}"
            output[label] = {
                "config": read_json_tar(bundle, f"{prefix}/config.json"),
                "manifest": read_json_tar(bundle, f"{prefix}/manifest.json"),
                "run_lock": read_json_tar(bundle, f"{prefix}/run_lock.json"),
                "model_manifest": read_json_tar(bundle, f"{prefix}/model_manifest.json"),
                "metrics": read_json_tar(bundle, f"{prefix}/metrics.json"),
                "evaluation": read_jsonl_tar(bundle, f"{prefix}/evaluation.jsonl"),
                "raw": read_jsonl_tar(bundle, f"{prefix}/raw_generations.jsonl"),
                "parsed": read_jsonl_tar(bundle, f"{prefix}/parsed_mapping_plans.jsonl"),
                "materialized": read_jsonl_tar(bundle, f"{prefix}/materialized_write_plans.jsonl"),
                "verification": read_jsonl_tar(bundle, f"{prefix}/verification.jsonl"),
                "compiled": read_jsonl_tar(bundle, f"{prefix}/compiled_programs.jsonl"),
                "execution": read_jsonl_tar(bundle, f"{prefix}/execution_logs.jsonl"),
            }
        output["report_final"] = read_json_tar(bundle, "artifacts/reports/final_matrix_results.json")
        output["server_model_manifest"] = read_json_tar(bundle, "artifacts/server/final_model_manifest.json")
        output["protocol"] = read_json_tar(bundle, "configs/experiments/final_protocol.json")
    return output


def by_sample(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["sample_id"]): row for row in rows}


def first_error_code(verification: dict[str, Any], evaluation: dict[str, Any]) -> str | None:
    errors = verification.get("errors") or []
    if errors:
        return str(errors[0].get("error_code") or "")
    return evaluation.get("error_type")


def classify_first_failure(
    evaluation: dict[str, Any],
    raw: dict[str, Any],
    parsed: dict[str, Any],
    verification: dict[str, Any],
    compiled: dict[str, Any],
    execution: dict[str, Any],
) -> str:
    if evaluation.get("target_state_correct"):
        return "none"
    if raw.get("status") != "success" or evaluation.get("generation_status") not in {None, "success"}:
        return "generation"
    if parsed.get("parse_status") != "success" or evaluation.get("parse_status") != "success":
        return "parse"
    code = first_error_code(verification, evaluation)
    if verification.get("status") == "invalid":
        if code in REFERENCE_CODES:
            return "reference_resolution"
        if code in MATERIALIZATION_CODES:
            return "materialization"
        return "verification"
    if compiled.get("status") not in {"success"}:
        return "compilation"
    preflight = execution.get("preflight") or evaluation.get("preflight") or {}
    if not preflight.get("accepted"):
        if preflight.get("error_class") == "semantic_grounding_risk":
            return "semantic_gate"
        return "preflight"
    if not evaluation.get("execution_success"):
        return "execution"
    return "state_mismatch"


def reason_code_for(stage: str, verification: dict[str, Any], evaluation: dict[str, Any]) -> str:
    raw_code = first_error_code(verification, evaluation)
    if stage == "semantic_gate":
        return "RISK_TRUE_REJECT"
    if stage == "preflight":
        message = str(evaluation.get("error_message") or "")
        if "FOREIGN KEY" in message.upper():
            return "PREFLIGHT_FK"
        if "UNIQUE" in message.upper():
            return "PREFLIGHT_UNIQUE"
        if "NOT NULL" in message.upper():
            return "PREFLIGHT_NOT_NULL"
        return "PREFLIGHT_CONSTRAINT"
    if stage == "state_mismatch":
        return "STATE_WRONG_VALUE"
    if stage == "compilation":
        return "COMPILER_ERROR"
    return REASON_MAP.get(str(raw_code), str(raw_code or "UNKNOWN"))


def reason_detail(verification: dict[str, Any], evaluation: dict[str, Any]) -> str:
    errors = verification.get("errors") or []
    if errors:
        return str(errors[0].get("message") or "")
    return str(evaluation.get("error_message") or evaluation.get("error_type") or "")


def root_cause_for(
    stage: str,
    reason_code: str,
    verification: dict[str, Any],
    evaluation: dict[str, Any],
    oracle_correct: bool | None,
) -> str:
    raw_code = first_error_code(verification, evaluation)
    if stage in {"reference_resolution", "materialization", "verification"} and oracle_correct:
        return "VERIFIER_OVER_REJECTION"
    if stage == "semantic_gate" and oracle_correct:
        return "RISK_GATE_OVER_REJECTION"
    if stage == "preflight":
        return "PREFLIGHT_ERROR"
    if stage == "compilation":
        return "COMPILER_ERROR"
    if stage == "state_mismatch":
        return "LLM_SEMANTIC_ERROR"
    return ROOT_CAUSE_MAP.get(str(raw_code), "UNKNOWN")


def stage_statuses(
    stage: str,
    evaluation: dict[str, Any],
    raw: dict[str, Any],
    parsed: dict[str, Any],
    verification: dict[str, Any],
    compiled: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, str]:
    statuses = {column: "NA" for column in STAGE_COLUMNS}
    statuses["generation_ok"] = "1" if raw.get("status") == "success" else "0"
    if statuses["generation_ok"] == "0":
        return statuses
    statuses["parse_ok"] = (
        "1" if parsed.get("parse_status") == "success" and evaluation.get("parse_status") == "success" else "0"
    )
    if statuses["parse_ok"] == "0":
        return statuses
    statuses["reference_resolution_ok"] = "0" if stage == "reference_resolution" else "1"
    if statuses["reference_resolution_ok"] == "0":
        return statuses
    statuses["materialization_ok"] = "0" if stage == "materialization" else "1"
    if statuses["materialization_ok"] == "0":
        return statuses
    statuses["verification_ok"] = "1" if verification.get("status") == "valid" else "0"
    if statuses["verification_ok"] == "0":
        return statuses
    statuses["compilation_ok"] = "1" if compiled.get("status") == "success" else "0"
    if statuses["compilation_ok"] == "0":
        return statuses
    preflight = execution.get("preflight") or evaluation.get("preflight") or {}
    semantic_fail = preflight.get("error_class") == "semantic_grounding_risk"
    statuses["semantic_gate_ok"] = "0" if semantic_fail else "1"
    if statuses["semantic_gate_ok"] == "0":
        return statuses
    statuses["preflight_ok"] = "1" if preflight.get("accepted") else "0"
    if statuses["preflight_ok"] == "0":
        return statuses
    statuses["admission_ok"] = "1" if evaluation.get("accepted_output") else "0"
    if statuses["admission_ok"] == "0":
        return statuses
    statuses["execution_ok"] = "1" if evaluation.get("execution_success") else "0"
    if statuses["execution_ok"] == "0":
        return statuses
    statuses["state_correct"] = "1" if evaluation.get("target_state_correct") else "0"
    return statuses


def oracle_lookup() -> dict[tuple[str, str], dict[str, Any]]:
    rows = read_jsonl(ABLATION_JSONL)
    return {(str(row["sample_id"]), str(row["variant"])): row for row in rows}


def bypass_variant(stage: str) -> str | None:
    if stage in {"verification", "reference_resolution", "materialization"}:
        return "V0_no_verifier_no_provenance_no_semantic_gate_no_preflight"
    if stage == "semantic_gate":
        return "V2_hard_verifier_plus_provenance"
    if stage == "preflight":
        return "V2_5_plus_semantic_risk_gate"
    return None


def oracle_correct_for(sample_id: str, stage: str, oracle: dict[tuple[str, str], dict[str, Any]]) -> bool | None:
    variant = bypass_variant(stage)
    if not variant:
        return None
    row = oracle.get((sample_id, variant))
    if row is None:
        return None
    return bool(row.get("target_state_correct"))


def pair_category(direct_correct: bool, jfs_correct: bool, mp_correct: bool) -> str:
    if direct_correct and jfs_correct and mp_correct:
        return "ALL_CORRECT"
    if direct_correct and jfs_correct and not mp_correct:
        return "DIRECT_J_CORRECT_MP_WRONG"
    if direct_correct and not mp_correct:
        return "DIRECT_CORRECT_MP_WRONG"
    if jfs_correct and not mp_correct:
        return "J_CORRECT_MP_WRONG"
    if mp_correct and not direct_correct and not jfs_correct:
        return "ONLY_MP_CORRECT"
    if mp_correct and not direct_correct:
        return "MP_CORRECT_DIRECT_WRONG"
    if mp_correct and not jfs_correct:
        return "MP_CORRECT_J_WRONG"
    return "ALL_WRONG"


def why_mp_failed(stage: str, reason_code: str) -> str:
    if stage == "reference_resolution":
        return "grounding_failure"
    if stage == "materialization":
        return "evidence_failure"
    if stage in {"verification", "semantic_gate", "preflight"}:
        if "RISK" in reason_code or "VERIFY" in reason_code:
            return "over_rejection"
        return "extra_representation_constraint"
    if reason_code.startswith("CONFLICT"):
        return "dependency_failure"
    return "wrong_plan"


def unique_success_feature(sample: dict[str, Any], evaluation: dict[str, Any]) -> str:
    if sample.get("conflict_sensitive"):
        return "conflict identification"
    if sample.get("multi_table") or "relational" in str(sample.get("complexity") or ""):
        return "dependency ordering"
    if evaluation.get("preflight_accepted"):
        return "transactional preflight"
    if sample.get("input_mode") == "free_text":
        return "provenance grounding"
    return "deterministic compilation"


def format_percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return ""
    return f"{100.0 * numerator / denominator:.2f}"


def build_master() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    samples = load_holdout_samples()
    artifacts = load_run_artifacts()
    oracle = oracle_lookup()
    mp = artifacts["mpfsplus"]
    indexes = {
        key: by_sample(mp[key])
        for key in ["evaluation", "raw", "parsed", "verification", "compiled", "execution"]
    }
    direct_eval = by_sample(artifacts["direct"]["evaluation"])
    jfs_eval = by_sample(artifacts["jfs"]["evaluation"])
    rows: list[dict[str, Any]] = []
    for sample_id in sorted(samples):
        sample = samples[sample_id]
        evaluation = indexes["evaluation"][sample_id]
        raw = indexes["raw"].get(sample_id, {})
        parsed = indexes["parsed"].get(sample_id, {})
        verification = indexes["verification"].get(sample_id, {})
        compiled = indexes["compiled"].get(sample_id, {})
        execution = indexes["execution"].get(sample_id, {})
        stage = classify_first_failure(evaluation, raw, parsed, verification, compiled, execution)
        reason = reason_code_for(stage, verification, evaluation) if stage != "none" else "NONE"
        oracle_correct = oracle_correct_for(sample_id, stage, oracle)
        root_cause = (
            "NONE"
            if stage == "none"
            else root_cause_for(stage, reason, verification, evaluation, oracle_correct)
        )
        statuses = stage_statuses(stage, evaluation, raw, parsed, verification, compiled, execution)
        direct_correct = bool(direct_eval[sample_id].get("target_state_correct"))
        jfs_correct = bool(jfs_eval[sample_id].get("target_state_correct"))
        mp_correct = bool(evaluation.get("target_state_correct"))
        preflight = execution.get("preflight") or evaluation.get("preflight") or {}
        manual_required = bool(
            stage in {"state_mismatch", "execution"}
            or root_cause in {"VERIFIER_OVER_REJECTION", "RISK_GATE_OVER_REJECTION"}
            or reason == "UNKNOWN"
        )
        row = {
            "sample_id": sample_id,
            "database": sample.get("db_id"),
            "input_type": sample.get("input_mode"),
            "operation_type": sample.get("operation_semantics"),
            "difficulty": sample.get("difficulty", ""),
            "conflict_sensitive": int(bool(sample.get("conflict_sensitive"))),
            "dependency_sensitive": int(bool(sample.get("multi_table") or "relational" in str(sample.get("complexity") or ""))),
            "target_state_correct": int(mp_correct),
            "strict_state_correct": int(bool(evaluation.get("strict_full_state_correct"))),
            "execution_success": int(bool(evaluation.get("execution_success"))),
            "admitted": int(bool(evaluation.get("accepted_output"))),
            "abstained": int(not bool(evaluation.get("accepted_output"))),
            "off_target_change": int(bool(evaluation.get("side_effect") or evaluation.get("any_off_target_change"))),
            "constraint_failure": int(str(preflight.get("error_class") or "").endswith("_violation")),
            "execution_failure": int(evaluation.get("error_type") == "execution_error"),
            **statuses,
            "first_failure_stage": stage,
            "failure_reason_code": reason,
            "failure_reason_detail": "" if stage == "none" else reason_detail(verification, evaluation),
            "root_cause": root_cause,
            "manual_review_required": int(manual_required),
            "manual_review_label": "executed_but_wrong" if stage == "state_mismatch" else ("over_reject_candidate" if "OVER_REJECTION" in root_cause else ""),
            "manual_review_notes": "",
            "oracle_if_bypassed_correct": "" if oracle_correct is None else int(oracle_correct),
            "abstention_reason": "" if evaluation.get("accepted_output") else ABSTENTION_REASON_BY_STAGE.get(stage, "unsupported"),
            "direct_correct": int(direct_correct),
            "jfs_correct": int(jfs_correct),
            "mpfsplus_correct": int(mp_correct),
            "paired_category": pair_category(direct_correct, jfs_correct, mp_correct),
        }
        rows.append(row)
    return rows, artifacts


def counter_rows(counter: Counter[str], total: int, incorrect: int, header: str) -> list[dict[str, Any]]:
    return [
        {
            header: key,
            "N": value,
            "% of 300": format_percent(value, total),
            "% of incorrect": format_percent(value, incorrect),
        }
        for key, value in sorted(counter.items(), key=lambda item: STAGE_ORDER.index(item[0]) if item[0] in STAGE_ORDER else 99)
        if key != "none"
    ]


def build_stage_failure_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(rows)
    incorrect = sum(not row["target_state_correct"] for row in rows)
    counter = Counter(row["first_failure_stage"] for row in rows if row["first_failure_stage"] != "none")
    return counter_rows(counter, total, incorrect, "First failure stage")


def build_root_cause_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    incorrect_rows = [row for row in rows if not row["target_state_correct"]]
    counter = Counter(row["root_cause"] for row in incorrect_rows)
    return [
        {
            "Root cause": key,
            "N incorrect": value,
            "%": format_percent(value, len(incorrect_rows)),
        }
        for key, value in sorted(counter.items())
    ]


def build_performance_by_input_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for input_type in sorted({row["input_type"] for row in rows}):
        subset = [row for row in rows if row["input_type"] == input_type]
        admitted = sum(row["admitted"] for row in subset)
        correct = sum(row["target_state_correct"] for row in subset)
        output.append(
            {
                "Input type": input_type,
                "N": len(subset),
                "Correct": correct,
                "Accuracy": format_percent(correct, len(subset)),
                "Coverage": format_percent(admitted, len(subset)),
                "Accepted accuracy": format_percent(correct, admitted),
            }
        )
    return output


def build_performance_by_database(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for database in sorted({row["database"] for row in rows}):
        subset = [row for row in rows if row["database"] == database]
        counter = Counter(row["first_failure_stage"] for row in subset if row["first_failure_stage"] != "none")
        admitted = sum(row["admitted"] for row in subset)
        correct = sum(row["target_state_correct"] for row in subset)
        output.append(
            {
                "DB": database,
                "N": len(subset),
                "MP-FS+ accuracy": format_percent(correct, len(subset)),
                "Coverage": format_percent(admitted, len(subset)),
                "Main failure": counter.most_common(1)[0][0] if counter else "none",
            }
        )
    return output


def build_failure_by_database(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages = [stage for stage in STAGE_ORDER if stage != "none"]
    output = []
    for database in sorted({row["database"] for row in rows}):
        subset = [row for row in rows if row["database"] == database]
        counter = Counter(row["first_failure_stage"] for row in subset)
        record = {"DB": database, "N": len(subset)}
        for stage in stages:
            record[stage] = counter.get(stage, 0)
        output.append(record)
    return output


def build_paired(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        baseline_success = bool(row["direct_correct"] or row["jfs_correct"])
        mp_failed = not bool(row["mpfsplus_correct"])
        output.append(
            {
                "sample_id": row["sample_id"],
                "direct_correct": row["direct_correct"],
                "jfs_correct": row["jfs_correct"],
                "mpfsplus_correct": row["mpfsplus_correct"],
                "paired_category": row["paired_category"],
                "why_baseline_succeeded": (
                    "less_constrained_generation" if baseline_success and mp_failed else ""
                ),
                "why_mpfsplus_failed": (
                    why_mp_failed(row["first_failure_stage"], row["failure_reason_code"])
                    if baseline_success and mp_failed
                    else ""
                ),
            }
        )
    return output


def build_unique_successes(rows: list[dict[str, Any]], artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    samples = load_holdout_samples()
    evals = by_sample(artifacts["mpfsplus"]["evaluation"])
    output = []
    for row in rows:
        if not row["mpfsplus_correct"] or (row["direct_correct"] and row["jfs_correct"]):
            continue
        sample = samples[row["sample_id"]]
        output.append(
            {
                "sample_id": row["sample_id"],
                "what_mpfsplus_did_correctly": "passed structured checks and matched target state",
                "why_direct_failed": "wrong_state_or_execution_failure" if not row["direct_correct"] else "",
                "why_jfs_failed": "wrong_state_or_verification_failure" if not row["jfs_correct"] else "",
                "feature_responsible": unique_success_feature(sample, evals[row["sample_id"]]),
            }
        )
    return output


def build_oracle(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        stage = row["first_failure_stage"]
        if stage not in {"verification", "semantic_gate", "preflight", "reference_resolution", "materialization"}:
            continue
        output.append(
            {
                "sample_id": row["sample_id"],
                "rejected_at_stage": stage,
                "failure_reason_code": row["failure_reason_code"],
                "oracle_variant": bypass_variant(stage) or "",
                "oracle_if_bypassed_correct": row["oracle_if_bypassed_correct"],
                "false_rejection": int(str(row["oracle_if_bypassed_correct"]) == "1"),
                "abstention_reason": row["abstention_reason"],
            }
        )
    return output


def build_verifier_confusion() -> list[dict[str, Any]]:
    oracle = oracle_lookup()
    artifacts = load_run_artifacts()
    production_verdicts = by_sample(artifacts["mpfsplus"]["verification"])
    rows: list[dict[str, Any]] = []
    for sample_id, production in sorted(production_verdicts.items()):
        if production.get("status") == "not_available":
            continue
        verdict = "PASS" if production.get("status") == "valid" else "REJECT"
        if verdict == "REJECT":
            bypass = oracle.get(
                (
                    sample_id,
                    "V0_no_verifier_no_provenance_no_semantic_gate_no_preflight",
                ),
                {},
            )
            candidate_correct = bool(bypass.get("target_state_correct"))
        else:
            replay = oracle.get((sample_id, "V2_hard_verifier_plus_provenance"), {})
            candidate_correct = bool(replay.get("target_state_correct"))
        rows.append(
            {
                "gate": "hard_verifier",
                "sample_id": sample_id,
                "verdict": verdict,
                "candidate_correct": int(candidate_correct),
                "cell": (
                    "A_pass_correct"
                    if verdict == "PASS" and candidate_correct
                    else "B_pass_wrong"
                    if verdict == "PASS"
                    else "C_reject_correct"
                    if candidate_correct
                    else "D_reject_wrong"
                ),
            }
        )
    return rows


def build_semantic_gate_confusion() -> list[dict[str, Any]]:
    oracle = oracle_lookup()
    rows: list[dict[str, Any]] = []
    for (sample_id, variant), row in oracle.items():
        if variant != "V2_5_plus_semantic_risk_gate":
            continue
        if row.get("semantic_gate_accepted") is None:
            continue
        verdict = "PASS" if row.get("semantic_gate_accepted") else "REJECT"
        bypass = oracle.get((sample_id, "V2_hard_verifier_plus_provenance"), {})
        candidate_correct = bool(
            row.get("target_state_correct")
            if verdict == "PASS"
            else bypass.get("target_state_correct")
        )
        rows.append(
            {
                "gate": "semantic_risk_gate",
                "sample_id": sample_id,
                "verdict": verdict,
                "candidate_correct": int(candidate_correct),
                "cell": (
                    "A_pass_correct"
                    if verdict == "PASS" and candidate_correct
                    else "B_pass_wrong"
                    if verdict == "PASS"
                    else "C_reject_correct"
                    if candidate_correct
                    else "D_reject_wrong"
                ),
            }
        )
    return rows


def summarize_confusion(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(row["cell"] for row in rows)
    passed = counter["A_pass_correct"] + counter["B_pass_wrong"]
    rejected = counter["C_reject_correct"] + counter["D_reject_wrong"]
    return [
        {"metric": "A_pass_correct", "value": counter["A_pass_correct"]},
        {"metric": "B_false_accept_pass_wrong", "value": counter["B_pass_wrong"]},
        {"metric": "C_false_reject_reject_correct", "value": counter["C_reject_correct"]},
        {"metric": "D_reject_wrong", "value": counter["D_reject_wrong"]},
        {
            "metric": "precision",
            "value": format_percent(counter["A_pass_correct"], passed),
        },
        {
            "metric": "recall_of_bad_candidates",
            "value": format_percent(counter["D_reject_wrong"], counter["B_pass_wrong"] + counter["D_reject_wrong"]),
        },
        {
            "metric": "false_accept_rate",
            "value": format_percent(counter["B_pass_wrong"], passed),
        },
        {
            "metric": "false_reject_rate",
            "value": format_percent(counter["C_reject_correct"], rejected),
        },
    ]


def build_survival(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def passed(column: str) -> int:
        return sum(row[column] == "1" for row in rows)

    return {
        "total": len(rows),
        "generation_pass": passed("generation_ok"),
        "parse_pass": passed("parse_ok"),
        "reference_pass": passed("reference_resolution_ok"),
        "materialization_pass": passed("materialization_ok"),
        "verification_pass": passed("verification_ok"),
        "compilation_pass": passed("compilation_ok"),
        "semantic_gate_pass": passed("semantic_gate_ok"),
        "preflight_pass": passed("preflight_ok"),
        "executed": passed("execution_ok"),
        "target_correct": sum(row["target_state_correct"] for row in rows),
    }


def build_diagnostic_traces(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stage_key = {
        "generation": "generation_ok",
        "parsing": "parse_ok",
        "reference_resolution": "reference_resolution_ok",
        "materialization": "materialization_ok",
        "hard_verification": "verification_ok",
        "deterministic_compilation": "compilation_ok",
        "semantic_risk_gate": "semantic_gate_ok",
        "transactional_preflight": "preflight_ok",
        "admission": "admission_ok",
        "execution": "execution_ok",
        "state_comparison": "state_correct",
    }
    traces = []
    for row in rows:
        stages = {}
        for name, column in stage_key.items():
            value = row[column]
            status = "not_run" if value == "NA" else ("pass" if value == "1" else "fail")
            record = {"status": status}
            if status == "fail" and row["first_failure_stage"] in {name, name.replace("hard_", "").replace("deterministic_", "").replace("transactional_", "").replace("semantic_risk_gate", "semantic_gate")}:
                record["code"] = row["failure_reason_code"]
            stages[name] = record
        traces.append(
            {
                "sample_id": row["sample_id"],
                "stages": stages,
                "first_failure_stage": row["first_failure_stage"],
                "final": {
                    "admitted": bool(row["admitted"]),
                    "executed": row["execution_ok"] == "1",
                    "target_state_correct": bool(row["target_state_correct"]),
                },
            }
        )
    return traces


def build_manifest(artifacts: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    protocol = artifacts["mpfsplus"]["run_lock"]
    model_manifest = artifacts["mpfsplus"]["model_manifest"]
    return {
        "analysis_id": "mp_fs_plus_failure_analysis_v1",
        "created_for_phase": "stage_1_error_failure_analysis",
        "predictions_modified": False,
        "model_inference_rerun": False,
        "gpu_required": False,
        "commit_hash": git_commit_hash(),
        "result_archive": str(RESULT_ARCHIVE.relative_to(WORKSPACE)),
        "result_archive_sha256": sha256_file(RESULT_ARCHIVE),
        "dataset_archive": str(HOLDOUT_ZIP.relative_to(WORKSPACE)),
        "dataset_archive_sha256": sha256_file(HOLDOUT_ZIP),
        "model": artifacts["mpfsplus"]["config"].get("method_id", "MP-FS+"),
        "model_path": model_manifest.get("model_path"),
        "model_revision": str(model_manifest.get("model_path", "")).rstrip("/").split("/")[-1],
        "model_aggregate_sha256": model_manifest.get("aggregate_sha256"),
        "dataset_manifest_hashes": (artifacts["protocol"].get("authorized_hashes") or {}),
        "test_ids": [row["sample_id"] for row in rows],
        "prompt_version": artifacts["mpfsplus"]["config"].get("prompt_version") or artifacts["mpfsplus"]["config"].get("family"),
        "seed": protocol.get("seed") or artifacts["mpfsplus"]["manifest"].get("seed"),
        "generation_config": {
            key: artifacts["mpfsplus"]["config"].get(key)
            for key in ["max_input_tokens", "max_new_tokens", "temperature", "top_p"]
            if key in artifacts["mpfsplus"]["config"]
        },
        "evaluation_config": {
            "state_scope": "all_user_tables",
            "protocol_id": artifacts["protocol"].get("protocol_id"),
            "reporting_policy": "retain output-limit adjudicated samples in denominator",
        },
        "sample_count": len(rows),
        "mpfsplus_correct": sum(row["target_state_correct"] for row in rows),
        "mpfsplus_incorrect": sum(not row["target_state_correct"] for row in rows),
    }


def git_commit_hash() -> str | None:
    head = WORKSPACE / ".git" / "HEAD"
    if not head.exists():
        return None
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref: "):
        ref = WORKSPACE / ".git" / value.split(" ", 1)[1]
        return ref.read_text(encoding="utf-8").strip() if ref.exists() else None
    return value


def build_candidate_fixes(rows: list[dict[str, Any]]) -> str:
    groups = Counter(row["failure_reason_code"] for row in rows if not row["target_state_correct"])
    templates = [
        (
            "MPF-ERR-001",
            "reference_resolution",
            "REF_UNKNOWN_COLUMN",
            "LLM emits non-existent enumerated target-column IDs.",
            "Strengthen schema-ID constraints and add a repair pass for nearest valid column IDs.",
        ),
        (
            "MPF-ERR-002",
            "materialization",
            "VALUE_MISSING",
            "Source fields remain unmapped or invalid source-field references are produced.",
            "Separate control/instruction fields from payload fields before materialization.",
        ),
        (
            "MPF-ERR-003",
            "verification",
            "TARGET_MISSING_COLUMN",
            "Plans omit required target columns after grounding.",
            "Add planner repair for required-column coverage using available evidence.",
        ),
        (
            "MPF-ERR-004",
            "execution/state_comparison",
            "STATE_WRONG_VALUE",
            "Candidates pass checks but produce target-state mismatch.",
            "Introduce post-execution semantic repair candidates in Stage 2.",
        ),
        (
            "MPF-ERR-005",
            "semantic_gate/preflight",
            "PREFLIGHT_CONSTRAINT",
            "Candidate is rejected after compilation by semantic or SQLite safety gate.",
            "Use oracle-bypass evidence to decide relax-vs-repair policy.",
        ),
    ]
    parts = ["# Candidate Fixes\n"]
    for issue, stage, code, observed, fix in templates:
        affected = groups.get(code, 0)
        parts.append(
            f"## Issue ID: {issue}\n\n"
            f"Affected stage: {stage}\n\n"
            f"Affected samples: {affected}\n\n"
            f"Observed behavior: {observed}\n\n"
            f"Likely cause: frozen v2.1 output shows this as a recurring first-order failure class.\n\n"
            f"Possible fix: {fix}\n\n"
            f"Expected benefit: bounded by {affected} currently affected incorrect samples before interaction with other fixes.\n\n"
            "Risk: changes planner/repair behavior and therefore requires a fresh Stage 2 evaluation.\n"
        )
    return "\n".join(parts)


def build_report(
    rows: list[dict[str, Any]],
    stage_summary: list[dict[str, Any]],
    root_summary: list[dict[str, Any]],
    perf_input: list[dict[str, Any]],
    perf_db: list[dict[str, Any]],
    oracle_rows: list[dict[str, Any]],
    paired_rows: list[dict[str, Any]],
    unique_rows: list[dict[str, Any]],
    verifier_confusion_summary: list[dict[str, Any]],
    semantic_confusion_summary: list[dict[str, Any]],
) -> str:
    total = len(rows)
    correct = sum(row["target_state_correct"] for row in rows)
    admitted = sum(row["admitted"] for row in rows)
    incorrect = total - correct
    executed_wrong = [row for row in rows if row["first_failure_stage"] == "state_mismatch"]
    verifier_boundary_rejects = [
        row
        for row in rows
        if row["first_failure_stage"] in {"reference_resolution", "materialization", "verification"}
    ]
    verifier_false = sum(str(row["oracle_if_bypassed_correct"]) == "1" for row in verifier_boundary_rejects)
    abstained = [row for row in rows if row["abstained"]]
    abstained_oracle = sum(str(row["oracle_if_bypassed_correct"]) == "1" for row in abstained)
    paired_counter = Counter(row["paired_category"] for row in paired_rows)
    lines = [
        "# MP-FS+ Failure Analysis",
        "",
        "## 1. Analysis protocol",
        "Frozen prediction artifacts were read without rerunning model inference. Downstream oracle-bypass uses the existing isolated replay ablation outputs; no production database is modified.",
        "",
        "## 2. Dataset/run identity",
        f"Samples: {total}. Result archive: `{RESULT_ARCHIVE.name}`. Dataset archive: `{HOLDOUT_ZIP.name}`.",
        "",
        "## 3. Overall result",
        f"MP-FS+ target-state accuracy: {correct}/{total} = {format_percent(correct, total)}%. Coverage/admission: {admitted}/{total} = {format_percent(admitted, total)}%. Incorrect: {incorrect}.",
        "",
        "## 4. Stage-wise survival",
        json.dumps(build_survival(rows), ensure_ascii=False, indent=2),
        "",
        "## 5. First-failure distribution",
        markdown_table(stage_summary),
        "",
        "## 6. Root-cause distribution",
        markdown_table(root_summary),
        "",
        "## 7. Free-text vs semi-structured",
        markdown_table(perf_input),
        "",
        "## 8. Database-level analysis",
        markdown_table(perf_db),
        "",
        "## 9. Verification false rejection analysis",
        f"Verifier-boundary rejects: {len(verifier_boundary_rejects)}. Oracle-correct if bypassed: {verifier_false}. False rejection rate: {format_percent(verifier_false, len(verifier_boundary_rejects))}%.",
        "",
        markdown_table(verifier_confusion_summary),
        "",
        "## 10. Semantic gate analysis",
        f"Semantic-gate first failures: {sum(row['first_failure_stage'] == 'semantic_gate' for row in rows)}.",
        "",
        markdown_table(semantic_confusion_summary),
        "",
        "## 11. Executed-but-wrong cases",
        f"Executed successfully but target state wrong: {len(executed_wrong)}. These are all marked `manual_review_required=1`.",
        "",
        "## 12. MP-FS+ vs Direct/J paired analysis",
        markdown_table([{"paired_category": k, "N": v} for k, v in sorted(paired_counter.items())]),
        "",
        "## 13. Unique MP-FS+ successes",
        f"Unique or partial unique MP-FS+ successes: {len(unique_rows)}.",
        "",
        "## 14. Candidate issues for method revision",
        "See `candidate_fixes.md` for issue-level notes. Acceptance questions: "
        f"planning/parse={sum(row['first_failure_stage'] in {'generation', 'parse'} for row in rows)}, "
        f"grounding={sum(row['first_failure_stage'] == 'reference_resolution' for row in rows)}, "
        f"materialization={sum(row['first_failure_stage'] == 'materialization' for row in rows)}, "
        f"verifier_boundary={len(verifier_boundary_rejects)}, verifier_oracle_correct={verifier_false}, "
        f"semantic_or_preflight={sum(row['first_failure_stage'] in {'semantic_gate', 'preflight'} for row in rows)}, "
        f"executed_wrong={len(executed_wrong)}, "
        f"abstained={len(abstained)}, abstained_oracle_correct={abstained_oracle}.",
    ]
    return "\n".join(lines) + "\n"


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No rows._"
    fields = list(rows[0])
    out = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return "\n".join(out)


def build_all(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, artifacts = build_master()
    stage_summary = build_stage_failure_summary(rows)
    root_summary = build_root_cause_summary(rows)
    perf_input = build_performance_by_input_type(rows)
    perf_db = build_performance_by_database(rows)
    failure_db = build_failure_by_database(rows)
    paired = build_paired(rows)
    unique = build_unique_successes(rows, artifacts)
    oracle = build_oracle(rows)
    verifier_confusion = build_verifier_confusion()
    semantic_confusion = build_semantic_gate_confusion()
    verifier_confusion_summary = summarize_confusion(verifier_confusion)
    semantic_confusion_summary = summarize_confusion(semantic_confusion)
    traces = build_diagnostic_traces(rows)
    survival = build_survival(rows)
    manifest = build_manifest(artifacts, rows)

    master_fields = [
        "sample_id",
        "database",
        "input_type",
        "operation_type",
        "difficulty",
        "conflict_sensitive",
        "dependency_sensitive",
        "target_state_correct",
        "strict_state_correct",
        "execution_success",
        "admitted",
        "abstained",
        "off_target_change",
        "constraint_failure",
        "execution_failure",
        *STAGE_COLUMNS,
        "first_failure_stage",
        "failure_reason_code",
        "failure_reason_detail",
        "root_cause",
        "manual_review_required",
        "manual_review_label",
        "manual_review_notes",
        "oracle_if_bypassed_correct",
        "abstention_reason",
        "direct_correct",
        "jfs_correct",
        "mpfsplus_correct",
        "paired_category",
    ]
    write_json(output_dir / "analysis_run_manifest.json", manifest)
    write_csv(output_dir / "mp_fs_plus_sample_level_analysis.csv", rows, master_fields)
    write_jsonl(output_dir / "diagnostic_traces.jsonl", traces)
    write_csv(output_dir / "stage_failure_summary.csv", stage_summary)
    write_csv(output_dir / "root_cause_summary.csv", root_summary)
    write_csv(output_dir / "performance_by_input_type.csv", perf_input)
    write_csv(output_dir / "performance_by_database.csv", perf_db)
    write_csv(output_dir / "failure_by_database.csv", failure_db)
    write_csv(output_dir / "paired_method_analysis.csv", paired)
    write_csv(output_dir / "mpfsplus_unique_successes.csv", unique)
    write_csv(output_dir / "oracle_rejection_analysis.csv", oracle)
    write_csv(output_dir / "verifier_confusion.csv", verifier_confusion)
    write_csv(output_dir / "verifier_confusion_summary.csv", verifier_confusion_summary)
    write_csv(output_dir / "semantic_gate_confusion.csv", semantic_confusion)
    write_csv(output_dir / "semantic_gate_confusion_summary.csv", semantic_confusion_summary)
    write_json(output_dir / "pipeline_survival.json", survival)
    (output_dir / "candidate_fixes.md").write_text(
        build_candidate_fixes(rows),
        encoding="utf-8",
        newline="\n",
    )
    (output_dir / "MP_FS_PLUS_FAILURE_ANALYSIS.md").write_text(
        build_report(
            rows,
            stage_summary,
            root_summary,
            perf_input,
            perf_db,
            oracle,
            paired,
            unique,
            verifier_confusion_summary,
            semantic_confusion_summary,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "output_dir": str(output_dir),
        "sample_count": len(rows),
        "correct": sum(row["target_state_correct"] for row in rows),
        "incorrect": sum(not row["target_state_correct"] for row in rows),
        "stage_summary": stage_summary,
        "root_summary": root_summary,
    }


if __name__ == "__main__":
    print(json.dumps(build_all(), ensure_ascii=False, indent=2))
