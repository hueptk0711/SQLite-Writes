from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


_DATE_YMD = re.compile(
    r"^(?P<year>\d{4})(?P<sep>[-/])(?P<month>\d{2})(?P=sep)(?P<day>\d{2})$"
)
_DATETIME_YMD = re.compile(
    r"^(?P<year>\d{4})(?P<sep>[-/])(?P<month>\d{2})(?P=sep)(?P<day>\d{2})"
    r"(?P<joiner>[ T])(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?P<fraction>\.\d+)?$"
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
        return cls(
            enabled=bool(value.get("enabled", False)),
            date_normalization=bool(value.get("date_normalization", True)),
            datetime_normalization=bool(value.get("datetime_normalization", True)),
            preserve_raw_evidence=bool(value.get("preserve_raw_evidence", True)),
            fail_closed_on_ambiguous_format=bool(
                value.get("fail_closed_on_ambiguous_format", True)
            ),
        )

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


def _target_temporal_compatible(column: Mapping[str, Any]) -> bool:
    """Reject clearly numeric/blob targets; SQLite temporal TEXT stays allowed."""
    declared = str(column.get("type") or "").upper()
    semantic_type = str(column.get("semantic_type") or "").casefold()
    if semantic_type in {
        "date",
        "datetime",
        "timestamp",
        "temporal",
        "date_key",
    }:
        return True
    if any(token in declared for token in ("DATE", "TIME")):
        return True
    if any(token in declared for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "BLOB")):
        return False
    # Existing benchmark timestamp columns are TEXT.  The reference plan's
    # explicit iso_date_normalization request plus a strictly parsed temporal
    # surface is the second semantic guard for these columns.
    return True


def _single_sentence_boundary(value: str) -> tuple[str, str | None]:
    """Remove at most one terminal sentence punctuation mark.

    This is intentionally not a generic rstrip.  A second punctuation mark is
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
    # datetime.strptime accepts at most microseconds.  SQLite text can preserve
    # more digits, but Stage E only claims validation for the observed <=6-digit
    # precision and fails closed outside that contract.
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
    return (
        f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
    )


def _canonical_datetime(match: re.Match[str]) -> str:
    return (
        f"{match.group('year')}-{match.group('month')}-{match.group('day')} "
        f"{match.group('hour')}:{match.group('minute')}:{match.group('second')}"
        f"{match.group('fraction') or ''}"
    )


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

    Stage E does not infer columns and does not repair evidence spans.  It only
    handles a cell when the reference plan explicitly requested
    ``iso_date_normalization`` and the evidence surface itself passes a strict,
    unambiguous year-first DATE/DATETIME grammar.
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
        "evidence_id": str(evidence_id or ""),
        "evidence_start": evidence_start,
        "evidence_end": evidence_end,
        "applied": False,
        "lossless": True,
    }
    if not selected.enabled or str(requested_rule or "identity") != "iso_date_normalization":
        return TypedNormalizationResult(False, value, base_audit)
    if not isinstance(value, str):
        base_audit["lossless"] = False
        return TypedNormalizationResult(
            True,
            value,
            base_audit,
            "Temporal normalization requires a textual evidence span.",
            "TEMPORAL_NON_TEXT_VALUE",
        )
    if not _target_temporal_compatible(column):
        base_audit["lossless"] = False
        return TypedNormalizationResult(
            True,
            value,
            base_audit,
            "Temporal normalization is incompatible with the resolved target column type.",
            "TEMPORAL_TARGET_TYPE_MISMATCH",
        )
    candidate_kind = str(candidate_type or "").casefold()
    if candidate_kind and candidate_kind not in {"date", "datetime"}:
        base_audit["lossless"] = False
        return TypedNormalizationResult(
            True,
            value,
            base_audit,
            (
                "The enumerated evidence span is not deterministically typed as "
                "DATE/DATETIME; Stage E does not reinterpret quoted/text evidence."
            ),
            "TEMPORAL_EVIDENCE_TYPE_MISMATCH",
        )

    # Evidence extraction already removes surrounding whitespace.  We permit
    # outer whitespace here for direct callers but never strip quotes.
    stripped = value.strip()
    core, boundary = _single_sentence_boundary(stripped)

    datetime_match = _DATETIME_YMD.fullmatch(core)
    if datetime_match is not None and _valid_datetime_parts(datetime_match):
        if not selected.datetime_normalization:
            return TypedNormalizationResult(False, value, base_audit)
        normalized = _canonical_datetime(datetime_match)
        rule = (
            "free_text_datetime_sentence_boundary_punctuation"
            if boundary is not None
            else "free_text_datetime_canonical_year_first"
        )
        audit = {
            **base_audit,
            "semantic_type": "datetime",
            "parsed_candidate": core,
            "normalized_value": normalized,
            "normalization_rule": rule,
            "normalization_confidence": "high",
            "sentence_boundary_punctuation": boundary,
            "applied": normalized != value,
        }
        return TypedNormalizationResult(True, normalized, audit)

    date_match = _DATE_YMD.fullmatch(core)
    if date_match is not None and _valid_date_parts(date_match):
        if not selected.date_normalization:
            return TypedNormalizationResult(False, value, base_audit)
        normalized = _canonical_date(date_match)
        rule = (
            "free_text_date_sentence_boundary_punctuation"
            if boundary is not None
            else "free_text_date_canonical_year_first"
        )
        audit = {
            **base_audit,
            "semantic_type": "date",
            "parsed_candidate": core,
            "normalized_value": normalized,
            "normalization_rule": rule,
            "normalization_confidence": "high",
            "sentence_boundary_punctuation": boundary,
            "applied": normalized != value,
        }
        return TypedNormalizationResult(True, normalized, audit)

    if not selected.fail_closed_on_ambiguous_format:
        return TypedNormalizationResult(False, value, base_audit)
    base_audit["lossless"] = False
    return TypedNormalizationResult(
        True,
        value,
        base_audit,
        (
            "Evidence is not a supported unambiguous year-first DATE/DATETIME; "
            "Stage E refuses to guess or repair the evidence span."
        ),
        "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_FORMAT",
    )
