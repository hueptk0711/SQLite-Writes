from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from typing import Any

from nldbwrite_v3.ir import Diagnostic, VerificationResult
from nldbwrite_v3.schema import column_map, limited_identifier_match, table_map


CONFLICT_ACTIONS = {"error", "do_nothing", "do_update"}
WRITE_ACTIONS = {"insert"}


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _evidence_supports_value(
    evidence: Any,
    value: Any,
    instruction_text: str,
    column_profile: dict[str, Any],
) -> tuple[bool, str]:
    if not isinstance(evidence, dict):
        return False, "Evidence must be an object."
    source = str(evidence.get("source") or "")
    if source == "instruction_text":
        exact_span = str(evidence.get("exact_span") or "")
        if not exact_span:
            return False, "instruction_text evidence requires exact_span."
        if exact_span not in instruction_text:
            return False, "exact_span is not a verbatim span of instruction_text."
        if value is None and re.search(
            r"\b(?:null|none|nil)\b",
            _normalized_text(exact_span),
        ):
            return True, ""
        normalization_audit = evidence.get("normalization_audit")
        if isinstance(normalization_audit, dict):
            raw_value = normalization_audit.get("raw_value")
            normalized_value = normalization_audit.get("normalized_value")
            if (
                normalization_audit.get("lossless") is True
                and _normalized_text(raw_value)
                in _normalized_text(exact_span)
                and (
                    value == normalized_value
                    or _normalized_text(value)
                    == _normalized_text(normalized_value)
                )
            ):
                return True, ""
        normalized_value = _normalized_text(value)
        if normalized_value and normalized_value not in _normalized_text(exact_span):
            return False, "The claimed value does not occur inside exact_span."
        return True, ""
    if source == "schema_default":
        default_expression = column_profile.get("default")
        if default_expression is None:
            return False, "Column has no schema default."
        connection = sqlite3.connect(":memory:")
        try:
            expected = connection.execute(
                f"SELECT {default_expression}"
            ).fetchone()[0]
        except sqlite3.Error:
            expected = str(default_expression).strip("'\"")
        finally:
            connection.close()
        if value != expected and _normalized_text(value) != _normalized_text(expected):
            return False, "Value does not match the declared schema default."
        return True, ""
    return False, "Evidence source must be instruction_text or schema_default."


def _diagnostic(
    errors: list[Diagnostic],
    code: str,
    message: str,
    path: str,
    group_id: str | None = None,
    table: str | None = None,
    candidates: list[str] | None = None,
    **details: Any,
) -> None:
    errors.append(
        Diagnostic(
            error_code=code,
            message=message,
            path=path,
            group_id=group_id,
            table=table,
            candidates=candidates or [],
            details=details,
        )
    )


def _warning(
    warnings: list[Diagnostic],
    code: str,
    message: str,
    path: str,
    group_id: str | None = None,
    table: str | None = None,
    **details: Any,
) -> None:
    warnings.append(
        Diagnostic(
            error_code=code,
            message=message,
            severity="warning",
            path=path,
            group_id=group_id,
            table=table,
            details=details,
        )
    )


def _required_columns(table_profile: dict[str, Any]) -> list[str]:
    explicit = table_profile.get("required_insert_columns")
    if isinstance(explicit, list):
        return [str(item) for item in explicit]
    required = []
    for column in table_profile.get("columns", []):
        if not column.get("is_insertable", True):
            continue
        if not (column.get("not_null") or column.get("is_primary_key")):
            continue
        if column.get("default") is not None:
            continue
        required.append(str(column["name"]))
    return required


def _valid_conflict_target(
    target: list[str],
    table_profile: dict[str, Any],
) -> bool:
    if not target:
        return False
    target_tuple = tuple(target)
    return any(
        tuple(index.get("columns") or []) == target_tuple
        for index in table_profile.get("unique_indexes", [])
    )


def _normalize_columns(
    values: dict[str, Any],
    table_profile: dict[str, Any],
    errors: list[Diagnostic],
    path: str,
    group_id: str,
    table: str,
) -> dict[str, Any]:
    columns = column_map(table_profile)
    normalized: dict[str, Any] = {}
    for raw_name, value in values.items():
        match, candidates = limited_identifier_match(str(raw_name), columns)
        if not match:
            _diagnostic(
                errors,
                "UNKNOWN_COLUMN",
                f"{table}: unknown column {raw_name!r}.",
                f"{path}/{raw_name}",
                group_id,
                table,
                candidates,
                predicted_column=str(raw_name),
            )
            continue
        if not columns[match].get("is_insertable", True):
            _diagnostic(
                errors,
                "NON_INSERTABLE_COLUMN",
                f"{table}.{match} cannot be inserted.",
                f"{path}/{raw_name}",
                group_id,
                table,
            )
            continue
        if match in normalized:
            _diagnostic(
                errors,
                "DUPLICATE_COLUMN",
                f"Multiple predicted names resolve to {table}.{match}.",
                f"{path}/{raw_name}",
                group_id,
                table,
            )
            continue
        normalized[match] = value
    return normalized


def _normalize_column_list(
    values: Any,
    table_profile: dict[str, Any],
    errors: list[Diagnostic],
    path: str,
    group_id: str,
    table: str,
) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        _diagnostic(
            errors,
            "INVALID_COLUMN_LIST",
            "Expected a list of column names.",
            path,
            group_id,
            table,
        )
        return []
    columns = column_map(table_profile)
    normalized: list[str] = []
    for index, raw_name in enumerate(values):
        match, candidates = limited_identifier_match(str(raw_name), columns)
        if not match:
            _diagnostic(
                errors,
                "UNKNOWN_COLUMN",
                f"{table}: unknown column {raw_name!r}.",
                f"{path}/{index}",
                group_id,
                table,
                candidates,
                predicted_column=str(raw_name),
            )
        elif match not in normalized:
            normalized.append(match)
    return normalized


def _verify_provenance(
    group: dict[str, Any],
    table_profile: dict[str, Any],
    errors: list[Diagnostic],
    group_path: str,
    source_mode: str,
    instruction_text: str,
) -> set[tuple[str, int]]:
    group_id = str(group.get("group_id") or "")
    rows = group.get("rows") or []
    provenance = group.get("provenance")
    if source_mode != "semi_structured":
        return set()
    if not isinstance(provenance, list) or len(provenance) != len(rows):
        _diagnostic(
            errors,
            "PROVENANCE_MISMATCH",
            "Semi-structured rows require one provenance entry per target row.",
            f"{group_path}/provenance",
            group_id,
            str(group.get("table") or ""),
        )
        return set()
    source_rows: set[tuple[str, int]] = set()
    columns = column_map(table_profile)
    for row_index, (row, trace) in enumerate(zip(rows, provenance)):
        trace_path = f"{group_path}/provenance/{row_index}"
        source_collection = str(trace.get("source_collection") or "") if isinstance(trace, dict) else ""
        if (
            not isinstance(trace, dict)
            or not source_collection
            or not isinstance(trace.get("source_row_index"), int)
        ):
            _diagnostic(
                errors,
                "INVALID_PROVENANCE",
                (
                    "Provenance must include source_collection and an integer "
                    "source_row_index."
                ),
                trace_path,
                group_id,
            )
            continue
        source_rows.add((source_collection, trace["source_row_index"]))
        raw_value_sources = trace.get("value_sources") or {}
        value_sources: dict[str, Any] = {}
        if isinstance(raw_value_sources, dict):
            for raw_column, source in raw_value_sources.items():
                matched, _ = limited_identifier_match(
                    str(raw_column),
                    columns,
                )
                if matched and matched not in value_sources:
                    value_sources[matched] = source
        trace["value_sources"] = value_sources
        for column in row:
            source = value_sources.get(column)
            if not isinstance(source, dict):
                _diagnostic(
                    errors,
                    "MISSING_VALUE_PROVENANCE",
                    f"No source provenance for target column {column}.",
                    f"{trace_path}/value_sources/{column}",
                    group_id,
                )
                continue
            if source.get("kind") == "source":
                if (
                    not str(source.get("source_collection") or "")
                    or not isinstance(source.get("source_row_index"), int)
                    or not str(source.get("source_field") or "")
                ):
                    _diagnostic(
                        errors,
                        "INVALID_VALUE_PROVENANCE",
                        f"Source provenance for {column} is incomplete.",
                        f"{trace_path}/value_sources/{column}",
                        group_id,
                    )
            elif source.get("kind") == "constant":
                supported, message = _evidence_supports_value(
                    source.get("evidence"),
                    row[column],
                    instruction_text,
                    columns.get(column, {}),
                )
                if not supported:
                    _diagnostic(
                        errors,
                        "UNSUPPORTED_CONSTANT",
                        f"Constant value for {column} is unsupported: {message}",
                        f"{trace_path}/value_sources/{column}",
                        group_id,
                    )
            else:
                _diagnostic(
                    errors,
                    "INVALID_VALUE_PROVENANCE",
                    f"Unknown provenance kind for {column}.",
                    f"{trace_path}/value_sources/{column}",
                    group_id,
                )
    return source_rows


def _verify_free_text_evidence(
    group: dict[str, Any],
    table_profile: dict[str, Any],
    errors: list[Diagnostic],
    group_path: str,
    instruction_text: str,
) -> None:
    rows = group.get("rows") or []
    raw_evidence = group.get("value_evidence")
    if isinstance(raw_evidence, dict) and len(rows) == 1:
        evidence_rows = [raw_evidence]
    else:
        evidence_rows = raw_evidence
    if not isinstance(evidence_rows, list) or len(evidence_rows) != len(rows):
        _diagnostic(
            errors,
            "VALUE_EVIDENCE_MISMATCH",
            "Free-text plans require one value_evidence object per row.",
            f"{group_path}/value_evidence",
            str(group.get("group_id") or ""),
            str(group.get("table") or ""),
        )
        return
    columns = column_map(table_profile)
    for row_index, (row, evidence_map) in enumerate(zip(rows, evidence_rows)):
        if not isinstance(evidence_map, dict):
            _diagnostic(
                errors,
                "INVALID_VALUE_EVIDENCE",
                "value_evidence row must be an object.",
                f"{group_path}/value_evidence/{row_index}",
                str(group.get("group_id") or ""),
                str(group.get("table") or ""),
            )
            continue
        normalized_evidence: dict[str, Any] = {}
        for raw_column, evidence in evidence_map.items():
            matched, _ = limited_identifier_match(str(raw_column), columns)
            if matched:
                normalized_evidence[matched] = evidence
        for column, value in row.items():
            evidence = normalized_evidence.get(column)
            supported, message = _evidence_supports_value(
                evidence,
                value,
                instruction_text,
                columns.get(column, {}),
            )
            if not supported:
                _diagnostic(
                    errors,
                    "UNSUPPORTED_EXTRACTED_VALUE",
                    f"{column}: {message}",
                    f"{group_path}/value_evidence/{row_index}/{column}",
                    str(group.get("group_id") or ""),
                    str(group.get("table") or ""),
                )


def _verify_dependencies(
    plan: dict[str, Any],
    errors: list[Diagnostic],
) -> None:
    groups = plan.get("write_groups") or []
    group_ids = {str(group.get("group_id")) for group in groups}
    edges: dict[str, set[str]] = {group_id: set() for group_id in group_ids}
    dependencies = plan.get("dependencies") or []
    if not isinstance(dependencies, list):
        _diagnostic(
            errors,
            "INVALID_DEPENDENCIES",
            "dependencies must be a list.",
            "/dependencies",
        )
        return
    for index, dependency in enumerate(dependencies):
        path = f"/dependencies/{index}"
        if not isinstance(dependency, dict):
            _diagnostic(
                errors,
                "INVALID_DEPENDENCY",
                "Dependency must be an object.",
                path,
            )
            continue
        before = str(dependency.get("before") or "")
        after = str(dependency.get("after") or "")
        if before not in group_ids or after not in group_ids:
            _diagnostic(
                errors,
                "UNKNOWN_DEPENDENCY_GROUP",
                f"Dependency references unknown groups: {before!r} -> {after!r}.",
                path,
            )
            continue
        if before == after:
            _diagnostic(
                errors,
                "DEPENDENCY_CYCLE",
                f"Group {before!r} depends on itself.",
                path,
            )
            continue
        edges[before].add(after)
    state: dict[str, int] = {group_id: 0 for group_id in group_ids}

    def visit(node: str) -> bool:
        state[node] = 1
        for child in edges[node]:
            if state[child] == 1:
                return True
            if state[child] == 0 and visit(child):
                return True
        state[node] = 2
        return False

    if any(state[node] == 0 and visit(node) for node in sorted(group_ids)):
        _diagnostic(
            errors,
            "DEPENDENCY_CYCLE",
            "Dependency graph contains a cycle.",
            "/dependencies",
        )


def verify_write_plan(
    write_plan: dict[str, Any],
    profile: dict[str, Any],
    *,
    check_provenance: bool = True,
) -> VerificationResult:
    """Validate and normalize a materialized Write Plan without semantic guessing.

    ``check_provenance=False`` exists only for the frozen-plan downstream
    ablation. Production callers retain the fail-closed default. It disables
    source/evidence lineage checks but keeps schema, required-column,
    conflict, dependency, and program-shape checks unchanged.
    """
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    if not isinstance(write_plan, dict):
        return VerificationResult(
            "invalid",
            None,
            [Diagnostic("INVALID_PLAN", "Write Plan must be an object.")],
            [],
        )
    plan = deepcopy(write_plan)
    groups = plan.get("write_groups")
    if not isinstance(groups, list) or not groups:
        return VerificationResult(
            "invalid",
            None,
            [
                Diagnostic(
                    "MISSING_WRITE_GROUPS",
                    "Write Plan must contain a non-empty write_groups list.",
                    path="/write_groups",
                )
            ],
            [],
        )
    tables = table_map(profile)
    seen_group_ids: set[str] = set()
    covered_source_rows: set[tuple[str, int]] = set()
    source = plan.get("source") or {}
    source_mode = str(source.get("mode") or "free_text")
    instruction_text = str(source.get("instruction_text") or "")
    evidence_required = bool(source.get("evidence_required")) or (
        plan.get("plan_kind") == "free_text_write_plan"
    )
    for index, group in enumerate(groups):
        group_path = f"/write_groups/{index}"
        if not isinstance(group, dict):
            _diagnostic(
                errors,
                "INVALID_WRITE_GROUP",
                "Write group must be an object.",
                group_path,
            )
            continue
        group_id = str(group.get("group_id") or f"g{index + 1}")
        group["group_id"] = group_id
        if group_id in seen_group_ids:
            _diagnostic(
                errors,
                "DUPLICATE_GROUP_ID",
                f"Duplicate group_id {group_id!r}.",
                f"{group_path}/group_id",
                group_id,
            )
        seen_group_ids.add(group_id)

        raw_table = group.get("table")
        matched_table, table_candidates = limited_identifier_match(raw_table, tables)
        if not matched_table:
            _diagnostic(
                errors,
                "UNKNOWN_TABLE",
                f"Unknown table {raw_table!r}.",
                f"{group_path}/table",
                group_id,
                candidates=table_candidates,
                predicted_table=raw_table,
            )
            continue
        group["table"] = matched_table
        table_profile = tables[matched_table]
        action = str(group.get("action") or "insert").lower()
        if action not in WRITE_ACTIONS:
            _diagnostic(
                errors,
                "UNSUPPORTED_WRITE_ACTION",
                f"Unsupported write action {action!r}; v3 currently accepts insert.",
                f"{group_path}/action",
                group_id,
                matched_table,
            )
        group["action"] = action

        rows = group.get("rows")
        if not isinstance(rows, list) or not rows:
            _diagnostic(
                errors,
                "MISSING_ROWS",
                "Write group must contain at least one row.",
                f"{group_path}/rows",
                group_id,
                matched_table,
            )
            continue
        normalized_rows: list[dict[str, Any]] = []
        row_fingerprints: set[str] = set()
        required = _required_columns(table_profile)
        for row_index, row in enumerate(rows):
            row_path = f"{group_path}/rows/{row_index}"
            if not isinstance(row, dict) or not row:
                _diagnostic(
                    errors,
                    "INVALID_ROW",
                    "Each row must be a non-empty object.",
                    row_path,
                    group_id,
                    matched_table,
                )
                normalized_rows.append({})
                continue
            normalized = _normalize_columns(
                row,
                table_profile,
                errors,
                row_path,
                group_id,
                matched_table,
            )
            missing = [
                column
                for column in required
                if column not in normalized or normalized[column] is None
            ]
            if missing:
                _diagnostic(
                    errors,
                    "MISSING_REQUIRED_COLUMN",
                    f"{matched_table}: missing required columns: {', '.join(missing)}.",
                    row_path,
                    group_id,
                    matched_table,
                    missing_columns=missing,
                )
            fingerprint = json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if fingerprint in row_fingerprints:
                _warning(
                    warnings,
                    "DUPLICATE_ROW",
                    (
                        f"Duplicate row in group {group_id}; it is preserved "
                        "because duplicate payload rows may be intentional."
                    ),
                    row_path,
                    group_id,
                    matched_table,
                )
            row_fingerprints.add(fingerprint)
            normalized_rows.append(normalized)
        group["rows"] = normalized_rows

        conflict = group.get("conflict")
        if not isinstance(conflict, dict):
            _diagnostic(
                errors,
                "MISSING_CONFLICT_POLICY",
                "Every write group requires an explicit conflict object.",
                f"{group_path}/conflict",
                group_id,
                matched_table,
            )
            conflict = {}
        conflict_action = str(conflict.get("action") or "").lower()
        if conflict_action not in CONFLICT_ACTIONS:
            _diagnostic(
                errors,
                "INVALID_CONFLICT_ACTION",
                "Conflict action must be error, do_nothing, or do_update.",
                f"{group_path}/conflict/action",
                group_id,
                matched_table,
            )
        target = _normalize_column_list(
            conflict.get("target"),
            table_profile,
            errors,
            f"{group_path}/conflict/target",
            group_id,
            matched_table,
        )
        update_columns = _normalize_column_list(
            conflict.get("update_columns"),
            table_profile,
            errors,
            f"{group_path}/conflict/update_columns",
            group_id,
            matched_table,
        )
        if target and not _valid_conflict_target(target, table_profile):
            _diagnostic(
                errors,
                "INVALID_CONFLICT_TARGET",
                f"{matched_table}: conflict target is not a PK/UNIQUE index.",
                f"{group_path}/conflict/target",
                group_id,
                matched_table,
                target=target,
                valid_targets=[
                    item.get("columns")
                    for item in table_profile.get("unique_indexes", [])
                ],
            )
        if conflict_action == "do_update":
            if not target:
                _diagnostic(
                    errors,
                    "MISSING_CONFLICT_TARGET",
                    "do_update requires an explicit PK/UNIQUE target.",
                    f"{group_path}/conflict/target",
                    group_id,
                    matched_table,
                )
            update_columns = [
                column for column in update_columns if column not in target
            ]
            row_columns = {
                column for row in normalized_rows for column in row
            }
            available_updates = [
                column for column in row_columns if column not in target
            ]
            if not update_columns and not available_updates:
                conflict_action = "do_nothing"
                _warning(
                    warnings,
                    "KEY_ONLY_UPSERT_DOWNGRADED",
                    "Key-only do_update was normalized to do_nothing.",
                    f"{group_path}/conflict/action",
                    group_id,
                    matched_table,
                )
            elif not update_columns:
                _diagnostic(
                    errors,
                    "MISSING_UPDATE_COLUMNS",
                    "do_update requires explicit update_columns.",
                    f"{group_path}/conflict/update_columns",
                    group_id,
                    matched_table,
                    available_columns=available_updates,
                )
            missing_updates = [
                column
                for column in update_columns
                if any(column not in row for row in normalized_rows)
            ]
            if missing_updates:
                _warning(
                    warnings,
                    "UPDATE_COLUMN_MISSING_VALUE",
                    (
                        "Every do_update column must be supplied by every row; "
                        "otherwise SQLite would overwrite existing data with "
                        "an inserted default/NULL value. Preflight must fail "
                        "closed; use an explicit null when that overwrite is "
                        "intended."
                    ),
                    f"{group_path}/conflict/update_columns",
                    group_id,
                    matched_table,
                    missing_columns=missing_updates,
                )
        elif conflict_action == "do_nothing" and update_columns:
            _diagnostic(
                errors,
                "UNEXPECTED_UPDATE_COLUMNS",
                "do_nothing must not declare update_columns.",
                f"{group_path}/conflict/update_columns",
                group_id,
                matched_table,
            )
        elif conflict_action == "error" and (target or update_columns):
            _warning(
                warnings,
                "UNUSED_CONFLICT_FIELDS",
                "Conflict target/update columns are ignored when action=error.",
                f"{group_path}/conflict",
                group_id,
                matched_table,
            )
            target = []
            update_columns = []
        group["conflict"] = {
            "action": conflict_action,
            "target": target,
            "update_columns": update_columns,
        }
        reference_trace = group.get("reference_trace")
        if isinstance(reference_trace, dict):
            cross_table_signals = reference_trace.get(
                "cross_table_evidence_signals"
            )
            if isinstance(cross_table_signals, list):
                for signal_index, signal in enumerate(
                    cross_table_signals
                ):
                    if not isinstance(signal, dict):
                        continue
                    _warning(
                        warnings,
                        "EVIDENCE_COLUMN_TABLE_MISMATCH",
                        (
                            "An evidence value is immediately labelled with "
                            "an exact column identifier owned by another "
                            "table; execution must fail closed at preflight."
                        ),
                        (
                            f"{group_path}/reference_trace/"
                            "cross_table_evidence_signals/"
                            f"{signal_index}"
                        ),
                        group_id,
                        matched_table,
                        **deepcopy(signal),
                    )
            ambiguous_signals = reference_trace.get(
                "ambiguous_evidence_grounding_signals"
            )
            if isinstance(ambiguous_signals, list):
                for signal_index, signal in enumerate(
                    ambiguous_signals
                ):
                    if not isinstance(signal, dict):
                        continue
                    _warning(
                        warnings,
                        "AMBIGUOUS_EXACT_EVIDENCE_COLUMN_GROUNDING",
                        (
                            "Exact evidence grounding conflicts with another "
                            "cell mapping; execution must fail closed at "
                            "preflight."
                        ),
                        (
                            f"{group_path}/reference_trace/"
                            "ambiguous_evidence_grounding_signals/"
                            f"{signal_index}"
                        ),
                        group_id,
                        matched_table,
                        **deepcopy(signal),
                    )
        if check_provenance:
            covered_source_rows.update(
                _verify_provenance(
                    group,
                    table_profile,
                    errors,
                    group_path,
                    source_mode,
                    instruction_text,
                )
            )
        if check_provenance and source_mode == "free_text" and evidence_required:
            _verify_free_text_evidence(
                group,
                table_profile,
                errors,
                group_path,
                instruction_text,
            )

    unresolved = plan.get("unresolved_fields") or []
    if check_provenance and not isinstance(unresolved, list):
        _diagnostic(
            errors,
            "INVALID_UNRESOLVED_FIELDS",
            "unresolved_fields must be a list.",
                    "/unresolved_fields",
        )
    elif check_provenance:
        for index, item in enumerate(unresolved):
            if not isinstance(item, dict):
                _diagnostic(
                    errors,
                    "INVALID_UNRESOLVED_FIELD",
                    "Unresolved field entry must be an object.",
                    f"/unresolved_fields/{index}",
                )
                continue
            status = str(item.get("status") or "")
            ignored_ok = status == "ignored" and bool(
                str(item.get("reason") or "").strip()
            )
            consumed_control_ok = (
                status == "consumed_control"
                and bool(str(item.get("role") or "").strip())
                and bool(str(item.get("consumed_by") or "").strip())
            )
            if not (ignored_ok or consumed_control_ok):
                _diagnostic(
                    errors,
                    "UNRESOLVED_SOURCE_FIELD",
                    f"Source field {item.get('field')!r} is neither mapped, justified, nor consumed as typed control semantics.",
                    f"/unresolved_fields/{index}",
                    source_collection=item.get("source_collection"),
                    source_row_index=item.get("source_row_index"),
                    role=item.get("role"),
                    consumed_by=item.get("consumed_by"),
                )

    source_collections = source.get("collections")
    if (
        check_provenance
        and source_mode == "semi_structured"
        and isinstance(source_collections, list)
    ):
        expected: set[tuple[str, int]] = set()
        for collection in source_collections:
            if not isinstance(collection, dict):
                continue
            collection_id = str(collection.get("collection_id") or "")
            row_count = collection.get("row_count")
            if collection_id and isinstance(row_count, int):
                expected.update(
                    (collection_id, row_index)
                    for row_index in range(row_count)
                )
        if covered_source_rows != expected:
            _diagnostic(
                errors,
                "SOURCE_ROW_COVERAGE_MISMATCH",
                "Materialized plan does not preserve every source row.",
                "/write_groups",
                missing_source_rows=[
                    {"source_collection": collection, "source_row_index": index}
                    for collection, index in sorted(
                        expected - covered_source_rows
                    )
                ],
                unexpected_source_rows=[
                    {"source_collection": collection, "source_row_index": index}
                    for collection, index in sorted(
                        covered_source_rows - expected
                    )
                ],
            )

    _verify_dependencies(plan, errors)
    return VerificationResult(
        status="invalid" if errors else "valid",
        normalized_plan=None if errors else plan,
        errors=errors,
        warnings=warnings,
    )
