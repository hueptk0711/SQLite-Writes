from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Any

from .typed_materializer import STRICT_INT, STRICT_REAL, semantic_materialization_type


DATE_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2}")
DATETIME_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?")
LEADING_LABEL = re.compile(r"^(?:date|timestamp|time|on|at|for|as|named|called)\s+", re.IGNORECASE)


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


def candidate_kind(text: str) -> str:
    canonical, _ = canonical_boundary_text(text)
    if str(text).strip().startswith("$"):
        return "currency_numeric" if STRICT_REAL.fullmatch(canonical) else "text"
    if DATETIME_LITERAL.fullmatch(canonical):
        return "datetime"
    if DATE_LITERAL.fullmatch(canonical):
        return "date"
    if STRICT_INT.fullmatch(canonical):
        return "integer"
    if STRICT_REAL.fullmatch(canonical):
        return "real"
    return "text"


def column_allows_candidate(column: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, str]:
    semantic = semantic_materialization_type(str(column.get("source_type") or column.get("type") or ""))
    kind = candidate_kind(str(candidate.get("text") or ""))
    if semantic == "INTEGER":
        return kind == "integer", "integer_exact_literal" if kind == "integer" else "integer_type_reject"
    if semantic in {"REAL", "NUMERIC"}:
        return kind in {"integer", "real"}, "numeric_exact_literal" if kind in {"integer", "real"} else "numeric_type_reject"
    if semantic == "DATE":
        return kind == "date", "date_exact_literal" if kind == "date" else "date_type_reject"
    if semantic == "DATETIME":
        return kind == "datetime", "timestamp_exact_literal" if kind == "datetime" else "timestamp_type_reject"
    return True, "text_candidate"


def dominated_boundary_signature(candidate: dict[str, Any]) -> tuple[str, str]:
    canonical, _ = canonical_boundary_text(str(candidate.get("text") or ""))
    return candidate_kind(canonical), canonical.casefold()


def filter_dominated_boundaries(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_signature: dict[tuple[str, str], list[dict[str, Any]]] = {}
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


def build_column_specific_domains(row: dict[str, Any]) -> dict[str, Any]:
    inventory = [dict(candidate) for candidate in row["runtime_constraints"]["candidate_inventory"]]
    columns = list(row["model_side_input"]["schema_inventory"]["columns"])
    domains: dict[str, list[str]] = {}
    audit_rows: list[dict[str, Any]] = []
    suppressed_gold = 0
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
        domain = [candidate["span_ref"] for candidate in kept]
        if column_is_omittable(column):
            domain = ["OMIT", *domain]
        column_ref = str(column["column_ref"])
        domains[column_ref] = domain
        gold_ref = row.get("label_side_expected", {}).get("phase_o", {}).get("column_span_refs", {}).get(column_ref)
        gold_represented = gold_ref in domain if gold_ref else None
        if gold_ref and gold_ref != "OMIT" and not gold_represented:
            suppressed_gold += 1
        audit_rows.append(
            {
                "column_ref": column_ref,
                "column_name": column.get("column_name"),
                "source_type": column.get("source_type"),
                "semantic_materialization_type": semantic_materialization_type(str(column.get("source_type") or "")),
                "global_candidate_count": len(inventory),
                "column_domain_count": len([item for item in domain if item != "OMIT"]),
                "omit_allowed": "OMIT" in domain,
                "rejected_count": len(rejected),
                "dominated_boundary_suppressed_count": len(dominated),
                "gold_ref": gold_ref,
                "gold_represented": gold_represented,
            }
        )
    return {
        "sample_id": row["sample_id"],
        "domains": domains,
        "audit_rows": audit_rows,
        "gold_suppressed_count": suppressed_gold,
    }


def dynamic_schema_with_column_domains(row: dict[str, Any], domains: dict[str, list[str]]) -> dict[str, Any]:
    schema = dict(row["runtime_constraints"]["phase_o_schema"])
    if "oneOf" in schema:
        branches = []
        for branch in schema["oneOf"]:
            table_ref = branch["properties"]["table_ref"]["const"]
            columns = [
                column
                for column in row["model_side_input"]["schema_inventory"]["columns"]
                if column["table_ref"] == table_ref
            ]
            patched = dict(branch)
            patched["properties"] = dict(branch["properties"])
            column_span_refs = dict(branch["properties"]["column_span_refs"])
            column_span_refs["properties"] = {
                column["column_ref"]: {"type": "string", "enum": domains[column["column_ref"]]}
                for column in columns
                if column["column_ref"] in domains
            }
            patched["properties"]["column_span_refs"] = column_span_refs
            branches.append(patched)
        schema["oneOf"] = branches
    else:
        column_span_refs = dict(schema["properties"]["column_span_refs"])
        column_span_refs["properties"] = {
            column_ref: {"type": "string", "enum": domain}
            for column_ref, domain in domains.items()
            if column_ref in column_span_refs.get("properties", {})
        }
        schema["properties"] = dict(schema["properties"])
        schema["properties"]["column_span_refs"] = column_span_refs
    schema["title"] = "StageENG2B Column-Specific Candidate Selection Output"
    schema["x-eng2b-span-uniqueness"] = "prefix decoder removes each selected non-OMIT SPAN ref from remaining column domains"
    return schema


def summarize_domain_audit(domain_rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [int(row["column_domain_count"]) for row in domain_rows]
    global_counts = [int(row["global_candidate_count"]) for row in domain_rows]
    by_type: dict[str, Counter[str]] = {}
    for row in domain_rows:
        semantic = str(row["semantic_materialization_type"])
        by_type.setdefault(semantic, Counter())
        by_type[semantic]["columns"] += 1
        by_type[semantic]["gold_represented"] += int(row.get("gold_represented") is True)
        by_type[semantic]["gold_missing"] += int(row.get("gold_represented") is False)
    return {
        "column_count": len(domain_rows),
        "mean_global_candidates_per_column": sum(global_counts) / len(global_counts) if global_counts else 0.0,
        "mean_candidates_per_column": sum(counts) / len(counts) if counts else 0.0,
        "median_candidates_per_column": median(counts) if counts else 0.0,
        "max_candidates_per_column": max(counts) if counts else 0,
        "gold_candidate_suppressed_count": sum(1 for row in domain_rows if row.get("gold_represented") is False),
        "by_semantic_materialization_type": {key: dict(value) for key, value in sorted(by_type.items())},
    }
