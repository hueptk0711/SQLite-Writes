from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from nldbwrite_v3.compiler import apply_declared_normalization
from nldbwrite_v3.ir import Diagnostic
from nldbwrite_v3.vnext.typed_normalization import (
    FreeTextTypedNormalizationConfig,
    normalize_free_text_typed_candidate,
)
from nldbwrite_v3.schema import (
    column_reference_map,
    ensure_reference_ids,
    table_reference_map,
)

from .materialize import MaterializationError
from .references import resolve_reference_policy


_QUOTED = re.compile(r"""(?P<quote>["'])(?P<value>.+?)(?P=quote)""")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(r"\bhttps?://[^\s,;]+", re.IGNORECASE)
_DATE = re.compile(
    r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}[-/]\d{1,2}[-/]\d{4})\b"
)
_DATETIME = re.compile(
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"[ T]\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b"
)
_LITERAL = re.compile(
    r"\b(?:true|false|yes|no|null|none)\b",
    re.IGNORECASE,
)
_NUMBER_OR_ID = re.compile(
    r"(?<!\w)[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?|[A-Za-z][A-Za-z_-]*\d[\w.-]*)(?!\w)"
)
_LABEL_VALUE = re.compile(
    r"(?i)\b(?:named|called|name|description|described\s+as|note|"
    r"address|code|identifier|id|date|amount|fee|score)\s*"
    r"(?:is|to|=|:)?\s+(?P<value>[^,;.\r\n]+?)"
    r"(?=\s+(?:and|or|when|if|unless|named|called)\b|[,;.\r\n]|$)"
)
_ASSIGNMENT_VALUE = re.compile(
    r"(?i)\b(?:set\s+)?[A-Za-z_][\w.-]*"
    r"(?:\s+[A-Za-z_][\w.-]*){0,2}\s+"
    r"(?:is|to|=|:)\s+(?P<value>[^,;.\r\n]+?)"
    r"(?=\s+(?:and|or|when|if|unless)\b|[,;.\r\n]|$)"
)
_CAPITALIZED_PHRASE = re.compile(
    r"\b(?:[A-ZÀ-Ỹ][\w'’-]*|[A-Z]*\d[\w.-]*)"
    r"(?:\s+(?:[A-ZÀ-Ỹ][\w'’-]*|[A-Z]*\d[\w.-]*)){1,4}\b",
    re.UNICODE,
)
_TOKEN = re.compile(r"\b[\w@.+/'’-]{2,}\b", re.UNICODE)
_CONTROL_TOKENS = {
    "add",
    "create",
    "id",
    "insert",
    "load",
    "process",
    "reading",
    "record",
    "request",
    "row",
    "set",
    "update",
}


def _candidate_type(text: str) -> str:
    stripped = text.strip()
    if _EMAIL.fullmatch(stripped):
        return "email"
    if _URL.fullmatch(stripped):
        return "url"
    if _DATETIME.fullmatch(stripped):
        return "datetime"
    if _DATE.fullmatch(stripped):
        return "date"
    if _NUMBER_OR_ID.fullmatch(stripped):
        return "number_or_identifier"
    if stripped.casefold() in {"true", "false", "yes", "no", "null", "none"}:
        return "literal"
    return "text"


def extract_evidence_candidates(
    text: str,
    *,
    max_candidates: int = 128,
) -> list[dict[str, Any]]:
    """Enumerate verbatim request spans; no candidate text is generated."""
    spans: dict[tuple[int, int], tuple[str, int]] = {}

    def add(
        start: int,
        end: int,
        candidate_type: str | None = None,
        *,
        priority: int,
    ) -> None:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end:
            return
        value = text[start:end]
        if len(value) > 160:
            return
        selected_type = candidate_type or _candidate_type(value)
        previous = spans.get((start, end))
        if previous is None or priority > previous[1]:
            spans[(start, end)] = (selected_type, priority)

    for match in _QUOTED.finditer(text):
        add(
            match.start("value"),
            match.end("value"),
            "quoted_text",
            priority=100,
        )
    for pattern in (
        _EMAIL,
        _URL,
        _DATETIME,
        _DATE,
        _LITERAL,
        _NUMBER_OR_ID,
    ):
        for match in pattern.finditer(text):
            add(match.start(), match.end(), priority=90)
    for match in _LABEL_VALUE.finditer(text):
        add(
            match.start("value"),
            match.end("value"),
            "label_value",
            priority=60,
        )
    for match in _ASSIGNMENT_VALUE.finditer(text):
        add(
            match.start("value"),
            match.end("value"),
            "label_value",
            priority=60,
        )
    for match in _CAPITALIZED_PHRASE.finditer(text):
        add(
            match.start(),
            match.end(),
            "text_phrase",
            priority=40,
        )
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if any(char.isdigit() for char in token) or (
            token[:1].isupper() and len(token) <= 64
        ):
            if token.casefold() not in _CONTROL_TOKENS:
                add(match.start(), match.end(), priority=10)

    selected: list[tuple[tuple[int, int], tuple[str, int]]] = []
    for candidate_span, candidate_meta in spans.items():
        start, end = candidate_span
        _, priority = candidate_meta
        redundant = False
        for other_span, other_meta in spans.items():
            if other_span == candidate_span:
                continue
            other_start, other_end = other_span
            other_type, other_priority = other_meta
            contains_other = (
                start <= other_start
                and end >= other_end
                and (end - start) > (other_end - other_start)
            )
            if (
                priority < 90
                and contains_other
                and other_priority >= 90
                and other_type != "literal"
            ):
                redundant = True
                break
        if not redundant:
            selected.append((candidate_span, candidate_meta))

    if len(selected) > max_candidates:
        selected = sorted(
            selected,
            key=lambda item: (
                -item[1][1],
                item[0][0],
                -(item[0][1] - item[0][0]),
            ),
        )[:max_candidates]
    def is_component(
        item: tuple[tuple[int, int], tuple[str, int]],
    ) -> bool:
        (start, end), (_, priority) = item
        return any(
            other_start <= start
            and other_end >= end
            and (other_end - other_start) > (end - start)
            and other_priority > priority
            for (other_start, other_end), (_, other_priority) in selected
        )

    ordered = sorted(
        selected,
        key=lambda item: (
            is_component(item),
            item[0][0],
            -(item[0][1] - item[0][0]),
            item[1][0],
        ),
    )

    def local_context(start: int) -> str:
        boundary = max(
            text.rfind(";", 0, start),
            text.rfind("\n", 0, start),
            text.rfind("(", 0, start),
        )
        context = " ".join(text[boundary + 1 : start].split())
        return context[-80:]

    return [
        {
            "evidence_id": f"e{index}",
            "text": text[start:end],
            "start": start,
            "end": end,
            "candidate_type": candidate_type,
            "candidate_role": (
                "component"
                if is_component(
                    ((start, end), (candidate_type, _priority))
                )
                else "primary"
            ),
            **(
                {"left_context": local_context(start)}
                if local_context(start)
                else {}
            ),
        }
        for index, ((start, end), (candidate_type, _priority)) in enumerate(
            ordered,
            start=1,
        )
    ]


def _explicit_column_before_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    table_profile: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve an immediately preceding exact schema identifier, if any."""
    context = str(candidate.get("left_context") or "")
    if not context:
        return None, []
    matches: list[tuple[int, int, str]] = []
    for table in profile.get("tables", []):
        if not isinstance(table, dict):
            continue
        for column in table.get("columns", []):
            if not isinstance(column, dict) or not column.get("name"):
                continue
            name = str(column["name"])
            for match in re.finditer(
                rf"(?<![\w]){re.escape(name)}(?![\w])",
                context,
                re.IGNORECASE,
            ):
                suffix = context[match.end() :]
                if re.fullmatch(
                    r"\s*(?:(?:=|:)|(?:is|to|as|of|value))?\s*",
                    suffix,
                    re.IGNORECASE,
                ):
                    matches.append((match.end(), match.start(), name))
    if not matches:
        return None, []
    nearest_end = max(item[0] for item in matches)
    nearest_start = max(
        item[1] for item in matches if item[0] == nearest_end
    )
    names = {
        item[2].casefold()
        for item in matches
        if item[0] == nearest_end and item[1] == nearest_start
    }
    if len(names) != 1:
        return None, []
    selected_name = next(iter(names))
    local = [
        column
        for column in table_profile.get("columns", [])
        if isinstance(column, dict)
        and str(column.get("name") or "").casefold() == selected_name
    ]
    if len(local) == 1:
        return local[0], []
    owners = sorted(
        {
            str(table.get("name") or "")
            for table in profile.get("tables", [])
            if isinstance(table, dict)
            and any(
                isinstance(column, dict)
                and str(column.get("name") or "").casefold()
                == selected_name
                for column in table.get("columns", [])
            )
        }
    )
    return None, owners


def materialize_reference_free_text_plan(
    reference_plan: dict[str, Any],
    request: str,
    profile: dict[str, Any],
    *,
    free_text_typed_normalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve evidence/schema IDs and copy verbatim spans into a Write Plan."""
    ensure_reference_ids(profile)
    typed_normalization_config = FreeTextTypedNormalizationConfig.from_mapping(
        free_text_typed_normalization
    )
    errors: list[Diagnostic] = []
    tables = table_reference_map(profile)
    candidates = extract_evidence_candidates(request)
    evidence = {str(item["evidence_id"]): item for item in candidates}
    output_groups: list[dict[str, Any]] = []
    groups = reference_plan.get("write_groups") or []
    if not isinstance(groups, list) or not groups:
        raise MaterializationError(
            [
                Diagnostic(
                    "MISSING_WRITE_GROUPS",
                    "Reference plan requires non-empty write_groups.",
                    path="/write_groups",
                )
            ]
        )

    for group_index, group in enumerate(groups):
        path = f"/write_groups/{group_index}"
        if not isinstance(group, dict):
            errors.append(
                Diagnostic(
                    "INVALID_WRITE_GROUP",
                    "Write group must be an object.",
                    path=path,
                )
            )
            continue
        group_id = str(group.get("group_id") or f"g{group_index + 1}")
        table_id = str(group.get("table_id") or "")
        table_profile = tables.get(table_id)
        if table_profile is None:
            errors.append(
                Diagnostic(
                    "UNKNOWN_TABLE_ID",
                    f"Unknown enumerated table ID {table_id!r}.",
                    path=f"{path}/table_id",
                    group_id=group_id,
                    candidates=sorted(tables),
                )
            )
            continue
        columns = column_reference_map(table_profile)
        rows = group.get("rows") or []
        if not isinstance(rows, list) or not rows:
            errors.append(
                Diagnostic(
                    "MISSING_ROWS",
                    "Reference group requires at least one row.",
                    path=f"{path}/rows",
                    group_id=group_id,
                )
            )
            continue
        output_rows: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        normalization_rows: list[dict[str, Any]] = []
        column_id_remaps: dict[str, str] = {}
        evidence_column_groundings: list[dict[str, Any]] = []
        cross_table_evidence_signals: list[dict[str, Any]] = []
        ambiguous_evidence_grounding_signals: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            row_path = f"{path}/rows/{row_index}"
            if not isinstance(row, dict) or not row:
                errors.append(
                    Diagnostic(
                        "INVALID_ROW",
                        "Each reference row must be a non-empty object.",
                        path=row_path,
                        group_id=group_id,
                    )
                )
                continue
            output_row: dict[str, Any] = {}
            evidence_row: dict[str, Any] = {}
            normalization_row: dict[str, Any] = {}
            for column_id, value_spec in row.items():
                column = columns.get(str(column_id))
                if column is None:
                    errors.append(
                        Diagnostic(
                            "UNKNOWN_COLUMN_ID",
                            f"Unknown enumerated column ID {column_id!r}.",
                            path=f"{row_path}/{column_id}",
                            group_id=group_id,
                            candidates=sorted(columns),
                        )
                    )
                    continue
                predicted_column = column
                if not isinstance(value_spec, dict):
                    errors.append(
                        Diagnostic(
                            "INVALID_EVIDENCE_REFERENCE",
                            "Cell value must use {value_from, normalization}.",
                            path=f"{row_path}/{column_id}",
                            group_id=group_id,
                        )
                    )
                    continue
                evidence_id = str(value_spec.get("value_from") or "")
                candidate = evidence.get(evidence_id)
                if candidate is None:
                    errors.append(
                        Diagnostic(
                            "UNKNOWN_EVIDENCE_ID",
                            f"Unknown enumerated evidence ID {evidence_id!r}.",
                            path=f"{row_path}/{column_id}/value_from",
                            group_id=group_id,
                            candidates=sorted(evidence),
                        )
                    )
                    continue
                explicit_column, other_table_owners = (
                    _explicit_column_before_candidate(
                        candidate,
                        profile,
                        table_profile,
                    )
                )
                if explicit_column is None and other_table_owners:
                    cross_table_evidence_signals.append(
                        {
                            "row_index": row_index,
                            "evidence_id": evidence_id,
                            "predicted_column_id": str(column_id),
                            "predicted_table": str(
                                table_profile.get("name") or ""
                            ),
                            "explicit_column_tables": other_table_owners,
                            "reason": (
                                "immediately_preceding_exact_identifier"
                            ),
                        }
                    )
                if explicit_column is not None:
                    predicted_column_id = str(column_id)
                    explicit_column_id = str(
                        explicit_column.get("column_id") or ""
                    )
                    explicit_column_name = str(explicit_column["name"])
                    if (
                        explicit_column_id != predicted_column_id
                        and explicit_column_name in output_row
                    ):
                        ambiguous_evidence_grounding_signals.append(
                            {
                                "row_index": row_index,
                                "evidence_id": evidence_id,
                                "predicted_column_id": predicted_column_id,
                                "explicit_column_id": explicit_column_id,
                                "explicit_column": explicit_column_name,
                                "reason": (
                                    "exact_identifier_target_already_mapped"
                                ),
                            }
                        )
                        explicit_column = None
                        column = predicted_column
                    else:
                        previous = column_id_remaps.get(predicted_column_id)
                        if (
                            previous is not None
                            and previous != explicit_column_id
                        ):
                            ambiguous_evidence_grounding_signals.append(
                                {
                                    "row_index": row_index,
                                    "evidence_id": evidence_id,
                                    "predicted_column_id": (
                                        predicted_column_id
                                    ),
                                    "prior_explicit_column_id": previous,
                                    "explicit_column_id": explicit_column_id,
                                    "reason": (
                                        "one_predicted_column_has_multiple_"
                                        "exact_identifiers"
                                    ),
                                }
                            )
                            explicit_column = None
                            column = predicted_column
                        else:
                            column_id_remaps[
                                predicted_column_id
                            ] = explicit_column_id
                            if explicit_column_id != predicted_column_id:
                                evidence_column_groundings.append(
                                    {
                                        "row_index": row_index,
                                        "evidence_id": evidence_id,
                                        "from_column_id": predicted_column_id,
                                        "to_column_id": explicit_column_id,
                                        "to_column": explicit_column_name,
                                        "reason": (
                                            "immediately_preceding_exact_"
                                            "identifier"
                                        ),
                                    }
                                )
                            column = explicit_column
                rule = str(value_spec.get("normalization") or "identity")
                raw_value = candidate["text"]
                typed_result = normalize_free_text_typed_candidate(
                    raw_value,
                    column,
                    requested_rule=rule,
                    candidate_type=str(candidate.get("candidate_type") or ""),
                    config=typed_normalization_config,
                    evidence_id=evidence_id,
                    evidence_start=candidate.get("start"),
                    evidence_end=candidate.get("end"),
                )
                if typed_result.handled:
                    normalized = typed_result.value
                    audit = deepcopy(typed_result.audit)
                    error = typed_result.error
                    if error is not None:
                        errors.append(
                            Diagnostic(
                                "TYPED_NORMALIZATION_REJECTED",
                                error,
                                path=f"{row_path}/{column_id}/normalization",
                                group_id=group_id,
                                details={
                                    "evidence_id": evidence_id,
                                    "normalization_rule": rule,
                                    "raw_value": raw_value,
                                    "typed_error_code": typed_result.error_code,
                                    "candidate_type": candidate.get("candidate_type"),
                                },
                            )
                        )
                        continue
                else:
                    normalized, audit, error = apply_declared_normalization(
                        raw_value,
                        column,
                        rule,
                    )
                    if error is not None:
                        errors.append(
                            Diagnostic(
                                "LOSSY_NORMALIZATION_REJECTED",
                                error,
                                path=f"{row_path}/{column_id}/normalization",
                                group_id=group_id,
                                details={
                                    "evidence_id": evidence_id,
                                    "normalization_rule": rule,
                                    "raw_value": raw_value,
                                },
                            )
                        )
                        continue
                column_name = str(column["name"])
                if column_name in output_row:
                    errors.append(
                        Diagnostic(
                            "DUPLICATE_TARGET_COLUMN_AFTER_EVIDENCE_GROUNDING",
                            (
                                f"Multiple cells resolve to target column "
                                f"{column_name!r}."
                            ),
                            path=f"{row_path}/{column_id}",
                            group_id=group_id,
                        )
                    )
                    continue
                output_row[column_name] = normalized
                evidence_row[column_name] = {
                    "source": "instruction_text",
                    "exact_span": raw_value,
                    "evidence_id": evidence_id,
                    "normalization_audit": deepcopy(audit),
                }
                normalization_row[column_name] = deepcopy(audit)
            output_rows.append(output_row)
            evidence_rows.append(evidence_row)
            normalization_rows.append(normalization_row)

        policy_group = deepcopy(group)
        policy_group["update_column_ids"] = [
            column_id_remaps.get(str(column_id), str(column_id))
            for column_id in group.get("update_column_ids") or []
        ]
        policy, policy_errors = resolve_reference_policy(
            policy_group,
            table_profile,
            path=path,
            group_id=group_id,
        )
        errors.extend(policy_errors)
        output_groups.append(
            {
                "group_id": group_id,
                "table": str(table_profile["name"]),
                "action": "insert",
                "rows": output_rows,
                "value_evidence": evidence_rows,
                "normalization_audit": normalization_rows,
                "conflict": policy,
                "reference_trace": {
                    "table_id": table_id,
                    "write_semantics": policy_group.get("write_semantics"),
                    "conflict_target_id": policy_group.get(
                        "conflict_target_id"
                    ),
                    "update_column_ids": deepcopy(
                        policy_group.get("update_column_ids") or []
                    ),
                    "stage2_intervention_trace": deepcopy(
                        policy_group.get("stage2_intervention_trace") or {}
                    ),
                    "evidence_column_groundings": (
                        evidence_column_groundings
                    ),
                    "cross_table_evidence_signals": (
                        cross_table_evidence_signals
                    ),
                    "ambiguous_evidence_grounding_signals": (
                        ambiguous_evidence_grounding_signals
                    ),
                },
            }
        )
    if errors:
        raise MaterializationError(errors)
    return {
        "version": "3.0",
        "plan_kind": "free_text_write_plan",
        "reference_contract": "mp-fs-plus-v1",
        "source": {
            "mode": "free_text",
            "format": "free_text",
            "instruction_text": request,
            "row_count": 0,
            "collections": [],
            "evidence_required": True,
            "evidence_candidates": candidates,
        },
        "write_groups": output_groups,
        "dependencies": deepcopy(reference_plan.get("dependencies") or []),
        "unresolved_fields": deepcopy(
            reference_plan.get("unresolved_fields") or []
        ),
    }
