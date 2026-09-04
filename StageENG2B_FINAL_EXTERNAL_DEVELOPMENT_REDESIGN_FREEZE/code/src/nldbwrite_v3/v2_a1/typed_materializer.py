from __future__ import annotations

import math
import re
from typing import Any

from nldbwrite_v3.vnext.typed_normalization import normalize_free_text_typed_candidate

from .inventories import column
from .slot_inventory import evidence_text
from .types import MaterializedBinding, MaterializedValue, SchemaInventory, SlotBundle, V2A1Error


STRICT_INT = re.compile(r"[+-]?\d+")
STRICT_REAL = re.compile(r"[+-]?(?:\d+\.\d+|\d+|\.\d+)(?:[eE][+-]?\d+)?")


def sqlite_affinity(declared_type: str) -> str:
    dtype = declared_type.upper()
    if dtype in {"DATE", "DATETIME", "TIMESTAMP"}:
        return "TEXT"
    if "INT" in dtype:
        return "INTEGER"
    if any(token in dtype for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in dtype or not dtype:
        return "BLOB"
    if any(token in dtype for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def semantic_materialization_type(declared_type: str) -> str:
    dtype = " ".join(str(declared_type or "").strip().upper().split())
    if dtype in {"DATE"}:
        return "DATE"
    if dtype in {"DATETIME", "TIMESTAMP"}:
        return "DATETIME"
    if "INT" in dtype:
        return "INTEGER"
    if any(token in dtype for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    if any(token in dtype for token in ("DEC", "NUM")):
        return "NUMERIC"
    if any(token in dtype for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in dtype or not dtype:
        return "BLOB"
    return "TEXT"


def materialization_error(reason: str, message: str, *, declared_type: str, semantic_type: str) -> V2A1Error:
    return V2A1Error(
        "materialization_failure",
        message,
        details={
            "reason": reason,
            "declared_type": declared_type,
            "semantic_materialization_type": semantic_type,
        },
    )


def materialize_value(raw: str, declared_type: str, *, evidence_ref: str = "") -> MaterializedValue:
    semantic_type = semantic_materialization_type(declared_type)
    affinity = sqlite_affinity(declared_type)
    if semantic_type == "TEXT":
        return MaterializedValue(value=raw, sqlite_affinity="TEXT", evidence_ref=evidence_ref)
    if semantic_type == "DATE":
        result = normalize_free_text_typed_candidate(
            raw,
            {"type": declared_type, "semantic_type": "date"},
            requested_rule="iso_date_normalization",
            candidate_type="date",
            config={"enabled": True, "fail_closed_on_ambiguous_format": True},
            evidence_id=evidence_ref,
        )
        if result.error:
            raise materialization_error("DATE", result.error, declared_type=declared_type, semantic_type=semantic_type)
        return MaterializedValue(value=result.value, sqlite_affinity="TEXT", evidence_ref=evidence_ref)
    if semantic_type == "DATETIME":
        result = normalize_free_text_typed_candidate(
            raw,
            {"type": declared_type, "semantic_type": "datetime"},
            requested_rule="iso_date_normalization",
            candidate_type="datetime",
            config={"enabled": True, "fail_closed_on_ambiguous_format": True},
            evidence_id=evidence_ref,
        )
        if result.error:
            raise materialization_error("DATETIME", result.error, declared_type=declared_type, semantic_type=semantic_type)
        return MaterializedValue(value=result.value, sqlite_affinity="TEXT", evidence_ref=evidence_ref)
    if semantic_type == "INTEGER":
        if not STRICT_INT.fullmatch(raw):
            raise materialization_error("INTEGER", "INTEGER evidence must be a strict lossless integer literal", declared_type=declared_type, semantic_type=semantic_type)
        return MaterializedValue(value=int(raw), sqlite_affinity="INTEGER", evidence_ref=evidence_ref)
    if semantic_type in {"REAL", "NUMERIC"}:
        if not STRICT_REAL.fullmatch(raw):
            raise materialization_error(semantic_type, "Numeric evidence must be a strict finite numeric literal", declared_type=declared_type, semantic_type=semantic_type)
        value = float(raw)
        if not math.isfinite(value):
            raise materialization_error(semantic_type, "Numeric evidence must be finite", declared_type=declared_type, semantic_type=semantic_type)
        if semantic_type == "NUMERIC" and STRICT_INT.fullmatch(raw):
            return MaterializedValue(value=int(raw), sqlite_affinity="NUMERIC", evidence_ref=evidence_ref)
        return MaterializedValue(value=value, sqlite_affinity="REAL" if semantic_type == "REAL" else "NUMERIC", evidence_ref=evidence_ref)
    raise materialization_error("UNSUPPORTED", "BLOB or unsupported affinity requires a frozen representation", declared_type=declared_type, semantic_type=semantic_type)


def enforce_unique_non_omit_span_refs(column_span_refs: dict[str, str]) -> None:
    seen: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for column_ref, span_ref in sorted(column_span_refs.items()):
        if span_ref == "OMIT":
            continue
        if span_ref in seen:
            duplicates.setdefault(span_ref, [seen[span_ref]]).append(column_ref)
        else:
            seen[span_ref] = column_ref
    if duplicates:
        raise V2A1Error(
            "duplicate_span_ref",
            "Each non-OMIT SPAN ref must be used at most once across target columns",
            details={"duplicates": duplicates},
        )


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
