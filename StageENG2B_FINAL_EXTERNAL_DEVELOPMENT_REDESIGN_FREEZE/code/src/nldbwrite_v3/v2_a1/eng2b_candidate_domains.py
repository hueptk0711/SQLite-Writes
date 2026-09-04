from __future__ import annotations

import copy
import re
from collections import Counter
from statistics import median
from typing import Any

from .typed_materializer import materialize_value, semantic_materialization_type


LEADING_LABEL = re.compile(r"^(?:date|timestamp|time|on|at|for|as|named|called)\s+", re.IGNORECASE)
WORD = re.compile(r"[a-z0-9]+")
DEFAULT_CUE = re.compile(
    r"\b(?:use(?:\s+the|\s+its)?\s+default|default\s+value|leave(?:\s+\w+){0,3}\s+default|as\s+default)\b",
    re.IGNORECASE,
)
LOCAL_VALUE_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "by",
    "data",
    "details",
    "for",
    "following",
    "from",
    "in",
    "insert",
    "into",
    "new",
    "of",
    "on",
    "or",
    "record",
    "records",
    "row",
    "table",
    "the",
    "to",
    "use",
    "value",
    "values",
    "with",
}


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


def interval(candidate: dict[str, Any]) -> tuple[int, int] | None:
    start = candidate.get("start_char")
    end = candidate.get("end_char")
    if isinstance(start, int) and isinstance(end, int) and start <= end:
        return start, end
    return None


def provenance_family(candidate: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(tag) for tag in candidate.get("provenance_tags") or candidate.get("tags") or [])


def intervals_overlap(left: tuple[int, int] | None, right: tuple[int, int] | None) -> bool:
    if left is None or right is None:
        return left == right
    return max(left[0], right[0]) <= min(left[1], right[1])


def same_provenance_family(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_tags = set(provenance_family(left))
    right_tags = set(provenance_family(right))
    return not left_tags or not right_tags or bool(left_tags & right_tags) or left_tags == right_tags


def dominated_boundary_signature(candidate: dict[str, Any]) -> tuple[str, str]:
    canonical, _rules = canonical_boundary_text(str(candidate.get("text") or ""))
    return candidate_kind(canonical), canonical.casefold()


def same_boundary_occurrence(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        dominated_boundary_signature(left) == dominated_boundary_signature(right)
        and intervals_overlap(interval(left), interval(right))
        and same_provenance_family(left, right)
    )


def filter_dominated_boundaries(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_signature: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_signature.setdefault(dominated_boundary_signature(candidate), []).append(candidate)
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for same_value in by_signature.values():
        groups: list[list[dict[str, Any]]] = []
        for candidate in sorted(same_value, key=lambda item: (interval(item) or (-1, -1), str(item.get("span_ref") or ""))):
            for group in groups:
                if any(same_boundary_occurrence(candidate, member) for member in group):
                    group.append(candidate)
                    break
            else:
                groups.append([candidate])
        for group in groups:
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


def label_variants(column: dict[str, Any]) -> set[str]:
    raw = str(column.get("column_name") or "").strip().casefold()
    raw_tokens = WORD.findall(raw.replace("_", " "))
    semantic_tokens = [token for token in raw_tokens if token not in {"id", "uuid", "pk", "fk"}]
    variants = {raw.replace("_", " "), raw}
    if semantic_tokens and not (len(semantic_tokens) == 1 and len(semantic_tokens) < len(raw_tokens)):
        label = " ".join(semantic_tokens)
        variants.update({label, label.replace(" ", "_")})
    return {variant for variant in variants if variant}


def label_occurrences(question: str, column: dict[str, Any]) -> list[tuple[int, int]]:
    normalized = question.casefold()
    output: list[tuple[int, int]] = []
    for variant in label_variants(column):
        for match in re.finditer(rf"(?<![a-z0-9_]){re.escape(variant)}(?![a-z0-9_])", normalized):
            output.append((match.start(), match.end()))
    return sorted(set(output))


def nonspace_before(text: str, index: int) -> str:
    cursor = index - 1
    while cursor >= 0 and text[cursor].isspace():
        cursor -= 1
    return text[cursor] if cursor >= 0 else ""


def nonspace_after(text: str, index: int) -> str:
    cursor = index
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return text[cursor] if cursor < len(text) else ""


def label_occurrence_in_schema_list_header(question: str, occurrence: tuple[int, int]) -> bool:
    begin, end = occurrence
    return nonspace_before(question, begin) in {"(", ","} and nonspace_after(question, end) in {",", ")"}


def candidate_in_label_segment(candidate: dict[str, Any], question: str, column: dict[str, Any]) -> bool:
    start = candidate.get("start_char")
    end_char = candidate.get("end_char")
    if not isinstance(start, int) or not isinstance(end_char, int):
        return False
    occurrences = [occurrence for occurrence in label_occurrences(question, column) if not label_occurrence_in_schema_list_header(question, occurrence)]
    if not occurrences:
        return False
    other_label_occurrences: list[tuple[int, int]] = []
    for other in column.get("_all_columns", []):
        if other.get("column_ref") == column.get("column_ref"):
            continue
        other_label_occurrences.extend(occurrence for occurrence in label_occurrences(question, other) if not label_occurrence_in_schema_list_header(question, occurrence))
    candidate_interval = (start, end_char)
    for occurrence in other_label_occurrences:
        if not intervals_overlap(candidate_interval, occurrence):
            continue
        occurrence_inside_candidate = start <= occurrence[0] and occurrence[1] <= end_char
        if candidate_has_quoted_signal(candidate) and occurrence_inside_candidate:
            continue
        return False
    if candidate_contains_column_label_with_extra_value(candidate, column):
        return True
    other_label_starts = [begin for begin, _end in other_label_occurrences]
    punctuation = [match.start() for match in re.finditer(r"[.,;\n]", question)]
    for begin, end in occurrences:
        if intervals_overlap(candidate_interval, (begin, end)):
            continue
        bounds = [pos for pos in [*other_label_starts, *punctuation, end + 96] if pos > end]
        segment_end = min(bounds) if bounds else end + 96
        if end <= start <= segment_end:
            return True
        labels_between = any(end_char <= other_begin < begin for other_begin, _other_end in other_label_occurrences)
        punctuation_between = any(end_char <= pos < begin for pos in punctuation)
        clause_start = max([pos for pos in punctuation if pos < begin], default=-1)
        prior_label_in_clause = any(clause_start < other_end <= start for _other_begin, other_end in other_label_occurrences)
        if end_char <= begin and begin - end_char <= 96 and not labels_between and not punctuation_between and not prior_label_in_clause:
            between = question[end_char:begin].casefold()
            normalized_between = between.strip(" \t\r\n'\"`_,:-()")
            if normalized_between == "" or re.fullmatch(r"(?:for|as|with)(?:\s+the)?", normalized_between):
                return True
        if end_char <= begin and begin - end_char <= 16 and candidate_has_quoted_signal(candidate):
            between = question[end_char:begin].casefold()
            normalized_between = between.strip(" \t\r\n'\"`_,:-()")
            if (normalized_between == "" or re.fullmatch(r"(?:for|as|with)(?:\s+the)?", normalized_between)) and not labels_between and not punctuation_between:
                return True
    return False


def label_segment_ranges(question: str, column: dict[str, Any]) -> list[tuple[int, int]]:
    occurrences = [occurrence for occurrence in label_occurrences(question, column) if not label_occurrence_in_schema_list_header(question, occurrence)]
    if not occurrences:
        return []
    other_label_starts: list[int] = []
    for other in column.get("_all_columns", []):
        if other.get("column_ref") == column.get("column_ref"):
            continue
        other_label_starts.extend(begin for begin, _end in label_occurrences(question, other) if not label_occurrence_in_schema_list_header(question, (begin, _end)))
    punctuation = [match.start() for match in re.finditer(r"[.,;\n]", question)]
    ranges = []
    for begin, end in occurrences:
        bounds = [pos for pos in [*other_label_starts, *punctuation, end + 96] if pos > end]
        ranges.append((begin, min(bounds) if bounds else end + 96))
    return ranges


def default_cue_in_label_segment(question: str, column: dict[str, Any]) -> bool:
    normalized = question.casefold()
    for start, end in label_segment_ranges(normalized, column):
        if DEFAULT_CUE.search(normalized[start:end]):
            return True
    return False


def candidate_is_default_cue(candidate: dict[str, Any]) -> bool:
    return bool(DEFAULT_CUE.search(str(candidate.get("text") or "")))


def candidate_tag_set(candidate: dict[str, Any]) -> set[str]:
    return {
        str(tag).casefold()
        for tag in [
            *(candidate.get("tags") or []),
            *(candidate.get("provenance_tags") or []),
        ]
    }


def candidate_has_quoted_signal(candidate: dict[str, Any]) -> bool:
    return bool(candidate_tag_set(candidate) & {"quoted", "quoted_text", "quoted_content"})


def candidate_text_tokens(candidate: dict[str, Any]) -> list[str]:
    canonical, _rules = canonical_boundary_text(str(candidate.get("text") or ""))
    return WORD.findall(canonical.casefold())


def candidate_contains_column_label_with_extra_value(candidate: dict[str, Any], column: dict[str, Any]) -> bool:
    tokens = candidate_text_tokens(candidate)
    label_tokens = set(normalized_label_tokens(column))
    if not tokens or not label_tokens or not label_tokens.issubset(tokens) or set(tokens).issubset(label_tokens):
        return False
    canonical, _rules = canonical_boundary_text(str(candidate.get("text") or ""))
    if re.search(r"[,;:()=]", canonical):
        return False
    return True


def candidate_overlaps_any_label(
    candidate: dict[str, Any],
    question: str,
    columns: list[dict[str, Any]],
    exclude_column_ref: str | None = None,
    *,
    allow_contained_quoted_overlap: bool = False,
) -> bool:
    candidate_interval = interval(candidate)
    if candidate_interval is None:
        return False
    label_ranges: list[tuple[int, int]] = []
    for column in columns:
        if exclude_column_ref is not None and str(column.get("column_ref")) == exclude_column_ref:
            continue
        label_ranges.extend(label_occurrences(question, column))
    for label_range in label_ranges:
        if not intervals_overlap(candidate_interval, label_range):
            continue
        label_inside_candidate = candidate_interval[0] <= label_range[0] and label_range[1] <= candidate_interval[1]
        if allow_contained_quoted_overlap and candidate_has_quoted_signal(candidate) and label_inside_candidate:
            continue
        return True
    return False


def candidate_has_local_value_signal(candidate: dict[str, Any], question: str, column: dict[str, Any]) -> bool:
    if candidate_is_default_cue(candidate):
        return False
    tokens = candidate_text_tokens(candidate)
    column_label_tokens = set(normalized_label_tokens(column))
    if column_label_tokens and set(tokens).issubset(column_label_tokens):
        return False
    tags = candidate_tag_set(candidate)
    if tags & {"quoted_text", "quoted_content", "identifier", "compound_identifier", "number"}:
        return not candidate_overlaps_any_label(
            candidate,
            question,
            column.get("_all_columns", []),
            str(column.get("column_ref")),
            allow_contained_quoted_overlap=True,
        )
    text = str(candidate.get("text") or "").strip()
    if candidate_kind(text) != "text":
        return True
    if not tokens:
        return False
    if all(token in LOCAL_VALUE_STOPWORDS for token in tokens):
        return False
    if tokens[0] in {"and", "as", "for", "into", "the", "to", "with"} or tokens[-1] in {"and", "as", "for", "into", "the", "to", "with"}:
        return False
    if candidate_overlaps_any_label(candidate, question, column.get("_all_columns", []), str(column.get("column_ref"))):
        return False
    return True


def expand_with_contained_value_candidates(selected: list[dict[str, Any]], kept: list[dict[str, Any]], question: str, column: dict[str, Any]) -> list[dict[str, Any]]:
    output = {str(candidate["span_ref"]): candidate for candidate in selected}
    selected_intervals = [interval(candidate) for candidate in selected]
    for candidate in kept:
        candidate_interval = interval(candidate)
        if candidate_interval is None:
            continue
        contained = any(
            selected_interval is not None
            and selected_interval[0] <= candidate_interval[0]
            and candidate_interval[1] <= selected_interval[1]
            for selected_interval in selected_intervals
        )
        if contained and candidate_has_local_value_signal(candidate, question, column):
            output[str(candidate["span_ref"])] = candidate
    return sorted(output.values(), key=lambda item: str(item.get("span_ref") or ""))


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
        default_cue = default_cue_in_label_segment(question, column)
        explicit_strong_local = [candidate for candidate in strong_local if not candidate_is_default_cue(candidate)]
        value_strong_local = [candidate for candidate in explicit_strong_local if candidate_has_local_value_signal(candidate, question, column)]
        semantic = semantic_materialization_type(str(column.get("source_type") or ""))
        used_text_label_filter = semantic == "TEXT" and bool(value_strong_local)
        if used_text_label_filter:
            final_candidates = expand_with_contained_value_candidates(value_strong_local, kept, question, column)
        else:
            final_candidates = kept
        domain = [candidate["span_ref"] for candidate in final_candidates]
        omit_removed_by_evidence = False
        omit_forced_by_default_cue = False
        if column_is_omittable(column):
            if default_cue and not value_strong_local:
                domain = ["OMIT"]
                omit_forced_by_default_cue = True
            elif value_strong_local:
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
                "omit_forced_by_default_cue": omit_forced_by_default_cue,
                "text_label_segment_filter_applied": used_text_label_filter,
                "strong_evidence_candidate_count": len(strong_local),
                "explicit_strong_evidence_candidate_count": len(explicit_strong_local),
                "value_strong_evidence_candidate_count": len(value_strong_local),
                "text_domain_restricted_by_label_segment": bool(used_text_label_filter and len(domain) < len(kept)),
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
        by_type[semantic]["omit_forced_by_default_cue"] += int(row.get("omit_forced_by_default_cue") is True)
        by_type[semantic]["text_domain_restricted_by_label_segment"] += int(row.get("text_domain_restricted_by_label_segment") is True)
        by_type[semantic]["dominated_boundary_suppressed_count"] += int(row.get("dominated_boundary_suppressed_count") or 0)
    text_strong = [row for row in domain_rows if row.get("semantic_materialization_type") == "TEXT" and int(row.get("explicit_strong_evidence_candidate_count") or 0) > 0]
    restricted_text = [row for row in text_strong if row.get("text_domain_restricted_by_label_segment") is True]
    return {
        "column_count": len(domain_rows),
        "mean_global_candidates_per_column": sum(global_counts) / len(global_counts) if global_counts else 0.0,
        "mean_candidates_per_column": sum(counts) / len(counts) if counts else 0.0,
        "median_candidates_per_column": median(counts) if counts else 0.0,
        "max_candidates_per_column": max(counts) if counts else 0,
        "dominated_boundary_suppressed_total": sum(int(row.get("dominated_boundary_suppressed_count") or 0) for row in domain_rows),
        "text_strong_evidence_columns": len(text_strong),
        "text_strong_evidence_restricted_columns": len(restricted_text),
        "text_strong_evidence_unrestricted_columns": len(text_strong) - len(restricted_text),
        "by_semantic_materialization_type": {key: dict(value) for key, value in sorted(by_type.items())},
    }
