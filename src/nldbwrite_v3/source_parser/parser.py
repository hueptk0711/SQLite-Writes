from __future__ import annotations

import ast
import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any

from nldbwrite_v3.ir import SourceCollection, SourcePayload


_FENCED_BLOCK = re.compile(
    r"```(?P<language>[A-Za-z0-9_-]*)[ \t]*\r?\n?"
    r"(?P<body>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_KEY_VALUE = re.compile(
    r"^\s*[-*]?\s*([^:\n]{1,80})\s*:\s*(.*?)\s*$",
    re.MULTILINE,
)
_MARKDOWN_SEPARATOR = re.compile(r":?-{2,}:?")
_RECORD_POSTAMBLE = re.compile(r"\r?\n\s*\r?\n")
_TRAILING_BARE_SCALAR_FIELD = re.compile(
    r"^(?P<value>.*?),\s*"
    r"(?P<field>[A-Za-z][A-Za-z0-9_ ]{0,40})\s+"
    r"(?P<scalar>"
    r"[-+]?\d+(?:\.\d+)?|true|false|null|none"
    r")$",
    re.IGNORECASE | re.DOTALL,
)
_FLEXIBLE_TEXT_FIELDS = {
    "about",
    "aboutme",
    "address",
    "comment",
    "comments",
    "description",
    "label",
    "location",
    "name",
    "notes",
    "text",
    "title",
}
_CONTROL_METADATA_FIELDS = {
    "action",
    "allowedupdates",
    "conflictkey",
    "conflictpolicy",
    "duplicatepolicy",
    "instruction",
    "keystatus",
    "onduplicate",
    "operation",
    "ordering",
    "processingorder",
    "registrystate",
    "relationshiporder",
    "requirement",
    "targettable",
    "updates",
}


@dataclass(slots=True)
class _Detected:
    collections: list[SourceCollection]
    spans: list[tuple[int, int]]


def _field_order(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    fields: list[str] = []
    for row in rows:
        for field_name in row:
            if field_name not in seen:
                seen.add(field_name)
                fields.append(str(field_name))
    return fields


def _normalize_textual_null(value: str) -> Any:
    """Interpret only explicit, unquoted textual null markers."""
    stripped = value.strip()
    if stripped.casefold() in {"null", "none", "nil"}:
        return None
    return stripped


def _safe_collection_id(raw: str, fallback: str) -> str:
    value = re.sub(r"[^\w-]+", "_", str(raw), flags=re.UNICODE).strip("_")
    return value or fallback


def _canonical_field_name(raw: str) -> str:
    return re.sub(r"[\W_]+", "", str(raw), flags=re.UNICODE).casefold()


def _is_control_metadata_field(raw: str) -> bool:
    canonical = _canonical_field_name(raw)
    return (
        canonical in _CONTROL_METADATA_FIELDS
        or canonical.endswith("conflictkey")
        or canonical.endswith("updatecolumns")
    )


def _strip_common_record_prefix(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Strip a shared ``record_1.``/``row_1.`` namespace from field names."""
    if not row or any("." not in str(field) for field in row):
        return row
    prefixes = {
        str(field).rsplit(".", 1)[0]
        for field in row
    }
    if len(prefixes) != 1:
        return row
    normalized: dict[str, Any] = {}
    for field, value in row.items():
        suffix = str(field).rsplit(".", 1)[1].strip()
        if not suffix or suffix in normalized:
            return row
        normalized[suffix] = value
    return normalized


def _is_control_metadata_row(row: dict[str, Any]) -> bool:
    fields = {
        field
        for field in row
    }
    return bool(fields) and all(
        _is_control_metadata_field(field)
        for field in fields
    )


def _collection_id_from_table_field(
    row: dict[str, Any],
    fallback: str,
) -> str:
    value = row.get("table")
    if not isinstance(value, str) or not value.strip():
        return fallback
    return _safe_collection_id(value, fallback)


def _collections_from_rows(
    rows: list[dict[str, Any]],
    *,
    fallback_id: str,
    base_path: str,
    source_format: str,
) -> list[SourceCollection]:
    """Split heterogeneous JSON rows only on an explicit table discriminator."""
    normalized_rows: list[dict[str, Any]] = []
    row_controls: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        containers = [
            key
            for key in ("record", "row", "values")
            if isinstance(row.get(key), dict)
        ]
        controls: dict[str, Any] = {}
        if len(containers) == 1:
            container = containers[0]
            values = dict(row[container])
            table_value = row.get("table", row.get("target_table"))
            if isinstance(table_value, str) and table_value.strip():
                values["table"] = table_value.strip()
            controls = {
                str(key): value
                for key, value in row.items()
                if key not in {
                    container,
                    "table",
                    "target_table",
                }
            }
            row = values
        normalized_rows.append(row)
        row_controls.append(controls)
    rows = normalized_rows
    table_values = [
        str(row.get("table") or "").strip()
        for row in rows
    ]
    if (
        rows
        and all(table_values)
        and len(set(table_values)) > 1
    ):
        grouped: dict[str, list[dict[str, Any]]] = {}
        grouped_controls: dict[str, list[dict[str, Any]]] = {}
        for table_value, row, controls in zip(
            table_values,
            rows,
            row_controls,
        ):
            grouped.setdefault(table_value, []).append(row)
            grouped_controls.setdefault(table_value, []).append(controls)
        return [
            SourceCollection(
                collection_id=_safe_collection_id(table_value, fallback_id),
                source_path=(
                    f"{base_path}[*][table={table_value!r}]"
                ),
                source_format=source_format,
                rows=group_rows,
                fields=_field_order(group_rows),
                metadata={
                    "control_metadata": grouped_controls[table_value],
                },
            )
            for table_value, group_rows in grouped.items()
        ]
    return [
        SourceCollection(
            collection_id=fallback_id,
            source_path=f"{base_path}[*]",
            source_format=source_format,
            rows=rows,
            fields=_field_order(rows),
            metadata={"control_metadata": row_controls},
        )
    ]


def _deduplicate_collection_ids(
    collections: list[SourceCollection],
) -> list[SourceCollection]:
    counts: dict[str, int] = {}
    for collection in collections:
        base = collection.collection_id
        counts[base] = counts.get(base, 0) + 1
        if counts[base] > 1:
            collection.collection_id = f"{base}_{counts[base]}"
    return collections


def _collections_from_json(
    value: Any,
    *,
    fallback_id: str,
    base_path: str = "$",
) -> list[SourceCollection]:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        rows = [dict(row) for row in value]
        return _collections_from_rows(
            rows,
            fallback_id=fallback_id,
            base_path=base_path,
            source_format="json_array",
        )
    if not isinstance(value, dict):
        return []
    nested_collections: list[SourceCollection] = []
    for key, nested in value.items():
        if isinstance(nested, list) and all(
            isinstance(row, dict) for row in nested
        ):
            rows = [dict(row) for row in nested]
            nested_collections.extend(
                _collections_from_rows(
                    rows,
                    fallback_id=_safe_collection_id(str(key), fallback_id),
                    base_path=f"{base_path}.{key}",
                    source_format="json_array",
                )
            )
    if nested_collections:
        return nested_collections
    row = dict(value)
    collections = _collections_from_rows(
        [row],
        fallback_id=fallback_id,
        base_path=base_path,
        source_format="json_object",
    )
    collections[0].source_path = base_path
    return collections


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)


def _parse_markdown_text(
    text: str,
    collection_id: str,
    source_path: str,
) -> SourceCollection | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return None
    for index in range(len(lines) - 2):
        header = lines[index]
        separator = lines[index + 1]
        if "|" not in header or "|" not in separator:
            continue
        separator_cells = [
            cell.strip() for cell in separator.strip("|").split("|")
        ]
        if not separator_cells or not all(
            _MARKDOWN_SEPARATOR.fullmatch(cell)
            for cell in separator_cells
        ):
            continue
        fields = [cell.strip() for cell in header.strip("|").split("|")]
        rows: list[dict[str, Any]] = []
        for data_line in lines[index + 2 :]:
            if "|" not in data_line:
                break
            cells = [
                _normalize_textual_null(cell)
                for cell in data_line.strip("|").split("|")
            ]
            if len(cells) != len(fields):
                break
            rows.append(dict(zip(fields, cells)))
        if rows:
            return SourceCollection(
                collection_id=collection_id,
                source_path=source_path,
                source_format="markdown_table",
                rows=rows,
                fields=fields,
            )
    return None


def _parse_delimited_text(
    text: str,
    collection_id: str,
    source_path: str,
    forced_delimiter: str | None = None,
) -> SourceCollection | None:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    delimiters = (
        [(forced_delimiter, "tsv" if forced_delimiter == "\t" else "csv")]
        if forced_delimiter
        else [(",", "csv"), ("\t", "tsv")]
    )
    for delimiter, source_format in delimiters:
        if delimiter not in lines[0]:
            continue
        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
        parsed_lines = list(reader)
        if not parsed_lines:
            continue
        fields = [str(field).strip() for field in parsed_lines[0]]
        if len(fields) < 2:
            continue
        rows: list[dict[str, Any]] = []
        valid = True
        flexible_index = next(
            (
                index
                for index, field in enumerate(fields)
                if field.casefold().replace("_", " ").strip()
                in _FLEXIBLE_TEXT_FIELDS
            ),
            None,
        )
        for cells in parsed_lines[1:]:
            values = list(cells)
            if len(values) > len(fields) and flexible_index is not None:
                overflow = len(values) - len(fields)
                values = (
                    values[:flexible_index]
                    + [
                        delimiter.join(
                            values[
                                flexible_index : flexible_index + overflow + 1
                            ]
                        )
                    ]
                    + values[flexible_index + overflow + 1 :]
                )
            values = [
                _normalize_textual_null(value)
                for value in values
            ]
            if len(values) != len(fields):
                valid = False
                break
            rows.append(dict(zip(fields, values)))
        if rows and valid:
            return SourceCollection(
                collection_id=collection_id,
                source_path=source_path,
                source_format=source_format,
                rows=rows,
                fields=fields,
            )
    return None


def _control_metadata_before_fence(
    text: str,
    fence_start: int,
) -> dict[str, Any]:
    """Read only contiguous ``key=value``/``key: value`` controls above a fence."""
    prefix = text[:fence_start]
    paragraph = re.split(r"\r?\n\s*\r?\n", prefix)[-1]
    controls: dict[str, Any] = {}
    for line in paragraph.splitlines():
        match = re.fullmatch(
            r"\s*([A-Za-z][A-Za-z0-9_. -]{0,119}?)\s*[:=]\s*(.*?)\s*",
            line,
        )
        if match is None:
            continue
        key, raw_value = match.groups()
        if not _is_control_metadata_field(key):
            continue
        controls[key.strip()] = _normalize_textual_null(raw_value)
    return controls


def _detect_fenced(text: str) -> _Detected:
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    for block_index, match in enumerate(_FENCED_BLOCK.finditer(text), start=1):
        body = match.group("body").strip()
        language = match.group("language").casefold()
        parsed: list[SourceCollection] = []
        if language in {"", "json", "jsonl"} or body.startswith(("[", "{")):
            try:
                parsed = _collections_from_json(
                    json.loads(body),
                    fallback_id=f"collection_{block_index}",
                )
            except json.JSONDecodeError:
                parsed = []
        if not parsed and language in {"csv", "tsv"}:
            collection = _parse_delimited_text(
                body,
                f"table_{block_index}",
                f"$block[{block_index}]",
                "\t" if language == "tsv" else ",",
            )
            parsed = [collection] if collection else []
        if not parsed and (language in {"", "table", "markdown", "md"}):
            collection = _parse_markdown_text(
                body,
                f"table_{block_index}",
                f"$block[{block_index}]",
            )
            parsed = [collection] if collection else []
        if parsed:
            controls = _control_metadata_before_fence(
                text,
                match.start(),
            )
            if controls:
                for collection in parsed:
                    existing = list(
                        collection.metadata.get("control_metadata") or []
                    )
                    existing.append(dict(controls))
                    collection.metadata["control_metadata"] = existing
            collections.extend(parsed)
            spans.append(match.span())
    return _Detected(collections, spans)


def _detect_inline_json(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    decoder = json.JSONDecoder()
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    index = 0
    sequence = 1
    while index < len(text):
        if text[index] not in "[{" or _overlaps((index, index + 1), occupied + spans):
            index += 1
            continue
        try:
            value, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        span = (index, index + consumed)
        parsed = _collections_from_json(
            value,
            fallback_id=f"collection_{sequence}",
        )
        if parsed:
            collections.extend(parsed)
            spans.append(span)
            sequence += 1
            index = span[1]
        else:
            index += 1
    return _Detected(collections, spans)


def _balanced_literal_end(text: str, start: int) -> int | None:
    opening = text[start]
    if opening not in "[{":
        return None
    pairs = {"]": "[", "}": "{"}
    stack = [opening]
    quote: str | None = None
    escaped = False
    for index in range(start + 1, len(text)):
        character = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "[{":
            stack.append(character)
        elif character in "]}":
            if not stack or stack[-1] != pairs[character]:
                return None
            stack.pop()
            if not stack:
                return index + 1
    return None


def _detect_python_literals(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    """Parse safe Python-style list/dict payloads common in the dataset."""
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    sequence = 1
    index = 0
    while index < len(text):
        if text[index] not in "[{" or _overlaps(
            (index, index + 1), occupied + spans
        ):
            index += 1
            continue
        end = _balanced_literal_end(text, index)
        if end is None:
            index += 1
            continue
        try:
            value = ast.literal_eval(text[index:end])
        except (SyntaxError, ValueError):
            index += 1
            continue
        parsed = _collections_from_json(
            value,
            fallback_id=f"collection_{sequence}",
        )
        if parsed:
            collections.extend(parsed)
            spans.append((index, end))
            sequence += 1
            index = end
        else:
            index += 1
    return _Detected(collections, spans)


def _line_blocks(text: str) -> list[tuple[int, int, str]]:
    output: list[tuple[int, int, str]] = []
    cursor = 0
    for match in re.finditer(r"\r?\n\s*\r?\n", text):
        end = match.start()
        if text[cursor:end].strip():
            output.append((cursor, end, text[cursor:end]))
        cursor = match.end()
    if text[cursor:].strip():
        output.append((cursor, len(text), text[cursor:]))
    return output


def _record_body(
    text: str,
    start: int,
    end: int,
) -> tuple[str, int]:
    """Keep a blank-line postamble outside the structured record span."""
    raw = text[start:end]
    postamble = _RECORD_POSTAMBLE.search(raw)
    record_end = start + postamble.start() if postamble else end
    return text[start:record_end].strip(), record_end


def _store_colon_field(
    row: dict[str, Any],
    key: str,
    value: str,
) -> None:
    """Recover a final ``Field scalar`` accidentally joined to a colon value."""
    trailing = _TRAILING_BARE_SCALAR_FIELD.fullmatch(value)
    if trailing is None:
        row[key] = _normalize_textual_null(value)
        return
    primary_value = trailing.group("value").strip().rstrip(",")
    trailing_field = trailing.group("field").strip()
    trailing_value = trailing.group("scalar").strip()
    if not primary_value or trailing_field in row:
        row[key] = _normalize_textual_null(value)
        return
    row[key] = _normalize_textual_null(primary_value)
    row[trailing_field] = _normalize_textual_null(trailing_value)


def _detect_tables(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    sequence = 1
    lines = list(re.finditer(r"^.*(?:\r?\n|$)", text, re.MULTILINE))
    index = 0
    while index + 2 < len(lines):
        first = lines[index]
        second = lines[index + 1]
        if _overlaps(first.span(), occupied + spans):
            index += 1
            continue
        header = first.group(0).strip()
        separator = second.group(0).strip()
        separator_cells = [
            cell.strip() for cell in separator.strip("|").split("|")
        ]
        if (
            "|" not in header
            or "|" not in separator
            or not separator_cells
            or not all(_MARKDOWN_SEPARATOR.fullmatch(cell) for cell in separator_cells)
        ):
            index += 1
            continue
        end_index = index + 2
        while end_index < len(lines):
            candidate = lines[end_index]
            if _overlaps(candidate.span(), occupied + spans):
                break
            if "|" not in candidate.group(0):
                break
            end_index += 1
        start = first.start()
        end = lines[end_index - 1].end()
        collection = _parse_markdown_text(
            text[start:end],
            f"table_{sequence}",
            f"$table[{sequence}]",
        )
        if collection:
            collections.append(collection)
            spans.append((start, end))
            sequence += 1
            index = end_index
        else:
            index += 1
    return _Detected(collections, spans)


def _detect_delimited_blocks(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    sequence = 1
    for start, end, block in _line_blocks(text):
        if _overlaps((start, end), occupied + spans):
            continue
        collection = _parse_delimited_text(
            block,
            f"table_{sequence}",
            f"$table[{sequence}]",
        )
        collection_span = (start, end)
        if collection is None:
            line_matches = list(re.finditer(r"^.*(?:\r?\n|$)", block, re.MULTILINE))
            for line_index in range(max(0, len(line_matches) - 1)):
                candidate_start = line_matches[line_index].start()
                for candidate_end_index in range(
                    len(line_matches) - 1,
                    line_index,
                    -1,
                ):
                    candidate_end = line_matches[candidate_end_index].end()
                    candidate = _parse_delimited_text(
                        block[candidate_start:candidate_end],
                        f"table_{sequence}",
                        f"$table[{sequence}]",
                    )
                    if candidate is not None:
                        collection = candidate
                        collection_span = (
                            start + candidate_start,
                            start + candidate_end,
                        )
                        break
                if collection is not None:
                    break
        if collection:
            collections.append(collection)
            spans.append(collection_span)
            sequence += 1
    return _Detected(collections, spans)


def _detect_numbered_key_value_records(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    """Extract ``1. key: value, key: value`` record sequences."""
    markers = [
        match
        for match in re.finditer(r"(?<![\w.])(\d+)\.\s+", text)
        if not _overlaps(match.span(), occupied)
    ]
    if len(markers) < 2:
        return _Detected([], [])
    runs: list[list[re.Match[str]]] = []
    current: list[re.Match[str]] = []
    previous_number: int | None = None
    for marker in markers:
        number = int(marker.group(1))
        if previous_number is None or number == previous_number + 1:
            current.append(marker)
        else:
            if len(current) >= 2 and int(current[0].group(1)) == 1:
                runs.append(current)
            current = [marker]
        previous_number = number
    if len(current) >= 2 and int(current[0].group(1)) == 1:
        runs.append(current)

    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    for sequence, run in enumerate(runs, start=1):
        grouped_rows: dict[str, list[dict[str, Any]]] = {}
        record_spans: list[tuple[int, int]] = []
        for marker_index, marker in enumerate(run):
            start = marker.end()
            end = (
                run[marker_index + 1].start()
                if marker_index + 1 < len(run)
                else len(text)
            )
            body, record_end = _record_body(text, start, end)
            row: dict[str, Any] = {}
            section_id: str | None = None
            section_match = re.match(
                r"([^:,\n]{1,80})\s*:\s*(.*)\Z",
                body,
                re.DOTALL,
            )
            if section_match and "=" in section_match.group(2):
                section_id = _safe_collection_id(
                    section_match.group(1).strip(),
                    f"section_{sequence}",
                )
                for field_match in re.finditer(
                    r"(?:^|,\s*)([^=,\n]{1,80})\s*=\s*"
                    r"(.*?)(?=,\s*[^=,\n]{1,80}\s*=|\Z)",
                    section_match.group(2),
                    re.DOTALL,
                ):
                    key = field_match.group(1).strip()
                    value = field_match.group(2).strip().rstrip(",")
                    if key and value:
                        _store_colon_field(row, key, value)
            else:
                for field_match in re.finditer(
                    r"(?:^|,\s*)([^,:;\n]{1,80})\s*:\s*"
                    r"(.*?)(?=,\s*[^,:;\n]{1,80}\s*:|\Z)",
                    body,
                    re.DOTALL,
                ):
                    key = field_match.group(1).strip()
                    value = field_match.group(2).strip().rstrip(",")
                    if key and value:
                        _store_colon_field(row, key, value)
            minimum_fields = 1 if section_id else 2
            if len(row) >= minimum_fields:
                group_key = section_id or f"section_{sequence}"
                grouped_rows.setdefault(group_key, []).append(row)
                record_spans.append((marker.start(), record_end))
        if sum(len(rows) for rows in grouped_rows.values()) >= 2:
            for group_index, (group_key, rows) in enumerate(
                grouped_rows.items(),
                start=1,
            ):
                collections.append(
                    SourceCollection(
                        collection_id=group_key,
                        source_path=(
                            f"$section[{sequence}][{group_index}][*]"
                        ),
                        source_format="key_value",
                        rows=rows,
                        fields=_field_order(rows),
                    )
                )
            spans.append(
                (record_spans[0][0], record_spans[-1][1])
            )
    return _Detected(collections, spans)


def _detect_bulleted_key_value_records(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    """Extract inline ``- key: value; - key: value`` record sequences."""
    markers = [
        match
        for match in re.finditer(
            r"(?:^|;\s*)-\s+(?=[^,:;\n]{1,80}\s*:)",
            text,
            re.MULTILINE,
        )
        if not _overlaps(match.span(), occupied)
    ]
    if len(markers) < 2:
        return _Detected([], [])
    rows: list[dict[str, Any]] = []
    for marker_index, marker in enumerate(markers):
        start = marker.end()
        end = (
            markers[marker_index + 1].start()
            if marker_index + 1 < len(markers)
            else len(text)
        )
        body, record_end = _record_body(text, start, end)
        body = body.rstrip(";")
        row: dict[str, Any] = {}
        for field_match in re.finditer(
            r"(?:^|,\s*)([^,:;\n]{1,80})\s*:\s*"
            r"(.*?)(?=,\s*[^,:;\n]{1,80}\s*:|\Z)",
            body,
            re.DOTALL,
        ):
            key = field_match.group(1).strip()
            value = field_match.group(2).strip().rstrip(",")
            if key and value:
                _store_colon_field(row, key, value)
        if len(row) >= 2:
            rows.append(row)
            end = record_end
    if len(rows) < 2:
        return _Detected([], [])
    return _Detected(
        [
            SourceCollection(
                collection_id="section_1",
                source_path="$section[1][*]",
                source_format="key_value",
                rows=rows,
                fields=_field_order(rows),
            )
        ],
        [(markers[0].start(), end)],
    )


def _detect_key_value_sections(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    sequence = 1
    for start, end, block in _line_blocks(text):
        if _overlaps((start, end), occupied + spans):
            continue
        row: dict[str, Any] = {}
        matched_spans: list[tuple[int, int]] = []
        for match in _KEY_VALUE.finditer(block):
            key, value = match.groups()
            if key.casefold().strip() in {"subject", "note"}:
                continue
            row[key.strip()] = _normalize_textual_null(value)
            matched_spans.append((start + match.start(), start + match.end()))
        row = _strip_common_record_prefix(row)
        if len(row) < 2:
            continue
        if _is_control_metadata_row(row):
            continue
        collections.append(
            SourceCollection(
                collection_id=_collection_id_from_table_field(
                    row,
                    f"section_{sequence}",
                ),
                source_path=f"$section[{sequence}]",
                source_format="key_value",
                rows=[row],
                fields=_field_order([row]),
            )
        )
        spans.extend(matched_spans)
        sequence += 1
    return _Detected(collections, spans)


def _detect_equals_key_value_records(
    text: str,
    occupied: list[tuple[int, int]],
) -> _Detected:
    """Parse ``table.record.field=value`` and flat ``field=value`` payloads."""
    matches = [
        match
        for match in re.finditer(
            r"(?m)^\s*([^=\r\n]{1,120}?)\s*=\s*(.*?)\s*$",
            text,
        )
        if not _overlaps(match.span(), occupied)
    ]
    if len(matches) < 2:
        return _Detected([], [])

    controls: dict[str, Any] = {}
    target_table = ""
    data_items: list[
        tuple[re.Match[str], str, str, str, Any]
    ] = []
    for match in matches:
        raw_key, raw_value = match.groups()
        key = raw_key.strip()
        value = _normalize_textual_null(raw_value)
        canonical = _canonical_field_name(key)
        if _is_control_metadata_field(key):
            controls[key] = value
            if canonical == "targettable" and isinstance(value, str):
                target_table = value.strip()
            continue
        parts = [part.strip() for part in key.split(".") if part.strip()]
        if len(parts) >= 2:
            field = parts[-1]
            if len(parts) >= 3 and parts[-2].isdigit():
                record_id = parts[-2]
                collection_name = ".".join(parts[:-2])
            else:
                record_id = "1"
                collection_name = ".".join(parts[:-1])
        else:
            field = key
            record_id = "1"
            collection_name = target_table or "section_1"
        if _is_control_metadata_field(field):
            controls[key] = value
            continue
        data_items.append(
            (match, collection_name, record_id, field, value)
        )

    if not data_items:
        return _Detected([], [])
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    group_spans: dict[str, list[tuple[int, int]]] = {}
    for match, collection_name, record_id, field, value in data_items:
        rows = grouped.setdefault(collection_name, {})
        rows.setdefault(record_id, {})[field] = value
        group_spans.setdefault(collection_name, []).append(match.span())

    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    for sequence, (collection_name, records) in enumerate(
        grouped.items(),
        start=1,
    ):
        rows = list(records.values())
        if not rows or any(not row for row in rows):
            continue
        collections.append(
            SourceCollection(
                collection_id=_safe_collection_id(
                    collection_name,
                    f"section_{sequence}",
                ),
                source_path=f"$equals[{sequence}][*]",
                source_format="key_value",
                rows=rows,
                fields=_field_order(rows),
                metadata={
                    "control_metadata": [dict(controls)]
                    if controls
                    else [],
                },
            )
        )
        spans.extend(group_spans.get(collection_name) or [])
    return _Detected(collections, spans)


def _instruction_without_payload(
    text: str,
    spans: list[tuple[int, int]],
) -> str:
    characters = list(text)
    for start, end in spans:
        for index in range(max(start, 0), min(end, len(characters))):
            if characters[index] not in "\r\n":
                characters[index] = " "
    instruction = "".join(characters)
    instruction = re.sub(r"[ \t]+", " ", instruction)
    instruction = re.sub(r" *\r?\n *", "\n", instruction)
    instruction = re.sub(r"\n{3,}", "\n\n", instruction)
    return instruction.strip()


def _overall_format(collections: list[SourceCollection]) -> str:
    if not collections:
        return "free_text"
    if len(collections) == 1:
        return collections[0].source_format
    formats = {collection.source_format for collection in collections}
    if formats <= {"json_array", "json_object"}:
        return "multi_json_collection"
    if formats <= {"markdown_table", "csv", "tsv", "key_value"}:
        return "multi_table"
    return "mixed"


def _assign_source_reference_ids(
    collections: list[SourceCollection],
) -> None:
    """Attach compact, deterministic IDs without changing legacy names/paths."""
    for collection_index, collection in enumerate(collections, start=1):
        collection.reference_id = f"c{collection_index}"
        collection.selector_id = f"s{collection_index}"
        collection.field_ids = {
            field_name: f"c{collection_index}.f{field_index}"
            for field_index, field_name in enumerate(
                collection.fields,
                start=1,
            )
        }


def parse_source_payload(text: str) -> SourcePayload:
    """Extract every structured collection without rewriting source cells."""
    detected = _detect_fenced(text)
    collections = list(detected.collections)
    spans = list(detected.spans)

    inline_json = _detect_inline_json(text, spans)
    collections.extend(inline_json.collections)
    spans.extend(inline_json.spans)

    python_literals = _detect_python_literals(text, spans)
    collections.extend(python_literals.collections)
    spans.extend(python_literals.spans)

    markdown = _detect_tables(text, spans)
    collections.extend(markdown.collections)
    spans.extend(markdown.spans)

    numbered = _detect_numbered_key_value_records(text, spans)
    collections.extend(numbered.collections)
    spans.extend(numbered.spans)

    bulleted = _detect_bulleted_key_value_records(text, spans)
    collections.extend(bulleted.collections)
    spans.extend(bulleted.spans)

    delimited = _detect_delimited_blocks(text, spans)
    collections.extend(delimited.collections)
    spans.extend(delimited.spans)

    equals_key_values = _detect_equals_key_value_records(text, spans)
    collections.extend(equals_key_values.collections)
    spans.extend(equals_key_values.spans)

    key_values = _detect_key_value_sections(text, spans)
    collections.extend(key_values.collections)
    spans.extend(key_values.spans)

    collections = _deduplicate_collection_ids(collections)
    _assign_source_reference_ids(collections)
    instruction = _instruction_without_payload(text, spans)
    mode = "semi_structured" if collections else "free_text"
    return SourcePayload(
        mode=mode,
        source_format=_overall_format(collections),
        collections=collections,
        instruction_text=instruction if collections else text.strip(),
        raw_text=text,
        metadata={
            "collection_count": len(collections),
            "row_count": sum(len(collection.rows) for collection in collections),
            "field_count": sum(len(collection.fields) for collection in collections),
            "multi_block_detected": len(collections) > 1,
            "reference_contract": "source-ids-v1",
        },
    )
