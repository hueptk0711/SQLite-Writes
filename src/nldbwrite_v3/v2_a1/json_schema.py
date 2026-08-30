from __future__ import annotations

from typing import Any

from .types import V2A1Error


def validate_schema_subset(schema: dict[str, Any], instance: Any, *, reason_code: str) -> None:
    try:
        _validate(schema, instance, path="$")
    except _SchemaFailure as exc:
        raise V2A1Error(reason_code, exc.message, details={"path": exc.path}) from exc


class _SchemaFailure(ValueError):
    def __init__(self, message: str, path: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def _validate(schema: dict[str, Any], instance: Any, *, path: str) -> None:
    if "oneOf" in schema:
        matches = 0
        last_failure: _SchemaFailure | None = None
        for subschema in schema["oneOf"]:
            try:
                _validate(subschema, instance, path=path)
                matches += 1
            except _SchemaFailure as exc:
                last_failure = exc
        if matches != 1:
            message = "oneOf must match exactly one schema" if last_failure is None else last_failure.message
            raise _SchemaFailure(message, path)
    if "not" in schema:
        try:
            _validate(schema["not"], instance, path=path)
        except _SchemaFailure:
            pass
        else:
            raise _SchemaFailure("not schema matched", path)
    if "const" in schema and instance != schema["const"]:
        raise _SchemaFailure("const mismatch", path)
    if "enum" in schema and instance not in schema["enum"]:
        raise _SchemaFailure("enum mismatch", path)
    if "type" in schema:
        _validate_type(schema["type"], instance, path)
    if isinstance(instance, dict):
        required = set(schema.get("required", []))
        missing = sorted(required - set(instance))
        if missing:
            raise _SchemaFailure(f"missing required keys: {missing}", path)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(properties))
            if extra:
                raise _SchemaFailure(f"additional properties: {extra}", path)
        for key, subschema in properties.items():
            if key in instance:
                _validate(subschema, instance[key], path=f"{path}.{key}")
    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < int(min_items):
            raise _SchemaFailure("array has too few items", path)
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                _validate(item_schema, item, path=f"{path}[{index}]")


def _validate_type(expected: str, instance: Any, path: str) -> None:
    ok = (
        (expected == "object" and isinstance(instance, dict))
        or (expected == "array" and isinstance(instance, list))
        or (expected == "string" and isinstance(instance, str))
        or (expected == "integer" and isinstance(instance, int) and not isinstance(instance, bool))
        or (expected == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool))
        or (expected == "boolean" and isinstance(instance, bool))
        or (expected == "null" and instance is None)
    )
    if not ok:
        raise _SchemaFailure(f"expected type {expected}", path)
