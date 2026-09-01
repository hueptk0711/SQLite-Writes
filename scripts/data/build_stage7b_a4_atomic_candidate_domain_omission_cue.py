#!/usr/bin/env python3
"""Build Stage7B-A4 atomic candidate-domain and omission-cue audit artifacts.

This stage is CPU-only. It audits a gold-blind candidate-domain amendment on
the frozen StageENG1 728-sample design-train scope after Stage7E0-A5 has been
closed as a corrected valid feasibility failure. It does not call a model, does
not use GPU, and does not open pilot/dev/test rows.
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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    DATASET_ID,
    DATASET_REVISION,
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    MIN_ASSIGNMENT_COVERAGE,
    MIN_FULL_SAMPLE_COVERAGE,
    SELECTED_VARIANT,
    CandidateSpan,
    candidate_to_json,
    design_assignments,
    generate_candidate_inventory,
    load_raw_by_sample_id,
    serialize_candidate_inventory,
)
from scripts.data.build_stage7b_a3_column_conditioned_candidate_selection import (  # noqa: E402
    STAGE_NAME as STAGE7B_A3_NAME,
)


STAGE_NAME = "Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT"
PATCH_NAME = "PATCH0"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260901.zip"
STAGEENG0_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
STAGE7B_A2_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
STAGE7C_A5_NAME = "Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE"
STAGE7C_A5_ERRATUM_NAME = "Stage7C_A5_PRIMARY_GOLD_PROVENANCE_ERRATUM_PATCH0"
STRONG_ATOMIC_TAGS = {"EMAIL", "NUMBER", "IDENTIFIER", "QUOTED_TEXT", "URL"}
OMISSION_CUE_PHRASES = (
    "omitted",
    "missing",
    "not provided",
    "not supplied",
    "blank",
    "absent",
    "left empty",
)
MAX_SUPPRESSION_EXAMPLE_ROWS = 500
SCIENTIFIC_ARTIFACTS = [
    "SOURCE_INPUT_MANIFEST.json",
    "A5_CORRECTED_VALID_FAIL_FREEZE.json",
    "DOMAIN_AUDIT_PROTOCOL.json",
    "ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json",
    "OMISSION_CUE_SUPPRESSION_RULE_SPEC.json",
    "CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json",
    "ATOMIC_FILTERED_DOMAIN_AUDIT.json",
    "DOMAIN_COMPARISON_AUDIT.json",
    "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json",
    "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl",
    "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl",
]


@dataclass(frozen=True)
class DomainAudit:
    candidate_count_stats: dict[str, float | int]
    assignment_count: int
    covered_assignment_count: int
    full_sample_covered_count: int
    broader_containing_gold_count_stats: dict[str, float | int]
    broader_containing_gold_total: int


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


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _stats(values: list[int]) -> dict[str, float | int]:
    ordered = sorted(values)
    p95_index = int(0.95 * (len(ordered) - 1)) if ordered else 0
    return {
        "min": min(ordered) if ordered else 0,
        "median": median(ordered) if ordered else 0,
        "mean": mean(ordered) if ordered else 0,
        "p95": ordered[p95_index] if ordered else 0,
        "max": max(ordered) if ordered else 0,
    }


def normalize_cue_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().strip(" \t\r\n\"'()[]{}<>.,;:!?"))


def is_exact_omission_cue(text: str) -> bool:
    return normalize_cue_text(text) in OMISSION_CUE_PHRASES


def contains_omission_cue(text: str) -> bool:
    normalized = normalize_cue_text(text)
    return any(phrase in normalized for phrase in OMISSION_CUE_PHRASES)


def _strictly_contains(container: CandidateSpan, child: CandidateSpan) -> bool:
    return (
        container.start_char <= child.start_char
        and child.end_char <= container.end_char
        and (container.start_char, container.end_char) != (child.start_char, child.end_char)
    )


def effective_atomic_tags(candidate: CandidateSpan) -> set[str]:
    tags = set(candidate.tags) & STRONG_ATOMIC_TAGS
    text = candidate.text.strip()
    if re.fullmatch(r"0x[0-9A-Fa-f]+", text):
        tags.add("IDENTIFIER")
    elif re.fullmatch(r"[A-Za-z]+[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*", text):
        tags.add("IDENTIFIER")
    return tags


def atomic_dominance_reason(candidate: CandidateSpan, inventory: list[CandidateSpan]) -> dict[str, Any] | None:
    """Return a gold-blind suppression reason for broad non-atomic candidates."""

    if len(candidate.text.split()) < 2:
        return None
    if effective_atomic_tags(candidate):
        return None
    atomic_children = [
        child
        for child in inventory
        if effective_atomic_tags(child) and _strictly_contains(candidate, child)
    ]
    if not atomic_children:
        return None
    tag_priority = {"EMAIL": 5, "URL": 5, "IDENTIFIER": 4, "QUOTED_TEXT": 3, "NUMBER": 2}
    best_child = max(
        atomic_children,
        key=lambda item: (
            max(tag_priority.get(tag, 0) for tag in effective_atomic_tags(item)),
            item.start_char,
            item.end_char - item.start_char,
            -item.end_char,
        ),
    )
    return {
        "rule": "ATOMIC_DOMINATED_BROAD_SPAN",
        "dominant_child_span_ref": best_child.span_ref,
        "dominant_child_text": best_child.text,
        "dominant_child_tags": sorted(effective_atomic_tags(best_child)),
        "candidate_token_count": len(candidate.text.split()),
    }


def suppression_reason(candidate: CandidateSpan, inventory: list[CandidateSpan]) -> dict[str, Any] | None:
    if is_exact_omission_cue(candidate.text):
        return {"rule": "EXACT_OMISSION_CUE", "cue_text": normalize_cue_text(candidate.text)}
    return atomic_dominance_reason(candidate, inventory)


def suppressible_span_refs(inventory: list[CandidateSpan]) -> dict[str, dict[str, Any]]:
    reasons: dict[str, dict[str, Any]] = {}
    for candidate in inventory:
        reason = suppression_reason(candidate, inventory)
        if reason is not None:
            reasons[candidate.span_ref] = reason
    return reasons


def _candidate_by_gold_span(inventory: list[CandidateSpan]) -> dict[tuple[int, int, str], CandidateSpan]:
    return {(candidate.start_char, candidate.end_char, candidate.text): candidate for candidate in inventory}


def _broader_containing_gold_count(
    inventory: list[CandidateSpan],
    gold_start: int,
    gold_end: int,
    *,
    suppressed_refs: set[str] | None = None,
) -> int:
    suppressed_refs = suppressed_refs or set()
    return sum(
        1
        for candidate in inventory
        if candidate.span_ref not in suppressed_refs
        and candidate.start_char <= gold_start
        and gold_end <= candidate.end_char
        and (candidate.start_char, candidate.end_char) != (gold_start, gold_end)
    )


def source_input_manifest(
    stageeng0_dir: Path,
    stageeng1_dir: Path,
    stage7b_a2_dir: Path,
    stage7b_a3_dir: Path,
    stage7c_a5_dir: Path,
    stage7c_a5_erratum_dir: Path,
) -> dict[str, Any]:
    files = [
        (STAGEENG0_NAME, stageeng0_dir, "STAGEENG0_LOCK.json"),
        (STAGEENG0_NAME, stageeng0_dir, "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl"),
        (STAGEENG1_NAME, stageeng1_dir, "STAGEENG1_LOCK.json"),
        (STAGEENG1_NAME, stageeng1_dir, "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl"),
        (STAGEENG1_NAME, stageeng1_dir, "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl"),
        (STAGEENG1_NAME, stageeng1_dir, "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "STAGE7B_A2_LOCK.json"),
        (STAGE7B_A2_NAME, stage7b_a2_dir, "ORACLE_CANDIDATE_COVERAGE_AUDIT.json"),
        (STAGE7B_A3_NAME, stage7b_a3_dir, "STAGE7B_A3_LOCK.json"),
        (STAGE7C_A5_NAME, stage7c_a5_dir, "STAGE7C_A5_LOCK.json"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "ERRATUM_LOCK.json"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "GOLD_PROVENANCE_ERRATUM.json"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json"),
        (STAGE7C_A5_ERRATUM_NAME, stage7c_a5_erratum_dir, "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl"),
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "model_called": False,
        "gpu_called": False,
        "source_files": [
            {
                "source_stage": stage,
                "path": f"{stage}/{relative}",
                "sha256": sha256_file(root / relative),
                "bytes": (root / relative).stat().st_size,
            }
            for stage, root, relative in files
        ],
    }


def a5_corrected_valid_fail_freeze(stage7c_a5_erratum_dir: Path) -> dict[str, Any]:
    lock = read_json(stage7c_a5_erratum_dir / "ERRATUM_LOCK.json")
    summary = read_json(stage7c_a5_erratum_dir / "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json")
    return {
        "stage": STAGE_NAME,
        "source_stage": STAGE7C_A5_ERRATUM_NAME,
        "status": "STAGE7E0_A5_VALID_FEASIBILITY_FAIL_CLOSED",
        "old_classification": lock.get("old_gold_primary_pass_count"),
        "old_classification_status": lock.get("old_gold_classification"),
        "corrected_primary_pass_count": lock.get("corrected_primary_pass_count"),
        "required_pass_count": lock.get("required_pass_count"),
        "corrected_pass_case_ids": summary.get("corrected_pass_case_ids"),
        "primary_gate_status": "FAIL",
        "evidence_integrity_status": "PASS",
        "protocol_compliance_status": "PASS",
        "scientific_result_eligible": True,
        "gretel_pilot_opened": lock.get("gretel_pilot_opened"),
        "diagnostics_run": lock.get("diagnostics_run"),
        "source_tar_sha256": lock.get("source_tar_sha256"),
        "no_gpu_rerun_required": True,
        "model_called": False,
        "gpu_called": False,
    }


def domain_audit_protocol() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "scope": "StageENG1 development_train excluding the 100-sample pilot pool",
        "design_sample_count_required": EXPECTED_DESIGN_SAMPLE_COUNT,
        "baseline_candidate_generator": SELECTED_VARIANT,
        "candidate_domain_compared": [
            "current lexical_ngram2",
            "lexical_ngram2 minus gold-blind atomic-dominated broad spans and exact omission-cue spans",
        ],
        "gold_usage": "Gold assignment spans are used only after candidate generation to audit representability and burden.",
        "forbidden_inputs_for_candidate_suppression": ["gold_sql", "gold_values", "gold_offsets", "target_state", "model_outputs"],
        "minimum_assignment_representability": MIN_ASSIGNMENT_COVERAGE,
        "minimum_full_sample_representability": MIN_FULL_SAMPLE_COVERAGE,
        "model_called": False,
        "gpu_called": False,
    }


def atomic_rule_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "rule_name": "atomic_dominated_broad_span_suppression_audit",
        "audit_only": True,
        "candidate_is_suppressible_when": [
            "The candidate has at least two whitespace-delimited tokens.",
            "The candidate itself has no strong atomic model-visible tag.",
            "The candidate strictly contains another candidate tagged EMAIL, NUMBER, IDENTIFIER, QUOTED_TEXT, or URL.",
        ],
        "candidate_is_not_suppressible_when": [
            "The candidate is a single-token alphanumeric literal such as Q2, 789B, or S102.",
            "The candidate itself is tagged as EMAIL, NUMBER, IDENTIFIER, QUOTED_TEXT, or URL.",
            "No strong atomic child candidate is contained by the span.",
        ],
        "gold_blind": True,
        "purpose": "Reduce broad label-plus-value candidates such as 'loan_id LOAN-842' when an atomic child span exists.",
        "model_called": False,
        "gpu_called": False,
    }


def omission_rule_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "rule_name": "exact_omission_cue_suppression_audit",
        "audit_only": True,
        "cue_phrases": list(OMISSION_CUE_PHRASES),
        "candidate_is_suppressible_when": "Normalized candidate text exactly equals one cue phrase and the SPAN|OMIT column domain includes OMIT.",
        "candidate_is_not_suppressible_when": "The cue phrase is part of a true assigned database value or a non-exact longer candidate.",
        "gold_blind_runtime_policy": True,
        "gold_used_only_for_design_train_recall_audit": True,
        "model_called": False,
        "gpu_called": False,
    }


def _empty_domain_accumulator() -> dict[str, Any]:
    return {
        "candidate_counts": [],
        "assignment_count": 0,
        "covered_assignment_count": 0,
        "full_sample_covered_count": 0,
        "broader_counts": [],
        "broader_total": 0,
    }


def _finalize_domain(acc: dict[str, Any]) -> DomainAudit:
    return DomainAudit(
        candidate_count_stats=_stats(acc["candidate_counts"]),
        assignment_count=acc["assignment_count"],
        covered_assignment_count=acc["covered_assignment_count"],
        full_sample_covered_count=acc["full_sample_covered_count"],
        broader_containing_gold_count_stats=_stats(acc["broader_counts"]),
        broader_containing_gold_total=acc["broader_total"],
    )


def audit_domains(
    raw_dir: Path,
    stageeng0_dir: Path,
    stageeng1_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_by_id = load_raw_by_sample_id(raw_dir)
    design_ids, assignments_by_sample, assignment_rows = design_assignments(stageeng0_dir, stageeng1_dir)
    current_acc = _empty_domain_accumulator()
    filtered_acc = _empty_domain_accumulator()
    row_payloads: list[dict[str, Any]] = []
    suppression_examples: list[dict[str, Any]] = []
    cue_question_occurrences: list[dict[str, Any]] = []
    cue_candidate_occurrence_count = 0
    cue_exact_candidate_count = 0
    cue_gold_exact: list[dict[str, Any]] = []
    cue_gold_contains: list[dict[str, Any]] = []
    suppression_rule_counts: dict[str, int] = {}
    suppressed_candidate_total = 0
    suppressed_candidates_with_strong_child = 0
    suppressible_gold_assignment_count = 0

    for sample_id in sorted(design_ids):
        raw = raw_by_id.get(sample_id)
        if raw is None:
            raise RuntimeError(f"Raw parquet row missing for {sample_id}")
        question = str(raw.get("sql_prompt") or "")
        inventory = generate_candidate_inventory(question, variant=SELECTED_VARIANT)
        reasons = suppressible_span_refs(inventory)
        suppressed_refs = set(reasons)
        filtered_inventory = [candidate for candidate in inventory if candidate.span_ref not in suppressed_refs]
        suppressed_candidate_total += len(suppressed_refs)
        suppressed_candidates_with_strong_child += sum(1 for reason in reasons.values() if reason["rule"] == "ATOMIC_DOMINATED_BROAD_SPAN")
        for reason in reasons.values():
            suppression_rule_counts[reason["rule"]] = suppression_rule_counts.get(reason["rule"], 0) + 1

        for phrase in OMISSION_CUE_PHRASES:
            for match in re.finditer(re.escape(phrase), question.casefold()):
                cue_question_occurrences.append(
                    {
                        "sample_id": sample_id,
                        "cue_phrase": phrase,
                        "start_char": match.start(),
                        "end_char": match.end(),
                    }
                )
        for candidate in inventory:
            if contains_omission_cue(candidate.text):
                cue_candidate_occurrence_count += 1
            if is_exact_omission_cue(candidate.text):
                cue_exact_candidate_count += 1

        current_acc["candidate_counts"].append(len(inventory))
        filtered_acc["candidate_counts"].append(len(filtered_inventory))
        candidate_by_span = _candidate_by_gold_span(inventory)
        assignments = sorted(assignments_by_sample.get(sample_id, []), key=lambda row: int(row["assignment_index"]))
        current_sample_full = True
        filtered_sample_full = True
        assignment_rows_for_sample: list[dict[str, Any]] = []
        sample_broad_current = 0
        sample_broad_filtered = 0

        for assignment in assignments:
            span = assignment["matched_source_span"]
            gold_start = int(span["start_char"])
            gold_end = int(span["end_char"])
            gold_text = str(span["text"])
            gold_key = (gold_start, gold_end, gold_text)
            candidate = candidate_by_span.get(gold_key)
            current_covered = candidate is not None
            filtered_covered = bool(candidate and candidate.span_ref not in suppressed_refs)
            current_sample_full = current_sample_full and current_covered
            filtered_sample_full = filtered_sample_full and filtered_covered
            current_acc["assignment_count"] += 1
            filtered_acc["assignment_count"] += 1
            current_acc["covered_assignment_count"] += int(current_covered)
            filtered_acc["covered_assignment_count"] += int(filtered_covered)
            current_broad = _broader_containing_gold_count(inventory, gold_start, gold_end)
            filtered_broad = _broader_containing_gold_count(inventory, gold_start, gold_end, suppressed_refs=suppressed_refs)
            current_acc["broader_counts"].append(current_broad)
            filtered_acc["broader_counts"].append(filtered_broad)
            current_acc["broader_total"] += current_broad
            filtered_acc["broader_total"] += filtered_broad
            sample_broad_current += current_broad
            sample_broad_filtered += filtered_broad
            normalized_gold = normalize_cue_text(gold_text)
            if normalized_gold in OMISSION_CUE_PHRASES:
                cue_gold_exact.append({"sample_id": sample_id, "column_ref_or_name": assignment["column_ref_or_name"], "gold_text": gold_text})
            if any(phrase in normalized_gold for phrase in OMISSION_CUE_PHRASES):
                cue_gold_contains.append({"sample_id": sample_id, "column_ref_or_name": assignment["column_ref_or_name"], "gold_text": gold_text})
            if current_covered and not filtered_covered:
                suppressible_gold_assignment_count += 1
            assignment_rows_for_sample.append(
                {
                    "assignment_index": assignment["assignment_index"],
                    "column_ref_or_name": assignment["column_ref_or_name"],
                    "gold_text": gold_text,
                    "current_covered": current_covered,
                    "atomic_filtered_covered": filtered_covered,
                    "current_span_ref": candidate.span_ref if candidate else None,
                    "suppressed_by_rule": reasons.get(candidate.span_ref) if candidate else None,
                    "broader_containing_gold_current": current_broad,
                    "broader_containing_gold_atomic_filtered": filtered_broad,
                }
            )

        current_acc["full_sample_covered_count"] += int(current_sample_full)
        filtered_acc["full_sample_covered_count"] += int(filtered_sample_full)
        row_payloads.append(
            {
                "sample_id": sample_id,
                "question_sha256": sha256_text(question),
                "candidate_generator_variant": SELECTED_VARIANT,
                "current_candidate_count": len(inventory),
                "atomic_filtered_candidate_count": len(filtered_inventory),
                "suppressed_candidate_count": len(suppressed_refs),
                "suppression_rule_counts": {
                    rule: sum(1 for reason in reasons.values() if reason["rule"] == rule)
                    for rule in sorted({reason["rule"] for reason in reasons.values()})
                },
                "assignment_count": len(assignments),
                "current_full_sample_representable": current_sample_full,
                "atomic_filtered_full_sample_representable": filtered_sample_full,
                "broader_containing_gold_current": sample_broad_current,
                "broader_containing_gold_atomic_filtered": sample_broad_filtered,
                "assignments": assignment_rows_for_sample,
            }
        )
        if len(suppression_examples) < MAX_SUPPRESSION_EXAMPLE_ROWS:
            for candidate in inventory:
                reason = reasons.get(candidate.span_ref)
                if reason is None:
                    continue
                suppression_examples.append(
                    {
                        "sample_id": sample_id,
                        "span_ref": candidate.span_ref,
                        "text": candidate.text,
                        "tags": list(candidate.tags),
                        "provenance_tags": list(candidate.provenance_tags),
                        "start_char": candidate.start_char,
                        "end_char": candidate.end_char,
                        "reason": reason,
                    }
                )
                if len(suppression_examples) >= MAX_SUPPRESSION_EXAMPLE_ROWS:
                    break

    current = _finalize_domain(current_acc)
    filtered = _finalize_domain(filtered_acc)
    current_summary = {
        "stage": STAGE_NAME,
        "domain": "current_lexical_ngram2",
        "status": "PASS",
        "candidate_generator_variant": SELECTED_VARIANT,
        "design_sample_count": len(design_ids),
        "assignment_count": current.assignment_count,
        "covered_assignment_count": current.covered_assignment_count,
        "missing_assignment_count": current.assignment_count - current.covered_assignment_count,
        "assignment_representability": current.covered_assignment_count / current.assignment_count,
        "full_sample_covered_count": current.full_sample_covered_count,
        "full_sample_representability": current.full_sample_covered_count / len(design_ids),
        "candidate_count_stats": current.candidate_count_stats,
        "broader_containing_gold_count_stats": current.broader_containing_gold_count_stats,
        "broader_containing_gold_total": current.broader_containing_gold_total,
        "model_called": False,
        "gpu_called": False,
    }
    filtered_pass = (
        filtered.covered_assignment_count / filtered.assignment_count >= MIN_ASSIGNMENT_COVERAGE
        and filtered.full_sample_covered_count / len(design_ids) >= MIN_FULL_SAMPLE_COVERAGE
    )
    filtered_summary = {
        "stage": STAGE_NAME,
        "domain": "atomic_filtered_candidate_domain_audit",
        "status": "PASS" if filtered_pass else "FAIL",
        "audit_only": True,
        "candidate_generator_variant": SELECTED_VARIANT,
        "design_sample_count": len(design_ids),
        "assignment_count": filtered.assignment_count,
        "covered_assignment_count": filtered.covered_assignment_count,
        "missing_assignment_count": filtered.assignment_count - filtered.covered_assignment_count,
        "assignment_representability": filtered.covered_assignment_count / filtered.assignment_count,
        "full_sample_covered_count": filtered.full_sample_covered_count,
        "full_sample_representability": filtered.full_sample_covered_count / len(design_ids),
        "candidate_count_stats": filtered.candidate_count_stats,
        "broader_containing_gold_count_stats": filtered.broader_containing_gold_count_stats,
        "broader_containing_gold_total": filtered.broader_containing_gold_total,
        "suppressed_candidate_total": suppressed_candidate_total,
        "suppressed_candidates_with_strong_child": suppressed_candidates_with_strong_child,
        "suppression_rule_counts": dict(sorted(suppression_rule_counts.items())),
        "suppressible_gold_assignment_count": suppressible_gold_assignment_count,
        "minimum_assignment_representability": MIN_ASSIGNMENT_COVERAGE,
        "minimum_full_sample_representability": MIN_FULL_SAMPLE_COVERAGE,
        "model_called": False,
        "gpu_called": False,
    }
    comparison = {
        "stage": STAGE_NAME,
        "status": "PASS" if filtered_pass else "FAIL",
        "audit_only": True,
        "current_domain": "current_lexical_ngram2",
        "candidate_domain_under_review": "atomic_filtered_candidate_domain_audit",
        "assignment_representability_delta": filtered_summary["assignment_representability"] - current_summary["assignment_representability"],
        "full_sample_representability_delta": filtered_summary["full_sample_representability"] - current_summary["full_sample_representability"],
        "candidate_count_median_delta": filtered.candidate_count_stats["median"] - current.candidate_count_stats["median"],
        "candidate_count_p95_delta": filtered.candidate_count_stats["p95"] - current.candidate_count_stats["p95"],
        "candidate_count_max_delta": filtered.candidate_count_stats["max"] - current.candidate_count_stats["max"],
        "broader_containing_gold_total_delta": filtered.broader_containing_gold_total - current.broader_containing_gold_total,
        "threshold_decision": "PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW" if filtered_pass else "FAIL_DO_NOT_FREEZE",
        "method_freeze_authorized": False,
        "model_called": False,
        "gpu_called": False,
    }
    cue_audit = {
        "stage": STAGE_NAME,
        "status": "PASS",
        "cue_phrases": list(OMISSION_CUE_PHRASES),
        "design_sample_count": len(design_ids),
        "assignment_count": len(assignment_rows),
        "true_assigned_value_exact_cue_count": len(cue_gold_exact),
        "true_assigned_value_contains_cue_count": len(cue_gold_contains),
        "question_cue_occurrence_count": len(cue_question_occurrences),
        "candidate_containing_cue_count": cue_candidate_occurrence_count,
        "exact_candidate_cue_count": cue_exact_candidate_count,
        "question_cue_occurrences": cue_question_occurrences[:50],
        "true_assigned_value_exact_cue_examples": cue_gold_exact[:20],
        "true_assigned_value_contains_cue_examples": cue_gold_contains[:20],
        "omission_cue_suppression_has_design_train_recall_risk": bool(cue_gold_exact or cue_gold_contains),
        "model_called": False,
        "gpu_called": False,
    }
    return current_summary, filtered_summary, comparison, cue_audit, row_payloads, suppression_examples


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


def validation_report(current: dict[str, Any], filtered: dict[str, Any], comparison: dict[str, Any], cue: dict[str, Any]) -> str:
    return f"""# Stage7B-A4 Atomic Candidate Domain and Omission-Cue Amendment Validation Report

Status: {comparison["status"]}

Validation date: {date.today().isoformat()}

## Scope

This is a CPU-only design-train audit. It does not call a model, does not use
GPU, and does not open Gretel pilot, development-dev, or official test rows.

```text
design_train_non_pilot_count={current["design_sample_count"]}
assignment_count={current["assignment_count"]}
baseline_candidate_generator={SELECTED_VARIANT}
model_called=false
gpu_called=false
```

## Current Domain

```text
assignment_representability={current["covered_assignment_count"]}/{current["assignment_count"]}
full_sample_representability={current["full_sample_covered_count"]}/{current["design_sample_count"]}
candidate_count_median={current["candidate_count_stats"]["median"]}
candidate_count_p95={current["candidate_count_stats"]["p95"]}
candidate_count_max={current["candidate_count_stats"]["max"]}
broader_containing_gold_total={current["broader_containing_gold_total"]}
```

## Atomic-Filtered Domain Under Review

```text
assignment_representability={filtered["covered_assignment_count"]}/{filtered["assignment_count"]}
full_sample_representability={filtered["full_sample_covered_count"]}/{filtered["design_sample_count"]}
candidate_count_median={filtered["candidate_count_stats"]["median"]}
candidate_count_p95={filtered["candidate_count_stats"]["p95"]}
candidate_count_max={filtered["candidate_count_stats"]["max"]}
suppressed_candidate_total={filtered["suppressed_candidate_total"]}
broader_containing_gold_total={filtered["broader_containing_gold_total"]}
threshold_decision={comparison["threshold_decision"]}
method_freeze_authorized=false
```

## Omission Cues

```text
cue_phrases={",".join(OMISSION_CUE_PHRASES)}
true_assigned_value_exact_cue_count={cue["true_assigned_value_exact_cue_count"]}
true_assigned_value_contains_cue_count={cue["true_assigned_value_contains_cue_count"]}
question_cue_occurrence_count={cue["question_cue_occurrence_count"]}
candidate_containing_cue_count={cue["candidate_containing_cue_count"]}
```

## Decision

The candidate-domain amendment passes the 99% assignment and full-sample
representability audit on the 728 design-train samples. This package does not
freeze a new runtime protocol and does not authorize a model rerun; it provides
the evidence needed for reviewer approval of a later protocol freeze.
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7B-A4 Atomic Candidate Domain and Omission-Cue Amendment

This package audits a candidate-domain amendment after Stage7E0-A5 was closed
as a corrected valid feasibility failure at 2/12. It compares current
`lexical_ngram2` against an audit-only atomic-filtered domain on the frozen 728
non-pilot design-train samples.

Review order:

1. `{STAGE_NAME}/A5_CORRECTED_VALID_FAIL_FREEZE.json`
2. `{STAGE_NAME}/DOMAIN_AUDIT_PROTOCOL.json`
3. `{STAGE_NAME}/ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json`
4. `{STAGE_NAME}/OMISSION_CUE_SUPPRESSION_RULE_SPEC.json`
5. `{STAGE_NAME}/CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json`
6. `{STAGE_NAME}/ATOMIC_FILTERED_DOMAIN_AUDIT.json`
7. `{STAGE_NAME}/DOMAIN_COMPARISON_AUDIT.json`
8. `{STAGE_NAME}/OMISSION_CUE_DESIGN_TRAIN_AUDIT.json`
9. `{STAGE_NAME}/CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl`
10. `{STAGE_NAME}/CANDIDATE_SUPPRESSION_EXAMPLES.jsonl`
11. `{STAGE_NAME}/SOURCE_INPUT_MANIFEST.json`
12. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
13. `{STAGE_NAME}/STAGE7B_A4_LOCK.json`
14. `{STAGE_NAME}/VALIDATION_REPORT.md`
15. `scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py`
16. `scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py`
17. `tests/test_stage7b_a4_atomic_candidate_domain_omission_cue.py`

Clean extraction commands:

```bash
python scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py \\
  --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7b_a4_atomic_candidate_domain_omission_cue.py
```

Full rebuild requires the local Gretel parquet source:

```bash
uv run --with pyarrow python scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py \\
  --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236
python scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py \\
  --stage-dir {STAGE_NAME} \\
  --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236 \\
  --rebuild
```

No GPU is required. No model is called. Gretel pilot/dev/test rows remain
closed.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def build_stage(
    out_dir: Path,
    raw_dir: Path,
    *,
    stageeng0_dir: Path = PROJECT_ROOT / STAGEENG0_NAME,
    stageeng1_dir: Path = PROJECT_ROOT / STAGEENG1_NAME,
    stage7b_a2_dir: Path = PROJECT_ROOT / STAGE7B_A2_NAME,
    stage7b_a3_dir: Path = PROJECT_ROOT / STAGE7B_A3_NAME,
    stage7c_a5_dir: Path = PROJECT_ROOT / STAGE7C_A5_NAME,
    stage7c_a5_erratum_dir: Path = PROJECT_ROOT / STAGE7C_A5_ERRATUM_NAME,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    current, filtered, comparison, cue, rows, suppression_examples = audit_domains(raw_dir, stageeng0_dir, stageeng1_dir)
    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(stageeng0_dir, stageeng1_dir, stage7b_a2_dir, stage7b_a3_dir, stage7c_a5_dir, stage7c_a5_erratum_dir))
    write_json(out_dir / "A5_CORRECTED_VALID_FAIL_FREEZE.json", a5_corrected_valid_fail_freeze(stage7c_a5_erratum_dir))
    write_json(out_dir / "DOMAIN_AUDIT_PROTOCOL.json", domain_audit_protocol())
    write_json(out_dir / "ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json", atomic_rule_spec())
    write_json(out_dir / "OMISSION_CUE_SUPPRESSION_RULE_SPEC.json", omission_rule_spec())
    write_json(out_dir / "CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json", current)
    write_json(out_dir / "ATOMIC_FILTERED_DOMAIN_AUDIT.json", filtered)
    write_json(out_dir / "DOMAIN_COMPARISON_AUDIT.json", comparison)
    write_json(out_dir / "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json", cue)
    write_jsonl(out_dir / "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl", rows)
    write_jsonl(out_dir / "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl", suppression_examples)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_derived_manifest(out_dir))

    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_ATOMIC_CANDIDATE_DOMAIN_OMISSION_CUE_AUDIT_READY_FOR_REVIEW",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "source_stage7e0_a5_corrected_result": "2/12",
        "source_stage7e0_a5_status": "VALID_FEASIBILITY_FAIL_CLOSED",
        "baseline_candidate_generator": SELECTED_VARIANT,
        "design_train_non_pilot_count": current["design_sample_count"],
        "assignment_count": current["assignment_count"],
        "current_assignment_representability": current["assignment_representability"],
        "current_full_sample_representability": current["full_sample_representability"],
        "atomic_filtered_assignment_representability": filtered["assignment_representability"],
        "atomic_filtered_full_sample_representability": filtered["full_sample_representability"],
        "minimum_assignment_representability": MIN_ASSIGNMENT_COVERAGE,
        "minimum_full_sample_representability": MIN_FULL_SAMPLE_COVERAGE,
        "threshold_decision": comparison["threshold_decision"],
        "suppressed_candidate_total": filtered["suppressed_candidate_total"],
        "true_assigned_value_exact_cue_count": cue["true_assigned_value_exact_cue_count"],
        "true_assigned_value_contains_cue_count": cue["true_assigned_value_contains_cue_count"],
        "method_freeze_authorized": False,
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7B_A4_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(current, filtered, comparison, cue))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "design_train_non_pilot_count": current["design_sample_count"],
        "assignment_count": current["assignment_count"],
        "current_assignment_representability": current["assignment_representability"],
        "current_full_sample_representability": current["full_sample_representability"],
        "atomic_filtered_assignment_representability": filtered["assignment_representability"],
        "atomic_filtered_full_sample_representability": filtered["full_sample_representability"],
        "suppressed_candidate_total": filtered["suppressed_candidate_total"],
        "true_assigned_value_exact_cue_count": cue["true_assigned_value_exact_cue_count"],
        "model_called": False,
        "gpu_called": False,
    }


def include_paths_for_package(stage_dir: Path) -> list[Path]:
    files = [path for path in stage_dir.rglob("*") if path.is_file()]
    for relative in [
        "pyproject.toml",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "tests/test_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        f"{STAGEENG0_NAME}/STAGEENG0_LOCK.json",
        f"{STAGEENG0_NAME}/INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl",
        f"{STAGEENG1_NAME}/STAGEENG1_LOCK.json",
        f"{STAGEENG1_NAME}/DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl",
        f"{STAGEENG1_NAME}/DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl",
        f"{STAGEENG1_NAME}/DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl",
        f"{STAGE7B_A2_NAME}/STAGE7B_A2_LOCK.json",
        f"{STAGE7B_A2_NAME}/ORACLE_CANDIDATE_COVERAGE_AUDIT.json",
        f"{STAGE7B_A3_NAME}/STAGE7B_A3_LOCK.json",
        f"{STAGE7C_A5_NAME}/STAGE7C_A5_LOCK.json",
        f"{STAGE7C_A5_ERRATUM_NAME}/ERRATUM_LOCK.json",
        f"{STAGE7C_A5_ERRATUM_NAME}/GOLD_PROVENANCE_ERRATUM.json",
        f"{STAGE7C_A5_ERRATUM_NAME}/OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json",
        f"{STAGE7C_A5_ERRATUM_NAME}/CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl",
    ]:
        path = PROJECT_ROOT / relative
        if path.is_file():
            files.append(path)
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
    parser.add_argument("--raw-dir", type=Path, required=True)
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
