from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.schema import (
    column_reference_map,
    constraint_reference_map,
    ensure_reference_ids,
    table_reference_map,
)

from .grounding import collection_grounding


WRITE_SEMANTICS = {
    "plain_insert",
    "insert_ignore",
    "upsert_update",
    "needs_clarification",
}

_CONFLICT_CUE = re.compile(
    r"\b(?:duplicate|already\s+exists?|conflict|existing\s+"
    r"(?:row|record|entry|id|key)|same\s+(?:id|key))\b",
    re.IGNORECASE,
)
_VAGUE_CONFLICT_POLICY = re.compile(
    r"\b(?:appropriat(?:e|ely)|suitable|whichever|whatever|"
    r"as\s+(?:needed|appropriate)|best\s+(?:policy|way)|"
    r"handle|deal\s+with|choose|decide)\b",
    re.IGNORECASE,
)
_EXPLICIT_CONFLICT_POLICY = re.compile(
    r"\b(?:ignore|skip|do\s+nothing|leave\s+(?:it|the\s+\w+)\s+unchanged|"
    r"keep\s+(?:the\s+)?existing|do\s+not\s+(?:update|overwrite|replace)|"
    r"update|overwrite|replace|merge|fail|error|reject|raise)\b",
    re.IGNORECASE,
)


def _canonical_identifier(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def _primary_constraint_id(
    table_profile: dict[str, Any],
) -> str | None:
    constraints = [
        constraint
        for constraint in table_profile.get("unique_indexes", [])
        if isinstance(constraint, dict)
        and constraint.get("constraint_id")
        and constraint.get("columns")
    ]
    constraints.sort(
        key=lambda item: (
            0 if item.get("is_primary_key") else 1,
            len(item.get("columns") or []),
            str(item.get("constraint_id")),
        )
    )
    return (
        str(constraints[0]["constraint_id"])
        if constraints
        else None
    )


def _control_column_ids(
    collection: SourceCollection,
    table_profile: dict[str, Any],
) -> tuple[list[str], list[str]]:
    columns = {
        _canonical_identifier(column.get("name")): str(
            column.get("column_id") or ""
        )
        for column in table_profile.get("columns", [])
        if isinstance(column, dict)
        and column.get("name")
        and column.get("column_id")
    }
    updates: list[str] = []
    preserves: list[str] = []

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                canonical = _canonical_identifier(key)
                if (
                    canonical == "columns"
                    or canonical.endswith("updatecolumns")
                ) and isinstance(nested, (list, str)):
                    target = (
                        preserves
                        if "preserve" in parent_key
                        else updates
                    )
                    names = (
                        nested
                        if isinstance(nested, list)
                        else nested.split(",")
                    )
                    for name in names:
                        column_id = columns.get(
                            _canonical_identifier(name)
                        )
                        if column_id and column_id not in target:
                            target.append(column_id)
                elif canonical == "preservecolumns" and isinstance(
                    nested,
                    list,
                ):
                    for name in nested:
                        column_id = columns.get(
                            _canonical_identifier(name)
                        )
                        if column_id and column_id not in preserves:
                            preserves.append(column_id)
                visit(nested, canonical)
        elif isinstance(value, list):
            for nested in value:
                visit(nested, parent_key)

    visit(collection.metadata.get("control_metadata") or [])
    return (
        [column_id for column_id in updates if column_id not in preserves],
        preserves,
    )


def _explicit_update_column_ids(
    request: str,
    table_profile: dict[str, Any],
) -> list[str]:
    """Extract only exact schema identifiers from explicit update-only text."""
    request = re.sub(r"[`*]", "", request)
    columns = [
        column
        for column in table_profile.get("columns", [])
        if isinstance(column, dict)
        and column.get("name")
        and column.get("column_id")
    ]
    table_name = str(table_profile.get("name") or "")
    segments = [request]
    table_match = re.search(re.escape(table_name), request, re.IGNORECASE)
    if table_match:
        segments.insert(
            0,
            request[
                table_match.start() : min(len(request), table_match.start() + 600)
            ],
        )
    patterns = (
        r"(?is)allowed\s+updates?\s*:\s*(?P<value>[^\r\n]+)",
        (
            r"(?is)update\s+only\s+(?:the\s+)?(?:listed\s+)?"
            r"(?:columns?\s+)?(?P<value>[^.;\r\n]+)"
        ),
    )
    for segment in segments:
        for pattern in patterns:
            match = re.search(pattern, segment)
            if not match:
                continue
            value = match.group("value")
            selected = [
                str(column["column_id"])
                for column in columns
                if re.search(
                    rf"(?<![\w]){re.escape(str(column['name']))}(?![\w])",
                    value,
                    re.IGNORECASE,
                )
            ]
            if selected:
                return selected
    return []


def _deterministic_reference_semantics(request: str) -> str:
    normalized = " ".join(str(request).split())
    if re.search(
        r"\b(?:upsert_update|upsert|insert[- ]or[- ]update)\b",
        normalized,
        re.IGNORECASE,
    ):
        return "upsert_update"
    if re.search(
        r"\binsert_ignore\b",
        normalized,
        re.IGNORECASE,
    ):
        return "insert_ignore"
    has_conflict = bool(_CONFLICT_CUE.search(normalized))
    update = re.search(
        r"\b(?:insert[- ]or[- ]update|overwrite|refresh|replace|update)\b",
        normalized,
        re.IGNORECASE,
    )
    ignore = re.search(
        r"\b(?:do\s+nothing|ignore|skip|leave\s+[^.;]{0,40}\s+unchanged|"
        r"keep\s+(?:the\s+)?existing)\b",
        normalized,
        re.IGNORECASE,
    )

    def negated(match: re.Match[str] | None) -> bool:
        if match is None:
            return False
        prefix = normalized[max(0, match.start() - 48) : match.start()]
        return bool(
            re.search(
                (
                    r"(?:do|does|must|should)\s+not"
                    r"(?:\s+\w+){0,5}\s*$|"
                    r"\bnever(?:\s+\w+){0,4}\s*$|"
                    r"\bwithout(?:\s+\w+){0,4}\s*$"
                ),
                prefix,
                re.IGNORECASE,
            )
        )

    if has_conflict and update is not None and not negated(update):
        return "upsert_update"
    if has_conflict and ignore is not None and not negated(ignore):
        return "insert_ignore"
    return "plain_insert"


def ambiguous_conflict_policy_diagnostic(
    request: str,
) -> Diagnostic | None:
    """Detect a stated but deliberately unspecified duplicate policy."""
    normalized = " ".join(str(request).split())
    if not _CONFLICT_CUE.search(normalized):
        return None
    if not _VAGUE_CONFLICT_POLICY.search(normalized):
        return None
    if _EXPLICIT_CONFLICT_POLICY.search(normalized):
        return None
    return Diagnostic(
        "NEEDS_CLARIFICATION",
        (
            "The request mentions a duplicate/conflict but leaves the "
            "write policy unspecified; the fail-closed policy abstains."
        ),
        path="/request/conflict_policy",
        details={"detector": "deterministic_ambiguous_conflict_language_v1"},
    )


def _error(
    errors: list[Diagnostic],
    code: str,
    message: str,
    path: str,
    *,
    group_id: str | None = None,
    candidates: list[str] | None = None,
    **details: Any,
) -> None:
    errors.append(
        Diagnostic(
            code,
            message,
            path=path,
            group_id=group_id,
            candidates=candidates or [],
            details=details,
        )
    )


def _source_reference_map(
    payload: SourcePayload,
) -> dict[str, SourceCollection]:
    references: dict[str, SourceCollection] = {}
    for collection in payload.collections:
        if collection.reference_id:
            references[collection.reference_id] = collection
        references[collection.collection_id] = collection
    return references


def _resolve_column_id(
    raw_reference: Any,
    table_profile: dict[str, Any],
    errors: list[Diagnostic],
    path: str,
    group_id: str,
) -> str | None:
    reference = str(raw_reference or "")
    columns = column_reference_map(table_profile)
    column = columns.get(reference)
    if column is None:
        _error(
            errors,
            "UNKNOWN_COLUMN_ID",
            f"Unknown enumerated column ID {reference!r}.",
            path,
            group_id=group_id,
            candidates=sorted(columns),
            predicted_column_id=reference,
        )
        return None
    if not column.get("is_insertable", True):
        _error(
            errors,
            "NON_INSERTABLE_COLUMN_ID",
            f"Column ID {reference!r} is not insertable.",
            path,
            group_id=group_id,
        )
        return None
    return str(column["name"])


def ground_reference_mapping_plan(
    mapping_plan: dict[str, Any],
    payload: SourcePayload,
    profile: dict[str, Any],
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Complete only unique all-fields mappings in the MP-FS+ ID space."""
    ensure_reference_ids(profile)
    plan = deepcopy(mapping_plan)
    diagnostics: list[Diagnostic] = []
    groups = plan.get("target_groups")
    if not isinstance(groups, list):
        return plan, diagnostics

    tables_by_name = {
        str(table.get("name")): table
        for table in profile.get("tables", [])
        if isinstance(table, dict) and table.get("name")
    }
    collections = _source_reference_map(payload)
    claimed: set[str] = set()
    ignored_fields = plan.setdefault("ignored_fields", {})
    if not isinstance(ignored_fields, dict):
        ignored_fields = {}
        plan["ignored_fields"] = ignored_fields

    def exact_spec(
        collection: SourceCollection,
    ) -> tuple[dict[str, Any], dict[str, str], list[str]] | None:
        grounding = collection_grounding(payload, collection, profile)
        table_name = str(grounding.get("exact_table_hint") or "")
        table_profile = tables_by_name.get(table_name)
        if table_profile is None:
            return None
        candidate = next(
            (
                item
                for item in grounding.get("candidate_tables") or []
                if item.get("table") == table_name
            ),
            None,
        )
        data_fields = [
            str(field)
            for field in grounding.get("data_fields") or []
        ]
        matches = (
            candidate.get("exact_identifier_match_ids") or {}
            if isinstance(candidate, dict)
            else {}
        )
        if (
            not data_fields
            or any(
                not collection.field_ids.get(field)
                or not matches.get(field)
                for field in data_fields
            )
        ):
            return None
        field_mapping = {
            str(collection.field_ids[field]): str(matches[field])
            for field in data_fields
        }
        return (
            table_profile,
            field_mapping,
            [
                str(field)
                for field in grounding.get("metadata_fields") or []
            ],
        )

    def apply_policy(
        group: dict[str, Any],
        collection: SourceCollection,
        table_profile: dict[str, Any],
        field_mapping: dict[str, str],
    ) -> None:
        semantics = _deterministic_reference_semantics(payload.raw_text)
        group["write_semantics"] = semantics
        if semantics == "plain_insert":
            group["conflict_target_id"] = None
            group["update_column_ids"] = []
            return
        group["conflict_target_id"] = _primary_constraint_id(table_profile)
        if semantics == "insert_ignore":
            group["update_column_ids"] = []
            return
        controlled_updates, preserved = _control_column_ids(
            collection,
            table_profile,
        )
        explicit_updates = _explicit_update_column_ids(
            payload.raw_text,
            table_profile,
        )
        current_updates = [
            str(value)
            for value in group.get("update_column_ids") or []
        ]
        valid_columns = {
            str(column.get("column_id"))
            for column in table_profile.get("columns", [])
            if isinstance(column, dict) and column.get("column_id")
        }
        updates = (
            controlled_updates
            or explicit_updates
            or [
                value
                for value in current_updates
                if value in valid_columns
            ]
        )
        if not updates:
            target = next(
                (
                    constraint
                    for constraint in table_profile.get("unique_indexes", [])
                    if str(constraint.get("constraint_id") or "")
                    == str(group.get("conflict_target_id") or "")
                ),
                {},
            )
            column_ids_by_name = {
                str(column.get("name")): str(
                    column.get("column_id") or ""
                )
                for column in table_profile.get("columns", [])
                if isinstance(column, dict)
                and column.get("name")
                and column.get("column_id")
            }
            key_ids = {
                column_ids_by_name[str(value)]
                for value in target.get("columns") or []
                if str(value) in column_ids_by_name
            }
            updates = [
                column_id
                for column_id in field_mapping.values()
                if column_id not in key_ids
                and column_id not in preserved
            ]
        group["update_column_ids"] = list(dict.fromkeys(updates))

    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        collection_id = str(group.get("source_collection_id") or "")
        collection = collections.get(collection_id)
        if collection is None:
            continue
        claimed.add(collection.reference_id)
        spec = exact_spec(collection)
        if spec is None:
            continue
        table_profile, field_mapping, metadata_fields = spec
        before = {
            "table_id": group.get("table_id"),
            "field_mapping": deepcopy(group.get("field_mapping") or {}),
            "write_semantics": group.get("write_semantics"),
            "conflict_target_id": group.get("conflict_target_id"),
            "update_column_ids": deepcopy(
                group.get("update_column_ids") or []
            ),
        }
        group["table_id"] = str(table_profile["table_id"])
        group["field_mapping"] = field_mapping
        group.setdefault("constants", {})
        apply_policy(group, collection, table_profile, field_mapping)
        if metadata_fields:
            collection_ignored = ignored_fields.setdefault(
                collection.reference_id,
                {},
            )
            if isinstance(collection_ignored, dict):
                for field in metadata_fields:
                    field_id = collection.field_ids.get(field)
                    if field_id:
                        collection_ignored.setdefault(
                            field_id,
                            "deterministic source control metadata",
                        )
        after = {
            "table_id": group.get("table_id"),
            "field_mapping": deepcopy(group.get("field_mapping") or {}),
            "write_semantics": group.get("write_semantics"),
            "conflict_target_id": group.get("conflict_target_id"),
            "update_column_ids": deepcopy(
                group.get("update_column_ids") or []
            ),
        }
        if before != after:
            diagnostics.append(
                Diagnostic(
                    "GROUNDED_EXACT_REFERENCE_GROUP",
                    (
                        f"Grounded collection {collection.reference_id!r} "
                        "using a unique all-fields identifier match."
                    ),
                    severity="warning",
                    path=f"/target_groups/{index}",
                    group_id=str(group.get("group_id") or f"g{index + 1}"),
                    table=str(table_profile.get("name") or ""),
                    details={
                        "reason": "unique_all_fields_identifier_match",
                        "original": before,
                    },
                )
            )

    group_ids = {
        str(group.get("group_id") or "")
        for group in groups
        if isinstance(group, dict)
    }
    for collection in payload.collections:
        if collection.reference_id in claimed:
            continue
        spec = exact_spec(collection)
        if spec is None:
            continue
        table_profile, field_mapping, metadata_fields = spec
        suffix = len(groups) + 1
        group_id = f"grounded_g{suffix}"
        while group_id in group_ids:
            suffix += 1
            group_id = f"grounded_g{suffix}"
        group: dict[str, Any] = {
            "group_id": group_id,
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": str(table_profile["table_id"]),
            "field_mapping": field_mapping,
            "constants": {},
            "write_semantics": "plain_insert",
            "conflict_target_id": None,
            "update_column_ids": [],
        }
        apply_policy(group, collection, table_profile, field_mapping)
        groups.append(group)
        group_ids.add(group_id)
        claimed.add(collection.reference_id)
        if metadata_fields:
            collection_ignored = ignored_fields.setdefault(
                collection.reference_id,
                {},
            )
            if isinstance(collection_ignored, dict):
                for field in metadata_fields:
                    field_id = collection.field_ids.get(field)
                    if field_id:
                        collection_ignored.setdefault(
                            field_id,
                            "deterministic source control metadata",
                        )
        diagnostics.append(
            Diagnostic(
                "COMPLETED_EXACT_REFERENCE_COLLECTION",
                (
                    f"Completed omitted collection "
                    f"{collection.reference_id!r} using a unique "
                    "all-fields identifier match."
                ),
                severity="warning",
                path=f"/target_groups/{len(groups) - 1}",
                group_id=group_id,
                table=str(table_profile.get("name") or ""),
                details={
                    "reason": "unique_all_fields_identifier_match",
                },
            )
        )
    return plan, diagnostics


def _resolve_policy(
    group: dict[str, Any],
    table_profile: dict[str, Any],
    errors: list[Diagnostic],
    path: str,
    group_id: str,
) -> dict[str, Any]:
    semantics = str(group.get("write_semantics") or "")
    if semantics not in WRITE_SEMANTICS:
        _error(
            errors,
            "INVALID_WRITE_SEMANTICS",
            (
                "write_semantics must be plain_insert, insert_ignore, "
                "upsert_update, or needs_clarification."
            ),
            f"{path}/write_semantics",
            group_id=group_id,
        )
        return {"action": "error", "target": [], "update_columns": []}
    if semantics == "needs_clarification":
        _error(
            errors,
            "NEEDS_CLARIFICATION",
            "Conflict behavior is ambiguous; the fail-closed policy abstains.",
            f"{path}/write_semantics",
            group_id=group_id,
        )
        return {"action": "error", "target": [], "update_columns": []}
    if semantics == "plain_insert":
        if group.get("conflict_target_id") not in {None, ""}:
            _error(
                errors,
                "UNEXPECTED_CONSTRAINT_ID",
                "plain_insert must not declare conflict_target_id.",
                f"{path}/conflict_target_id",
                group_id=group_id,
            )
        if group.get("update_column_ids"):
            _error(
                errors,
                "UNEXPECTED_UPDATE_COLUMN_IDS",
                "plain_insert must not declare update_column_ids.",
                f"{path}/update_column_ids",
                group_id=group_id,
            )
        return {"action": "error", "target": [], "update_columns": []}

    constraints = constraint_reference_map(table_profile)
    constraint_id = str(group.get("conflict_target_id") or "")
    constraint = constraints.get(constraint_id)
    if constraint is None:
        _error(
            errors,
            "UNKNOWN_CONSTRAINT_ID",
            f"Unknown enumerated constraint ID {constraint_id!r}.",
            f"{path}/conflict_target_id",
            group_id=group_id,
            candidates=sorted(constraints),
            predicted_constraint_id=constraint_id,
        )
        target: list[str] = []
    else:
        target = [str(value) for value in constraint.get("columns") or []]

    raw_updates = group.get("update_column_ids") or []
    if not isinstance(raw_updates, list):
        _error(
            errors,
            "INVALID_UPDATE_COLUMN_IDS",
            "update_column_ids must be a list.",
            f"{path}/update_column_ids",
            group_id=group_id,
        )
        raw_updates = []
    updates: list[str] = []
    for update_index, column_id in enumerate(raw_updates):
        resolved = _resolve_column_id(
            column_id,
            table_profile,
            errors,
            f"{path}/update_column_ids/{update_index}",
            group_id,
        )
        if resolved is not None and resolved not in updates:
            updates.append(resolved)
    if semantics == "insert_ignore":
        if updates:
            _error(
                errors,
                "UNEXPECTED_UPDATE_COLUMN_IDS",
                "insert_ignore must not declare update_column_ids.",
                f"{path}/update_column_ids",
                group_id=group_id,
            )
        return {
            "action": "do_nothing",
            "target": target,
            "update_columns": [],
        }
    if not updates:
        _error(
            errors,
            "MISSING_UPDATE_COLUMN_IDS",
            "upsert_update requires at least one enumerated update column.",
            f"{path}/update_column_ids",
            group_id=group_id,
        )
    return {
        "action": "do_update",
        "target": target,
        "update_columns": updates,
    }


def resolve_reference_mapping_plan(
    mapping_plan: dict[str, Any],
    payload: SourcePayload,
    profile: dict[str, Any],
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Resolve an MP-FS+ ID-only Mapping Plan into materializer input."""
    ensure_reference_ids(profile)
    plan = deepcopy(mapping_plan)
    errors: list[Diagnostic] = []
    tables = table_reference_map(profile)
    collections = _source_reference_map(payload)
    selector_ids = {
        collection.selector_id: collection
        for collection in payload.collections
        if collection.selector_id
    }
    output_groups: list[dict[str, Any]] = []
    groups = plan.get("target_groups") or []
    if not isinstance(groups, list):
        return plan, [
            Diagnostic(
                "MISSING_TARGET_GROUPS",
                "target_groups must be a list.",
                path="/target_groups",
            )
        ]

    for index, group in enumerate(groups):
        path = f"/target_groups/{index}"
        if not isinstance(group, dict):
            _error(
                errors,
                "INVALID_TARGET_GROUP",
                "Target group must be an object.",
                path,
            )
            continue
        group_id = str(group.get("group_id") or f"g{index + 1}")
        collection_id = str(group.get("source_collection_id") or "")
        selector_id = str(group.get("source_selector_id") or "")
        collection = collections.get(collection_id)
        if collection is None:
            _error(
                errors,
                "UNKNOWN_SOURCE_COLLECTION_ID",
                f"Unknown source collection ID {collection_id!r}.",
                f"{path}/source_collection_id",
                group_id=group_id,
                candidates=sorted(
                    item.reference_id
                    for item in payload.collections
                    if item.reference_id
                ),
            )
        selector_collection = selector_ids.get(selector_id)
        if selector_collection is None:
            _error(
                errors,
                "UNKNOWN_SOURCE_SELECTOR_ID",
                f"Unknown source selector ID {selector_id!r}.",
                f"{path}/source_selector_id",
                group_id=group_id,
                candidates=sorted(selector_ids),
            )
        elif collection is not None and selector_collection is not collection:
            _error(
                errors,
                "SOURCE_REFERENCE_MISMATCH",
                "Collection and selector IDs refer to different collections.",
                path,
                group_id=group_id,
            )

        table_id = str(group.get("table_id") or "")
        table_profile = tables.get(table_id)
        if table_profile is None:
            _error(
                errors,
                "UNKNOWN_TABLE_ID",
                f"Unknown enumerated table ID {table_id!r}.",
                f"{path}/table_id",
                group_id=group_id,
                candidates=sorted(tables),
                predicted_table_id=table_id,
            )

        resolved_mapping: dict[str, str] = {}
        field_mapping = group.get("field_mapping") or {}
        if not isinstance(field_mapping, dict):
            _error(
                errors,
                "INVALID_FIELD_MAPPING",
                "field_mapping must be an object.",
                f"{path}/field_mapping",
                group_id=group_id,
            )
            field_mapping = {}
        if collection is not None and table_profile is not None:
            fields_by_id = {
                field_id: field_name
                for field_name, field_id in collection.field_ids.items()
            }
            for raw_source, column_id in field_mapping.items():
                source_reference = str(raw_source)
                source_field = fields_by_id.get(source_reference, source_reference)
                if source_field not in collection.fields:
                    _error(
                        errors,
                        "UNKNOWN_SOURCE_FIELD_ID",
                        (
                            f"Unknown source field reference "
                            f"{source_reference!r} for {collection.reference_id}."
                        ),
                        f"{path}/field_mapping/{source_reference}",
                        group_id=group_id,
                        candidates=sorted(
                            [
                                *collection.fields,
                                *collection.field_ids.values(),
                            ]
                        ),
                    )
                    continue
                target_name = _resolve_column_id(
                    column_id,
                    table_profile,
                    errors,
                    f"{path}/field_mapping/{source_reference}",
                    group_id,
                )
                if target_name is not None:
                    resolved_mapping[source_field] = target_name

        resolved_constants: dict[str, Any] = {}
        constants = group.get("constants") or {}
        if not isinstance(constants, dict):
            _error(
                errors,
                "INVALID_CONSTANTS",
                "constants must be an object.",
                f"{path}/constants",
                group_id=group_id,
            )
            constants = {}
        if table_profile is not None:
            for column_id, constant in constants.items():
                target_name = _resolve_column_id(
                    column_id,
                    table_profile,
                    errors,
                    f"{path}/constants/{column_id}",
                    group_id,
                )
                if target_name is not None:
                    resolved_constants[target_name] = deepcopy(constant)

        policy = (
            _resolve_policy(group, table_profile, errors, path, group_id)
            if table_profile is not None
            else {"action": "error", "target": [], "update_columns": []}
        )
        output_groups.append(
            {
                "group_id": group_id,
                "source_collection": (
                    collection.collection_id if collection is not None else ""
                ),
                "source_rows": (
                    collection.source_path if collection is not None else ""
                ),
                "table": (
                    str(table_profile["name"])
                    if table_profile is not None
                    else ""
                ),
                "field_mapping": resolved_mapping,
                "constants": resolved_constants,
                "action": "insert",
                "conflict": policy,
                "reference_trace": {
                    "source_collection_id": collection_id,
                    "source_selector_id": selector_id,
                    "table_id": table_id,
                    "write_semantics": group.get("write_semantics"),
                    "conflict_target_id": group.get("conflict_target_id"),
                    "update_column_ids": deepcopy(
                        group.get("update_column_ids") or []
                    ),
                },
            }
        )

    ignored_fields = deepcopy(plan.get("ignored_fields") or {})
    if isinstance(ignored_fields, dict):
        for reference in list(ignored_fields):
            collection = collections.get(str(reference))
            if collection is None:
                continue
            ignored_value = ignored_fields.pop(reference)
            if isinstance(ignored_value, dict):
                fields_by_id = {
                    field_id: field_name
                    for field_name, field_id in collection.field_ids.items()
                }
                ignored_value = {
                    fields_by_id.get(str(field), str(field)): reason
                    for field, reason in ignored_value.items()
                }
            ignored_fields[collection.collection_id] = ignored_value
    return {
        "target_groups": output_groups,
        "dependencies": deepcopy(plan.get("dependencies") or []),
        "ignored_fields": ignored_fields,
        "reference_contract": "mp-fs-plus-v1",
    }, errors


def resolve_reference_policy(
    group: dict[str, Any],
    table_profile: dict[str, Any],
    *,
    path: str,
    group_id: str,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    errors: list[Diagnostic] = []
    return (
        _resolve_policy(
            group,
            table_profile,
            errors,
            path,
            group_id,
        ),
        errors,
    )
