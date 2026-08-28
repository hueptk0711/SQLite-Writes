from __future__ import annotations

from typing import Any

from .inventories import column, constraint, table_name
from .types import MaterializedValue, SQLiteProgram, SchemaInventory


OPERATOR_SQL = {"EQ": "=", "NE": "!=", "LT": "<", "GT": ">"}


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def compile_sqlite_program(ir: dict[str, Any], inventory: SchemaInventory, values: dict[str, MaterializedValue]) -> SQLiteProgram:
    operation = ir["operation"]
    table = quote_identifier(table_name(inventory, ir["table_ref"]))
    if operation == "INSERT":
        cols, params = _assignment_columns_and_params(ir["assignments"], inventory, values)
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in params)})"
    elif operation == "UPDATE":
        cols, params = _assignment_columns_and_params(ir["assignments"], inventory, values)
        set_sql = ",".join(f"{col}=?" for col in cols)
        where_sql, where_params = _where(ir["row_selector"], inventory, values)
        params = tuple(params) + tuple(where_params)
        sql = f"UPDATE {table} SET {set_sql} WHERE {where_sql}"
    elif operation == "DELETE":
        where_sql, params = _where(ir["row_selector"], inventory, values)
        sql = f"DELETE FROM {table} WHERE {where_sql}"
    elif operation == "UPSERT":
        insert_cols, insert_params = _assignment_columns_and_params(ir["insert_assignments"], inventory, values)
        conflict_cols = ",".join(quote_identifier(column(inventory, ref).name) for ref in constraint(inventory, ir["conflict_target_ref"]).column_refs)
        params = tuple(insert_params)
        if ir["update_policy"] == "DO_NOTHING":
            sql = f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_params)}) ON CONFLICT ({conflict_cols}) DO NOTHING"
        else:
            update_cols, update_params = _assignment_columns_and_params(ir["update_assignments"], inventory, values)
            params = tuple(insert_params) + tuple(update_params)
            update_sql = ",".join(f"{col}=?" for col in update_cols)
            sql = f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_params)}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_sql}"
    else:
        raise AssertionError(operation)
    normalized = f"{operation}|{sql}|{repr(tuple(params))}"
    return SQLiteProgram(operation=operation, sql=sql, parameters=tuple(params), normalized=normalized)


def _assignment_columns_and_params(assignments: list[dict[str, str]], inventory: SchemaInventory, values: dict[str, MaterializedValue]) -> tuple[list[str], tuple[Any, ...]]:
    cols: list[str] = []
    params: list[Any] = []
    for item in assignments:
        cols.append(quote_identifier(column(inventory, item["column_ref"]).name))
        params.append(values[item["evidence_ref"]].value)
    return cols, tuple(params)


def _where(selector: dict[str, Any], inventory: SchemaInventory, values: dict[str, MaterializedValue]) -> tuple[str, tuple[Any, ...]]:
    parts: list[str] = []
    params: list[Any] = []
    connector = f" {selector['connector']} "
    for item in selector["predicates"]:
        parts.append(f"{quote_identifier(column(inventory, item['column_ref']).name)} {OPERATOR_SQL[item['operator']]} ?")
        params.append(values[item["evidence_ref"]].value)
    return connector.join(parts), tuple(params)
