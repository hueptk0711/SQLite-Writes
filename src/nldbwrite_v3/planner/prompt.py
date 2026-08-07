from __future__ import annotations

import json
from typing import Any

from nldbwrite_v3.ir import SourcePayload
from nldbwrite_v3.schema import ensure_reference_ids, serialize_prompt_schema

from .evidence import extract_evidence_candidates
from .grounding import collection_grounding


def _compact_schema(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return serialize_prompt_schema(profile)


def _reference_schema_for_collections(
    profile: dict[str, Any],
    collections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Narrow only when every collection has an unambiguous table hint."""
    schema = _compact_schema(profile)
    hinted_tables = {
        str(
            collection.get("table_hint")
            or collection.get("exact_table_hint")
            or ""
        )
        for collection in collections
    }
    if not collections or "" in hinted_tables:
        return schema
    selected = [
        table
        for table in schema
        if str(table.get("table") or "") in hinted_tables
    ]
    return selected if len(selected) == len(hinted_tables) else schema


def _demonstration_text(
    config: dict[str, Any] | None,
    mode: str,
) -> str:
    if not config:
        return ""
    demonstrations = config.get("demonstrations") or {}
    if isinstance(demonstrations, dict):
        demonstrations = demonstrations.get(mode) or []
    if not isinstance(demonstrations, list):
        return ""
    blocks = []
    for index, demonstration in enumerate(demonstrations, start=1):
        if not isinstance(demonstration, dict):
            continue
        blocks.append(
            f"EXAMPLE {index} INPUT:\n{demonstration.get('input', '')}\n"
            f"EXAMPLE {index} OUTPUT:\n{demonstration.get('output', '')}"
        )
    return "\n\n".join(blocks) + "\n\n" if blocks else ""


def _mapping_contract() -> dict[str, Any]:
    return {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection": "exact_collection_id",
                "table": "exact_schema_table",
                "source_rows": "exact_source_path",
                "field_mapping": {"source field": "ExactTargetColumn"},
                "constants": {
                    "TargetColumn": {
                        "value": "value explicitly stated once in the instruction",
                        "evidence": {
                            "source": "instruction_text",
                            "exact_span": "verbatim supporting instruction span",
                        },
                    }
                },
                "action": "insert",
                "conflict": {
                    "action": "error | do_nothing | do_update",
                    "target": [],
                    "update_columns": [],
                },
            }
        ],
        "dependencies": [],
        "ignored_fields": {
            "collection_id": {"source field": "reason"}
        },
    }


def _free_text_contract() -> dict[str, Any]:
    return {
        "version": "3.0",
        "plan_kind": "free_text_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table": "exact_schema_table",
                "action": "insert",
                "rows": [{"ExactTargetColumn": "value from request"}],
                "value_evidence": [
                    {
                        "ExactTargetColumn": {
                            "source": "instruction_text",
                            "exact_span": "verbatim text that supports the value",
                        }
                    }
                ],
                "conflict": {
                    "action": "error | do_nothing | do_update",
                    "target": [],
                    "update_columns": [],
                },
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }


def _mapping_reference_contract() -> dict[str, Any]:
    return {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection_id": "c1",
                "source_selector_id": "s1",
                "table_id": "t1",
                "field_mapping": {"c1.f1": "t1.c1"},
                "constants": {},
                "write_semantics": (
                    "plain_insert | insert_ignore | upsert_update | "
                    "needs_clarification"
                ),
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "ignored_fields": {"c1": {"c1.f3": "reason"}},
    }


def _free_text_reference_contract() -> dict[str, Any]:
    return {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": "t1",
                "rows": [
                    {
                        "t1.c1": {
                            "value_from": "e1",
                            "normalization": "identity",
                        }
                    }
                ],
                "write_semantics": (
                    "plain_insert | insert_ignore | upsert_update | "
                    "needs_clarification"
                ),
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }


def build_mapping_prompt(
    payload: SourcePayload,
    profile: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    reference_planning = bool((config or {}).get("reference_planning"))
    ensure_reference_ids(profile)
    collections = []
    source_fields: set[str] = set()
    for collection in payload.collections:
        grounding = collection_grounding(
            payload,
            collection,
            profile,
        )
        collections.append(
            {
            "collection_id": (
                collection.reference_id
                if reference_planning
                else collection.collection_id
            ),
            "selector_id": collection.selector_id,
            "source_path": collection.source_path,
            "format": collection.source_format,
            "row_count": len(collection.rows),
            "fields": (
                [
                    {
                        "field_id": collection.field_ids[field],
                        "name": field,
                    }
                    for field in collection.fields
                ]
                if reference_planning
                else collection.fields
            ),
                **(
                    {
                        "control_metadata": collection.metadata.get(
                            "control_metadata"
                        )
                    }
                    if collection.metadata.get("control_metadata")
                    and any(
                        collection.metadata.get("control_metadata") or []
                    )
                    else {}
                ),
                **grounding,
            }
        )
        source_fields.update(str(field) for field in collection.fields)
    max_target_groups = min(
        12,
        max(
            len(payload.collections),
            len(payload.collections) + len(source_fields),
        ),
    )
    max_dependencies = max(0, max_target_groups - 1)
    if reference_planning:
        reference_schema = _reference_schema_for_collections(
            profile,
            collections,
        )
        default_policy = str(
            (config or {}).get("conflict_default_policy")
            or "needs_clarification"
        )
        policy_rule = (
            "Use insert_ignore when duplicate behavior is not explicit, "
            "unless the request asks to update or fail."
            if default_policy == "insert_ignore"
            else (
                "Use plain_insert when duplicate behavior is not explicit."
                if default_policy == "plain_insert"
                else (
                    "Use needs_clarification when duplicate behavior is not "
                    "explicit and the system-level task definition supplies "
                    "no default."
                )
            )
        )
        return (
            "Return JSON only. Predict an MP-FS+ reference Mapping Plan.\n"
            "The source values are withheld. Select only enumerated IDs; never "
            "write a JSONPath, table name, column name, or constraint columns.\n"
            "Rules:\n"
            "1. Copy source_collection_id, source_selector_id, table_id, "
            "field IDs, column IDs, and constraint IDs exactly from the "
            "enumerated candidates.\n"
            "2. Each field_mapping value must be one listed candidate column "
            "ID; choose NONE only by placing the source field in ignored_fields "
            "with a reason.\n"
            "3. Map every source field or justify it exactly as "
            "ignored_fields[collection_id][field_id]=reason. Treat control "
            "phrases accidentally parsed as fields (for example duplicate or "
            "update instructions) as ignored metadata, not payload columns.\n"
            "4. Never invent values, identifiers, selectors, or defaults.\n"
            "5. Use write_semantics=plain_insert, insert_ignore, "
            "upsert_update, or needs_clarification.\n"
            f"6. {policy_rule}\n"
            "7. insert_ignore and upsert_update require a listed "
            "conflict_target_id; upsert_update also requires supplied non-key "
            "update_column_ids.\n"
            "8. Emit at least one target group per non-ignored collection.\n"
            "For each emitted group, copy every data field listed for that "
            "collection into field_mapping; do not stop after the first few "
            "fields or collections. A table_hint is deterministic and must "
            "be followed when present. An exact_table_hint is a unique "
            "all-fields identifier match and must also be followed.\n"
            "9. Dependencies use exactly "
            '{"before":"parent_group_id","after":"child_group_id"}. '
            "Do not repeat dependencies.\n"
            "10. Emit no more than "
            f"{max_target_groups} target_groups and {max_dependencies} "
            "dependencies. Close JSON immediately.\n\n"
            + _demonstration_text(config, "semi_structured")
            + f"INSTRUCTION:\n{payload.instruction_text}\n\n"
            "ENUMERATED SOURCE COLLECTIONS (metadata only):\n"
            f"{json.dumps(collections, ensure_ascii=False)}\n\n"
            "ENUMERATED SCHEMA:\n"
            f"{json.dumps(reference_schema, ensure_ascii=False)}\n\n"
            "OUTPUT CONTRACT:\n"
            f"{json.dumps(_mapping_reference_contract(), ensure_ascii=False)}"
        )
    return (
        "Return JSON only. Predict a schema-grounded Mapping Plan.\n"
        "The source cell values are intentionally withheld. Your task is only "
        "schema mapping and write-policy prediction.\n"
        "Rules:\n"
        "1. Use only exact table and column names from SCHEMA.\n"
        "2. Copy source_collection and source_rows exactly from SOURCE "
        "COLLECTIONS; never invent a selector.\n"
        "3. Map every source field or put it in ignored_fields with a reason.\n"
        "4. Never invent values or defaults.\n"
        "5. A global constant requires verbatim instruction evidence, or a "
        "real schema_default.\n"
        "6. action is always insert. Express duplicate handling only through "
        "conflict.action.\n"
        "7. If the instruction says update, refresh, replace, or overwrite "
        "existing/conflicting rows, use do_update with a real PK/UNIQUE "
        "target and every supplied non-key column in update_columns. If it "
        "says ignore or skip duplicates, use do_nothing. Otherwise use error.\n"
        "8. Honor table_hint when present. Otherwise prefer candidate_tables "
        "with the strongest exact_identifier_matches and instruction intent; "
        "do not select a table that makes most source fields unknown.\n"
        "9. metadata_fields are not payload columns; justify them in "
        "ignored_fields instead of mapping them.\n"
        "10. Emit at least one target group for every source collection "
        "unless every field in that collection is explicitly ignored.\n"
        "11. Dependencies must use exactly "
        '{"before":"parent_group_id","after":"child_group_id"}'
        ".\n"
        "12. Cardinality guard: emit no more than "
        f"{max_target_groups} target_groups and no more than "
        f"{max_dependencies} dependencies. Dependencies may reference only "
        "group_id values actually present in target_groups. Never generate "
        "a numbered dependency chain or repeat a dependency. Close the JSON "
        "immediately after the final required field.\n\n"
        + _demonstration_text(config, "semi_structured")
        + f"INSTRUCTION:\n{payload.instruction_text}\n\n"
        f"SOURCE COLLECTIONS (metadata only):\n"
        f"{json.dumps(collections, ensure_ascii=False)}\n\n"
        f"SCHEMA:\n{json.dumps(_compact_schema(profile), ensure_ascii=False)}\n\n"
        f"OUTPUT CONTRACT:\n"
        f"{json.dumps(_mapping_contract(), ensure_ascii=False)}"
    )


def build_free_text_prompt(
    request: str,
    profile: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    reference_planning = bool((config or {}).get("reference_planning"))
    ensure_reference_ids(profile)
    if reference_planning:
        evidence = extract_evidence_candidates(request)
        default_policy = str(
            (config or {}).get("conflict_default_policy")
            or "needs_clarification"
        )
        return (
            "Return JSON only. Predict an MP-FS+ reference Write Plan.\n"
            "Select only enumerated table, column, constraint, and evidence "
            "IDs. Never write a database identifier or extracted value.\n"
            "Rules:\n"
            "1. Every cell is {value_from:evidence_id, normalization:rule}.\n"
            "2. Allowed normalization rules are identity, "
            "lossless_integer_parsing, decimal_parsing, "
            "remove_thousands_separator, iso_date_normalization, "
            "boolean_mapping, and trim_surrounding_quotes.\n"
            "3. Use identity unless a declared transformation is necessary. "
            "Evidence text is exactly the displayed text: quoted_text "
            "candidates already exclude their surrounding quotes, so use "
            "identity for them. Use trim_surrounding_quotes only when the "
            "displayed evidence text visibly starts and ends with one matching "
            "quote pair.\n"
            "For an INTEGER column use lossless_integer_parsing for a plain "
            "integer and remove_thousands_separator for a comma-grouped "
            "integer. For a REAL/DECIMAL column use decimal_parsing. Keep "
            "already-ISO dates and ordinary text as identity. If "
            "semantic_type is identifier, code, date_key, or another "
            "text-preserving type, use identity even when the declared SQL "
            "type contains INTEGER. Do not choose a leading-zero component "
            "when the complete requested number is available.\n"
            "4. Use only enumerated table_id, column IDs, constraint IDs, and "
            "evidence IDs.\n"
            "5. write_semantics is plain_insert, insert_ignore, "
            "upsert_update, or needs_clarification.\n"
            "6. insert_ignore/upsert_update require conflict_target_id; "
            "upsert_update requires update_column_ids.\n"
            "7. Resolve explicit duplicate language first: fail/error means "
            "plain_insert; ignore/skip means insert_ignore; update/replace/"
            "overwrite means upsert_update. For plain_insert use a null "
            "conflict_target_id and empty update_column_ids.\n"
            f"8. System conflict default: {default_policy}. If the request "
            "does not determine duplicate behavior, apply this default.\n"
            "9. Match requested field meaning to the enumerated column name. "
            "Do not select a surrogate primary-key column merely because it "
            "is a key, and do not populate an absent auto-generated ID.\n"
            "10. Use literal evidence true/false for boolean columns and the "
            "complete date candidate for date columns. Never reuse one "
            "evidence ID for different requested values.\n"
            "11. Prefer evidence candidates with candidate_role=primary. "
            "A component candidate is only a compatibility fallback when "
            "the requested value is precisely that component rather than "
            "its enclosing primary span. Use left_context to align each "
            "evidence ID with the requested field.\n\n"
            + _demonstration_text(config, "free_text")
            + f"REQUEST:\n{request}\n\n"
            "EVIDENCE CANDIDATES:\n"
            f"{json.dumps(evidence, ensure_ascii=False)}\n\n"
            "ENUMERATED SCHEMA:\n"
            f"{json.dumps(_compact_schema(profile), ensure_ascii=False)}\n\n"
            "OUTPUT CONTRACT:\n"
            f"{json.dumps(_free_text_reference_contract(), ensure_ascii=False)}"
        )
    return (
        "Return JSON only. Extract a compact, schema-grounded Write Plan from "
        "the natural-language request.\n"
        "Rules:\n"
        "1. Use only exact table and column names from SCHEMA.\n"
        "2. Do not infer a value that is absent from the request or a declared "
        "schema default.\n"
        "3. Every extracted cell requires a verbatim exact_span from REQUEST.\n"
        "4. Keep insert action separate from conflict action, target, and "
        "update-column mask.\n"
        "5. Express parent-before-child dependencies explicitly.\n\n"
        + _demonstration_text(config, "free_text")
        + f"REQUEST:\n{request}\n\n"
        f"SCHEMA:\n{json.dumps(_compact_schema(profile), ensure_ascii=False)}\n\n"
        f"OUTPUT CONTRACT:\n"
        f"{json.dumps(_free_text_contract(), ensure_ascii=False)}"
    )


def build_planner_prompt(
    request: str,
    payload: SourcePayload,
    profile: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    if payload.mode == "semi_structured":
        return build_mapping_prompt(payload, profile, config)
    return build_free_text_prompt(request, profile, config)
