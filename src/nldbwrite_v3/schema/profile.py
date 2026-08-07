from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


IDENTIFIER_HINTS = (
    "id",
    "code",
    "phone",
    "zip",
    "postal",
    "account",
    "document",
    "number",
)
DATE_KEY_HINTS = ("date", "month", "year")


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _canonical_identifier(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def limited_identifier_match(
    name: str | None,
    candidates: Iterable[str],
) -> tuple[str | None, list[str]]:
    """Match exact/case/punctuation variants only, never semantic aliases."""
    choices = list(candidates)
    if name is None:
        return None, []
    if name in choices:
        return name, []
    case_matches = [item for item in choices if item.casefold() == str(name).casefold()]
    if len(case_matches) == 1:
        return case_matches[0], []
    canonical = _canonical_identifier(str(name))
    punctuation_matches = [
        item for item in choices if _canonical_identifier(item) == canonical
    ]
    if len(punctuation_matches) == 1:
        return punctuation_matches[0], []
    suggestions = sorted(
        choices,
        key=lambda item: (
            0 if canonical and canonical in _canonical_identifier(item) else 1,
            abs(len(_canonical_identifier(item)) - len(canonical)),
            item.casefold(),
        ),
    )[:5]
    return None, suggestions


def table_map(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ensure_reference_ids(profile)
    return {
        str(table["name"]): table
        for table in profile.get("tables", [])
        if isinstance(table, dict) and table.get("name")
    }


def column_map(table_profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(column["name"]): column
        for column in table_profile.get("columns", [])
        if isinstance(column, dict) and column.get("name")
    }


def load_profile(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("tables"), list):
        raise ValueError(f"Invalid database profile: {path}")
    return ensure_reference_ids(value)


def ensure_reference_ids(profile: dict[str, Any]) -> dict[str, Any]:
    """Attach stable table, column, and constraint IDs to a schema profile.

    IDs are derived from sorted schema identifiers so they remain stable when
    a profile is serialized and reloaded. Existing IDs are validated and
    preserved to keep frozen profiles reproducible.
    """
    tables = [
        table
        for table in profile.get("tables", [])
        if isinstance(table, dict) and table.get("name")
    ]
    ordered_tables = sorted(tables, key=lambda item: str(item["name"]).casefold())
    seen_table_ids: set[str] = set()
    for table_index, table in enumerate(ordered_tables, start=1):
        table_id = str(table.get("table_id") or f"t{table_index}")
        if table_id in seen_table_ids:
            raise ValueError(f"Duplicate schema table_id: {table_id}")
        table["table_id"] = table_id
        seen_table_ids.add(table_id)

        columns = [
            column
            for column in table.get("columns", [])
            if isinstance(column, dict) and column.get("name")
        ]
        ordered_columns = sorted(
            columns,
            key=lambda item: str(item["name"]).casefold(),
        )
        seen_column_ids: set[str] = set()
        for column_index, column in enumerate(ordered_columns, start=1):
            column_id = str(
                column.get("column_id")
                or f"{table_id}.c{column_index}"
            )
            if column_id in seen_column_ids:
                raise ValueError(
                    f"Duplicate schema column_id in {table['name']}: {column_id}"
                )
            column["column_id"] = column_id
            seen_column_ids.add(column_id)

        constraints = [
            constraint
            for constraint in table.get("unique_indexes", [])
            if isinstance(constraint, dict) and constraint.get("columns")
        ]
        ordered_constraints = sorted(
            constraints,
            key=lambda item: (
                tuple(str(value).casefold() for value in item.get("columns") or []),
                str(item.get("name") or "").casefold(),
            ),
        )
        seen_constraint_ids: set[str] = set()
        for constraint_index, constraint in enumerate(
            ordered_constraints,
            start=1,
        ):
            constraint_id = str(
                constraint.get("constraint_id")
                or f"{table_id}.u{constraint_index}"
            )
            if constraint_id in seen_constraint_ids:
                raise ValueError(
                    "Duplicate schema constraint_id in "
                    f"{table['name']}: {constraint_id}"
                )
            constraint["constraint_id"] = constraint_id
            seen_constraint_ids.add(constraint_id)
    profile["reference_contract"] = "schema-ids-v1"
    return profile


def profile_with_reference_ids(profile: dict[str, Any]) -> dict[str, Any]:
    """Return an ID-enriched copy when callers must not mutate frozen input."""
    return ensure_reference_ids(deepcopy(profile))


def table_reference_map(
    profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    ensure_reference_ids(profile)
    return {
        str(table["table_id"]): table
        for table in profile.get("tables", [])
        if isinstance(table, dict) and table.get("table_id")
    }


def column_reference_map(
    table_profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(column["column_id"]): column
        for column in table_profile.get("columns", [])
        if isinstance(column, dict) and column.get("column_id")
    }


def constraint_reference_map(
    table_profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(constraint["constraint_id"]): constraint
        for constraint in table_profile.get("unique_indexes", [])
        if isinstance(constraint, dict) and constraint.get("constraint_id")
    }


def ranked_column_candidates(
    source_field: str,
    table_profile: dict[str, Any],
    *,
    limit: int = 5,
) -> list[str]:
    """Return deterministic lexical candidates plus an explicit NONE option."""
    canonical_source = _canonical_identifier(source_field)
    source_tokens = {
        token
        for token in re.split(r"[\W_]+", str(source_field).casefold())
        if token
    }
    scored: list[tuple[float, str, str]] = []
    for column in table_profile.get("columns", []):
        if (
            not isinstance(column, dict)
            or not column.get("column_id")
            or not column.get("is_insertable", True)
        ):
            continue
        name = str(column.get("name") or "")
        canonical_name = _canonical_identifier(name)
        name_tokens = {
            token
            for token in re.split(r"[\W_]+", name.casefold())
            if token
        }
        token_score = (
            len(source_tokens & name_tokens) / len(source_tokens | name_tokens)
            if source_tokens or name_tokens
            else 0.0
        )
        sequence_score = SequenceMatcher(
            None,
            canonical_source,
            canonical_name,
        ).ratio()
        exact_bonus = 1.0 if canonical_source == canonical_name else 0.0
        score = exact_bonus + max(token_score, sequence_score)
        scored.append(
            (
                -score,
                name.casefold(),
                str(column["column_id"]),
            )
        )
    candidates = [item[2] for item in sorted(scored)[: max(1, limit)]]
    return [*candidates, "NONE"]


def infer_semantic_type(name: str, declared_type: str) -> tuple[str, bool]:
    normalized = _canonical_identifier(name)
    declared = (declared_type or "").upper()
    if any(hint in normalized for hint in IDENTIFIER_HINTS):
        return "identifier", True
    if any(hint in normalized for hint in DATE_KEY_HINTS) and "TEXT" in declared:
        return "date_key", True
    if "BOOL" in declared:
        return "boolean", False
    if any(token in normalized for token in ("count", "amount", "score", "quantity", "percent")):
        return "measure", False
    return "text" if "TEXT" in declared else "unknown", "TEXT" in declared


def _required_columns(columns: list[dict[str, Any]]) -> list[str]:
    primary_keys = [column for column in columns if column["is_primary_key"]]
    required: list[str] = []
    for column in columns:
        declared = str(column.get("type") or "").upper()
        auto_integer_pk = (
            len(primary_keys) == 1
            and column["is_primary_key"]
            and "INT" in declared
        )
        if auto_integer_pk:
            continue
        if column.get("default") is not None:
            continue
        if column.get("not_null") or column.get("is_primary_key"):
            required.append(str(column["name"]))
    return required


def build_profile(
    db_path: str | Path,
    db_id: str | None = None,
) -> dict[str, Any]:
    path = Path(db_path)
    conn = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        output: list[dict[str, Any]] = []
        for table_name in tables:
            raw_columns = conn.execute(
                f"PRAGMA table_xinfo({quote_identifier(table_name)})"
            ).fetchall()
            columns: list[dict[str, Any]] = []
            for cid, name, declared_type, not_null, default, pk_order, hidden in raw_columns:
                semantic_type, preserve_as_text = infer_semantic_type(name, declared_type)
                columns.append(
                    {
                        "name": name,
                        "type": declared_type or "",
                        "not_null": bool(not_null),
                        "default": default,
                        "is_primary_key": bool(pk_order),
                        "pk_order": int(pk_order),
                        "hidden": int(hidden),
                        "is_generated": int(hidden) in {2, 3},
                        "is_insertable": int(hidden) == 0,
                        "semantic_type": semantic_type,
                        "preserve_as_text": preserve_as_text,
                    }
                )
            primary_keys = [
                column["name"]
                for column in sorted(columns, key=lambda item: item["pk_order"])
                if column["is_primary_key"]
            ]
            unique_indexes: list[dict[str, Any]] = []
            if primary_keys:
                unique_indexes.append(
                    {
                        "name": "PRIMARY_KEY",
                        "columns": primary_keys,
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                )
            for index_row in conn.execute(
                f"PRAGMA index_list({quote_identifier(table_name)})"
            ):
                _, index_name, is_unique, origin, partial = index_row[:5]
                if not is_unique or partial:
                    continue
                index_columns = [
                    row[2]
                    for row in conn.execute(
                        f"PRAGMA index_info({quote_identifier(index_name)})"
                    )
                    if row[2] is not None
                ]
                if index_columns and index_columns != primary_keys:
                    unique_indexes.append(
                        {
                            "name": index_name,
                            "columns": index_columns,
                            "origin": origin,
                            "is_primary_key": origin == "pk",
                        }
                    )
            foreign_keys = [
                {
                    "from_column": row[3],
                    "to_table": row[2],
                    "to_column": row[4],
                }
                for row in conn.execute(
                    f"PRAGMA foreign_key_list({quote_identifier(table_name)})"
                )
            ]
            output.append(
                {
                    "name": table_name,
                    "columns": columns,
                    "primary_keys": primary_keys,
                    "unique_indexes": unique_indexes,
                    "foreign_keys": foreign_keys,
                    "required_insert_columns": _required_columns(columns),
                }
            )
        return ensure_reference_ids({
            "db_id": db_id or path.stem,
            "db_path": str(path),
            "tables": output,
        })
    finally:
        conn.close()
