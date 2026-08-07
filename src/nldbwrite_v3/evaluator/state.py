from __future__ import annotations

import sqlite3
import re
import json
from pathlib import Path
from typing import Any, Iterable

from nldbwrite_v3.compiler import compile_verified_plan, execute_program
from nldbwrite_v3.ir import CompiledProgram
from nldbwrite_v3.schema.profile import quote_identifier
from nldbwrite_v3.verifier import verify_write_plan


_WRITE_TABLE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|"
    r"UPDATE|DELETE\s+FROM)\s+"
    r'(?:"((?:[^"]|"")*)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))',
    re.IGNORECASE,
)


def find_database(db_root: str | Path, db_id: str) -> Path:
    root = Path(db_root)
    candidates = [
        root / db_id / f"{db_id}.sqlite",
        root / db_id / f"{db_id}.db",
        root / f"{db_id}.sqlite",
        root / f"{db_id}.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    nested = sorted((root / db_id).glob("*.sqlite")) if (root / db_id).exists() else []
    if nested:
        return nested[0]
    raise FileNotFoundError(f"Database not found for db_id={db_id!r} under {root}")


def _memory_copy(
    path_or_connection: str | Path | sqlite3.Connection | bytes,
) -> sqlite3.Connection:
    if isinstance(path_or_connection, bytes):
        destination = sqlite3.connect(":memory:")
        destination.deserialize(path_or_connection)
        destination.execute("PRAGMA journal_mode = MEMORY")
        destination.execute("PRAGMA temp_store = MEMORY")
        destination.execute("PRAGMA foreign_keys = ON")
        return destination
    owns_source = not isinstance(path_or_connection, sqlite3.Connection)
    source = (
        sqlite3.connect(str(path_or_connection))
        if owns_source
        else path_or_connection
    )
    destination = sqlite3.connect(":memory:")
    try:
        source.backup(destination)
    finally:
        if owns_source:
            source.close()
    destination.execute("PRAGMA foreign_keys = ON")
    return destination


def snapshot_database(
    path_or_connection: str | Path | sqlite3.Connection | bytes,
) -> sqlite3.Connection:
    """Return an isolated in-memory snapshot suitable for repeated evaluation."""
    return _memory_copy(path_or_connection)


def load_database_image(
    path: str | Path,
    *,
    max_bytes: int = 768 * 1024 * 1024,
) -> str | Path | bytes:
    """Serialize a moderately sized SQLite DB for fast repeated isolation."""
    source_path = Path(path)
    if source_path.stat().st_size > max_bytes:
        return source_path
    source = sqlite3.connect(str(source_path))
    try:
        image = source.serialize()
    finally:
        source.close()
    probe = sqlite3.connect(":memory:")
    try:
        probe.deserialize(image)
        probe.execute("PRAGMA journal_mode = MEMORY")
        probe.execute("PRAGMA temp_store = MEMORY")
        probe.execute("SAVEPOINT image_probe")
        probe.execute("ROLLBACK TO image_probe")
        probe.execute("RELEASE image_probe")
    except sqlite3.Error:
        return source_path
    finally:
        probe.close()
    return image


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _sort_key(row: tuple[Any, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((type(value).__name__, repr(value)) for value in row)


def dump_database_state(
    conn: sqlite3.Connection,
    tables: Iterable[str] | None = None,
) -> dict[str, list[tuple[Any, ...]]]:
    selected = list(tables) if tables is not None else _user_tables(conn)
    state: dict[str, list[tuple[Any, ...]]] = {}
    for table in sorted(set(selected)):
        columns = [
            row[1]
            for row in conn.execute(f"PRAGMA table_xinfo({quote_identifier(table)})")
            if int(row[6]) == 0
        ]
        if not columns:
            state[table] = []
            continue
        column_sql = ", ".join(quote_identifier(column) for column in columns)
        rows = list(
            conn.execute(
                f"SELECT {column_sql} FROM {quote_identifier(table)}"
            ).fetchall()
        )
        state[table] = sorted(rows, key=_sort_key)
    return state


def compare_database_states(
    left: sqlite3.Connection,
    right: sqlite3.Connection,
    tables: Iterable[str] | None = None,
) -> dict[str, Any]:
    def streams_equal(
        left_sql: str,
        right_sql: str,
    ) -> bool:
        left_cursor = left.execute(left_sql)
        right_cursor = right.execute(right_sql)
        while True:
            left_batch = left_cursor.fetchmany(1024)
            right_batch = right_cursor.fetchmany(1024)
            if left_batch != right_batch:
                return False
            if not left_batch:
                return True

    selected = (
        sorted(set(tables))
        if tables is not None
        else sorted(set(_user_tables(left)) | set(_user_tables(right)))
    )
    mismatched: list[str] = []
    for table in selected:
        left_info = [
            row
            for row in left.execute(
                f"PRAGMA table_xinfo({quote_identifier(table)})"
            )
            if int(row[6]) == 0
        ]
        right_info = [
            row
            for row in right.execute(
                f"PRAGMA table_xinfo({quote_identifier(table)})"
            )
            if int(row[6]) == 0
        ]
        left_columns = [row[1] for row in left_info]
        right_columns = [row[1] for row in right_info]
        if left_columns != right_columns or not left_columns:
            if left_columns != right_columns:
                mismatched.append(table)
            continue
        column_sql = ", ".join(
            quote_identifier(column) for column in left_columns
        )
        primary_key = [
            row[1]
            for row in sorted(left_info, key=lambda item: int(item[5]) or 10**9)
            if int(row[5]) > 0
        ]
        fast_order = (
            ", ".join(quote_identifier(column) for column in primary_key)
            if primary_key
            else "rowid"
        )
        base_select = (
            f"SELECT {column_sql} FROM {quote_identifier(table)}"
        )
        fast_sql = f"{base_select} ORDER BY {fast_order}"
        try:
            if streams_equal(fast_sql, fast_sql):
                continue
        except sqlite3.Error:
            pass
        order_sql = ", ".join(
            f"{quote_identifier(column)} IS NULL, {quote_identifier(column)}"
            for column in left_columns
        )
        canonical_sql = f"{base_select} ORDER BY {order_sql}"
        if not streams_equal(canonical_sql, canonical_sql):
            mismatched.append(table)
    return {
        "correct": not mismatched,
        "mismatched_tables": mismatched,
    }


def _written_tables(statements: Iterable[str]) -> set[str]:
    tables: set[str] = set()
    for statement in statements:
        for match in _WRITE_TABLE.finditer(statement):
            table = next(
                (value for value in match.groups() if value is not None),
                None,
            )
            if table:
                tables.add(table.replace('""', '"'))
    return tables


def _affected_table_closure(
    conn: sqlite3.Connection,
    seed_tables: Iterable[str],
) -> list[str]:
    """Conservatively find every table that these writes can modify.

    Tables outside this closure are provably unchanged: SQLite writes affect
    their target table, trigger targets, and FK-cascade descendants only.
    """
    all_tables = set(_user_tables(conn))
    affected = {table for table in seed_tables if table in all_tables}
    trigger_rows = list(
        conn.execute(
            "SELECT tbl_name, sql FROM sqlite_master "
            "WHERE type='trigger' AND sql IS NOT NULL"
        )
    )
    changed = True
    while changed:
        changed = False
        for trigger_table, trigger_sql in trigger_rows:
            if trigger_table not in affected:
                continue
            targets = _written_tables([str(trigger_sql)])
            if not targets:
                # An unparseable trigger is rare; full comparison is the safe
                # fallback and preserves strict-state semantics.
                return sorted(all_tables)
            before = len(affected)
            affected.update(targets & all_tables)
            changed = changed or len(affected) != before
        for child in all_tables:
            foreign_keys = conn.execute(
                f"PRAGMA foreign_key_list({quote_identifier(child)})"
            )
            if any(str(row[2]) in affected for row in foreign_keys):
                if child not in affected:
                    affected.add(child)
                    changed = True
    return sorted(affected)


def _state_comparison_tables(
    conn: sqlite3.Connection,
    possible_writes: Iterable[str],
    state_scope: str,
) -> list[str]:
    """Return a method-invariant or legacy state-comparison scope.

    ``all_user_tables`` is the reviewer-facing definition: every persistent
    user table is compared and SQLite-owned tables are excluded by
    :func:`_user_tables`. ``affected_tables`` is retained only so frozen
    results can be audited against the former implementation.
    """
    if state_scope == "all_user_tables":
        return _user_tables(conn)
    if state_scope == "affected_tables":
        return _affected_table_closure(conn, possible_writes)
    raise ValueError(
        "state_scope must be 'all_user_tables' or 'affected_tables'"
    )


def _table_unique_keys(
    conn: sqlite3.Connection,
    table: str,
) -> list[list[str]]:
    info = list(
        conn.execute(f"PRAGMA table_info({quote_identifier(table)})")
    )
    primary_key = [
        row[1]
        for row in sorted(info, key=lambda item: int(item[5]) or 10**9)
        if int(row[5]) > 0
    ]
    keys = [primary_key] if primary_key else []
    for index_row in conn.execute(
        f"PRAGMA index_list({quote_identifier(table)})"
    ):
        if not bool(index_row[2]) or bool(index_row[4]):
            continue
        columns = [
            row[2]
            for row in conn.execute(
                f"PRAGMA index_info({quote_identifier(str(index_row[1]))})"
            )
            if row[2] is not None
        ]
        if columns and columns not in keys:
            keys.append(columns)
    return keys


def _oracle_table_summary(
    conn: sqlite3.Connection,
    table: str,
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    columns = [
        row[1]
        for row in conn.execute(
            f"PRAGMA table_xinfo({quote_identifier(table)})"
        )
        if int(row[6]) == 0
    ]
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    unique_keys = _table_unique_keys(conn, table)
    probes: dict[str, dict[str, Any]] = {}
    for group in groups:
        conflict_target = list(
            group.get("conflict", {}).get("target") or []
        )
        for row in group.get("rows") or []:
            candidate_keys: list[list[str]] = []
            if conflict_target and all(column in row for column in conflict_target):
                candidate_keys.append(conflict_target)
            candidate_keys.extend(
                key
                for key in unique_keys
                if all(column in row for column in key)
            )
            # A full inserted-value probe covers keyless tables and values
            # affected by defaults without scanning unrelated rows.
            row_columns = [column for column in columns if column in row]
            if row_columns:
                candidate_keys.append(row_columns)
            for key_columns in candidate_keys:
                values = [row[column] for column in key_columns]
                fingerprint = json.dumps(
                    [key_columns, values],
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if fingerprint in probes:
                    continue
                where_sql = " AND ".join(
                    f"{quote_identifier(column)} IS ?"
                    for column in key_columns
                )
                order_sql = ", ".join(
                    f"{quote_identifier(column)} IS NULL, "
                    f"{quote_identifier(column)}"
                    for column in columns
                )
                matches = conn.execute(
                    f"SELECT {column_sql} FROM {quote_identifier(table)} "
                    f"WHERE {where_sql} ORDER BY {order_sql}",
                    values,
                ).fetchall()
                probes[fingerprint] = {
                    "key_columns": key_columns,
                    "values": values,
                    "matches": matches,
                }
    return {
        "row_count": conn.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0],
        "probes": probes,
    }


def _oracle_delta_state(
    conn: sqlite3.Connection,
    plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    groups_by_table: dict[str, list[dict[str, Any]]] = {}
    for group in plan.get("write_groups") or []:
        groups_by_table.setdefault(str(group["table"]), []).append(group)
    return {
        table: _oracle_table_summary(conn, table, groups)
        for table, groups in groups_by_table.items()
    }


def _compare_oracle_delta_states(
    predicted: dict[str, dict[str, Any]],
    gold: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mismatched: list[str] = []
    for table in sorted(set(predicted) | set(gold)):
        if predicted.get(table) != gold.get(table):
            mismatched.append(table)
    return {
        "correct": not mismatched,
        "mismatched_tables": sorted(mismatched),
    }


def _oracle_requires_full_comparison(
    conn: sqlite3.Connection,
    plan: dict[str, Any],
) -> bool:
    groups = plan.get("write_groups") or []
    tables = {str(group["table"]) for group in groups}
    active_triggers = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='trigger' AND tbl_name IN ("
        + ",".join("?" for _ in tables)
        + ")",
        sorted(tables),
    ).fetchone()[0] if tables else 0
    if active_triggers:
        return True
    updated_parent_columns = {
        (str(group["table"]), str(column))
        for group in groups
        if group.get("conflict", {}).get("action") == "do_update"
        for column in group.get("conflict", {}).get("update_columns") or []
    }
    if not updated_parent_columns:
        return False
    for child in _user_tables(conn):
        for foreign_key in conn.execute(
            f"PRAGMA foreign_key_list({quote_identifier(child)})"
        ):
            parent = str(foreign_key[2])
            parent_column = str(foreign_key[4])
            on_update = str(foreign_key[5]).upper()
            if (
                (parent, parent_column) in updated_parent_columns
                and on_update not in {"NO ACTION", "RESTRICT"}
            ):
                return True
    return False


def _rollback_savepoint(
    conn: sqlite3.Connection,
    savepoint: str,
) -> None:
    try:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
    except sqlite3.Error:
        conn.rollback()


def _execute_gold(
    conn: sqlite3.Connection,
    statements: list[str],
) -> dict[str, Any]:
    try:
        conn.execute("SAVEPOINT gold_write")
        for statement in statements:
            conn.execute(statement)
        conn.execute("RELEASE gold_write")
        return {"status": "success", "error": None}
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK TO gold_write")
            conn.execute("RELEASE gold_write")
        except sqlite3.Error:
            conn.rollback()
        return {"status": "execution_error", "error": str(exc)}


def _execute_direct_sql(
    conn: sqlite3.Connection,
    statements: list[str],
) -> dict[str, Any]:
    def leading_sql_body(statement: str) -> str:
        body = statement.lstrip()
        while True:
            if body.startswith("--"):
                newline = body.find("\n")
                body = "" if newline < 0 else body[newline + 1 :].lstrip()
                continue
            if body.startswith("/*"):
                end = body.find("*/", 2)
                if end < 0:
                    return ""
                body = body[end + 2 :].lstrip()
                continue
            return body

    unsafe = [
        statement
        for statement in statements
        if not leading_sql_body(statement).upper().startswith(
            ("INSERT ", "REPLACE ")
        )
    ]
    if unsafe:
        return {
            "status": "unsafe_sql",
            "executed_statements": 0,
            "error": "Only INSERT/REPLACE statements are allowed.",
        }
    executed = 0
    try:
        conn.execute("SAVEPOINT predicted_write")
        for statement in statements:
            conn.execute(statement)
            executed += 1
        conn.execute("RELEASE predicted_write")
        return {
            "status": "success",
            "executed_statements": executed,
            "error": None,
        }
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK TO predicted_write")
            conn.execute("RELEASE predicted_write")
        except sqlite3.Error:
            conn.rollback()
        return {
            "status": "execution_error",
            "executed_statements": executed,
            "error": str(exc),
        }


def evaluate_oracle_sample(
    sample: dict[str, Any],
    gold_plan: dict[str, Any],
    profile: dict[str, Any],
    db_path: str | Path | sqlite3.Connection | bytes,
    *,
    reuse_connection: bool = False,
    fallback_db_path: str | Path | None = None,
) -> dict[str, Any]:
    verification = verify_write_plan(gold_plan, profile)
    program = (
        compile_verified_plan(
            verification.normalized_plan,
            profile,
            normalize_values=False,
        )
        if verification.valid
        else None
    )
    result: dict[str, Any] = {
        "sample_id": sample.get("id"),
        "db_id": sample.get("db_id"),
        "plan_valid": verification.valid,
        "build_success": bool(program and program.status == "success"),
        "execution_success": False,
        "target_state_correct": False,
        "strict_full_state_correct": False,
        "verification_errors": [
            item.to_dict() for item in verification.errors
        ],
        "compiler_errors": (
            [item.to_dict() for item in program.errors] if program else []
        ),
    }
    if program is None or program.status != "success":
        return result
    shared_connection = (
        reuse_connection and isinstance(db_path, sqlite3.Connection)
    )
    gold_conn = db_path if shared_connection else _memory_copy(db_path)
    pred_conn: sqlite3.Connection | None = None
    try:
        comparison_source: str | Path | sqlite3.Connection | bytes = db_path
        if shared_connection:
            # Full-state cases need simultaneous states. Rebuild isolated
            # copies from the original path instead of mutating the reusable
            # per-database connection.
            if fallback_db_path is None:
                raise ValueError(
                    "fallback_db_path is required for a full-state oracle "
                    "comparison when reuse_connection=True"
                )
            gold_conn = _memory_copy(fallback_db_path)
            comparison_source = fallback_db_path
            shared_connection = False
        pred_conn = _memory_copy(comparison_source)
        gold_execution = _execute_gold(
            gold_conn,
            list(sample.get("gold_sql") or []),
        )
        pred_execution = execute_program(pred_conn, program)
        result["gold_execution"] = gold_execution
        result["compiler_execution"] = pred_execution
        result["execution_success"] = (
            gold_execution["status"] == "success"
            and pred_execution["status"] == "success"
        )
        if not result["execution_success"]:
            return result
        target_tables = sorted(
            set(sample.get("gold_tables") or [])
            | {str(group["table"]) for group in gold_plan.get("write_groups") or []}
        )
        strict_tables = _state_comparison_tables(
            pred_conn,
            (),
            "all_user_tables",
        )
        strict = compare_database_states(
            pred_conn,
            gold_conn,
            strict_tables,
        )
        result["state_comparison_scope"] = "all_user_tables"
        strict_mismatches = strict["mismatched_tables"]
        target_mismatches = [
            table
            for table in strict_mismatches
            if table in target_tables
        ]
        result["target_state_correct"] = not target_mismatches
        result["strict_full_state_correct"] = not strict_mismatches
        result["target_mismatched_tables"] = target_mismatches
        result["strict_mismatched_tables"] = strict_mismatches
        return result
    finally:
        if not shared_connection:
            gold_conn.close()
        if pred_conn is not None:
            pred_conn.close()


def evaluate_candidate_sample(
    sample: dict[str, Any],
    db_path: str | Path | sqlite3.Connection | bytes,
    *,
    program: CompiledProgram | None = None,
    direct_sql: list[str] | None = None,
    parse_status: str = "success",
    build_status: str = "success",
    preflight: dict[str, Any] | None = None,
    state_scope: str = "all_user_tables",
) -> dict[str, Any]:
    """Evaluate one deployable prediction against gold state in isolation."""
    gold_conn = _memory_copy(db_path)
    pred_conn = _memory_copy(db_path)
    result: dict[str, Any] = {
        "sample_id": sample.get("id"),
        "db_id": sample.get("db_id"),
        "parse_status": parse_status,
        "build_status": build_status,
        "execution_success": False,
        "target_state_correct": False,
        "strict_full_state_correct": False,
        "side_effect": False,
        "any_off_target_change": False,
        "target_correct_with_side_effect": False,
        "off_target_mismatched_tables": [],
        "error_type": None,
        "error_message": None,
        "preflight": preflight,
    }
    try:
        gold_execution = _execute_gold(
            gold_conn,
            list(sample.get("gold_sql") or []),
        )
        if preflight is not None and not bool(preflight.get("accepted")):
            prediction_execution = {
                "status": "preflight_abstention",
                "executed_statements": 0,
                "error": preflight.get("error"),
            }
        elif program is not None:
            prediction_execution = execute_program(pred_conn, program)
        else:
            prediction_execution = _execute_direct_sql(
                pred_conn,
                list(direct_sql or []),
            )
        result["gold_execution"] = gold_execution
        result["prediction_execution"] = prediction_execution
        if gold_execution["status"] != "success":
            result["error_type"] = "gold_sql_error"
            result["error_message"] = gold_execution.get("error")
            return result
        if parse_status != "success":
            result["error_type"] = "parse_error"
            return result
        if build_status != "success":
            result["error_type"] = "builder_error"
            return result
        if preflight is not None and not bool(preflight.get("accepted")):
            result["error_type"] = "preflight_abstention"
            result["error_message"] = preflight.get("error")
            return result
        if prediction_execution["status"] != "success":
            result["error_type"] = prediction_execution["status"]
            result["error_message"] = prediction_execution.get("error")
            return result
        result["execution_success"] = True
        target_tables = sorted(set(sample.get("gold_tables") or []))
        possible_writes = set(target_tables)
        if program is not None:
            possible_writes.update(
                statement.table for statement in program.statements
            )
        else:
            possible_writes.update(_written_tables(direct_sql or []))
        strict_tables = _state_comparison_tables(
            pred_conn,
            possible_writes,
            state_scope,
        )
        strict = compare_database_states(
            pred_conn,
            gold_conn,
            strict_tables,
        )
        strict_mismatches = strict["mismatched_tables"]
        target_mismatches = [
            table
            for table in strict_mismatches
            if table in target_tables
        ]
        off_target_mismatches = [
            table
            for table in strict_mismatches
            if table not in target_tables
        ]
        result["target_state_correct"] = not target_mismatches
        result["strict_full_state_correct"] = not strict_mismatches
        result["any_off_target_change"] = bool(off_target_mismatches)
        result["target_correct_with_side_effect"] = bool(
            not target_mismatches and off_target_mismatches
        )
        # Backward-compatible metric key. From reporting amendment v2.3 onward,
        # ``side_effect`` means any observed off-target state modification,
        # irrespective of whether the target state is also wrong.
        result["side_effect"] = result["any_off_target_change"]
        result["target_mismatched_tables"] = target_mismatches
        result["strict_mismatched_tables"] = strict_mismatches
        result["off_target_mismatched_tables"] = off_target_mismatches
        result["state_comparison_scope"] = state_scope
        if target_mismatches and off_target_mismatches:
            result["error_type"] = "wrong_state_with_off_target_change"
        elif target_mismatches:
            result["error_type"] = "wrong_state"
        elif off_target_mismatches:
            result["error_type"] = "unintended_side_effect"
        return result
    finally:
        gold_conn.close()
        pred_conn.close()
