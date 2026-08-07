from __future__ import annotations

import json
import re
from typing import Any


_FENCE = re.compile(
    r"```(?:sql|json)?[ \t]*\r?\n?(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


def extract_json_object(raw_output: str) -> tuple[dict[str, Any] | None, str | None]:
    candidates = [match.group(1).strip() for match in _FENCE.finditer(raw_output)]
    candidates.append(raw_output.strip())
    decoder = json.JSONDecoder()
    error: str | None = None
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError as exc:
                error = exc.msg
                continue
            if isinstance(value, dict):
                return value, None
    return None, error or "No JSON object found."


def _split_sql(text: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        buffer.append(char)
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    buffer.append(text[index + 1])
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        index += 1
    final = "".join(buffer).strip()
    if final:
        statements.append(final)
    return statements


def extract_sql_statements(raw_output: str) -> tuple[list[str], str | None]:
    fenced = [match.group(1).strip() for match in _FENCE.finditer(raw_output)]
    candidate = next(
        (text for text in fenced if re.search(r"\b(?:INSERT|REPLACE)\b", text, re.I)),
        raw_output.strip(),
    )
    start = re.search(r"\b(?:INSERT|REPLACE)\b", candidate, re.IGNORECASE)
    if not start:
        return [], "No INSERT/REPLACE statement found."
    statements = _split_sql(candidate[start.start() :])
    statements = [
        statement
        for statement in statements
        if statement.strip()
    ]
    return statements, None if statements else "No SQL statement parsed."


def extract_patch_list(raw_output: str) -> tuple[list[dict[str, Any]], str | None]:
    value, error = extract_json_object(raw_output)
    if value is None:
        return [], error
    patches = value.get("patches")
    if not isinstance(patches, list) or not all(
        isinstance(patch, dict) for patch in patches
    ):
        return [], "Repair output must contain a patches array."
    return patches, None

