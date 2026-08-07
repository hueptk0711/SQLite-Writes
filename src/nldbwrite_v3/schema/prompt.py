from __future__ import annotations

from typing import Any

from .profile import ensure_reference_ids


def serialize_prompt_schema(
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """One schema view shared by every LLM method."""
    ensure_reference_ids(profile)
    return [
        {
            "table_id": table.get("table_id"),
            "table": table.get("name"),
            "columns": [
                {
                    "column_id": column.get("column_id"),
                    "name": column.get("name"),
                    "type": column.get("type"),
                    "semantic_type": column.get("semantic_type"),
                    "default": column.get("default"),
                    "not_null": bool(column.get("not_null")),
                }
                for column in table.get("columns", [])
                if column.get("is_insertable", True)
            ],
            "unique_indexes": [
                {
                    "constraint_id": index.get("constraint_id"),
                    "name": index.get("name"),
                    "columns": index.get("columns"),
                    "column_ids": [
                        next(
                            (
                                column.get("column_id")
                                for column in table.get("columns", [])
                                if column.get("name") == column_name
                            ),
                            None,
                        )
                        for column_name in index.get("columns") or []
                    ],
                }
                for index in table.get("unique_indexes", [])
            ],
            "foreign_keys": table.get("foreign_keys", []),
        }
        for table in profile.get("tables", [])
    ]
