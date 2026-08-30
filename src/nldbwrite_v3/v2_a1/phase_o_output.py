from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .json_schema import validate_schema_subset
from .phase_o_schema import ALLOWED_OPERATIONS, PHASE_O_REQUIRED_KEYS, PHASE_O_SPAN_KEYS
from .types import V2A1Error


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def phase_o_json_schema(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    return json.loads((root / "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json").read_text(encoding="utf-8"))


def parse_phase_o_output(text: str, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise V2A1Error("phase_o_parse", "Phase O output is not valid JSON", details={"error": str(exc)}) from exc
    validate_phase_o_object(obj, root=root)
    return obj


def validate_phase_o_object(obj: Any, *, root: Path = PROJECT_ROOT) -> None:
    try:
        validate_schema_subset(phase_o_json_schema(root), obj, reason_code="phase_o_schema_failure")
    except V2A1Error as exc:
        if isinstance(obj, dict):
            extra = sorted(set(obj) - PHASE_O_REQUIRED_KEYS)
            if "span_ref" in extra:
                raise V2A1Error("phase_o_schema_failure", "Model-generated span_ref is forbidden", details={"extra": extra}) from exc
            if "value" in extra or "text" in extra:
                raise V2A1Error("phase_o_schema_failure", "Model-generated value text is forbidden", details={"extra": extra}) from exc
        raise
    if not isinstance(obj, dict):
        raise V2A1Error("phase_o_schema_failure", "Phase O output must be a JSON object")
    keys = set(obj)
    if keys != PHASE_O_REQUIRED_KEYS:
        extra = sorted(keys - PHASE_O_REQUIRED_KEYS)
        missing = sorted(PHASE_O_REQUIRED_KEYS - keys)
        if "span_ref" in extra:
            raise V2A1Error("phase_o_schema_failure", "Model-generated span_ref is forbidden", details={"extra": extra})
        if "value" in extra or "text" in extra:
            raise V2A1Error("phase_o_schema_failure", "Model-generated value text is forbidden", details={"extra": extra})
        raise V2A1Error("phase_o_schema_failure", "Phase O keys do not match the frozen schema", details={"extra": extra, "missing": missing})
    if obj["operation"] not in ALLOWED_OPERATIONS:
        raise V2A1Error("phase_o_invalid_operation", "Invalid Phase O operation", details={"operation": obj["operation"]})
    spans = obj["value_spans"]
    if not isinstance(spans, list):
        raise V2A1Error("phase_o_schema_failure", "value_spans must be a list")
    if not spans:
        raise V2A1Error("phase_o_empty_spans", "value_spans must contain at least one span")
    for span in spans:
        if not isinstance(span, dict) or set(span) != PHASE_O_SPAN_KEYS:
            raise V2A1Error("phase_o_schema_failure", "Each Phase O span must contain only start_char and end_char")
        if not isinstance(span["start_char"], int) or not isinstance(span["end_char"], int):
            raise V2A1Error("phase_o_invalid_offset", "Span offsets must be integers")
