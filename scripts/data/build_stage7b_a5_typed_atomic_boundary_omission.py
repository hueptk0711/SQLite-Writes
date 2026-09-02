#!/usr/bin/env python3
"""Build Stage7B-A5 typed atomic-boundary and omission-construction audit.

This stage is CPU-only. It audits deterministic, gold-blind candidate-domain
amendments after Stage7E0-A6 closed as a valid feasibility failure. The A6
primary failures are used only as development diagnostics, never as a fresh
acceptance gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    SELECTED_VARIANT,
    CandidateSpan,
    candidate_to_json,
    design_assignments,
    generate_candidate_inventory,
    load_raw_by_sample_id,
)
from scripts.data.build_stage7b_a3_column_conditioned_candidate_selection import parse_schema_tables  # noqa: E402
from scripts.data.build_stage7b_a4_atomic_candidate_domain_omission_cue import (  # noqa: E402
    OMISSION_CUE_PHRASES,
    PATCH_NAME as STAGE7B_A4_PATCH_NAME,
    STAGE_NAME as STAGE7B_A4_NAME,
    _broader_containing_gold_count,
    _candidate_by_gold_span,
    canonical_json,
    detect_omission_constructions,
    schema_inventory_aliases,
    schema_label_alias_index,
    sha256_file as upstream_sha256_file,
    suppressible_span_refs,
    visible_schema_labels,
)


STAGE_NAME = "Stage7B_A5_ENGLISH_TYPED_ATOMIC_BOUNDARY_AND_OMISSION_CONSTRUCTION_AMENDMENT"
PATCH_NAME = "PATCH1"
PACKAGE_DATE = "20260902"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
RAW_DIR_DEFAULT = PROJECT_ROOT.parents[1] / "external_sources" / "gretel_synthetic_text_to_sql_740ab236"
STAGEENG0_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
STAGE7C_A6_NAME = "Stage7C_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROTOCOL_FREEZE"
STAGE7E0_A6_NAME = "Stage7E0_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT"
STAGE7E0_A6_RESULT_DIR = "stage7e0_a6_english_atomic_domain_column_conditioned_uet_rtx4090_primary_results_20260902"

SCIENTIFIC_ARTIFACTS = [
    "SOURCE_INPUT_MANIFEST.json",
    "A6_VALID_FEASIBILITY_FAIL_FREEZE.json",
    "METHOD_AUDIT_PROTOCOL.json",
    "TYPED_ATOMICITY_RULE_SPEC.json",
    "OMIT_ADMISSIBILITY_RULE_SPEC.json",
    "OMISSION_CONSTRUCTION_SUPPRESSION_RULE_SPEC.json",
    "QUOTE_BOUNDARY_RULE_SPEC.json",
    "DESIGN_TRAIN_BASELINE_A4_DOMAIN_AUDIT.json",
    "DESIGN_TRAIN_STAGE7B_A5_DOMAIN_AUDIT.json",
    "FALSE_SUPPRESSION_AUDIT.json",
    "A6_OBSERVED_ERROR_COUNTERFACTUAL_AUDIT.json",
    "SYNTHETIC_TYPED_OMISSION_BOUNDARY_SAFETY_AUDIT.json",
    "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl",
    "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl",
]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return sha256_bytes(data)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _stats(values: list[int]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"min": 0, "median": 0, "mean": 0, "p95": 0, "max": 0}
    return {
        "min": min(ordered),
        "median": median(ordered),
        "mean": mean(ordered),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "max": max(ordered),
    }


def candidate_from_json(payload: dict[str, Any]) -> CandidateSpan:
    return CandidateSpan(
        span_ref=str(payload["span_ref"]),
        start_char=int(payload["start_char"]),
        end_char=int(payload["end_char"]),
        text=str(payload["text"]),
        tags=tuple(payload.get("tags", [])),
        provenance_tags=tuple(payload.get("provenance_tags", [])),
    )


def _strip_trailing_punctuation(text: str) -> str:
    return text.rstrip(".,;:")


def _normal_label(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", text.casefold()).strip(" \t\r\n\"'()[]{}<>.,;:!?"))


def _is_bare_number(text: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", text.strip()))


def _span_by_bounds(inventory: list[CandidateSpan]) -> dict[tuple[int, int], CandidateSpan]:
    return {(candidate.start_char, candidate.end_char): candidate for candidate in inventory}


def _column_is_omit_admissible(column: Any) -> bool:
    if isinstance(column, dict):
        return bool(
            column.get("nullable")
            or column.get("has_default")
            or column.get("primary_key")
            or column.get("autoincrement")
            or column.get("generated")
        )
    return bool(
        getattr(column, "nullable", False)
        or getattr(column, "has_default", False)
        or getattr(column, "primary_key", False)
        or getattr(column, "autoincrement", False)
        or getattr(column, "generated", False)
    )


def omittable_schema_aliases_from_sql_context(sql_context: str) -> dict[str, list[str]]:
    labels: set[str] = set()
    for columns in parse_schema_tables(sql_context).values():
        for column in columns:
            if _column_is_omit_admissible(column):
                labels.add(_normal_label(column.column_name))
    return schema_label_alias_index({label for label in labels if label})


def omittable_schema_aliases_from_inventory(schema_inventory: dict[str, Any]) -> dict[str, list[str]]:
    labels: set[str] = set()
    for column in schema_inventory.get("columns", []):
        if _column_is_omit_admissible(column):
            labels.add(_normal_label(str(column.get("column_name") or "")))
    return schema_label_alias_index({label for label in labels if label})


def typed_complete_literal_reason(candidate: CandidateSpan, inventory: list[CandidateSpan]) -> dict[str, Any] | None:
    """Suppress a numeric child when a same-start complete typed literal exists."""

    text = candidate.text.strip()
    if not _is_bare_number(text):
        return None
    typed_parents = [
        parent
        for parent in inventory
        if parent.start_char == candidate.start_char
        and parent.end_char > candidate.end_char
        and re.fullmatch(re.escape(text) + r"(?:%|\s+percent)", parent.text.strip(), flags=re.IGNORECASE)
    ]
    if not typed_parents:
        return None
    parent = min(typed_parents, key=lambda item: item.end_char - item.start_char)
    typed_label_context = parent.text.strip().casefold().endswith(" percent")
    for container in inventory:
        if container.start_char <= parent.start_char and parent.end_char <= container.end_char and container.start_char < parent.start_char:
            relative_start = parent.start_char - container.start_char
            relative_end = parent.end_char - container.start_char
            residual = _normal_label(f"{container.text[:relative_start]} {container.text[relative_end:]}")
            residual_tokens = set(residual.split())
            if residual_tokens & {"pct", "percent", "percentage"}:
                typed_label_context = True
                break
    if not typed_label_context:
        return None
    return {
        "rule": "TYPED_COMPLETE_LITERAL_DOMINATES_NUMERIC_CHILD",
        "complete_literal_span_ref": parent.span_ref,
        "complete_literal_text": parent.text,
        "numeric_child_text": candidate.text,
    }


def omission_construction_region_reason(candidate: CandidateSpan, detections: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Suppress every candidate fully contained in a deterministic omission construction."""

    stripped_end = candidate.end_char
    while stripped_end > candidate.start_char and candidate.text[: stripped_end - candidate.start_char].endswith((".", ",", ";", ":")):
        stripped_end -= 1
    for detection in detections:
        if int(detection["start_char"]) <= candidate.start_char and stripped_end <= int(detection["end_char"]):
            return {
                "rule": "FULL_OMISSION_CONSTRUCTION_REGION",
                "cue_phrase": detection["cue_phrase"],
                "schema_label": detection["label"],
                "schema_alias_source_labels": detection.get("schema_alias_source_labels", [detection["label"]]),
                "construction_text": detection["text"],
            }
    return None


def boundary_quality_reason(
    candidate: CandidateSpan,
    inventory: list[CandidateSpan],
    schema_aliases: dict[str, list[str]],
) -> dict[str, Any] | None:
    text = candidate.text
    by_bounds = _span_by_bounds(inventory)
    if text.count('"') % 2 == 1:
        return {"rule": "BOUNDARY_UNBALANCED_DOUBLE_QUOTE"}
    stripped = _strip_trailing_punctuation(text)
    if (
        stripped != text
        and (candidate.start_char, candidate.start_char + len(stripped)) in by_bounds
        and re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?|[A-Z0-9][A-Z0-9_-]*", stripped)
    ):
        return {
            "rule": "BOUNDARY_TRAILING_PUNCTUATION_HAS_STRIPPED_CANDIDATE",
            "stripped_span_ref": by_bounds[(candidate.start_char, candidate.start_char + len(stripped))].span_ref,
        }
    if "," in text:
        tail = _normal_label(text.split(",", 1)[1])
        if tail and any(tail.startswith(alias) or alias.startswith(tail) for alias in schema_aliases):
            return {"rule": "BOUNDARY_CROSSES_FIELD_SEPARATOR", "separator": ","}
    for child in inventory:
        if not (candidate.start_char < child.start_char and child.end_char == candidate.end_char):
            continue
        raw_residual = text[: child.start_char - candidate.start_char]
        if "_" not in raw_residual:
            continue
        residual = _normal_label(raw_residual)
        if residual not in schema_aliases:
            continue
        has_longer_value = any(
            other.start_char == child.start_char
            and other.end_char > child.end_char
            and other.end_char <= candidate.end_char + 40
            for other in inventory
        )
        if has_longer_value:
            return {
                "rule": "BOUNDARY_SCHEMA_LABEL_PREFIX_PARTIAL_VALUE",
                "schema_alias_residual": residual,
                "partial_value_span_ref": child.span_ref,
                "partial_value_text": child.text,
            }
    return None


def a5_additional_suppression_reasons(
    inventory: list[CandidateSpan],
    schema_aliases: dict[str, list[str]],
    omission_detections: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    reasons: dict[str, dict[str, Any]] = {}
    for candidate in inventory:
        reason = typed_complete_literal_reason(candidate, inventory)
        if reason is None:
            reason = omission_construction_region_reason(candidate, omission_detections)
        if reason is None:
            reason = boundary_quality_reason(candidate, inventory, schema_aliases)
        if reason is not None:
            reasons[candidate.span_ref] = reason
    return reasons


def a5_suppression_reasons(
    inventory: list[CandidateSpan],
    schema_aliases: dict[str, list[str]],
    omission_detections: list[dict[str, Any]],
    *,
    include_a4: bool,
) -> dict[str, dict[str, Any]]:
    reasons = suppressible_span_refs(inventory, schema_aliases, omission_detections) if include_a4 else {}
    for span_ref, reason in a5_additional_suppression_reasons(inventory, schema_aliases, omission_detections).items():
        reasons.setdefault(span_ref, reason)
    return reasons


def _audit_summary(stage: str, domain: str, acc: dict[str, Any], design_count: int, rule_counts: dict[str, int], suppressed_total: int) -> dict[str, Any]:
    return {
        "stage": stage,
        "patch": PATCH_NAME,
        "domain": domain,
        "status": "PASS",
        "design_sample_count": design_count,
        "assignment_count": acc["assignment_count"],
        "covered_assignment_count": acc["covered_assignment_count"],
        "full_sample_covered_count": acc["full_sample_covered_count"],
        "assignment_representability": acc["covered_assignment_count"] / acc["assignment_count"],
        "full_sample_representability": acc["full_sample_covered_count"] / design_count,
        "candidate_count_stats": _stats(acc["candidate_counts"]),
        "broader_containing_gold_count_stats": _stats(acc["broader_counts"]),
        "broader_containing_gold_total": acc["broader_total"],
        "suppressed_candidate_total": suppressed_total,
        "suppression_rule_counts": dict(sorted(rule_counts.items())),
        "model_called": False,
        "gpu_called": False,
    }


def audit_design_train(raw_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_by_id = load_raw_by_sample_id(raw_dir)
    design_ids, assignments_by_sample, _assignment_rows = design_assignments(PROJECT_ROOT / STAGEENG0_NAME, PROJECT_ROOT / STAGEENG1_NAME)
    baseline_acc = {"candidate_counts": [], "assignment_count": 0, "covered_assignment_count": 0, "full_sample_covered_count": 0, "broader_counts": [], "broader_total": 0}
    a5_acc = {"candidate_counts": [], "assignment_count": 0, "covered_assignment_count": 0, "full_sample_covered_count": 0, "broader_counts": [], "broader_total": 0}
    baseline_rule_counts: dict[str, int] = {}
    a5_rule_counts: dict[str, int] = {}
    baseline_suppressed_total = 0
    a5_suppressed_total = 0
    false_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []

    for sample_id in sorted(design_ids):
        raw = raw_by_id[sample_id]
        question = str(raw.get("sql_prompt") or "")
        inventory = generate_candidate_inventory(question, variant=SELECTED_VARIANT)
        sql_context = str(raw.get("sql_context") or "")
        schema_aliases = schema_label_alias_index(visible_schema_labels(sql_context))
        baseline_detections = detect_omission_constructions(question, schema_aliases)
        omittable_aliases = omittable_schema_aliases_from_sql_context(sql_context)
        omittable_detections = detect_omission_constructions(question, omittable_aliases)
        baseline_reasons = suppressible_span_refs(inventory, schema_aliases, baseline_detections)
        a5_reasons = a5_suppression_reasons(inventory, schema_aliases, omittable_detections, include_a4=True)
        baseline_suppressed = set(baseline_reasons)
        a5_suppressed = set(a5_reasons)
        baseline_inventory = [candidate for candidate in inventory if candidate.span_ref not in baseline_suppressed]
        a5_inventory = [candidate for candidate in inventory if candidate.span_ref not in a5_suppressed]
        baseline_acc["candidate_counts"].append(len(baseline_inventory))
        a5_acc["candidate_counts"].append(len(a5_inventory))
        baseline_suppressed_total += len(baseline_suppressed)
        a5_suppressed_total += len(a5_suppressed)
        for reason in baseline_reasons.values():
            baseline_rule_counts[reason["rule"]] = baseline_rule_counts.get(reason["rule"], 0) + 1
        for reason in a5_reasons.values():
            a5_rule_counts[reason["rule"]] = a5_rule_counts.get(reason["rule"], 0) + 1

        candidate_by_span = _candidate_by_gold_span(inventory)
        baseline_full = True
        a5_full = True
        assignment_payloads = []
        for assignment in sorted(assignments_by_sample.get(sample_id, []), key=lambda item: int(item["assignment_index"])):
            span = assignment["matched_source_span"]
            gold_key = (int(span["start_char"]), int(span["end_char"]), str(span["text"]))
            candidate = candidate_by_span.get(gold_key)
            baseline_covered = bool(candidate and candidate.span_ref not in baseline_suppressed)
            a5_covered = bool(candidate and candidate.span_ref not in a5_suppressed)
            baseline_full = baseline_full and baseline_covered
            a5_full = a5_full and a5_covered
            baseline_acc["assignment_count"] += 1
            a5_acc["assignment_count"] += 1
            baseline_acc["covered_assignment_count"] += int(baseline_covered)
            a5_acc["covered_assignment_count"] += int(a5_covered)
            baseline_broad = _broader_containing_gold_count(inventory, int(span["start_char"]), int(span["end_char"]), suppressed_refs=baseline_suppressed)
            a5_broad = _broader_containing_gold_count(inventory, int(span["start_char"]), int(span["end_char"]), suppressed_refs=a5_suppressed)
            baseline_acc["broader_counts"].append(baseline_broad)
            a5_acc["broader_counts"].append(a5_broad)
            baseline_acc["broader_total"] += baseline_broad
            a5_acc["broader_total"] += a5_broad
            if baseline_covered and not a5_covered:
                false_rows.append(
                    {
                        "sample_id": sample_id,
                        "column_ref_or_name": assignment["column_ref_or_name"],
                        "gold_text": span["text"],
                        "span_ref": candidate.span_ref if candidate else None,
                        "suppression_reason": a5_reasons.get(candidate.span_ref) if candidate else None,
                    }
                )
            assignment_payloads.append(
                {
                    "assignment_index": assignment["assignment_index"],
                    "column_ref_or_name": assignment["column_ref_or_name"],
                    "gold_text": span["text"],
                    "baseline_a4_covered": baseline_covered,
                    "stage7b_a5_covered": a5_covered,
                    "baseline_broader_containing_gold": baseline_broad,
                    "stage7b_a5_broader_containing_gold": a5_broad,
                    "stage7b_a5_suppression_reason": a5_reasons.get(candidate.span_ref) if candidate else None,
                }
            )
        baseline_acc["full_sample_covered_count"] += int(baseline_full)
        a5_acc["full_sample_covered_count"] += int(a5_full)
        audit_rows.append(
            {
                "sample_id": sample_id,
                "question_sha256": sha256_text(question),
                "candidate_generator_variant": SELECTED_VARIANT,
                "baseline_a4_candidate_count": len(baseline_inventory),
                "stage7b_a5_candidate_count": len(a5_inventory),
                "baseline_a4_suppressed_candidate_count": len(baseline_suppressed),
                "stage7b_a5_suppressed_candidate_count": len(a5_suppressed),
                "baseline_a4_full_sample_representable": baseline_full,
                "stage7b_a5_full_sample_representable": a5_full,
                "assignments": assignment_payloads,
            }
        )
        if len(examples) < 500:
            for candidate in inventory:
                reason = a5_reasons.get(candidate.span_ref)
                if reason is None or reason == baseline_reasons.get(candidate.span_ref):
                    continue
                examples.append({"sample_id": sample_id, **candidate_to_json(candidate), "reason": reason})
                if len(examples) >= 500:
                    break

    baseline = _audit_summary(STAGE_NAME, "stage7b_a4_patch2_baseline_domain", baseline_acc, len(design_ids), baseline_rule_counts, baseline_suppressed_total)
    a5 = _audit_summary(STAGE_NAME, "stage7b_a5_typed_boundary_omission_domain_audit", a5_acc, len(design_ids), a5_rule_counts, a5_suppressed_total)
    false = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if not false_rows else "FAIL",
        "baseline_domain": "stage7b_a4_patch2_baseline_domain",
        "candidate_domain_under_review": "stage7b_a5_typed_boundary_omission_domain_audit",
        "additional_assignment_losses": len(false_rows),
        "additional_full_sample_losses": baseline["full_sample_covered_count"] - a5["full_sample_covered_count"],
        "false_suppression_examples": false_rows[:50],
        "strict_gate": "additional gold assignment loss = 0 and additional full-sample loss = 0",
        "model_called": False,
        "gpu_called": False,
    }
    return baseline, a5, false, audit_rows, examples


def _candidate_text(by_ref: dict[str, CandidateSpan], span_ref: str | None) -> str | None:
    if span_ref is None or span_ref == "OMIT":
        return None
    candidate = by_ref.get(span_ref)
    return candidate.text if candidate else None


def _column_meta(row: dict[str, Any], column_ref: str) -> dict[str, Any]:
    for column in row["model_side_input"]["schema_inventory"]["columns"]:
        if column["column_ref"] == column_ref:
            return column
    return {}


def classify_a6_error(
    expected_ref: str | None,
    predicted_ref: str | None,
    by_ref: dict[str, CandidateSpan],
    a5_reasons: dict[str, dict[str, Any]],
    column: dict[str, Any],
) -> str:
    expected_text = _candidate_text(by_ref, expected_ref)
    predicted_text = _candidate_text(by_ref, predicted_ref)
    if predicted_ref == "OMIT" and expected_ref != "OMIT":
        if column.get("nullable") is False and column.get("has_default") is False:
            return "false_omit_required_value"
        return "false_omit_optional_value"
    if expected_ref == "OMIT" and predicted_ref != "OMIT":
        reason = a5_reasons.get(str(predicted_ref), {})
        if reason.get("rule") == "FULL_OMISSION_CONSTRUCTION_REGION":
            return "omission_construction_candidate_leak"
        return "false_value_for_omit"
    reason = a5_reasons.get(str(predicted_ref), {})
    if reason.get("rule") == "TYPED_COMPLETE_LITERAL_DOMINATES_NUMERIC_CHILD":
        return "typed_complete_literal_numeric_child"
    if str(reason.get("rule", "")).startswith("BOUNDARY_"):
        return "quote_punctuation_boundary_quality"
    if predicted_text and expected_text and _strip_trailing_punctuation(predicted_text).strip("\"") == expected_text:
        return "trailing_punctuation_or_quote_normalization"
    return "other_semantic_value_error"


def a6_observed_error_counterfactual(stage7c_a6_dir: Path, stage7e0_a6_dir: Path) -> dict[str, Any]:
    frozen_rows = {row["sample_id"]: row for row in read_jsonl(stage7c_a6_dir / "FRESH_ENGLISH_A6_PRIMARY_FEASIBILITY_SET.jsonl")}
    raw_rows = {
        row["sample_id"]: json.loads(row["raw_output"])
        for row in read_jsonl(stage7e0_a6_dir / "uet_p4" / STAGE7E0_A6_RESULT_DIR / "raw_primary_phase_o_generations.jsonl")
        if row.get("raw_output")
    }
    case_rows = read_jsonl(stage7e0_a6_dir / "uet_p4" / STAGE7E0_A6_RESULT_DIR / "primary_case_results.jsonl")
    decision_rows: list[dict[str, Any]] = []
    wrong_span_suppressed = 0
    wrong_required_omit_structurally_impossible = 0
    correct_gold_suppressed = 0
    family_counts: dict[str, int] = {}

    for sample_id in sorted(frozen_rows):
        row = frozen_rows[sample_id]
        expected = row["label_side_expected"]["phase_o"]["column_span_refs"]
        predicted = raw_rows[sample_id].get("column_span_refs", {})
        inventory = [candidate_from_json(candidate) for candidate in row["runtime_constraints"]["candidate_inventory"]]
        by_ref = {candidate.span_ref: candidate for candidate in inventory}
        schema_aliases = schema_inventory_aliases(row["model_side_input"]["schema_inventory"])
        omittable_aliases = omittable_schema_aliases_from_inventory(row["model_side_input"]["schema_inventory"])
        omittable_detections = detect_omission_constructions(row["model_side_input"]["question"], omittable_aliases)
        reasons = a5_suppression_reasons(inventory, schema_aliases, omittable_detections, include_a4=False)
        for column_ref in sorted(set(expected) | set(predicted)):
            expected_ref = expected.get(column_ref)
            predicted_ref = predicted.get(column_ref)
            if expected_ref == predicted_ref:
                continue
            column = _column_meta(row, column_ref)
            family = classify_a6_error(expected_ref, predicted_ref, by_ref, reasons, column)
            family_counts[family] = family_counts.get(family, 0) + 1
            pred_suppressed = bool(predicted_ref and predicted_ref != "OMIT" and predicted_ref in reasons)
            gold_suppressed = bool(expected_ref and expected_ref != "OMIT" and expected_ref in reasons)
            required_omit_structurally_impossible = bool(
                predicted_ref == "OMIT"
                and expected_ref != "OMIT"
                and column.get("nullable") is False
                and column.get("has_default") is False
                and not column.get("primary_key")
                and not column.get("autoincrement")
                and not column.get("generated")
            )
            wrong_span_suppressed += int(pred_suppressed)
            wrong_required_omit_structurally_impossible += int(required_omit_structurally_impossible)
            correct_gold_suppressed += int(gold_suppressed)
            decision_rows.append(
                {
                    "sample_id": sample_id,
                    "column_ref": column_ref,
                    "column_name": column.get("column_name"),
                    "expected_span_ref": expected_ref,
                    "expected_text": _candidate_text(by_ref, expected_ref),
                    "predicted_span_ref": predicted_ref,
                    "predicted_text": _candidate_text(by_ref, predicted_ref),
                    "error_family": family,
                    "stage7b_a5_wrong_span_choice_suppressed": pred_suppressed,
                    "stage7b_a5_wrong_required_omit_structurally_impossible": required_omit_structurally_impossible,
                    "stage7b_a5_correct_gold_suppressed": gold_suppressed,
                    "stage7b_a5_suppression_reason": reasons.get(str(predicted_ref)),
                }
            )
    pass_cases = sum(1 for row in case_rows if row.get("status") == "PASS")
    observed_addressed = wrong_span_suppressed + wrong_required_omit_structurally_impossible
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if pass_cases == 2 and len(decision_rows) == 15 and observed_addressed == 15 and correct_gold_suppressed == 0 else "FAIL",
        "audit_type": "development_diagnostic_not_independent_evaluation",
        "source_stage": STAGE7E0_A6_NAME,
        "source_primary_result": "2/12 valid feasibility failure, closed",
        "case_exact_pass_count": f"{pass_cases}/12",
        "wrong_decision_count": len(decision_rows),
        "stage7b_a5_wrong_span_choices_suppressed": wrong_span_suppressed,
        "stage7b_a5_wrong_required_omit_structurally_impossible": wrong_required_omit_structurally_impossible,
        "stage7b_a5_observed_wrong_decisions_addressed": observed_addressed,
        "stage7b_a5_correct_gold_suppressed": correct_gold_suppressed,
        "error_family_counts": dict(sorted(family_counts.items())),
        "decision_rows": decision_rows,
        "model_called": False,
        "gpu_called": False,
    }


def synthetic_safety_audit() -> dict[str, Any]:
    fixtures = [
        {
            "name": "percent_complete_literal",
            "question": "Insert hydration_pct 68%, completion_percentage 68 percent, and proof_minutes 42.",
            "aliases": {"hydration pct", "proof minutes"},
            "omittable_aliases": set(),
            "must_suppress": ["68"],
            "must_keep": ["68%", "68 percent", "42"],
        },
        {
            "name": "required_status_absent_missing_kept",
            "question": 'Insert record. Required status absent, required status missing, status "Absent", and status "Missing".',
            "aliases": {"status"},
            "omittable_aliases": set(),
            "must_suppress": [],
            "must_keep": ["status absent", "absent", "status missing", "missing", "Absent", "Missing"],
        },
        {
            "name": "optional_memo_absent_suppressed",
            "question": "Insert record. Optional memo absent.",
            "aliases": {"memo"},
            "omittable_aliases": {"memo"},
            "must_suppress": ["memo absent", "absent"],
            "must_keep": [],
        },
        {
            "name": "percentage_context_rejects_generic_alpha_suffix",
            "question": "Insert hydration_pct 68kg and completion_percentage 68abc.",
            "aliases": {"hydration pct", "completion percentage"},
            "omittable_aliases": set(),
            "must_suppress": [],
            "must_keep": ["68", "68kg", "68abc"],
        },
        {
            "name": "unit_suffixes_are_not_percentage_literals",
            "question": "Insert weight_kg 68kg and duration_ms 25ms.",
            "aliases": {"weight kg", "duration ms"},
            "omittable_aliases": set(),
            "must_suppress": [],
            "must_keep": ["68", "68kg", "25", "25ms"],
        },
        {
            "name": "quoted_absent_missing_literals_kept",
            "question": 'Insert record. Optional memo "Absent" and optional note "Missing".',
            "aliases": {"field memo", "status"},
            "omittable_aliases": {"field memo"},
            "must_suppress": [],
            "must_keep": ["Absent", "Missing"],
        },
        {
            "name": "unbalanced_quote_boundary",
            "question": "Insert scanner \"flatbed nine\" and job_id JOB-9.",
            "aliases": {"scanner name", "job id"},
            "omittable_aliases": set(),
            "must_suppress": ['scanner "flatbed'],
            "must_keep": ["flatbed nine", "JOB-9"],
        },
        {
            "name": "label_prefix_partial_value",
            "question": "Insert docket_stage second review and intake_id INT-7.",
            "aliases": {"docket stage", "intake id"},
            "omittable_aliases": set(),
            "must_suppress": ["docket_stage second"],
            "must_keep": ["second review", "INT-7"],
        },
    ]
    rows = []
    failures: list[str] = []
    for fixture in fixtures:
        inventory = generate_candidate_inventory(fixture["question"], variant=SELECTED_VARIANT)
        aliases = schema_label_alias_index(fixture["aliases"])
        omittable_aliases = schema_label_alias_index(fixture["omittable_aliases"])
        detections = detect_omission_constructions(fixture["question"], omittable_aliases)
        reasons = a5_suppression_reasons(inventory, aliases, detections, include_a4=False)
        by_text: dict[str, list[CandidateSpan]] = {}
        for candidate in inventory:
            by_text.setdefault(candidate.text, []).append(candidate)
        suppressed_texts = {candidate.text for candidate in inventory if candidate.span_ref in reasons}
        for text in fixture["must_suppress"]:
            if text not in suppressed_texts:
                failures.append(f"{fixture['name']}:not_suppressed:{text}")
        for text in fixture["must_keep"]:
            if text not in by_text:
                failures.append(f"{fixture['name']}:missing_fixture_candidate:{text}")
                continue
            if any(candidate.span_ref in reasons for candidate in by_text.get(text, [])):
                failures.append(f"{fixture['name']}:incorrectly_suppressed:{text}")
        rows.append({"fixture": fixture["name"], "suppressed_texts": sorted(suppressed_texts), "reasons": reasons})
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "fixture_count": len(fixtures),
        "fixtures": rows,
        "model_called": False,
        "gpu_called": False,
    }


def source_input_manifest(raw_dir: Path) -> dict[str, Any]:
    rels = [
        "scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/validate_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/build_stage7e0_a6_english_preflight.py",
        "scripts/data/validate_stage7e0_a6_english_preflight.py",
        "scripts/server/preflight_runtime_stage7e0_a6.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/server/run_stage7e0_a6_english.py",
        "scripts/data/validate_stage7e0_a6_server_results.py",
        "src/nldbwrite_v3/__init__.py",
        "src/nldbwrite_v3/inference",
        "src/nldbwrite_v3/v2_a1",
        "tests/conftest.py",
        "tests/test_stage7b_a5_typed_atomic_boundary_omission.py",
        "tests/test_stage7e0_a6_english_preflight.py",
        f"{STAGE7C_A6_NAME}/STAGE7C_A6_LOCK.json",
        f"{STAGE7E0_A6_NAME}/STAGE7E0_A6_SERVER_RESULT_LOCK.json",
        f"{STAGE7E0_A6_NAME}/SERVER_RESULT_IMPORT_REPORT.json",
    ]
    files = []
    for rel in rels:
        path = PROJECT_ROOT / rel
        if path.is_file():
            files.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    raw_file = raw_dir / "synthetic_text_to_sql_train.snappy.parquet"
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "git_branch": git_output("branch", "--show-current"),
        "git_commit": git_output("rev-parse", "HEAD"),
        "raw_dir": str(raw_dir),
        "raw_train_parquet_present": raw_file.is_file(),
        "raw_train_parquet_sha256": sha256_file(raw_file) if raw_file.is_file() else None,
        "source_files": files,
        "model_called": False,
        "gpu_called": False,
    }


def a6_valid_fail_freeze(stage7e0_a6_dir: Path) -> dict[str, Any]:
    lock = read_json(stage7e0_a6_dir / "STAGE7E0_A6_SERVER_RESULT_LOCK.json")
    report = read_json(stage7e0_a6_dir / "SERVER_RESULT_IMPORT_REPORT.json")
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "source_stage": STAGE7E0_A6_NAME,
        "source_status": "VALID_FEASIBILITY_FAIL_CLOSED",
        "source_lock_status": lock["status"],
        "primary_pass_count": lock["primary_pass_count"],
        "required_pass_count": lock["required_pass_count"],
        "evidence_integrity_status": lock["evidence_integrity_status"],
        "protocol_compliance_status": lock["protocol_compliance_status"],
        "scientific_result_eligible": lock["scientific_result_eligible"],
        "diagnostics_run": lock["diagnostics_run"],
        "gretel_pilot_opened": lock["gretel_pilot_opened"],
        "source_tar_sha256": report["source_tar"]["sha256"],
        "a6_failures_used_as": "development_diagnostic_not_independent_evaluation",
        "model_called": False,
        "gpu_called": False,
    }


def build_derived_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = [
        {"path": name, "bytes": (stage_dir / name).stat().st_size, "sha256": sha256_file(stage_dir / name)}
        for name in SCIENTIFIC_ARTIFACTS
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def validation_report(baseline: dict[str, Any], a5: dict[str, Any], false: dict[str, Any], a6: dict[str, Any], synthetic: dict[str, Any]) -> str:
    return f"""# Stage7B-A5 Typed Atomic Boundary and Omission-Construction Amendment Validation Report

Status: {"PASS" if false["status"] == "PASS" and a6["status"] == "PASS" and synthetic["status"] == "PASS" else "FAIL"}

Validation date: {date.today().isoformat()}

## Scope

CPU-only audit. No model call, no GPU call, no Gretel pilot/dev/test rows.
A6 real failures are development diagnostics only.

```text
design_train_non_pilot_count={baseline["design_sample_count"]}
assignment_count={baseline["assignment_count"]}
baseline_a4_assignment_representability={baseline["covered_assignment_count"]}/{baseline["assignment_count"]}
baseline_a4_full_sample_representability={baseline["full_sample_covered_count"]}/{baseline["design_sample_count"]}
stage7b_a5_assignment_representability={a5["covered_assignment_count"]}/{a5["assignment_count"]}
stage7b_a5_full_sample_representability={a5["full_sample_covered_count"]}/{a5["design_sample_count"]}
additional_assignment_losses={false["additional_assignment_losses"]}
additional_full_sample_losses={false["additional_full_sample_losses"]}
a6_primary_result=2/12 valid feasibility failure
a6_wrong_decisions={a6["wrong_decision_count"]}
a6_wrong_span_choices_suppressed_by_a5={a6["stage7b_a5_wrong_span_choices_suppressed"]}
a6_wrong_required_omit_structurally_impossible={a6["stage7b_a5_wrong_required_omit_structurally_impossible"]}
a6_observed_wrong_decisions_addressed={a6["stage7b_a5_observed_wrong_decisions_addressed"]}
a6_correct_gold_suppressed_by_a5={a6["stage7b_a5_correct_gold_suppressed"]}
synthetic_safety={synthetic["status"]}
model_called=false
gpu_called=false
```

## Decision

The Stage7B-A5 audit is ready for reviewer inspection only if the strict design
gate remains zero additional gold losses. This package does not authorize an
A6 rerun and does not open Gretel.
"""


def reviewer_readme() -> str:
    return f"""# Stage7B-A5 Typed Atomic Boundary and Omission-Construction Amendment

Review this CPU-only audit after Stage7E0-A6 was closed as a valid feasibility
failure at 2/12. The A6 failures are diagnostics, not an independent gate.

Review order:

1. `{STAGE_NAME}/VALIDATION_REPORT.md`
2. `{STAGE_NAME}/A6_VALID_FEASIBILITY_FAIL_FREEZE.json`
3. `{STAGE_NAME}/METHOD_AUDIT_PROTOCOL.json`
4. `{STAGE_NAME}/TYPED_ATOMICITY_RULE_SPEC.json`
5. `{STAGE_NAME}/OMIT_ADMISSIBILITY_RULE_SPEC.json`
6. `{STAGE_NAME}/OMISSION_CONSTRUCTION_SUPPRESSION_RULE_SPEC.json`
7. `{STAGE_NAME}/QUOTE_BOUNDARY_RULE_SPEC.json`
8. `{STAGE_NAME}/DESIGN_TRAIN_BASELINE_A4_DOMAIN_AUDIT.json`
9. `{STAGE_NAME}/DESIGN_TRAIN_STAGE7B_A5_DOMAIN_AUDIT.json`
10. `{STAGE_NAME}/FALSE_SUPPRESSION_AUDIT.json`
11. `{STAGE_NAME}/A6_OBSERVED_ERROR_COUNTERFACTUAL_AUDIT.json`
12. `{STAGE_NAME}/SYNTHETIC_TYPED_OMISSION_BOUNDARY_SAFETY_AUDIT.json`
13. `{STAGE_NAME}/CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl`
14. `{STAGE_NAME}/CANDIDATE_SUPPRESSION_EXAMPLES.jsonl`
15. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
16. `{STAGE_NAME}/STAGE7B_A5_LOCK.json`
17. `scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py`
18. `scripts/data/validate_stage7b_a5_typed_atomic_boundary_omission.py`
19. `tests/test_stage7b_a5_typed_atomic_boundary_omission.py`

Clean extraction validation:

```bash
python scripts/data/validate_stage7b_a5_typed_atomic_boundary_omission.py --stage-dir {STAGE_NAME}
python -m pytest -q
```

Full rebuild requires the local Gretel parquet source:

```bash
uv run --with pyarrow python scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236
```
"""


def build_stage(out_dir: Path, raw_dir: Path) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline, a5, false, rows, examples = audit_design_train(raw_dir)
    a6 = a6_observed_error_counterfactual(PROJECT_ROOT / STAGE7C_A6_NAME, PROJECT_ROOT / STAGE7E0_A6_NAME)
    synthetic = synthetic_safety_audit()

    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(raw_dir))
    write_json(out_dir / "A6_VALID_FEASIBILITY_FAIL_FREEZE.json", a6_valid_fail_freeze(PROJECT_ROOT / STAGE7E0_A6_NAME))
    write_json(
        out_dir / "METHOD_AUDIT_PROTOCOL.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "scope": "CPU-only design-train candidate-domain audit",
            "baseline": f"{STAGE7B_A4_NAME} {STAGE7B_A4_PATCH_NAME}",
            "design_train_non_pilot_rows": EXPECTED_DESIGN_SAMPLE_COUNT,
            "assignment_count": EXPECTED_ASSIGNMENT_COUNT,
            "strict_gate": "additional_assignment_losses=0 and additional_full_sample_losses=0",
            "a6_observed_errors_used_as": "development_diagnostic_not_independent_evaluation",
            "forbidden": ["model rerun", "A6 primary rerun", "prompt tuning on A6 primary", "Gretel pilot"],
            "model_called": False,
            "gpu_called": False,
        },
    )
    write_json(
        out_dir / "TYPED_ATOMICITY_RULE_SPEC.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "rule_name": "typed_complete_literal_dominates_numeric_child",
            "purpose": "Prefer complete percent literals such as 68% or 68 percent over numeric child 68 when both spans are present.",
            "dominant_suffixes": ["%", " percent"],
            "forbidden_suffix_policy": "Generic alphabetic suffixes such as kg, ms, and abc are not percentage literals and do not suppress the numeric child.",
            "activated_only_by_label_context": ["pct", "percent", "percentage"],
            "gold_blind": True,
            "model_called": False,
            "gpu_called": False,
        },
    )
    write_json(
        out_dir / "OMIT_ADMISSIBILITY_RULE_SPEC.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "rule_name": "schema_semantic_omit_admissibility",
            "purpose": "Allow omission-region suppression and dynamic OMIT availability only for schema columns whose SQL semantics permit an omitted value.",
            "omit_admissible_when_any": ["nullable", "has_default", "primary_key", "autoincrement", "generated"],
            "omit_forbidden_when_all": ["not nullable", "no default", "not primary key", "not autoincrement", "not generated"],
            "gold_blind": True,
            "model_called": False,
            "gpu_called": False,
        },
    )
    write_json(
        out_dir / "OMISSION_CONSTRUCTION_SUPPRESSION_RULE_SPEC.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "rule_name": "full_omission_construction_region_suppression",
            "cue_phrases": list(OMISSION_CUE_PHRASES),
            "purpose": "Suppress normal candidate spans inside deterministic schema-label omission constructions only when that schema column is semantically omittable; keep the special OMIT decision outside the candidate inventory.",
            "gold_blind": True,
            "model_called": False,
            "gpu_called": False,
        },
    )
    write_json(
        out_dir / "QUOTE_BOUNDARY_RULE_SPEC.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "rules": [
                "unbalanced double quote",
                "trailing punctuation when stripped candidate exists",
                "candidate crossing a comma field separator into a schema label",
                "schema-label prefix plus partial value when a longer value candidate exists",
            ],
            "gold_blind": True,
            "model_called": False,
            "gpu_called": False,
        },
    )
    write_json(out_dir / "DESIGN_TRAIN_BASELINE_A4_DOMAIN_AUDIT.json", baseline)
    write_json(out_dir / "DESIGN_TRAIN_STAGE7B_A5_DOMAIN_AUDIT.json", a5)
    write_json(out_dir / "FALSE_SUPPRESSION_AUDIT.json", false)
    write_json(out_dir / "A6_OBSERVED_ERROR_COUNTERFACTUAL_AUDIT.json", a6)
    write_json(out_dir / "SYNTHETIC_TYPED_OMISSION_BOUNDARY_SAFETY_AUDIT.json", synthetic)
    write_jsonl(out_dir / "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl", rows)
    write_jsonl(out_dir / "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl", examples)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_derived_manifest(out_dir))
    status = "PASS_READY_FOR_REVIEW" if false["status"] == "PASS" and a6["status"] == "PASS" and synthetic["status"] == "PASS" else "FAIL_DO_NOT_FREEZE"
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output("branch", "--show-current"),
        "git_commit": git_output("rev-parse", "HEAD"),
        "baseline_stage": STAGE7B_A4_NAME,
        "baseline_patch": STAGE7B_A4_PATCH_NAME,
        "design_train_non_pilot_count": baseline["design_sample_count"],
        "assignment_count": baseline["assignment_count"],
        "baseline_a4_assignment_representability": baseline["assignment_representability"],
        "baseline_a4_full_sample_representability": baseline["full_sample_representability"],
        "stage7b_a5_assignment_representability": a5["assignment_representability"],
        "stage7b_a5_full_sample_representability": a5["full_sample_representability"],
        "additional_assignment_losses": false["additional_assignment_losses"],
        "additional_full_sample_losses": false["additional_full_sample_losses"],
        "a6_primary_result": "2/12 valid feasibility failure closed",
        "a6_wrong_decisions": a6["wrong_decision_count"],
        "a6_wrong_span_choices_suppressed_by_a5": a6["stage7b_a5_wrong_span_choices_suppressed"],
        "a6_wrong_required_omit_structurally_impossible": a6["stage7b_a5_wrong_required_omit_structurally_impossible"],
        "a6_observed_wrong_decisions_addressed": a6["stage7b_a5_observed_wrong_decisions_addressed"],
        "a6_correct_gold_suppressed_by_a5": a6["stage7b_a5_correct_gold_suppressed"],
        "method_freeze_authorized": False,
        "a6_rerun_authorized": False,
        "gretel_pilot_opened": False,
        "model_called": False,
        "gpu_called": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7B_A5_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(baseline, a5, false, a6, synthetic))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme())
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": status,
        "design_train_non_pilot_count": baseline["design_sample_count"],
        "assignment_count": baseline["assignment_count"],
        "additional_assignment_losses": false["additional_assignment_losses"],
        "additional_full_sample_losses": false["additional_full_sample_losses"],
        "a6_primary_result": "2/12",
        "a6_wrong_decisions": a6["wrong_decision_count"],
        "a6_wrong_span_choices_suppressed_by_a5": a6["stage7b_a5_wrong_span_choices_suppressed"],
        "a6_wrong_required_omit_structurally_impossible": a6["stage7b_a5_wrong_required_omit_structurally_impossible"],
        "a6_observed_wrong_decisions_addressed": a6["stage7b_a5_observed_wrong_decisions_addressed"],
        "model_called": False,
        "gpu_called": False,
    }


def include_paths_for_package(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    rels = [
        "pyproject.toml",
        "scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/validate_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/build_stage7e0_a6_english_preflight.py",
        "scripts/data/validate_stage7e0_a6_english_preflight.py",
        "scripts/server/preflight_runtime_stage7e0_a6.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/server/run_stage7e0_a6_english.py",
        "scripts/data/validate_stage7e0_a6_server_results.py",
        "requirements-inference-uet-rtx4090-cu124.lock.txt",
        "tests/conftest.py",
        "tests/test_stage7b_a5_typed_atomic_boundary_omission.py",
        "tests/test_stage7e0_a6_english_preflight.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "src/nldbwrite_v3/__init__.py",
        "src/nldbwrite_v3",
        STAGE7B_A4_NAME,
        STAGE7C_A6_NAME,
        STAGE7E0_A6_NAME,
    ]
    for rel in rels:
        path = PROJECT_ROOT / rel
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                child
                for child in path.rglob("*")
                if child.is_file()
                and "__pycache__" not in child.parts
                and not (rel == "src/nldbwrite_v3" and "analysis" in child.relative_to(path).parts)
            )
    return sorted({path for path in files if path.is_file()}, key=lambda item: item.as_posix())


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_paths_for_package(stage_dir):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            elif path.name == "sitecustomize.py" and "windows_py314_pytest_tempdir" in path.parts:
                arcname = Path("sitecustomize.py")
            else:
                arcname = path.relative_to(PROJECT_ROOT)
            archive.write(path, arcname.as_posix())
    with zipfile.ZipFile(package_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity check failed at {bad}")
    digest = sha256_file(package_path)
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(f"{digest}  {package_path.name}\n", encoding="utf-8", newline="\n")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    args = parser.parse_args()
    summary = build_stage(args.out_dir, args.raw_dir)
    digest = package_reviewer(args.out_dir, args.package)
    summary["package"] = str(args.package)
    summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
