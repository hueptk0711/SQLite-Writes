from __future__ import annotations

import csv
import hashlib
import json
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from .state_diff_audit import load_database_schema_ddl, replay_state_diff
except ImportError:  # Direct script execution.
    from state_diff_audit import load_database_schema_ddl, replay_state_diff


def discover_workspace(start: Path | None = None) -> Path:
    """Find the repository root without relying on a fragile fixed parent index."""
    anchor = (start or Path(__file__).resolve()).resolve()
    for candidate in [anchor.parent, *anchor.parents]:
        if (candidate / "04_results").is_dir() and (candidate / "03_protocol_and_data").is_dir():
            return candidate
    raise RuntimeError(
        "Cannot locate SQLite-Writes repository root. Run this analysis from inside a full "
        "checkout containing 03_protocol_and_data/ and 04_results/."
    )


WORKSPACE = discover_workspace()
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
MANUAL_AUDIT_DECISIONS = OUTPUT_ROOT / "stage1_manual_audit_decisions.csv"

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


def reason_code_for(
    stage: str,
    verification: dict[str, Any],
    evaluation: dict[str, Any],
    state_diff_primary: str | None = None,
) -> str:
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
        return state_diff_primary or "STATE_DIFF_AUDIT_REQUIRED"
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
    # V0 removes verifier, provenance, semantic gate, and preflight together.
    # Therefore a V0 recovery is evidence of system-level bypass recoverability,
    # not proof that the hard verifier caused the rejection.
    if stage == "reference_resolution" and oracle_correct:
        return "BYPASS_RECOVERABLE_REFERENCE_RESOLUTION"
    if stage == "materialization" and oracle_correct:
        return "BYPASS_RECOVERABLE_MATERIALIZATION"
    if stage == "verification" and oracle_correct:
        return "BYPASS_RECOVERABLE_VERIFICATION"
    if stage == "semantic_gate" and oracle_correct:
        return "BYPASS_RECOVERABLE_SEMANTIC_GATE"
    if stage == "preflight":
        return "PREFLIGHT_ERROR"
    if stage == "compilation":
        return "COMPILER_ERROR"
    if stage == "state_mismatch":
        return "FINAL_STATE_MISMATCH"
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


def format_percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return ""
    return f"{100.0 * numerator / denominator:.2f}"


def load_manual_audit_decisions() -> dict[str, dict[str, str]]:
    if not MANUAL_AUDIT_DECISIONS.exists():
        return {}
    allowed_status = {"PENDING", "COMPLETED"}
    allowed_conflict_labels = {
        "",
        "TRULY_AMBIGUOUS",
        "RESOLVABLE_FROM_INPUT",
        "RESOLVABLE_FROM_SCHEMA",
        "UNKNOWN",
    }
    output: dict[str, dict[str, str]] = {}
    with MANUAL_AUDIT_DECISIONS.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            sample_id = str(raw.get("sample_id") or "").strip()
            if not sample_id:
                raise ValueError("manual audit row has an empty sample_id")
            if sample_id in output:
                raise ValueError(f"duplicate manual audit decision for sample {sample_id}")
            row = {key: str(value or "").strip() for key, value in raw.items()}
            status = row.get("manual_review_status", "PENDING").upper() or "PENDING"
            if status not in allowed_status:
                raise ValueError(f"invalid manual_review_status={status!r} for {sample_id}")
            row["manual_review_status"] = status
            conflict_label = row.get("conflict_ambiguity_gold_label", "").upper()
            if conflict_label not in allowed_conflict_labels:
                raise ValueError(
                    f"invalid conflict_ambiguity_gold_label={conflict_label!r} for {sample_id}"
                )
            row["conflict_ambiguity_gold_label"] = conflict_label
            if status == "COMPLETED":
                if not row.get("manual_review_notes"):
                    raise ValueError(
                        f"completed manual audit for {sample_id} requires non-empty manual_review_notes"
                    )
                if not row.get("reviewer_root_cause"):
                    raise ValueError(
                        f"completed manual audit for {sample_id} requires non-empty reviewer_root_cause"
                    )
            output[sample_id] = row
    return output


def systematic_audit_tags(stage: str, reason_code: str, detail: str) -> list[str]:
    lowered = detail.lower()
    tags: list[str] = []
    if "source field 'operation' is neither mapped nor justified" in lowered:
        tags.append("CONTROL_FIELD_OPERATION")
    if reason_code == "VALUE_NORMALIZATION_ERROR" and "date" in lowered:
        tags.append("DATE_NORMALIZATION")
    if reason_code == "CONFLICT_MISSING":
        tags.append("CONFLICT_AMBIGUITY")
    if stage == "state_mismatch":
        tags.append("STATE_MISMATCH")
    return tags


def manual_review_categories(
    *,
    stage: str,
    root_cause: str,
    reason_code: str,
    systematic_tags: list[str],
) -> list[str]:
    categories: list[str] = []
    if stage == "state_mismatch":
        categories.append("EXECUTED_BUT_WRONG")
    if stage == "execution":
        categories.append("EXECUTION_FAILURE")
    if root_cause.startswith("BYPASS_RECOVERABLE_"):
        categories.append("BYPASS_RECOVERABLE")
    if reason_code == "UNKNOWN":
        categories.append("UNKNOWN_REASON")
    categories.extend(systematic_tags)
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(categories))


def build_master() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    samples = load_holdout_samples()
    artifacts = load_run_artifacts()
    oracle = oracle_lookup()
    manual_decisions = load_manual_audit_decisions()
    mp = artifacts["mpfsplus"]
    indexes = {
        key: by_sample(mp[key])
        for key in ["evaluation", "raw", "parsed", "verification", "compiled", "execution"]
    }
    direct_eval = by_sample(artifacts["direct"]["evaluation"])
    jfs_eval = by_sample(artifacts["jfs"]["evaluation"])
    rows: list[dict[str, Any]] = []
    state_diff_records: dict[str, dict[str, Any]] = {}

    for sample_id in sorted(samples):
        sample = samples[sample_id]
        evaluation = indexes["evaluation"][sample_id]
        raw = indexes["raw"].get(sample_id, {})
        parsed = indexes["parsed"].get(sample_id, {})
        verification = indexes["verification"].get(sample_id, {})
        compiled = indexes["compiled"].get(sample_id, {})
        execution = indexes["execution"].get(sample_id, {})
        stage = classify_first_failure(evaluation, raw, parsed, verification, compiled, execution)

        state_diff: dict[str, Any] = {}
        state_diff_error = ""
        if stage == "state_mismatch":
            try:
                state_diff = replay_state_diff(sample, compiled, HOLDOUT_ZIP)
            except Exception as exc:  # Keep the audit package buildable and expose the failure explicitly.
                state_diff_error = f"{type(exc).__name__}: {exc}"
                state_diff = {
                    "sample_id": sample_id,
                    "database": sample.get("db_id"),
                    "state_diff_classes": ["STATE_DIFF_AUDIT_ERROR"],
                    "primary_class": "STATE_DIFF_AUDIT_ERROR",
                    "gold_delta": {},
                    "predicted_delta": {},
                    "difference": {},
                    "error": state_diff_error,
                }
            state_diff_records[sample_id] = state_diff

        state_diff_primary = str(state_diff.get("primary_class") or "") or None
        reason = (
            reason_code_for(stage, verification, evaluation, state_diff_primary)
            if stage != "none"
            else "NONE"
        )
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
        detail = "" if stage == "none" else reason_detail(verification, evaluation)
        systematic_tags = systematic_audit_tags(stage, reason, detail)
        review_categories = manual_review_categories(
            stage=stage,
            root_cause=root_cause,
            reason_code=reason,
            systematic_tags=systematic_tags,
        )
        manual_required = bool(review_categories)
        decision = manual_decisions.get(sample_id, {})
        manual_status = str(decision.get("manual_review_status") or "").strip()
        if not manual_status:
            manual_status = "PENDING" if manual_required else "NOT_REQUIRED"
        manual_notes = str(decision.get("manual_review_notes") or "").strip()
        if manual_required and not manual_notes:
            manual_notes = "PENDING_MANUAL_AUDIT: " + ";".join(review_categories)
        conflict_label = str(decision.get("conflict_ambiguity_gold_label") or "").strip()
        if reason == "CONFLICT_MISSING" and not conflict_label:
            conflict_label = "UNKNOWN"

        row = {
            "sample_id": sample_id,
            "database": sample.get("db_id"),
            "input_type": sample.get("input_mode"),
            "operation_type": sample.get("operation_semantics"),
            "difficulty": sample.get("difficulty", ""),
            "conflict_sensitive": int(bool(sample.get("conflict_sensitive"))),
            "dependency_sensitive": int(
                bool(sample.get("multi_table") or "relational" in str(sample.get("complexity") or "").lower())
            ),
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
            "failure_reason_detail": detail,
            "root_cause": root_cause,
            "systematic_audit_tags": ";".join(systematic_tags),
            "state_diff_classes": ";".join(state_diff.get("state_diff_classes") or []),
            "state_diff_error": state_diff_error,
            "state_diff_gold_delta": json.dumps(state_diff.get("gold_delta") or {}, ensure_ascii=False, sort_keys=True),
            "state_diff_predicted_delta": json.dumps(state_diff.get("predicted_delta") or {}, ensure_ascii=False, sort_keys=True),
            "state_diff_difference": json.dumps(state_diff.get("difference") or {}, ensure_ascii=False, sort_keys=True),
            "manual_review_required": int(manual_required),
            "manual_review_label": ";".join(review_categories),
            "manual_review_status": manual_status,
            "reviewer_root_cause": str(decision.get("reviewer_root_cause") or "").strip(),
            "conflict_ambiguity_gold_label": conflict_label,
            "manual_review_notes": manual_notes,
            "oracle_if_bypassed_correct": "" if oracle_correct is None else int(oracle_correct),
            "abstention_reason": "" if evaluation.get("accepted_output") else ABSTENTION_REASON_BY_STAGE.get(stage, "unsupported"),
            "direct_correct": int(direct_correct),
            "jfs_correct": int(jfs_correct),
            "mpfsplus_correct": int(mp_correct),
            "paired_category": pair_category(direct_correct, jfs_correct, mp_correct),
        }
        rows.append(row)

    artifacts["state_diff_records"] = state_diff_records
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


def _format_root_cause_summary(counter: Counter[str], incorrect_count: int) -> list[dict[str, Any]]:
    return [
        {
            "Root cause": key,
            "N incorrect": value,
            "%": format_percent(value, incorrect_count),
        }
        for key, value in sorted(counter.items())
    ]


def build_root_cause_summary_auto(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize the automatic, pre-manual root-cause diagnosis."""
    incorrect_rows = [row for row in rows if not row["target_state_correct"]]
    counter = Counter(row["root_cause"] for row in incorrect_rows)
    return _format_root_cause_summary(counter, len(incorrect_rows))


def reviewed_root_cause(row: dict[str, Any]) -> str:
    """Use the completed manual decision when available; otherwise retain automatic diagnosis."""
    manual_status = str(row.get("manual_review_status") or "").strip().upper()
    reviewer_root = str(row.get("reviewer_root_cause") or "").strip()
    if manual_status == "COMPLETED" and reviewer_root:
        return reviewer_root
    return str(row.get("root_cause") or "UNKNOWN")


def build_reviewed_root_cause_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Final Stage-1.1 root-cause summary with manual decisions overriding automatic labels."""
    incorrect_rows = [row for row in rows if not row["target_state_correct"]]
    counter = Counter(reviewed_root_cause(row) for row in incorrect_rows)
    return _format_root_cause_summary(counter, len(incorrect_rows))


def build_root_cause_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-compatible alias for the final reviewed root-cause summary."""
    return build_reviewed_root_cause_summary(rows)


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


def build_performance_by_dependency_sensitivity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for value in [0, 1]:
        subset = [row for row in rows if int(row["dependency_sensitive"]) == value]
        admitted = sum(row["admitted"] for row in subset)
        correct = sum(row["target_state_correct"] for row in subset)
        output.append(
            {
                "Dependency-sensitive": "yes" if value else "no",
                "N": len(subset),
                "Correct": correct,
                "Accuracy": format_percent(correct, len(subset)),
                "Coverage": format_percent(admitted, len(subset)),
                "Accepted accuracy": format_percent(correct, admitted),
            }
        )
    return output


def build_performance_by_operation_type(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for operation_type in sorted({str(row["operation_type"]) for row in rows}):
        subset = [row for row in rows if str(row["operation_type"]) == operation_type]
        admitted = sum(row["admitted"] for row in subset)
        correct = sum(row["target_state_correct"] for row in subset)
        output.append(
            {
                "Operation": operation_type,
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
                "observed_mpfsplus_failure_stage": row["first_failure_stage"] if baseline_success and mp_failed else "",
                "observed_mpfsplus_failure_reason": row["failure_reason_code"] if baseline_success and mp_failed else "",
                # Deliberately blank: an automatic causal explanation is not evidence.
                "hypothesized_baseline_advantage": "",
                "baseline_advantage_requires_manual_audit": int(baseline_success and mp_failed),
            }
        )
    return output


def build_unique_successes(rows: list[dict[str, Any]], artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if not row["mpfsplus_correct"] or (row["direct_correct"] and row["jfs_correct"]):
            continue
        output.append(
            {
                "sample_id": row["sample_id"],
                "direct_correct": row["direct_correct"],
                "jfs_correct": row["jfs_correct"],
                "mpfsplus_correct": row["mpfsplus_correct"],
                "conflict_sensitive": row["conflict_sensitive"],
                "dependency_sensitive": row["dependency_sensitive"],
                "input_type": row["input_type"],
                "operation_type": row["operation_type"],
                "observed_result": "MP-FS+ matched target state while at least one baseline did not",
                # No feature_responsible field: causality requires manual/component-level evidence.
                "feature_attribution_status": "NOT_CAUSALLY_ESTABLISHED",
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
                "first_failure_stage": stage,
                "failure_reason_code": row["failure_reason_code"],
                "oracle_variant": bypass_variant(stage) or "",
                "oracle_if_bypassed_correct": row["oracle_if_bypassed_correct"],
                "system_bypass_recoverable": int(str(row["oracle_if_bypassed_correct"]) == "1"),
                "causal_interpretation": (
                    "SYSTEM_LEVEL_BYPASS_ONLY"
                    if stage in {"reference_resolution", "materialization", "verification"}
                    else "STAGE_PAIRED_ABLATION"
                ),
                "abstention_reason": row["abstention_reason"],
            }
        )
    return output


def build_downstream_bypass_analysis(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relevant = [
        row
        for row in rows
        if row["first_failure_stage"] in {"reference_resolution", "materialization", "verification"}
    ]
    detail_rows = [
        {
            "sample_id": row["sample_id"],
            "first_failure_stage": row["first_failure_stage"],
            "failure_reason_code": row["failure_reason_code"],
            "oracle_variant": bypass_variant(row["first_failure_stage"]) or "",
            "downstream_bypass_correct": row["oracle_if_bypassed_correct"],
            "interpretation": "system-level bypass; not a single-component verifier intervention",
        }
        for row in relevant
    ]
    summary_rows: list[dict[str, Any]] = []
    for stage in ["reference_resolution", "materialization", "verification"]:
        subset = [row for row in relevant if row["first_failure_stage"] == stage]
        recoverable = sum(str(row["oracle_if_bypassed_correct"]) == "1" for row in subset)
        summary_rows.append(
            {
                "First failure stage": stage,
                "Rejects": len(subset),
                "Bypass-correct": recoverable,
                "Bypass-recoverable rate": format_percent(recoverable, len(subset)),
                "Causal scope": "system-level V0 bypass",
            }
        )
    return detail_rows, summary_rows


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


def build_manual_audit_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if not row["manual_review_required"]:
            continue
        output.append(
            {
                "sample_id": row["sample_id"],
                "database": row["database"],
                "input_type": row["input_type"],
                "operation_type": row["operation_type"],
                "review_categories": row["manual_review_label"],
                "first_failure_stage": row["first_failure_stage"],
                "failure_reason_code": row["failure_reason_code"],
                "failure_reason_detail": row["failure_reason_detail"],
                "root_cause_auto_noncausal": row["root_cause"],
                "oracle_if_bypassed_correct": row["oracle_if_bypassed_correct"],
                "state_diff_classes": row["state_diff_classes"],
                "state_diff_gold_delta": row["state_diff_gold_delta"],
                "state_diff_predicted_delta": row["state_diff_predicted_delta"],
                "state_diff_difference": row["state_diff_difference"],
                "manual_review_status": row["manual_review_status"],
                "reviewer_root_cause": row["reviewer_root_cause"],
                "conflict_ambiguity_gold_label": row["conflict_ambiguity_gold_label"],
                "manual_review_notes": row["manual_review_notes"],
            }
        )
    return output


def build_manual_audit_evidence(
    rows: list[dict[str, Any]],
    artifacts: dict[str, Any],
) -> list[dict[str, Any]]:
    samples = load_holdout_samples()
    mp = artifacts["mpfsplus"]
    indexes = {
        key: by_sample(mp[key])
        for key in [
            "evaluation",
            "raw",
            "parsed",
            "materialized",
            "verification",
            "compiled",
            "execution",
        ]
    }
    schema_cache: dict[str, list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for row in rows:
        if not row["manual_review_required"]:
            continue
        sample_id = str(row["sample_id"])
        sample = samples[sample_id]
        db_id = str(row["database"])
        if db_id not in schema_cache:
            schema_cache[db_id] = load_database_schema_ddl(HOLDOUT_ZIP, db_id)
        output.append(
            {
                "sample_id": sample_id,
                "review_categories": str(row["manual_review_label"]).split(";"),
                "first_failure_stage": row["first_failure_stage"],
                "failure_reason_code": row["failure_reason_code"],
                "failure_reason_detail": row["failure_reason_detail"],
                "oracle_if_bypassed_correct": row["oracle_if_bypassed_correct"],
                "sample": sample,
                "database_schema_ddl": schema_cache[db_id],
                "raw_generation": indexes["raw"].get(sample_id, {}),
                "parsed_plan": indexes["parsed"].get(sample_id, {}),
                "materialized_plan": indexes["materialized"].get(sample_id, {}),
                "verification": indexes["verification"].get(sample_id, {}),
                "compiled_program": indexes["compiled"].get(sample_id, {}),
                "execution": indexes["execution"].get(sample_id, {}),
                "evaluation": indexes["evaluation"].get(sample_id, {}),
                "state_diff": (artifacts.get("state_diff_records") or {}).get(sample_id, {}),
                "manual_decision": {
                    "status": row["manual_review_status"],
                    "reviewer_root_cause": row["reviewer_root_cause"],
                    "conflict_ambiguity_gold_label": row["conflict_ambiguity_gold_label"],
                    "notes": row["manual_review_notes"],
                },
            }
        )
    return output


def build_manual_audit_template(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if not row["manual_review_required"]:
            continue
        output.append(
            {
                "sample_id": row["sample_id"],
                "manual_review_status": "PENDING",
                "reviewer_root_cause": "",
                "conflict_ambiguity_gold_label": (
                    "UNKNOWN" if row["failure_reason_code"] == "CONFLICT_MISSING" else ""
                ),
                "manual_review_notes": "",
            }
        )
    return output


def build_state_mismatch_audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "sample_id": row["sample_id"],
            "database": row["database"],
            "state_diff_classes": row["state_diff_classes"],
            "state_diff_error": row["state_diff_error"],
            "gold_delta": row["state_diff_gold_delta"],
            "predicted_delta": row["state_diff_predicted_delta"],
            "difference": row["state_diff_difference"],
            "manual_review_status": row["manual_review_status"],
            "manual_review_notes": row["manual_review_notes"],
        }
        for row in rows
        if row["first_failure_stage"] == "state_mismatch"
    ]


def build_systematic_audit_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tags = ["CONTROL_FIELD_OPERATION", "DATE_NORMALIZATION", "CONFLICT_AMBIGUITY", "STATE_MISMATCH"]
    return [
        {
            "Audit group": tag,
            "N": sum(tag in str(row.get("systematic_audit_tags") or "").split(";") for row in rows),
            "Completed": sum(
                tag in str(row.get("systematic_audit_tags") or "").split(";")
                and row.get("manual_review_status") == "COMPLETED"
                for row in rows
            ),
        }
        for tag in tags
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


TRACE_TO_FAILURE_STAGE = {
    "generation": "generation",
    "parsing": "parse",
    "reference_resolution": "reference_resolution",
    "materialization": "materialization",
    "hard_verification": "verification",
    "deterministic_compilation": "compilation",
    "semantic_risk_gate": "semantic_gate",
    "transactional_preflight": "preflight",
    "execution": "execution",
    "state_comparison": "state_mismatch",
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
            expected_failure_stage = TRACE_TO_FAILURE_STAGE.get(name)
            if (
                status == "fail"
                and expected_failure_stage is not None
                and row["first_failure_stage"] == expected_failure_stage
            ):
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


def _find_nested_key(value: Any, wanted_keys: set[str], path: str = "") -> tuple[Any, str] | None:
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if str(key).lower() in wanted_keys and item not in (None, "", {}, []):
                return item, current
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            found = _find_nested_key(item, wanted_keys, current)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_nested_key(item, wanted_keys, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _recorded_field(
    named_sources: list[tuple[str, Any]],
    keys: Iterable[str],
) -> dict[str, Any]:
    wanted = {str(key).lower() for key in keys}
    for source_name, source in named_sources:
        found = _find_nested_key(source, wanted)
        if found is not None:
            value, path = found
            return {
                "value": value,
                "status": "recorded",
                "source": f"{source_name}:{path}",
            }
    return {
        "value": None,
        "status": "not_recorded_in_frozen_artifact",
        "source": None,
    }


def build_manifest(artifacts: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    mp = artifacts["mpfsplus"]
    model_manifest = mp["model_manifest"]
    sources = [
        ("config.json", mp.get("config") or {}),
        ("manifest.json", mp.get("manifest") or {}),
        ("run_lock.json", mp.get("run_lock") or {}),
        ("model_manifest.json", model_manifest or {}),
        ("final_protocol.json", artifacts.get("protocol") or {}),
    ]
    prompt_version = _recorded_field(
        sources,
        ["prompt_version", "prompt_template_version", "prompt_id", "prompt_revision"],
    )
    seed = _recorded_field(sources, ["seed", "random_seed", "generation_seed"])
    generation_config = {
        "max_input_tokens": _recorded_field(sources, ["max_input_tokens", "max_prompt_tokens"]),
        "max_new_tokens": _recorded_field(sources, ["max_new_tokens", "max_output_tokens"]),
        "temperature": _recorded_field(sources, ["temperature"]),
        "top_p": _recorded_field(sources, ["top_p"]),
        "do_sample": _recorded_field(sources, ["do_sample"]),
    }
    return {
        "analysis_id": "mp_fs_plus_failure_analysis_v1_1",
        "created_for_phase": "stage_1_1_manual_causal_correction",
        "predictions_modified": False,
        "model_inference_rerun": False,
        "gpu_required": False,
        "commit_hash": git_commit_hash(),
        "result_archive": str(RESULT_ARCHIVE.relative_to(WORKSPACE)),
        "result_archive_sha256": sha256_file(RESULT_ARCHIVE),
        "dataset_archive": str(HOLDOUT_ZIP.relative_to(WORKSPACE)),
        "dataset_archive_sha256": sha256_file(HOLDOUT_ZIP),
        "model": mp["config"].get("method_id", "MP-FS+"),
        "model_path": model_manifest.get("model_path"),
        "model_revision": str(model_manifest.get("model_path", "")).rstrip("/").split("/")[-1],
        "model_aggregate_sha256": model_manifest.get("aggregate_sha256"),
        "dataset_manifest_hashes": (artifacts["protocol"].get("authorized_hashes") or {}),
        "test_ids": [row["sample_id"] for row in rows],
        "prompt_version": prompt_version,
        "seed": seed,
        "generation_config": generation_config,
        "evaluation_config": {
            "state_scope": "all_user_tables",
            "protocol_id": artifacts["protocol"].get("protocol_id"),
            "reporting_policy": "retain output-limit adjudicated samples in denominator",
            "state_mismatch_audit": "gold/prediction replay on isolated SQLite copies",
            "v0_causal_scope": "system-level bypass; not a single-component verifier intervention",
        },
        "manual_audit_decisions_file": str(MANUAL_AUDIT_DECISIONS.relative_to(WORKSPACE)),
        "sample_count": len(rows),
        "mpfsplus_correct": sum(row["target_state_correct"] for row in rows),
        "mpfsplus_incorrect": sum(not row["target_state_correct"] for row in rows),
        "manual_review_required": sum(row["manual_review_required"] for row in rows),
        "manual_review_pending": sum(
            row["manual_review_required"] and row["manual_review_status"] != "COMPLETED"
            for row in rows
        ),
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
    incorrect = [row for row in rows if not row["target_state_correct"]]

    def count_reason(code: str) -> int:
        return sum(row["failure_reason_code"] == code for row in incorrect)

    def count_stage(stages: set[str]) -> int:
        return sum(row["first_failure_stage"] in stages for row in incorrect)

    def count_tag(tag: str) -> int:
        return sum(tag in str(row.get("systematic_audit_tags") or "").split(";") for row in incorrect)

    issues = [
        {
            "id": "MPF-ERR-001",
            "stage": "reference_resolution",
            "affected": count_reason("REF_UNKNOWN_COLUMN"),
            "observed": "LLM emits target-column references that are not members of the enumerated legal inventory.",
            "next": (
                "Detect the invalid reference, present only legal IDs plus relevant schema meanings, and run a targeted constrained repair. "
                "Do not auto-map to the nearest name because that can turn a fail-safe error into silent semantic corruption."
            ),
        },
        {
            "id": "MPF-ERR-002",
            "stage": "materialization/provenance",
            "affected": count_tag("CONTROL_FIELD_OPERATION"),
            "observed": "The recurring `operation` source field is treated as an unmapped payload field even when it functions as control/instruction metadata.",
            "next": (
                "Audit the affected samples, then introduce an explicit control-field versus payload-field policy only if the audit confirms that `operation` is non-payload metadata."
            ),
        },
        {
            "id": "MPF-ERR-003",
            "stage": "verification",
            "affected": count_reason("TARGET_MISSING_COLUMN"),
            "observed": "Plans omit required target columns after grounding.",
            "next": "After causal audit, test a constrained required-column coverage repair using only grounded evidence.",
        },
        {
            "id": "MPF-ERR-004",
            "stage": "execution/state_comparison",
            "affected": count_stage({"state_mismatch"}),
            "observed": "Candidates execute successfully but do not match the gold target state.",
            "next": (
                "Use `state_mismatch_audit.csv` to inspect gold delta, predicted delta, and the deterministic state-diff class. "
                "Do not prescribe a post-execution repair until these mismatch subtypes have been manually reviewed."
            ),
        },
        {
            "id": "MPF-ERR-005",
            "stage": "semantic_gate/preflight",
            "affected": count_stage({"semantic_gate", "preflight"}),
            "observed": "Candidates are rejected after compilation by the semantic-risk gate or SQLite preflight.",
            "next": (
                "Analyze semantic-gate and preflight failures separately. Use stage-matched ablations where available; do not infer verifier causality from V0."
            ),
        },
        {
            "id": "MPF-ERR-006",
            "stage": "free_text/materialization",
            "affected": count_tag("DATE_NORMALIZATION"),
            "observed": "Date normalization failures form a systematic free-text subgroup.",
            "next": "Manually distinguish genuinely ambiguous dates, unsupported-but-valid formats, and incorrect evidence spans before changing the normalizer.",
        },
        {
            "id": "MPF-ERR-007",
            "stage": "conflict_planning",
            "affected": count_tag("CONFLICT_AMBIGUITY"),
            "observed": "Conflict behavior is marked ambiguous and the fail-closed policy abstains.",
            "next": (
                "Populate `conflict_ambiguity_gold_label` as TRULY_AMBIGUOUS, RESOLVABLE_FROM_INPUT, RESOLVABLE_FROM_SCHEMA, or UNKNOWN before treating these cases as representation failures."
            ),
        },
    ]
    parts = [
        "# Candidate Fixes",
        "",
        "These are Stage-2 candidates, not established causal fixes. Stage 1.1 must finish the manual audit first.",
        "",
    ]
    for issue in issues:
        parts.extend(
            [
                f"## Issue ID: {issue['id']}",
                "",
                f"Affected stage: {issue['stage']}",
                "",
                f"Affected samples: {issue['affected']}",
                "",
                f"Observed behavior: {issue['observed']}",
                "",
                f"Stage-2 candidate action: {issue['next']}",
                "",
                "Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.",
                "",
            ]
        )
    return "\n".join(parts).rstrip() + "\n"


def build_report(
    rows: list[dict[str, Any]],
    stage_summary: list[dict[str, Any]],
    root_summary: list[dict[str, Any]],
    perf_input: list[dict[str, Any]],
    perf_db: list[dict[str, Any]],
    perf_dependency: list[dict[str, Any]],
    perf_operation: list[dict[str, Any]],
    paired_rows: list[dict[str, Any]],
    unique_rows: list[dict[str, Any]],
    downstream_bypass_summary: list[dict[str, Any]],
    semantic_confusion_summary: list[dict[str, Any]],
    systematic_audit_summary: list[dict[str, Any]],
) -> str:
    total = len(rows)
    correct = sum(row["target_state_correct"] for row in rows)
    admitted = sum(row["admitted"] for row in rows)
    incorrect = total - correct
    executed_wrong = [row for row in rows if row["first_failure_stage"] == "state_mismatch"]
    state_diff_errors = sum(
        row["first_failure_stage"] == "state_mismatch" and bool(row.get("state_diff_error"))
        for row in rows
    )
    abstained = [row for row in rows if row["abstained"]]
    abstained_bypass = sum(str(row["oracle_if_bypassed_correct"]) == "1" for row in abstained)
    paired_counter = Counter(row["paired_category"] for row in paired_rows)
    manual_required = sum(row["manual_review_required"] for row in rows)
    manual_completed = sum(
        row["manual_review_required"] and row["manual_review_status"] == "COMPLETED"
        for row in rows
    )
    manual_pending = manual_required - manual_completed
    state_class_counter: Counter[str] = Counter()
    for row in executed_wrong:
        for code in str(row.get("state_diff_classes") or "").split(";"):
            if code:
                state_class_counter[code] += 1
    unique_conflict = sum(row.get("conflict_sensitive") for row in unique_rows)
    unique_dependency = sum(row.get("dependency_sensitive") for row in unique_rows)
    stage11_complete = manual_pending == 0 and state_diff_errors == 0

    lines = [
        "# MP-FS+ Failure Analysis — Stage 1.1 causal/manual correction",
        "",
        "## 1. Analysis protocol",
        (
            "Frozen prediction artifacts are read without rerunning model inference. State-mismatch cases are replayed on isolated SQLite copies to obtain gold/predicted database deltas. "
            "V0 is treated only as a system-level downstream bypass because it removes multiple components together; it is not interpreted as a single-component verifier intervention."
        ),
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
        "## 6. Final reviewed root-cause labels",
        (
            "For samples with completed manual review, this table uses `reviewer_root_cause`; "
            "all other incorrect samples retain the automatic diagnosis. The automatic-only "
            "summary is exported separately as `root_cause_summary_auto.csv`, while the final "
            "manual-overridden table is exported as `reviewed_root_cause_summary.csv`. "
            "V0 recoverability remains a system-level downstream-bypass observation rather than "
            "a component-isolated verifier causal claim."
        ),
        "",
        markdown_table(root_summary),
        "",
        "## 7. Free-text vs semi-structured",
        markdown_table(perf_input),
        "",
        "## 8. Dependency-sensitive analysis",
        markdown_table(perf_dependency),
        "",
        "## 9. Operation-type analysis",
        markdown_table(perf_operation),
        "",
        "## 10. Database-level analysis",
        markdown_table(perf_db),
        "",
        "## 11. Downstream bypass analysis (system-level, non-causal)",
        (
            "For reference-resolution, materialization, and verification first failures, the available V0 comparison removes hard verification, provenance, semantic gating, and preflight together. "
            "Therefore the table below reports bypass recoverability only; verifier precision/FNR are not computed."
        ),
        "",
        markdown_table(downstream_bypass_summary),
        "",
        "## 12. Semantic-risk gate diagnostic",
        f"Semantic-gate first failures: {sum(row['first_failure_stage'] == 'semantic_gate' for row in rows)}.",
        "",
        markdown_table(semantic_confusion_summary),
        "",
        "## 13. Executed-but-wrong state-diff audit",
        f"Executed successfully but target state wrong: {len(executed_wrong)}. State-diff replay errors: {state_diff_errors}.",
        "",
        markdown_table([{"State-diff class": key, "N": value} for key, value in state_class_counter.items()]),
        "",
        "Detailed gold delta, predicted delta, and final difference are written to `state_mismatch_audit.csv`.",
        "",
        "## 14. Systematic manual-audit groups",
        markdown_table(systematic_audit_summary),
        "",
        (
            f"Manual review required: {manual_required}. Completed: {manual_completed}. Pending: {manual_pending}. "
            "`manual_review_notes` is never silently blank for required rows; pending rows are explicitly marked `PENDING_MANUAL_AUDIT`."
        ),
        "",
        "`manual_audit_evidence.jsonl` contains the source sample, schema DDL, raw/parsed/materialized plan, verification, compiled program, execution, evaluation, and state-diff evidence needed for the audit.",
        "",
        "Use `manual_audit_decisions.template.csv` as the review worksheet. Save completed decisions as "
        f"`{MANUAL_AUDIT_DECISIONS.relative_to(WORKSPACE)}` and rerun the analysis.",
        "",
        "## 15. MP-FS+ vs Direct/J paired analysis",
        markdown_table([{"paired_category": k, "N": v} for k, v in sorted(paired_counter.items())]),
        "",
        (
            "No automatic `why_baseline_succeeded` claim is emitted. For baseline-correct/MP-FS+-wrong samples, the output records only the observed MP-FS+ failure stage/reason; causal explanation requires audit."
        ),
        "",
        "## 16. MP-FS+ partial/unique successes",
        (
            f"Cases where MP-FS+ is correct while at least one baseline is wrong: {len(unique_rows)}. "
            f"Conflict-sensitive among these: {unique_conflict}/{len(unique_rows) if unique_rows else 0}; "
            f"dependency-sensitive: {unique_dependency}/{len(unique_rows) if unique_rows else 0}. "
            "The analysis does not attribute these wins to a specific MP-FS+ feature without component-level evidence."
        ),
        "",
        "## 17. Stage 1.1 completion status",
        (
            "COMPLETE" if stage11_complete else "NOT COMPLETE"
        )
        + f" — manual audits pending={manual_pending}, state-diff replay errors={state_diff_errors}.",
        "",
        "See `candidate_fixes.md` for Stage-2 candidates. They are hypotheses/actions to test after Stage 1.1 audit closure, not established causal fixes.",
        "",
        f"Abstained samples: {len(abstained)}; system/stage bypass-correct among abstentions where an ablation is available: {abstained_bypass}.",
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
    root_summary_auto = build_root_cause_summary_auto(rows)
    reviewed_root_summary = build_reviewed_root_cause_summary(rows)
    perf_input = build_performance_by_input_type(rows)
    perf_dependency = build_performance_by_dependency_sensitivity(rows)
    perf_operation = build_performance_by_operation_type(rows)
    perf_db = build_performance_by_database(rows)
    failure_db = build_failure_by_database(rows)
    paired = build_paired(rows)
    unique = build_unique_successes(rows, artifacts)
    oracle = build_oracle(rows)
    downstream_bypass, downstream_bypass_summary = build_downstream_bypass_analysis(rows)
    semantic_confusion = build_semantic_gate_confusion()
    semantic_confusion_summary = summarize_confusion(semantic_confusion)
    traces = build_diagnostic_traces(rows)
    survival = build_survival(rows)
    manifest = build_manifest(artifacts, rows)
    manual_audit_queue = build_manual_audit_queue(rows)
    manual_audit_evidence = build_manual_audit_evidence(rows, artifacts)
    manual_audit_template = build_manual_audit_template(rows)
    state_mismatch_audit = build_state_mismatch_audit(rows)
    systematic_audit_summary = build_systematic_audit_summary(rows)

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
        "systematic_audit_tags",
        "state_diff_classes",
        "state_diff_error",
        "state_diff_gold_delta",
        "state_diff_predicted_delta",
        "state_diff_difference",
        "manual_review_required",
        "manual_review_label",
        "manual_review_status",
        "reviewer_root_cause",
        "conflict_ambiguity_gold_label",
        "manual_review_notes",
        "oracle_if_bypassed_correct",
        "abstention_reason",
        "direct_correct",
        "jfs_correct",
        "mpfsplus_correct",
        "paired_category",
    ]

    # Remove misleading legacy files if this directory was generated by Stage 1.0.
    for legacy_name in ["verifier_confusion.csv", "verifier_confusion_summary.csv"]:
        legacy_path = output_dir / legacy_name
        if legacy_path.exists():
            legacy_path.unlink()

    write_json(output_dir / "analysis_run_manifest.json", manifest)
    write_csv(output_dir / "mp_fs_plus_sample_level_analysis.csv", rows, master_fields)
    write_jsonl(output_dir / "diagnostic_traces.jsonl", traces)
    write_csv(output_dir / "stage_failure_summary.csv", stage_summary)
    write_csv(output_dir / "root_cause_summary_auto.csv", root_summary_auto)
    write_csv(output_dir / "reviewed_root_cause_summary.csv", reviewed_root_summary)
    # Backward-compatible alias: root_cause_summary.csv now represents the final reviewed summary.
    write_csv(output_dir / "root_cause_summary.csv", reviewed_root_summary)
    write_csv(output_dir / "performance_by_input_type.csv", perf_input)
    write_csv(output_dir / "performance_by_dependency_sensitivity.csv", perf_dependency)
    write_csv(output_dir / "performance_by_operation_type.csv", perf_operation)
    write_csv(output_dir / "performance_by_database.csv", perf_db)
    write_csv(output_dir / "failure_by_database.csv", failure_db)
    write_csv(output_dir / "paired_method_analysis.csv", paired)
    write_csv(output_dir / "mpfsplus_unique_successes.csv", unique)
    write_csv(output_dir / "oracle_rejection_analysis.csv", oracle)
    write_csv(output_dir / "downstream_bypass_analysis.csv", downstream_bypass)
    write_csv(output_dir / "downstream_bypass_summary.csv", downstream_bypass_summary)
    write_csv(output_dir / "semantic_gate_confusion.csv", semantic_confusion)
    write_csv(output_dir / "semantic_gate_confusion_summary.csv", semantic_confusion_summary)
    write_csv(output_dir / "manual_audit_queue.csv", manual_audit_queue)
    write_jsonl(output_dir / "manual_audit_evidence.jsonl", manual_audit_evidence)
    write_csv(output_dir / "manual_audit_decisions.template.csv", manual_audit_template)
    write_csv(output_dir / "state_mismatch_audit.csv", state_mismatch_audit)
    write_csv(output_dir / "systematic_audit_summary.csv", systematic_audit_summary)
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
            reviewed_root_summary,
            perf_input,
            perf_db,
            perf_dependency,
            perf_operation,
            paired,
            unique,
            downstream_bypass_summary,
            semantic_confusion_summary,
            systematic_audit_summary,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "output_dir": str(output_dir),
        "sample_count": len(rows),
        "correct": sum(row["target_state_correct"] for row in rows),
        "incorrect": sum(not row["target_state_correct"] for row in rows),
        "manual_review_required": sum(row["manual_review_required"] for row in rows),
        "manual_review_pending": sum(
            row["manual_review_required"] and row["manual_review_status"] != "COMPLETED"
            for row in rows
        ),
        "state_diff_replay_errors": sum(bool(row.get("state_diff_error")) for row in rows),
        "stage_summary": stage_summary,
        "root_summary": reviewed_root_summary,
        "root_summary_auto": root_summary_auto,
    }


if __name__ == "__main__":
    print(json.dumps(build_all(), ensure_ascii=False, indent=2))
