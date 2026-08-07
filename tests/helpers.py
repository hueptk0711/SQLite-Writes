from __future__ import annotations

from typing import Any


def test_profile() -> dict[str, Any]:
    return {
        "db_id": "test",
        "tables": [
            {
                "name": "parent",
                "columns": [
                    {
                        "name": "id",
                        "type": "TEXT",
                        "is_primary_key": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "name",
                        "type": "TEXT",
                        "not_null": True,
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "count",
                        "type": "INTEGER",
                        "is_insertable": True,
                        "semantic_type": "count",
                        "preserve_as_text": False,
                    },
                ],
                "required_insert_columns": ["id", "name"],
                "primary_keys": ["id"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["id"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [],
            },
            {
                "name": "child",
                "columns": [
                    {
                        "name": "id",
                        "type": "INTEGER",
                        "is_primary_key": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                    },
                    {
                        "name": "parent_id",
                        "type": "TEXT",
                        "not_null": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "note",
                        "type": "TEXT",
                        "not_null": True,
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    },
                ],
                "required_insert_columns": ["parent_id", "note"],
                "primary_keys": ["id"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["id"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [
                    {
                        "from_column": "parent_id",
                        "to_table": "parent",
                        "to_column": "id",
                    }
                ],
            },
            {
                "name": "pair",
                "columns": [
                    {
                        "name": "a",
                        "type": "TEXT",
                        "is_primary_key": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "b",
                        "type": "TEXT",
                        "is_primary_key": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "value",
                        "type": "TEXT",
                        "not_null": True,
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    },
                ],
                "required_insert_columns": ["a", "b", "value"],
                "primary_keys": ["a", "b"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["a", "b"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [],
            },
        ],
    }


test_profile.__test__ = False


def conflict(
    action: str = "error",
    target: list[str] | None = None,
    update_columns: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "target": target or [],
        "update_columns": update_columns or [],
    }


def group(
    group_id: str,
    table: str,
    rows: list[dict[str, Any]],
    conflict_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "table": table,
        "action": "insert",
        "rows": rows,
        "conflict": conflict_policy or conflict(),
    }


def plan(
    groups: list[dict[str, Any]],
    dependencies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": "3.0",
        "plan_kind": "test",
        "source": {"mode": "free_text", "format": "free_text", "row_count": 0},
        "write_groups": groups,
        "dependencies": dependencies or [],
        "unresolved_fields": [],
    }
