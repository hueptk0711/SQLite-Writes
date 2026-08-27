from __future__ import annotations

from collections import Counter
from typing import Any

from .types import SlotBundle, V2A1Error


def mapped_slot_refs(ir: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("assignments", "insert_assignments", "update_assignments"):
        refs.extend(str(item["slot_ref"]) for item in ir.get(key, []))
    selector = ir.get("row_selector")
    if selector:
        refs.extend(str(item["slot_ref"]) for item in selector.get("predicates", []))
    return refs


def verify_completeness(ir: dict[str, Any], slots: SlotBundle) -> None:
    required = {slot.slot_ref for slot in slots.slots if slot.required}
    mapped = mapped_slot_refs(ir)
    counts = Counter(mapped)
    unknown = sorted(set(mapped) - {slot.slot_ref for slot in slots.slots})
    if unknown:
        raise V2A1Error("completeness_unknown_slot", "Mapped SLOT ids must exist", details={"unknown": unknown})
    duplicates = sorted(ref for ref, count in counts.items() if count > 1)
    if duplicates:
        raise V2A1Error("completeness_duplicate_slot", "Each SLOT must be mapped exactly once", details={"duplicates": duplicates})
    missing = sorted(required - set(mapped))
    if missing:
        raise V2A1Error("completeness_missing_slot", "Each required SLOT must be mapped exactly once", details={"missing": missing})
