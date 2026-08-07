from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, load_json, read_ids
from nldbwrite_v3.data.gold_sql import parse_gold_sql
from nldbwrite_v3.source_parser import parse_source_payload


def _sample_features(sample: dict[str, Any]) -> set[str]:
    payload = parse_source_payload(str(sample.get("input_text") or ""))
    plan = parse_gold_sql(
        list(sample.get("gold_sql") or []),
        sample_id=str(sample["id"]),
    )
    actions = {
        str(group.get("conflict", {}).get("action"))
        for group in plan.get("write_groups") or []
    }
    source_rows = int(
        sample.get("num_records")
        or len(sample.get("gold_records") or [])
    )
    table_count = int(
        sample.get("table_count")
        or len(sample.get("gold_tables") or [])
    )
    return {
        f"db:{sample.get('db_id')}",
        f"mode:{payload.mode}",
        f"format:{payload.source_format}",
        "rows:single" if source_rows == 1 else "rows:multi",
        "rows:batch_large" if source_rows > 20 else "rows:not_large",
        "tables:multi" if table_count > 1 else "tables:single",
        *{f"conflict:{action}" for action in actions},
    }


def select_dev_pilot(
    dataset_path: str | Path,
    dev_ids_path: str | Path,
    output_ids_path: str | Path,
    output_manifest_path: str | Path,
    *,
    sample_count: int = 120,
    seed: int = 42,
    max_per_source_group: int = 2,
) -> dict[str, Any]:
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    all_samples = {str(row["id"]): row for row in load_json(dataset_path)}
    dev_ids = read_ids(dev_ids_path)
    missing = [sample_id for sample_id in dev_ids if sample_id not in all_samples]
    if missing:
        raise ValueError(f"Dev split references {len(missing)} missing samples")
    if sample_count > len(dev_ids):
        raise ValueError("sample_count exceeds the available dev samples")
    features = {
        sample_id: _sample_features(all_samples[sample_id])
        for sample_id in dev_ids
    }
    frequency = Counter(
        feature
        for sample_features in features.values()
        for feature in sample_features
    )
    selected: list[str] = []
    selected_set: set[str] = set()
    feature_coverage: Counter[str] = Counter()
    source_group_coverage: Counter[str] = Counter()

    while len(selected) < sample_count:
        candidates: list[tuple[float, str, str]] = []
        for sample_id in dev_ids:
            if sample_id in selected_set:
                continue
            sample = all_samples[sample_id]
            source_group = str(
                sample.get("source_group_id") or sample_id
            )
            if source_group_coverage[source_group] >= max_per_source_group:
                continue
            diversity_score = sum(
                (1.0 / frequency[feature])
                + (1.0 / (1 + feature_coverage[feature]))
                for feature in features[sample_id]
            )
            tie_break = hashlib.sha256(
                f"{seed}:{sample_id}".encode("utf-8")
            ).hexdigest()
            candidates.append((diversity_score, tie_break, sample_id))
        if not candidates:
            raise ValueError(
                "Source-group cap prevents selecting the requested pilot size"
            )
        _, _, chosen = max(candidates)
        selected.append(chosen)
        selected_set.add(chosen)
        chosen_group = str(
            all_samples[chosen].get("source_group_id") or chosen
        )
        source_group_coverage[chosen_group] += 1
        feature_coverage.update(features[chosen])

    output_ids = Path(output_ids_path)
    output_ids.parent.mkdir(parents=True, exist_ok=True)
    output_ids.write_text("\n".join(selected) + "\n", encoding="utf-8")
    manifest = {
        "sample_count": len(selected),
        "seed": seed,
        "max_per_source_group": max_per_source_group,
        "source_group_count": len(source_group_coverage),
        "feature_coverage": dict(sorted(feature_coverage.items())),
        "dataset": str(Path(dataset_path).resolve()),
        "source_dev_split": str(Path(dev_ids_path).resolve()),
        "output_ids": str(output_ids.resolve()),
    }
    dump_json(manifest, output_manifest_path)
    return manifest

