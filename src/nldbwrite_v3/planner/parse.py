from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from nldbwrite_v3.ir import Diagnostic, PlanParseResult
from nldbwrite_v3.planner.schema_validation import validate_json_schema


_JSON_FENCE = re.compile(
    r"```(?:json)?[ \t]*\r?\n?(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


@lru_cache(maxsize=4)
def _schema(plan_kind: str, reference_mode: bool = False) -> dict[str, Any]:
    if reference_mode:
        filename = (
            "mapping_plan_plus.schema.json"
            if plan_kind == "mapping"
            else "free_text_plan_plus.schema.json"
        )
    else:
        filename = (
            "mapping_plan.schema.json"
            if plan_kind == "mapping"
            else "write_plan.schema.json"
        )
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "schemas" / filename).read_text(encoding="utf-8"))


def _extract_json_object(raw_output: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    candidates = [match.group(1).strip() for match in _JSON_FENCE.finditer(raw_output)]
    candidates.append(raw_output.strip())
    decoder = json.JSONDecoder()
    last_error: str | None = None
    for candidate in candidates:
        if not candidate:
            continue
        starts = [index for index, char in enumerate(candidate) if char == "{"]
        if candidate.startswith("{"):
            starts = [0, *[index for index in starts if index != 0]]
        for start in starts:
            try:
                value, consumed = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError as exc:
                last_error = (
                    f"Malformed JSON at line {exc.lineno}, column {exc.colno}: "
                    f"{exc.msg}"
                )
                continue
            if isinstance(value, dict):
                extracted = candidate[start : start + consumed]
                return value, extracted, None
            last_error = "Top-level generated JSON must be an object."
    return None, None, last_error or "No JSON object found in model output."


def validate_plan_object(
    plan: dict[str, Any],
    plan_kind: Literal["mapping", "free_text"],
    *,
    reference_mode: bool = False,
) -> list[Diagnostic]:
    diagnostics = [
        Diagnostic(
            "SCHEMA_ERROR",
            issue.message,
            path=issue.path,
        )
        for issue in validate_json_schema(
            plan,
            _schema(plan_kind, reference_mode),
        )
    ]
    groups_key = "target_groups" if plan_kind == "mapping" else "write_groups"
    groups = plan.get(groups_key)
    if isinstance(groups, list):
        seen: set[str] = set()
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            group_id = str(group.get("group_id") or "")
            if group_id and group_id in seen:
                diagnostics.append(
                    Diagnostic(
                        "DUPLICATE_GROUP_ID",
                        f"Duplicate group_id {group_id!r}.",
                        path=f"/{groups_key}/{index}/group_id",
                        group_id=group_id,
                    )
                )
            seen.add(group_id)
    return diagnostics


def _normalize_compatible_plan(
    plan: dict[str, Any],
    plan_kind: Literal["mapping", "free_text"],
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Canonicalize unambiguous surface aliases without guessing semantics."""
    normalized = deepcopy(plan)
    diagnostics: list[Diagnostic] = []
    groups_key = "target_groups" if plan_kind == "mapping" else "write_groups"
    groups = normalized.get(groups_key)
    if isinstance(groups, list):
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            action = re.sub(
                r"[\s-]+",
                "_",
                str(group.get("action") or "").strip().casefold(),
            )
            conflict = group.get("conflict")
            conflict_action = (
                str(conflict.get("action") or "").casefold()
                if isinstance(conflict, dict)
                else ""
            )
            if (
                action
                in {
                    "insert_or_update",
                    "insert_or_replace",
                    "upsert",
                }
                and conflict_action in {"do_nothing", "do_update"}
            ):
                group["action"] = "insert"
                diagnostics.append(
                    Diagnostic(
                        "NORMALIZED_WRITE_ACTION_ALIAS",
                        (
                            f"Normalized write action {action!r} to 'insert'; "
                            "conflict semantics remain explicit."
                        ),
                        severity="warning",
                        path=f"/{groups_key}/{index}/action",
                        group_id=str(group.get("group_id") or ""),
                    )
                )

    dependencies = normalized.get("dependencies")
    if isinstance(dependencies, list):
        if plan_kind == "mapping" and isinstance(groups, list):
            remaining_dependencies: list[Any] = []
            for dependency in dependencies:
                if (
                    isinstance(dependency, dict)
                    and {
                        "group_id",
                        "source_collection",
                        "table",
                        "source_rows",
                        "field_mapping",
                        "constants",
                        "action",
                        "conflict",
                    }
                    <= dependency.keys()
                ):
                    groups.append(dependency)
                    diagnostics.append(
                        Diagnostic(
                            "RECOVERED_MISPLACED_TARGET_GROUP",
                            (
                                "Moved an unambiguous target-group object "
                                "out of dependencies."
                            ),
                            severity="warning",
                            path="/dependencies",
                            group_id=str(
                                dependency.get("group_id") or ""
                            ),
                        )
                    )
                else:
                    remaining_dependencies.append(dependency)
            dependencies = remaining_dependencies
            normalized["dependencies"] = dependencies
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, dict):
                continue
            if "before" in dependency or "after" in dependency:
                continue
            before: Any = None
            after: Any = None
            alias_keys: set[str] = set()
            if {
                "parent_group_id",
                "child_group_id",
            } <= dependency.keys():
                before = dependency.get("parent_group_id")
                after = dependency.get("child_group_id")
                alias_keys.update(
                    {"parent_group_id", "child_group_id"}
                )
            elif {
                "depends_on_group_id",
                "dependent_group_id",
            } <= dependency.keys():
                before = dependency.get("depends_on_group_id")
                after = dependency.get("dependent_group_id")
                alias_keys.update(
                    {"depends_on_group_id", "dependent_group_id"}
                )
            elif {
                "group_id",
                "depends_on_group_id",
            } <= dependency.keys():
                before = dependency.get("depends_on_group_id")
                after = dependency.get("group_id")
                alias_keys.update(
                    {"group_id", "depends_on_group_id"}
                )
            if before is None or after is None:
                continue
            canonical: dict[str, Any] = {}
            canonical["before"] = before
            canonical["after"] = after
            if "on" in dependency:
                canonical["foreign_key"] = {
                    "on": deepcopy(dependency["on"])
                }
            elif "constraint" in dependency:
                canonical["foreign_key"] = {
                    "constraint": deepcopy(
                        dependency["constraint"]
                    )
                }
            dependencies[index] = canonical
            diagnostics.append(
                Diagnostic(
                    "NORMALIZED_DEPENDENCY_ALIAS",
                    "Normalized dependency aliases to before/after.",
                    severity="warning",
                    path=f"/dependencies/{index}",
                )
            )
    return normalized, diagnostics


def parse_llm_plan(
    raw_output: str,
    *,
    plan_kind: Literal["mapping", "free_text"],
    reference_mode: bool = False,
) -> PlanParseResult:
    plan, extracted, parse_error = _extract_json_object(raw_output)
    if plan is None:
        return PlanParseResult(
            "json_error",
            None,
            [
                Diagnostic(
                    "JSON_PARSE_ERROR",
                    parse_error or "Could not parse generated JSON.",
                    path="/",
                )
            ],
            extracted,
        )
    normalized_plan, normalization_diagnostics = _normalize_compatible_plan(
        plan,
        plan_kind,
    )
    schema_diagnostics = validate_plan_object(
        normalized_plan,
        plan_kind,
        reference_mode=reference_mode,
    )
    if schema_diagnostics:
        return PlanParseResult(
            "schema_error",
            None,
            [*normalization_diagnostics, *schema_diagnostics],
            extracted,
        )
    return PlanParseResult(
        "success",
        normalized_plan,
        normalization_diagnostics,
        extracted,
    )
