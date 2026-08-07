from __future__ import annotations

import re
from datetime import datetime
from typing import Any


_INTEGER = re.compile(r"^[+-]?\d+$")
_REAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_THOUSANDS = re.compile(
    r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$"
)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
)

ALLOWED_NORMALIZATIONS = {
    "identity",
    "lossless_integer_parsing",
    "decimal_parsing",
    "remove_thousands_separator",
    "iso_date_normalization",
    "boolean_mapping",
    "trim_surrounding_quotes",
}


def sqlite_storage_class(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool) or isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    if isinstance(value, bytes):
        return "blob"
    return "text"


def _text_preserving_column(column: dict[str, Any]) -> bool:
    semantic_type = str(column.get("semantic_type") or "").casefold()
    return bool(column.get("preserve_as_text")) or semantic_type in {
        "identifier",
        "code",
        "phone",
        "zip",
        "postal_code",
        "date_key",
        "document_number",
        "account_number",
    }


def apply_declared_normalization(
    value: Any,
    column: dict[str, Any],
    rule: str = "identity",
) -> tuple[Any, dict[str, Any], str | None]:
    """Apply only a declared, reversible normalization and emit an audit row."""
    selected = str(rule or "identity")
    audit = {
        "raw_value": value,
        "normalized_value": value,
        "sqlite_storage_class": sqlite_storage_class(value),
        "normalization_rule": selected,
        "applied": False,
        "lossless": True,
    }
    if selected not in ALLOWED_NORMALIZATIONS:
        audit["lossless"] = False
        return value, audit, f"Unsupported normalization rule: {selected}"
    if selected == "identity" or value is None:
        return value, audit, None

    raw = value if isinstance(value, str) else str(value)
    normalized: Any = raw
    error: str | None = None
    if selected == "trim_surrounding_quotes":
        if (
            len(raw) >= 2
            and raw[0] == raw[-1]
            and raw[0] in {"'", '"'}
        ):
            normalized = raw[1:-1]
        else:
            error = "Value is not enclosed by one matching quote pair."
    elif selected == "remove_thousands_separator":
        if not _THOUSANDS.fullmatch(raw.strip()):
            error = "Value is not a valid thousands-separated number."
        else:
            normalized_text = raw.strip().replace(",", "")
            declared_type = str(column.get("type") or "").upper()
            if "INT" in declared_type and "." not in normalized_text:
                normalized = int(normalized_text)
            else:
                # Keep decimals as text to avoid binary floating-point loss.
                normalized = normalized_text
    elif selected == "lossless_integer_parsing":
        stripped = raw.strip()
        unsigned = stripped.lstrip("+-")
        if not _INTEGER.fullmatch(stripped):
            error = "Value is not an integer literal."
        elif len(unsigned) > 1 and unsigned.startswith("0"):
            error = "Integer parsing would remove leading zeros."
        elif _text_preserving_column(column):
            error = "Identifier-like TEXT values must remain text."
        else:
            normalized = int(stripped)
    elif selected == "decimal_parsing":
        stripped = raw.strip()
        if not _REAL.fullmatch(stripped):
            error = "Value is not a decimal literal."
        else:
            # Canonical text is reversible and avoids float rounding.
            normalized = stripped
    elif selected == "boolean_mapping":
        folded = raw.strip().casefold()
        if folded in {"true", "yes"}:
            normalized = 1
        elif folded in {"false", "no"}:
            normalized = 0
        else:
            error = "Value is not a supported boolean token."
    elif selected == "iso_date_normalization":
        stripped = raw.strip()
        parsed = None
        for date_format in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(stripped, date_format)
                break
            except ValueError:
                continue
        if parsed is None:
            error = "Value is not a supported unambiguous date."
        else:
            normalized = parsed.date().isoformat()

    if error is not None:
        audit["lossless"] = False
        return value, audit, error
    audit.update(
        {
            "normalized_value": normalized,
            "sqlite_storage_class": sqlite_storage_class(normalized),
            "applied": normalized != value,
        }
    )
    return normalized, audit, None


def normalize_value_lossless(
    value: Any,
    column: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Conservative automatic mode used by MP-FS+ compilation."""
    if value is None or not isinstance(value, str):
        normalized, audit, _ = apply_declared_normalization(
            value,
            column,
            "identity",
        )
        return normalized, audit
    if _text_preserving_column(column):
        normalized, audit, _ = apply_declared_normalization(
            value,
            column,
            "identity",
        )
        return normalized, audit
    declared_type = str(column.get("type") or "").upper()
    semantic_type = str(column.get("semantic_type") or "").casefold()
    if "INT" in declared_type:
        normalized, audit, error = apply_declared_normalization(
            value,
            column,
            "lossless_integer_parsing",
        )
        if error is None:
            return normalized, audit
    if semantic_type == "boolean" or "BOOL" in declared_type:
        normalized, audit, error = apply_declared_normalization(
            value,
            column,
            "boolean_mapping",
        )
        if error is None:
            return normalized, audit
    normalized, audit, _ = apply_declared_normalization(
        value,
        column,
        "identity",
    )
    return normalized, audit
