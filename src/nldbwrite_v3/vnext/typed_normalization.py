from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


_DATE_YMD = re.compile(
    r"^(?P<year>[0-9]{4})(?P<sep>[-/])(?P<month>[0-9]{2})(?P=sep)(?P<day>[0-9]{2})$"
)
_DATETIME_YMD = re.compile(
    r"^(?P<year>[0-9]{4})(?P<sep>[-/])(?P<month>[0-9]{2})(?P=sep)(?P<day>[0-9]{2})"
    r"(?P<joiner>[ T])(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?P<fraction>\.[0-9]+)?$"
)

_TEMPORAL_COMPATIBLE_SEMANTICS = {
    "",
    "text",
    "date",
    "datetime",
    "timestamp",
    "temporal",
    "date_key",
}
_TEXT_DECLARED_TOKENS = ("CHAR", "CLOB", "TEXT", "VARCHAR")
_TEMPORAL_DECLARED_TOKENS = ("DATE", "TIME")
_INCOMPATIBLE_DECLARED_TOKENS = (
    "INT",
    "REAL",
    "FLOA",
    "DOUB",
    "NUM",
    "DEC",
    "BOOL",
    "JSON",
    "BLOB",
)


@dataclass(frozen=True, slots=True)
class FreeTextTypedNormalizationConfig:
    """Stage-E deterministic normalization contract for free-text evidence."""

    enabled: bool = False
    date_normalization: bool = True
    datetime_normalization: bool = True
    preserve_raw_evidence: bool = True
    fail_closed_on_ambiguous_format: bool = True

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "FreeTextTypedNormalizationConfig":
        value = value or {}
        selected = cls(
            enabled=bool(value.get("enabled", False)),
            date_normalization=bool(value.get("date_normalization", True)),
            datetime_normalization=bool(value.get("datetime_normalization", True)),
            preserve_raw_evidence=bool(value.get("preserve_raw_evidence", True)),
            fail_closed_on_ambiguous_format=bool(
                value.get("fail_closed_on_ambiguous_format", True)
            ),
        )
        if selected.enabled and not selected.preserve_raw_evidence:
            raise ValueError(
                "Stage E requires preserve_raw_evidence=true; raw evidence provenance "
                "is a mandatory invariant and cannot be disabled."
            )
        return selected

    def to_dict(self) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "date_normalization": self.date_normalization,
            "datetime_normalization": self.datetime_normalization,
            "preserve_raw_evidence": self.preserve_raw_evidence,
            "fail_closed_on_ambiguous_format": self.fail_closed_on_ambiguous_format,
        }


@dataclass(frozen=True, slots=True)
class TypedNormalizationResult:
    handled: bool
    value: Any
    audit: dict[str, Any]
    error: str | None = None
    error_code: str | None = None


def _normalized_semantic_type(column: Mapping[str, Any]) -> str:
    return str(column.get("semantic_type") or "").strip().casefold().replace("-", "_")


def _target_temporal_compatibility(column: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Fail closed on target semantics/types incompatible with temporal text.

    Existing benchmark temporal values may be stored in SQLite TEXT columns, so
    semantic ``text`` remains compatible. A known non-temporal semantic is not
    allowed to become temporal merely because the plan requested normalization.
    """
    semantic_type = _normalized_semantic_type(column)
    if semantic_type not in _TEMPORAL_COMPATIBLE_SEMANTICS:
        return False, "TEMPORAL_TARGET_SEMANTIC_MISMATCH"

    declared = str(column.get("type") or "").strip().upper()
    if any(token in declared for token in _INCOMPATIBLE_DECLARED_TOKENS):
        return False, "TEMPORAL_TARGET_TYPE_MISMATCH"
    if not declared:
        return True, None
    if any(token in declared for token in _TEMPORAL_DECLARED_TOKENS):
        return True, None
    if any(token in declared for token in _TEXT_DECLARED_TOKENS):
        return True, None
    # Unknown/custom declared types are not silently treated as temporal storage.
    return False, "TEMPORAL_TARGET_TYPE_MISMATCH"


def _single_sentence_boundary(value: str) -> tuple[str, str | None]:
    """Remove at most one terminal sentence punctuation mark.

    This is intentionally not a generic rstrip. A second punctuation mark is
    left in place and therefore fails strict temporal parsing.
    """
    if value.endswith((".", ",")):
        return value[:-1], value[-1]
    return value, None


def _valid_date_parts(match: re.Match[str]) -> bool:
    try:
        datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return False
    return True


def _valid_datetime_parts(match: re.Match[str]) -> bool:
    fraction = match.group("fraction") or ""
    if fraction and len(fraction) - 1 > 6:
        return False
    try:
        datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            int((fraction[1:] if fraction else "").ljust(6, "0") or "0"),
        )
    except ValueError:
        return False
    return True


def _canonical_date(match: re.Match[str]) -> str:
    return f"{match.group('year')}-{match.group('month')}-{match.group('day')}"


def _canonical_datetime(match: re.Match[str]) -> str:
    return (
        f"{match.group('year')}-{match.group('month')}-{match.group('day')} "
        f"{match.group('hour')}:{match.group('minute')}:{match.group('second')}"
        f"{match.group('fraction') or ''}"
    )


def _mark_handled(
    audit: dict[str, Any],
    *,
    accepted: bool,
    value_changed: bool = False,
) -> dict[str, Any]:
    return {
        **audit,
        "intervention_applied": True,
        "applied": True,
        "value_changed": bool(value_changed),
        "accepted": bool(accepted),
        "outcome": "ACCEPT" if accepted else "REJECT",
    }


def normalize_free_text_typed_candidate(
    value: Any,
    column: Mapping[str, Any],
    *,
    requested_rule: str,
    candidate_type: str = "",
    config: Mapping[str, Any] | FreeTextTypedNormalizationConfig | None = None,
    evidence_id: str = "",
    evidence_start: int | None = None,
    evidence_end: int | None = None,
) -> TypedNormalizationResult:
    """Apply Stage-E temporal normalization only at the free-text boundary.

    Stage E does not infer columns and does not repair evidence spans. It handles
    a cell only when the reference plan explicitly requests
    ``iso_date_normalization``, the evidence enumerator typed the span exactly as
    DATE/DATETIME, the candidate subtype matches the strict grammar, and the
    resolved target semantics are compatible with temporal text.
    """
    selected = (
        config
        if isinstance(config, FreeTextTypedNormalizationConfig)
        else FreeTextTypedNormalizationConfig.from_mapping(config)
    )
    raw_value = value
    base_audit: dict[str, Any] = {
        "stage2_intervention": "E_free_text_typed_normalization",
        "raw_evidence_span": raw_value,
        "parsed_candidate": raw_value,
        "normalized_value": raw_value,
        "semantic_type": "unresolved",
        "normalization_rule": str(requested_rule or "identity"),
        "normalization_confidence": "none",
        "requested_normalization": str(requested_rule or "identity"),
        "candidate_type": str(candidate_type or ""),
        "target_semantic_type": _normalized_semantic_type(column),
        "target_declared_type": str(column.get("type") or ""),
        "evidence_id": str(evidence_id or ""),
        "evidence_start": evidence_start,
        "evidence_end": evidence_end,
        "intervention_applied": False,
        "applied": False,
        "value_changed": False,
        "accepted": None,
        "outcome": "NOT_APPLICABLE",
        "lossless": True,
    }
    if not selected.enabled or str(requested_rule or "identity") != "iso_date_normalization":
        return TypedNormalizationResult(False, value, base_audit)

    if not isinstance(value, str):
        audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
        return TypedNormalizationResult(
            True,
            value,
            audit,
            "Temporal normalization requires a textual evidence span.",
            "TEMPORAL_NON_TEXT_VALUE",
        )

    candidate_kind = str(candidate_type or "").strip().casefold()
    if not candidate_kind:
        audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
        return TypedNormalizationResult(
            True,
            value,
            audit,
            "Temporal normalization requires deterministic DATE/DATETIME evidence typing.",
            "TEMPORAL_EVIDENCE_TYPE_MISSING",
        )
    if candidate_kind not in {"date", "datetime"}:
        audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
        return TypedNormalizationResult(
            True,
            value,
            audit,
            (
                "The enumerated evidence span is not deterministically typed as "
                "DATE/DATETIME; Stage E does not reinterpret quoted/text evidence."
            ),
            "TEMPORAL_EVIDENCE_TYPE_MISMATCH",
        )

    compatible, target_error_code = _target_temporal_compatibility(column)
    if not compatible:
        audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
        message = (
            "Temporal normalization is incompatible with the resolved target column semantic type."
            if target_error_code == "TEMPORAL_TARGET_SEMANTIC_MISMATCH"
            else "Temporal normalization is incompatible with the resolved target column declared type."
        )
        return TypedNormalizationResult(
            True,
            value,
            audit,
            message,
            target_error_code,
        )

    # Evidence extraction already removes surrounding whitespace. We permit
    # outer whitespace here for direct callers but never strip quotes.
    stripped = value.strip()
    core, boundary = _single_sentence_boundary(stripped)

    datetime_match = _DATETIME_YMD.fullmatch(core)
    date_match = _DATE_YMD.fullmatch(core)
    valid_datetime = datetime_match is not None and _valid_datetime_parts(datetime_match)
    valid_date = date_match is not None and _valid_date_parts(date_match)

    if candidate_kind == "date" and valid_datetime:
        audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
        return TypedNormalizationResult(
            True,
            value,
            audit,
            "DATE-typed evidence matched DATETIME grammar; Stage E refuses subtype reinterpretation.",
            "TEMPORAL_EVIDENCE_SUBTYPE_MISMATCH",
        )
    if candidate_kind == "datetime" and valid_date:
        audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
        return TypedNormalizationResult(
            True,
            value,
            audit,
            "DATETIME-typed evidence matched DATE grammar; Stage E refuses subtype reinterpretation.",
            "TEMPORAL_EVIDENCE_SUBTYPE_MISMATCH",
        )

    if candidate_kind == "datetime" and valid_datetime:
        if not selected.datetime_normalization:
            return TypedNormalizationResult(False, value, base_audit)
        assert datetime_match is not None
        normalized = _canonical_datetime(datetime_match)
        rule = (
            "free_text_datetime_sentence_boundary_punctuation"
            if boundary is not None
            else "free_text_datetime_canonical_year_first"
        )
        audit = _mark_handled(
            {
                **base_audit,
                "semantic_type": "datetime",
                "parsed_candidate": core,
                "normalized_value": normalized,
                "normalization_rule": rule,
                "normalization_confidence": "high",
                "sentence_boundary_punctuation": boundary,
            },
            accepted=True,
            value_changed=normalized != value,
        )
        return TypedNormalizationResult(True, normalized, audit)

    if candidate_kind == "date" and valid_date:
        if not selected.date_normalization:
            return TypedNormalizationResult(False, value, base_audit)
        assert date_match is not None
        normalized = _canonical_date(date_match)
        rule = (
            "free_text_date_sentence_boundary_punctuation"
            if boundary is not None
            else "free_text_date_canonical_year_first"
        )
        audit = _mark_handled(
            {
                **base_audit,
                "semantic_type": "date",
                "parsed_candidate": core,
                "normalized_value": normalized,
                "normalization_rule": rule,
                "normalization_confidence": "high",
                "sentence_boundary_punctuation": boundary,
            },
            accepted=True,
            value_changed=normalized != value,
        )
        return TypedNormalizationResult(True, normalized, audit)

    if not selected.fail_closed_on_ambiguous_format:
        return TypedNormalizationResult(False, value, base_audit)
    audit = _mark_handled({**base_audit, "lossless": False}, accepted=False)
    return TypedNormalizationResult(
        True,
        value,
        audit,
        (
            "Evidence is not a supported unambiguous year-first DATE/DATETIME; "
            "Stage E refuses to guess or repair the evidence span."
        ),
        "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_FORMAT",
    )
