from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, iter_jsonl
from nldbwrite_v3.experiments.metrics import error_taxonomy_row, summarize_run


def evaluate_saved_run(
    evaluation_path: str | Path,
    metrics_output: str | Path,
    error_output: str | Path,
) -> dict[str, Any]:
    """Recompute aggregate metrics and the error taxonomy from saved rows."""
    rows = list(iter_jsonl(evaluation_path))
    metrics = summarize_run(rows)
    dump_json(metrics, metrics_output)
    errors = [error_taxonomy_row(row) for row in rows]
    target = Path(error_output)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = list(errors[0]) if errors else [
        "sample_id",
        "db_id",
        "method",
        "error_category",
        "error_type",
        "error_message",
        "detected_mode",
        "detected_format",
    ]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(errors)
    return metrics
