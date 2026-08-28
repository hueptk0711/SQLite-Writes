from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .json_schema import validate_schema_subset
from .reference_validation import column_refs, constraint_refs, evidence_refs, require_ref, slot_refs, table_refs
from .slot_inventory import evidence_ref_for_slot
from .types import SchemaInventory, SlotBundle, V2A1Error


OPERATORS = {"EQ", "NE", "LT", "GT"}
CONNECTORS = {"AND", "OR"}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_BY_OPERATION = {
    "INSERT": "insert_ir.schema.json",
    "UPDATE": "update_ir.schema.json",
    "DELETE": "delete_ir.schema.json",
    "UPSERT": "upsert_ir.schema.json",
}


def dynamic_schema(operation: str, inventory: SchemaInventory, slots: SlotBundle, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    if operation not in SCHEMA_BY_OPERATION:
        raise V2A1Error("phase_m_schema_failure", "Unsupported operation")
    schema_path = root / "stage7b_v2_method_specification" / "schemas" / SCHEMA_BY_OPERATION[operation]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    base_refs = {
        "table_refs": sorted(table_refs(inventory)),
        "column_refs": sorted(column_refs(inventory)),
        "constraint_refs": sorted(constraint_refs(inventory)),
        "evidence_refs": sorted(evidence_refs(slots)),
        "slot_refs": sorted(slot_refs(slots)),
    }
    schema = _instantiate_enums(copy.deepcopy(schema), base_refs)
    schema["x-runtime-dynamic-enums"] = base_refs
    return schema


def validate_phase_m_ir(ir: Any, operation: str, inventory: SchemaInventory, slots: SlotBundle, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    if not isinstance(ir, dict):
        raise V2A1Error("phase_m_schema_failure", "Phase M output must be a JSON object")
    validate_schema_subset(dynamic_schema(operation, inventory, slots, root=root), ir, reason_code="phase_m_schema_failure")
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
    _validate_slot_evidence_coherence(str(item["slot_ref"]), str(item["evidence_ref"]), slots)
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
    _validate_slot_evidence_coherence(str(item["slot_ref"]), str(item["evidence_ref"]), slots)
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


def _validate_slot_evidence_coherence(slot_ref: str, evidence_ref: str, slots: SlotBundle) -> None:
    expected = evidence_ref_for_slot(slots, slot_ref)
    if evidence_ref != expected:
        raise V2A1Error("phase_m_slot_evidence_mismatch", "slot_ref must use its deterministic evidence_ref", details={"slot_ref": slot_ref, "expected_evidence_ref": expected, "actual_evidence_ref": evidence_ref})


def _instantiate_enums(schema: dict[str, Any], refs: dict[str, list[str]]) -> dict[str, Any]:
    if isinstance(schema, dict):
        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            first = enum[0]
            if isinstance(first, str):
                replacement = _replacement_for_enum(first, refs)
                if replacement is not None:
                    schema["enum"] = replacement
        for value in schema.values():
            if isinstance(value, (dict, list)):
                _instantiate_enums(value, refs)
    elif isinstance(schema, list):
        for value in schema:
            if isinstance(value, (dict, list)):
                _instantiate_enums(value, refs)
    return schema


def _replacement_for_enum(example: str, refs: dict[str, list[str]]) -> list[str] | None:
    if example.startswith("TAB_"):
        return refs["table_refs"]
    if example.startswith("COL_"):
        return refs["column_refs"]
    if example.startswith("EV_"):
        return refs["evidence_refs"]
    if example.startswith("SLOT_"):
        return refs["slot_refs"]
    if example.startswith("CONSTRAINT_"):
        return refs["constraint_refs"]
    return None
