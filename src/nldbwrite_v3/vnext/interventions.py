from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.schema import ensure_reference_ids


PAYLOAD_VALUE = "PAYLOAD_VALUE"
OPERATION_CONTROL = "OPERATION_CONTROL"
# Compatibility umbrella retained for external imports.  Patch 3 deliberately
# classifies conflict action and target controls into distinct roles.
CONFLICT_CONTROL = "CONFLICT_CONTROL"
CONFLICT_ACTION_CONTROL = "CONFLICT_ACTION_CONTROL"
CONFLICT_TARGET_CONTROL = "CONFLICT_TARGET_CONTROL"
UPDATE_CONTROL = "UPDATE_CONTROL"
METADATA = "METADATA"


def _canonical(value: Any) -> str:
    """Loose key used only for control-field aliases, never DB identifiers."""
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def _strip_sql_identifier_quotes(value: Any) -> str:
    text = str(value).strip()
    pairs = (("[", "]"), ('"', '"'), ("`", "`"))
    changed = True
    while changed and len(text) >= 2:
        changed = False
        for left, right in pairs:
            if text.startswith(left) and text.endswith(right):
                text = text[1:-1].strip()
                changed = True
                break
    return text


def _identifier_key(value: Any) -> str:
    """SQLite identifier key: case-insensitive, punctuation-preserving."""
    return _strip_sql_identifier_quotes(value).casefold()


def _operation_alias_key(value: Any) -> str:
    text = str(value).strip().casefold()
    text = re.sub(r"[\s-]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


# A intentionally uses only high-confidence operation-control field names.
# Generic names such as ``action`` remain payload unless another semantic
# component proves that a particular source reference is instruction metadata.
_OPERATION_FIELDS = {
    "operation", "writeoperation", "operationtype",
}
_CONFLICT_ACTION_FIELDS = {
    "conflict", "conflictaction", "conflictbehavior", "conflictpolicy",
    "duplicatepolicy", "onconflict", "onduplicate",
}
_CONFLICT_TARGET_FIELDS = {
    "conflictkey", "conflicttarget", "conflicttargets", "uniquekey",
}
_UPDATE_FIELDS = {
    "update", "updates", "updatecolumns", "requestedupdatecolumns",
    "allowedupdates", "preservecolumns", "relationshipcolumnsnotupdated",
    "excludedupdatecolumns", "donotupdate", "donotupdatecolumns",
}
_METADATA_FIELDS = {
    "table", "targettable", "instruction", "requirement", "ordering",
    "processingorder", "relationshiporder", "keystatus", "registrystate",
    "newkeys", "policy", "knownconflictwitness",
}


def classify_source_field_role(field: str) -> str:
    """Classify a control alias without claiming semantic consumption."""
    canonical = _canonical(field)
    if canonical in _OPERATION_FIELDS:
        return OPERATION_CONTROL
    if canonical in _CONFLICT_ACTION_FIELDS:
        return CONFLICT_ACTION_CONTROL
    if (
        canonical in _CONFLICT_TARGET_FIELDS
        or canonical.endswith("conflictkey")
        or canonical.endswith("conflicttarget")
    ):
        return CONFLICT_TARGET_CONTROL
    if canonical in _UPDATE_FIELDS or canonical.endswith("updatecolumns"):
        return UPDATE_CONTROL
    if canonical in _METADATA_FIELDS:
        return METADATA
    return PAYLOAD_VALUE


_OPERATION_ALIASES = {
    "upsert_update": "upsert_update",
    "upsert": "upsert_update",
    "insert_or_update": "upsert_update",
    "insert_update": "upsert_update",
    "do_update": "upsert_update",
    "insert_ignore": "insert_ignore",
    "insert_or_ignore": "insert_ignore",
    "do_nothing": "insert_ignore",
    "plain_insert": "plain_insert",
    "insert": "plain_insert",
    "error_on_conflict": "plain_insert",
    "raise_on_conflict": "plain_insert",
    "fail_on_conflict": "plain_insert",
}


def _operation_from_value(value: Any) -> str | None:
    """Parse a structured control value by exact aliases only."""
    key = _operation_alias_key(value)
    return _OPERATION_ALIASES.get(key)


def control_consumed_by(
    field: str,
    value: Any,
    *,
    instruction_context: bool = False,
) -> str | None:
    """Compatibility helper for A: only typed operation controls are consumable.

    Patch 2 deliberately does *not* consume conflict/update/metadata fields here.
    B and C must first resolve their semantics and then emit concrete
    ``consumed_control_refs``.  This keeps V1 isolated from V2/V3.
    """
    del instruction_context
    if classify_source_field_role(field) != OPERATION_CONTROL:
        return None
    return (
        "instruction_semantics.operation"
        if _operation_from_value(value) is not None
        else None
    )


def row_has_instruction_context(row: Mapping[str, Any]) -> bool:
    """Return true only for a high-confidence typed operation control."""
    return any(
        classify_source_field_role(str(field)) == OPERATION_CONTROL
        and _operation_from_value(value) is not None
        for field, value in row.items()
    )


@dataclass(frozen=True, slots=True)
class Stage2InterventionConfig:
    control_field_roles: bool = False
    explicit_conflict_preservation: bool = False
    update_column_consistency: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "Stage2InterventionConfig":
        value = value or {}
        return cls(
            control_field_roles=bool(value.get("control_field_roles", False)),
            explicit_conflict_preservation=bool(value.get("explicit_conflict_preservation", False)),
            update_column_consistency=bool(value.get("update_column_consistency", False)),
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "control_field_roles": self.control_field_roles,
            "explicit_conflict_preservation": self.explicit_conflict_preservation,
            "update_column_consistency": self.update_column_consistency,
        }


def _iter_control_objects(collection: SourceCollection) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    raw = collection.metadata.get("control_metadata") or []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            output.append(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(raw)
    for row in collection.rows:
        controls = {
            str(field): value
            for field, value in row.items()
            if classify_source_field_role(str(field)) != PAYLOAD_VALUE
        }
        if controls:
            output.append(controls)
    return output


def _row_control_entries(collection: SourceCollection):
    for row_index, row in enumerate(collection.rows):
        for field, value in row.items():
            role = classify_source_field_role(str(field))
            if role == PAYLOAD_VALUE:
                continue
            yield row_index, str(field), value, role


def _control_ref(
    collection: SourceCollection,
    row_index: int,
    field: str,
    role: str,
    consumed_by: str,
    *,
    resolved_value: Any = None,
) -> dict[str, Any]:
    output = {
        "source_collection": collection.collection_id,
        "source_collection_ref": collection.reference_id,
        "source_row_index": row_index,
        "source_field": field,
        "source_field_ref": collection.field_ids.get(field),
        "role": role,
        "consumed_by": consumed_by,
    }
    if resolved_value is not None:
        output["resolved_value"] = deepcopy(resolved_value)
    return output


def _append_consumed_refs(plan: dict[str, Any], refs: list[dict[str, Any]]) -> None:
    if not refs:
        return
    existing = plan.setdefault("consumed_control_refs", [])
    if not isinstance(existing, list):
        existing = []
        plan["consumed_control_refs"] = existing
    seen = {
        (
            str(item.get("source_collection") or ""),
            item.get("source_row_index"),
            str(item.get("source_field") or ""),
            str(item.get("consumed_by") or ""),
        )
        for item in existing
        if isinstance(item, dict)
    }
    for item in refs:
        key = (
            str(item.get("source_collection") or ""),
            item.get("source_row_index"),
            str(item.get("source_field") or ""),
            str(item.get("consumed_by") or ""),
        )
        if key not in seen:
            existing.append(item)
            seen.add(key)


def _typed_operation_signal(
    payload: SourcePayload,
    collection: SourceCollection,
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Return a unique operation semantic and provenance refs.

    Operation-field refs are owned by A. Conflict-policy/action refs are owned
    by B.  If local controls contradict one another, deterministic rewriting is
    disabled rather than choosing one.
    """
    signals: list[tuple[str, str, int | None, str | None, str | None]] = []
    for row_index, field, value, role in _row_control_entries(collection):
        parsed = _operation_from_value(value)
        if parsed and role in {OPERATION_CONTROL, CONFLICT_ACTION_CONTROL}:
            signals.append((parsed, role, row_index, field, str(value)))
    # Collection-level control metadata can establish semantics but has no row
    # provenance record to suppress in materialization.
    for controls in _iter_control_objects(collection):
        for field, value in controls.items():
            role = classify_source_field_role(str(field))
            parsed = _operation_from_value(value)
            if parsed and role in {OPERATION_CONTROL, CONFLICT_ACTION_CONTROL}:
                signals.append((parsed, role, None, None, str(value)))

    semantics = {item[0] for item in signals}
    if len(semantics) > 1:
        return None, [], []
    semantic = next(iter(semantics), None)

    # Raw-text fallback is only safe for a single semi-structured collection.
    if semantic is None and len(payload.collections) == 1:
        semantic = _operation_from_request_text(payload.raw_text)
    if semantic is None:
        return None, [], []

    operation_refs: list[dict[str, Any]] = []
    conflict_refs: list[dict[str, Any]] = []
    for parsed, role, row_index, field, _ in signals:
        if parsed != semantic or row_index is None or field is None:
            continue
        if role == OPERATION_CONTROL:
            operation_refs.append(_control_ref(
                collection, row_index, field, role,
                "instruction_semantics.operation", resolved_value=semantic,
            ))
        elif role == CONFLICT_ACTION_CONTROL:
            conflict_refs.append(_control_ref(
                collection, row_index, field, role,
                "explicit_conflict_preservation.action", resolved_value=semantic,
            ))
    return semantic, operation_refs, conflict_refs


def _split_names(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = re.sub(r"[\[\](){}]", " ", value)
        return [
            item.strip(" `\"'")
            for item in re.split(r"[,;|]", cleaned)
            if item.strip(" `\"'")
        ]
    return []


def _column_candidates_by_name(table: dict[str, Any]) -> dict[str, list[str]]:
    """Map exact identifier keys to all matching column IDs without overwrite."""
    output: dict[str, list[str]] = {}
    for column in table.get("columns", []):
        if not isinstance(column, dict) or not column.get("name") or not column.get("column_id"):
            continue
        output.setdefault(_identifier_key(column.get("name")), []).append(str(column.get("column_id")))
    return output


def _exact_column_ids(
    names: list[str],
    table: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    by_name = _column_candidates_by_name(table)
    resolved: list[str] = []
    unresolved: list[str] = []
    ambiguous: list[str] = []
    for name in names:
        if not name:
            continue
        candidates = by_name.get(_identifier_key(name), [])
        if len(candidates) == 1:
            if candidates[0] not in resolved:
                resolved.append(candidates[0])
        elif len(candidates) > 1:
            ambiguous.append(name)
        else:
            unresolved.append(name)
    return resolved, unresolved, ambiguous


def _constraint_column_ids(
    constraint: dict[str, Any],
    table: dict[str, Any],
) -> set[str] | None:
    names = [str(value) for value in constraint.get("columns") or []]
    ids, unresolved, ambiguous = _exact_column_ids(names, table)
    if unresolved or ambiguous or len(ids) != len(names):
        return None
    return set(ids)


def _update_control_resolution(
    collection: SourceCollection,
    table: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[dict[str, Any]]]:
    requested_names: list[str] = []
    excluded_names: list[str] = []
    requested_refs: list[tuple[int, str]] = []
    excluded_refs: list[tuple[int, str]] = []

    for row_index, field, value, role in _row_control_entries(collection):
        if role != UPDATE_CONTROL:
            continue
        canonical = _canonical(field)
        is_excluded = (
            "preserve" in canonical
            or "notupdated" in canonical
            or "donotupdate" in canonical
            or "excluded" in canonical
        )
        names = _split_names(value)
        if is_excluded:
            excluded_names.extend(names)
            excluded_refs.append((row_index, field))
        else:
            requested_names.extend(names)
            requested_refs.append((row_index, field))

    # Metadata controls can contribute semantics but do not need row-level
    # provenance suppression.
    for controls in _iter_control_objects(collection):
        for field, value in controls.items():
            if classify_source_field_role(str(field)) != UPDATE_CONTROL:
                continue
            canonical = _canonical(field)
            target = excluded_names if (
                "preserve" in canonical
                or "notupdated" in canonical
                or "donotupdate" in canonical
                or "excluded" in canonical
            ) else requested_names
            target.extend(_split_names(value))

    requested, unresolved_requested, ambiguous_requested = _exact_column_ids(requested_names, table)
    excluded, unresolved_excluded, ambiguous_excluded = _exact_column_ids(excluded_names, table)
    overlap = set(requested) & set(excluded)
    excluded_keys = {_identifier_key(value) for value in excluded_names}
    contradiction_names = sorted({
        name
        for name in requested_names
        if _identifier_key(name) in excluded_keys
    })
    if overlap and not contradiction_names:
        by_id = {
            str(column.get("column_id")): str(column.get("name"))
            for column in table.get("columns", [])
            if isinstance(column, dict)
        }
        contradiction_names = sorted(by_id.get(item, item) for item in overlap)

    unresolved = list(dict.fromkeys(unresolved_requested + unresolved_excluded))
    ambiguous = list(dict.fromkeys(ambiguous_requested + ambiguous_excluded))
    refs: list[dict[str, Any]] = []
    if not unresolved and not ambiguous and not contradiction_names:
        for row_index, field in requested_refs:
            refs.append(_control_ref(
                collection, row_index, field, UPDATE_CONTROL,
                "update_column_consistency.requested", resolved_value=requested,
            ))
        for row_index, field in excluded_refs:
            refs.append(_control_ref(
                collection, row_index, field, UPDATE_CONTROL,
                "update_column_consistency.excluded", resolved_value=excluded,
            ))
    return requested, excluded, unresolved, ambiguous, contradiction_names, refs


def _conflict_target_resolution(
    collection: SourceCollection,
    table: dict[str, Any],
) -> tuple[str | None, list[str], list[str], list[dict[str, Any]]]:
    names: list[str] = []
    row_refs: list[tuple[int, str]] = []
    for row_index, field, value, role in _row_control_entries(collection):
        canonical = _canonical(field)
        if role == CONFLICT_TARGET_CONTROL:
            names.extend(_split_names(value))
            row_refs.append((row_index, field))
    for controls in _iter_control_objects(collection):
        for field, value in controls.items():
            canonical = _canonical(field)
            if classify_source_field_role(str(field)) == CONFLICT_TARGET_CONTROL:
                names.extend(_split_names(value))
    names = list(dict.fromkeys(names))
    if not names:
        return None, [], [], []
    column_ids, unresolved, ambiguous = _exact_column_ids(names, table)
    if unresolved or ambiguous:
        return None, unresolved, ambiguous, []
    column_id_set = set(column_ids)
    matches = []
    for constraint in table.get("unique_indexes", []):
        if not isinstance(constraint, dict) or not constraint.get("constraint_id"):
            continue
        constraint_ids = _constraint_column_ids(constraint, table)
        if constraint_ids and constraint_ids == column_id_set:
            matches.append(str(constraint["constraint_id"]))
    if len(matches) != 1:
        return None, names, [], []
    target_id = matches[0]
    refs = [
        _control_ref(
            collection, row_index, field, CONFLICT_TARGET_CONTROL,
            "explicit_conflict_preservation.target", resolved_value=target_id,
        )
        for row_index, field in row_refs
    ]
    return target_id, [], [], refs


def _assignment_lhs_before_quote(value: str, quote_index: int) -> str | None:
    """Return the simple assignment LHS when a quote starts directly after ``=``."""
    prefix = value[:quote_index]
    match = re.search(
        r'(?is)(?P<lhs>(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*))\s*=\s*$',
        prefix,
    )
    if not match:
        return None
    return _strip_sql_identifier_quotes(match.group("lhs"))


def _quoted_control_value_context(value: str, quote_index: int) -> bool:
    """Return true when a quoted span is the explicit value of a known control."""
    prefix = value[:quote_index]
    match = re.search(
        r"(?is)(?P<field>[A-Za-z_][A-Za-z0-9_ -]{0,50})\s*[:=]\s*$",
        prefix,
    )
    if not match:
        return False
    return classify_source_field_role(match.group("field")) != PAYLOAD_VALUE


def _quoted_identifier_context(value: str, start: int, end: int, quote: str) -> bool:
    """Preserve quoted SQL identifiers while masking quoted payload content."""
    if quote not in {'"', "`"}:
        return False
    content = value[start + 1:end]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", content):
        return False

    prefix = value[:start]
    suffix = value[end + 1:]
    if re.search(r"\.\s*$", prefix):
        return True
    if re.match(r"\s*=", suffix):
        return True
    if re.search(r"(?is)\bon\s+conflict\s*\([^)]*$", prefix):
        return True
    return False


def _mask_payload_literals(value: str) -> str:
    """Mask payload literal spans without destroying quoted SQL/control identifiers.

    Patch 4 applies one instruction/payload boundary to operation, conflict-target,
    and update-column extraction. Quoted RHS values of ordinary payload assignments
    are masked. Explicit quoted control values and quoted SQL identifiers remain
    visible to the deterministic control parsers.
    """
    text = str(value)
    output = list(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char not in {"'", '"', "`"}:
            index += 1
            continue

        quote = char
        end = index + 1
        escape = False
        while end < len(text):
            current = text[end]
            if escape:
                escape = False
            elif current == "\\":
                escape = True
            elif current == quote:
                break
            end += 1
        if end >= len(text):
            for pos in range(index + 1, len(text)):
                output[pos] = " "
            break

        lhs = _assignment_lhs_before_quote(text, index)
        payload_assignment = (
            lhs is not None
            and classify_source_field_role(lhs) == PAYLOAD_VALUE
        )
        preserve = (
            (lhs is not None and not payload_assignment)
            or _quoted_control_value_context(text, index)
            or _quoted_identifier_context(text, index, end, quote)
        )
        if not preserve:
            for pos in range(index + 1, end):
                output[pos] = " "
        index = end + 1
    return "".join(output)


def _operation_from_request_text(request: str) -> str | None:
    """Restore only explicit high-confidence conflict/operation instructions."""
    text = " ".join(_mask_payload_literals(str(request)).split()).casefold()

    # SQL-like conflict syntax is the highest-confidence signal.
    on_conflict = re.search(r"\bon\s+conflict(?:\s*\([^)]*\))?\s+do\s+(?P<action>nothing|update)\b", text)
    if on_conflict:
        return "insert_ignore" if on_conflict.group("action") == "nothing" else "upsert_update"
    if re.search(r"\binsert\s+or\s+ignore\b", text):
        return "insert_ignore"
    if re.search(r"\binsert\s+or\s+update\b", text):
        return "upsert_update"

    # Explicit typed assignment, e.g. ``operation: upsert_update``.
    match = re.search(
        r"\b(?:operation|write[_ -]?operation|operation[_ -]?type)\s*[:=]\s*"
        r"(?P<value>[a-z][a-z0-9_ -]{0,40})",
        text,
    )
    if match:
        token = re.split(r"[.;,]", match.group("value"), maxsplit=1)[0].strip()
        parsed = _operation_from_value(token)
        if parsed:
            return parsed

    # Natural-language restoration requires an explicit conflict/duplicate cue
    # and a nearby action. Bare words such as payload value 'upsert' are ignored.
    conflict_cue = r"(?:conflict|duplicate|already\s+exists?|existing\s+(?:row|record|key))"
    if re.search(rf"\b(?:if|when|on)\b[^.;]{{0,80}}\b{conflict_cue}\b[^.;]{{0,80}}\b(?:ignore|skip|keep\s+(?:the\s+)?existing|leave[^.;]{{0,30}}unchanged)\b", text):
        return "insert_ignore"
    if re.search(rf"\b(?:if|when|on)\b[^.;]{{0,80}}\b{conflict_cue}\b[^.;]{{0,80}}\b(?:update|overwrite|replace|merge)\b", text):
        return "upsert_update"
    if re.search(rf"\b(?:if|when|on)\b[^.;]{{0,80}}\b{conflict_cue}\b[^.;]{{0,80}}\b(?:error|fail|raise|reject)\b", text):
        return "plain_insert"
    if re.search(r"\bplain[_ -]?insert\b", text):
        return "plain_insert"
    return None


def _request_conflict_target_id(request: str, table: dict[str, Any]) -> tuple[str | None, list[str], list[str]]:
    request = _mask_payload_literals(request)
    names: list[str] = []
    patterns = (
        r"(?is)on\s+conflict\s*\((?P<value>[^)]+)\)",
        r"(?is)conflict[_ -]?target\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)conflict[_ -]?key\s*[:=]\s*(?P<value>[^.;\r\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, request)
        if match:
            names = _split_names(match.group("value"))
            break
    if not names:
        return None, [], []
    column_ids, unresolved, ambiguous = _exact_column_ids(names, table)
    if unresolved or ambiguous:
        return None, unresolved, ambiguous
    target_set = set(column_ids)
    matches = []
    for constraint in table.get("unique_indexes", []):
        if not isinstance(constraint, dict) or not constraint.get("constraint_id"):
            continue
        ids = _constraint_column_ids(constraint, table)
        if ids and ids == target_set:
            matches.append(str(constraint["constraint_id"]))
    return (matches[0] if len(matches) == 1 else None), ([] if len(matches) == 1 else names), []


def _split_top_level_commas(value: str) -> list[str]:
    output: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escape = False
    for index, char in enumerate(value):
        if quote is not None:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            output.append(value[start:index].strip())
            start = index + 1
    output.append(value[start:].strip())
    return [item for item in output if item]


def _identifier_name(value: str) -> str | None:
    text = value.strip().strip("`\"[] ")
    if "." in text:
        text = text.rsplit(".", 1)[-1].strip().strip("`\"[] ")
    return text if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) else None


def _request_update_names(request: str) -> tuple[list[str], list[str]]:
    """Parse explicit update controls; SET clauses use assignment LHS only."""
    request = _mask_payload_literals(request)
    cleaned = re.sub(r"[`*]", "", request)
    requested: list[str] = []
    excluded: list[str] = []

    simple_patterns = (
        r"(?is)allowed\s+updates?\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)update[_ -]?columns?\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)update\s+only\s+(?:the\s+)?(?:listed\s+)?(?:columns?\s+)?(?P<value>[^.;\r\n]+)",
    )
    for pattern in simple_patterns:
        match = re.search(pattern, cleaned)
        if match:
            requested.extend(_split_names(match.group("value")))
            break

    set_match = re.search(r"(?is)do\s+update\s+set\s+(?P<value>[^;\r\n]+)", cleaned)
    if set_match:
        set_value = set_match.group("value")
        sentence_boundary = re.search(r"\.\s+(?=[A-Z])", set_value)
        if sentence_boundary:
            set_value = set_value[: sentence_boundary.start()]
        for assignment in _split_top_level_commas(set_value):
            lhs = assignment.split("=", 1)[0] if "=" in assignment else assignment
            name = _identifier_name(lhs)
            if name:
                requested.append(name)
            else:
                # Preserve unparseable explicit tokens as unresolved names.
                token = lhs.strip()
                if token:
                    requested.append(token)

    exclusion_patterns = (
        r"(?is)relationship[_ -]?columns?[_ -]?not[_ -]?updated\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)excluded[_ -]?update[_ -]?columns?\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)do\s+not\s+update\s+(?P<value>[^.;\r\n]+)",
        r"(?is)preserve\s+(?:the\s+)?(?:columns?\s+)?(?P<value>[^.;\r\n]+)",
    )
    for pattern in exclusion_patterns:
        match = re.search(pattern, cleaned)
        if match:
            excluded.extend(_split_names(match.group("value")))
            break

    return list(dict.fromkeys(requested)), list(dict.fromkeys(excluded))


def _request_update_resolution(
    request: str,
    table: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    requested_names, excluded_names = _request_update_names(request)
    requested, unresolved_requested, ambiguous_requested = _exact_column_ids(requested_names, table)
    excluded, unresolved_excluded, ambiguous_excluded = _exact_column_ids(excluded_names, table)
    excluded_keys = {_identifier_key(item) for item in excluded_names}
    contradictions = sorted({
        name for name in requested_names
        if _identifier_key(name) in excluded_keys
    })
    unresolved = list(dict.fromkeys(unresolved_requested + unresolved_excluded))
    ambiguous = list(dict.fromkeys(ambiguous_requested + ambiguous_excluded))
    return requested, excluded, unresolved, ambiguous, contradictions


def _table_anchor(request: str, table_name: str) -> list[int]:
    escaped = re.escape(str(table_name))
    patterns = (
        rf"(?i)\bfor\s+[`\"']?{escaped}(?!\w)",
        rf"(?i)\btable\s+[`\"']?{escaped}(?!\w)",
        rf"(?i)\binto\s+[`\"']?{escaped}(?!\w)",
    )
    positions: list[int] = []
    for pattern in patterns:
        positions.extend(match.start() for match in re.finditer(pattern, request))
    if positions:
        return sorted(set(positions))
    fallback = re.compile(rf"(?i)(?<!\w){escaped}(?!\w)")
    matches = [match.start() for match in fallback.finditer(request)]
    return matches if len(matches) == 1 else []


def _scoped_request_segments(
    request: str,
    groups: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
) -> dict[int, str | None]:
    valid = [
        (index, group, tables.get(str(group.get("table_id") or "")))
        for index, group in enumerate(groups)
        if isinstance(group, dict)
    ]
    valid = [(index, group, table) for index, group, table in valid if table is not None]
    if len(valid) <= 1:
        return {index: request for index, _, _ in valid}

    table_ids = [str(group.get("table_id") or "") for _, group, _ in valid]
    if len(set(table_ids)) != len(table_ids):
        return {index: None for index, _, _ in valid}

    anchors: list[tuple[int, int]] = []
    for index, _, table in valid:
        positions = _table_anchor(request, str(table.get("name") or ""))
        if len(positions) != 1:
            return {idx: None for idx, _, _ in valid}
        anchors.append((positions[0], index))
    anchors.sort()
    output: dict[int, str | None] = {}
    for offset, (start, index) in enumerate(anchors):
        end = anchors[offset + 1][0] if offset + 1 < len(anchors) else len(request)
        output[index] = request[start:end]
    return output


def apply_free_text_reference_interventions(
    mapping_plan: dict[str, Any],
    request: str,
    profile: dict[str, Any],
    config: Stage2InterventionConfig,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Apply B-C preservation to group-scoped free-text reference plans."""
    plan = deepcopy(mapping_plan)
    if not (config.explicit_conflict_preservation or config.update_column_consistency):
        return plan, []
    ensure_reference_ids(profile)
    diagnostics: list[Diagnostic] = []
    tables = {
        str(table.get("table_id")): table
        for table in profile.get("tables", [])
        if isinstance(table, dict) and table.get("table_id")
    }
    groups = plan.get("write_groups") or []
    if not isinstance(groups, list):
        return plan, []
    segments = _scoped_request_segments(request, groups, tables)

    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or f"g{index + 1}")
        path = f"/write_groups/{index}"
        table = tables.get(str(group.get("table_id") or ""))
        segment = segments.get(index)
        if table is None or not segment:
            continue
        trace = group.setdefault("stage2_intervention_trace", {})
        if not isinstance(trace, dict):
            trace = {}
            group["stage2_intervention_trace"] = trace
        trace["request_scope"] = "group_local" if len(groups) > 1 else "request"
        group_diag_start = len(diagnostics)

        explicit_operation = _operation_from_request_text(segment)
        if config.explicit_conflict_preservation and explicit_operation:
            previous = str(group.get("write_semantics") or "")
            if previous != explicit_operation:
                group["write_semantics"] = explicit_operation
                diagnostics.append(Diagnostic(
                    "EXPLICIT_CONFLICT_SEMANTICS_DROPPED",
                    "The generated plan dropped or contradicted explicit group-scoped free-text conflict semantics; the typed semantic was deterministically restored before materialization.",
                    severity="warning", path=f"{path}/write_semantics", group_id=group_id,
                    details={
                        "previous": previous,
                        "restored": explicit_operation,
                        "deterministically_restored": True,
                    },
                ))
            trace["explicit_operation"] = explicit_operation
            if explicit_operation == "plain_insert":
                group["conflict_target_id"] = None
                group["update_column_ids"] = []
            else:
                target_id, unresolved, ambiguous = _request_conflict_target_id(segment, table)
                if ambiguous:
                    diagnostics.append(Diagnostic(
                        "AMBIGUOUS_IDENTIFIER",
                        "Explicit group-scoped free-text conflict target matches more than one exact identifier candidate.",
                        path=f"{path}/conflict_target_id", group_id=group_id,
                        details={"ambiguous_identifier_names": ambiguous},
                    ))
                elif unresolved:
                    diagnostics.append(Diagnostic(
                        "EXPLICIT_CONFLICT_SEMANTICS_DROPPED",
                        "Explicit group-scoped free-text conflict target cannot be resolved exactly to one unique constraint.",
                        path=f"{path}/conflict_target_id", group_id=group_id,
                        details={"unresolved_target_names": unresolved},
                    ))
                elif target_id:
                    group["conflict_target_id"] = target_id
                    trace["requested_conflict_target_id"] = target_id
                if explicit_operation == "insert_ignore":
                    group["update_column_ids"] = []

        if config.update_column_consistency and str(group.get("write_semantics") or "") == "upsert_update":
            requested, excluded, unresolved, ambiguous, contradictions = _request_update_resolution(segment, table)
            trace["requested_update_column_ids"] = requested
            trace["excluded_update_column_ids"] = excluded
            current = [str(value) for value in group.get("update_column_ids") or []]
            trace["planned_update_column_ids_before"] = current
            if ambiguous:
                diagnostics.append(Diagnostic(
                    "AMBIGUOUS_IDENTIFIER",
                    "Explicit free-text update control matches more than one exact identifier candidate.",
                    path=f"{path}/update_column_ids", group_id=group_id,
                    details={"ambiguous_identifier_names": ambiguous},
                ))
            elif contradictions:
                diagnostics.append(Diagnostic(
                    "CONTRADICTORY_UPDATE_CONTROL",
                    "The free-text request both requires and excludes the same update column(s).",
                    path=f"{path}/update_column_ids", group_id=group_id,
                    details={"contradictory_column_names": contradictions},
                ))
            elif unresolved:
                diagnostics.append(Diagnostic(
                    "REQUIRED_UPDATE_COLUMNS_UNRESOLVED",
                    "Explicit free-text update columns cannot be resolved exactly.",
                    path=f"{path}/update_column_ids", group_id=group_id,
                    details={"unresolved_column_names": unresolved},
                ))
            elif requested:
                if current != requested:
                    group["update_column_ids"] = list(requested)
                    diagnostics.append(Diagnostic(
                        "REQUIRED_UPDATE_COLUMNS_DROPPED",
                        "The generated plan dropped or expanded explicit update columns; the exact requested set was deterministically restored before compilation.",
                        severity="warning", path=f"{path}/update_column_ids", group_id=group_id,
                        details={
                            "previous": current,
                            "restored": requested,
                            "missing": [x for x in requested if x not in current],
                            "extras_removed": [x for x in current if x not in set(requested)],
                            "deterministically_restored": True,
                        },
                    ))
            elif excluded:
                filtered = [item for item in current if item not in set(excluded)]
                if filtered != current:
                    group["update_column_ids"] = filtered
                    diagnostics.append(Diagnostic(
                        "EXCLUDED_UPDATE_COLUMNS_REMOVED",
                        "Explicitly excluded update columns were removed from the group-local update set.",
                        severity="warning", path=f"{path}/update_column_ids", group_id=group_id,
                        details={"previous": current, "preserved": filtered, "excluded": excluded},
                    ))
            trace["materialized_update_column_ids"] = [str(value) for value in group.get("update_column_ids") or []]

        group_diagnostics = [
            item.error_code
            for item in diagnostics[group_diag_start:]
            if item.group_id == group_id
        ]
        if group_diagnostics:
            trace["diagnostic_codes"] = list(dict.fromkeys(group_diagnostics))
    return plan, diagnostics


def apply_reference_interventions(
    mapping_plan: dict[str, Any],
    payload: SourcePayload,
    profile: dict[str, Any],
    config: Stage2InterventionConfig,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Apply isolated A-C preservation in reference-ID space."""
    plan = deepcopy(mapping_plan)
    if not (
        config.control_field_roles
        or config.explicit_conflict_preservation
        or config.update_column_consistency
    ):
        return plan, []
    ensure_reference_ids(profile)
    diagnostics: list[Diagnostic] = []
    tables = {
        str(table.get("table_id")): table
        for table in profile.get("tables", [])
        if isinstance(table, dict) and table.get("table_id")
    }
    collections: dict[str, SourceCollection] = {}
    for collection in payload.collections:
        collections[collection.collection_id] = collection
        if collection.reference_id:
            collections[collection.reference_id] = collection

    for index, group in enumerate(plan.get("target_groups") or []):
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or f"g{index + 1}")
        path = f"/target_groups/{index}"
        table = tables.get(str(group.get("table_id") or ""))
        collection = collections.get(str(group.get("source_collection_id") or ""))
        if table is None or collection is None:
            continue

        explicit_operation, operation_refs, conflict_action_refs = _typed_operation_signal(payload, collection)
        if config.control_field_roles:
            _append_consumed_refs(plan, operation_refs)

        trace = group.setdefault("stage2_intervention_trace", {})
        if not isinstance(trace, dict):
            trace = {}
            group["stage2_intervention_trace"] = trace
        group_diag_start = len(diagnostics)

        if config.explicit_conflict_preservation and explicit_operation:
            previous = str(group.get("write_semantics") or "")
            if previous != explicit_operation:
                group["write_semantics"] = explicit_operation
                diagnostics.append(Diagnostic(
                    "EXPLICIT_CONFLICT_SEMANTICS_DROPPED",
                    "The generated plan dropped or contradicted explicit conflict semantics; the typed semantic was deterministically restored before materialization.",
                    severity="warning",
                    path=f"{path}/write_semantics",
                    group_id=group_id,
                    details={
                        "previous": previous,
                        "restored": explicit_operation,
                        "deterministically_restored": True,
                    },
                ))
            trace["explicit_operation"] = explicit_operation
            _append_consumed_refs(plan, conflict_action_refs)

            if explicit_operation == "plain_insert":
                group["conflict_target_id"] = None
                group["update_column_ids"] = []
            elif explicit_operation in {"insert_ignore", "upsert_update"}:
                explicit_target, unresolved_target, ambiguous_target, target_refs = _conflict_target_resolution(collection, table)
                if ambiguous_target:
                    diagnostics.append(Diagnostic(
                        "AMBIGUOUS_IDENTIFIER",
                        "The request contains a conflict target matching more than one exact identifier candidate.",
                        path=f"{path}/conflict_target_id",
                        group_id=group_id,
                        details={"ambiguous_identifier_names": ambiguous_target},
                    ))
                elif unresolved_target:
                    diagnostics.append(Diagnostic(
                        "EXPLICIT_CONFLICT_SEMANTICS_DROPPED",
                        "The request contains an explicit conflict target that cannot be resolved exactly to one enumerated unique constraint.",
                        path=f"{path}/conflict_target_id",
                        group_id=group_id,
                        details={"unresolved_target_names": unresolved_target},
                    ))
                elif explicit_target:
                    old_target = group.get("conflict_target_id")
                    group["conflict_target_id"] = explicit_target
                    trace["requested_conflict_target_id"] = explicit_target
                    _append_consumed_refs(plan, target_refs)
                    if old_target != explicit_target:
                        diagnostics.append(Diagnostic(
                            "EXPLICIT_CONFLICT_TARGET_PRESERVED",
                            "Explicit conflict target was deterministically preserved.",
                            severity="warning",
                            path=f"{path}/conflict_target_id",
                            group_id=group_id,
                            details={"previous": old_target, "preserved": explicit_target},
                        ))
                if explicit_operation == "insert_ignore":
                    group["update_column_ids"] = []

        if config.update_column_consistency and str(group.get("write_semantics") or "") == "upsert_update":
            requested, excluded, unresolved, ambiguous, contradictions, update_refs = _update_control_resolution(collection, table)
            trace["requested_update_column_ids"] = requested
            trace["excluded_update_column_ids"] = excluded
            current = [str(value) for value in group.get("update_column_ids") or []]
            trace["planned_update_column_ids_before"] = current
            if ambiguous:
                diagnostics.append(Diagnostic(
                    "AMBIGUOUS_IDENTIFIER",
                    "Explicit update control matches more than one exact identifier candidate.",
                    path=f"{path}/update_column_ids",
                    group_id=group_id,
                    details={"ambiguous_identifier_names": ambiguous},
                ))
            elif contradictions:
                diagnostics.append(Diagnostic(
                    "CONTRADICTORY_UPDATE_CONTROL",
                    "The request both requires and excludes the same update column(s).",
                    path=f"{path}/update_column_ids",
                    group_id=group_id,
                    details={"contradictory_column_names": contradictions},
                ))
            elif unresolved:
                diagnostics.append(Diagnostic(
                    "REQUIRED_UPDATE_COLUMNS_UNRESOLVED",
                    "Explicit update-column controls contain names that cannot be resolved exactly.",
                    path=f"{path}/update_column_ids",
                    group_id=group_id,
                    details={"unresolved_column_names": unresolved},
                ))
            else:
                _append_consumed_refs(plan, update_refs)
                if requested:
                    if current != requested:
                        group["update_column_ids"] = list(requested)
                        diagnostics.append(Diagnostic(
                            "REQUIRED_UPDATE_COLUMNS_DROPPED",
                            "The generated plan dropped or expanded explicit requested update columns; the exact requested set was deterministically restored before compilation.",
                            severity="warning",
                            path=f"{path}/update_column_ids",
                            group_id=group_id,
                            details={
                                "previous": current,
                                "restored": requested,
                                "missing": [column_id for column_id in requested if column_id not in current],
                                "forbidden": [column_id for column_id in current if column_id in set(excluded)],
                                "extras_removed": [column_id for column_id in current if column_id not in set(requested)],
                                "deterministically_restored": True,
                            },
                        ))
                elif excluded:
                    filtered = [column_id for column_id in current if column_id not in set(excluded)]
                    if filtered != current:
                        group["update_column_ids"] = filtered
                        diagnostics.append(Diagnostic(
                            "EXCLUDED_UPDATE_COLUMNS_REMOVED",
                            "Explicitly excluded relationship/update columns were removed from the update set.",
                            severity="warning",
                            path=f"{path}/update_column_ids",
                            group_id=group_id,
                            details={"previous": current, "preserved": filtered, "excluded": excluded},
                        ))
            trace["materialized_update_column_ids"] = [str(value) for value in group.get("update_column_ids") or []]

        group_diagnostics = [
            item.error_code
            for item in diagnostics[group_diag_start:]
            if item.group_id == group_id
        ]
        if group_diagnostics:
            trace["diagnostic_codes"] = list(dict.fromkeys(group_diagnostics))
    return plan, diagnostics
