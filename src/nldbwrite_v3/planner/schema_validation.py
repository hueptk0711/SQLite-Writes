from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SchemaIssue:
    path: str
    message: str


def _pointer(path: list[str | int]) -> str:
    if not path:
        return "/"
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"Only local schema references are supported: {reference}")
    node: Any = root
    for token in reference[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        node = node[key]
    if not isinstance(node, dict):
        raise ValueError(f"Schema reference is not an object: {reference}")
    return node


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_json_schema(
    instance: Any,
    schema: dict[str, Any],
) -> list[SchemaIssue]:
    """Validate the JSON-Schema subset used by the v3 plan contracts."""
    issues: list[SchemaIssue] = []

    def visit(value: Any, rule: dict[str, Any], path: list[str | int]) -> None:
        if "$ref" in rule:
            visit(value, _resolve_ref(schema, str(rule["$ref"])), path)
            return
        if "oneOf" in rule:
            branch_results: list[list[SchemaIssue]] = []
            for branch in rule["oneOf"]:
                before = len(issues)
                visit(value, branch, path)
                branch_results.append(issues[before:])
                del issues[before:]
            successes = [result for result in branch_results if not result]
            if len(successes) != 1:
                issues.append(
                    SchemaIssue(
                        _pointer(path),
                        "Value must match exactly one allowed schema.",
                    )
                )
            return

        expected_type = rule.get("type")
        if expected_type is not None:
            allowed = (
                expected_type
                if isinstance(expected_type, list)
                else [expected_type]
            )
            if not any(_matches_type(value, item) for item in allowed):
                issues.append(
                    SchemaIssue(
                        _pointer(path),
                        "Expected type " + " or ".join(str(item) for item in allowed),
                    )
                )
                return
        if "const" in rule and value != rule["const"]:
            issues.append(
                SchemaIssue(
                    _pointer(path),
                    f"Value must equal {rule['const']!r}.",
                )
            )
        if "enum" in rule and value not in rule["enum"]:
            issues.append(
                SchemaIssue(
                    _pointer(path),
                    "Value must be one of " + ", ".join(map(str, rule["enum"])),
                )
            )
        if isinstance(value, str) and len(value) < int(rule.get("minLength", 0)):
            issues.append(
                SchemaIssue(
                    _pointer(path),
                    f"String length must be at least {rule['minLength']}.",
                )
            )
        if isinstance(value, list):
            if len(value) < int(rule.get("minItems", 0)):
                issues.append(
                    SchemaIssue(
                        _pointer(path),
                        f"Array must contain at least {rule['minItems']} items.",
                    )
                )
            if rule.get("uniqueItems"):
                fingerprints = [
                    json.dumps(item, ensure_ascii=False, sort_keys=True)
                    for item in value
                ]
                if len(fingerprints) != len(set(fingerprints)):
                    issues.append(
                        SchemaIssue(_pointer(path), "Array items must be unique.")
                    )
            item_rule = rule.get("items")
            if isinstance(item_rule, dict):
                for index, item in enumerate(value):
                    visit(item, item_rule, [*path, index])
        if isinstance(value, dict):
            if len(value) < int(rule.get("minProperties", 0)):
                issues.append(
                    SchemaIssue(
                        _pointer(path),
                        "Object must contain at least "
                        f"{rule['minProperties']} properties.",
                    )
                )
            required = rule.get("required") or []
            for key in required:
                if key not in value:
                    issues.append(
                        SchemaIssue(
                            _pointer([*path, key]),
                            f"Required property {key!r} is missing.",
                        )
                    )
            properties = rule.get("properties") or {}
            for key, child in value.items():
                if key in properties:
                    visit(child, properties[key], [*path, key])
                    continue
                additional = rule.get("additionalProperties", True)
                if additional is False:
                    issues.append(
                        SchemaIssue(
                            _pointer([*path, key]),
                            f"Additional property {key!r} is not allowed.",
                        )
                    )
                elif isinstance(additional, dict):
                    visit(child, additional, [*path, key])

    visit(instance, schema, [])
    return issues
