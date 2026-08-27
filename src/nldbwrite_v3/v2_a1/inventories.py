from __future__ import annotations

import re
from typing import Any

from .types import ColumnRef, ConstraintRef, SchemaInventory, TableRef, V2A1Error


FORBIDDEN_MODEL_SIDE_KEYS = {
    "agg",
    "conds",
    "crudsql_sql",
    "gold_operation",
    "gold_post_state",
    "gold_program",
    "gold_sql",
    "operation_label",
    "post_state_hash",
    "sel",
    "target_state",
}


def assert_no_gold_leakage(payload: dict[str, Any]) -> None:
    present = sorted(FORBIDDEN_MODEL_SIDE_KEYS.intersection(payload))
    if present:
        raise V2A1Error("leakage_boundary_violation", "Gold/evaluation fields are forbidden in V2-A1 model-side inputs", details={"fields": present})


def _natural_key(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)_(\d+)", value)
    return (match.group(1), int(match.group(2))) if match else (value, 0)


def build_schema_inventory(model_side_input: dict[str, Any]) -> SchemaInventory:
    assert_no_gold_leakage(model_side_input)
    schema = model_side_input.get("schema_inventory", model_side_input)
    tables_raw = schema.get("tables")
    columns_raw = schema.get("columns")
    if not isinstance(tables_raw, list) or not isinstance(columns_raw, list):
        raise V2A1Error("schema_inventory_missing", "schema_inventory must include tables and columns lists")
    tables = tuple(
        TableRef(ref=str(row["table_ref"]), name=str(row.get("table_name", row.get("name", row["table_ref"]))))
        for row in sorted(tables_raw, key=lambda item: _natural_key(str(item["table_ref"])))
    )
    columns = tuple(
        ColumnRef(
            ref=str(row["column_ref"]),
            name=str(row.get("column_name", row.get("name", row["column_ref"]))),
            source_type=str(row.get("source_type", row.get("sqlite_affinity", "TEXT"))),
        )
        for row in sorted(columns_raw, key=lambda item: _natural_key(str(item["column_ref"])))
    )
    constraints = tuple(
        ConstraintRef(ref=str(row["constraint_ref"]), column_refs=tuple(str(col) for col in row.get("column_refs", ())))
        for row in sorted(schema.get("constraints", []), key=lambda item: _natural_key(str(item["constraint_ref"])))
    )
    return SchemaInventory(tables=tables, columns=columns, constraints=constraints)


def refs_by_kind(inventory: SchemaInventory) -> dict[str, set[str]]:
    return {
        "table": {item.ref for item in inventory.tables},
        "column": {item.ref for item in inventory.columns},
        "constraint": {item.ref for item in inventory.constraints},
    }


def table_name(inventory: SchemaInventory, table_ref: str) -> str:
    for item in inventory.tables:
        if item.ref == table_ref:
            return item.name
    raise V2A1Error("phase_m_invalid_reference", "Unknown table_ref", details={"table_ref": table_ref})


def column(inventory: SchemaInventory, column_ref: str) -> ColumnRef:
    for item in inventory.columns:
        if item.ref == column_ref:
            return item
    raise V2A1Error("phase_m_invalid_reference", "Unknown column_ref", details={"column_ref": column_ref})


def constraint(inventory: SchemaInventory, constraint_ref: str) -> ConstraintRef:
    for item in inventory.constraints:
        if item.ref == constraint_ref:
            return item
    raise V2A1Error("phase_m_invalid_reference", "Unknown conflict_target_ref", details={"constraint_ref": constraint_ref})
