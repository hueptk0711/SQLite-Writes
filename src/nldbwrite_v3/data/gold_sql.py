from __future__ import annotations

import re
import sqlite3
from copy import deepcopy
from typing import Any

from nldbwrite_v3.ir import Diagnostic
from nldbwrite_v3.schema import table_map


_IDENTIFIER = r'(?:"(?:[^"]|"")+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)'
_INSERT = re.compile(
    rf"^\s*INSERT\s+(?:(OR)\s+(IGNORE|REPLACE|ABORT|FAIL|ROLLBACK)\s+)?"
    rf"INTO\s+({_IDENTIFIER})",
    re.IGNORECASE | re.DOTALL,
)
_LITERAL_CONNECTION = sqlite3.connect(":memory:")


class GoldSqlParseError(ValueError):
    def __init__(self, diagnostic: Diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def unquote_identifier(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('""', '"')
    if len(value) >= 2 and value[0] == "`" and value[-1] == "`":
        return value[1:-1].replace("``", "`")
    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        return value[1:-1]
    return value


def split_sql_csv(text: str) -> list[str]:
    output: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    bracket = False
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if bracket:
            buffer.append(char)
            if char == "]":
                bracket = False
            index += 1
            continue
        if quote:
            buffer.append(char)
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    buffer.append(text[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            buffer.append(char)
        elif char == "[":
            bracket = True
            buffer.append(char)
        elif char == "(":
            depth += 1
            buffer.append(char)
        elif char == ")":
            depth -= 1
            buffer.append(char)
        elif char == "," and depth == 0:
            output.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        index += 1
    final = "".join(buffer).strip()
    if final:
        output.append(final)
    return output


def extract_parenthesized(text: str, start: int) -> tuple[str, int] | None:
    if start < 0 or start >= len(text) or text[start] != "(":
        return None
    quote: str | None = None
    bracket = False
    depth = 0
    content_start = start + 1
    index = start
    while index < len(text):
        char = text[index]
        if bracket:
            if char == "]":
                bracket = False
            index += 1
            continue
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "[":
            bracket = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[content_start:index], index + 1
        index += 1
    return None


def find_top_level_keyword(text: str, keyword: str, start: int = 0) -> int:
    quote: str | None = None
    bracket = False
    depth = 0
    lowered = keyword.casefold()
    index = start
    while index < len(text):
        char = text[index]
        if bracket:
            if char == "]":
                bracket = False
            index += 1
            continue
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "[":
            bracket = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and text[index : index + len(keyword)].casefold() == lowered:
            left_ok = index == 0 or not (
                text[index - 1].isalnum() or text[index - 1] == "_"
            )
            right_index = index + len(keyword)
            right_ok = right_index >= len(text) or not (
                text[right_index].isalnum() or text[right_index] == "_"
            )
            if left_ok and right_ok:
                return index
        index += 1
    return -1


def parse_literal(expression: str) -> Any:
    expression = expression.strip().rstrip(";")
    if not expression:
        raise ValueError("Empty SQL literal")
    try:
        return _LITERAL_CONNECTION.execute(
            f"SELECT {expression}"
        ).fetchone()[0]
    except sqlite3.Error as exc:
        raise ValueError(f"Unsupported SQL value expression {expression!r}: {exc}") from exc


def _conflict_policy(
    sql: str,
    values_end: int,
    insert_algorithm: str | None,
) -> dict[str, Any]:
    if insert_algorithm == "IGNORE":
        return {
            "action": "do_nothing",
            "target": [],
            "update_columns": [],
            "target_scope": "any_unique",
        }
    if insert_algorithm in {"REPLACE", "ABORT", "FAIL", "ROLLBACK"}:
        raise ValueError(f"Unsupported INSERT OR algorithm: {insert_algorithm}")
    conflict_position = find_top_level_keyword(sql, "ON CONFLICT", values_end)
    if conflict_position < 0:
        return {
            "action": "error",
            "target": [],
            "update_columns": [],
        }
    cursor = conflict_position + len("ON CONFLICT")
    while cursor < len(sql) and sql[cursor].isspace():
        cursor += 1
    target: list[str] = []
    if cursor < len(sql) and sql[cursor] == "(":
        target_group = extract_parenthesized(sql, cursor)
        if not target_group:
            raise ValueError("Unclosed ON CONFLICT target")
        target_text, cursor = target_group
        target = [
            unquote_identifier(item)
            for item in split_sql_csv(target_text)
            if item.strip()
        ]
    do_nothing = find_top_level_keyword(sql, "DO NOTHING", cursor)
    do_update = find_top_level_keyword(sql, "DO UPDATE", cursor)
    if do_nothing >= 0 and (do_update < 0 or do_nothing < do_update):
        return {
            "action": "do_nothing",
            "target": target,
            "update_columns": [],
        }
    if do_update < 0:
        raise ValueError("ON CONFLICT is missing DO NOTHING or DO UPDATE")
    set_position = find_top_level_keyword(sql, "SET", do_update + len("DO UPDATE"))
    if set_position < 0:
        raise ValueError("DO UPDATE is missing SET")
    update_end = len(sql)
    for keyword in ("WHERE", "RETURNING"):
        position = find_top_level_keyword(sql, keyword, set_position + len("SET"))
        if position >= 0:
            update_end = min(update_end, position)
    update_text = sql[set_position + len("SET") : update_end].strip().rstrip(";")
    update_columns: list[str] = []
    for assignment in split_sql_csv(update_text):
        if "=" not in assignment:
            raise ValueError(f"Invalid update assignment: {assignment!r}")
        update_columns.append(unquote_identifier(assignment.split("=", 1)[0]))
    return {
        "action": "do_update",
        "target": target,
        "update_columns": update_columns,
    }


def parse_insert_statement(
    sql: str,
    *,
    group_id: str = "g1",
) -> dict[str, Any]:
    match = _INSERT.search(sql)
    if not match:
        raise GoldSqlParseError(
            Diagnostic(
                "UNSUPPORTED_GOLD_SQL",
                "Only explicit INSERT ... VALUES statements are supported.",
                path="/gold_sql",
                details={"sql": sql},
            )
        )
    insert_algorithm = match.group(2).upper() if match.group(2) else None
    table = unquote_identifier(match.group(3))
    column_start = sql.find("(", match.end())
    column_group = extract_parenthesized(sql, column_start)
    if not column_group:
        raise GoldSqlParseError(
            Diagnostic(
                "MISSING_INSERT_COLUMNS",
                f"Could not parse INSERT columns for table {table}.",
                path="/gold_sql",
                group_id=group_id,
                table=table,
            )
        )
    column_text, after_columns = column_group
    columns = [
        unquote_identifier(item)
        for item in split_sql_csv(column_text)
        if item.strip()
    ]
    values_position = find_top_level_keyword(sql, "VALUES", after_columns)
    if values_position < 0:
        raise GoldSqlParseError(
            Diagnostic(
                "MISSING_VALUES",
                f"INSERT into {table} does not use an explicit VALUES clause.",
                path="/gold_sql",
                group_id=group_id,
                table=table,
            )
        )
    cursor = values_position + len("VALUES")
    rows: list[dict[str, Any]] = []
    values_end = cursor
    try:
        while cursor < len(sql):
            while cursor < len(sql) and (sql[cursor].isspace() or sql[cursor] == ","):
                cursor += 1
            if cursor >= len(sql) or sql[cursor] != "(":
                break
            value_group = extract_parenthesized(sql, cursor)
            if not value_group:
                raise ValueError("Unclosed VALUES row")
            value_text, cursor = value_group
            expressions = split_sql_csv(value_text)
            if len(expressions) != len(columns):
                raise ValueError(
                    f"Expected {len(columns)} values but found {len(expressions)}"
                )
            rows.append(
                {
                    column: parse_literal(expression)
                    for column, expression in zip(columns, expressions)
                }
            )
            values_end = cursor
        if not rows:
            raise ValueError("VALUES clause contains no rows")
        conflict = _conflict_policy(sql, values_end, insert_algorithm)
    except ValueError as exc:
        raise GoldSqlParseError(
            Diagnostic(
                "GOLD_SQL_PARSE_ERROR",
                f"{table}: {exc}",
                path="/gold_sql",
                group_id=group_id,
                table=table,
                details={"sql": sql},
            )
        ) from exc
    return {
        "group_id": group_id,
        "table": table,
        "action": "insert",
        "rows": rows,
        "conflict": conflict,
        "provenance": [
            {
                "source_row_index": index,
                "value_sources": {
                    column: {"kind": "gold_sql"} for column in row
                },
            }
            for index, row in enumerate(rows)
        ],
    }


def _infer_dependencies(
    groups: list[dict[str, Any]],
    profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not profile:
        return []
    profiles = table_map(profile)
    dependencies: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for child in groups:
        table_profile = profiles.get(str(child["table"]))
        if not table_profile:
            continue
        for foreign_key in table_profile.get("foreign_keys", []):
            parent_table = foreign_key.get("to_table")
            for parent in groups:
                if parent["group_id"] == child["group_id"]:
                    continue
                if parent["table"] != parent_table:
                    continue
                key = (
                    str(parent["group_id"]),
                    str(child["group_id"]),
                    str(foreign_key.get("from_column")),
                    str(foreign_key.get("to_column")),
                )
                if key in seen:
                    continue
                seen.add(key)
                dependencies.append(
                    {
                        "before": parent["group_id"],
                        "after": child["group_id"],
                        "foreign_key": {
                            "from_columns": [foreign_key.get("from_column")],
                            "to_columns": [foreign_key.get("to_column")],
                        },
                        "inferred": True,
                    }
                )
    return dependencies


def parse_gold_sql(
    statements: list[str],
    *,
    sample_id: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    groups = [
        parse_insert_statement(statement, group_id=f"g{index + 1}")
        for index, statement in enumerate(statements)
    ]
    return {
        "version": "3.0",
        "plan_kind": "gold_write_plan",
        "sample_id": sample_id,
        "source": {"mode": "free_text", "format": "gold_sql", "row_count": 0},
        "write_groups": groups,
        "dependencies": _infer_dependencies(groups, profile),
        "unresolved_fields": [],
    }


def parse_gold_dataset(
    samples: list[dict[str, Any]],
    *,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plans: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample.get("id") or "")
        try:
            plan = parse_gold_sql(
                list(sample.get("gold_sql") or []),
                sample_id=sample_id,
                profile=(profiles or {}).get(str(sample.get("db_id") or "")),
            )
            plans.append(plan)
        except GoldSqlParseError as exc:
            diagnostic = exc.diagnostic.to_dict()
            diagnostic["sample_id"] = sample_id
            diagnostic["db_id"] = sample.get("db_id")
            diagnostics.append(diagnostic)
    return plans, diagnostics

