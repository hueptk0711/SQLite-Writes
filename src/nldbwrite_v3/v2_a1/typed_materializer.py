from __future__ import annotations

import math
import re
from typing import Any

from .inventories import column
from .slot_inventory import evidence_text
from .types import MaterializedBinding, MaterializedValue, SchemaInventory, SlotBundle, V2A1Error


STRICT_INT = re.compile(r"[+-]?\d+")
STRICT_REAL = re.compile(r"[+-]?(?:\d+\.\d+|\d+|\.\d+)(?:[eE][+-]?\d+)?")


def sqlite_affinity(declared_type: str) -> str:
    dtype = declared_type.upper()
    if "INT" in dtype:
        return "INTEGER"
    if any(token in dtype for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in dtype or not dtype:
        return "BLOB"
    if any(token in dtype for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def materialize_value(raw: str, declared_type: str, *, evidence_ref: str = "") -> MaterializedValue:
    affinity = sqlite_affinity(declared_type)
    if affinity == "TEXT":
        return MaterializedValue(value=raw, sqlite_affinity=affinity, evidence_ref=evidence_ref)
    if affinity == "INTEGER":
        if not STRICT_INT.fullmatch(raw):
            raise V2A1Error("materialization_failure", "INTEGER evidence must be a strict lossless integer literal")
        return MaterializedValue(value=int(raw), sqlite_affinity=affinity, evidence_ref=evidence_ref)
    if affinity in {"REAL", "NUMERIC"}:
        if not STRICT_REAL.fullmatch(raw):
            raise V2A1Error("materialization_failure", "Numeric evidence must be a strict finite numeric literal")
        value = float(raw)
        if not math.isfinite(value):
            raise V2A1Error("materialization_failure", "Numeric evidence must be finite")
        if affinity == "NUMERIC" and STRICT_INT.fullmatch(raw):
            return MaterializedValue(value=int(raw), sqlite_affinity=affinity, evidence_ref=evidence_ref)
        return MaterializedValue(value=value, sqlite_affinity=affinity, evidence_ref=evidence_ref)
    raise V2A1Error("materialization_failure", "BLOB or unsupported affinity requires a frozen representation")


def binding_key(context: str, index: int) -> str:
    return f"{context}[{index}]"


def materialize_ir_values(ir: dict[str, Any], inventory: SchemaInventory, slots: SlotBundle) -> dict[str, MaterializedBinding]:
    values: dict[str, MaterializedBinding] = {}
    for context, index, item in iter_value_bindings(ir):
        evidence_ref = item["evidence_ref"]
        col = column(inventory, item["column_ref"])
        materialized = materialize_value(evidence_text(slots, evidence_ref), col.source_type, evidence_ref=evidence_ref)
        key = binding_key(context, index)
        values[key] = MaterializedBinding(
            binding_key=key,
            context=context,
            index=index,
            column_ref=item["column_ref"],
            evidence_ref=evidence_ref,
            slot_ref=item["slot_ref"],
            value=materialized.value,
            sqlite_affinity=materialized.sqlite_affinity,
        )
    return values


def iter_value_bindings(ir: dict[str, Any]) -> list[tuple[str, int, dict[str, str]]]:
    items: list[tuple[str, int, dict[str, str]]] = []
    for key in ("assignments", "insert_assignments", "update_assignments"):
        items.extend((key, index, item) for index, item in enumerate(ir.get(key, [])))
    selector = ir.get("row_selector")
    if selector:
        items.extend(("row_selector.predicates", index, item) for index, item in enumerate(selector.get("predicates", [])))
    return items
