from __future__ import annotations

import json
from collections import Counter
from typing import Any

from nldbwrite_v3.schema import serialize_prompt_schema


def compact_schema(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible name for the common prompt schema."""
    return serialize_prompt_schema(profile)


def compact_verifier_errors(
    verifier_errors: list[dict[str, Any]],
    *,
    max_groups: int = 64,
    max_example_paths: int = 3,
    max_example_row_indices: int = 5,
) -> dict[str, Any]:
    """Deduplicate repeated row-level diagnostics for the repair prompt."""

    def group_key(error: dict[str, Any]) -> tuple[Any, ...]:
        code = error.get("error_code")
        details = error.get("details") or {}
        common = (code, error.get("group_id"), error.get("table"))

        if code == "UNKNOWN_COLUMN":
            return common + (details.get("predicted_column"),)
        if code == "UNRESOLVED_SOURCE_FIELD":
            return common + (
                error.get("message"),
                details.get("source_collection"),
            )

        stable_details = {
            key: value
            for key, value in details.items()
            if key not in {"source_row_index", "row_index"}
        }
        return common + (
            error.get("message"),
            json.dumps(stable_details, ensure_ascii=False, sort_keys=True),
            json.dumps(error.get("candidates") or [], ensure_ascii=False),
        )

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    code_counts: Counter[str] = Counter()

    for error in verifier_errors:
        code_counts[str(error.get("error_code") or "UNKNOWN")] += 1
        key = group_key(error)
        details = dict(error.get("details") or {})
        row_index = details.pop("source_row_index", details.pop("row_index", None))

        if key not in grouped:
            item = {
                field: error[field]
                for field in (
                    "error_code",
                    "message",
                    "severity",
                    "group_id",
                    "table",
                    "candidates",
                )
                if error.get(field) not in (None, [], {})
            }
            if details:
                item["details"] = details
            item["occurrences"] = 0
            item["example_paths"] = []
            item["example_source_row_indices"] = []
            grouped[key] = item

        item = grouped[key]
        item["occurrences"] += 1
        path = error.get("path")
        if (
            path
            and path not in item["example_paths"]
            and len(item["example_paths"]) < max_example_paths
        ):
            item["example_paths"].append(path)
        if (
            row_index is not None
            and row_index not in item["example_source_row_indices"]
            and len(item["example_source_row_indices"]) < max_example_row_indices
        ):
            item["example_source_row_indices"].append(row_index)

    all_groups = list(grouped.values())
    for item in all_groups:
        if not item["example_paths"]:
            item.pop("example_paths")
        if not item["example_source_row_indices"]:
            item.pop("example_source_row_indices")

    kept_groups = all_groups[:max_groups]
    return {
        "original_error_count": len(verifier_errors),
        "error_code_counts": dict(code_counts),
        "distinct_group_count": len(all_groups),
        "omitted_group_count": max(0, len(all_groups) - len(kept_groups)),
        "groups": kept_groups,
    }


def _demonstration_text(config: dict[str, Any]) -> str:
    demonstrations = config.get("demonstrations")
    if isinstance(demonstrations, dict):
        demonstrations = demonstrations.get("free_text") or []
    if not isinstance(demonstrations, list) or not demonstrations:
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


def build_direct_prompt(
    request: str,
    profile: dict[str, Any],
    config: dict[str, Any],
) -> str:
    return (
        "Generate SQLite INSERT statements only. Use only the supplied schema, "
        "preserve every requested value, and implement the requested conflict "
        "behavior exactly. List the INSERT columns explicitly, and make every "
        "VALUES tuple contain exactly one value per listed column. Emit each "
        "requested source row exactly once; never duplicate rows or continue a "
        "tuple beyond its listed columns. End immediately after the final SQL "
        "semicolon. Do not emit explanations.\n\n"
        + _demonstration_text(config)
        + f"SCHEMA:\n{json.dumps(compact_schema(profile), ensure_ascii=False)}\n\n"
        + f"REQUEST:\n{request}"
    )


def build_legacy_json_prompt(
    request: str,
    profile: dict[str, Any],
    config: dict[str, Any],
) -> str:
    matched = config.get("demonstration_policy") == "matched_semantic_bank_v1"
    contract = {
        "records": [
            {
                "table": "table_name",
                "operation": (
                    "insert | insert_ignore | upsert_update"
                    if matched
                    else "insert | upsert"
                ),
                "values": {"column": "value"},
            }
        ]
    }
    return (
        "Return JSON only. Extract database records using exact schema names. "
        "Do not invent values. Express duplicate behavior explicitly as "
        "insert, insert_ignore, or upsert_update.\n\n"
        + _demonstration_text(config)
        + f"SCHEMA:\n{json.dumps(compact_schema(profile), ensure_ascii=False)}\n\n"
        + f"REQUEST:\n{request}\n\n"
        + f"OUTPUT CONTRACT:\n{json.dumps(contract, ensure_ascii=False)}"
    )


def build_repair_prompt(
    instruction_text: str,
    source_metadata: list[dict[str, Any]],
    profile: dict[str, Any],
    current_mapping: dict[str, Any],
    verifier_errors: list[dict[str, Any]],
) -> str:
    compact_errors = compact_verifier_errors(verifier_errors)
    return (
        "Return JSON only with a minimal RFC-6902 patches array. Repair only "
        "the Mapping Plan. Do not emit or modify source rows or source values. "
        "Every patch must include a concise reason.\n\n"
        f"INSTRUCTION:\n{instruction_text}\n\n"
        f"SOURCE METADATA:\n{json.dumps(source_metadata, ensure_ascii=False)}\n\n"
        f"SCHEMA:\n{json.dumps(compact_schema(profile), ensure_ascii=False)}\n\n"
        f"CURRENT MAPPING:\n{json.dumps(current_mapping, ensure_ascii=False)}\n\n"
        "VERIFIER ERROR SUMMARY (repeated row-level errors are grouped):\n"
        f"{json.dumps(compact_errors, ensure_ascii=False)}\n\n"
        'OUTPUT: {"patches":[{"op":"replace","path":"/target_groups/0/'
        'field_mapping/source field","value":"ExactColumn","reason":"..."}]}'
    )
