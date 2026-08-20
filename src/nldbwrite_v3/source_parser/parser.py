from __future__ import annotations

import ast
import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

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
_D_CONTROL_METADATA_FIELDS = _CONTROL_METADATA_FIELDS | {
    "conflictaction",
    "conflictbehavior",
    "conflicttarget",
    "policy",
    "table",
    "updatecolumns",
}
_D_STRONG_CONTROL_FIELDS = _D_CONTROL_METADATA_FIELDS - {"policy", "table"}

# Stage-2 D must not infer a strong control context from a reserved-looking
# field name alone.  These aliases mirror the frozen A control semantics and
# are intentionally conservative.
_D_OPERATION_VALUE_ALIASES = {
    "upsert_update",
    "upsert",
    "insert_or_update",
    "insert_update",
    "do_update",
    "insert_ignore",
    "insert_or_ignore",
    "do_nothing",
    "plain_insert",
    "insert",
    "error_on_conflict",
    "raise_on_conflict",
    "fail_on_conflict",
}
_D_OPERATION_SEMANTIC_FIELDS = {"operation", "writeoperation", "operationtype"}
_D_CONFLICT_ACTION_SEMANTIC_FIELDS = {
    "conflict",
    "conflictaction",
    "conflictbehavior",
    "conflictpolicy",
    "duplicatepolicy",
    "onconflict",
    "onduplicate",
}
_D_CONFLICT_TARGET_SEMANTIC_FIELDS = {
    "conflictkey",
    "conflicttarget",
    "conflicttargets",
    "uniquekey",
}
_D_UPDATE_SEMANTIC_FIELDS = {
    "update",
    "updates",
    "updatecolumns",
    "requestedupdatecolumns",
    "allowedupdates",
    "excludedupdatecolumns",
    "donotupdate",
    "donotupdatecolumns",
}
_ROW_MARKER_KEY = re.compile(r"^(?:row|record)(?:[_ -]?id)?$", re.IGNORECASE)
_ROW_HEADING = re.compile(
    r"^\s*(?P<label>(?:row|record)[ _-]?\d+)\s*:\s*$",
    re.IGNORECASE,
)
_DOTTED_ROW_FIELD = re.compile(
    r"^(?:(?P<prefix>.+?)\.)?"
    r"(?P<label>(?:row|record)[ _-]?\d+)\."
    r"(?P<field>[^.]+)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _Detected:
    collections: list[SourceCollection]
    spans: list[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class StructuredParserConfig:
    """Stage-2 D deterministic structured-source parser policy.

    The default is deliberately legacy-compatible.  Stage-2 D is enabled only
    by an explicit config so the frozen A-C checkpoint remains reproducible.
    """

    enabled: bool = False
    null_literal_policy: str = "legacy"
    emit_value_provenance: bool = False

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "StructuredParserConfig":
        value = value or {}
        enabled = bool(value.get("enabled", False))
        policy = str(value.get("null_literal_policy") or (
            "explicit_only" if enabled else "legacy"
        ))
        if policy not in {"legacy", "explicit_only"}:
            raise ValueError(
                "structured_source_parser.null_literal_policy must be "
                "'legacy' or 'explicit_only'"
            )
        return cls(
            enabled=enabled,
            null_literal_policy=policy,
            emit_value_provenance=bool(
                value.get("emit_value_provenance", enabled)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "null_literal_policy": self.null_literal_policy,
            "emit_value_provenance": self.emit_value_provenance,
        }


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


def _strip_matching_literal_quotes(value: str) -> tuple[str, bool]:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {
        "'",
        '"',
    }:
        quote = stripped[0]
        inner = stripped[1:-1]
        if quote == '"':
            inner = inner.replace('""', '"')
        return inner, True
    return stripped, False


def _normalize_stage2_text_value(
    value: str,
    config: StructuredParserConfig,
    *,
    raw_lexeme: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Normalize a text cell under the Stage-2 D explicit-null contract."""
    raw = value if raw_lexeme is None else raw_lexeme
    parsed, quoted = _strip_matching_literal_quotes(
        raw if raw_lexeme is not None else value
    )
    if raw_lexeme is not None and not quoted:
        # ``csv.reader`` is the source of truth for CSV escape handling.
        parsed = value.strip()
    elif raw_lexeme is not None and quoted:
        # The lexical helper identifies quoting; csv.reader provides the
        # correctly unescaped value.
        parsed = value

    folded = str(parsed).casefold()
    if quoted:
        normalized: Any = parsed
        rule = "quoted_literal_preserved"
        confidence = "exact"
    elif folded == "null":
        normalized = None
        rule = "explicit_text_null_to_null"
        confidence = "high"
    elif folded in {"none", "nil"}:
        normalized = parsed
        rule = "ambiguous_text_null_preserved"
        confidence = "high"
    else:
        normalized = parsed
        rule = "identity"
        confidence = "exact"

    return normalized, {
        "raw_value": raw,
        "parsed_value": parsed,
        "normalized_value": normalized,
        "coercion_rule": rule,
        "coercion_confidence": confidence,
    }


def _normalize_source_text_value(
    value: str,
    config: StructuredParserConfig,
    *,
    raw_lexeme: str | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    if not config.enabled or config.null_literal_policy == "legacy":
        return _normalize_textual_null(value), None
    normalized, trace = _normalize_stage2_text_value(
        value,
        config,
        raw_lexeme=raw_lexeme,
    )
    return normalized, trace if config.emit_value_provenance else None


def _append_value_trace(
    traces: list[dict[str, Any]],
    *,
    row_index: int,
    field: str,
    trace: dict[str, Any] | None,
) -> None:
    if trace is None:
        return
    traces.append(
        {
            "row_index": row_index,
            "field": field,
            **trace,
        }
    )


def _split_delimited_lexemes(line: str, delimiter: str) -> list[str]:
    """Split a single CSV/TSV line while retaining lexical quote markers."""
    output: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(line):
        character = line[index]
        if character == '"':
            if quoted and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif character == delimiter and not quoted:
            output.append(line[start:index])
            start = index + 1
        index += 1
    output.append(line[start:])
    return output


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


def _is_d_control_metadata_field(raw: str) -> bool:
    canonical = _canonical_field_name(raw)
    return (
        canonical in _D_CONTROL_METADATA_FIELDS
        or canonical.endswith("conflictkey")
        or canonical.endswith("conflicttarget")
        or canonical.endswith("updatecolumns")
    )


def _is_d_strong_control_field(raw: str) -> bool:
    canonical = _canonical_field_name(raw)
    return (
        canonical in _D_STRONG_CONTROL_FIELDS
        or canonical.endswith("conflictkey")
        or canonical.endswith("conflicttarget")
        or canonical.endswith("updatecolumns")
    )


def _d_control_value_key(value: Any) -> str:
    text, _ = _strip_matching_literal_quotes(str(value))
    text = text.strip().casefold()
    text = re.sub(r"[\s-]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _looks_like_d_identifier_list(value: Any) -> bool:
    text, _ = _strip_matching_literal_quotes(str(value))
    text = text.strip()
    if not text:
        return False
    parts = [part.strip() for part in re.split(r"[,;]", text) if part.strip()]
    if not parts:
        return False
    return all(
        re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_]*)(?:\.(?:[A-Za-z_][A-Za-z0-9_]*))*",
            part,
        )
        is not None
        for part in parts
    )


def _is_high_confidence_d_control_signal(field: str, value: Any) -> bool:
    """Return whether a field/value pair proves D control context.

    Context-only names such as ``table`` and ``policy`` are deliberately not
    sufficient.  In particular, ``operation=login`` must not reclassify a
    neighboring payload field named ``table``.
    """
    canonical = _canonical_field_name(field)
    if canonical in _D_OPERATION_SEMANTIC_FIELDS:
        return _d_control_value_key(value) in _D_OPERATION_VALUE_ALIASES
    if canonical in _D_CONFLICT_ACTION_SEMANTIC_FIELDS:
        return _d_control_value_key(value) in _D_OPERATION_VALUE_ALIASES
    if (
        canonical in _D_CONFLICT_TARGET_SEMANTIC_FIELDS
        or canonical.endswith("conflictkey")
        or canonical.endswith("conflicttarget")
    ):
        return _looks_like_d_identifier_list(value)
    if canonical in _D_UPDATE_SEMANTIC_FIELDS or canonical.endswith("updatecolumns"):
        return _looks_like_d_identifier_list(value)
    return False


def _is_d_control_metadata_row(row: dict[str, Any]) -> bool:
    if not row:
        return False
    return (
        all(_is_d_control_metadata_field(field) for field in row)
        and any(
            _is_high_confidence_d_control_signal(field, value)
            for field, value in row.items()
        )
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
    parser_config: StructuredParserConfig,
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
        value_traces: list[dict[str, Any]] = []
        for data_line in lines[index + 2 :]:
            if "|" not in data_line:
                break
            raw_cells = [
                cell.strip() for cell in data_line.strip("|").split("|")
            ]
            if len(raw_cells) != len(fields):
                break
            row: dict[str, Any] = {}
            row_index = len(rows)
            for field, raw_cell in zip(fields, raw_cells):
                normalized, trace = _normalize_source_text_value(
                    raw_cell,
                    parser_config,
                )
                row[field] = normalized
                _append_value_trace(
                    value_traces,
                    row_index=row_index,
                    field=field,
                    trace=trace,
                )
            rows.append(row)
        if rows:
            metadata: dict[str, Any] = {}
            if value_traces:
                metadata["value_provenance"] = value_traces
            return SourceCollection(
                collection_id=collection_id,
                source_path=source_path,
                source_format="markdown_table",
                rows=rows,
                fields=fields,
                metadata=metadata,
            )
    return None

def _parse_delimited_text(
    text: str,
    collection_id: str,
    source_path: str,
    parser_config: StructuredParserConfig,
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
        value_traces: list[dict[str, Any]] = []
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
        for data_index, cells in enumerate(parsed_lines[1:], start=1):
            values = list(cells)
            raw_lexemes = (
                _split_delimited_lexemes(lines[data_index], delimiter)
                if data_index < len(lines)
                else [str(value) for value in values]
            )
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
                if len(raw_lexemes) >= flexible_index + overflow + 1:
                    raw_lexemes = (
                        raw_lexemes[:flexible_index]
                        + [
                            delimiter.join(
                                raw_lexemes[
                                    flexible_index : flexible_index + overflow + 1
                                ]
                            )
                        ]
                        + raw_lexemes[flexible_index + overflow + 1 :]
                    )
            if len(values) != len(fields):
                valid = False
                break
            if len(raw_lexemes) != len(values):
                raw_lexemes = [str(value) for value in values]
            row: dict[str, Any] = {}
            row_index = len(rows)
            for field, value, raw_lexeme in zip(fields, values, raw_lexemes):
                normalized, trace = _normalize_source_text_value(
                    value,
                    parser_config,
                    raw_lexeme=raw_lexeme,
                )
                row[field] = normalized
                _append_value_trace(
                    value_traces,
                    row_index=row_index,
                    field=field,
                    trace=trace,
                )
            rows.append(row)
        if rows and valid:
            metadata: dict[str, Any] = {}
            if value_traces:
                metadata["value_provenance"] = value_traces
            return SourceCollection(
                collection_id=collection_id,
                source_path=source_path,
                source_format=source_format,
                rows=rows,
                fields=fields,
                metadata=metadata,
            )
    return None

def _control_metadata_before_fence(
    text: str,
    fence_start: int,
    parser_config: StructuredParserConfig,
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
        normalized, _ = _normalize_source_text_value(
            raw_value,
            parser_config,
        )
        controls[key.strip()] = normalized
    return controls


def _detect_fenced(
    text: str,
    parser_config: StructuredParserConfig,
) -> _Detected:
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
                parser_config,
                "\t" if language == "tsv" else ",",
            )
            parsed = [collection] if collection else []
        if not parsed and (language in {"", "table", "markdown", "md"}):
            collection = _parse_markdown_text(
                body,
                f"table_{block_index}",
                f"$block[{block_index}]",
                parser_config,
            )
            parsed = [collection] if collection else []
        if parsed:
            controls = _control_metadata_before_fence(
                text,
                match.start(),
                parser_config,
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
    parser_config: StructuredParserConfig,
    *,
    traces: list[dict[str, Any]] | None = None,
    row_index: int = 0,
) -> None:
    """Recover a final ``Field scalar`` accidentally joined to a colon value."""

    def store(field: str, raw: str) -> None:
        normalized, trace = _normalize_source_text_value(raw, parser_config)
        row[field] = normalized
        if traces is not None:
            _append_value_trace(
                traces,
                row_index=row_index,
                field=field,
                trace=trace,
            )

    trailing = _TRAILING_BARE_SCALAR_FIELD.fullmatch(value)
    if trailing is None:
        store(key, value)
        return
    primary_value = trailing.group("value").strip().rstrip(",")
    trailing_field = trailing.group("field").strip()
    trailing_value = trailing.group("scalar").strip()
    if not primary_value or trailing_field in row:
        store(key, value)
        return
    store(key, primary_value)
    store(trailing_field, trailing_value)


def _detect_tables(
    text: str,
    occupied: list[tuple[int, int]],
    parser_config: StructuredParserConfig,
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
            parser_config,
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
    parser_config: StructuredParserConfig,
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
            parser_config,
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
                        parser_config,
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
    parser_config: StructuredParserConfig,
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
        grouped_traces: dict[str, list[dict[str, Any]]] = {}
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
            row_traces: list[dict[str, Any]] = []
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
                        _store_colon_field(
                            row,
                            key,
                            value,
                            parser_config,
                            traces=row_traces,
                            row_index=0,
                        )
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
                        _store_colon_field(
                            row,
                            key,
                            value,
                            parser_config,
                            traces=row_traces,
                            row_index=0,
                        )
            minimum_fields = 1 if section_id else 2
            if len(row) >= minimum_fields:
                group_key = section_id or f"section_{sequence}"
                group_rows = grouped_rows.setdefault(group_key, [])
                row_index = len(group_rows)
                group_rows.append(row)
                for trace in row_traces:
                    trace["row_index"] = row_index
                grouped_traces.setdefault(group_key, []).extend(row_traces)
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
                        metadata=(
                            {"value_provenance": grouped_traces[group_key]}
                            if grouped_traces.get(group_key)
                            else {}
                        ),
                    )
                )
            spans.append(
                (record_spans[0][0], record_spans[-1][1])
            )
    return _Detected(collections, spans)


def _detect_bulleted_key_value_records(
    text: str,
    occupied: list[tuple[int, int]],
    parser_config: StructuredParserConfig,
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
    value_traces: list[dict[str, Any]] = []
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
        row_traces: list[dict[str, Any]] = []
        for field_match in re.finditer(
            r"(?:^|,\s*)([^,:;\n]{1,80})\s*:\s*"
            r"(.*?)(?=,\s*[^,:;\n]{1,80}\s*:|\Z)",
            body,
            re.DOTALL,
        ):
            key = field_match.group(1).strip()
            value = field_match.group(2).strip().rstrip(",")
            if key and value:
                _store_colon_field(
                    row,
                    key,
                    value,
                    parser_config,
                    traces=row_traces,
                    row_index=len(rows),
                )
        if len(row) >= 2:
            rows.append(row)
            value_traces.extend(row_traces)
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
                metadata=(
                    {"value_provenance": value_traces}
                    if value_traces
                    else {}
                ),
            )
        ],
        [(markers[0].start(), end)],
    )


def _detect_explicit_equals_rows(
    text: str,
    occupied: list[tuple[int, int]],
    parser_config: StructuredParserConfig,
) -> _Detected:
    """Stage-2 D parser for explicit repeated-row ``key=value`` layouts.

    This detector is intentionally gated behind the D config and requires at
    least two explicit row labels/markers.  It therefore does not broaden the
    historical single-row grammar.
    """
    if not parser_config.enabled:
        return _Detected([], [])

    controls: dict[str, Any] = {}
    rows_by_label: dict[str, dict[str, Any]] = {}
    row_order: list[str] = []
    row_spans: list[tuple[int, int]] = []
    value_traces: list[dict[str, Any]] = []
    current_label: str | None = None
    unknown_pre_row_assignment = False
    dotted_prefixes: set[str] = set()

    def ensure_row(label: str) -> dict[str, Any]:
        normalized_label = _canonical_field_name(label)
        if normalized_label not in rows_by_label:
            rows_by_label[normalized_label] = {}
            row_order.append(normalized_label)
        return rows_by_label[normalized_label]

    def add_value(
        label: str,
        field: str,
        raw_value: str,
        *,
        span: tuple[int, int],
    ) -> None:
        row = ensure_row(label)
        if field in row:
            # Duplicate fields inside one explicitly delimited row are
            # ambiguous.  Fail closed by making this detector inapplicable.
            raise ValueError("duplicate_explicit_row_field")
        normalized, trace = _normalize_source_text_value(
            raw_value,
            parser_config,
        )
        row[field] = normalized
        _append_value_trace(
            value_traces,
            row_index=row_order.index(_canonical_field_name(label)),
            field=field,
            trace=trace,
        )
        row_spans.append(span)

    try:
        for line_match in re.finditer(r"^.*(?:\r?\n|$)", text, re.MULTILINE):
            if not line_match.group(0):
                continue
            span = line_match.span()
            if _overlaps(span, occupied):
                continue
            line = line_match.group(0).strip()
            if not line:
                continue

            heading = _ROW_HEADING.fullmatch(line)
            if heading is not None:
                current_label = heading.group("label")
                ensure_row(current_label)
                row_spans.append(span)
                continue

            equals = re.fullmatch(
                r"\s*([^=\r\n]{1,160}?)\s*=\s*(.*?)\s*",
                line,
            )
            if equals is None:
                continue
            raw_key, raw_value = equals.groups()
            key = raw_key.strip()

            dotted = _DOTTED_ROW_FIELD.fullmatch(key)
            if dotted is not None:
                label = dotted.group("label")
                prefix = str(dotted.group("prefix") or "").strip(". ")
                if prefix:
                    dotted_prefixes.add(prefix)
                add_value(
                    label,
                    dotted.group("field").strip(),
                    raw_value,
                    span=span,
                )
                continue

            if _ROW_MARKER_KEY.fullmatch(key):
                marker, quoted = _strip_matching_literal_quotes(raw_value)
                if quoted or not re.fullmatch(r"\d+", str(marker).strip()):
                    return _Detected([], [])
                current_label = f"row_{str(marker).strip()}"
                ensure_row(current_label)
                row_spans.append(span)
                continue

            if current_label is not None:
                add_value(current_label, key, raw_value, span=span)
                continue

            if _is_d_control_metadata_field(key):
                normalized, _ = _normalize_source_text_value(
                    raw_value,
                    parser_config,
                )
                controls[key] = normalized
                continue

            # Do not silently drop a top-level data assignment merely because
            # explicit rows appear later.  Let the legacy parser handle it.
            unknown_pre_row_assignment = True
    except ValueError:
        return _Detected([], [])

    # A dotted explicit-row grammar is only supported for one collection
    # prefix at this checkpoint.  Multiple prefixes (for example parent/child)
    # must never be merged by row label.  Defer to the historical parser path
    # instead of inventing a multi-collection interpretation here.
    if len(dotted_prefixes) > 1:
        return _Detected([], [])

    rows = [rows_by_label[label] for label in row_order if rows_by_label[label]]
    if unknown_pre_row_assignment or len(rows) < 2:
        return _Detected([], [])

    table_value = next(
        (
            value
            for key, value in controls.items()
            if _canonical_field_name(key) in {"table", "targettable"}
            and isinstance(value, str)
            and value.strip()
        ),
        "",
    )
    prefix_value = next(iter(dotted_prefixes), "") if len(dotted_prefixes) == 1 else ""
    collection_id = _safe_collection_id(
        table_value or prefix_value or "section_1",
        "section_1",
    )
    metadata: dict[str, Any] = {
        "control_metadata": [dict(controls)] if controls else [],
        "explicit_row_segmentation": True,
        "source_row_labels": list(row_order),
    }
    if value_traces:
        metadata["value_provenance"] = value_traces
    return _Detected(
        [
            SourceCollection(
                collection_id=collection_id,
                source_path="$explicit_rows[1][*]",
                source_format="key_value",
                rows=rows,
                fields=_field_order(rows),
                metadata=metadata,
            )
        ],
        row_spans,
    )


def _detect_key_value_sections(
    text: str,
    occupied: list[tuple[int, int]],
    parser_config: StructuredParserConfig,
) -> _Detected:
    collections: list[SourceCollection] = []
    spans: list[tuple[int, int]] = []
    sequence = 1
    pending_controls: dict[str, Any] = {}
    for start, end, block in _line_blocks(text):
        if _overlaps((start, end), occupied + spans):
            continue
        row: dict[str, Any] = {}
        matched_spans: list[tuple[int, int]] = []
        value_traces: list[dict[str, Any]] = []
        for match in _KEY_VALUE.finditer(block):
            key, value = match.groups()
            if key.casefold().strip() in {"subject", "note"}:
                continue
            normalized, trace = _normalize_source_text_value(
                value,
                parser_config,
            )
            row[key.strip()] = normalized
            _append_value_trace(
                value_traces,
                row_index=0,
                field=key.strip(),
                trace=trace,
            )
            matched_spans.append((start + match.start(), start + match.end()))
        row = _strip_common_record_prefix(row)
        if len(row) < 2:
            continue
        if parser_config.enabled and _is_d_control_metadata_row(row):
            pending_controls.update(row)
            # Preserve explicit control text in instruction_text while moving
            # the same semantics into deterministic collection metadata.
            continue
        if _is_control_metadata_row(row):
            continue
        metadata: dict[str, Any] = {}
        if parser_config.enabled and pending_controls:
            metadata["control_metadata"] = [dict(pending_controls)]
        if value_traces:
            metadata["value_provenance"] = value_traces
        collections.append(
            SourceCollection(
                collection_id=_collection_id_from_table_field(
                    pending_controls if parser_config.enabled and pending_controls else row,
                    f"section_{sequence}",
                ),
                source_path=f"$section[{sequence}]",
                source_format="key_value",
                rows=[row],
                fields=_field_order([row]),
                metadata=metadata,
            )
        )
        spans.extend(matched_spans)
        pending_controls = {}
        sequence += 1
    return _Detected(collections, spans)

def _detect_equals_key_value_records(
    text: str,
    occupied: list[tuple[int, int]],
    parser_config: StructuredParserConfig,
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
    d_strong_control_context = parser_config.enabled and any(
        _is_high_confidence_d_control_signal(
            match.group(1).strip(),
            match.group(2),
        )
        for match in matches
    )
    data_items: list[
        tuple[re.Match[str], str, str, str, Any, dict[str, Any] | None]
    ] = []
    for match in matches:
        raw_key, raw_value = match.groups()
        key = raw_key.strip()
        value, trace = _normalize_source_text_value(
            raw_value,
            parser_config,
        )
        canonical = _canonical_field_name(key)
        is_control = (
            _is_d_control_metadata_field(key)
            if d_strong_control_context
            else _is_control_metadata_field(key)
        )
        if is_control:
            controls[key] = value
            if canonical in {"targettable", "table"} and isinstance(value, str):
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
        field_is_control = (
            _is_d_control_metadata_field(field)
            if d_strong_control_context
            else _is_control_metadata_field(field)
        )
        if field_is_control:
            controls[key] = value
            continue
        data_items.append(
            (match, collection_name, record_id, field, value, trace)
        )

    if not data_items:
        return _Detected([], [])
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    group_spans: dict[str, list[tuple[int, int]]] = {}
    group_traces: dict[str, list[dict[str, Any]]] = {}
    record_indexes: dict[str, dict[str, int]] = {}
    for match, collection_name, record_id, field, value, trace in data_items:
        rows = grouped.setdefault(collection_name, {})
        indexes = record_indexes.setdefault(collection_name, {})
        if record_id not in rows:
            rows[record_id] = {}
            indexes[record_id] = len(indexes)
        rows[record_id][field] = value
        _append_value_trace(
            group_traces.setdefault(collection_name, []),
            row_index=indexes[record_id],
            field=field,
            trace=trace,
        )
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
                    **(
                        {"value_provenance": group_traces[collection_name]}
                        if group_traces.get(collection_name)
                        else {}
                    ),
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




def _finalize_stage2_d_metadata(
    collections: list[SourceCollection],
    parser_config: StructuredParserConfig,
) -> None:
    if not parser_config.enabled:
        return
    row_counter = 1
    for collection in collections:
        row_ids: list[str] = []
        for _ in collection.rows:
            row_ids.append(f"SRC_ROW_{row_counter:04d}")
            row_counter += 1
        collection.metadata["row_ids"] = row_ids
        collection.metadata["structured_parser_contract"] = "stage2-d-v1"
        for trace in collection.metadata.get("value_provenance") or []:
            row_index = trace.get("row_index")
            if isinstance(row_index, int) and 0 <= row_index < len(row_ids):
                trace["row_id"] = row_ids[row_index]

def parse_source_payload(
    text: str,
    structured_parser: Mapping[str, Any] | None = None,
) -> SourcePayload:
    """Extract every structured collection without rewriting source cells.

    ``structured_parser`` is the ablatable Stage-2 D parser policy.  Omitting
    it preserves the historical parser behavior used by the frozen A-C tag.
    """
    parser_config = StructuredParserConfig.from_mapping(structured_parser)
    detected = _detect_fenced(text, parser_config)
    collections = list(detected.collections)
    spans = list(detected.spans)

    inline_json = _detect_inline_json(text, spans)
    collections.extend(inline_json.collections)
    spans.extend(inline_json.spans)

    python_literals = _detect_python_literals(text, spans)
    collections.extend(python_literals.collections)
    spans.extend(python_literals.spans)

    markdown = _detect_tables(text, spans, parser_config)
    collections.extend(markdown.collections)
    spans.extend(markdown.spans)

    numbered = _detect_numbered_key_value_records(text, spans, parser_config)
    collections.extend(numbered.collections)
    spans.extend(numbered.spans)

    bulleted = _detect_bulleted_key_value_records(text, spans, parser_config)
    collections.extend(bulleted.collections)
    spans.extend(bulleted.spans)

    delimited = _detect_delimited_blocks(text, spans, parser_config)
    collections.extend(delimited.collections)
    spans.extend(delimited.spans)

    explicit_rows = _detect_explicit_equals_rows(text, spans, parser_config)
    collections.extend(explicit_rows.collections)
    spans.extend(explicit_rows.spans)

    equals_key_values = _detect_equals_key_value_records(
        text,
        spans,
        parser_config,
    )
    collections.extend(equals_key_values.collections)
    spans.extend(equals_key_values.spans)

    key_values = _detect_key_value_sections(text, spans, parser_config)
    collections.extend(key_values.collections)
    spans.extend(key_values.spans)

    collections = _deduplicate_collection_ids(collections)
    _assign_source_reference_ids(collections)
    _finalize_stage2_d_metadata(collections, parser_config)
    instruction = _instruction_without_payload(text, spans)
    mode = "semi_structured" if collections else "free_text"
    metadata: dict[str, Any] = {
        "collection_count": len(collections),
        "row_count": sum(len(collection.rows) for collection in collections),
        "field_count": sum(len(collection.fields) for collection in collections),
        "multi_block_detected": len(collections) > 1,
        "reference_contract": "source-ids-v1",
    }
    if parser_config.enabled:
        metadata["structured_source_parser"] = parser_config.to_dict()
        metadata["structured_parser_contract"] = "stage2-d-v1"
    return SourcePayload(
        mode=mode,
        source_format=_overall_format(collections),
        collections=collections,
        instruction_text=instruction if collections else text.strip(),
        raw_text=text,
        metadata=metadata,
    )
