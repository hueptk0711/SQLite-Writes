from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


STATE_DIFF_CLASSES = (
    "STATE_MISSING_ROW",
    "STATE_EXTRA_ROW",
    "STATE_WRONG_VALUE",
    "STATE_WRONG_TARGET_ROW",
    "STATE_WRONG_CONFLICT_BEHAVIOR",
    "STATE_MISSING_DEPENDENT_ROW",
    "STATE_EXTRA_DEPENDENT_ROW",
    "STATE_OFF_TARGET_CHANGE",
)


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for table in _user_tables(conn):
        columns = [
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
        ]
        order_by = ", ".join(quote_identifier(column) for column in columns)
        query = f"SELECT * FROM {quote_identifier(table)}"
        if order_by:
            query += f" ORDER BY {order_by}"
        rows = [tuple(row) for row in conn.execute(query).fetchall()]
        output[table] = {"columns": columns, "rows": Counter(rows)}
    return output


def _execute_statements(
    conn: sqlite3.Connection,
    statements: Iterable[tuple[str, list[Any]]],
) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")
    try:
        for sql, params in statements:
            conn.execute(str(sql), list(params))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _gold_statements(sample: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    output: list[tuple[str, list[Any]]] = []
    for item in list(sample.get("gold_sql") or []):
        if isinstance(item, str):
            output.append((item, []))
        elif isinstance(item, dict):
            sql = item.get("sql") or item.get("statement")
            if not sql:
                raise ValueError(f"Gold SQL item lacks SQL text: {item!r}")
            output.append((str(sql), list(item.get("params") or item.get("parameters") or [])))
        else:
            raise TypeError(f"Unsupported gold SQL item type: {type(item).__name__}")
    return output


def _compiled_statements(compiled: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    payload = compiled.get("program") if isinstance(compiled.get("program"), dict) else compiled
    statements = list(payload.get("statements") or [])
    output: list[tuple[str, list[Any]]] = []
    for item in statements:
        if not isinstance(item, dict):
            raise TypeError(f"Unsupported compiled statement type: {type(item).__name__}")
        sql = item.get("sql") or item.get("statement")
        if not sql:
            raise ValueError(f"Compiled statement lacks SQL text: {item!r}")
        output.append((str(sql), list(item.get("params") or item.get("parameters") or [])))
    return output


def _find_database_member(archive: zipfile.ZipFile, db_id: str) -> str:
    expected_suffix = f"/databases/{db_id}/{db_id}.sqlite"
    candidates = [name for name in archive.namelist() if name.endswith(expected_suffix)]
    if len(candidates) != 1:
        raise FileNotFoundError(
            f"Expected exactly one SQLite member ending with {expected_suffix!r}; found {candidates!r}"
        )
    return candidates[0]


def _counter_diff(left: Counter[tuple[Any, ...]], right: Counter[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return list((left - right).elements())


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if isinstance(value, (str, int, float, type(None))):
        return value
    return repr(value)


def _row_records(columns: list[str], rows: Iterable[tuple[Any, ...]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append({column: _jsonable_value(value) for column, value in zip(columns, row)})
    return sorted(
        output,
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _delta(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for table in sorted(set(before) | set(after)):
        before_record = before.get(table, {"columns": [], "rows": Counter()})
        after_record = after.get(table, {"columns": before_record.get("columns", []), "rows": Counter()})
        columns = list(after_record.get("columns") or before_record.get("columns") or [])
        before_rows: Counter[tuple[Any, ...]] = before_record.get("rows") or Counter()
        after_rows: Counter[tuple[Any, ...]] = after_record.get("rows") or Counter()
        added = _counter_diff(after_rows, before_rows)
        removed = _counter_diff(before_rows, after_rows)
        if not added and not removed:
            continue
        output[table] = {
            "columns": columns,
            "added": _row_records(columns, added),
            "removed": _row_records(columns, removed),
            "added_count": len(added),
            "removed_count": len(removed),
        }
    return output


def _final_difference(
    gold: dict[str, dict[str, Any]],
    predicted: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for table in sorted(set(gold) | set(predicted)):
        gold_record = gold.get(table, {"columns": [], "rows": Counter()})
        pred_record = predicted.get(table, {"columns": gold_record.get("columns", []), "rows": Counter()})
        columns = list(gold_record.get("columns") or pred_record.get("columns") or [])
        gold_rows: Counter[tuple[Any, ...]] = gold_record.get("rows") or Counter()
        pred_rows: Counter[tuple[Any, ...]] = pred_record.get("rows") or Counter()
        missing = _counter_diff(gold_rows, pred_rows)
        extra = _counter_diff(pred_rows, gold_rows)
        if not missing and not extra:
            continue
        output[table] = {
            "columns": columns,
            "missing_from_prediction": _row_records(columns, missing),
            "extra_in_prediction": _row_records(columns, extra),
            "missing_count": len(missing),
            "extra_count": len(extra),
        }
    return output


def _action_signature(delta_record: dict[str, Any] | None) -> str:
    record = delta_record or {}
    added = int(record.get("added_count") or 0)
    removed = int(record.get("removed_count") or 0)
    if added and removed:
        return "update_or_replace"
    if added:
        return "insert"
    if removed:
        return "delete"
    return "no_change"


def classify_state_diff(
    *,
    sample: dict[str, Any],
    before: dict[str, dict[str, Any]],
    gold: dict[str, dict[str, Any]],
    predicted: dict[str, dict[str, Any]],
    gold_delta: dict[str, dict[str, Any]],
    predicted_delta: dict[str, dict[str, Any]],
    difference: dict[str, dict[str, Any]],
) -> list[str]:
    target_tables = {str(table) for table in sample.get("gold_tables") or []}
    dependency_sensitive = bool(
        sample.get("multi_table")
        or "relational" in str(sample.get("complexity") or "").lower()
    )
    conflict_sensitive = bool(sample.get("conflict_sensitive"))
    classes: set[str] = set()

    for table, diff in difference.items():
        gold_action = _action_signature(gold_delta.get(table))
        pred_action = _action_signature(predicted_delta.get(table))
        missing_count = int(diff.get("missing_count") or 0)
        extra_count = int(diff.get("extra_count") or 0)

        if table not in target_tables:
            if gold_action == "no_change" and pred_action != "no_change":
                classes.add("STATE_OFF_TARGET_CHANGE")
            elif dependency_sensitive:
                if missing_count:
                    classes.add("STATE_MISSING_DEPENDENT_ROW")
                if extra_count:
                    classes.add("STATE_EXTRA_DEPENDENT_ROW")
            else:
                classes.add("STATE_OFF_TARGET_CHANGE")
            continue

        if conflict_sensitive and gold_action != pred_action:
            classes.add("STATE_WRONG_CONFLICT_BEHAVIOR")

        gold_rows = gold.get(table, {}).get("rows") or Counter()
        pred_rows = predicted.get(table, {}).get("rows") or Counter()
        if sum(gold_rows.values()) > sum(pred_rows.values()):
            classes.add("STATE_MISSING_ROW")
        elif sum(pred_rows.values()) > sum(gold_rows.values()):
            classes.add("STATE_EXTRA_ROW")
        elif missing_count and extra_count:
            gold_removed_rows = list((gold_delta.get(table) or {}).get("removed", []))
            pred_removed_rows = list((predicted_delta.get(table) or {}).get("removed", []))
            # JSON row dictionaries are not hashable, so compare stable JSON strings.
            gold_removed = {
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in gold_removed_rows
            }
            pred_removed = {
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in pred_removed_rows
            }
            if gold_removed and pred_removed and gold_removed != pred_removed:
                classes.add("STATE_WRONG_TARGET_ROW")
            else:
                classes.add("STATE_WRONG_VALUE")
        elif missing_count:
            classes.add("STATE_MISSING_ROW")
        elif extra_count:
            classes.add("STATE_EXTRA_ROW")

    if not classes and difference:
        classes.add("STATE_WRONG_VALUE")

    precedence = [
        "STATE_OFF_TARGET_CHANGE",
        "STATE_WRONG_CONFLICT_BEHAVIOR",
        "STATE_WRONG_TARGET_ROW",
        "STATE_MISSING_DEPENDENT_ROW",
        "STATE_EXTRA_DEPENDENT_ROW",
        "STATE_MISSING_ROW",
        "STATE_EXTRA_ROW",
        "STATE_WRONG_VALUE",
    ]
    return [item for item in precedence if item in classes]



def load_database_schema_ddl(holdout_zip: Path, db_id: str) -> list[dict[str, Any]]:
    """Return persistent SQLite schema objects for manual audit, from the frozen DB copy."""
    with zipfile.ZipFile(holdout_zip) as archive:
        member = _find_database_member(archive, str(db_id))
        db_bytes = archive.read(member)
    with tempfile.TemporaryDirectory(prefix="mpfsplus_schema_") as tmp_dir:
        db_path = Path(tmp_dir) / "schema.sqlite"
        db_path.write_bytes(db_bytes)
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','index','trigger','view') "
                "ORDER BY type, name"
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "type": str(row[0]),
            "name": str(row[1]),
            "table": str(row[2]),
            "sql": row[3],
        }
        for row in rows
    ]

def replay_state_diff(
    sample: dict[str, Any],
    compiled: dict[str, Any],
    holdout_zip: Path,
) -> dict[str, Any]:
    """Replay gold and prediction on isolated SQLite copies and classify final-state differences.

    This function intentionally uses only frozen artifacts. It does not call a model or mutate the
    source database. The returned deltas are suitable for manual audit and report generation.
    """
    db_id = str(sample.get("db_id") or "")
    if not db_id:
        raise ValueError(f"Sample {sample.get('id')!r} has no db_id")
    with zipfile.ZipFile(holdout_zip) as archive:
        member = _find_database_member(archive, db_id)
        db_bytes = archive.read(member)

    with tempfile.TemporaryDirectory(prefix="mpfsplus_state_diff_") as tmp_dir:
        root = Path(tmp_dir)
        before_path = root / "before.sqlite"
        gold_path = root / "gold.sqlite"
        pred_path = root / "pred.sqlite"
        before_path.write_bytes(db_bytes)
        gold_path.write_bytes(db_bytes)
        pred_path.write_bytes(db_bytes)

        before_conn = sqlite3.connect(before_path)
        gold_conn = sqlite3.connect(gold_path)
        pred_conn = sqlite3.connect(pred_path)
        try:
            before = _snapshot(before_conn)
            _execute_statements(gold_conn, _gold_statements(sample))
            _execute_statements(pred_conn, _compiled_statements(compiled))
            gold = _snapshot(gold_conn)
            predicted = _snapshot(pred_conn)
        finally:
            before_conn.close()
            gold_conn.close()
            pred_conn.close()

    gold_delta = _delta(before, gold)
    predicted_delta = _delta(before, predicted)
    difference = _final_difference(gold, predicted)
    classes = classify_state_diff(
        sample=sample,
        before=before,
        gold=gold,
        predicted=predicted,
        gold_delta=gold_delta,
        predicted_delta=predicted_delta,
        difference=difference,
    )
    return {
        "sample_id": str(sample.get("id") or ""),
        "database": db_id,
        "state_diff_classes": classes,
        "primary_class": classes[0] if classes else "STATE_MATCH",
        "gold_delta": gold_delta,
        "predicted_delta": predicted_delta,
        "difference": difference,
    }
