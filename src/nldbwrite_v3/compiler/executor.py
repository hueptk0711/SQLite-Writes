from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from nldbwrite_v3.ir import CompiledProgram


def _execution_error_class(message: str | None) -> str:
    normalized = str(message or "").casefold()
    if "foreign key constraint failed" in normalized:
        return "foreign_key_violation"
    if "unique constraint failed" in normalized:
        return "unique_violation"
    if "not null constraint failed" in normalized:
        return "not_null_violation"
    if "check constraint failed" in normalized:
        return "check_violation"
    if "datatype mismatch" in normalized or "type" in normalized:
        return "type_error"
    if "database is locked" in normalized:
        return "transaction_failure"
    return "unclassified_constraint_or_execution_error"


def execute_program(
    connection_or_path: sqlite3.Connection | str | Path,
    program: CompiledProgram,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute the whole parameterized program behind one SQLite savepoint."""
    if program.status != "success":
        return {
            "status": "rejected",
            "executed_statements": 0,
            "committed": False,
            "error": "Program must have status=success before execution.",
        }
    owns_connection = not isinstance(connection_or_path, sqlite3.Connection)
    conn = (
        sqlite3.connect(str(connection_or_path))
        if owns_connection
        else connection_or_path
    )
    conn.execute("PRAGMA foreign_keys = ON")
    savepoint = "nldbwrite_" + uuid.uuid4().hex
    executed = 0
    try:
        conn.execute(f"SAVEPOINT {savepoint}")
        for statement in program.statements:
            conn.execute(statement.sql, statement.params)
            executed += 1
        if dry_run:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
        else:
            conn.execute(f"RELEASE {savepoint}")
            if owns_connection:
                conn.commit()
        return {
            "status": "dry_run_success" if dry_run else "success",
            "executed_statements": executed,
            "committed": not dry_run,
            "error": None,
        }
    except sqlite3.Error as exc:
        try:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
        except sqlite3.Error:
            conn.rollback()
        return {
            "status": "execution_error",
            "executed_statements": executed,
            "committed": False,
            "error": str(exc),
        }
    finally:
        if owns_connection:
            conn.close()


def check_semantic_risk_gate(program: CompiledProgram) -> dict[str, Any]:
    """Reject compiled programs carrying a blocking semantic-risk warning."""
    blocking_warning_codes = {
        "AMBIGUOUS_EXACT_EVIDENCE_COLUMN_GROUNDING",
        "EVIDENCE_COLUMN_TABLE_MISMATCH",
        "UPDATE_COLUMN_MISSING_VALUE",
    }
    blocking_warnings = [
        warning
        for warning in program.warnings
        if warning.error_code in blocking_warning_codes
    ]
    if blocking_warnings:
        codes = sorted(
            {warning.error_code for warning in blocking_warnings}
        )
        return {
            "status": "rejected",
            "accepted": False,
            "error_class": "semantic_grounding_risk",
            "error_codes": codes,
        }
    return {
        "status": "accepted",
        "accepted": True,
        "error_class": None,
        "error_codes": [],
    }


def preflight_program(
    connection_or_path: sqlite3.Connection | str | Path,
    program: CompiledProgram,
) -> dict[str, Any]:
    """Execute the complete program in SQLite and roll back every write.

    This stage evaluates transactional executability and active SQLite
    constraints only. Semantic-risk filtering is handled separately by
    :func:`check_semantic_risk_gate` and is not performed here.
    """
    started = time.perf_counter()
    preflight_connection: sqlite3.Connection | None = None
    target: sqlite3.Connection | str | Path = connection_or_path
    if not isinstance(connection_or_path, sqlite3.Connection):
        source = sqlite3.connect(str(connection_or_path))
        preflight_connection = sqlite3.connect(":memory:")
        try:
            source.backup(preflight_connection)
        finally:
            source.close()
        target = preflight_connection
    try:
        execution = execute_program(
            target,
            program,
            dry_run=True,
        )
    finally:
        if preflight_connection is not None:
            preflight_connection.close()
    accepted = execution.get("status") == "dry_run_success"
    return {
        "status": "accepted" if accepted else "abstained",
        "accepted": accepted,
        "action": "accept" if accepted else "abstain",
        "deterministic_repair_applied": False,
        "error_class": (
            None
            if accepted
            else _execution_error_class(execution.get("error"))
        ),
        "error": execution.get("error"),
        "executed_statements": execution.get("executed_statements", 0),
        "latency_sec": time.perf_counter() - started,
    }
