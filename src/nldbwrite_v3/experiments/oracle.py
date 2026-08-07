from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, iter_jsonl, load_json, read_ids, write_jsonl
from nldbwrite_v3.evaluator import (
    evaluate_oracle_sample,
    find_database,
    load_database_image,
    snapshot_database,
)
from nldbwrite_v3.schema import load_profile


def _oracle_metrics(
    results: list[dict[str, Any]],
    selected_count: int,
    *,
    elapsed_sec: float,
) -> dict[str, Any]:
    completed = len(results)
    rate = lambda key: (
        sum(bool(row.get(key)) for row in results) / completed
        if completed
        else 0.0
    )
    complete = completed == selected_count
    metrics = {
        "samples": completed,
        "selected_samples": selected_count,
        "completed_samples": completed,
        "remaining_samples": selected_count - completed,
        "complete": complete,
        "elapsed_sec_this_invocation": elapsed_sec,
        "plan_validation_success": rate("plan_valid"),
        "build_success": rate("build_success"),
        "execution_success": rate("execution_success"),
        "target_state_accuracy": rate("target_state_correct"),
        "strict_full_state_accuracy": rate("strict_full_state_correct"),
    }
    metrics["gate_pass"] = bool(
        complete
        and metrics["plan_validation_success"] == 1.0
        and metrics["build_success"] >= 0.99
        and metrics["execution_success"] >= 0.99
        and metrics["target_state_accuracy"] >= 0.98
        and metrics["strict_full_state_accuracy"] >= 0.98
    )
    return metrics


def run_oracle_evaluation(
    dataset_path: str | Path,
    gold_plans_path: str | Path,
    profile_dir: str | Path,
    db_root: str | Path,
    output_dir: str | Path,
    *,
    ids_path: str | Path | None = None,
    resume: bool = True,
    max_samples: int | None = None,
    progress_every: int = 10,
) -> dict[str, Any]:
    started = time.perf_counter()
    if max_samples is not None and max_samples < 1:
        raise ValueError("max_samples must be positive when provided")
    if progress_every < 1:
        raise ValueError("progress_every must be positive")
    samples = {str(row["id"]): row for row in load_json(dataset_path)}
    plans = {str(row["sample_id"]): row for row in iter_jsonl(gold_plans_path)}
    selected_ids = read_ids(ids_path) if ids_path else sorted(samples)
    profiles = {
        path.stem: load_profile(path)
        for path in Path(profile_dir).glob("*.json")
    }
    missing_samples = [sample_id for sample_id in selected_ids if sample_id not in samples]
    missing_plans = [sample_id for sample_id in selected_ids if sample_id not in plans]
    if missing_samples:
        raise ValueError(f"Split references {len(missing_samples)} missing samples")
    if missing_plans:
        raise ValueError(f"Gold plans are missing {len(missing_plans)} selected samples")

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    evaluation_path = target / "evaluation.jsonl"
    existing: dict[str, dict[str, Any]] = {}
    selected_set = set(selected_ids)
    if resume and evaluation_path.exists():
        existing = {
            str(row["sample_id"]): row
            for row in iter_jsonl(evaluation_path)
            if str(row.get("sample_id")) in selected_set
        }
    else:
        evaluation_path.open("w", encoding="utf-8").close()

    pending = [sample_id for sample_id in selected_ids if sample_id not in existing]
    pending.sort(key=lambda sample_id: (str(samples[sample_id]["db_id"]), sample_id))
    if max_samples is not None:
        pending = pending[:max_samples]
    database_image: str | Path | bytes | None = None
    database_connection = None
    database_path: Path | None = None
    current_db_id: str | None = None
    try:
        with evaluation_path.open("a", encoding="utf-8", newline="\n") as handle:
            for invocation_index, sample_id in enumerate(pending, start=1):
                sample = samples[sample_id]
                db_id = str(sample["db_id"])
                if db_id not in profiles:
                    raise ValueError(f"Profile is missing for db_id={db_id!r}")
                if db_id != current_db_id:
                    if database_connection is not None:
                        database_connection.close()
                    database_path = find_database(db_root, db_id)
                    database_image = load_database_image(database_path)
                    database_connection = snapshot_database(database_image)
                    current_db_id = db_id
                result = evaluate_oracle_sample(
                    sample,
                    plans[sample_id],
                    profiles[db_id],
                    database_connection,
                    reuse_connection=True,
                    fallback_db_path=database_path,
                )
                existing[sample_id] = result
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                completed = len(existing)
                if (
                    invocation_index % progress_every == 0
                    or invocation_index == len(pending)
                ):
                    partial = _oracle_metrics(
                        [existing[item] for item in selected_ids if item in existing],
                        len(selected_ids),
                        elapsed_sec=time.perf_counter() - started,
                    )
                    dump_json(partial, target / "metrics.partial.json")
                    print(
                        "[oracle] "
                        f"{completed}/{len(selected_ids)} completed; "
                        f"target={partial['target_state_accuracy']:.4f}; "
                        f"strict={partial['strict_full_state_accuracy']:.4f}",
                        flush=True,
                    )
    finally:
        if database_connection is not None:
            database_connection.close()

    results = [existing[sample_id] for sample_id in selected_ids if sample_id in existing]
    # Normalize order and remove any duplicate checkpoint rows.
    write_jsonl(results, evaluation_path)
    metrics = _oracle_metrics(
        results,
        len(selected_ids),
        elapsed_sec=time.perf_counter() - started,
    )
    dump_json(metrics, target / "metrics.json")
    dump_json(metrics, target / "metrics.partial.json")
    return metrics
