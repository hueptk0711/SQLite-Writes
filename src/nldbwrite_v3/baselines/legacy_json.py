from __future__ import annotations

from typing import Any

from nldbwrite_v3.schema import table_map


def _records(value: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    default_operation = value.get("operation") or value.get("operation_type")
    for record in value.get("records") or []:
        if not isinstance(record, dict):
            continue
        rows = record.get("values")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = [
                {
                    key: item
                    for key, item in record.items()
                    if key not in {"table", "operation", "operation_type"}
                }
            ]
        for row in rows:
            if isinstance(row, dict):
                output.append(
                    {
                        "table": record.get("table"),
                        "operation": record.get("operation", default_operation),
                        "values": row,
                    }
                )
    for table_group in value.get("tables") or []:
        if not isinstance(table_group, dict):
            continue
        rows = table_group.get("records") or table_group.get("rows") or []
        for row in rows:
            if isinstance(row, dict):
                output.append(
                    {
                        "table": table_group.get("table"),
                        "operation": table_group.get(
                            "operation",
                            default_operation,
                        ),
                        "values": row,
                    }
                )
    return output


def _legacy_conflict(
    table: str,
    operation: str,
    values: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    operation = operation.casefold()
    if operation in {"ignore", "insert_ignore", "do_nothing"}:
        return {"action": "do_nothing", "target": [], "update_columns": []}
    if operation not in {"upsert", "update", "replace", "upsert_update"}:
        return {"action": "error", "target": [], "update_columns": []}
    table_profile = table_map(profile).get(table) or {}
    candidates = [
        index.get("columns") or []
        for index in table_profile.get("unique_indexes") or []
        if index.get("columns")
        and all(column in values for column in index.get("columns") or [])
    ]
    if not candidates:
        return {"action": "do_update", "target": [], "update_columns": []}
    target = sorted(candidates, key=lambda item: (len(item), item))[0]
    update_columns = [column for column in values if column not in target]
    return {
        "action": "do_update" if update_columns else "do_nothing",
        "target": target,
        "update_columns": update_columns,
    }


def legacy_record_json_to_write_plan(
    predicted_json: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    groups = []
    for index, record in enumerate(_records(predicted_json), start=1):
        table = str(record.get("table") or "")
        values = dict(record.get("values") or {})
        operation = str(record.get("operation") or "insert")
        groups.append(
            {
                "group_id": f"g{index}",
                "table": table,
                "action": "insert",
                "rows": [values],
                "conflict": _legacy_conflict(
                    table,
                    operation,
                    values,
                    profile,
                ),
            }
        )
    return {
        "version": "3.0",
        "plan_kind": "legacy_record_json",
        "source": {
            "mode": "free_text",
            "format": "legacy_record_json",
            "row_count": 0,
            "evidence_required": False,
        },
        "write_groups": groups,
        "dependencies": [],
        "unresolved_fields": [],
    }

