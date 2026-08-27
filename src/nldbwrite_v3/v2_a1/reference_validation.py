from __future__ import annotations

from .types import SchemaInventory, SlotBundle, V2A1Error


def require_ref(ref: str, allowed: set[str], kind: str) -> None:
    if ref not in allowed:
        raise V2A1Error("phase_m_invalid_reference", f"Unknown {kind} reference", details={"ref": ref, "kind": kind})


def slot_refs(slots: SlotBundle) -> set[str]:
    return {item.slot_ref for item in slots.slots}


def evidence_refs(slots: SlotBundle) -> set[str]:
    return {item.evidence_ref for item in slots.evidence}


def table_refs(inventory: SchemaInventory) -> set[str]:
    return {item.ref for item in inventory.tables}


def column_refs(inventory: SchemaInventory) -> set[str]:
    return {item.ref for item in inventory.columns}


def constraint_refs(inventory: SchemaInventory) -> set[str]:
    return {item.ref for item in inventory.constraints}
