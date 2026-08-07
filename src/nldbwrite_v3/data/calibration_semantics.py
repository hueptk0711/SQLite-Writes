from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.evaluator import dump_database_state, snapshot_database
from nldbwrite_v3.schema import load_profile, table_map
from nldbwrite_v3.verifier import verify_write_plan

from .gold_sql import GoldSqlParseError, parse_gold_sql


EXPECTED_CONFLICT_ACTION = {
    "plain_insert": "error",
    "insert_ignore": "do_nothing",
    "upsert_update": "do_update",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _plan_core(plan: dict[str, Any]) -> dict[str, Any]:
    groups = []
    for group in plan.get("write_groups") or []:
        groups.append(
            {
                "group_id": group.get("group_id"),
                "table": group.get("table"),
                "action": group.get("action"),
                "rows": group.get("rows"),
                "conflict": {
                    "action": (group.get("conflict") or {}).get("action"),
                    "target": list(
                        (group.get("conflict") or {}).get("target") or []
                    ),
                    "update_columns": list(
                        (group.get("conflict") or {}).get("update_columns") or []
                    ),
                },
            }
        )
    dependencies = [
        {
            "before": row.get("before"),
            "after": row.get("after"),
            "foreign_key": row.get("foreign_key"),
        }
        for row in plan.get("dependencies") or []
        if isinstance(row, dict)
    ]
    return {
        "write_groups": groups,
        "dependencies": dependencies,
    }


def _normalized_records(plan: dict[str, Any]) -> list[str]:
    return sorted(
        _canonical({"table": group["table"], "values": row})
        for group in plan.get("write_groups") or []
        for row in group.get("rows") or []
    )


def _sample_records(sample: dict[str, Any]) -> list[str]:
    return sorted(
        _canonical(
            {
                "table": record.get("table"),
                "values": record.get("values") or {},
            }
        )
        for record in sample.get("gold_records") or []
        if isinstance(record, dict)
    )


def _unique_keys(table_profile: dict[str, Any]) -> list[list[str]]:
    return [
        list(index.get("columns") or [])
        for index in table_profile.get("unique_indexes") or []
        if isinstance(index, dict) and index.get("columns")
    ]


def _matching_rows(
    connection: sqlite3.Connection,
    table: str,
    key_columns: list[str],
    row: dict[str, Any],
    selected_columns: list[str],
) -> list[tuple[Any, ...]]:
    if not key_columns or not all(column in row for column in key_columns):
        return []
    if any(row[column] is None for column in key_columns):
        return []
    where = " AND ".join(
        f"{_quote_identifier(column)} IS ?" for column in key_columns
    )
    selected = ", ".join(
        _quote_identifier(column) for column in selected_columns
    )
    return connection.execute(
        f"SELECT {selected} FROM {_quote_identifier(table)} WHERE {where}",
        [row[column] for column in key_columns],
    ).fetchall()


def _audit_conflict_witnesses(
    sample: dict[str, Any],
    plan: dict[str, Any],
    profile: dict[str, Any],
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    sample_id = str(sample.get("id") or "")
    operation = str(sample.get("operation_semantics") or "")
    expected_action = EXPECTED_CONFLICT_ACTION.get(operation)
    profiles = table_map(profile)
    issues: list[dict[str, Any]] = []
    witness_count = 0
    changed_update_witness_count = 0
    for group in plan.get("write_groups") or []:
        table = str(group.get("table") or "")
        table_profile = profiles.get(table) or {}
        conflict = group.get("conflict") or {}
        action = str(conflict.get("action") or "")
        target = list(conflict.get("target") or [])
        update_columns = list(conflict.get("update_columns") or [])
        if action != expected_action:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "OPERATION_GOLD_CONFLICT_MISMATCH",
                    "message": (
                        f"{table}: expected conflict action {expected_action}, "
                        f"got {action}"
                    ),
                }
            )
        for row in group.get("rows") or []:
            if operation == "plain_insert":
                present_keys = [
                    key
                    for key in _unique_keys(table_profile)
                    if all(column in row for column in key)
                ]
                if not present_keys:
                    issues.append(
                        {
                            "sample_id": sample_id,
                            "error_code": "PLAIN_INSERT_WITHOUT_EXPLICIT_UNIQUE_KEY",
                            "message": (
                                f"{table}: each plain-insert row must carry a "
                                "complete primary or unique key."
                            ),
                        }
                    )
                for key in present_keys:
                    if _matching_rows(
                        connection,
                        table,
                        key,
                        row,
                        key,
                    ):
                        issues.append(
                            {
                                "sample_id": sample_id,
                                "error_code": "PLAIN_INSERT_CONFLICTS_IN_PRISTINE_DB",
                                "message": f"{table}: key {key} already exists.",
                            }
                        )
                continue
            if not target or not all(column in row for column in target):
                issues.append(
                    {
                        "sample_id": sample_id,
                        "error_code": "CONFLICT_TARGET_VALUE_MISSING",
                        "message": (
                            f"{table}: every conflict-sensitive row must carry "
                            f"all target columns {target}."
                        ),
                    }
                )
                continue
            selected = list(dict.fromkeys([*target, *update_columns]))
            matches = _matching_rows(
                connection,
                table,
                target,
                row,
                selected,
            )
            if not matches:
                continue
            witness_count += 1
            if operation == "upsert_update":
                column_positions = {
                    column: selected.index(column) for column in selected
                }
                for existing in matches:
                    if any(
                        column in row
                        and existing[column_positions[column]] != row[column]
                        for column in update_columns
                    ):
                        changed_update_witness_count += 1
                        break
    if operation in {"insert_ignore", "upsert_update"} and witness_count == 0:
        issues.append(
            {
                "sample_id": sample_id,
                "error_code": "MISSING_PRISTINE_CONFLICT_WITNESS",
                "message": "At least one row must conflict in the pristine database.",
            }
        )
    if operation == "upsert_update" and changed_update_witness_count == 0:
        issues.append(
            {
                "sample_id": sample_id,
                "error_code": "UPSERT_WITNESS_HAS_NO_VALUE_CHANGE",
                "message": (
                    "At least one conflict witness must change an allowed "
                    "update column."
                ),
            }
        )
    return issues


def _audit_complexity(
    sample: dict[str, Any],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    sample_id = str(sample.get("id") or "")
    complexity = str(sample.get("complexity") or "")
    multi_table = sample.get("multi_table") is True
    groups = list(plan.get("write_groups") or [])
    tables = {str(group.get("table") or "") for group in groups}
    row_count = sum(len(group.get("rows") or []) for group in groups)
    issues: list[dict[str, Any]] = []

    def issue(code: str, message: str) -> None:
        issues.append(
            {"sample_id": sample_id, "error_code": code, "message": message}
        )

    if multi_table != (len(tables) > 1):
        issue(
            "MULTI_TABLE_LABEL_MISMATCH",
            f"multi_table={multi_table}, but gold plan targets {len(tables)} tables.",
        )
    if complexity == "single_row":
        if row_count != 1 or len(tables) != 1 or multi_table:
            issue(
                "INVALID_SINGLE_ROW_SHAPE",
                "single_row requires exactly one row in one table.",
            )
    elif complexity == "small_batch":
        if not 2 <= row_count <= 5:
            issue(
                "INVALID_SMALL_BATCH_SIZE",
                f"small_batch requires 2-5 rows, got {row_count}.",
            )
    elif complexity == "large_or_relational":
        if multi_table:
            if len(tables) < 2 or not plan.get("dependencies"):
                issue(
                    "INVALID_RELATIONAL_SHAPE",
                    "Relational cases require multiple FK-dependent tables.",
                )
        elif row_count < 8:
            issue(
                "INVALID_LARGE_BATCH_SIZE",
                f"Large single-table cases require at least 8 rows, got {row_count}.",
            )
    if multi_table and not plan.get("dependencies"):
        issue(
            "MULTI_TABLE_WITHOUT_DEPENDENCY",
            "Multi-table cases require an explicit or inferred dependency.",
        )
    return issues


def audit_calibration_semantics(
    samples: list[dict[str, Any]],
    *,
    kit_dir: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    root = Path(kit_dir)
    issues: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    metrics: Counter[str] = Counter(samples=len(samples))
    for sample in samples:
        sample_id = str(sample.get("id") or "")
        db_id = str(sample.get("db_id") or "")
        profile_path = root / "profiles" / f"{db_id}.json"
        database_path = root / "databases" / db_id / f"{db_id}.sqlite"
        if not profile_path.is_file() or not database_path.is_file():
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "MISSING_SEMANTIC_AUDIT_ASSET",
                    "message": f"Missing profile or database for {db_id}.",
                }
            )
            continue
        profile = load_profile(profile_path)
        try:
            plan = parse_gold_sql(
                list(sample.get("gold_sql") or []),
                sample_id=sample_id,
                profile=profile,
            )
        except GoldSqlParseError as exc:
            diagnostic = exc.diagnostic.to_dict()
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": diagnostic.get(
                        "error_code", "GOLD_SQL_PARSE_ERROR"
                    ),
                    "message": diagnostic.get("message", str(exc)),
                }
            )
            continue
        plans.append(plan)
        metrics["parsed"] += 1
        authored_plan = sample.get("gold_plan")
        if (
            not isinstance(authored_plan, dict)
            or _canonical(_plan_core(authored_plan))
            != _canonical(_plan_core(plan))
        ):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_PLAN_SQL_SEMANTICS_MISMATCH",
                    "message": "gold_plan does not match the plan parsed from gold_sql.",
                }
            )
        else:
            metrics["gold_plan_matches_sql"] += 1
        if _sample_records(sample) != _normalized_records(plan):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_RECORDS_SQL_MISMATCH",
                    "message": "gold_records do not match rows parsed from gold_sql.",
                }
            )
        else:
            metrics["gold_records_match_sql"] += 1
        plan_tables = {
            str(group.get("table") or "")
            for group in plan.get("write_groups") or []
        }
        if set(sample.get("gold_tables") or []) != plan_tables:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_TABLES_PLAN_MISMATCH",
                    "message": "gold_tables must exactly match plan target tables.",
                }
            )
        verification = verify_write_plan(plan, profile)
        if not verification.valid:
            for error in verification.errors:
                issues.append(
                    {
                        "sample_id": sample_id,
                        "error_code": error.error_code,
                        "message": error.message,
                    }
                )
        else:
            metrics["schema_valid_plan"] += 1
        first_conflict = (
            (plan.get("write_groups") or [{}])[0].get("conflict") or {}
        )
        if list(sample.get("conflict_target") or []) != list(
            first_conflict.get("target") or []
        ):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "TOP_LEVEL_CONFLICT_TARGET_MISMATCH",
                    "message": "Top-level conflict_target must match the first group.",
                }
            )
        if list(sample.get("update_columns") or []) != list(
            first_conflict.get("update_columns") or []
        ):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "TOP_LEVEL_UPDATE_COLUMNS_MISMATCH",
                    "message": "Top-level update_columns must match the first group.",
                }
            )
        connection = snapshot_database(database_path)
        try:
            issues.extend(
                _audit_conflict_witnesses(
                    sample,
                    plan,
                    profile,
                    connection,
                )
            )
            before = dump_database_state(connection)
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("SAVEPOINT authored_gold")
                for statement in sample.get("gold_sql") or []:
                    connection.execute(statement)
                connection.execute("RELEASE authored_gold")
            except sqlite3.Error as exc:
                try:
                    connection.execute("ROLLBACK TO authored_gold")
                    connection.execute("RELEASE authored_gold")
                except sqlite3.Error:
                    connection.rollback()
                issues.append(
                    {
                        "sample_id": sample_id,
                        "error_code": "GOLD_SQL_EXECUTION_ERROR",
                        "message": str(exc),
                    }
                )
                continue
            after = dump_database_state(connection)
            changed_tables = sorted(
                table
                for table in set(before) | set(after)
                if before.get(table) != after.get(table)
            )
            if sample.get("state_changing") is True and not changed_tables:
                issues.append(
                    {
                        "sample_id": sample_id,
                        "error_code": "DECLARED_STATE_CHANGE_IS_NO_OP",
                        "message": "Gold SQL leaves the pristine database unchanged.",
                    }
                )
            unintended = sorted(set(changed_tables) - plan_tables)
            if unintended:
                issues.append(
                    {
                        "sample_id": sample_id,
                        "error_code": "GOLD_SQL_UNINTENDED_SIDE_EFFECT",
                        "message": f"Unexpected changed tables: {unintended}",
                    }
                )
            if changed_tables:
                metrics["state_changed"] += 1
            if not unintended:
                metrics["no_unintended_side_effect"] += 1
        finally:
            connection.close()
        issues.extend(_audit_complexity(sample, plan))
    counts = Counter(row["error_code"] for row in issues)
    summary = {
        "samples": len(samples),
        "parsed_plans": metrics["parsed"],
        "schema_valid_plans": metrics["schema_valid_plan"],
        "gold_plan_matches_sql": metrics["gold_plan_matches_sql"],
        "gold_records_match_sql": metrics["gold_records_match_sql"],
        "state_changed_samples": metrics["state_changed"],
        "samples_without_unintended_side_effect": metrics[
            "no_unintended_side_effect"
        ],
        "semantic_issue_count": len(issues),
        "semantic_issues_by_code": dict(sorted(counts.items())),
        "semantic_status": "valid" if not issues else "invalid",
    }
    return issues, summary, plans
