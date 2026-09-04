from __future__ import annotations

import copy
import re
from collections import Counter
from statistics import median
from typing import Any

from .typed_materializer import materialize_value, semantic_materialization_type


LEADING_LABEL = re.compile(r"^(?:date|timestamp|time|on|at|for|as|named|called)\s+", re.IGNORECASE)
WORD = re.compile(r"[a-z0-9]+")


def canonical_boundary_text(text: str) -> tuple[str, list[str]]:
    value = text.strip()
    rules: list[str] = []
    stripped = value.rstrip(",.)")
    if stripped != value:
        value = stripped.strip()
        rules.append("trailing_punctuation")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        value = value[1:-1].strip()
        rules.append("surrounding_quotes")
    new_value = LEADING_LABEL.sub("", value).strip()
    if new_value != value:
        value = new_value
        rules.append("leading_label")
    if value.startswith("$") and len(value) > 1:
        value = value[1:].replace(",", "")
        rules.append("currency_symbol")
    if value.endswith("'s") and len(value) > 2:
        value = value[:-2]
        rules.append("possessive_suffix")
    return value, rules


def materialize_candidate_for_column(candidate: dict[str, Any], column: dict[str, Any]) -> tuple[bool, Any, str]:
    raw = str(candidate.get("text") or "")
    semantic = semantic_materialization_type(str(column.get("source_type") or column.get("type") or ""))
    if semantic in {"INTEGER", "REAL", "NUMERIC"} and raw.strip().startswith("$"):
        return False, None, "currency_prefixed_numeric_reject"
    canonical, _rules = canonical_boundary_text(raw)
    try:
        materialized = materialize_value(canonical, str(column.get("source_type") or column.get("type") or ""))
    except Exception as exc:  # noqa: BLE001
        return False, None, str(getattr(exc, "reason_code", "") or exc)
    return True, materialized.value, "materializer_accept"


def candidate_kind(text: str) -> str:
    for declared, kind in (("DATETIME", "datetime"), ("DATE", "date"), ("INTEGER", "integer"), ("REAL", "real")):
        accepted, _value, _reason = materialize_candidate_for_column({"text": text}, {"source_type": declared})
        if accepted:
            return kind
    if str(text).strip().startswith("$"):
        accepted, _value, _reason = materialize_candidate_for_column({"text": text}, {"source_type": "REAL"})
        return "currency_numeric" if accepted else "text"
    return "text"


def column_allows_candidate(column: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, str]:
    semantic = semantic_materialization_type(str(column.get("source_type") or column.get("type") or ""))
    if semantic == "BLOB":
        return False, "unsupported_blob_reject"
    if semantic == "TEXT":
        return True, "text_candidate"
    accepted, _value, reason = materialize_candidate_for_column(candidate, column)
    return accepted, "materializer_accept" if accepted else f"{semantic.lower()}_{reason}"


def occurrence_signature(candidate: dict[str, Any]) -> tuple[Any, Any, str]:
    return (
        candidate.get("start_char"),
        candidate.get("end_char"),
        "|".join(str(tag) for tag in candidate.get("provenance_tags") or candidate.get("tags") or []),
    )


def dominated_boundary_signature(candidate: dict[str, Any]) -> tuple[tuple[Any, Any, str], str, str]:
    canonical, _rules = canonical_boundary_text(str(candidate.get("text") or ""))
    return occurrence_signature(candidate), candidate_kind(canonical), canonical.casefold()


def filter_dominated_boundaries(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_signature: dict[tuple[tuple[Any, Any, str], str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_signature.setdefault(dominated_boundary_signature(candidate), []).append(candidate)
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for group in by_signature.values():
        ranked = sorted(group, key=lambda item: (len(str(item.get("text") or "")), str(item.get("span_ref") or "")))
        kept.append(ranked[0])
        suppressed.extend(ranked[1:])
    return sorted(kept, key=lambda item: str(item.get("span_ref") or "")), sorted(suppressed, key=lambda item: str(item.get("span_ref") or ""))


def column_is_omittable(column: dict[str, Any]) -> bool:
    return bool(column.get("nullable") or column.get("has_default") or column.get("primary_key") or column.get("autoincrement") or column.get("generated"))


def normalized_label_tokens(column: dict[str, Any]) -> list[str]:
    raw = str(column.get("column_name") or "")
    tokens = WORD.findall(raw.replace("_", " ").casefold())
    return [token for token in tokens if token not in {"id", "uuid", "pk", "fk"}]


def label_occurrences(question: str, column: dict[str, Any]) -> list[tuple[int, int]]:
    normalized = question.casefold()
    label = " ".join(normalized_label_tokens(column))
    if not label:
        return []
    variants = {label, label.replace(" ", "_")}
    output: list[tuple[int, int]] = []
    for variant in variants:
        if not variant:
            continue
        for match in re.finditer(rf"(?<![a-z0-9_]){re.escape(variant)}(?![a-z0-9_])", normalized):
            output.append((match.start(), match.end()))
    return sorted(set(output))


def candidate_in_label_segment(candidate: dict[str, Any], question: str, column: dict[str, Any]) -> bool:
    start = candidate.get("start_char")
    if not isinstance(start, int):
        return False
    occurrences = label_occurrences(question, column)
    if not occurrences:
        return False
    other_label_starts: list[int] = []
    for other in column.get("_all_columns", []):
        if other.get("column_ref") == column.get("column_ref"):
            continue
        other_label_starts.extend(begin for begin, _end in label_occurrences(question, other))
    punctuation = [match.start() for match in re.finditer(r"[.;\n]", question)]
    for _begin, end in occurrences:
        bounds = [pos for pos in [*other_label_starts, *punctuation, end + 96] if pos > end]
        segment_end = min(bounds) if bounds else end + 96
        if end <= start <= segment_end:
            return True
    return False


def build_column_specific_domains(
    *,
    model_side_input: dict[str, Any],
    runtime_constraints: dict[str, Any],
) -> dict[str, Any]:
    inventory = [dict(candidate) for candidate in runtime_constraints["candidate_inventory"]]
    columns = [dict(column) for column in model_side_input["schema_inventory"]["columns"]]
    for column in columns:
        column["_all_columns"] = columns
    question = str(model_side_input.get("question") or "")
    domains: dict[str, list[str]] = {}
    audit_rows: list[dict[str, Any]] = []
    for column in columns:
        allowed = []
        rejected = []
        for candidate in inventory:
            accepted, reason = column_allows_candidate(column, candidate)
            if accepted:
                allowed.append(candidate)
            else:
                rejected.append({"span_ref": candidate["span_ref"], "reason": reason})
        kept, dominated = filter_dominated_boundaries(allowed)
        strong_local = [candidate for candidate in kept if candidate_in_label_segment(candidate, question, column)]
        semantic = semantic_materialization_type(str(column.get("source_type") or ""))
        used_text_label_filter = semantic == "TEXT" and bool(strong_local)
        if used_text_label_filter:
            strong_refs = {candidate["span_ref"] for candidate in strong_local}
            final_candidates = [*strong_local, *[candidate for candidate in kept if candidate["span_ref"] not in strong_refs]]
        else:
            final_candidates = kept
        domain = [candidate["span_ref"] for candidate in final_candidates]
        omit_removed_by_evidence = False
        if column_is_omittable(column):
            if strong_local:
                omit_removed_by_evidence = True
            else:
                domain = ["OMIT", *domain]
        column_ref = str(column["column_ref"])
        domains[column_ref] = domain
        audit_rows.append(
            {
                "column_ref": column_ref,
                "column_name": column.get("column_name"),
                "source_type": column.get("source_type"),
                "semantic_materialization_type": semantic,
                "global_candidate_count": len(inventory),
                "column_domain_count": len([item for item in domain if item != "OMIT"]),
                "omit_allowed": "OMIT" in domain,
                "omit_removed_by_strong_evidence": omit_removed_by_evidence,
                "text_label_segment_filter_applied": used_text_label_filter,
                "strong_evidence_candidate_count": len(strong_local),
                "rejected_count": len(rejected),
                "dominated_boundary_suppressed_count": len(dominated),
            }
        )
    return {
        "domains": domains,
        "audit_rows": audit_rows,
        "domain_construction_uses_gold": False,
        "model_visible_inputs": ["declared column type", "column/table name", "question", "deterministic candidate tags", "deterministic lexical/boundary rules"],
    }


def dynamic_schema_with_column_domains(
    *,
    model_side_input: dict[str, Any],
    runtime_constraints: dict[str, Any],
    domains: dict[str, list[str]],
) -> dict[str, Any]:
    schema = copy.deepcopy(runtime_constraints["phase_o_schema"])
    columns = list(model_side_input["schema_inventory"]["columns"])
    if "oneOf" in schema:
        for branch in schema["oneOf"]:
            table_ref = branch["properties"]["table_ref"].get("const") or branch["properties"]["table_ref"].get("enum", [None])[0]
            branch_columns = [column for column in columns if column["table_ref"] == table_ref]
            column_span_refs = branch["properties"]["column_span_refs"]
            column_span_refs["properties"] = {
                column["column_ref"]: {"type": "string", "enum": domains[column["column_ref"]]}
                for column in branch_columns
                if column["column_ref"] in domains
            }
    else:
        column_span_refs = schema["properties"]["column_span_refs"]
        column_span_refs["properties"] = {
            column_ref: {"type": "string", "enum": domain}
            for column_ref, domain in domains.items()
            if column_ref in column_span_refs.get("properties", {})
        }
    schema["title"] = "StageENG2B Column-Specific Candidate Selection Output"
    schema["x-eng2b-span-uniqueness"] = "stateful prefix grammar removes each selected non-OMIT SPAN ref from later column domains"
    return schema


def audit_domains_against_gold(row: dict[str, Any], domain_result: dict[str, Any]) -> dict[str, Any]:
    candidate_by_ref = {candidate["span_ref"]: candidate for candidate in row["runtime_constraints"]["candidate_inventory"]}
    column_by_ref = {column["column_ref"]: column for column in row["model_side_input"]["schema_inventory"]["columns"]}
    target_rows = row["label_side_expected"]["target_state"]["typed_target_rows"]
    target_row = target_rows[0] if target_rows else {}
    rows = []
    newly_suppressed = 0
    for item in row["label_side_expected"]["gold_column_span_ref_oracle"]:
        column_ref = item["column_ref"]
        column = column_by_ref[column_ref]
        domain = domain_result["domains"].get(column_ref, [])
        gold_ref = item.get("candidate_span_ref")
        exact_ref_represented = gold_ref in domain if gold_ref else False
        expected_value = target_row.get(str(item.get("column_name")))
        original_semantic = False
        final_semantic = False
        for candidate in candidate_by_ref.values():
            accepted, value, _reason = materialize_candidate_for_column(candidate, column)
            if accepted and value == expected_value:
                original_semantic = True
                if candidate["span_ref"] in domain:
                    final_semantic = True
        if original_semantic and not final_semantic:
            newly_suppressed += 1
        rows.append(
            {
                "column_ref": column_ref,
                "column_name": item.get("column_name"),
                "source_type": item.get("source_type"),
                "gold_ref": gold_ref,
                "candidate_generation_miss": bool(item.get("candidate_generation_miss")),
                "exact_ref_represented": exact_ref_represented,
                "semantic_gold_represented_in_prefilter_inventory": original_semantic,
                "semantic_gold_represented_in_final_domain": final_semantic,
                "newly_semantically_suppressed_gold": bool(original_semantic and not final_semantic),
            }
        )
    return {
        "sample_id": row["sample_id"],
        "gold_audit_rows": rows,
        "newly_semantically_suppressed_gold_count": newly_suppressed,
        "exact_ref_missing_count": sum(1 for audit in rows if not audit["exact_ref_represented"] and not audit["candidate_generation_miss"]),
        "semantic_missing_after_filter_count": sum(1 for audit in rows if not audit["semantic_gold_represented_in_final_domain"] and audit["semantic_gold_represented_in_prefilter_inventory"]),
    }


def summarize_domain_audit(domain_rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [int(row["column_domain_count"]) for row in domain_rows]
    global_counts = [int(row["global_candidate_count"]) for row in domain_rows]
    by_type: dict[str, Counter[str]] = {}
    for row in domain_rows:
        semantic = str(row["semantic_materialization_type"])
        by_type.setdefault(semantic, Counter())
        by_type[semantic]["columns"] += 1
        by_type[semantic]["text_label_segment_filter_applied"] += int(row.get("text_label_segment_filter_applied") is True)
        by_type[semantic]["omit_removed_by_strong_evidence"] += int(row.get("omit_removed_by_strong_evidence") is True)
    return {
        "column_count": len(domain_rows),
        "mean_global_candidates_per_column": sum(global_counts) / len(global_counts) if global_counts else 0.0,
        "mean_candidates_per_column": sum(counts) / len(counts) if counts else 0.0,
        "median_candidates_per_column": median(counts) if counts else 0.0,
        "max_candidates_per_column": max(counts) if counts else 0,
        "by_semantic_materialization_type": {key: dict(value) for key, value in sorted(by_type.items())},
    }
