from __future__ import annotations

import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from .types import PreflightResult, SQLiteProgram


def preflight_sqlite(db_path: Path, program: SQLiteProgram, *, timeout_seconds: float = 30.0) -> PreflightResult:
    db_copy = db_path.with_name(f"{db_path.stem}.preflight.{uuid.uuid4().hex}{db_path.suffix}")
    shutil.copy2(db_path, db_copy)
    conn = sqlite3.connect(db_copy, timeout=min(max(timeout_seconds, 0.001), 5.0))
    deadline = time.monotonic() + timeout_seconds
    timed_out = False

    def progress_handler() -> int:
        nonlocal timed_out
        if time.monotonic() > deadline:
            timed_out = True
            return 1
        return 0

    try:
        conn.set_progress_handler(progress_handler, 1000)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        conn.execute(program.sql, program.parameters)
        conn.rollback()
        return PreflightResult(admitted=True, reason_code="admitted", message="preflight succeeded and rolled back")
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        if timed_out:
            return PreflightResult(admitted=False, reason_code="preflight_timeout", message=f"preflight exceeded {timeout_seconds} seconds")
        return PreflightResult(admitted=False, reason_code="preflight_execution_failure", message=str(exc))
    finally:
        conn.set_progress_handler(None, 0)
        conn.close()
        db_copy.unlink(missing_ok=True)
