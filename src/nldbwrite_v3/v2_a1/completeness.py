from __future__ import annotations

from typing import Any

from .types import SlotBundle, V2A1Error


ASSIGNMENT_CONTEXTS = ("assignments", "insert_assignments", "update_assignments")


def mapped_slot_refs(ir: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for context_refs in slot_refs_by_context(ir).values():
        refs.extend(context_refs)
    return refs


def slot_refs_by_context(ir: dict[str, Any]) -> dict[str, list[str]]:
    by_context: dict[str, list[str]] = {}
    for key in ("assignments", "insert_assignments", "update_assignments"):
        values = [str(item["slot_ref"]) for item in ir.get(key, [])]
        if values:
            by_context[key] = values
    selector = ir.get("row_selector")
    if selector:
        values = [str(item["slot_ref"]) for item in selector.get("predicates", [])]
        if values:
            by_context["row_selector.predicates"] = values
    return by_context


def assignment_column_refs_by_context(ir: dict[str, Any]) -> dict[str, list[str]]:
    by_context: dict[str, list[str]] = {}
    for key in ASSIGNMENT_CONTEXTS:
        values = [str(item["column_ref"]) for item in ir.get(key, [])]
        if values:
            by_context[key] = values
    return by_context


def verify_completeness(ir: dict[str, Any], slots: SlotBundle) -> None:
    required = {slot.slot_ref for slot in slots.slots if slot.required}
    mapped = mapped_slot_refs(ir)
    unknown = sorted(set(mapped) - {slot.slot_ref for slot in slots.slots})
    if unknown:
        raise V2A1Error("completeness_unknown_slot", "Mapped SLOT ids must exist", details={"unknown": unknown})
    for context, refs in slot_refs_by_context(ir).items():
        duplicates = _duplicates(refs)
        if duplicates:
            raise V2A1Error("completeness_duplicate_slot", "Each SLOT must be mapped at most once within a mapping context", details={"context": context, "duplicates": duplicates})
    for context, refs in assignment_column_refs_by_context(ir).items():
        duplicates = _duplicates(refs)
        if duplicates:
            raise V2A1Error("completeness_duplicate_column", "Target columns must be unique within an assignment context", details={"context": context, "duplicates": duplicates})
    missing = sorted(required - set(mapped))
    if missing:
        raise V2A1Error("completeness_missing_slot", "Each required SLOT must be mapped exactly once", details={"missing": missing})


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
