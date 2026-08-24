from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.schema import (
    column_reference_map,
    ensure_reference_ids,
    table_reference_map,
)


def _strip_identifier_quotes(value: Any) -> str:
    text = str(value or "").strip()
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
    """Exact identifier key; punctuation/underscores are intentionally preserved."""
    return _strip_identifier_quotes(value).casefold()


def _suffix_identifier(value: Any) -> str:
    text = _strip_identifier_quotes(value)
    if "." in text:
        return text.split(".", 1)[1]
    return text


@dataclass(frozen=True, slots=True)
class ConstrainedReferenceRepairConfig:
    enabled: bool = False
    max_attempts_per_slot: int = 1
    require_unique_candidate: bool = True
    preserve_non_reference_semantics: bool = True
    emit_repair_provenance: bool = True

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "ConstrainedReferenceRepairConfig":
        value = value or {}
        config = cls(
            enabled=bool(value.get("enabled", False)),
            max_attempts_per_slot=int(value.get("max_attempts_per_slot", 1)),
            require_unique_candidate=bool(
                value.get("require_unique_candidate", True)
            ),
            preserve_non_reference_semantics=bool(
                value.get("preserve_non_reference_semantics", True)
            ),
            emit_repair_provenance=bool(
                value.get("emit_repair_provenance", True)
            ),
        )
        if config.enabled:
            if config.max_attempts_per_slot != 1:
                raise ValueError("Stage2-F requires max_attempts_per_slot=1.")
            if not config.require_unique_candidate:
                raise ValueError("Stage2-F requires unique-candidate repair.")
            if not config.preserve_non_reference_semantics:
                raise ValueError(
                    "Stage2-F must preserve non-reference semantics."
                )
            if not config.emit_repair_provenance:
                raise ValueError("Stage2-F repair provenance is mandatory.")
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_attempts_per_slot": self.max_attempts_per_slot,
            "require_unique_candidate": self.require_unique_candidate,
            "preserve_non_reference_semantics": self.preserve_non_reference_semantics,
            "emit_repair_provenance": self.emit_repair_provenance,
        }


@dataclass(frozen=True, slots=True)
class ReferenceRepairResult:
    attempted: bool
    applied: bool
    replacement: str | None
    trace: dict[str, Any]


@dataclass(slots=True)
class ReferencePlanRepairOutcome:
    plan: dict[str, Any]
    traces: list[dict[str, Any]]

    @property
    def applied(self) -> bool:
        return any(bool(item.get("repair_applied")) for item in self.traces)


def _base_trace(
    *,
    raw_reference: Any,
    valid_references: Sequence[str],
    reference_kind: str,
    slot_path: str,
    config: ConstrainedReferenceRepairConfig,
    validation_before: str,
) -> dict[str, Any]:
    candidates = sorted(dict.fromkeys(str(item) for item in valid_references))
    return {
        "repair_attempted": False,
        "repair_applied": False,
        "repair_succeeded": False,
        "reference_kind": str(reference_kind),
        "slot_path": str(slot_path),
        "original_reference": str(raw_reference or ""),
        "replacement_reference": None,
        "candidate_set": candidates,
        "candidate_count": len(candidates),
        "repair_rule": "not_enabled" if not config.enabled else "none",
        "repair_reason": (
            "Stage2-F disabled" if not config.enabled else ""
        ),
        "validation_before": str(validation_before),
        "validation_after": str(validation_before),
        "max_attempts_per_slot": config.max_attempts_per_slot,
    }


def attempt_constrained_reference_repair(
    raw_reference: Any,
    valid_references: Sequence[str],
    *,
    reference_kind: str,
    slot_path: str,
    config: ConstrainedReferenceRepairConfig,
    named_references: Mapping[str, str] | None = None,
    validation_before: str,
) -> ReferenceRepairResult:
    """Select at most one replacement from a diagnostic-provided closed set.

    This helper never uses edit distance, fuzzy similarity, regeneration, gold
    state, downstream execution, or values. Missing slots are not synthesized,
    and already-valid references are not rewritten.
    """
    trace = _base_trace(
        raw_reference=raw_reference,
        valid_references=valid_references,
        reference_kind=reference_kind,
        slot_path=slot_path,
        config=config,
        validation_before=validation_before,
    )
    raw = str(raw_reference or "")
    candidates = list(trace["candidate_set"])

    if not config.enabled:
        return ReferenceRepairResult(False, False, None, trace)
    if raw in candidates:
        trace.update(
            repair_rule="already_valid_reference",
            repair_reason="the supplied reference is already valid",
            validation_after="PASS",
        )
        return ReferenceRepairResult(False, False, None, trace)
    if not raw.strip():
        trace.update(
            repair_attempted=False,
            repair_rule="missing_reference_not_repairable",
            repair_reason="Stage2-F repairs invalid non-empty references, not missing slots",
            validation_after="FAIL_CLOSED",
        )
        return ReferenceRepairResult(False, False, None, trace)

    trace["repair_attempted"] = True
    named = {
        str(reference): str(name)
        for reference, name in (named_references or {}).items()
        if str(reference) in candidates and name is not None
    }
    raw_name_key = _identifier_key(_suffix_identifier(raw))
    exact_matches = [
        reference
        for reference, name in named.items()
        if _identifier_key(name) == raw_name_key
    ]

    replacement: str | None = None
    if len(exact_matches) == 1:
        replacement = exact_matches[0]
        rule = "unique_exact_identifier_name"
        reason = "exact identifier name uniquely selects one closed-set reference"
    elif len(exact_matches) > 1:
        rule = "ambiguous_exact_identifier_name"
        reason = "exact identifier name matches more than one closed-set reference"
    elif len(candidates) == 1:
        replacement = candidates[0]
        rule = "unique_closed_set_candidate"
        reason = "the invalid slot has exactly one valid closed-set reference"
    elif not candidates:
        rule = "empty_closed_set"
        reason = "the invalid slot has no valid closed-set reference"
    else:
        rule = "ambiguous_closed_set"
        reason = "more than one valid closed-set reference remains"

    if replacement is None:
        trace.update(
            repair_rule=rule,
            repair_reason=reason,
            validation_after="FAIL_CLOSED",
        )
        return ReferenceRepairResult(True, False, None, trace)

    trace.update(
        repair_applied=True,
        replacement_reference=replacement,
        repair_rule=rule,
        repair_reason=reason,
        validation_after="PENDING_REVALIDATION",
    )
    return ReferenceRepairResult(True, True, replacement, trace)


def _protected_trace(
    diagnostic: Diagnostic,
    *,
    raw_reference: Any,
    reference_kind: str,
    config: ConstrainedReferenceRepairConfig,
    reason: str,
) -> dict[str, Any]:
    trace = _base_trace(
        raw_reference=raw_reference,
        valid_references=diagnostic.candidates,
        reference_kind=reference_kind,
        slot_path=diagnostic.path,
        config=config,
        validation_before=diagnostic.error_code,
    )
    if config.enabled:
        trace.update(
            repair_rule="protected_semantics_not_repairable",
            repair_reason=reason,
            validation_after="FAIL_CLOSED",
        )
    return trace


def _collection_for_group(
    group: Mapping[str, Any], payload: SourcePayload
) -> SourceCollection | None:
    reference = str(group.get("source_collection_id") or "")
    return next(
        (
            item
            for item in payload.collections
            if reference in {str(item.reference_id), str(item.collection_id)}
        ),
        None,
    )


def _table_for_group(
    group: Mapping[str, Any], profile: dict[str, Any]
) -> dict[str, Any] | None:
    return table_reference_map(profile).get(str(group.get("table_id") or ""))


def _replacement_slot_collision_trace(
    result: ReferenceRepairResult,
    *,
    semantic_alias: bool = False,
) -> dict[str, Any]:
    trace = deepcopy(result.trace)
    if semantic_alias:
        rule = "replacement_semantic_slot_collision"
        reason = (
            "replacement source-field reference resolves to a semantic source-field "
            "identity already represented by another key in the same mapping; Stage2-F "
            "fails closed instead of allowing resolver-level overwrite or alias collapse"
        )
    else:
        rule = "replacement_slot_collision"
        reason = (
            "replacement reference already exists in the same structural container; "
            "Stage2-F fails closed instead of overwriting or merging assignments"
        )
    trace.update(
        repair_applied=False,
        repair_succeeded=False,
        repair_rule=rule,
        repair_reason=reason,
        validation_after="FAIL_CLOSED",
    )
    return trace


def _source_field_identity(
    reference: Any,
    collection: SourceCollection,
) -> str | None:
    """Resolve a source-field name/ID exactly as the frozen resolver does."""
    raw = str(reference or "")
    fields_by_id = {
        str(field_id): str(field_name)
        for field_name, field_id in collection.field_ids.items()
    }
    candidate = fields_by_id.get(raw, raw)
    valid_fields = {str(field_name) for field_name in collection.fields}
    return candidate if candidate in valid_fields else None


def _target_assignment_collision(
    group: Mapping[str, Any],
    replacement: Any,
    *,
    current_mapping_source: str | None = None,
    current_constant_key: str | None = None,
) -> bool:
    """Return true when a replacement would duplicate a semi-structured target slot."""
    target = str(replacement or "")
    field_mapping = group.get("field_mapping") or {}
    for source_key, target_reference in field_mapping.items():
        if current_mapping_source is not None and source_key == current_mapping_source:
            continue
        if str(target_reference or "") == target:
            return True

    constants = group.get("constants") or {}
    for constant_key in constants:
        if current_constant_key is not None and constant_key == current_constant_key:
            continue
        if str(constant_key or "") == target:
            return True
    return False


def _target_assignment_collision_trace(
    result: ReferenceRepairResult,
) -> dict[str, Any]:
    trace = deepcopy(result.trace)
    trace.update(
        repair_applied=False,
        repair_succeeded=False,
        repair_rule="replacement_target_assignment_collision",
        repair_reason=(
            "replacement target-column reference is already assigned by another "
            "source mapping or constant in the same target group; Stage2-F fails "
            "closed instead of allowing materialization-time overwrite"
        ),
        validation_after="FAIL_CLOSED",
    )
    return trace


def _rollback_batch_after_collision(
    traces: Sequence[Mapping[str, Any]],
    collision_trace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw_trace in traces:
        trace = deepcopy(dict(raw_trace))
        if trace.get("repair_applied"):
            trace["repair_applied"] = False
            trace["repair_succeeded"] = False
            trace["validation_after"] = "FAIL_CLOSED"
            prior_reason = str(trace.get("repair_reason") or "").rstrip()
            trace["repair_reason"] = (
                prior_reason
                + ("; " if prior_reason else "")
                + "repair batch rolled back because another replacement would collide "
                "with an existing structural slot"
            )
        output.append(trace)
    output.append(deepcopy(dict(collision_trace)))
    return output


def repair_mapping_plan_after_diagnostics(
    mapping_plan: dict[str, Any],
    payload: SourcePayload,
    profile: dict[str, Any],
    diagnostics: Sequence[Diagnostic],
    config: ConstrainedReferenceRepairConfig,
) -> ReferencePlanRepairOutcome:
    """Repair only slots explicitly rejected by the frozen reference resolver.

    Conflict-target and update-column semantics are protected by frozen B/C and
    are therefore annotated but never repaired by F.
    """
    ensure_reference_ids(profile)
    plan = deepcopy(mapping_plan)
    groups = plan.get("target_groups") or []
    tables = table_reference_map(profile)
    traces: list[dict[str, Any]] = []

    for diagnostic in diagnostics:
        match = re.match(r"^/target_groups/([0-9]+)(?:/|$)", diagnostic.path)
        if match is None:
            continue
        index = int(match.group(1))
        if index >= len(groups) or not isinstance(groups[index], dict):
            continue
        group = groups[index]
        result: ReferenceRepairResult | None = None
        apply = None
        collision_check = None
        semantic_collision_check = None
        target_assignment_collision_check = None

        if diagnostic.error_code == "UNKNOWN_SOURCE_COLLECTION_ID":
            raw = str(group.get("source_collection_id") or "")
            result = attempt_constrained_reference_repair(
                raw,
                diagnostic.candidates,
                reference_kind="source_collection",
                slot_path=diagnostic.path,
                config=config,
                named_references={
                    str(item.reference_id): str(item.collection_id)
                    for item in payload.collections
                    if item.reference_id
                },
                validation_before=diagnostic.error_code,
            )
            apply = lambda value: group.__setitem__("source_collection_id", value)

        elif diagnostic.error_code == "UNKNOWN_SOURCE_SELECTOR_ID":
            raw = str(group.get("source_selector_id") or "")
            result = attempt_constrained_reference_repair(
                raw,
                diagnostic.candidates,
                reference_kind="source_selector",
                slot_path=diagnostic.path,
                config=config,
                validation_before=diagnostic.error_code,
            )
            apply = lambda value: group.__setitem__("source_selector_id", value)

        elif diagnostic.error_code == "UNKNOWN_TABLE_ID":
            raw = str(group.get("table_id") or "")
            result = attempt_constrained_reference_repair(
                raw,
                diagnostic.candidates,
                reference_kind="table",
                slot_path=diagnostic.path,
                config=config,
                named_references={
                    str(reference): str(table.get("name") or "")
                    for reference, table in tables.items()
                },
                validation_before=diagnostic.error_code,
            )
            apply = lambda value: group.__setitem__("table_id", value)

        elif diagnostic.error_code == "UNKNOWN_SOURCE_FIELD_ID":
            collection = _collection_for_group(group, payload)
            if collection is None or "/field_mapping/" not in diagnostic.path:
                continue
            raw_key = diagnostic.path.split("/field_mapping/", 1)[1]
            named = {
                str(field_id): str(field_name)
                for field_name, field_id in collection.field_ids.items()
            }
            result = attempt_constrained_reference_repair(
                raw_key,
                diagnostic.candidates,
                reference_kind="source_field",
                slot_path=diagnostic.path,
                config=config,
                named_references=named,
                validation_before=diagnostic.error_code,
            )

            def collision_check(value: str, *, old=raw_key, target=group) -> bool:
                field_mapping = target.get("field_mapping") or {}
                return value != old and value in field_mapping

            def semantic_collision_check(
                value: str,
                *,
                old=raw_key,
                target=group,
                source_collection=collection,
            ) -> bool:
                replacement_identity = _source_field_identity(value, source_collection)
                if replacement_identity is None:
                    return False
                field_mapping = target.get("field_mapping") or {}
                for existing_key in field_mapping:
                    if existing_key == old:
                        continue
                    if (
                        _source_field_identity(existing_key, source_collection)
                        == replacement_identity
                    ):
                        return True
                return False

            def apply(value: str, *, old=raw_key, target=group) -> None:
                field_mapping = target.get("field_mapping") or {}
                if old in field_mapping:
                    mapped_value = field_mapping.pop(old)
                    field_mapping[value] = mapped_value

        elif diagnostic.error_code == "UNKNOWN_COLUMN_ID":
            raw = str(
                diagnostic.details.get("predicted_column_id")
                or diagnostic.path.rsplit("/", 1)[-1]
            )
            if "/update_column_ids/" in diagnostic.path:
                traces.append(
                    _protected_trace(
                        diagnostic,
                        raw_reference=raw,
                        reference_kind="update_column",
                        config=config,
                        reason=(
                            "update-column semantics are frozen by Stage2-C and "
                            "are outside Stage2-F repair scope"
                        ),
                    )
                )
                continue
            table = _table_for_group(group, profile)
            if table is None:
                continue
            columns = column_reference_map(table)
            result = attempt_constrained_reference_repair(
                raw,
                diagnostic.candidates,
                reference_kind="column",
                slot_path=diagnostic.path,
                config=config,
                named_references={
                    str(reference): str(column.get("name") or "")
                    for reference, column in columns.items()
                },
                validation_before=diagnostic.error_code,
            )
            if "/field_mapping/" in diagnostic.path:
                source_key = diagnostic.path.split("/field_mapping/", 1)[1]

                def target_assignment_collision_check(
                    value: str,
                    *,
                    key=source_key,
                    target=group,
                ) -> bool:
                    return _target_assignment_collision(
                        target,
                        value,
                        current_mapping_source=key,
                    )

                apply = lambda value, key=source_key: group["field_mapping"].__setitem__(key, value)
            elif "/constants/" in diagnostic.path:
                old_key = diagnostic.path.split("/constants/", 1)[1]

                def collision_check(value: str, *, old=old_key, target=group) -> bool:
                    constants = target.get("constants") or {}
                    return value != old and value in constants

                def target_assignment_collision_check(
                    value: str,
                    *,
                    old=old_key,
                    target=group,
                ) -> bool:
                    return _target_assignment_collision(
                        target,
                        value,
                        current_constant_key=old,
                    )

                def apply(value: str, *, old=old_key, target=group) -> None:
                    constants = target.get("constants") or {}
                    if old in constants:
                        constant_value = constants.pop(old)
                        constants[value] = constant_value
            else:
                result = None

        elif diagnostic.error_code == "UNKNOWN_CONSTRAINT_ID":
            traces.append(
                _protected_trace(
                    diagnostic,
                    raw_reference=group.get("conflict_target_id"),
                    reference_kind="conflict_target",
                    config=config,
                    reason=(
                        "conflict-target semantics are frozen by Stage2-B and "
                        "are outside Stage2-F repair scope"
                    ),
                )
            )
            continue

        if result is None:
            continue
        replacement = result.replacement if result.applied else None
        if (
            replacement is not None
            and collision_check is not None
            and collision_check(replacement)
        ):
            collision_trace = _replacement_slot_collision_trace(result)
            return ReferencePlanRepairOutcome(
                deepcopy(mapping_plan),
                _rollback_batch_after_collision(traces, collision_trace),
            )
        if (
            replacement is not None
            and semantic_collision_check is not None
            and semantic_collision_check(replacement)
        ):
            collision_trace = _replacement_slot_collision_trace(
                result, semantic_alias=True
            )
            return ReferencePlanRepairOutcome(
                deepcopy(mapping_plan),
                _rollback_batch_after_collision(traces, collision_trace),
            )
        if (
            replacement is not None
            and target_assignment_collision_check is not None
            and target_assignment_collision_check(replacement)
        ):
            collision_trace = _target_assignment_collision_trace(result)
            return ReferencePlanRepairOutcome(
                deepcopy(mapping_plan),
                _rollback_batch_after_collision(traces, collision_trace),
            )
        traces.append(deepcopy(result.trace))
        if replacement is not None and apply is not None:
            apply(replacement)

    return ReferencePlanRepairOutcome(plan, traces)


def repair_free_text_plan_after_diagnostics(
    reference_plan: dict[str, Any],
    profile: dict[str, Any],
    diagnostics: Sequence[Diagnostic],
    config: ConstrainedReferenceRepairConfig,
) -> ReferencePlanRepairOutcome:
    """Repair only target table/column refs rejected by frozen materialization.

    Evidence selection and conflict semantics are intentionally not repairable in
    F because those are semantic choices rather than schema-reference spelling.
    """
    ensure_reference_ids(profile)
    plan = deepcopy(reference_plan)
    groups = plan.get("write_groups") or []
    tables = table_reference_map(profile)
    traces: list[dict[str, Any]] = []

    for diagnostic in diagnostics:
        match = re.match(r"^/write_groups/([0-9]+)(?:/|$)", diagnostic.path)
        if match is None:
            continue
        group_index = int(match.group(1))
        if group_index >= len(groups) or not isinstance(groups[group_index], dict):
            continue
        group = groups[group_index]
        result: ReferenceRepairResult | None = None
        apply = None
        collision_check = None

        if diagnostic.error_code == "UNKNOWN_TABLE_ID":
            raw = str(group.get("table_id") or "")
            result = attempt_constrained_reference_repair(
                raw,
                diagnostic.candidates,
                reference_kind="table",
                slot_path=diagnostic.path,
                config=config,
                named_references={
                    str(reference): str(table.get("name") or "")
                    for reference, table in tables.items()
                },
                validation_before=diagnostic.error_code,
            )
            apply = lambda value: group.__setitem__("table_id", value)

        elif diagnostic.error_code == "UNKNOWN_COLUMN_ID":
            table = tables.get(str(group.get("table_id") or ""))
            if table is None:
                continue
            row_match = re.match(
                r"^/write_groups/[0-9]+/rows/([0-9]+)/([^/]+)$",
                diagnostic.path,
            )
            if row_match is None:
                continue
            row_index = int(row_match.group(1))
            raw_key = row_match.group(2)
            rows = group.get("rows") or []
            if row_index >= len(rows) or raw_key not in rows[row_index]:
                continue
            columns = column_reference_map(table)
            result = attempt_constrained_reference_repair(
                raw_key,
                diagnostic.candidates,
                reference_kind="column",
                slot_path=diagnostic.path,
                config=config,
                named_references={
                    str(reference): str(column.get("name") or "")
                    for reference, column in columns.items()
                },
                validation_before=diagnostic.error_code,
            )

            def collision_check(value: str, *, row=rows[row_index], old=raw_key) -> bool:
                return value != old and value in row

            def apply(value: str, *, row=rows[row_index], old=raw_key) -> None:
                value_spec = row.pop(old)
                row[value] = value_spec

        elif diagnostic.error_code == "UNKNOWN_EVIDENCE_ID":
            row_match = re.match(
                r"^/write_groups/[0-9]+/rows/([0-9]+)/([^/]+)/value_from$",
                diagnostic.path,
            )
            raw = ""
            if row_match is not None:
                row_index = int(row_match.group(1))
                column_key = row_match.group(2)
                rows = group.get("rows") or []
                if row_index < len(rows):
                    value_spec = rows[row_index].get(column_key)
                    if isinstance(value_spec, dict):
                        raw = str(value_spec.get("value_from") or "")
            traces.append(
                _protected_trace(
                    diagnostic,
                    raw_reference=raw,
                    reference_kind="evidence",
                    config=config,
                    reason=(
                        "evidence selection is a semantic choice and is outside "
                        "Stage2-F reference repair scope"
                    ),
                )
            )
            continue

        elif diagnostic.error_code == "UNKNOWN_CONSTRAINT_ID":
            traces.append(
                _protected_trace(
                    diagnostic,
                    raw_reference=group.get("conflict_target_id"),
                    reference_kind="conflict_target",
                    config=config,
                    reason=(
                        "conflict-target semantics are frozen by Stage2-B and "
                        "are outside Stage2-F repair scope"
                    ),
                )
            )
            continue

        if result is None:
            continue
        replacement = result.replacement if result.applied else None
        if (
            replacement is not None
            and collision_check is not None
            and collision_check(replacement)
        ):
            collision_trace = _replacement_slot_collision_trace(result)
            return ReferencePlanRepairOutcome(
                deepcopy(reference_plan),
                _rollback_batch_after_collision(traces, collision_trace),
            )
        traces.append(deepcopy(result.trace))
        if replacement is not None and apply is not None:
            apply(replacement)

    return ReferencePlanRepairOutcome(plan, traces)


def annotate_reference_diagnostics(
    diagnostics: Sequence[Diagnostic],
    traces: Sequence[Mapping[str, Any]],
) -> list[Diagnostic]:
    """Attach the matching F trace to a frozen-boundary diagnostic copy."""
    by_key = {
        (str(trace.get("slot_path") or ""), str(trace.get("validation_before") or "")): trace
        for trace in traces
    }
    output: list[Diagnostic] = []
    for diagnostic in diagnostics:
        item = deepcopy(diagnostic)
        trace = by_key.get((item.path, item.error_code))
        if trace is not None:
            item.details = {
                **dict(item.details),
                "reference_repair": deepcopy(dict(trace)),
            }
        output.append(item)
    return output


def mark_revalidation_outcome(
    traces: Sequence[Mapping[str, Any]],
    diagnostics: Sequence[Diagnostic],
) -> list[dict[str, Any]]:
    """Finalize applied traces after the single frozen-boundary retry."""
    failed = {(item.path, item.error_code) for item in diagnostics}
    output: list[dict[str, Any]] = []
    for raw_trace in traces:
        trace = deepcopy(dict(raw_trace))
        if trace.get("repair_applied"):
            before = str(trace.get("validation_before") or "")
            slot = str(trace.get("slot_path") or "")
            if (slot, before) in failed:
                trace["repair_succeeded"] = False
                trace["validation_after"] = before
            else:
                trace["repair_succeeded"] = True
                trace["validation_after"] = "PASS"
        output.append(trace)
    return output


def repair_warnings_from_traces(
    traces: Sequence[Mapping[str, Any]],
) -> list[Diagnostic]:
    output: list[Diagnostic] = []
    for raw_trace in traces:
        trace = deepcopy(dict(raw_trace))
        if not trace.get("repair_applied"):
            continue
        output.append(
            Diagnostic(
                "CONSTRAINED_REFERENCE_REPAIR_APPLIED",
                (
                    f"Repaired invalid {trace.get('reference_kind')} reference "
                    f"{trace.get('original_reference')!r} to "
                    f"{trace.get('replacement_reference')!r} from a closed set."
                ),
                severity="warning",
                path=str(trace.get("slot_path") or ""),
                candidates=list(trace.get("candidate_set") or []),
                details=trace,
            )
        )
    return output


def attach_repair_trace(
    write_plan: dict[str, Any],
    traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attach F provenance without modifying materialized non-reference semantics."""
    output = deepcopy(write_plan)
    for key in ("target_groups", "write_groups"):
        groups = output.get(key)
        if not isinstance(groups, list):
            continue
        prefix = "/target_groups/" if key == "target_groups" else "/write_groups/"
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            group_traces = [
                deepcopy(dict(trace))
                for trace in traces
                if str(trace.get("slot_path") or "").startswith(f"{prefix}{index}/")
            ]
            if not group_traces:
                continue
            reference_trace = group.setdefault("reference_trace", {})
            reference_trace["constrained_reference_repairs"] = group_traces
    return output
