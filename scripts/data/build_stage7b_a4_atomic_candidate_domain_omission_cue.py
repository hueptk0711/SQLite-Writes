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
    parse_schema_tables,
)


STAGE_NAME = "Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT"
PATCH_NAME = "PATCH2"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260902.zip"
STAGEENG0_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
STAGE7B_A2_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
STAGE7C_A5_NAME = "Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE"
STAGE7C_A5_ERRATUM_NAME = "Stage7C_A5_PRIMARY_GOLD_PROVENANCE_ERRATUM_PATCH0"
STAGE7E0_A5_NAME = "Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT"
STRONG_ATOMIC_TAGS = {"EMAIL", "NUMBER", "IDENTIFIER", "QUOTED_TEXT", "URL"}
SCHEMA_ALIAS_STOPWORDS = {
    "c",
    "code",
    "count",
    "date",
    "flag",
    "g",
    "id",
    "kg",
    "name",
    "no",
    "number",
    "pct",
    "percent",
    "percentage",
    "text",
    "time",
    "type",
    "value",
}
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
    "SCHEMA_LABEL_ALIAS_SPEC.json",
    "OMISSION_CUE_SUPPRESSION_RULE_SPEC.json",
    "CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json",
    "PATCH0_GENERIC_ATOMIC_DOMAIN_AUDIT.json",
    "SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json",
    "SCHEMA_LABEL_ALIAS_DOMAIN_AUDIT.json",
    "ATOMIC_FILTERED_DOMAIN_AUDIT.json",
    "DOMAIN_COMPARISON_AUDIT.json",
    "FALSE_SUPPRESSION_AUDIT.json",
    "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json",
    "SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json",
    "A5_OBSERVED_ERROR_COUNTERFACTUAL_DOMAIN_AUDIT.json",
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


def normalize_label_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", text.casefold()).strip(" \t\r\n\"'()[]{}<>.,;:!?"))


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
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?", text):
        tags.add("DATETIME")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        tags.add("DATE")
    elif re.fullmatch(r"\d+(?:st|nd|rd|th)\s+[A-Za-z][A-Za-z ]*", text):
        tags.add("ORDINAL_PHRASE")
    elif re.fullmatch(r"[A-Za-z]+'s\s+[A-Za-z][A-Za-z ]*", text):
        tags.add("COMPOUND_LITERAL")
    if re.fullmatch(r"0x[0-9A-Fa-f]+", text):
        tags.add("IDENTIFIER")
    elif re.fullmatch(r"[A-Za-z]+[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*", text):
        tags.add("IDENTIFIER")
    return tags


def complete_compound_literal(candidate: CandidateSpan) -> bool:
    return bool(effective_atomic_tags(candidate) - STRONG_ATOMIC_TAGS)


def visible_schema_labels(sql_context: str) -> set[str]:
    labels: set[str] = set()
    for table_name, columns in parse_schema_tables(sql_context).items():
        labels.add(normalize_label_text(table_name))
        for column in columns:
            labels.add(normalize_label_text(column.column_name))
    return {label for label in labels if label}


def _label_tokens(label: str) -> list[str]:
    return [token for token in normalize_label_text(label).split() if token]


def schema_label_alias_index(schema_labels: set[str]) -> dict[str, list[str]]:
    """Build conservative gold-blind schema aliases from visible labels."""

    aliases: dict[str, set[str]] = {}
    for label in schema_labels:
        normalized = normalize_label_text(label)
        if not normalized:
            continue
        aliases.setdefault(normalized, set()).add(normalized)
        tokens = _label_tokens(normalized)
        if len(tokens) < 2:
            continue
        terminal = tokens[-1]
        if len(terminal) >= 3 and terminal not in SCHEMA_ALIAS_STOPWORDS:
            aliases.setdefault(terminal, set()).add(normalized)
        for index, token in enumerate(tokens[:-1]):
            next_token = tokens[index + 1]
            if len(token) >= 3 and token not in SCHEMA_ALIAS_STOPWORDS and next_token in SCHEMA_ALIAS_STOPWORDS:
                aliases.setdefault(token, set()).add(normalized)
    return {alias: sorted(sources) for alias, sources in sorted(aliases.items())}


def visible_schema_aliases(sql_context: str) -> dict[str, list[str]]:
    return schema_label_alias_index(visible_schema_labels(sql_context))


def schema_inventory_aliases(schema_inventory: dict[str, Any]) -> dict[str, list[str]]:
    labels: set[str] = set()
    for table in schema_inventory.get("tables", []):
        labels.add(normalize_label_text(str(table.get("table_name") or "")))
    for column in schema_inventory.get("columns", []):
        labels.add(normalize_label_text(str(column.get("column_name") or "")))
    return schema_label_alias_index({label for label in labels if label})


def residual_around_child(parent: CandidateSpan, child: CandidateSpan) -> str:
    parent_text = parent.text
    child_start = child.start_char - parent.start_char
    child_end = child.end_char - parent.start_char
    return f"{parent_text[:child_start]} {parent_text[child_end:]}"


def generic_atomic_dominance_reason(candidate: CandidateSpan, inventory: list[CandidateSpan]) -> dict[str, Any] | None:
    """Return the PATCH0 broad atomic-child suppression reason."""

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


def schema_label_aware_dominance_reason(
    candidate: CandidateSpan,
    inventory: list[CandidateSpan],
    schema_labels: set[str],
) -> dict[str, Any] | None:
    """Suppress only when the non-child residual is a visible schema label."""

    if len(candidate.text.split()) < 2:
        return None
    if effective_atomic_tags(candidate) or complete_compound_literal(candidate):
        return None
    generic_reason = generic_atomic_dominance_reason(candidate, inventory)
    if generic_reason is None:
        return None
    child_ref = str(generic_reason["dominant_child_span_ref"])
    child = next(item for item in inventory if item.span_ref == child_ref)
    residual = normalize_label_text(residual_around_child(candidate, child))
    if residual not in schema_labels:
        return None
    return {
        "rule": "SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE",
        "dominant_child_span_ref": child.span_ref,
        "dominant_child_text": child.text,
        "dominant_child_tags": sorted(effective_atomic_tags(child)),
        "schema_label_residual": residual,
        "candidate_token_count": len(candidate.text.split()),
    }


def schema_label_alias_aware_dominance_reason(
    candidate: CandidateSpan,
    inventory: list[CandidateSpan],
    schema_aliases: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Suppress label/value spans when the residual matches a schema label or alias."""

    if len(candidate.text.split()) < 2:
        return None
    if effective_atomic_tags(candidate) or complete_compound_literal(candidate):
        return None
    generic_reason = generic_atomic_dominance_reason(candidate, inventory)
    if generic_reason is None:
        return None
    child_ref = str(generic_reason["dominant_child_span_ref"])
    child = next(item for item in inventory if item.span_ref == child_ref)
    residual = normalize_label_text(residual_around_child(candidate, child))
    source_labels = schema_aliases.get(residual)
    if not source_labels:
        return None
    rule = "SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE" if residual in source_labels else "SCHEMA_LABEL_ALIAS_ATOMIC_DOMINANCE"
    return {
        "rule": rule,
        "dominant_child_span_ref": child.span_ref,
        "dominant_child_text": child.text,
        "dominant_child_tags": sorted(effective_atomic_tags(child)),
        "schema_label_residual": residual,
        "schema_alias_residual": residual,
        "schema_alias_source_labels": source_labels,
        "candidate_token_count": len(candidate.text.split()),
    }


def detect_omission_constructions(question: str, schema_labels: set[str] | dict[str, list[str]]) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    lowered = question.casefold()
    if isinstance(schema_labels, dict):
        label_items = [(label, schema_labels[label]) for label in schema_labels]
    else:
        label_items = [(label, [label]) for label in schema_labels]
    for label, source_labels in sorted(label_items, key=lambda item: len(item[0]), reverse=True):
        if not label:
            continue
        label_pattern = re.escape(label).replace(r"\ ", r"[\s_\-]+")
        for phrase in OMISSION_CUE_PHRASES:
            phrase_pattern = re.escape(phrase).replace(r"\ ", r"\s+")
            patterns = [
                rf"\b(?P<label>{label_pattern})\b\s+(?:is\s+|was\s+)?(?P<cue>{phrase_pattern})\b",
                rf"\bleave\s+(?P<label>{label_pattern})\s+(?P<cue>{phrase_pattern})\b",
                rf"\bdo\s+not\s+set\s+(?P<label>{label_pattern})\b",
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, lowered):
                    detections.append(
                        {
                            "label": label,
                            "cue_phrase": phrase,
                            "schema_alias_source_labels": list(source_labels),
                            "start_char": match.start(),
                            "end_char": match.end(),
                            "text": question[match.start() : match.end()],
                        }
                    )
    return detections


def context_aware_omission_reason(
    candidate: CandidateSpan,
    omission_detections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_candidate = normalize_cue_text(candidate.text)
    exact_cue = normalized_candidate in OMISSION_CUE_PHRASES
    exact_construction = False
    if not exact_cue:
        exact_construction = any(normalized_candidate == normalize_cue_text(str(detection["text"])) for detection in omission_detections)
    if not exact_cue and not exact_construction:
        return None
    for detection in omission_detections:
        cue = str(detection["cue_phrase"])
        if exact_cue and cue != normalized_candidate:
            continue
        if int(detection["start_char"]) <= candidate.start_char and candidate.end_char <= int(detection["end_char"]):
            return {
                "rule": "CONTEXT_AWARE_OMISSION_CUE",
                "cue_phrase": cue,
                "schema_label": detection["label"],
                "schema_alias_source_labels": detection.get("schema_alias_source_labels", [detection["label"]]),
                "construction_text": detection["text"],
                "suppressed_span_kind": "exact_cue" if exact_cue else "full_omission_construction",
            }
    return None


def suppression_reason(
    candidate: CandidateSpan,
    inventory: list[CandidateSpan],
    schema_aliases: dict[str, list[str]],
    omission_detections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    omission_reason = context_aware_omission_reason(candidate, omission_detections)
    if omission_reason is not None:
        return omission_reason
    return schema_label_alias_aware_dominance_reason(candidate, inventory, schema_aliases)


def suppressible_span_refs(
    inventory: list[CandidateSpan],
    schema_labels: set[str] | dict[str, list[str]] | None = None,
    omission_detections: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    schema_aliases = schema_labels if isinstance(schema_labels, dict) else schema_label_alias_index(schema_labels or set())
    omission_detections = omission_detections or []
    reasons: dict[str, dict[str, Any]] = {}
    for candidate in inventory:
        reason = suppression_reason(candidate, inventory, schema_aliases, omission_detections)
        if reason is not None:
            reasons[candidate.span_ref] = reason
    return reasons


def patch1_suppressible_span_refs(
    inventory: list[CandidateSpan],
    schema_labels: set[str],
    omission_detections: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    reasons: dict[str, dict[str, Any]] = {}
    for candidate in inventory:
        omission_reason = context_aware_omission_reason(candidate, omission_detections)
        if omission_reason is not None:
            reasons[candidate.span_ref] = omission_reason
            continue
        reason = schema_label_aware_dominance_reason(candidate, inventory, schema_labels)
        if reason is not None:
            reasons[candidate.span_ref] = reason
    return reasons


def generic_suppressible_span_refs(inventory: list[CandidateSpan]) -> dict[str, dict[str, Any]]:
    reasons: dict[str, dict[str, Any]] = {}
    for candidate in inventory:
        if is_exact_omission_cue(candidate.text):
            reasons[candidate.span_ref] = {"rule": "EXACT_OMISSION_CUE", "cue_text": normalize_cue_text(candidate.text)}
            continue
        reason = patch0_original_generic_atomic_dominance_reason(candidate, inventory)
        if reason is not None:
            reasons[candidate.span_ref] = reason
    return reasons


def patch0_original_generic_atomic_dominance_reason(candidate: CandidateSpan, inventory: list[CandidateSpan]) -> dict[str, Any] | None:
    if len(candidate.text.split()) < 2:
        return None
    if STRONG_ATOMIC_TAGS.intersection(candidate.tags):
        return None
    atomic_children = [
        child
        for child in inventory
        if STRONG_ATOMIC_TAGS.intersection(child.tags) and _strictly_contains(candidate, child)
    ]
    if not atomic_children:
        return None
    tag_priority = {"EMAIL": 5, "URL": 5, "IDENTIFIER": 4, "QUOTED_TEXT": 3, "NUMBER": 2}
    best_child = max(
        atomic_children,
        key=lambda item: (
            max(tag_priority.get(tag, 0) for tag in item.tags),
            item.start_char,
            item.end_char - item.start_char,
            -item.end_char,
        ),
    )
    return {
        "rule": "ATOMIC_DOMINATED_BROAD_SPAN",
        "dominant_child_span_ref": best_child.span_ref,
        "dominant_child_text": best_child.text,
        "dominant_child_tags": list(best_child.tags),
        "candidate_token_count": len(candidate.text.split()),
    }


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
    stage7e0_a5_dir: Path,
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
        (STAGE7E0_A5_NAME, stage7e0_a5_dir, "SERVER_RESULT_CLASSIFICATION_PATCH5.json"),
        (STAGE7E0_A5_NAME, stage7e0_a5_dir, "uet_p4/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901/raw_primary_phase_o_generations.jsonl"),
        (STAGE7E0_A5_NAME, stage7e0_a5_dir, "uet_p4/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901/primary_case_results.jsonl"),
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
            "PATCH0 generic atomic dominated broad spans",
            "PATCH1 exact schema-label-aware atomic dominated broad spans",
            "PATCH2 conservative schema-label-alias-aware atomic dominance plus context-aware omission cues",
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
        "rule_name": "schema_label_alias_aware_atomic_dominated_broad_span_suppression_audit",
        "audit_only": True,
        "candidate_is_suppressible_when": [
            "The candidate has at least two whitespace-delimited tokens.",
            "The candidate itself is not a recognized complete atomic, datetime, ordinal, possessive, or compound literal.",
            "The candidate strictly contains another candidate tagged EMAIL, NUMBER, IDENTIFIER, QUOTED_TEXT, or URL.",
            "The text outside the dominant child normalizes to a model-visible schema table/column label or a conservative schema-derived alias.",
        ],
        "candidate_is_not_suppressible_when": [
            "The candidate is a single-token alphanumeric literal such as Q2, 789B, or S102.",
            "The candidate itself is tagged as EMAIL, NUMBER, IDENTIFIER, QUOTED_TEXT, or URL.",
            "The candidate is a complete datetime, ordinal phrase, or possessive/compound literal.",
            "The residual outside the atomic child is neither a visible schema label nor a conservative schema-derived alias.",
            "No strong atomic child candidate is contained by the span.",
        ],
        "gold_blind": True,
        "uses_model_visible_schema": True,
        "schema_alias_stopwords": sorted(SCHEMA_ALIAS_STOPWORDS),
        "purpose": "Reduce broad label-plus-value candidates such as 'loan_id LOAN-842' and 'card CARD-190' only when the label residual matches visible schema or a locked schema-derived alias.",
        "model_called": False,
        "gpu_called": False,
    }


def schema_label_alias_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "rule_name": "conservative_schema_label_alias_generation",
        "audit_only": True,
        "alias_sources": [
            "full normalized model-visible table names",
            "full normalized model-visible column names",
            "terminal non-generic tokens from multi-token labels",
            "non-generic tokens immediately followed by a generic schema suffix token",
        ],
        "normalization": "casefold plus underscore/dash/dot to space, then whitespace collapse",
        "generic_token_stoplist": sorted(SCHEMA_ALIAS_STOPWORDS),
        "examples": {
            "borrower_card": ["borrower card", "card"],
            "bin_code": ["bin code", "bin"],
            "temperature_c": ["temperature c", "temperature"],
            "humidity_pct": ["humidity pct", "humidity"],
            "mass_kg": ["mass kg", "mass"],
            "accessibility_note": ["accessibility note", "note"],
        },
        "forbidden": [
            "Do not use gold SQL, gold values, target states, or model outputs to construct aliases.",
            "Do not expose generic tokens such as id, code, name, value, number, no, or text as standalone aliases.",
        ],
        "gold_blind": True,
        "model_called": False,
        "gpu_called": False,
    }


def omission_rule_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "rule_name": "exact_omission_cue_suppression_audit",
        "audit_only": True,
        "cue_phrases": list(OMISSION_CUE_PHRASES),
        "candidate_is_suppressible_when": "Normalized candidate text exactly equals one cue phrase inside a detected schema-label or schema-alias omission construction.",
        "candidate_is_not_suppressible_when": "The cue phrase appears as a quoted/legitimate value or outside a schema-label/schema-alias omission construction.",
        "gold_blind_runtime_policy": True,
        "gold_used_only_for_design_train_recall_audit": True,
        "model_called": False,
        "gpu_called": False,
    }


def synthetic_omission_cue_safety_audit() -> dict[str, Any]:
    fixtures = [
        {"case_id": "positive_phone_not_provided", "question": "Insert contact Bob. phone not provided.", "schema_labels": {"phone"}, "expected_detected": True},
        {"case_id": "positive_region_omitted", "question": "Add city row. region omitted.", "schema_labels": {"region"}, "expected_detected": True},
        {"case_id": "positive_note_missing", "question": "Create task row. note missing.", "schema_labels": {"note"}, "expected_detected": True},
        {"case_id": "positive_address_left_empty", "question": "Register customer. address left empty.", "schema_labels": {"address"}, "expected_detected": True},
        {"case_id": "negative_status_missing_literal", "question": 'Insert status "missing".', "schema_labels": {"status"}, "expected_detected": False},
        {"case_id": "negative_title_missing_link_literal", "question": 'Insert title "Missing Link".', "schema_labels": {"title"}, "expected_detected": False},
        {"case_id": "negative_album_blank_space_literal", "question": 'Insert album "Blank Space".', "schema_labels": {"album"}, "expected_detected": False},
        {"case_id": "negative_state_absent_literal", "question": 'Insert state "Absent".', "schema_labels": {"state"}, "expected_detected": False},
    ]
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for fixture in fixtures:
        question = fixture["question"]
        schema_labels = set(fixture["schema_labels"])
        detections = detect_omission_constructions(question, schema_labels)
        inventory = generate_candidate_inventory(question, variant=SELECTED_VARIANT)
        reasons = suppressible_span_refs(inventory, schema_labels, detections)
        cue_suppressions = [
            {
                "span_ref": candidate.span_ref,
                "text": candidate.text,
                "reason": reasons[candidate.span_ref],
            }
            for candidate in inventory
            if candidate.span_ref in reasons and reasons[candidate.span_ref]["rule"] == "CONTEXT_AWARE_OMISSION_CUE"
        ]
        detected = bool(detections or cue_suppressions)
        expected = bool(fixture["expected_detected"])
        if detected != expected:
            failures.append(str(fixture["case_id"]))
        rows.append(
            {
                "case_id": fixture["case_id"],
                "question": question,
                "schema_labels": sorted(schema_labels),
                "expected_detected": expected,
                "detected": detected,
                "omission_constructions": detections,
                "context_aware_cue_suppressions": cue_suppressions,
            }
        )
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "fixture_count": len(fixtures),
        "positive_fixture_count": sum(1 for fixture in fixtures if fixture["expected_detected"]),
        "negative_literal_fixture_count": sum(1 for fixture in fixtures if not fixture["expected_detected"]),
        "failures": failures,
        "fixtures": rows,
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
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_by_id = load_raw_by_sample_id(raw_dir)
    design_ids, assignments_by_sample, assignment_rows = design_assignments(stageeng0_dir, stageeng1_dir)
    current_acc = _empty_domain_accumulator()
    patch0_acc = _empty_domain_accumulator()
    patch1_acc = _empty_domain_accumulator()
    patch2_acc = _empty_domain_accumulator()
    row_payloads: list[dict[str, Any]] = []
    suppression_examples: list[dict[str, Any]] = []
    cue_question_occurrences: list[dict[str, Any]] = []
    cue_candidate_occurrence_count = 0
    cue_exact_candidate_count = 0
    cue_gold_exact: list[dict[str, Any]] = []
    cue_gold_contains: list[dict[str, Any]] = []
    suppression_rule_counts: dict[str, int] = {}
    patch0_suppression_rule_counts: dict[str, int] = {}
    patch1_suppression_rule_counts: dict[str, int] = {}
    suppressed_candidate_total = 0
    patch1_suppressed_candidate_total = 0
    patch0_suppressed_candidate_total = 0
    suppressed_candidates_with_strong_child = 0
    alias_suppressed_candidates_with_strong_child = 0
    suppressible_gold_assignment_count = 0
    false_suppression_rows: list[dict[str, Any]] = []

    for sample_id in sorted(design_ids):
        raw = raw_by_id.get(sample_id)
        if raw is None:
            raise RuntimeError(f"Raw parquet row missing for {sample_id}")
        question = str(raw.get("sql_prompt") or "")
        inventory = generate_candidate_inventory(question, variant=SELECTED_VARIANT)
        schema_labels = visible_schema_labels(str(raw.get("sql_context") or ""))
        schema_aliases = schema_label_alias_index(schema_labels)
        patch1_omission_detections = detect_omission_constructions(question, schema_labels)
        omission_detections = detect_omission_constructions(question, schema_aliases)
        patch0_reasons = generic_suppressible_span_refs(inventory)
        patch1_reasons = patch1_suppressible_span_refs(inventory, schema_labels, patch1_omission_detections)
        reasons = suppressible_span_refs(inventory, schema_labels, omission_detections)
        patch0_suppressed_refs = set(patch0_reasons)
        patch1_suppressed_refs = set(patch1_reasons)
        suppressed_refs = set(reasons)
        patch0_filtered_inventory = [candidate for candidate in inventory if candidate.span_ref not in patch0_suppressed_refs]
        patch1_filtered_inventory = [candidate for candidate in inventory if candidate.span_ref not in patch1_suppressed_refs]
        filtered_inventory = [candidate for candidate in inventory if candidate.span_ref not in suppressed_refs]
        patch0_suppressed_candidate_total += len(patch0_suppressed_refs)
        patch1_suppressed_candidate_total += len(patch1_suppressed_refs)
        suppressed_candidate_total += len(suppressed_refs)
        suppressed_candidates_with_strong_child += sum(1 for reason in patch1_reasons.values() if reason["rule"] == "SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE")
        alias_suppressed_candidates_with_strong_child += sum(1 for reason in reasons.values() if reason["rule"] in {"SCHEMA_LABEL_AWARE_ATOMIC_DOMINANCE", "SCHEMA_LABEL_ALIAS_ATOMIC_DOMINANCE"})
        for reason in patch0_reasons.values():
            patch0_suppression_rule_counts[reason["rule"]] = patch0_suppression_rule_counts.get(reason["rule"], 0) + 1
        for reason in patch1_reasons.values():
            patch1_suppression_rule_counts[reason["rule"]] = patch1_suppression_rule_counts.get(reason["rule"], 0) + 1
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
        patch0_acc["candidate_counts"].append(len(patch0_filtered_inventory))
        patch1_acc["candidate_counts"].append(len(patch1_filtered_inventory))
        patch2_acc["candidate_counts"].append(len(filtered_inventory))
        candidate_by_span = _candidate_by_gold_span(inventory)
        assignments = sorted(assignments_by_sample.get(sample_id, []), key=lambda row: int(row["assignment_index"]))
        current_sample_full = True
        patch0_sample_full = True
        patch1_sample_full = True
        filtered_sample_full = True
        assignment_rows_for_sample: list[dict[str, Any]] = []
        sample_broad_current = 0
        sample_broad_patch0 = 0
        sample_broad_patch1 = 0
        sample_broad_filtered = 0

        for assignment in assignments:
            span = assignment["matched_source_span"]
            gold_start = int(span["start_char"])
            gold_end = int(span["end_char"])
            gold_text = str(span["text"])
            gold_key = (gold_start, gold_end, gold_text)
            candidate = candidate_by_span.get(gold_key)
            current_covered = candidate is not None
            patch0_covered = bool(candidate and candidate.span_ref not in patch0_suppressed_refs)
            patch1_covered = bool(candidate and candidate.span_ref not in patch1_suppressed_refs)
            filtered_covered = bool(candidate and candidate.span_ref not in suppressed_refs)
            current_sample_full = current_sample_full and current_covered
            patch0_sample_full = patch0_sample_full and patch0_covered
            patch1_sample_full = patch1_sample_full and patch1_covered
            filtered_sample_full = filtered_sample_full and filtered_covered
            current_acc["assignment_count"] += 1
            patch0_acc["assignment_count"] += 1
            patch1_acc["assignment_count"] += 1
            patch2_acc["assignment_count"] += 1
            current_acc["covered_assignment_count"] += int(current_covered)
            patch0_acc["covered_assignment_count"] += int(patch0_covered)
            patch1_acc["covered_assignment_count"] += int(patch1_covered)
            patch2_acc["covered_assignment_count"] += int(filtered_covered)
            current_broad = _broader_containing_gold_count(inventory, gold_start, gold_end)
            patch0_broad = _broader_containing_gold_count(inventory, gold_start, gold_end, suppressed_refs=patch0_suppressed_refs)
            patch1_broad = _broader_containing_gold_count(inventory, gold_start, gold_end, suppressed_refs=patch1_suppressed_refs)
            filtered_broad = _broader_containing_gold_count(inventory, gold_start, gold_end, suppressed_refs=suppressed_refs)
            current_acc["broader_counts"].append(current_broad)
            patch0_acc["broader_counts"].append(patch0_broad)
            patch1_acc["broader_counts"].append(patch1_broad)
            patch2_acc["broader_counts"].append(filtered_broad)
            current_acc["broader_total"] += current_broad
            patch0_acc["broader_total"] += patch0_broad
            patch1_acc["broader_total"] += patch1_broad
            patch2_acc["broader_total"] += filtered_broad
            sample_broad_current += current_broad
            sample_broad_patch0 += patch0_broad
            sample_broad_patch1 += patch1_broad
            sample_broad_filtered += filtered_broad
            normalized_gold = normalize_cue_text(gold_text)
            if normalized_gold in OMISSION_CUE_PHRASES:
                cue_gold_exact.append({"sample_id": sample_id, "column_ref_or_name": assignment["column_ref_or_name"], "gold_text": gold_text})
            if any(phrase in normalized_gold for phrase in OMISSION_CUE_PHRASES):
                cue_gold_contains.append({"sample_id": sample_id, "column_ref_or_name": assignment["column_ref_or_name"], "gold_text": gold_text})
            if current_covered and not filtered_covered:
                suppressible_gold_assignment_count += 1
                false_suppression_rows.append(
                    {
                        "sample_id": sample_id,
                        "column_ref_or_name": assignment["column_ref_or_name"],
                        "gold_text": gold_text,
                        "span_ref": candidate.span_ref if candidate else None,
                        "suppression_reason": reasons.get(candidate.span_ref) if candidate else None,
                    }
                )
            assignment_rows_for_sample.append(
                {
                    "assignment_index": assignment["assignment_index"],
                    "column_ref_or_name": assignment["column_ref_or_name"],
                    "gold_text": gold_text,
                    "current_covered": current_covered,
                    "patch0_generic_atomic_covered": patch0_covered,
                    "patch1_schema_label_aware_covered": patch1_covered,
                    "atomic_filtered_covered": filtered_covered,
                    "current_span_ref": candidate.span_ref if candidate else None,
                    "suppressed_by_rule": reasons.get(candidate.span_ref) if candidate else None,
                    "broader_containing_gold_current": current_broad,
                    "broader_containing_gold_patch0_generic": patch0_broad,
                    "broader_containing_gold_patch1_schema_label_aware": patch1_broad,
                    "broader_containing_gold_atomic_filtered": filtered_broad,
                }
            )

        current_acc["full_sample_covered_count"] += int(current_sample_full)
        patch0_acc["full_sample_covered_count"] += int(patch0_sample_full)
        patch1_acc["full_sample_covered_count"] += int(patch1_sample_full)
        patch2_acc["full_sample_covered_count"] += int(filtered_sample_full)
        row_payloads.append(
            {
                "sample_id": sample_id,
                "question_sha256": sha256_text(question),
                "candidate_generator_variant": SELECTED_VARIANT,
                "current_candidate_count": len(inventory),
                "patch0_generic_atomic_candidate_count": len(patch0_filtered_inventory),
                "patch1_schema_label_aware_candidate_count": len(patch1_filtered_inventory),
                "atomic_filtered_candidate_count": len(filtered_inventory),
                "patch0_generic_suppressed_candidate_count": len(patch0_suppressed_refs),
                "patch1_schema_label_aware_suppressed_candidate_count": len(patch1_suppressed_refs),
                "suppressed_candidate_count": len(suppressed_refs),
                "schema_alias_count": len(schema_aliases),
                "schema_label_count": len(schema_labels),
                "suppression_rule_counts": {
                    rule: sum(1 for reason in reasons.values() if reason["rule"] == rule)
                    for rule in sorted({reason["rule"] for reason in reasons.values()})
                },
                "assignment_count": len(assignments),
                "current_full_sample_representable": current_sample_full,
                "patch0_generic_full_sample_representable": patch0_sample_full,
                "patch1_schema_label_aware_full_sample_representable": patch1_sample_full,
                "atomic_filtered_full_sample_representable": filtered_sample_full,
                "broader_containing_gold_current": sample_broad_current,
                "broader_containing_gold_patch0_generic": sample_broad_patch0,
                "broader_containing_gold_patch1_schema_label_aware": sample_broad_patch1,
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
    patch0 = _finalize_domain(patch0_acc)
    patch1 = _finalize_domain(patch1_acc)
    filtered = _finalize_domain(patch2_acc)
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
    patch0_pass = (
        patch0.covered_assignment_count / patch0.assignment_count >= MIN_ASSIGNMENT_COVERAGE
        and patch0.full_sample_covered_count / len(design_ids) >= MIN_FULL_SAMPLE_COVERAGE
    )
    patch0_summary = {
        "stage": STAGE_NAME,
        "domain": "patch0_generic_atomic_candidate_domain",
        "status": "PASS" if patch0_pass else "FAIL",
        "audit_only": True,
        "candidate_generator_variant": SELECTED_VARIANT,
        "design_sample_count": len(design_ids),
        "assignment_count": patch0.assignment_count,
        "covered_assignment_count": patch0.covered_assignment_count,
        "missing_assignment_count": patch0.assignment_count - patch0.covered_assignment_count,
        "assignment_representability": patch0.covered_assignment_count / patch0.assignment_count,
        "full_sample_covered_count": patch0.full_sample_covered_count,
        "full_sample_representability": patch0.full_sample_covered_count / len(design_ids),
        "candidate_count_stats": patch0.candidate_count_stats,
        "broader_containing_gold_count_stats": patch0.broader_containing_gold_count_stats,
        "broader_containing_gold_total": patch0.broader_containing_gold_total,
        "suppressed_candidate_total": patch0_suppressed_candidate_total,
        "suppression_rule_counts": dict(sorted(patch0_suppression_rule_counts.items())),
        "reviewer_patch0_blocker": "generic atomic-child suppression creates three additional gold losses",
        "model_called": False,
        "gpu_called": False,
    }
    patch1_pass = (
        patch1.covered_assignment_count / patch1.assignment_count >= MIN_ASSIGNMENT_COVERAGE
        and patch1.full_sample_covered_count / len(design_ids) >= MIN_FULL_SAMPLE_COVERAGE
    )
    patch1_summary = {
        "stage": STAGE_NAME,
        "domain": "patch1_schema_label_aware_candidate_domain_audit",
        "status": "PASS" if patch1_pass else "FAIL",
        "audit_only": True,
        "candidate_generator_variant": SELECTED_VARIANT,
        "design_sample_count": len(design_ids),
        "assignment_count": patch1.assignment_count,
        "covered_assignment_count": patch1.covered_assignment_count,
        "missing_assignment_count": patch1.assignment_count - patch1.covered_assignment_count,
        "assignment_representability": patch1.covered_assignment_count / patch1.assignment_count,
        "full_sample_covered_count": patch1.full_sample_covered_count,
        "full_sample_representability": patch1.full_sample_covered_count / len(design_ids),
        "candidate_count_stats": patch1.candidate_count_stats,
        "broader_containing_gold_count_stats": patch1.broader_containing_gold_count_stats,
        "broader_containing_gold_total": patch1.broader_containing_gold_total,
        "suppressed_candidate_total": patch1_suppressed_candidate_total,
        "suppressed_schema_label_atomic_candidates": suppressed_candidates_with_strong_child,
        "suppression_rule_counts": dict(sorted(patch1_suppression_rule_counts.items())),
        "suppressible_gold_assignment_count": suppressible_gold_assignment_count,
        "minimum_assignment_representability": MIN_ASSIGNMENT_COVERAGE,
        "minimum_full_sample_representability": MIN_FULL_SAMPLE_COVERAGE,
        "model_called": False,
        "gpu_called": False,
    }
    filtered_pass = (
        filtered.covered_assignment_count / filtered.assignment_count >= MIN_ASSIGNMENT_COVERAGE
        and filtered.full_sample_covered_count / len(design_ids) >= MIN_FULL_SAMPLE_COVERAGE
    )
    filtered_summary = {
        "stage": STAGE_NAME,
        "domain": "patch2_schema_label_alias_candidate_domain_audit",
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
        "suppressed_schema_label_or_alias_atomic_candidates": alias_suppressed_candidates_with_strong_child,
        "suppression_rule_counts": dict(sorted(suppression_rule_counts.items())),
        "suppressible_gold_assignment_count": suppressible_gold_assignment_count,
        "schema_alias_stopwords": sorted(SCHEMA_ALIAS_STOPWORDS),
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
        "patch0_domain": "patch0_generic_atomic_candidate_domain",
        "candidate_domain_under_review": "patch2_schema_label_alias_candidate_domain_audit",
        "pareto_rows": [
            {
                "domain": "lexical_ngram2",
                "assignment_representability": current_summary["assignment_representability"],
                "full_sample_representability": current_summary["full_sample_representability"],
                "candidate_count_median": current.candidate_count_stats["median"],
                "candidate_count_p95": current.candidate_count_stats["p95"],
                "broader_containing_gold_total": current.broader_containing_gold_total,
            },
            {
                "domain": "patch0_generic_atomic",
                "assignment_representability": patch0_summary["assignment_representability"],
                "full_sample_representability": patch0_summary["full_sample_representability"],
                "candidate_count_median": patch0.candidate_count_stats["median"],
                "candidate_count_p95": patch0.candidate_count_stats["p95"],
                "broader_containing_gold_total": patch0.broader_containing_gold_total,
            },
            {
                "domain": "patch1_schema_label_aware",
                "assignment_representability": patch1_summary["assignment_representability"],
                "full_sample_representability": patch1_summary["full_sample_representability"],
                "candidate_count_median": patch1.candidate_count_stats["median"],
                "candidate_count_p95": patch1.candidate_count_stats["p95"],
                "broader_containing_gold_total": patch1.broader_containing_gold_total,
            },
            {
                "domain": "patch2_schema_label_alias",
                "assignment_representability": filtered_summary["assignment_representability"],
                "full_sample_representability": filtered_summary["full_sample_representability"],
                "candidate_count_median": filtered.candidate_count_stats["median"],
                "candidate_count_p95": filtered.candidate_count_stats["p95"],
                "broader_containing_gold_total": filtered.broader_containing_gold_total,
            },
        ],
        "assignment_representability_delta": filtered_summary["assignment_representability"] - current_summary["assignment_representability"],
        "full_sample_representability_delta": filtered_summary["full_sample_representability"] - current_summary["full_sample_representability"],
        "candidate_count_median_delta": filtered.candidate_count_stats["median"] - current.candidate_count_stats["median"],
        "candidate_count_p95_delta": filtered.candidate_count_stats["p95"] - current.candidate_count_stats["p95"],
        "candidate_count_max_delta": filtered.candidate_count_stats["max"] - current.candidate_count_stats["max"],
        "broader_containing_gold_total_delta": filtered.broader_containing_gold_total - current.broader_containing_gold_total,
        "threshold_decision": "PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW" if filtered_pass else "FAIL_DO_NOT_FREEZE",
        "preferred_freeze_gate": "no additional baseline-covered assignment losses",
        "method_freeze_authorized": False,
        "model_called": False,
        "gpu_called": False,
    }
    false_suppression_audit = {
        "stage": STAGE_NAME,
        "status": "PASS" if not false_suppression_rows else "FAIL",
        "baseline_domain": "current_lexical_ngram2",
        "candidate_domain_under_review": "patch2_schema_label_alias_candidate_domain_audit",
        "baseline_covered_assignment_count": current.covered_assignment_count,
        "patch1_covered_assignment_count": patch1.covered_assignment_count,
        "patch2_covered_assignment_count": filtered.covered_assignment_count,
        "additional_assignment_losses": len(false_suppression_rows),
        "additional_full_sample_losses": current.full_sample_covered_count - filtered.full_sample_covered_count,
        "false_suppression_examples": false_suppression_rows[:50],
        "preferred_freeze_gate_passed": not false_suppression_rows,
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
    synthetic_safety = synthetic_omission_cue_safety_audit()
    return current_summary, patch0_summary, patch1_summary, filtered_summary, comparison, false_suppression_audit, cue_audit, synthetic_safety, row_payloads, suppression_examples


def candidate_from_a5_json(payload: dict[str, Any]) -> CandidateSpan:
    return CandidateSpan(
        span_ref=str(payload["span_ref"]),
        start_char=int(payload["start_char"]),
        end_char=int(payload["end_char"]),
        text=str(payload["text"]),
        tags=tuple(payload.get("tags", [])),
        provenance_tags=tuple(payload.get("provenance_tags", [])),
    )


def a5_observed_error_counterfactual_domain_audit(
    stage7c_a5_erratum_dir: Path,
    stage7e0_a5_dir: Path,
) -> dict[str, Any]:
    corrected_rows = {
        row["sample_id"]: row
        for row in read_jsonl(stage7c_a5_erratum_dir / "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl")
    }
    raw_rows = {
        row["sample_id"]: json.loads(row["raw_output"])
        for row in read_jsonl(stage7e0_a5_dir / "uet_p4" / "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901" / "raw_primary_phase_o_generations.jsonl")
        if row.get("raw_output")
    }
    case_rows = {
        row["sample_id"]: row
        for row in read_jsonl(stage7e0_a5_dir / "uet_p4" / "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901" / "primary_case_results.jsonl")
    }
    decision_rows: list[dict[str, Any]] = []
    patch1_wrong_suppressed = 0
    patch2_wrong_suppressed = 0
    patch1_correct_gold_suppressed = 0
    patch2_correct_gold_suppressed = 0

    for sample_id in sorted(corrected_rows):
        corrected = corrected_rows[sample_id]
        predicted = raw_rows.get(sample_id, {}).get("column_span_refs", {})
        expected = corrected["label_side_expected"]["phase_o"]["column_span_refs"]
        inventory = [
            candidate_from_a5_json(candidate)
            for candidate in corrected["runtime_constraints"]["candidate_inventory"]
        ]
        by_ref = {candidate.span_ref: candidate for candidate in inventory}
        schema_aliases = schema_inventory_aliases(corrected["model_side_input"]["schema_inventory"])
        schema_labels = {label for sources in schema_aliases.values() for label in sources}
        patch1_detections = detect_omission_constructions(corrected["model_side_input"]["question"], schema_labels)
        patch2_detections = detect_omission_constructions(corrected["model_side_input"]["question"], schema_aliases)
        patch1_reasons = patch1_suppressible_span_refs(inventory, schema_labels, patch1_detections)
        patch2_reasons = suppressible_span_refs(inventory, schema_aliases, patch2_detections)

        for column_ref in sorted(set(expected) | set(predicted)):
            expected_ref = expected.get(column_ref)
            predicted_ref = predicted.get(column_ref)
            if expected_ref == predicted_ref:
                continue
            patch1_pred_suppressed = bool(predicted_ref and predicted_ref != "OMIT" and predicted_ref in patch1_reasons)
            patch2_pred_suppressed = bool(predicted_ref and predicted_ref != "OMIT" and predicted_ref in patch2_reasons)
            patch1_gold_suppressed = bool(expected_ref and expected_ref != "OMIT" and expected_ref in patch1_reasons)
            patch2_gold_suppressed = bool(expected_ref and expected_ref != "OMIT" and expected_ref in patch2_reasons)
            patch1_wrong_suppressed += int(patch1_pred_suppressed)
            patch2_wrong_suppressed += int(patch2_pred_suppressed)
            patch1_correct_gold_suppressed += int(patch1_gold_suppressed)
            patch2_correct_gold_suppressed += int(patch2_gold_suppressed)
            decision_rows.append(
                {
                    "sample_id": sample_id,
                    "column_ref": column_ref,
                    "expected_span_ref": expected_ref,
                    "expected_text": None if expected_ref == "OMIT" else by_ref.get(str(expected_ref), CandidateSpan("", 0, 0, "", ())).text,
                    "predicted_span_ref": predicted_ref,
                    "predicted_text": None if predicted_ref == "OMIT" else by_ref.get(str(predicted_ref), CandidateSpan("", 0, 0, "", ())).text,
                    "patch1_wrong_decision_suppressed": patch1_pred_suppressed,
                    "patch1_correct_gold_suppressed": patch1_gold_suppressed,
                    "patch1_suppression_reason": patch1_reasons.get(str(predicted_ref)),
                    "patch2_wrong_decision_suppressed": patch2_pred_suppressed,
                    "patch2_correct_gold_suppressed": patch2_gold_suppressed,
                    "patch2_suppression_reason": patch2_reasons.get(str(predicted_ref)),
                }
            )

    wrong_count = len(decision_rows)
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if wrong_count == 23 and patch2_correct_gold_suppressed == 0 and patch2_wrong_suppressed >= patch1_wrong_suppressed else "FAIL",
        "audit_type": "development_diagnostic_not_independent_evaluation",
        "source": "corrected A5 primary gold plus raw UET Qwen outputs already closed as 2/12 valid feasibility failure",
        "corrected_a5_wrong_decision_count": wrong_count,
        "patch1_wrong_decisions_suppressed": patch1_wrong_suppressed,
        "patch1_correct_gold_suppressed": patch1_correct_gold_suppressed,
        "patch2_wrong_decisions_suppressed": patch2_wrong_suppressed,
        "patch2_correct_gold_suppressed": patch2_correct_gold_suppressed,
        "patch2_incremental_wrong_decisions_suppressed": patch2_wrong_suppressed - patch1_wrong_suppressed,
        "case_status_counts": {
            status: sum(1 for row in case_rows.values() if row.get("status") == status)
            for status in sorted({str(row.get("status")) for row in case_rows.values()})
        },
        "decision_rows": decision_rows,
        "method_freeze_authorized": False,
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


def validation_report(
    current: dict[str, Any],
    patch0: dict[str, Any],
    patch1: dict[str, Any],
    filtered: dict[str, Any],
    comparison: dict[str, Any],
    false_suppression: dict[str, Any],
    cue: dict[str, Any],
    synthetic_safety: dict[str, Any],
    a5_counterfactual: dict[str, Any],
) -> str:
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

## PATCH0 Generic Atomic Domain

```text
assignment_representability={patch0["covered_assignment_count"]}/{patch0["assignment_count"]}
full_sample_representability={patch0["full_sample_covered_count"]}/{patch0["design_sample_count"]}
candidate_count_median={patch0["candidate_count_stats"]["median"]}
candidate_count_p95={patch0["candidate_count_stats"]["p95"]}
candidate_count_max={patch0["candidate_count_stats"]["max"]}
broader_containing_gold_total={patch0["broader_containing_gold_total"]}
reviewer_blocker={patch0["reviewer_patch0_blocker"]}
```

## PATCH1 Schema-Label-Aware Domain

```text
assignment_representability={patch1["covered_assignment_count"]}/{patch1["assignment_count"]}
full_sample_representability={patch1["full_sample_covered_count"]}/{patch1["design_sample_count"]}
candidate_count_median={patch1["candidate_count_stats"]["median"]}
candidate_count_p95={patch1["candidate_count_stats"]["p95"]}
candidate_count_max={patch1["candidate_count_stats"]["max"]}
suppressed_candidate_total={patch1["suppressed_candidate_total"]}
broader_containing_gold_total={patch1["broader_containing_gold_total"]}
```

## PATCH2 Schema-Label-Alias Domain

```text
assignment_representability={filtered["covered_assignment_count"]}/{filtered["assignment_count"]}
full_sample_representability={filtered["full_sample_covered_count"]}/{filtered["design_sample_count"]}
candidate_count_median={filtered["candidate_count_stats"]["median"]}
candidate_count_p95={filtered["candidate_count_stats"]["p95"]}
candidate_count_max={filtered["candidate_count_stats"]["max"]}
suppressed_candidate_total={filtered["suppressed_candidate_total"]}
broader_containing_gold_total={filtered["broader_containing_gold_total"]}
threshold_decision={comparison["threshold_decision"]}
additional_assignment_losses={false_suppression["additional_assignment_losses"]}
additional_full_sample_losses={false_suppression["additional_full_sample_losses"]}
preferred_freeze_gate_passed={str(false_suppression["preferred_freeze_gate_passed"]).lower()}
method_freeze_authorized=false
```

## Omission Cues

```text
cue_phrases={",".join(OMISSION_CUE_PHRASES)}
true_assigned_value_exact_cue_count={cue["true_assigned_value_exact_cue_count"]}
true_assigned_value_contains_cue_count={cue["true_assigned_value_contains_cue_count"]}
question_cue_occurrence_count={cue["question_cue_occurrence_count"]}
candidate_containing_cue_count={cue["candidate_containing_cue_count"]}
synthetic_omission_safety_status={synthetic_safety["status"]}
synthetic_positive_fixtures={synthetic_safety["positive_fixture_count"]}
synthetic_negative_literal_fixtures={synthetic_safety["negative_literal_fixture_count"]}
```

## A5 Observed Error Counterfactual

This is a development diagnostic over the already-closed A5 UET outputs, not an
independent evaluation.

```text
corrected_a5_wrong_decision_count={a5_counterfactual["corrected_a5_wrong_decision_count"]}
patch1_wrong_decisions_suppressed={a5_counterfactual["patch1_wrong_decisions_suppressed"]}
patch1_correct_gold_suppressed={a5_counterfactual["patch1_correct_gold_suppressed"]}
patch2_wrong_decisions_suppressed={a5_counterfactual["patch2_wrong_decisions_suppressed"]}
patch2_correct_gold_suppressed={a5_counterfactual["patch2_correct_gold_suppressed"]}
```

## Decision

The PATCH2 schema-label-alias candidate-domain amendment preserves the current
baseline representability while removing more observed A5 label-plus-value
distractors. This package does not freeze a new runtime protocol and does not
authorize a model rerun; it provides the evidence needed for reviewer approval
of a later protocol freeze.
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7B-A4 Atomic Candidate Domain and Omission-Cue Amendment

This package audits a candidate-domain amendment after Stage7E0-A5 was closed
as a corrected valid feasibility failure at 2/12. It compares current
`lexical_ngram2` against an audit-only schema-label-alias-filtered domain on the frozen 728
non-pilot design-train samples.

Review order:

1. `{STAGE_NAME}/A5_CORRECTED_VALID_FAIL_FREEZE.json`
2. `{STAGE_NAME}/DOMAIN_AUDIT_PROTOCOL.json`
3. `{STAGE_NAME}/ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json`
4. `{STAGE_NAME}/SCHEMA_LABEL_ALIAS_SPEC.json`
5. `{STAGE_NAME}/OMISSION_CUE_SUPPRESSION_RULE_SPEC.json`
6. `{STAGE_NAME}/CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json`
7. `{STAGE_NAME}/PATCH0_GENERIC_ATOMIC_DOMAIN_AUDIT.json`
8. `{STAGE_NAME}/SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json`
9. `{STAGE_NAME}/SCHEMA_LABEL_ALIAS_DOMAIN_AUDIT.json`
10. `{STAGE_NAME}/ATOMIC_FILTERED_DOMAIN_AUDIT.json`
11. `{STAGE_NAME}/DOMAIN_COMPARISON_AUDIT.json`
12. `{STAGE_NAME}/FALSE_SUPPRESSION_AUDIT.json`
13. `{STAGE_NAME}/OMISSION_CUE_DESIGN_TRAIN_AUDIT.json`
14. `{STAGE_NAME}/SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json`
15. `{STAGE_NAME}/A5_OBSERVED_ERROR_COUNTERFACTUAL_DOMAIN_AUDIT.json`
16. `{STAGE_NAME}/CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl`
17. `{STAGE_NAME}/CANDIDATE_SUPPRESSION_EXAMPLES.jsonl`
18. `{STAGE_NAME}/SOURCE_INPUT_MANIFEST.json`
19. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
20. `{STAGE_NAME}/STAGE7B_A4_LOCK.json`
21. `{STAGE_NAME}/VALIDATION_REPORT.md`
22. `scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py`
23. `scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py`
24. `tests/test_stage7b_a4_atomic_candidate_domain_omission_cue.py`

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
    stage7e0_a5_dir: Path = PROJECT_ROOT / STAGE7E0_A5_NAME,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    current, patch0, patch1, filtered, comparison, false_suppression, cue, synthetic_safety, rows, suppression_examples = audit_domains(raw_dir, stageeng0_dir, stageeng1_dir)
    a5_counterfactual = a5_observed_error_counterfactual_domain_audit(stage7c_a5_erratum_dir, stage7e0_a5_dir)
    write_json(out_dir / "SOURCE_INPUT_MANIFEST.json", source_input_manifest(stageeng0_dir, stageeng1_dir, stage7b_a2_dir, stage7b_a3_dir, stage7c_a5_dir, stage7c_a5_erratum_dir, stage7e0_a5_dir))
    write_json(out_dir / "A5_CORRECTED_VALID_FAIL_FREEZE.json", a5_corrected_valid_fail_freeze(stage7c_a5_erratum_dir))
    write_json(out_dir / "DOMAIN_AUDIT_PROTOCOL.json", domain_audit_protocol())
    write_json(out_dir / "ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json", atomic_rule_spec())
    write_json(out_dir / "SCHEMA_LABEL_ALIAS_SPEC.json", schema_label_alias_spec())
    write_json(out_dir / "OMISSION_CUE_SUPPRESSION_RULE_SPEC.json", omission_rule_spec())
    write_json(out_dir / "CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json", current)
    write_json(out_dir / "PATCH0_GENERIC_ATOMIC_DOMAIN_AUDIT.json", patch0)
    write_json(out_dir / "SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json", patch1)
    write_json(out_dir / "SCHEMA_LABEL_ALIAS_DOMAIN_AUDIT.json", filtered)
    write_json(out_dir / "ATOMIC_FILTERED_DOMAIN_AUDIT.json", filtered)
    write_json(out_dir / "DOMAIN_COMPARISON_AUDIT.json", comparison)
    write_json(out_dir / "FALSE_SUPPRESSION_AUDIT.json", false_suppression)
    write_json(out_dir / "OMISSION_CUE_DESIGN_TRAIN_AUDIT.json", cue)
    write_json(out_dir / "SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json", synthetic_safety)
    write_json(out_dir / "A5_OBSERVED_ERROR_COUNTERFACTUAL_DOMAIN_AUDIT.json", a5_counterfactual)
    write_jsonl(out_dir / "CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl", rows)
    write_jsonl(out_dir / "CANDIDATE_SUPPRESSION_EXAMPLES.jsonl", suppression_examples)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_derived_manifest(out_dir))

    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_SCHEMA_LABEL_ALIAS_CANDIDATE_DOMAIN_OMISSION_CUE_AUDIT_READY_FOR_REVIEW",
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
        "patch0_generic_assignment_representability": patch0["assignment_representability"],
        "patch0_generic_full_sample_representability": patch0["full_sample_representability"],
        "patch1_schema_label_aware_assignment_representability": patch1["assignment_representability"],
        "patch1_schema_label_aware_full_sample_representability": patch1["full_sample_representability"],
        "patch2_schema_label_alias_assignment_representability": filtered["assignment_representability"],
        "patch2_schema_label_alias_full_sample_representability": filtered["full_sample_representability"],
        "minimum_assignment_representability": MIN_ASSIGNMENT_COVERAGE,
        "minimum_full_sample_representability": MIN_FULL_SAMPLE_COVERAGE,
        "threshold_decision": comparison["threshold_decision"],
        "suppressed_candidate_total": filtered["suppressed_candidate_total"],
        "additional_assignment_losses": false_suppression["additional_assignment_losses"],
        "additional_full_sample_losses": false_suppression["additional_full_sample_losses"],
        "preferred_freeze_gate_passed": false_suppression["preferred_freeze_gate_passed"],
        "true_assigned_value_exact_cue_count": cue["true_assigned_value_exact_cue_count"],
        "true_assigned_value_contains_cue_count": cue["true_assigned_value_contains_cue_count"],
        "synthetic_omission_cue_safety_status": synthetic_safety["status"],
        "a5_counterfactual_status": a5_counterfactual["status"],
        "a5_patch1_wrong_decisions_suppressed": a5_counterfactual["patch1_wrong_decisions_suppressed"],
        "a5_patch2_wrong_decisions_suppressed": a5_counterfactual["patch2_wrong_decisions_suppressed"],
        "a5_patch2_correct_gold_suppressed": a5_counterfactual["patch2_correct_gold_suppressed"],
        "method_freeze_authorized": False,
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7B_A4_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(current, patch0, patch1, filtered, comparison, false_suppression, cue, synthetic_safety, a5_counterfactual))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "design_train_non_pilot_count": current["design_sample_count"],
        "assignment_count": current["assignment_count"],
        "current_assignment_representability": current["assignment_representability"],
        "current_full_sample_representability": current["full_sample_representability"],
        "patch0_generic_assignment_representability": patch0["assignment_representability"],
        "patch0_generic_full_sample_representability": patch0["full_sample_representability"],
        "patch1_schema_label_aware_assignment_representability": patch1["assignment_representability"],
        "patch1_schema_label_aware_full_sample_representability": patch1["full_sample_representability"],
        "patch2_schema_label_alias_assignment_representability": filtered["assignment_representability"],
        "patch2_schema_label_alias_full_sample_representability": filtered["full_sample_representability"],
        "suppressed_candidate_total": filtered["suppressed_candidate_total"],
        "additional_assignment_losses": false_suppression["additional_assignment_losses"],
        "additional_full_sample_losses": false_suppression["additional_full_sample_losses"],
        "a5_patch1_wrong_decisions_suppressed": a5_counterfactual["patch1_wrong_decisions_suppressed"],
        "a5_patch2_wrong_decisions_suppressed": a5_counterfactual["patch2_wrong_decisions_suppressed"],
        "a5_patch2_correct_gold_suppressed": a5_counterfactual["patch2_correct_gold_suppressed"],
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
        f"{STAGE7E0_A5_NAME}/SERVER_RESULT_CLASSIFICATION_PATCH5.json",
        f"{STAGE7E0_A5_NAME}/uet_p4/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901/raw_primary_phase_o_generations.jsonl",
        f"{STAGE7E0_A5_NAME}/uet_p4/stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901/primary_case_results.jsonl",
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
