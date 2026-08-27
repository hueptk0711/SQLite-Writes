from __future__ import annotations

from typing import Any

from .reference_validation import column_refs, constraint_refs, evidence_refs, require_ref, slot_refs, table_refs
from .types import SchemaInventory, SlotBundle, V2A1Error


OPERATORS = {"EQ", "NE", "LT", "GT"}
CONNECTORS = {"AND", "OR"}


def dynamic_schema(operation: str, inventory: SchemaInventory, slots: SlotBundle) -> dict[str, Any]:
    base_refs = {
        "table_refs": sorted(table_refs(inventory)),
        "column_refs": sorted(column_refs(inventory)),
        "constraint_refs": sorted(constraint_refs(inventory)),
        "evidence_refs": sorted(evidence_refs(slots)),
        "slot_refs": sorted(slot_refs(slots)),
    }
    return {"operation": operation, "dynamic_enums": base_refs, "additionalProperties": False}


def validate_phase_m_ir(ir: Any, operation: str, inventory: SchemaInventory, slots: SlotBundle) -> dict[str, Any]:
    if not isinstance(ir, dict):
        raise V2A1Error("phase_m_schema_failure", "Phase M output must be a JSON object")
    if ir.get("operation") != operation:
        raise V2A1Error("phase_m_schema_failure", "Phase M operation must equal predicted Phase O operation")
    if operation == "INSERT":
        _require_keys(ir, {"operation", "table_ref", "assignments"})
        _validate_table(ir, inventory)
        _validate_assignments(ir["assignments"], inventory, slots, min_items=1)
    elif operation == "UPDATE":
        _require_keys(ir, {"operation", "table_ref", "row_selector", "assignments"})
        _validate_table(ir, inventory)
        _validate_selector(ir["row_selector"], inventory, slots)
        _validate_assignments(ir["assignments"], inventory, slots, min_items=1)
    elif operation == "DELETE":
        _require_keys(ir, {"operation", "table_ref", "row_selector"})
        _validate_table(ir, inventory)
        _validate_selector(ir["row_selector"], inventory, slots)
    elif operation == "UPSERT":
        _validate_upsert(ir, inventory, slots)
    else:
        raise V2A1Error("phase_m_schema_failure", "Unsupported operation")
    return ir


def _require_keys(obj: dict[str, Any], expected: set[str]) -> None:
    keys = set(obj)
    if keys != expected:
        raise V2A1Error("phase_m_schema_failure", "Object keys do not match schema", details={"extra": sorted(keys - expected), "missing": sorted(expected - keys)})


def _validate_table(ir: dict[str, Any], inventory: SchemaInventory) -> None:
    require_ref(str(ir.get("table_ref")), table_refs(inventory), "table")


def _validate_assignment(item: Any, inventory: SchemaInventory, slots: SlotBundle) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise V2A1Error("phase_m_schema_failure", "Assignment must be an object")
    _require_keys(item, {"column_ref", "evidence_ref", "slot_ref"})
    require_ref(str(item["column_ref"]), column_refs(inventory), "column")
    require_ref(str(item["evidence_ref"]), evidence_refs(slots), "evidence")
    require_ref(str(item["slot_ref"]), slot_refs(slots), "slot")
    return str(item["column_ref"]), str(item["slot_ref"])


def _validate_assignments(assignments: Any, inventory: SchemaInventory, slots: SlotBundle, *, min_items: int) -> None:
    if not isinstance(assignments, list) or len(assignments) < min_items:
        raise V2A1Error("phase_m_schema_failure", "assignments must be a non-empty list")
    seen_columns: set[str] = set()
    seen_slots: set[str] = set()
    for item in assignments:
        column_ref, slot_ref = _validate_assignment(item, inventory, slots)
        if column_ref in seen_columns:
            raise V2A1Error("completeness_duplicate_column", "Target columns must be unique within assignments")
        if slot_ref in seen_slots:
            raise V2A1Error("completeness_duplicate_slot", "A slot may be assigned at most once")
        seen_columns.add(column_ref)
        seen_slots.add(slot_ref)


def _validate_predicate(item: Any, inventory: SchemaInventory, slots: SlotBundle) -> str:
    if not isinstance(item, dict):
        raise V2A1Error("phase_m_schema_failure", "Predicate must be an object")
    _require_keys(item, {"column_ref", "operator", "evidence_ref", "slot_ref"})
    require_ref(str(item["column_ref"]), column_refs(inventory), "column")
    require_ref(str(item["evidence_ref"]), evidence_refs(slots), "evidence")
    require_ref(str(item["slot_ref"]), slot_refs(slots), "slot")
    if item["operator"] not in OPERATORS:
        raise V2A1Error("phase_m_schema_failure", "Unsupported predicate operator")
    return str(item["slot_ref"])


def _validate_selector(selector: Any, inventory: SchemaInventory, slots: SlotBundle) -> None:
    if not isinstance(selector, dict):
        raise V2A1Error("phase_m_schema_failure", "row_selector must be an object")
    _require_keys(selector, {"connector", "predicates"})
    if selector["connector"] not in CONNECTORS:
        raise V2A1Error("phase_m_schema_failure", "Unsupported row selector connector")
    predicates = selector["predicates"]
    if not isinstance(predicates, list) or not predicates:
        raise V2A1Error("phase_m_schema_failure", "row_selector.predicates must be non-empty")
    seen_slots: set[str] = set()
    for item in predicates:
        slot_ref = _validate_predicate(item, inventory, slots)
        if slot_ref in seen_slots:
            raise V2A1Error("completeness_duplicate_slot", "Predicate slot_ref values must be unique")
        seen_slots.add(slot_ref)


def _validate_upsert(ir: dict[str, Any], inventory: SchemaInventory, slots: SlotBundle) -> None:
    allowed = {"operation", "table_ref", "conflict_target_ref", "insert_assignments", "update_policy", "update_assignments"}
    required = {"operation", "table_ref", "conflict_target_ref", "insert_assignments", "update_policy"}
    keys = set(ir)
    if not required.issubset(keys) or keys - allowed:
        raise V2A1Error("phase_m_schema_failure", "UPSERT keys do not match schema", details={"extra": sorted(keys - allowed), "missing": sorted(required - keys)})
    _validate_table(ir, inventory)
    require_ref(str(ir["conflict_target_ref"]), constraint_refs(inventory), "constraint")
    _validate_assignments(ir["insert_assignments"], inventory, slots, min_items=1)
    if ir["update_policy"] == "DO_NOTHING":
        if "update_assignments" in ir:
            raise V2A1Error("phase_m_schema_failure", "DO_NOTHING forbids update_assignments")
    elif ir["update_policy"] == "DO_UPDATE":
        if "update_assignments" not in ir:
            raise V2A1Error("phase_m_schema_failure", "DO_UPDATE requires update_assignments")
        _validate_assignments(ir["update_assignments"], inventory, slots, min_items=1)
    else:
        raise V2A1Error("phase_m_schema_failure", "Unsupported UPSERT update_policy")
