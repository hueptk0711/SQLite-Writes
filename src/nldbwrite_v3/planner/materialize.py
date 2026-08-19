from __future__ import annotations

from copy import deepcopy
from typing import Any

from nldbwrite_v3.ir import Diagnostic, SourcePayload
from nldbwrite_v3.vnext import (
    PAYLOAD_VALUE,
    classify_source_field_role,
    control_consumed_by,
    row_has_instruction_context,
)


class MaterializationError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(item.message for item in diagnostics))


def _selected_rows(
    group: dict[str, Any],
    payload: SourcePayload,
) -> list[tuple[str, int, dict[str, Any]]]:
    requested_collection = group.get(
        "source_collection_id",
        group.get("source_collection"),
    )
    if requested_collection is None:
        if len(payload.collections) != 1:
            raise ValueError(
                "source_collection is required when the payload has "
                f"{len(payload.collections)} collections"
            )
        collection = payload.collections[0]
    else:
        collection = next(
            (
                candidate
                for candidate in payload.collections
                if str(requested_collection)
                in {candidate.collection_id, candidate.reference_id}
            ),
            None,
        )
        if collection is None:
            raise ValueError(
                f"Unknown source_collection: {requested_collection!r}"
            )
    selector_id = group.get("source_selector_id")
    if selector_id is not None and selector_id != collection.selector_id:
        raise ValueError(f"Unknown source_selector_id: {selector_id!r}")
    selector = group.get("source_rows", collection.source_path)
    if selector in {
        "$[*]",
        "$.records[*]",
        "*",
        None,
        collection.source_path,
    }:
        return [
            (collection.collection_id, index, row)
            for index, row in enumerate(collection.rows)
        ]
    explicit = group.get("source_row_indices")
    if isinstance(explicit, list):
        selected: list[tuple[str, int, dict[str, Any]]] = []
        for raw_index in explicit:
            index = int(raw_index)
            if index < 0 or index >= len(collection.rows):
                raise IndexError(index)
            selected.append(
                (collection.collection_id, index, collection.rows[index])
            )
        return selected
    raise ValueError(f"Unsupported source_rows selector: {selector}")


def materialize_mapping_plan(
    mapping_plan: dict[str, Any],
    payload: SourcePayload,
    *,
    control_field_roles: bool = False,
) -> dict[str, Any]:
    """Apply one predicted mapping to every source row deterministically."""
    diagnostics: list[Diagnostic] = []
    target_groups = mapping_plan.get("target_groups")
    if not isinstance(target_groups, list) or not target_groups:
        raise MaterializationError(
            [
                Diagnostic(
                    "MISSING_TARGET_GROUPS",
                    "Mapping plan must contain a non-empty target_groups list.",
                    path="/target_groups",
                )
            ]
        )
    write_groups: list[dict[str, Any]] = []
    all_consumed: set[tuple[str, int, str]] = set()
    ignored_fields = mapping_plan.get("ignored_fields") or {}
    for group_index, source_group in enumerate(target_groups):
        path = f"/target_groups/{group_index}"
        if not isinstance(source_group, dict):
            diagnostics.append(
                Diagnostic(
                    "INVALID_TARGET_GROUP",
                    "Target group must be an object.",
                    path=path,
                )
            )
            continue
        group_id = str(source_group.get("group_id") or f"g{group_index + 1}")
        field_mapping = source_group.get("field_mapping") or {}
        constants = source_group.get("constants") or {}
        if not isinstance(field_mapping, dict):
            diagnostics.append(
                Diagnostic(
                    "INVALID_FIELD_MAPPING",
                    "field_mapping must be an object.",
                    path=f"{path}/field_mapping",
                    group_id=group_id,
                )
            )
            continue
        if not isinstance(constants, dict):
            diagnostics.append(
                Diagnostic(
                    "INVALID_CONSTANTS",
                    "constants must be an object.",
                    path=f"{path}/constants",
                    group_id=group_id,
                )
            )
            continue
        try:
            selected = _selected_rows(source_group, payload)
        except (ValueError, IndexError) as exc:
            diagnostics.append(
                Diagnostic(
                    "INVALID_SOURCE_SELECTOR",
                    str(exc),
                    path=f"{path}/source_rows",
                    group_id=group_id,
                )
            )
            continue
        rows: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        for collection_id, row_index, source_row in selected:
            target_row: dict[str, Any] = {}
            value_sources: dict[str, dict[str, Any]] = {}
            for source_field, target_column in field_mapping.items():
                if source_field not in source_row:
                    diagnostics.append(
                        Diagnostic(
                            "MISSING_SOURCE_FIELD",
                            f"Source row {row_index} has no field {source_field!r}.",
                            path=f"{path}/field_mapping/{source_field}",
                            group_id=group_id,
                            details={"source_row_index": row_index},
                        )
                    )
                    continue
                target_name = str(target_column)
                target_row[target_name] = deepcopy(source_row[source_field])
                value_sources[target_name] = {
                    "kind": "source",
                    "source_collection": collection_id,
                    "source_row_index": row_index,
                    "source_field": str(source_field),
                }
                all_consumed.add(
                    (collection_id, row_index, str(source_field))
                )
            for target_column, constant_spec in constants.items():
                target_name = str(target_column)
                if isinstance(constant_spec, dict) and "value" in constant_spec:
                    value = constant_spec.get("value")
                    evidence = deepcopy(constant_spec.get("evidence") or {})
                else:
                    # Legacy constants remain materializable so the verifier can
                    # reject them with a structured provenance diagnostic.
                    value = constant_spec
                    evidence = {}
                target_row[target_name] = deepcopy(value)
                value_sources[target_name] = {
                    "kind": "constant",
                    "evidence": evidence,
                }
            rows.append(target_row)
            provenance.append(
                {
                    "source_collection": collection_id,
                    "source_row_index": row_index,
                    "value_sources": value_sources,
                }
            )
        write_groups.append(
            {
                "group_id": group_id,
                "table": source_group.get("table"),
                "action": source_group.get("action", "insert"),
                "rows": rows,
                "conflict": deepcopy(
                    source_group.get("conflict")
                    or {
                        "action": "error",
                        "target": [],
                        "update_columns": [],
                    }
                ),
                "provenance": provenance,
                **(
                    {
                        "reference_trace": deepcopy(
                            source_group["reference_trace"]
                        )
                    }
                    if isinstance(source_group.get("reference_trace"), dict)
                    else {}
                ),
            }
        )

    unresolved_fields: list[dict[str, Any]] = []
    for collection in payload.collections:
        for row_index, row in enumerate(collection.rows):
            for field in row:
                if (
                    collection.collection_id,
                    row_index,
                    field,
                ) in all_consumed:
                    continue
                reason = ""
                if isinstance(ignored_fields, dict):
                    collection_ignored = ignored_fields.get(
                        collection.collection_id
                    )
                    if isinstance(collection_ignored, dict):
                        reason = str(collection_ignored.get(field) or "")
                    if not reason:
                        reason = str(ignored_fields.get(field) or "")
                role = classify_source_field_role(str(field))
                instruction_context = row_has_instruction_context(row)
                consumed_by = (
                    control_consumed_by(
                        str(field),
                        row.get(field),
                        instruction_context=instruction_context,
                    )
                    if control_field_roles and role != PAYLOAD_VALUE
                    else None
                )
                if consumed_by:
                    unresolved_fields.append(
                        {
                            "source_collection": collection.collection_id,
                            "source_row_index": row_index,
                            "field": field,
                            "role": role,
                            "consumed_by": consumed_by,
                            "reason": "typed control/instruction semantic",
                            "status": "consumed_control",
                        }
                    )
                    continue
                unresolved_fields.append(
                    {
                        "source_collection": collection.collection_id,
                        "source_row_index": row_index,
                        "field": field,
                        "role": role,
                        "reason": reason,
                        "status": "ignored" if reason else "unresolved",
                    }
                )
    if diagnostics:
        raise MaterializationError(diagnostics)
    return {
        "version": "3.0",
        "plan_kind": "materialized_write_plan",
        "source": {
            "mode": payload.mode,
            "format": payload.source_format,
            "instruction_text": payload.instruction_text,
            "row_count": len(payload.rows),
            "collections": [
                {
                    "collection_id": collection.collection_id,
                    "reference_id": collection.reference_id,
                    "selector_id": collection.selector_id,
                    "source_path": collection.source_path,
                    "format": collection.source_format,
                    "row_count": len(collection.rows),
                    "fields": list(collection.fields),
                    "field_ids": deepcopy(collection.field_ids),
                }
                for collection in payload.collections
            ],
        },
        "write_groups": write_groups,
        "dependencies": deepcopy(mapping_plan.get("dependencies") or []),
        "unresolved_fields": unresolved_fields,
    }
