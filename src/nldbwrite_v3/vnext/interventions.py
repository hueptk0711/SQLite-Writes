from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.schema import ensure_reference_ids


PAYLOAD_VALUE = "PAYLOAD_VALUE"
OPERATION_CONTROL = "OPERATION_CONTROL"
CONFLICT_CONTROL = "CONFLICT_CONTROL"
UPDATE_CONTROL = "UPDATE_CONTROL"
METADATA = "METADATA"


def _canonical(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value), flags=re.UNICODE).casefold()


_OPERATION_FIELDS = {
    "operation", "writeoperation", "writeaction", "action", "operationtype",
}
_CONFLICT_FIELDS = {
    "conflict", "conflictaction", "conflictbehavior", "conflictkey",
    "conflicttarget", "conflicttargets", "conflictpolicy", "duplicatepolicy",
    "onconflict", "onduplicate", "knownconflictwitness",
}
_UPDATE_FIELDS = {
    "update", "updates", "updatecolumns", "requestedupdatecolumns",
    "allowedupdates", "preservecolumns", "relationshipcolumnsnotupdated",
    "excludedupdatecolumns", "donotupdate", "donotupdatecolumns",
}
_METADATA_FIELDS = {
    "table", "targettable", "instruction", "requirement", "ordering",
    "processingorder", "relationshiporder", "keystatus", "registrystate",
    "newkeys", "policy",
}


def classify_source_field_role(field: str) -> str:
    """Classify a source field without using sample IDs or gold information."""
    canonical = _canonical(field)
    if canonical in _OPERATION_FIELDS:
        return OPERATION_CONTROL
    if canonical in _CONFLICT_FIELDS or canonical.endswith("conflictkey") or canonical.endswith("conflicttarget"):
        return CONFLICT_CONTROL
    if canonical in _UPDATE_FIELDS or canonical.endswith("updatecolumns"):
        return UPDATE_CONTROL
    if canonical in _METADATA_FIELDS:
        return METADATA
    return PAYLOAD_VALUE


def control_consumed_by(
    field: str,
    value: Any,
    *,
    instruction_context: bool = False,
) -> str | None:
    """Return the typed semantic namespace that consumes a control field.

    Name classification alone is not enough to suppress provenance checks.
    Operation controls must contain a recognized write operation; other
    control/metadata fields are consumed only when the same row/collection is
    already known to be an instruction context. This avoids silently treating
    a legitimate payload column named ``action``/``table`` as metadata.
    """
    role = classify_source_field_role(field)
    if role == OPERATION_CONTROL:
        return (
            "instruction_semantics.operation"
            if _operation_from_value(value) is not None
            else None
        )
    if not instruction_context or not str(value).strip():
        return None
    if role == CONFLICT_CONTROL:
        canonical = _canonical(field)
        if "target" in canonical or "key" in canonical:
            return "instruction_semantics.conflict_target"
        if "action" in canonical or "policy" in canonical or canonical in {"conflict", "onconflict", "onduplicate"}:
            return "instruction_semantics.conflict_action"
        return "instruction_semantics.conflict_metadata"
    if role == UPDATE_CONTROL:
        canonical = _canonical(field)
        if "preserve" in canonical or "notupdated" in canonical or "donotupdate" in canonical or "excluded" in canonical:
            return "instruction_semantics.excluded_update_columns"
        return "instruction_semantics.requested_update_columns"
    if role == METADATA:
        return f"instruction_semantics.metadata.{_canonical(field)}"
    return None


def row_has_instruction_context(row: Mapping[str, Any]) -> bool:
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
    # Some historical formats leave controls in rows. Read them as semantic
    # evidence, but do not delete or rewrite the source row here.
    for row in collection.rows:
        controls = {
            str(field): value
            for field, value in row.items()
            if classify_source_field_role(str(field)) != PAYLOAD_VALUE
        }
        if controls:
            output.append(controls)
    return output


def _operation_from_value(value: Any) -> str | None:
    text = " ".join(str(value).strip().casefold().replace("_", " ").replace("-", " ").split())
    if not text:
        return None
    if any(token in text for token in ("upsert", "do update", "insert or update", "insert update")):
        return "upsert_update"
    if any(token in text for token in ("insert ignore", "do nothing", "ignore", "skip")):
        return "insert_ignore"
    if any(token in text for token in ("plain insert", "error on conflict", "raise on conflict", "fail on conflict")):
        return "plain_insert"
    if text in {"insert", "error", "fail", "raise"}:
        return "plain_insert"
    return None


def _explicit_operation(payload: SourcePayload, collection: SourceCollection) -> str | None:
    for controls in _iter_control_objects(collection):
        for field, value in controls.items():
            role = classify_source_field_role(str(field))
            canonical = _canonical(field)
            if role == OPERATION_CONTROL or canonical in {"conflictpolicy", "onconflict", "onduplicate"}:
                result = _operation_from_value(value)
                if result:
                    return result
    text = " ".join(payload.raw_text.split())
    lowered = text.casefold()
    patterns = [
        (r"\b(?:upsert_update|upsert|insert[- ]or[- ]update)\b", "upsert_update"),
        (r"\b(?:insert_ignore|insert[- ]or[- ]ignore|do\s+nothing)\b", "insert_ignore"),
        (r"\b(?:plain_insert|plain\s+insert)\b", "plain_insert"),
    ]
    for pattern, semantic in patterns:
        if re.search(pattern, lowered, re.IGNORECASE):
            return semantic
    if re.search(r"\bon\s+conflict\b", lowered):
        if re.search(r"\bdo\s+update\b", lowered):
            return "upsert_update"
        if re.search(r"\bdo\s+nothing\b", lowered):
            return "insert_ignore"
    if re.search(r"\b(?:error|fail|raise|reject)\b[^.;\n]{0,50}\bconflict\b", lowered):
        return "plain_insert"
    return None


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


def _column_ids_by_name(table: dict[str, Any]) -> dict[str, str]:
    return {
        _canonical(column.get("name")): str(column.get("column_id"))
        for column in table.get("columns", [])
        if isinstance(column, dict) and column.get("name") and column.get("column_id")
    }


def _exact_column_ids(names: list[str], table: dict[str, Any]) -> tuple[list[str], list[str]]:
    by_name = _column_ids_by_name(table)
    resolved: list[str] = []
    unresolved: list[str] = []
    for name in names:
        column_id = by_name.get(_canonical(name))
        if column_id:
            if column_id not in resolved:
                resolved.append(column_id)
        elif name:
            unresolved.append(name)
    return resolved, unresolved


def _explicit_update_ids(collection: SourceCollection, table: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    requested_names: list[str] = []
    excluded_names: list[str] = []
    for controls in _iter_control_objects(collection):
        for field, value in controls.items():
            if classify_source_field_role(str(field)) != UPDATE_CONTROL:
                continue
            canonical = _canonical(field)
            target = excluded_names if (
                "preserve" in canonical or "notupdated" in canonical or "donotupdate" in canonical or "excluded" in canonical
            ) else requested_names
            target.extend(_split_names(value))
    requested, unresolved_requested = _exact_column_ids(requested_names, table)
    excluded, unresolved_excluded = _exact_column_ids(excluded_names, table)
    requested = [column_id for column_id in requested if column_id not in set(excluded)]
    return requested, excluded, unresolved_requested + unresolved_excluded


def _explicit_conflict_target_id(collection: SourceCollection, table: dict[str, Any]) -> tuple[str | None, list[str]]:
    names: list[str] = []
    for controls in _iter_control_objects(collection):
        for field, value in controls.items():
            canonical = _canonical(field)
            if classify_source_field_role(str(field)) == CONFLICT_CONTROL and ("target" in canonical or "key" in canonical):
                names.extend(_split_names(value))
    if not names:
        return None, []
    column_ids, unresolved = _exact_column_ids(names, table)
    if unresolved:
        return None, unresolved
    column_id_set = set(column_ids)
    matches = []
    by_name = _column_ids_by_name(table)
    for constraint in table.get("unique_indexes", []):
        if not isinstance(constraint, dict) or not constraint.get("constraint_id"):
            continue
        constraint_ids = {
            by_name.get(_canonical(name), "")
            for name in constraint.get("columns") or []
        }
        constraint_ids.discard("")
        if constraint_ids and constraint_ids == column_id_set:
            matches.append(str(constraint["constraint_id"]))
    return (matches[0] if len(matches) == 1 else None), ([] if len(matches) == 1 else names)



def _operation_from_request_text(request: str) -> str | None:
    text = " ".join(str(request).split()).casefold()
    if re.search(r"\b(?:upsert_update|upsert|insert[- ]or[- ]update)\b", text):
        return "upsert_update"
    if re.search(r"\b(?:insert_ignore|insert[- ]or[- ]ignore|do\s+nothing)\b", text):
        return "insert_ignore"
    if re.search(r"\bon\s+conflict\b", text):
        if re.search(r"\bdo\s+update\b", text):
            return "upsert_update"
        if re.search(r"\bdo\s+nothing\b", text):
            return "insert_ignore"
    conflict_cue = re.search(r"\b(?:conflict|duplicate|already\s+exists?|existing\s+(?:row|record|key))\b", text)
    if conflict_cue and re.search(r"\b(?:ignore|skip|keep\s+(?:the\s+)?existing|leave[^.;]{0,40}unchanged)\b", text):
        return "insert_ignore"
    if conflict_cue and re.search(r"\b(?:update|overwrite|replace|merge)\b", text):
        return "upsert_update"
    if re.search(r"\b(?:plain[_ -]?insert)\b", text):
        return "plain_insert"
    if conflict_cue and re.search(r"\b(?:error|fail|raise|reject)\b", text):
        return "plain_insert"
    return None


def _request_conflict_target_id(request: str, table: dict[str, Any]) -> tuple[str | None, list[str]]:
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
        return None, []
    column_ids, unresolved = _exact_column_ids(names, table)
    if unresolved:
        return None, unresolved
    by_name = _column_ids_by_name(table)
    target_set = set(column_ids)
    matches = []
    for constraint in table.get("unique_indexes", []):
        if not isinstance(constraint, dict) or not constraint.get("constraint_id"):
            continue
        ids = {by_name.get(_canonical(name), "") for name in constraint.get("columns") or []}
        ids.discard("")
        if ids and ids == target_set:
            matches.append(str(constraint["constraint_id"]))
    return (matches[0] if len(matches) == 1 else None), ([] if len(matches) == 1 else names)


def _request_update_ids(request: str, table: dict[str, Any]) -> tuple[list[str], list[str]]:
    cleaned = re.sub(r"[`*]", "", request)
    patterns = (
        r"(?is)allowed\s+updates?\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)update[_ -]?columns?\s*[:=]\s*(?P<value>[^.;\r\n]+)",
        r"(?is)do\s+update\s+set\s+(?P<value>[^.;\r\n]+)",
        r"(?is)update\s+only\s+(?:the\s+)?(?:listed\s+)?(?:columns?\s+)?(?P<value>[^.;\r\n]+)",
    )
    columns = [
        column for column in table.get("columns", [])
        if isinstance(column, dict) and column.get("name") and column.get("column_id")
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        value = match.group("value")
        selected = [
            str(column["column_id"])
            for column in columns
            if re.search(rf"(?<![\w]){re.escape(str(column['name']))}(?![\w])", value, re.IGNORECASE)
        ]
        if selected:
            return list(dict.fromkeys(selected)), []
    return [], []


def apply_free_text_reference_interventions(
    mapping_plan: dict[str, Any],
    request: str,
    profile: dict[str, Any],
    config: Stage2InterventionConfig,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Apply B-C preservation to free-text reference plans before materialization."""
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
    explicit_operation = _operation_from_request_text(request)
    for index, group in enumerate(plan.get("write_groups") or []):
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or f"g{index + 1}")
        path = f"/write_groups/{index}"
        table = tables.get(str(group.get("table_id") or ""))
        if table is None:
            continue
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
                    "The generated plan dropped or contradicted explicit free-text conflict semantics; the typed semantic was deterministically restored before materialization.",
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
                target_id, unresolved = _request_conflict_target_id(request, table)
                if target_id:
                    group["conflict_target_id"] = target_id
                    trace["requested_conflict_target_id"] = target_id
                elif unresolved and not group.get("conflict_target_id"):
                    diagnostics.append(Diagnostic(
                        "EXPLICIT_CONFLICT_SEMANTICS_DROPPED",
                        "Explicit free-text conflict target cannot be resolved exactly to one unique constraint.",
                        path=f"{path}/conflict_target_id", group_id=group_id,
                        details={"unresolved_target_names": unresolved},
                    ))
                if explicit_operation == "insert_ignore":
                    group["update_column_ids"] = []
        if config.update_column_consistency and str(group.get("write_semantics") or "") == "upsert_update":
            requested, unresolved = _request_update_ids(request, table)
            trace["requested_update_column_ids"] = requested
            current = [str(value) for value in group.get("update_column_ids") or []]
            trace["planned_update_column_ids_before"] = current
            if unresolved:
                diagnostics.append(Diagnostic(
                    "REQUIRED_UPDATE_COLUMNS_UNRESOLVED",
                    "Explicit free-text update columns cannot be resolved exactly.",
                    path=f"{path}/update_column_ids", group_id=group_id,
                    details={"unresolved_column_names": unresolved},
                ))
            if requested and current != requested:
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
    """Apply A-C deterministic preservation in reference-ID space.

    This function never inspects gold plans, sample IDs, or database post-states.
    When all flags are false it returns a deep copy with no changes.
    """
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
        trace = group.setdefault("stage2_intervention_trace", {})
        if not isinstance(trace, dict):
            trace = {}
            group["stage2_intervention_trace"] = trace
        group_diag_start = len(diagnostics)

        explicit_operation = _explicit_operation(payload, collection)
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
            if explicit_operation == "plain_insert":
                group["conflict_target_id"] = None
                group["update_column_ids"] = []
            elif explicit_operation in {"insert_ignore", "upsert_update"}:
                explicit_target, unresolved_target = _explicit_conflict_target_id(collection, table)
                if explicit_target:
                    old_target = group.get("conflict_target_id")
                    group["conflict_target_id"] = explicit_target
                    trace["requested_conflict_target_id"] = explicit_target
                    if old_target != explicit_target:
                        diagnostics.append(Diagnostic(
                            "EXPLICIT_CONFLICT_TARGET_PRESERVED",
                            "Explicit conflict target was deterministically preserved.",
                            severity="warning",
                            path=f"{path}/conflict_target_id",
                            group_id=group_id,
                            details={"previous": old_target, "preserved": explicit_target},
                        ))
                elif unresolved_target and not group.get("conflict_target_id"):
                    diagnostics.append(Diagnostic(
                        "EXPLICIT_CONFLICT_SEMANTICS_DROPPED",
                        "The request contains an explicit conflict target that cannot be resolved exactly to one enumerated unique constraint.",
                        path=f"{path}/conflict_target_id",
                        group_id=group_id,
                        details={"unresolved_target_names": unresolved_target},
                    ))
                if explicit_operation == "insert_ignore":
                    group["update_column_ids"] = []

        if config.update_column_consistency and str(group.get("write_semantics") or "") == "upsert_update":
            requested, excluded, unresolved = _explicit_update_ids(collection, table)
            trace["requested_update_column_ids"] = requested
            trace["excluded_update_column_ids"] = excluded
            current = [str(value) for value in group.get("update_column_ids") or []]
            trace["planned_update_column_ids_before"] = current
            if unresolved:
                diagnostics.append(Diagnostic(
                    "REQUIRED_UPDATE_COLUMNS_UNRESOLVED",
                    "Explicit update-column controls contain names that cannot be resolved exactly.",
                    path=f"{path}/update_column_ids",
                    group_id=group_id,
                    details={"unresolved_column_names": unresolved},
                ))
            if requested:
                missing = [column_id for column_id in requested if column_id not in current]
                forbidden = [column_id for column_id in current if column_id in set(excluded)]
                extras = [column_id for column_id in current if column_id not in set(requested)]
                # Explicit requested updates define the closed update set. This is
                # deterministic semantic preservation, not LLM repair.
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
                            "missing": missing,
                            "forbidden": forbidden,
                            "extras_removed": extras,
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
