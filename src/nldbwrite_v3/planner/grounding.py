from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.schema import (
    column_map,
    limited_identifier_match,
    ranked_column_candidates,
    table_map,
)


_DUPLICATE_LANGUAGE = re.compile(
    r"\b(?:already|conflict(?:ing)?|duplicate[sd]?|existing)\b",
    re.IGNORECASE,
)
_UPDATE_LANGUAGE = re.compile(
    r"\b(?:insert[- ]or[- ]update|overwrite|refresh(?:ed)?|"
    r"replace|update[sd]?)\b",
    re.IGNORECASE,
)
_IGNORE_LANGUAGE = re.compile(
    r"\b(?:do\s+nothing|ignore[sd]?|skip(?:ped)?|"
    r"without\s+error|do\s+not\s+error|don(?:'|’)?t\s+error)\b",
    re.IGNORECASE,
)
_ERROR_LANGUAGE = re.compile(
    r"\b(?:abort|error|fail(?:ed|ure)?|reject(?:ed|ion)?)\b",
    re.IGNORECASE,
)
_SOURCE_METADATA_FIELDS = {
    "allowedupdates",
    "conflictkey",
    "conflicttarget",
    "instruction",
    "operation",
    "policy",
    "preservecolumns",
    "table",
    "targettable",
    "updatecolumns",
}


def _canonical_field(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


def collection_grounding(
    payload: SourcePayload,
    collection: SourceCollection,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Return value-free, deterministic schema-linking evidence."""
    tables = table_map(profile)
    table_hint: str | None = None
    metadata_fields: list[str] = []

    matched_collection, _ = limited_identifier_match(
        collection.collection_id,
        tables,
    )
    if matched_collection:
        table_hint = matched_collection

    labels = re.findall(
        r"(?im)^\s*table\s*:\s*([^\r\n]+?)\s*$",
        payload.instruction_text,
    )
    if not labels:
        labels = re.findall(
            (
                r"(?im)^\s*([A-Za-z][\w]*)\s+"
                r"(?:CSV|TSV)(?:\s*\([^)\r\n]*\))?\s*:\s*$"
            ),
            payload.instruction_text,
        )
    if not labels:
        labels = re.findall(
            (
                r"(?im)^\s*#{1,6}\s*(?:\d+\.\s*)?"
                r"`([^`\r\n]+)`\s*$"
            ),
            payload.instruction_text,
        )
    if labels and len(labels) == len(payload.collections):
        collection_index = next(
            (
                index
                for index, candidate in enumerate(payload.collections)
                if candidate is collection
            ),
            None,
        )
        if collection_index is not None:
            matched_label, _ = limited_identifier_match(
                labels[collection_index].strip(),
                tables,
            )
            if matched_label:
                table_hint = matched_label

    if "table" in collection.fields and collection.rows:
        values = {
            str(row.get("table") or "").strip()
            for row in collection.rows
        }
        values.discard("")
        if len(values) == 1:
            matched_value, _ = limited_identifier_match(
                next(iter(values)),
                tables,
            )
            if matched_value:
                table_hint = matched_value
                metadata_fields.append("table")
    metadata_fields.extend(
        field
        for field in collection.fields
        if field not in metadata_fields
        and _canonical_field(field) in _SOURCE_METADATA_FIELDS
    )

    data_fields = [
        field
        for field in collection.fields
        if field not in metadata_fields
    ]
    candidates: list[dict[str, Any]] = []
    for table_name, table_profile in tables.items():
        columns = column_map(table_profile)
        matches: dict[str, str] = {}
        match_ids: dict[str, str] = {}
        candidate_column_ids: dict[str, list[str]] = {}
        for field in data_fields:
            matched, _ = limited_identifier_match(field, columns)
            if matched:
                matches[field] = matched
                match_ids[field] = str(columns[matched].get("column_id") or "")
            candidate_column_ids[field] = ranked_column_candidates(
                field,
                table_profile,
                limit=5,
            )
        if matches:
            candidates.append(
                {
                    "table_id": table_profile.get("table_id"),
                    "table": table_name,
                    "exact_identifier_matches": matches,
                    "exact_identifier_match_ids": match_ids,
                    "candidate_column_ids": candidate_column_ids,
                    "match_count": len(matches),
                    "field_count": len(data_fields),
                }
            )
    candidates.sort(
        key=lambda item: (
            0 if item["table"] == table_hint else 1,
            -int(item["match_count"]),
            str(item["table"]).casefold(),
        )
    )
    exact_table_hint: str | None = None
    if candidates:
        top = candidates[0]
        top_count = int(top.get("match_count") or 0)
        field_count = int(top.get("field_count") or 0)
        second_count = (
            int(candidates[1].get("match_count") or 0)
            if len(candidates) > 1
            else 0
        )
        if (
            field_count > 0
            and top_count == field_count
            and top_count > second_count
        ):
            exact_table_hint = str(top["table"])
    return {
        "table_hint": table_hint,
        "exact_table_hint": exact_table_hint,
        "metadata_fields": metadata_fields,
        "data_fields": data_fields,
        "candidate_tables": candidates[:4],
    }


def _dominant_table_candidate(
    grounding: dict[str, Any],
    current_table: str,
) -> str | None:
    candidates = grounding.get("candidate_tables") or []
    if not candidates:
        return None
    top = candidates[0]
    field_count = int(top.get("field_count") or 0)
    top_count = int(top.get("match_count") or 0)
    if field_count <= 0 or top_count < 2:
        return None
    if top_count / field_count < 0.75:
        return None
    second_count = (
        int(candidates[1].get("match_count") or 0)
        if len(candidates) > 1
        else 0
    )
    current_count = next(
        (
            int(item.get("match_count") or 0)
            for item in candidates
            if item.get("table") == current_table
        ),
        0,
    )
    if top_count <= second_count or top_count < current_count + 2:
        return None
    return str(top["table"])


def _explicit_conflict_intent(instruction_text: str) -> str | None:
    if not _DUPLICATE_LANGUAGE.search(instruction_text):
        return None
    if _UPDATE_LANGUAGE.search(instruction_text):
        return "do_update"
    if _IGNORE_LANGUAGE.search(instruction_text):
        return "do_nothing"
    return None


def _explicit_error_intent(instruction_text: str) -> bool:
    return bool(
        _DUPLICATE_LANGUAGE.search(instruction_text)
        and _ERROR_LANGUAGE.search(instruction_text)
    )


def _preferred_conflict_target(
    table_profile: dict[str, Any],
    available_columns: set[str],
) -> list[str]:
    candidates = [
        index
        for index in table_profile.get("unique_indexes", [])
        if isinstance(index, dict)
        and index.get("columns")
        and all(
            str(column) in available_columns
            for column in index.get("columns") or []
        )
    ]
    candidates.sort(
        key=lambda index: (
            0 if index.get("is_primary_key") else 1,
            len(index.get("columns") or []),
            tuple(str(item) for item in index.get("columns") or []),
        )
    )
    return (
        [str(column) for column in candidates[0]["columns"]]
        if candidates
        else []
    )


def _complete_unclaimed_collections(
    plan: dict[str, Any],
    payload: SourcePayload,
    profile: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> None:
    """Complete only exact, value-free mappings for omitted collections."""
    groups = plan.get("target_groups")
    if not isinstance(groups, list):
        return
    tables = table_map(profile)
    claimed = {
        str(group.get("source_collection") or "")
        for group in groups
        if isinstance(group, dict)
    }
    group_ids = {
        str(group.get("group_id") or "")
        for group in groups
        if isinstance(group, dict)
    }
    for collection in payload.collections:
        if collection.collection_id in claimed:
            continue
        grounding = collection_grounding(payload, collection, profile)
        table_name = grounding.get("table_hint")
        table_profile = tables.get(str(table_name or ""))
        data_fields = grounding.get("data_fields") or []
        if table_profile is None or not data_fields:
            continue
        columns = column_map(table_profile)
        field_mapping: dict[str, str] = {}
        for field in data_fields:
            matched, _ = limited_identifier_match(str(field), columns)
            if not matched:
                break
            field_mapping[str(field)] = matched
        if len(field_mapping) != len(data_fields):
            continue
        suffix = len(groups) + 1
        group_id = f"grounded_g{suffix}"
        while group_id in group_ids:
            suffix += 1
            group_id = f"grounded_g{suffix}"
        groups.append(
            {
                "group_id": group_id,
                "source_collection": collection.collection_id,
                "table": table_name,
                "source_rows": collection.source_path,
                "field_mapping": field_mapping,
                "constants": {},
                "action": "insert",
                "conflict": {
                    "action": "error",
                    "target": [],
                    "update_columns": [],
                },
            }
        )
        group_ids.add(group_id)
        claimed.add(collection.collection_id)
        diagnostics.append(
            Diagnostic(
                "COMPLETED_EXACT_SOURCE_COLLECTION",
                (
                    f"Completed omitted collection "
                    f"{collection.collection_id!r} as table "
                    f"{table_name!r} using exact identifiers only."
                ),
                severity="warning",
                path=f"/target_groups/{len(groups) - 1}",
                group_id=group_id,
                table=str(table_name),
                details={"reason": "complete_exact_identifier_mapping"},
            )
        )
def _complete_schema_dependencies(
    plan: dict[str, Any],
    profile: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> None:
    """Add only unambiguous parent-before-child schema dependencies."""
    groups = [
        group
        for group in (plan.get("target_groups") or [])
        if isinstance(group, dict)
    ]
    dependencies = plan.setdefault("dependencies", [])
    if not isinstance(dependencies, list):
        return
    groups_by_table: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        groups_by_table.setdefault(str(group.get("table") or ""), []).append(
            group
        )
    existing = {
        (
            str(item.get("before") or ""),
            str(item.get("after") or ""),
        )
        for item in dependencies
        if isinstance(item, dict)
    }
    tables = table_map(profile)
    for child in groups:
        child_id = str(child.get("group_id") or "")
        child_profile = tables.get(str(child.get("table") or ""))
        if not child_id or child_profile is None:
            continue
        supplied_columns = {
            str(column)
            for column in (child.get("field_mapping") or {}).values()
        }
        supplied_columns.update(
            str(column)
            for column in (child.get("constants") or {})
        )
        for foreign_key in child_profile.get("foreign_keys", []):
            if not isinstance(foreign_key, dict):
                continue
            from_column = str(
                foreign_key.get("from_column") or ""
            )
            parent_table = str(
                foreign_key.get("to_table") or ""
            )
            parents = groups_by_table.get(parent_table) or []
            if from_column not in supplied_columns or len(parents) != 1:
                continue
            parent_id = str(parents[0].get("group_id") or "")
            edge = (parent_id, child_id)
            if not parent_id or parent_id == child_id or edge in existing:
                continue
            dependencies.append(
                {
                    "before": parent_id,
                    "after": child_id,
                    "foreign_key": deepcopy(foreign_key),
                }
            )
            existing.add(edge)
            diagnostics.append(
                Diagnostic(
                    "COMPLETED_SCHEMA_DEPENDENCY",
                    (
                        f"Added schema dependency {parent_id!r} before "
                        f"{child_id!r}."
                    ),
                    severity="warning",
                    path=f"/dependencies/{len(dependencies) - 1}",
                    group_id=child_id,
                    table=str(child.get("table") or ""),
                    details={"reason": "declared_foreign_key"},
                )
            )


def ground_mapping_plan(
    mapping_plan: dict[str, Any],
    payload: SourcePayload,
    profile: dict[str, Any],
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Apply conservative, auditable schema and policy grounding."""
    plan = deepcopy(mapping_plan)
    diagnostics: list[Diagnostic] = []
    tables = table_map(profile)
    collections = {
        collection.collection_id: collection
        for collection in payload.collections
    }
    ignored_fields = plan.setdefault("ignored_fields", {})
    if not isinstance(ignored_fields, dict):
        return plan, diagnostics
    _complete_unclaimed_collections(
        plan,
        payload,
        profile,
        diagnostics,
    )
    source_group_counts = Counter(
        str(group.get("source_collection") or "")
        for group in (plan.get("target_groups") or [])
        if isinstance(group, dict)
    )

    for index, group in enumerate(plan.get("target_groups") or []):
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or f"g{index + 1}")
        collection = collections.get(
            str(group.get("source_collection") or "")
        )
        if collection is None:
            continue
        grounding = collection_grounding(
            payload,
            collection,
            profile,
        )
        current_table = str(group.get("table") or "")
        desired_table = grounding.get("table_hint")
        grounding_reason = "explicit_table_hint"
        if (
            not desired_table
            and source_group_counts[collection.collection_id] == 1
        ):
            desired_table = _dominant_table_candidate(
                grounding,
                current_table,
            )
            grounding_reason = "dominant_exact_identifier_match"

        table_changed = bool(
            desired_table
            and desired_table in tables
            and desired_table != current_table
        )
        exact_grounding = bool(
            desired_table
            and desired_table in tables
            and (
                table_changed
                or grounding.get("table_hint") == desired_table
            )
        )
        if table_changed:
            group["table"] = desired_table
            diagnostics.append(
                Diagnostic(
                    "GROUNDED_TARGET_TABLE",
                    (
                        f"Grounded target table {current_table!r} to "
                        f"{desired_table!r} using {grounding_reason}."
                    ),
                    severity="warning",
                    path=f"/target_groups/{index}/table",
                    group_id=group_id,
                    table=str(desired_table),
                    details={
                        "original_table": current_table,
                        "reason": grounding_reason,
                    },
                )
            )

        selected_table = str(group.get("table") or "")
        table_profile = tables.get(selected_table)
        field_mapping = group.get("field_mapping")
        if table_profile is not None and isinstance(field_mapping, dict):
            columns = column_map(table_profile)
            for source_field, predicted_column in list(
                field_mapping.items()
            ):
                if source_field in grounding.get("metadata_fields", []):
                    del field_mapping[source_field]
                    continue
                exact_column, _ = limited_identifier_match(
                    str(source_field),
                    columns,
                )
                predicted_match, _ = limited_identifier_match(
                    str(predicted_column),
                    columns,
                )
                if (
                    exact_grounding
                    and exact_column
                    and predicted_match != exact_column
                ):
                    field_mapping[source_field] = exact_column
                    diagnostics.append(
                        Diagnostic(
                            "GROUNDED_TARGET_COLUMN",
                            (
                                f"Grounded {source_field!r} from "
                                f"{predicted_column!r} to "
                                f"{exact_column!r} by exact identifier."
                            ),
                            severity="warning",
                            path=(
                                f"/target_groups/{index}/field_mapping/"
                                f"{source_field}"
                            ),
                            group_id=group_id,
                            table=selected_table,
                            details={
                                "original_column": predicted_column,
                                "reason": "exact_identifier_match",
                            },
                        )
                    )

        metadata_fields = grounding.get("metadata_fields") or []
        if metadata_fields:
            collection_ignored = ignored_fields.get(
                collection.collection_id
            )
            if not isinstance(collection_ignored, dict):
                collection_ignored = {}
                ignored_fields[collection.collection_id] = (
                    collection_ignored
                )
            for field in metadata_fields:
                collection_ignored.setdefault(
                    field,
                    "deterministic target-table metadata",
                )

        if table_profile is None:
            continue
        mapped_columns = {
            str(column)
            for column in (group.get("field_mapping") or {}).values()
        }
        mapped_columns.update(
            str(column)
            for column in (group.get("constants") or {})
        )
        normalized_columns = set()
        columns = column_map(table_profile)
        for raw_column in mapped_columns:
            matched, _ = limited_identifier_match(raw_column, columns)
            if matched:
                normalized_columns.add(matched)
        target = _preferred_conflict_target(
            table_profile,
            normalized_columns,
        )
        intent = _explicit_conflict_intent(payload.instruction_text)
        grounding_reason = "explicit_duplicate_instruction"
        if (
            intent is None
            and len(payload.collections) > 1
            and not _explicit_error_intent(payload.instruction_text)
            and str((group.get("conflict") or {}).get("action")) == "error"
            and target
            and normalized_columns == set(target)
        ):
            intent = "do_nothing"
            grounding_reason = "safe_key_only_multicollection_insert"
        if intent is None:
            continue
        if intent == "do_update":
            update_columns = sorted(normalized_columns - set(target))
            if not target or not update_columns:
                continue
        else:
            update_columns = []
        prior = deepcopy(group.get("conflict") or {})
        grounded_conflict = {
            "action": intent,
            "target": target,
            "update_columns": update_columns,
        }
        if prior != grounded_conflict:
            group["conflict"] = grounded_conflict
            diagnostics.append(
                Diagnostic(
                    "GROUNDED_CONFLICT_POLICY",
                    (
                        "Grounded conflict policy to "
                        f"{intent!r} using {grounding_reason}."
                    ),
                    severity="warning",
                    path=f"/target_groups/{index}/conflict",
                    group_id=group_id,
                    table=selected_table,
                    details={
                        "original_conflict": prior,
                        "reason": grounding_reason,
                    },
                )
            )
    _complete_schema_dependencies(plan, profile, diagnostics)
    return plan, diagnostics
