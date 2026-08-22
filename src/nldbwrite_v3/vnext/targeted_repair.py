from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from nldbwrite_v3.ir import Diagnostic
from nldbwrite_v3.schema import (
    column_reference_map,
    ensure_reference_ids,
    table_reference_map,
)

from .typed_normalization import normalize_free_text_typed_candidate


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z_-]*\d[\w.-]*$", re.UNICODE)
_DIAGNOSTIC_CODE = "EVIDENCE_SPAN_TERMINAL_PUNCTUATION"
_REPAIR_RULE = "unique_pre_enumerated_terminal_punctuation_trim"
_SELECTION_DIAGNOSTIC_CODE = "TEMPORAL_EVIDENCE_SELECTION_INCOMPATIBLE"
_SELECTION_REPAIR_RULE = (
    "unique_primary_temporal_candidate_in_forward_same_sentence"
)
_SELECTION_POLICY = "temporal_type_contradiction_forward_same_sentence"
_SENTENCE_BOUNDARY = re.compile(r"[.!?;](?=\s|$)|\r?\n")


@dataclass(frozen=True, slots=True)
class DiagnosticTargetedRepairConfig:
    """Safety contract for Stage 2-G1/G2 diagnostic targeted repair."""

    enabled: bool = False
    evidence_span_boundary: bool = True
    evidence_span_selection: bool = False
    selection_policy: str = _SELECTION_POLICY
    allowed_terminal_punctuation: tuple[str, ...] = (".", ",")
    max_revalidation_attempts: int = 1
    require_deterministic_diagnostic: bool = True
    require_single_diagnosed_slot: bool = True
    require_unique_candidate: bool = True
    preserve_other_semantics: bool = True
    emit_repair_provenance: bool = True

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "DiagnosticTargetedRepairConfig":
        value = value or {}
        punctuation = value.get("allowed_terminal_punctuation", [".", ","])
        if not isinstance(punctuation, list) or not all(
            isinstance(item, str) and len(item) == 1 for item in punctuation
        ):
            raise ValueError(
                "Stage G1 allowed_terminal_punctuation must be a list of "
                "single-character strings."
            )
        selected = cls(
            enabled=bool(value.get("enabled", False)),
            evidence_span_boundary=bool(
                value.get("evidence_span_boundary", True)
            ),
            evidence_span_selection=bool(
                value.get("evidence_span_selection", False)
            ),
            selection_policy=str(
                value.get("selection_policy") or _SELECTION_POLICY
            ),
            allowed_terminal_punctuation=tuple(punctuation),
            max_revalidation_attempts=int(
                value.get("max_revalidation_attempts", 1)
            ),
            require_deterministic_diagnostic=bool(
                value.get("require_deterministic_diagnostic", True)
            ),
            require_single_diagnosed_slot=bool(
                value.get("require_single_diagnosed_slot", True)
            ),
            require_unique_candidate=bool(
                value.get("require_unique_candidate", True)
            ),
            preserve_other_semantics=bool(
                value.get("preserve_other_semantics", True)
            ),
            emit_repair_provenance=bool(
                value.get("emit_repair_provenance", True)
            ),
        )
        if selected.enabled:
            required_true = {
                "evidence_span_boundary": selected.evidence_span_boundary,
                "require_deterministic_diagnostic": (
                    selected.require_deterministic_diagnostic
                ),
                "require_single_diagnosed_slot": (
                    selected.require_single_diagnosed_slot
                ),
                "require_unique_candidate": selected.require_unique_candidate,
                "preserve_other_semantics": selected.preserve_other_semantics,
                "emit_repair_provenance": selected.emit_repair_provenance,
            }
            relaxed = [name for name, enabled in required_true.items() if not enabled]
            if relaxed:
                raise ValueError(
                    "Stage G1 safety invariants cannot be disabled: "
                    + ", ".join(relaxed)
                )
            if selected.max_revalidation_attempts != 1:
                raise ValueError(
                    "Stage G1 requires max_revalidation_attempts=1."
                )
            if set(selected.allowed_terminal_punctuation) - {".", ","}:
                raise ValueError(
                    "Stage G1 permits only terminal period and comma repair."
                )
            if (
                selected.evidence_span_selection
                and selected.selection_policy != _SELECTION_POLICY
            ):
                raise ValueError(
                    "Stage G2 requires the frozen temporal contradiction "
                    "forward-same-sentence selection policy."
                )
        return selected

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "evidence_span_boundary": self.evidence_span_boundary,
            "evidence_span_selection": self.evidence_span_selection,
            "selection_policy": self.selection_policy,
            "allowed_terminal_punctuation": list(
                self.allowed_terminal_punctuation
            ),
            "max_revalidation_attempts": self.max_revalidation_attempts,
            "require_deterministic_diagnostic": (
                self.require_deterministic_diagnostic
            ),
            "require_single_diagnosed_slot": self.require_single_diagnosed_slot,
            "require_unique_candidate": self.require_unique_candidate,
            "preserve_other_semantics": self.preserve_other_semantics,
            "emit_repair_provenance": self.emit_repair_provenance,
        }


@dataclass(slots=True)
class TargetedRepairOutcome:
    plan: dict[str, Any]
    applied: bool
    traces: list[dict[str, Any]] = field(default_factory=list)


def _candidate_index(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for candidate in candidates:
        evidence_id = str(candidate.get("evidence_id") or "")
        if not evidence_id:
            continue
        if evidence_id in output:
            duplicate_ids.add(evidence_id)
        else:
            output[evidence_id] = candidate
    for evidence_id in duplicate_ids:
        output.pop(evidence_id, None)
    return output


def _bounded_candidates(
    selected: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    config: DiagnosticTargetedRepairConfig,
) -> list[dict[str, Any]]:
    text = str(selected.get("text") or "")
    if (
        len(text) < 2
        or text[-1] not in config.allowed_terminal_punctuation
        or str(selected.get("candidate_type") or "")
        != "number_or_identifier"
    ):
        return []
    trimmed = text[:-1]
    if not _IDENTIFIER.fullmatch(trimmed):
        return []
    start = selected.get("start")
    end = selected.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return []
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if (
            candidate.get("start") == start
            and candidate.get("end") == end - 1
            and str(candidate.get("text") or "") == trimmed
            and str(candidate.get("candidate_type") or "")
            == "number_or_identifier"
            and str(candidate.get("evidence_id") or "")
        ):
            matches.append(
                {
                    "evidence_id": str(candidate["evidence_id"]),
                    "text": str(candidate["text"]),
                    "start": candidate["start"],
                    "end": candidate["end"],
                    "candidate_type": str(candidate["candidate_type"]),
                }
            )
    return matches


def diagnose_evidence_span_boundaries(
    reference_plan: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    config: DiagnosticTargetedRepairConfig,
) -> list[Diagnostic]:
    """Diagnose only pre-enumerated one-character terminal boundaries.

    The function never searches request text. A repair candidate must already
    exist in the frozen evidence candidate set at the same start offset and
    end exactly one character before the selected evidence.
    """
    if not config.enabled or not config.evidence_span_boundary:
        return []
    evidence = _candidate_index(candidates)
    diagnostics: list[Diagnostic] = []
    groups = reference_plan.get("write_groups") or []
    if not isinstance(groups, list):
        return []
    for group_index, group in enumerate(groups):
        if not isinstance(group, Mapping):
            continue
        rows = group.get("rows") or []
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            for column_id, value_spec in row.items():
                if not isinstance(value_spec, Mapping):
                    continue
                old_reference = str(value_spec.get("value_from") or "")
                selected = evidence.get(old_reference)
                if selected is None:
                    continue
                closed_set = _bounded_candidates(selected, candidates, config)
                if not closed_set:
                    continue
                path = (
                    f"/write_groups/{group_index}/rows/{row_index}/"
                    f"{column_id}/value_from"
                )
                diagnostics.append(
                    Diagnostic(
                        _DIAGNOSTIC_CODE,
                        (
                            "Selected identifier evidence includes terminal "
                            "sentence punctuation while a bounded, "
                            "pre-enumerated trimmed span exists."
                        ),
                        path=path,
                        group_id=str(group.get("group_id") or ""),
                        candidates=[item["evidence_id"] for item in closed_set],
                        details={
                            "diagnostic": _DIAGNOSTIC_CODE,
                            "semantic_slot": "evidence_reference",
                            "target_column_reference": str(column_id),
                            "old_reference": old_reference,
                            "old_value": str(selected.get("text") or ""),
                            "candidate_set": deepcopy(closed_set),
                            "repair_rule": _REPAIR_RULE,
                        },
                    )
                )
    return diagnostics


def _used_evidence_paths(
    reference_plan: Mapping[str, Any],
) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    groups = reference_plan.get("write_groups") or []
    if not isinstance(groups, list):
        return used
    for group_index, group in enumerate(groups):
        if not isinstance(group, Mapping):
            continue
        rows = group.get("rows") or []
        if not isinstance(rows, list):
            continue
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            for column_id, value_spec in row.items():
                if not isinstance(value_spec, Mapping):
                    continue
                evidence_id = str(value_spec.get("value_from") or "")
                if evidence_id:
                    path = (
                        f"/write_groups/{group_index}/rows/{row_index}/"
                        f"{column_id}/value_from"
                    )
                    used.setdefault(evidence_id, []).append(path)
    return used


def _trace_from_diagnostic(
    diagnostic: Diagnostic,
    *,
    repair_rule: str,
    reason: str,
    selected_repair: str | None = None,
) -> dict[str, Any]:
    details = diagnostic.details
    return {
        "stage2_intervention": "G1_evidence_span_boundary_repair",
        "diagnostic": diagnostic.error_code,
        "diagnosed_slot": diagnostic.path,
        "semantic_slot": str(details.get("semantic_slot") or ""),
        "target_column_reference": str(
            details.get("target_column_reference") or ""
        ),
        "old_reference": str(details.get("old_reference") or ""),
        "old_value": deepcopy(details.get("old_value")),
        "candidate_set": deepcopy(details.get("candidate_set") or []),
        "selected_repair": selected_repair,
        "repair_rule": repair_rule,
        "repair_reason": reason,
        "repair_attempted": True,
        "repair_applied": False,
        "repair_succeeded": False,
        "revalidation_result": "NOT_RUN",
        "revalidation_attempts": 0,
        "atomic_rollback": False,
        "max_revalidation_attempts": 1,
    }


def repair_evidence_span_boundary_after_diagnostic(
    reference_plan: Mapping[str, Any],
    diagnostics: Sequence[Diagnostic],
    config: DiagnosticTargetedRepairConfig,
) -> TargetedRepairOutcome:
    """Deep-copy and change exactly one diagnosed ``value_from`` slot."""
    original = deepcopy(dict(reference_plan))
    eligible = [
        item for item in diagnostics if item.error_code == _DIAGNOSTIC_CODE
    ]
    if not config.enabled or not eligible:
        return TargetedRepairOutcome(original, False, [])
    if len(eligible) != 1:
        traces = [
            _trace_from_diagnostic(
                item,
                repair_rule="multiple_diagnosed_slots",
                reason=(
                    "G1 requires exactly one diagnosed semantic slot per "
                    "invocation and fails closed otherwise."
                ),
            )
            for item in eligible
        ]
        return TargetedRepairOutcome(original, False, traces)

    diagnostic = eligible[0]
    closed_set = diagnostic.details.get("candidate_set") or []
    if not isinstance(closed_set, list) or len(closed_set) != 1:
        trace = _trace_from_diagnostic(
            diagnostic,
            repair_rule="non_unique_closed_candidate_set",
            reason="The diagnosed slot does not have exactly one bounded candidate.",
        )
        return TargetedRepairOutcome(original, False, [trace])
    candidate = closed_set[0]
    if not isinstance(candidate, Mapping):
        trace = _trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_closed_candidate",
            reason="The bounded candidate is not a structured candidate record.",
        )
        return TargetedRepairOutcome(original, False, [trace])
    replacement = str(candidate.get("evidence_id") or "")
    old_reference = str(diagnostic.details.get("old_reference") or "")
    if not replacement or replacement == old_reference:
        trace = _trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_closed_candidate",
            reason="The bounded replacement reference is empty or unchanged.",
        )
        return TargetedRepairOutcome(original, False, [trace])

    collisions = [
        path
        for path in _used_evidence_paths(reference_plan).get(replacement, [])
        if path != diagnostic.path
    ]
    if collisions:
        trace = _trace_from_diagnostic(
            diagnostic,
            repair_rule="replacement_evidence_reference_collision",
            reason=(
                "The replacement evidence is already assigned to another "
                "semantic slot; G1 refuses to collapse evidence assignments."
            ),
            selected_repair=replacement,
        )
        trace["collision_paths"] = collisions
        return TargetedRepairOutcome(original, False, [trace])

    match = re.fullmatch(
        r"/write_groups/(\d+)/rows/(\d+)/([^/]+)/value_from",
        diagnostic.path,
    )
    if match is None:
        trace = _trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_diagnostic_path",
            reason="The deterministic diagnostic path is not an eligible G1 slot.",
            selected_repair=replacement,
        )
        return TargetedRepairOutcome(original, False, [trace])
    group_index, row_index = int(match[1]), int(match[2])
    column_id = match[3]
    repaired = deepcopy(dict(reference_plan))
    try:
        value_spec = repaired["write_groups"][group_index]["rows"][row_index][
            column_id
        ]
    except (KeyError, IndexError, TypeError):
        value_spec = None
    if (
        not isinstance(value_spec, dict)
        or str(value_spec.get("value_from") or "") != old_reference
    ):
        trace = _trace_from_diagnostic(
            diagnostic,
            repair_rule="diagnosed_slot_changed",
            reason=(
                "The diagnosed slot no longer contains the diagnosed evidence "
                "reference; the repair was rolled back."
            ),
            selected_repair=replacement,
        )
        return TargetedRepairOutcome(original, False, [trace])

    value_spec["value_from"] = replacement
    trace = _trace_from_diagnostic(
        diagnostic,
        repair_rule=_REPAIR_RULE,
        reason=(
            "Selected the unique pre-enumerated candidate with the same start "
            "and an end offset shortened by exactly one terminal punctuation."
        ),
        selected_repair=replacement,
    )
    trace["repair_applied"] = True
    return TargetedRepairOutcome(repaired, True, [trace])


def _sentence_window(
    request: str,
    start: int,
    end: int,
) -> tuple[int, int]:
    """Return a deterministic sentence window without generating spans."""
    window_start = 0
    window_end = len(request)
    for match in _SENTENCE_BOUNDARY.finditer(request):
        if match.end() <= start:
            window_start = match.end()
            continue
        if match.start() >= end:
            window_end = match.end()
            break
    return window_start, window_end


def _selection_trace_from_diagnostic(
    diagnostic: Diagnostic,
    *,
    repair_rule: str,
    reason: str,
    selected_repair: str | None = None,
) -> dict[str, Any]:
    details = diagnostic.details
    return {
        "stage2_intervention": "G2_temporal_evidence_selection_repair",
        "diagnostic": diagnostic.error_code,
        "diagnostic_source": deepcopy(details.get("diagnostic_source") or {}),
        "diagnosed_slot": diagnostic.path,
        "semantic_slot": str(details.get("semantic_slot") or ""),
        "target_table_reference": str(
            details.get("target_table_reference") or ""
        ),
        "target_column_reference": str(
            details.get("target_column_reference") or ""
        ),
        "target_column_name": str(details.get("target_column_name") or ""),
        "target_semantic_type": str(
            details.get("target_semantic_type") or ""
        ),
        "target_declared_type": str(
            details.get("target_declared_type") or ""
        ),
        "old_reference": str(details.get("old_reference") or ""),
        "old_value": deepcopy(details.get("old_value")),
        "old_candidate_type": str(
            details.get("old_candidate_type") or ""
        ),
        "candidate_set": deepcopy(details.get("candidate_set") or []),
        "selected_repair": selected_repair,
        "repair_rule": repair_rule,
        "repair_reason": reason,
        "selection_policy": _SELECTION_POLICY,
        "context_window": deepcopy(details.get("context_window") or {}),
        "repair_attempted": True,
        "repair_applied": False,
        "repair_succeeded": False,
        "revalidation_result": "NOT_RUN",
        "revalidation_attempts": 0,
        "atomic_rollback": False,
        "max_revalidation_attempts": 1,
    }


def diagnose_temporal_evidence_selections(
    reference_plan: Mapping[str, Any],
    request: str,
    candidates: Sequence[Mapping[str, Any]],
    profile: dict[str, Any],
    materialization_diagnostics: Sequence[Diagnostic],
    typed_normalization_config: Mapping[str, Any] | None,
    config: DiagnosticTargetedRepairConfig,
) -> list[Diagnostic]:
    """Convert a frozen Stage-E type contradiction into a bounded G2 diagnostic.

    G2 Patch 1 is deliberately temporal-only. Replacement candidates must be
    frozen primary evidence spans, occur after the invalid selection in the
    same deterministic sentence, and pass the existing Stage-E target/type
    validator. No candidate text is generated and no scoring is performed.
    """
    if not config.enabled or not config.evidence_span_selection:
        return []
    ensure_reference_ids(profile)
    evidence = _candidate_index(candidates)
    tables = table_reference_map(profile)
    output: list[Diagnostic] = []
    for source_diagnostic in materialization_diagnostics:
        if (
            source_diagnostic.error_code != "TYPED_NORMALIZATION_REJECTED"
            or source_diagnostic.details.get("typed_error_code")
            != "TEMPORAL_EVIDENCE_TYPE_MISMATCH"
            or str(source_diagnostic.details.get("normalization_rule") or "")
            != "iso_date_normalization"
        ):
            continue
        match = re.fullmatch(
            r"/write_groups/(\d+)/rows/(\d+)/([^/]+)/normalization",
            source_diagnostic.path,
        )
        if match is None:
            continue
        group_index, row_index = int(match[1]), int(match[2])
        column_id = match[3]
        groups = reference_plan.get("write_groups") or []
        try:
            group = groups[group_index]
            value_spec = group["rows"][row_index][column_id]
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(group, Mapping) or not isinstance(value_spec, Mapping):
            continue
        if (
            str(value_spec.get("normalization") or "")
            != "iso_date_normalization"
        ):
            continue
        old_reference = str(value_spec.get("value_from") or "")
        if old_reference != str(
            source_diagnostic.details.get("evidence_id") or ""
        ):
            continue
        selected = evidence.get(old_reference)
        if selected is None:
            continue
        old_candidate_type = str(selected.get("candidate_type") or "")
        if old_candidate_type in {"date", "datetime"}:
            continue
        if str(selected.get("text") or "") != str(
            source_diagnostic.details.get("raw_value") or ""
        ):
            continue
        selected_start = selected.get("start")
        selected_end = selected.get("end")
        if not isinstance(selected_start, int) or not isinstance(selected_end, int):
            continue

        table_id = str(group.get("table_id") or "")
        table = tables.get(table_id)
        if table is None:
            continue
        column = column_reference_map(table).get(column_id)
        if column is None:
            continue
        window_start, window_end = _sentence_window(
            request,
            selected_start,
            selected_end,
        )
        compatible: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_id = str(candidate.get("evidence_id") or "")
            candidate_start = candidate.get("start")
            candidate_end = candidate.get("end")
            candidate_type = str(candidate.get("candidate_type") or "")
            if (
                not candidate_id
                or candidate_id == old_reference
                or candidate_type not in {"date", "datetime"}
                or str(candidate.get("candidate_role") or "") != "primary"
                or not isinstance(candidate_start, int)
                or not isinstance(candidate_end, int)
                or candidate_start < selected_end
                or candidate_end > window_end
                or candidate_start < window_start
            ):
                continue
            # The frozen extractor can mark both an ISO date and the enclosing
            # ISO datetime as ``primary`` because their extractor priorities
            # are equal.  G2 does not modify that A--F behavior.  Within its
            # already-enumerated temporal set it deterministically keeps only
            # maximal spans, so a date component cannot compete with the
            # enclosing datetime that supplies the full typed value.
            if any(
                str(other.get("candidate_type") or "")
                in {"date", "datetime"}
                and isinstance(other.get("start"), int)
                and isinstance(other.get("end"), int)
                and int(other["start"]) <= candidate_start
                and int(other["end"]) >= candidate_end
                and (
                    int(other["start"]) < candidate_start
                    or int(other["end"]) > candidate_end
                )
                for other in candidates
            ):
                continue
            typed = normalize_free_text_typed_candidate(
                candidate.get("text"),
                column,
                requested_rule="iso_date_normalization",
                candidate_type=candidate_type,
                config=typed_normalization_config,
                evidence_id=candidate_id,
                evidence_start=candidate_start,
                evidence_end=candidate_end,
            )
            if not typed.handled or typed.error is not None:
                continue
            compatible.append(
                {
                    "evidence_id": candidate_id,
                    "text": str(candidate.get("text") or ""),
                    "start": candidate_start,
                    "end": candidate_end,
                    "candidate_type": candidate_type,
                    "candidate_role": "primary",
                    "normalized_value": deepcopy(typed.value),
                }
            )
        compatible.sort(
            key=lambda item: (
                item["start"],
                item["end"],
                item["evidence_id"],
            )
        )
        slot_path = (
            f"/write_groups/{group_index}/rows/{row_index}/"
            f"{column_id}/value_from"
        )
        output.append(
            Diagnostic(
                _SELECTION_DIAGNOSTIC_CODE,
                (
                    "Selected evidence is deterministically incompatible with "
                    "the requested temporal normalization; G2 may use only a "
                    "unique compatible primary candidate in the forward same-"
                    "sentence closed set."
                ),
                path=slot_path,
                group_id=str(group.get("group_id") or ""),
                candidates=[item["evidence_id"] for item in compatible],
                details={
                    "diagnostic": _SELECTION_DIAGNOSTIC_CODE,
                    "diagnostic_source": source_diagnostic.to_dict(),
                    "semantic_slot": "evidence_reference",
                    "target_table_reference": table_id,
                    "target_column_reference": column_id,
                    "target_column_name": str(column.get("name") or ""),
                    "target_semantic_type": str(
                        column.get("semantic_type") or ""
                    ),
                    "target_declared_type": str(column.get("type") or ""),
                    "old_reference": old_reference,
                    "old_value": str(selected.get("text") or ""),
                    "old_candidate_type": old_candidate_type,
                    "candidate_set": compatible,
                    "repair_rule": _SELECTION_REPAIR_RULE,
                    "selection_policy": _SELECTION_POLICY,
                    "context_window": {
                        "start": window_start,
                        "end": window_end,
                        "text": request[window_start:window_end],
                    },
                },
            )
        )
    return output


def repair_temporal_evidence_selection_after_diagnostic(
    reference_plan: Mapping[str, Any],
    diagnostics: Sequence[Diagnostic],
    config: DiagnosticTargetedRepairConfig,
) -> TargetedRepairOutcome:
    """Deep-copy and replace one G2-diagnosed temporal evidence reference."""
    original = deepcopy(dict(reference_plan))
    eligible = [
        item
        for item in diagnostics
        if item.error_code == _SELECTION_DIAGNOSTIC_CODE
    ]
    if (
        not config.enabled
        or not config.evidence_span_selection
        or not eligible
    ):
        return TargetedRepairOutcome(original, False, [])
    if len(eligible) != 1:
        return TargetedRepairOutcome(
            original,
            False,
            [
                _selection_trace_from_diagnostic(
                    item,
                    repair_rule="multiple_diagnosed_slots",
                    reason=(
                        "G2 requires exactly one diagnosed semantic slot per "
                        "invocation and fails closed otherwise."
                    ),
                )
                for item in eligible
            ],
        )

    diagnostic = eligible[0]
    details = diagnostic.details
    diagnostic_source = details.get("diagnostic_source")
    expected_source_path = re.sub(
        r"/value_from$",
        "/normalization",
        diagnostic.path,
    )
    if (
        not isinstance(diagnostic_source, Mapping)
        or diagnostic_source.get("error_code")
        != "TYPED_NORMALIZATION_REJECTED"
        or diagnostic_source.get("path") != expected_source_path
        or not isinstance(diagnostic_source.get("details"), Mapping)
        or diagnostic_source["details"].get("typed_error_code")
        != "TEMPORAL_EVIDENCE_TYPE_MISMATCH"
        or diagnostic_source["details"].get("normalization_rule")
        != "iso_date_normalization"
        or str(diagnostic_source["details"].get("evidence_id") or "")
        != str(details.get("old_reference") or "")
        or str(diagnostic_source["details"].get("raw_value") or "")
        != str(details.get("old_value") or "")
        or str(details.get("old_candidate_type") or "")
        in {"date", "datetime"}
        or details.get("selection_policy") != _SELECTION_POLICY
        or details.get("repair_rule") != _SELECTION_REPAIR_RULE
    ):
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_deterministic_source_diagnostic",
            reason=(
                "The G2 diagnostic no longer carries the exact frozen Stage-E "
                "temporal incompatibility provenance for this slot."
            ),
        )
        return TargetedRepairOutcome(original, False, [trace])
    closed_set = diagnostic.details.get("candidate_set") or []
    if not isinstance(closed_set, list) or len(closed_set) != 1:
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="non_unique_compatible_candidate_set",
            reason=(
                "The temporal compatibility/context filter did not produce "
                "exactly one frozen primary candidate."
            ),
        )
        return TargetedRepairOutcome(original, False, [trace])
    candidate = closed_set[0]
    if (
        not isinstance(candidate, Mapping)
        or str(candidate.get("candidate_type") or "")
        not in {"date", "datetime"}
        or str(candidate.get("candidate_role") or "") != "primary"
        or not isinstance(candidate.get("start"), int)
        or not isinstance(candidate.get("end"), int)
    ):
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_closed_candidate",
            reason=(
                "The compatible candidate is not a frozen primary temporal "
                "span record."
            ),
        )
        return TargetedRepairOutcome(original, False, [trace])
    replacement = str(candidate.get("evidence_id") or "")
    old_reference = str(diagnostic.details.get("old_reference") or "")
    if not replacement or replacement == old_reference:
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_closed_candidate",
            reason="The compatible replacement is empty or unchanged.",
        )
        return TargetedRepairOutcome(original, False, [trace])
    collisions = [
        path
        for path in _used_evidence_paths(reference_plan).get(replacement, [])
        if path != diagnostic.path
    ]
    if collisions:
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="replacement_evidence_reference_collision",
            reason=(
                "The replacement evidence is already assigned to another "
                "semantic slot; G2 refuses to collapse evidence assignments."
            ),
            selected_repair=replacement,
        )
        trace["collision_paths"] = collisions
        return TargetedRepairOutcome(original, False, [trace])

    match = re.fullmatch(
        r"/write_groups/(\d+)/rows/(\d+)/([^/]+)/value_from",
        diagnostic.path,
    )
    if match is None:
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="invalid_diagnostic_path",
            reason="The deterministic diagnostic path is not a G2 slot.",
            selected_repair=replacement,
        )
        return TargetedRepairOutcome(original, False, [trace])
    group_index, row_index, column_id = int(match[1]), int(match[2]), match[3]
    repaired = deepcopy(dict(reference_plan))
    try:
        value_spec = repaired["write_groups"][group_index]["rows"][row_index][
            column_id
        ]
    except (KeyError, IndexError, TypeError):
        value_spec = None
    if (
        not isinstance(value_spec, dict)
        or str(value_spec.get("value_from") or "") != old_reference
        or str(value_spec.get("normalization") or "")
        != "iso_date_normalization"
    ):
        trace = _selection_trace_from_diagnostic(
            diagnostic,
            repair_rule="diagnosed_slot_changed",
            reason=(
                "The diagnosed slot or its temporal normalization contract "
                "changed; G2 rolled back without mutation."
            ),
            selected_repair=replacement,
        )
        return TargetedRepairOutcome(original, False, [trace])

    value_spec["value_from"] = replacement
    trace = _selection_trace_from_diagnostic(
        diagnostic,
        repair_rule=_SELECTION_REPAIR_RULE,
        reason=(
            "Selected the unique frozen primary DATE/DATETIME candidate after "
            "the incompatible evidence in the same deterministic sentence; "
            "the candidate passed the existing Stage-E target/type validator."
        ),
        selected_repair=replacement,
    )
    trace["repair_applied"] = True
    return TargetedRepairOutcome(repaired, True, [trace])


def mark_targeted_revalidation(
    traces: Sequence[Mapping[str, Any]],
    *,
    passed: bool,
    error_codes: Sequence[str] = (),
) -> list[dict[str, Any]]:
    output = deepcopy(list(traces))
    for trace in output:
        if not trace.get("repair_applied"):
            continue
        trace["revalidation_attempts"] = 1
        trace["revalidation_result"] = "PASS" if passed else "FAIL_CLOSED"
        trace["repair_succeeded"] = bool(passed)
        trace["atomic_rollback"] = not passed
        trace["revalidation_error_codes"] = list(error_codes)
    return output


def attach_targeted_repair_trace(
    plan: dict[str, Any],
    traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attach provenance metadata without changing plan semantics."""
    output = deepcopy(plan)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for trace in traces:
        match = re.match(r"^/write_groups/(\d+)(?:/|$)", str(trace.get("diagnosed_slot") or ""))
        if match:
            grouped.setdefault(int(match[1]), []).append(deepcopy(dict(trace)))
    for group_index, group_traces in grouped.items():
        groups = output.get("write_groups") or []
        if group_index >= len(groups) or not isinstance(groups[group_index], dict):
            continue
        reference_trace = groups[group_index].setdefault("reference_trace", {})
        if isinstance(reference_trace, dict):
            reference_trace["diagnostic_targeted_repairs"] = group_traces
    return output


def targeted_repair_warnings(
    traces: Sequence[Mapping[str, Any]],
) -> list[Diagnostic]:
    warnings: list[Diagnostic] = []
    for trace in traces:
        if not trace.get("repair_applied"):
            continue
        intervention = str(trace.get("stage2_intervention") or "")
        if intervention == "G2_temporal_evidence_selection_repair":
            message = (
                "Stage G2 applied one bounded temporal evidence-selection repair."
            )
        else:
            message = (
                "Stage G1 applied one bounded evidence-span boundary repair."
            )
        warnings.append(
            Diagnostic(
                "DIAGNOSTIC_TARGETED_REPAIR_APPLIED",
                message,
                severity="warning",
                path=str(trace.get("diagnosed_slot") or ""),
                details=deepcopy(dict(trace)),
            )
        )
    return warnings
