from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

from .types import PreflightResult, SQLiteProgram


def preflight_sqlite(db_path: Path, program: SQLiteProgram, *, timeout_seconds: float = 30.0) -> PreflightResult:
    db_copy = db_path.with_name(f"{db_path.stem}.preflight.{uuid.uuid4().hex}{db_path.suffix}")
    shutil.copy2(db_path, db_copy)
    conn = sqlite3.connect(db_copy, timeout=timeout_seconds)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        conn.execute(program.sql, program.parameters)
        conn.rollback()
        return PreflightResult(admitted=True, reason_code="admitted", message="preflight succeeded and rolled back")
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        return PreflightResult(admitted=False, reason_code="preflight_execution_failure", message=str(exc))
    finally:
        conn.close()
        db_copy.unlink(missing_ok=True)
