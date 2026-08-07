from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from nldbwrite_v3.compiler import compile_verified_plan, execute_program
from nldbwrite_v3.evaluator.state import snapshot_database
from nldbwrite_v3.ir import SourcePayload, VerificationResult
from nldbwrite_v3.planner import (
    MaterializationError,
    materialize_mapping_plan,
    validate_plan_object,
)
from nldbwrite_v3.verifier import verify_write_plan


DEFAULT_ALLOWED_PREFIXES = (
    "/target_groups/",
    "/dependencies/",
    "/ignored_fields/",
)


class PatchError(ValueError):
    pass


def _decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _pointer_parts(path: str) -> list[str]:
    if not path.startswith("/"):
        raise PatchError(f"JSON Patch path must start with '/': {path!r}")
    return [_decode_pointer_token(part) for part in path[1:].split("/")]


def _parent_for_path(document: Any, path: str) -> tuple[Any, str]:
    parts = _pointer_parts(path)
    if not parts:
        raise PatchError("Replacing the entire mapping plan is not allowed")
    node = document
    for part in parts[:-1]:
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as exc:
                raise PatchError(f"Invalid list path component {part!r}") from exc
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise PatchError(f"Path does not exist: {path!r}")
    return node, parts[-1]


def apply_plan_patch(
    mapping_plan: dict[str, Any],
    patches: list[dict[str, Any]],
    *,
    allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PREFIXES,
) -> dict[str, Any]:
    """Apply a restricted JSON Patch; source payload values are never patchable."""
    candidate = deepcopy(mapping_plan)
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise PatchError(f"Patch {index} must be an object")
        operation = str(patch.get("op") or "").lower()
        path = str(patch.get("path") or "")
        if operation not in {"add", "replace", "remove"}:
            raise PatchError(f"Patch {index}: unsupported operation {operation!r}")
        if not any(path.startswith(prefix) for prefix in allowed_prefixes):
            raise PatchError(
                f"Patch {index}: path {path!r} is outside the repair allow-list"
            )
        parent, key = _parent_for_path(candidate, path)
        if isinstance(parent, list):
            if key == "-" and operation == "add":
                parent.append(deepcopy(patch.get("value")))
                continue
            try:
                item_index = int(key)
            except ValueError as exc:
                raise PatchError(f"Patch {index}: invalid list index {key!r}") from exc
            if operation == "add":
                if item_index < 0 or item_index > len(parent):
                    raise PatchError(f"Patch {index}: list index is out of range")
                parent.insert(item_index, deepcopy(patch.get("value")))
            elif operation == "replace":
                if item_index < 0 or item_index >= len(parent):
                    raise PatchError(f"Patch {index}: list index is out of range")
                parent[item_index] = deepcopy(patch.get("value"))
            else:
                if item_index < 0 or item_index >= len(parent):
                    raise PatchError(f"Patch {index}: list index is out of range")
                del parent[item_index]
        elif isinstance(parent, dict):
            if operation == "remove":
                if key not in parent:
                    raise PatchError(f"Patch {index}: key {key!r} does not exist")
                del parent[key]
            elif operation == "replace":
                if key not in parent:
                    raise PatchError(f"Patch {index}: key {key!r} does not exist")
                parent[key] = deepcopy(patch.get("value"))
            else:
                parent[key] = deepcopy(patch.get("value"))
        else:
            raise PatchError(f"Patch {index}: parent is not a container")
    return candidate


def repair_and_validate(
    mapping_plan: dict[str, Any],
    patches: list[dict[str, Any]],
    payload: SourcePayload,
    profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], VerificationResult]:
    repaired_mapping = apply_plan_patch(mapping_plan, patches)
    materialized = materialize_mapping_plan(repaired_mapping, payload)
    verification = verify_write_plan(materialized, profile)
    return repaired_mapping, materialized, verification


def _payload_hash(payload: SourcePayload) -> str:
    encoded = json.dumps(
        payload.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coverage(plan: dict[str, Any] | None) -> tuple[set[tuple[str, int]], set[tuple[str, int, str]]]:
    rows: set[tuple[str, int]] = set()
    cells: set[tuple[str, int, str]] = set()
    for group in (plan or {}).get("write_groups") or []:
        for provenance in group.get("provenance") or []:
            if not isinstance(provenance, dict):
                continue
            collection = str(provenance.get("source_collection") or "")
            row_index = provenance.get("source_row_index")
            if not collection or not isinstance(row_index, int):
                continue
            rows.add((collection, row_index))
            for source in (provenance.get("value_sources") or {}).values():
                if not isinstance(source, dict) or source.get("kind") != "source":
                    continue
                cells.add(
                    (
                        str(source.get("source_collection") or collection),
                        int(source.get("source_row_index", row_index)),
                        str(source.get("source_field") or ""),
                    )
                )
    return rows, cells


def _values_trace_to_source(
    plan: dict[str, Any],
    payload: SourcePayload,
) -> tuple[bool, list[str]]:
    collections = {
        collection.collection_id: collection for collection in payload.collections
    }
    errors: list[str] = []
    for group in plan.get("write_groups") or []:
        for row_index, (row, provenance) in enumerate(
            zip(group.get("rows") or [], group.get("provenance") or [])
        ):
            value_sources = provenance.get("value_sources") or {}
            for column, value in row.items():
                source = value_sources.get(column)
                if not isinstance(source, dict):
                    errors.append(f"{group.get('group_id')}[{row_index}].{column}: no provenance")
                    continue
                if source.get("kind") == "constant":
                    # Constant evidence is checked against instruction/schema by
                    # verify_write_plan.
                    continue
                collection_id = str(source.get("source_collection") or "")
                source_index = source.get("source_row_index")
                source_field = str(source.get("source_field") or "")
                collection = collections.get(collection_id)
                if (
                    collection is None
                    or not isinstance(source_index, int)
                    or source_index < 0
                    or source_index >= len(collection.rows)
                    or source_field not in collection.rows[source_index]
                ):
                    errors.append(
                        f"{group.get('group_id')}[{row_index}].{column}: invalid source pointer"
                    )
                    continue
                if collection.rows[source_index][source_field] != value:
                    errors.append(
                        f"{group.get('group_id')}[{row_index}].{column}: source value changed"
                    )
    return not errors, errors


def _write_scope(mapping_plan: dict[str, Any]) -> set[tuple[str, str]]:
    scope: set[tuple[str, str]] = set()
    for group in mapping_plan.get("target_groups") or []:
        table = str(group.get("table") or "")
        for target_column in (group.get("field_mapping") or {}).values():
            scope.add((table, str(target_column)))
        for target_column in (group.get("constants") or {}):
            scope.add((table, str(target_column)))
    return scope


def evaluate_repair_candidate(
    original_mapping_plan: dict[str, Any],
    repaired_mapping_plan: dict[str, Any],
    source_payload: SourcePayload,
    profile: dict[str, Any],
    database_path: str | Path | sqlite3.Connection | bytes,
    *,
    repair_reason: str | None = None,
) -> dict[str, Any]:
    """Apply the MP-FS-R-semi policy without consulting gold state."""
    payload_hash_before = _payload_hash(source_payload)
    schema_diagnostics = validate_plan_object(repaired_mapping_plan, "mapping")
    original_materialized: dict[str, Any] | None = None
    repaired_materialized: dict[str, Any] | None = None
    materialization_error: str | None = None
    try:
        original_materialized = materialize_mapping_plan(
            original_mapping_plan,
            source_payload,
        )
    except (MaterializationError, ValueError):
        original_materialized = None
    try:
        repaired_materialized = materialize_mapping_plan(
            repaired_mapping_plan,
            source_payload,
        )
    except (MaterializationError, ValueError) as exc:
        materialization_error = str(exc)

    original_rows, original_cells = _coverage(original_materialized)
    repaired_rows, repaired_cells = _coverage(repaired_materialized)
    verification = (
        verify_write_plan(repaired_materialized, profile)
        if repaired_materialized is not None
        else None
    )
    no_new_values, value_errors = (
        _values_trace_to_source(repaired_materialized, source_payload)
        if repaired_materialized is not None
        else (False, ["Materialization failed"])
    )
    program = (
        compile_verified_plan(verification.normalized_plan, profile)
        if verification is not None and verification.valid
        else None
    )
    if program is not None and program.status == "success":
        dry_run_connection = snapshot_database(database_path)
        try:
            dry_run = execute_program(
                dry_run_connection,
                program,
                dry_run=True,
            )
        finally:
            dry_run_connection.close()
    else:
        dry_run = {
            "status": "rejected",
            "executed_statements": 0,
            "committed": False,
            "error": "Program did not compile.",
        }
    original_scope = _write_scope(original_mapping_plan)
    repaired_scope = _write_scope(repaired_mapping_plan)
    expanded_scope = repaired_scope - original_scope
    checks = {
        "schema_valid": not schema_diagnostics,
        "materialization_success": repaired_materialized is not None,
        "source_row_coverage_not_reduced": repaired_rows >= original_rows,
        "source_cell_coverage_not_reduced": repaired_cells >= original_cells,
        "no_new_values": no_new_values,
        "verifier_has_no_errors": bool(verification and verification.valid),
        "full_program_builds": bool(program and program.status == "success"),
        "transaction_dry_run_succeeds": dry_run.get("status") == "dry_run_success",
        "payload_hash_unchanged": payload_hash_before == _payload_hash(source_payload),
        "write_scope_not_unjustifiably_expanded": (
            not expanded_scope or bool(str(repair_reason or "").strip())
        ),
    }
    return {
        "accepted": all(checks.values()),
        "checks": checks,
        "details": {
            "schema_errors": [item.to_dict() for item in schema_diagnostics],
            "materialization_error": materialization_error,
            "value_trace_errors": value_errors,
            "verification": verification.to_dict() if verification else None,
            "compiled_program": program.to_dict() if program else None,
            "dry_run": dry_run,
            "payload_sha256": payload_hash_before,
            "original_row_coverage": len(original_rows),
            "repaired_row_coverage": len(repaired_rows),
            "original_cell_coverage": len(original_cells),
            "repaired_cell_coverage": len(repaired_cells),
            "expanded_write_scope": [
                {"table": table, "column": column}
                for table, column in sorted(expanded_scope)
            ],
            "repair_reason": repair_reason,
        },
        "repaired_write_plan": repaired_materialized,
    }
