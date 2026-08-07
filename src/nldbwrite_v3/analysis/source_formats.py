from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, load_json, read_ids
from nldbwrite_v3.source_parser import parse_source_payload


def _expected_mode(input_type: str) -> str | None:
    if input_type in {"json_like", "table_markdown"}:
        return "semi_structured"
    if input_type == "natural_language":
        return "free_text"
    return None


def _format_proxy_correct(input_type: str, detected_format: str) -> bool | None:
    if input_type == "json_like":
        return "json" in detected_format or detected_format == "mixed"
    if input_type == "table_markdown":
        return detected_format in {"markdown_table", "multi_table", "mixed"}
    if input_type == "natural_language":
        return detected_format == "free_text"
    return None


def _raw_json_collection_count(text: str) -> int:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and all(isinstance(row, dict) for row in value):
            return 1
        if isinstance(value, dict):
            count = sum(
                isinstance(nested, list)
                and all(isinstance(row, dict) for row in nested)
                for nested in value.values()
            )
            return int(count or 1)
    return 0


def _gold_row_count(sample: dict[str, Any]) -> int:
    # In the supplied augmentation schema ``num_records`` is the number of
    # source records, while ``gold_records`` can contain one target record per
    # table and therefore over-count relational fan-out.
    if sample.get("num_records") is not None:
        return int(sample["num_records"])
    records = sample.get("gold_records")
    if isinstance(records, list):
        return len(records)
    return int(sample.get("row_count") or 0)


def analyze_source_formats(
    dataset_path: str | Path,
    output_csv: str | Path,
    output_summary: str | Path,
    *,
    ids_path: str | Path | None = None,
) -> dict[str, Any]:
    selected = set(read_ids(ids_path)) if ids_path else None
    samples = [
        sample
        for sample in load_json(dataset_path)
        if selected is None or str(sample["id"]) in selected
    ]
    rows: list[dict[str, Any]] = []
    for sample in samples:
        payload = parse_source_payload(str(sample.get("input_text") or ""))
        gold_rows = _gold_row_count(sample)
        parsed_rows = len(payload.rows)
        raw_collection_count = _raw_json_collection_count(payload.raw_text)
        expected_mode = _expected_mode(str(sample.get("input_type") or ""))
        format_correct = _format_proxy_correct(
            str(sample.get("input_type") or ""),
            payload.source_format,
        )
        rows.append(
            {
                "sample_id": sample.get("id"),
                "db_id": sample.get("db_id"),
                "annotated_input_type": sample.get("input_type"),
                "expected_mode_proxy": expected_mode,
                "detected_mode": payload.mode,
                "detected_format": payload.source_format,
                "number_of_collections": len(payload.collections),
                "raw_json_collection_count": raw_collection_count,
                "parsed_row_count": parsed_rows,
                "gold_row_count": gold_rows,
                "row_count_match": parsed_rows == gold_rows,
                "parsed_field_count": sum(
                    len(collection.fields)
                    for collection in payload.collections
                ),
                "multi_block_detected": len(payload.collections) > 1,
                "collection_loss_detected": (
                    raw_collection_count > len(payload.collections)
                ),
                "mode_proxy_correct": (
                    payload.mode == expected_mode
                    if expected_mode is not None
                    else None
                ),
                "format_proxy_correct": format_correct,
            }
        )
    target = Path(output_csv)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    semi = [row for row in rows if row["detected_mode"] == "semi_structured"]
    proxy_mode = [row for row in rows if row["mode_proxy_correct"] is not None]
    proxy_format = [
        row for row in rows if row["format_proxy_correct"] is not None
    ]
    multi_json = [
        row for row in rows if int(row["raw_json_collection_count"]) > 1
    ]
    summary = {
        "samples": total,
        "detected_modes": dict(Counter(row["detected_mode"] for row in rows)),
        "detected_formats": dict(
            Counter(row["detected_format"] for row in rows)
        ),
        "semi_structured_rate": len(semi) / total if total else 0.0,
        "free_text_rate": 1.0 - (len(semi) / total if total else 0.0),
        "row_count_exact_accuracy_all": (
            sum(bool(row["row_count_match"]) for row in rows) / total
            if total
            else 0.0
        ),
        "row_count_exact_accuracy_semi_structured": (
            sum(bool(row["row_count_match"]) for row in semi) / len(semi)
            if semi
            else 0.0
        ),
        "mode_detection_proxy_accuracy": (
            sum(bool(row["mode_proxy_correct"]) for row in proxy_mode)
            / len(proxy_mode)
            if proxy_mode
            else None
        ),
        "format_detection_proxy_accuracy": (
            sum(bool(row["format_proxy_correct"]) for row in proxy_format)
            / len(proxy_format)
            if proxy_format
            else None
        ),
        "multi_json_samples": len(multi_json),
        "multi_collection_coverage": (
            sum(
                int(row["number_of_collections"])
                >= int(row["raw_json_collection_count"])
                for row in multi_json
            )
            / len(multi_json)
            if multi_json
            else 1.0
        ),
        "samples_with_detected_collection_loss": sum(
            bool(row["collection_loss_detected"]) for row in rows
        ),
        "field_set_f1": None,
        "field_set_f1_note": (
            "Requires source-field gold annotations; target gold columns are "
            "not a valid substitute for source field names."
        ),
    }
    dump_json(summary, output_summary)
    return summary
