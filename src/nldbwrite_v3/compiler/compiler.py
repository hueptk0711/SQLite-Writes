from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from nldbwrite_v3.ir import CompiledProgram, CompiledStatement, Diagnostic
from nldbwrite_v3.schema import column_map, table_map
from nldbwrite_v3.schema.profile import quote_identifier
from nldbwrite_v3.verifier import verify_write_plan

from .normalization import normalize_value_lossless


_INTEGER = re.compile(r"^[+-]?\d+$")
_REAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def normalize_value(value: Any, column: dict[str, Any]) -> Any:
    """Conservative, semantic-type-aware normalization.

    The compiler does not call this unless normalize_values=True. Strings for
    identifiers, codes, phones, ZIPs, account numbers, and date keys are never
    numerically coerced.
    """
    if value is None:
        return None
    semantic_type = str(column.get("semantic_type") or "").lower()
    declared_type = str(column.get("type") or "").upper()
    if column.get("preserve_as_text") or semantic_type in {
        "identifier",
        "code",
        "phone",
        "zip",
        "postal_code",
        "date_key",
        "document_number",
        "account_number",
    }:
        return value if isinstance(value, str) else str(value)
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if semantic_type == "boolean":
        if stripped.casefold() in {"true", "yes"}:
            return 1
        if stripped.casefold() in {"false", "no"}:
            return 0
        return value
    if "INT" in declared_type and _INTEGER.fullmatch(stripped):
        return int(stripped)
    if any(token in declared_type for token in ("REAL", "FLOA", "DOUB", "NUM")):
        if _REAL.fullmatch(stripped):
            return float(stripped)
    return value


def _group_shapes(rows: list[dict[str, Any]]) -> list[tuple[list[str], list[dict[str, Any]]]]:
    order: list[tuple[str, ...]] = []
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        shape = tuple(row)
        if shape not in buckets:
            order.append(shape)
        buckets[shape].append(row)
    return [(list(shape), buckets[shape]) for shape in order]


def _conflict_sql(conflict: dict[str, Any]) -> str:
    action = conflict["action"]
    target = conflict.get("target") or []
    update_columns = conflict.get("update_columns") or []
    if action == "error":
        return ""
    target_sql = (
        " (" + ", ".join(quote_identifier(column) for column in target) + ")"
        if target
        else ""
    )
    if action == "do_nothing":
        return f" ON CONFLICT{target_sql} DO NOTHING"
    assignments = ", ".join(
        f"{quote_identifier(column)} = excluded.{quote_identifier(column)}"
        for column in update_columns
    )
    return f" ON CONFLICT{target_sql} DO UPDATE SET {assignments}"


def _compile_group(
    group: dict[str, Any],
    profile: dict[str, Any],
    normalize_values: bool,
    normalization_mode: str,
) -> list[CompiledStatement]:
    table = str(group["table"])
    group_id = str(group["group_id"])
    table_profile = table_map(profile)[table]
    columns_by_name = column_map(table_profile)
    conflict_sql = _conflict_sql(group["conflict"])
    statements: list[CompiledStatement] = []
    for columns, rows in _group_shapes(group["rows"]):
        column_sql = ", ".join(quote_identifier(column) for column in columns)
        tuple_sql = "(" + ", ".join("?" for _ in columns) + ")"
        values_sql = ", ".join(tuple_sql for _ in rows)
        params: list[Any] = []
        normalization_audit: list[dict[str, Any]] = []
        for row in rows:
            for column in columns:
                value = row[column]
                if normalization_mode == "lossless":
                    normalized, audit = normalize_value_lossless(
                        value,
                        columns_by_name[column],
                    )
                    params.append(normalized)
                    normalization_audit.append(
                        {
                            "column": column,
                            **audit,
                        }
                    )
                else:
                    params.append(
                        normalize_value(value, columns_by_name[column])
                        if normalize_values
                        else value
                    )
        statements.append(
            CompiledStatement(
                sql=(
                    f"INSERT INTO {quote_identifier(table)} ({column_sql}) "
                    f"VALUES {values_sql}{conflict_sql}"
                ),
                params=params,
                group_id=group_id,
                table=table,
                row_count=len(rows),
                normalizations=normalization_audit,
            )
        )
    return statements


def _ordered_groups(
    plan: dict[str, Any],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    groups = list(plan.get("write_groups") or [])
    by_id = {str(group["group_id"]): group for group in groups}
    position = {str(group["group_id"]): index for index, group in enumerate(groups)}
    edges: dict[str, set[str]] = {group_id: set() for group_id in by_id}
    indegree: dict[str, int] = {group_id: 0 for group_id in by_id}

    def add_edge(before: str, after: str) -> None:
        if before == after or after in edges[before]:
            return
        edges[before].add(after)
        indegree[after] += 1

    for dependency in plan.get("dependencies") or []:
        add_edge(str(dependency["before"]), str(dependency["after"]))

    profiles = table_map(profile)
    groups_by_table: dict[str, list[str]] = defaultdict(list)
    for group in groups:
        groups_by_table[str(group["table"])].append(str(group["group_id"]))
    for child_group in groups:
        child_id = str(child_group["group_id"])
        child_table = str(child_group["table"])
        for foreign_key in profiles[child_table].get("foreign_keys", []):
            parent_table = str(foreign_key.get("to_table") or "")
            if parent_table == child_table:
                continue
            for parent_id in groups_by_table.get(parent_table, []):
                add_edge(parent_id, child_id)

    ready = sorted(
        (group_id for group_id, degree in indegree.items() if degree == 0),
        key=position.get,
    )
    ordered: list[str] = []
    while ready:
        group_id = ready.pop(0)
        ordered.append(group_id)
        for child in sorted(edges[group_id], key=position.get):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=position.get)
    if len(ordered) != len(groups):
        raise ValueError("Dependency graph contains a cycle after FK ordering.")
    return [by_id[group_id] for group_id in ordered]


def _best_effort_groups(
    write_plan: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[Diagnostic], list[Diagnostic]]:
    valid_groups: list[dict[str, Any]] = []
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    for index, group in enumerate(write_plan.get("write_groups") or []):
        candidate = {
            "version": write_plan.get("version", "3.0"),
            "source": {"mode": "free_text", "row_count": 0},
            "write_groups": [group],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = verify_write_plan(candidate, profile)
        warnings.extend(result.warnings)
        if result.valid:
            valid_groups.extend(result.normalized_plan["write_groups"])
        else:
            for error in result.errors:
                error.details.setdefault("original_group_index", index)
                errors.append(error)
    return valid_groups, errors, warnings


def compile_write_plan(
    write_plan: dict[str, Any],
    profile: dict[str, Any],
    *,
    strict_atomic: bool = True,
    normalize_values: bool = False,
    normalization_mode: str = "legacy",
) -> CompiledProgram:
    verification = verify_write_plan(write_plan, profile)
    if not verification.valid and strict_atomic:
        return CompiledProgram(
            status="error",
            statements=[],
            errors=verification.errors,
            warnings=verification.warnings,
            strict_atomic=True,
        )

    if verification.valid:
        plan = verification.normalized_plan
        errors: list[Diagnostic] = []
        warnings = list(verification.warnings)
    else:
        groups, errors, warnings = _best_effort_groups(write_plan, profile)
        plan = {
            "write_groups": groups,
            "dependencies": [],
        }
        warnings = list(verification.warnings) + warnings

    statements: list[CompiledStatement] = []
    try:
        groups = _ordered_groups(plan, profile)
        for group in groups:
            statements.extend(
                _compile_group(
                    group,
                    profile,
                    normalize_values,
                    normalization_mode,
                )
            )
    except (KeyError, ValueError) as exc:
        errors.append(
            Diagnostic(
                "COMPILER_ERROR",
                str(exc),
                path="/write_groups",
            )
        )
        if strict_atomic:
            statements = []

    if errors and statements:
        status = "partial"
    elif errors or not statements:
        status = "error"
    else:
        status = "success"
    return CompiledProgram(
        status=status,
        statements=statements,
        errors=errors,
        warnings=warnings,
        strict_atomic=strict_atomic,
    )


def compile_verified_plan(
    verified_plan: dict[str, Any],
    profile: dict[str, Any],
    *,
    normalize_values: bool = False,
    normalization_mode: str = "legacy",
) -> CompiledProgram:
    """Compile a plan already normalized by verify_write_plan().

    This is the production API used by MP and prevents verification from being
    performed twice. compile_write_plan remains a boundary convenience wrapper.
    """
    statements: list[CompiledStatement] = []
    errors: list[Diagnostic] = []
    try:
        for group in _ordered_groups(verified_plan, profile):
            statements.extend(
                _compile_group(
                    group,
                    profile,
                    normalize_values,
                    normalization_mode,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(
            Diagnostic(
                "COMPILER_ERROR",
                str(exc),
                path="/write_groups",
            )
        )
    return CompiledProgram(
        status="error" if errors or not statements else "success",
        statements=[] if errors else statements,
        errors=errors,
        warnings=[],
        strict_atomic=True,
    )
