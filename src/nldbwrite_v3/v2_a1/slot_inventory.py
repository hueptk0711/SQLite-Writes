from __future__ import annotations

from .types import AcceptedSpan, EvidenceItem, SlotBundle, SlotItem


def build_slot_bundle(spans: tuple[AcceptedSpan, ...]) -> SlotBundle:
    evidence: list[EvidenceItem] = []
    slots: list[SlotItem] = []
    for index, span in enumerate(spans, start=1):
        span_ref = f"SPAN_{index}"
        evidence_ref = f"EV_{index}"
        slot_ref = f"SLOT_{index}"
        evidence.append(EvidenceItem(evidence_ref=evidence_ref, span_ref=span_ref, start_char=span.start_char, end_char=span.end_char, text=span.text))
        slots.append(SlotItem(slot_ref=slot_ref, evidence_ref=evidence_ref, required=True, start_char=span.start_char, end_char=span.end_char, text=span.text))
    return SlotBundle(evidence=tuple(evidence), slots=tuple(slots))


def evidence_text(slots: SlotBundle, evidence_ref: str) -> str:
    for item in slots.evidence:
        if item.evidence_ref == evidence_ref:
            return item.text
    raise KeyError(evidence_ref)
