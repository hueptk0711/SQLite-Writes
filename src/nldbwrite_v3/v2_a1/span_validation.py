from __future__ import annotations

from .types import AcceptedSpan, V2A1Error


def validate_and_sort_spans(question: str, spans: list[dict[str, int]]) -> tuple[AcceptedSpan, ...]:
    accepted: list[AcceptedSpan] = []
    seen: set[tuple[int, int]] = set()
    qlen = len(question)
    for span in spans:
        start = span["start_char"]
        end = span["end_char"]
        if not (0 <= start < end <= qlen):
            raise V2A1Error("phase_o_invalid_offset", "Span offsets are out of range", details={"start_char": start, "end_char": end, "question_length": qlen})
        key = (start, end)
        if key in seen:
            raise V2A1Error("phase_o_duplicate_span", "Duplicate exact offsets are forbidden", details={"start_char": start, "end_char": end})
        seen.add(key)
        accepted.append(AcceptedSpan(start_char=start, end_char=end, text=question[start:end]))
    accepted.sort(key=lambda item: (item.start_char, item.end_char))
    for left, right in zip(accepted, accepted[1:]):
        if left.end_char > right.start_char:
            raise V2A1Error(
                "phase_o_overlap",
                "Nested or partially overlapping spans are forbidden",
                details={"left": (left.start_char, left.end_char), "right": (right.start_char, right.end_char)},
            )
    return tuple(accepted)
