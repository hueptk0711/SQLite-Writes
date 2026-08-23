"""Run and analyze actual Stage4R.2 D+F+G1 deterministic replay.

This script performs the reviewer-requested CPU-only replay from the frozen
Stage-4 shared MP-FS+ raw generations. It does not call a model and does not
regenerate prompts for inference. The replay path is:

frozen mp_fs_plus_shared raw generation -> D+F+G1 config -> materialization ->
verification -> compilation -> preflight -> execution -> state comparison.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.run_method import run_method  # noqa: E402
from scripts.analysis.analyze_stage4_fresh_7b import (  # noqa: E402
    first_failure_stage,
    load_frozen_sample_ids,
    load_method_rows,
    mcnemar_exact_pvalue,
    metric_bool,
    read_jsonl,
    truthy,
    validate_method_rows,
)
from scripts.analysis.run_stage3_causal_replay import write_csv, write_json  # noqa: E402

D_G1_METHOD = "d_g1_primary"
FULL_METHOD = "full_secondary"
ACTUAL_METHOD = "d_f_g1_diagnostic_actual"
DEFAULT_CONFIG = "configs/stage4/d_f_g1_diagnostic.json"
RAW_GENERATION_SOURCE = "raw_generations/mp_fs_plus_shared.jsonl"
COPIED_ACTUAL_ARTIFACTS = {
    "raw_generations.jsonl": "d_f_g1_actual_raw_generations.jsonl",
    "materialized_write_plans.jsonl": "d_f_g1_actual_materialized_write_plans.jsonl",
    "verification.jsonl": "d_f_g1_actual_verification.jsonl",
    "compiled_programs.jsonl": "d_f_g1_actual_compiled_programs.jsonl",
    "execution_logs.jsonl": "d_f_g1_actual_execution.jsonl",
    "evaluation.jsonl": "d_f_g1_actual_evaluation.jsonl",
    "metrics.json": "d_f_g1_actual_metrics.json",
    "manifest.json": "d_f_g1_actual_manifest.json",
    "run_lock.json": "d_f_g1_actual_run_lock.json",
    "config.json": "d_f_g1_actual_config.json",
}
F_REPAIR_COLUMNS = [
    "sample_id",
    "source_artifact",
    "trace_path",
    "slot_path",
    "reference_kind",
    "repair_rule",
    "repair_attempted",
    "repair_applied",
    "repair_succeeded",
    "original_reference",
    "replacement_reference",
    "candidate_count",
    "candidate_set",
    "validation_before",
    "validation_after",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_jsonl(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def selected_source_paths(protocol_root: Path) -> dict[str, Path]:
    manifest_path = protocol_root / "data" / "fresh_dataset_manifest.json"
    manifest = read_json(manifest_path)
    paths = {
        "data": Path(str(manifest["source_dataset"])),
        "gold_plans": Path(str(manifest["source_gold_plans"])),
        "profile_dir": Path(str(manifest["profile_dir"])),
        "db_root": Path(str(manifest["db_root"])),
        "ids": protocol_root / "data" / "fresh_sample_ids.txt",
    }
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise SystemExit(f"STOP: missing Stage4R.2 replay source paths: {missing}")
    return paths


def ensure_actual_replay(
    *,
    protocol_root: Path,
    result_root: Path,
    config_path: Path,
    actual_run_dir: Path,
    skip_replay: bool,
) -> dict[str, Any]:
    evaluation_path = actual_run_dir / "evaluation.jsonl"
    if skip_replay and evaluation_path.is_file():
        return {
            "status": "reused_existing_actual_run",
            "actual_run_dir": str(actual_run_dir),
        }
    source_paths = selected_source_paths(protocol_root)
    raw_source = result_root / RAW_GENERATION_SOURCE
    if not raw_source.is_file():
        raise SystemExit(f"STOP: missing frozen shared raw generation: {raw_source}")
    metrics = run_method(
        config_path,
        source_paths["data"],
        source_paths["ids"],
        source_paths["profile_dir"],
        source_paths["db_root"],
        actual_run_dir,
        gold_plans_path=source_paths["gold_plans"],
        resume=False,
        stage="dev",
        reuse_raw_generations_path=raw_source,
    )
    return {
        "status": "actual_replay_completed",
        "actual_run_dir": str(actual_run_dir),
        "metrics": metrics,
        "raw_generation_source": str(raw_source),
        "raw_generation_source_sha256": sha256_file(raw_source),
    }


def copy_actual_artifacts(actual_run_dir: Path, output_dir: Path) -> None:
    for source_name, target_name in COPIED_ACTUAL_ARTIFACTS.items():
        source = actual_run_dir / source_name
        if not source.is_file():
            raise SystemExit(f"STOP: actual replay missing artifact {source}")
        shutil.copyfile(source, output_dir / target_name)


def write_actual_preflight(actual_run_dir: Path, output_dir: Path) -> None:
    rows = []
    for row in read_jsonl(actual_run_dir / "execution_logs.jsonl"):
        preflight = row.get("preflight") if isinstance(row.get("preflight"), dict) else {}
        rows.append(
            {
                "sample_id": row.get("sample_id"),
                "accepted": preflight.get("accepted"),
                "error_class": preflight.get("error_class"),
                "error": preflight.get("error"),
                "latency_sec": preflight.get("latency_sec"),
            }
        )
    write_jsonl(
        rows,
        output_dir / "d_f_g1_actual_preflight.jsonl",
    )


def correctness_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    return {str(row["sample_id"]): metric_bool(row, "target_state_correct") for row in rows}


def paired_summary_row(
    *,
    comparison: str,
    baseline_rows: Sequence[Mapping[str, Any]],
    method_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = correctness_by_id(baseline_rows)
    method = correctness_by_id(method_rows)
    sample_ids = sorted(baseline)
    rescue = sum(method[sample_id] and not baseline[sample_id] for sample_id in sample_ids)
    regression = sum(baseline[sample_id] and not method[sample_id] for sample_id in sample_ids)
    baseline_correct = sum(baseline.values())
    method_correct = sum(method.values())
    return {
        "comparison": comparison,
        "paired_sample_count": len(sample_ids),
        "baseline_correct": baseline_correct,
        "method_correct": method_correct,
        "both_correct": sum(baseline[sample_id] and method[sample_id] for sample_id in sample_ids),
        "both_wrong": sum(not baseline[sample_id] and not method[sample_id] for sample_id in sample_ids),
        "rescue": rescue,
        "regression": regression,
        "accuracy_delta": (method_correct - baseline_correct) / len(sample_ids),
        "mcnemar_exact_p": mcnemar_exact_pvalue(regression, rescue),
    }


def comparison_outcome(*, baseline_correct: bool, method_correct: bool) -> str:
    if method_correct and not baseline_correct:
        return "rescue"
    if baseline_correct and not method_correct:
        return "regression"
    if method_correct:
        return "both_correct"
    return "both_wrong"


def build_sample_comparison_rows(
    *,
    sample_ids: Sequence[str],
    d_g1_rows: Sequence[Mapping[str, Any]],
    actual_rows: Sequence[Mapping[str, Any]],
    full_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    d_by_id = {str(row["sample_id"]): row for row in d_g1_rows}
    a_by_id = {str(row["sample_id"]): row for row in actual_rows}
    f_by_id = {str(row["sample_id"]): row for row in full_rows}
    rows: list[dict[str, Any]] = []
    for sample_id in sample_ids:
        d_row = d_by_id[sample_id]
        a_row = a_by_id[sample_id]
        f_row = f_by_id[sample_id]
        d_correct = metric_bool(d_row, "target_state_correct")
        a_correct = metric_bool(a_row, "target_state_correct")
        f_correct = metric_bool(f_row, "target_state_correct")
        rows.append(
            {
                "sample_id": sample_id,
                "D_G1_target_state_correct": int(d_correct),
                "ACTUAL_D_F_G1_target_state_correct": int(a_correct),
                "FULL_target_state_correct": int(f_correct),
                "D_G1_to_ACTUAL_D_F_G1": comparison_outcome(
                    baseline_correct=d_correct,
                    method_correct=a_correct,
                ),
                "ACTUAL_D_F_G1_to_FULL": comparison_outcome(
                    baseline_correct=a_correct,
                    method_correct=f_correct,
                ),
                "D_G1_first_failure_stage": first_failure_stage(d_row),
                "ACTUAL_D_F_G1_first_failure_stage": first_failure_stage(a_row),
                "FULL_first_failure_stage": first_failure_stage(f_row),
                "D_G1_error_type": d_row.get("error_type") or "",
                "ACTUAL_D_F_G1_error_type": a_row.get("error_type") or "",
                "FULL_error_type": f_row.get("error_type") or "",
                "ACTUAL_D_F_G1_accepted_output": int(truthy(a_row.get("accepted_output"))),
                "ACTUAL_D_F_G1_preflight_accepted": int(
                    truthy(a_row.get("preflight_accepted"))
                ),
                "ACTUAL_D_F_G1_hit_max_new_tokens": int(
                    truthy(a_row.get("hit_max_new_tokens"))
                ),
            }
        )
    return rows


def repair_row_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("sample_id"),
        row.get("slot_path"),
        row.get("reference_kind"),
        row.get("repair_rule"),
        row.get("original_reference"),
        row.get("replacement_reference"),
        row.get("candidate_count"),
        row.get("candidate_set"),
        row.get("validation_before"),
        row.get("validation_after"),
        row.get("repair_applied"),
        row.get("repair_succeeded"),
    )


def build_f_repair_rows_from_artifact(
    actual_run_dir: Path,
    *,
    artifact_name: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for artifact_row in read_jsonl(actual_run_dir / artifact_name):
        sample_id = str(artifact_row.get("sample_id") or "")
        for trace_path, item in walk_dicts(artifact_row):
            if not truthy(item.get("repair_attempted")):
                continue
            candidate_set = item.get("candidate_set")
            row = {
                "sample_id": sample_id,
                "source_artifact": artifact_name,
                "trace_path": trace_path,
                "slot_path": item.get("slot_path") or "",
                "reference_kind": item.get("reference_kind") or "",
                "repair_rule": item.get("repair_rule") or "",
                "repair_attempted": int(truthy(item.get("repair_attempted"))),
                "repair_applied": int(truthy(item.get("repair_applied"))),
                "repair_succeeded": int(truthy(item.get("repair_succeeded"))),
                "original_reference": item.get("original_reference") or "",
                "replacement_reference": item.get("replacement_reference") or "",
                "candidate_count": item.get("candidate_count") or "",
                "candidate_set": json.dumps(candidate_set, ensure_ascii=False, sort_keys=True)
                if candidate_set is not None
                else "",
                "validation_before": item.get("validation_before") or "",
                "validation_after": item.get("validation_after") or "",
            }
            key = repair_row_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield "", value
        for key, nested in value.items():
            for nested_path, nested_item in walk_dicts(nested):
                yield f"/{key}{nested_path}", nested_item
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            for nested_path, nested_item in walk_dicts(nested):
                yield f"/{index}{nested_path}", nested_item


def is_exact_name_repair(row: Mapping[str, Any]) -> bool:
    return row.get("repair_rule") == "unique_exact_identifier_name"


def is_applied_repair(row: Mapping[str, Any]) -> bool:
    return truthy(row.get("repair_applied")) and truthy(row.get("repair_succeeded"))


def build_f_sample_outcome_rows(
    *,
    f_attempt_rows: Sequence[Mapping[str, Any]],
    f_applied_rows: Sequence[Mapping[str, Any]],
    f_materialized_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_sample = {str(row["sample_id"]): row for row in comparison_rows}
    sample_ids = sorted({str(row["sample_id"]) for row in f_attempt_rows})
    rows: list[dict[str, Any]] = []
    for sample_id in sample_ids:
        comparison = by_sample[sample_id]
        sample_attempts = [row for row in f_attempt_rows if str(row["sample_id"]) == sample_id]
        sample_applied = [row for row in f_applied_rows if str(row["sample_id"]) == sample_id]
        sample_materialized = [
            row for row in f_materialized_rows if str(row["sample_id"]) == sample_id
        ]
        rows.append(
            {
                "sample_id": sample_id,
                "F_attempt_count": len(sample_attempts),
                "F_exact_name_attempt_count": sum(
                    is_exact_name_repair(row) for row in sample_attempts
                ),
                "F_applied_exact_name_repair_count": len(sample_applied),
                "F_materialized_exact_name_repair_count": len(sample_materialized),
                "D_G1_target_state_correct": comparison["D_G1_target_state_correct"],
                "ACTUAL_D_F_G1_target_state_correct": comparison[
                    "ACTUAL_D_F_G1_target_state_correct"
                ],
                "FULL_target_state_correct": comparison["FULL_target_state_correct"],
                "D_G1_to_ACTUAL_D_F_G1": comparison["D_G1_to_ACTUAL_D_F_G1"],
                "ACTUAL_D_F_G1_to_FULL": comparison["ACTUAL_D_F_G1_to_FULL"],
                "ACTUAL_D_F_G1_first_failure_stage": comparison[
                    "ACTUAL_D_F_G1_first_failure_stage"
                ],
                "ACTUAL_D_F_G1_error_type": comparison["ACTUAL_D_F_G1_error_type"],
            }
        )
    return rows


def run_stage4r2(
    *,
    protocol_root: Path,
    result_root: Path,
    config_path: Path,
    output_dir: Path,
    actual_run_dir: Path,
    skip_replay: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    replay = ensure_actual_replay(
        protocol_root=protocol_root,
        result_root=result_root,
        config_path=config_path,
        actual_run_dir=actual_run_dir,
        skip_replay=skip_replay,
    )
    sample_ids = load_frozen_sample_ids(protocol_root)
    d_g1_rows = load_method_rows(result_root, D_G1_METHOD, sample_ids)
    full_rows = load_method_rows(result_root, FULL_METHOD, sample_ids)
    actual_rows = read_jsonl(actual_run_dir / "evaluation.jsonl")
    validate_method_rows(actual_rows, method_slug=ACTUAL_METHOD, frozen_ids=sample_ids)

    comparison_rows = build_sample_comparison_rows(
        sample_ids=sample_ids,
        d_g1_rows=d_g1_rows,
        actual_rows=actual_rows,
        full_rows=full_rows,
    )
    paired_rows = [
        paired_summary_row(
            comparison="D_G1_to_ACTUAL_D_F_G1",
            baseline_rows=d_g1_rows,
            method_rows=actual_rows,
        ),
        paired_summary_row(
            comparison="ACTUAL_D_F_G1_to_FULL",
            baseline_rows=actual_rows,
            method_rows=full_rows,
        ),
    ]
    f_attempt_rows = build_f_repair_rows_from_artifact(
        actual_run_dir,
        artifact_name="verification.jsonl",
    )
    f_applied_rows = [
        row
        for row in f_attempt_rows
        if is_exact_name_repair(row) and is_applied_repair(row)
    ]
    f_materialized_rows = [
        row
        for row in build_f_repair_rows_from_artifact(
            actual_run_dir,
            artifact_name="materialized_write_plans.jsonl",
        )
        if is_exact_name_repair(row)
    ]
    f_sample_rows = build_f_sample_outcome_rows(
        f_attempt_rows=f_attempt_rows,
        f_applied_rows=f_applied_rows,
        f_materialized_rows=f_materialized_rows,
        comparison_rows=comparison_rows,
    )
    f_attempt_ids = sorted({str(row["sample_id"]) for row in f_attempt_rows})
    f_exact_attempt_ids = sorted(
        {str(row["sample_id"]) for row in f_attempt_rows if is_exact_name_repair(row)}
    )
    f_applied_ids = sorted({str(row["sample_id"]) for row in f_applied_rows})
    f_materialized_ids = sorted({str(row["sample_id"]) for row in f_materialized_rows})
    summary = {
        "stage": "Stage4R2_ACTUAL_D_F_G1_REPLAY",
        "model_called": False,
        "gpu_called": False,
        "fresh_sample_count": len(sample_ids),
        "actual_method": ACTUAL_METHOD,
        "actual_config": str(config_path),
        "actual_config_sha256": sha256_file(config_path),
        "replay": replay,
        "D_G1_correct": paired_rows[0]["baseline_correct"],
        "ACTUAL_D_F_G1_correct": paired_rows[0]["method_correct"],
        "FULL_correct": paired_rows[1]["method_correct"],
        "D_G1_to_ACTUAL_D_F_G1_rescue": paired_rows[0]["rescue"],
        "D_G1_to_ACTUAL_D_F_G1_regression": paired_rows[0]["regression"],
        "ACTUAL_D_F_G1_to_FULL_rescue": paired_rows[1]["rescue"],
        "ACTUAL_D_F_G1_to_FULL_regression": paired_rows[1]["regression"],
        "F_attempt_sample_count": len(f_attempt_ids),
        "F_attempt_count": len(f_attempt_rows),
        "F_exact_name_attempt_sample_count": len(f_exact_attempt_ids),
        "F_exact_name_attempt_count": sum(is_exact_name_repair(row) for row in f_attempt_rows),
        "F_applied_sample_count": len(f_applied_ids),
        "F_applied_exact_name_repair_count": len(f_applied_rows),
        "F_materialized_sample_count": len(f_materialized_ids),
        "F_materialized_exact_name_repair_count": len(f_materialized_rows),
        "F_state_rescue_count": paired_rows[0]["rescue"],
        "F_state_regression_count": paired_rows[0]["regression"],
        "F_attempt_rule_counts": {
            rule: sum(row["repair_rule"] == rule for row in f_attempt_rows)
            for rule in sorted({str(row["repair_rule"]) for row in f_attempt_rows})
        },
        "deprecated_note": (
            "F_activation_sample_count/F_repair_count were removed because they "
            "undercounted repairs by reading only materialized_write_plans.jsonl."
        ),
    }

    copy_actual_artifacts(actual_run_dir, output_dir)
    write_actual_preflight(actual_run_dir, output_dir)
    write_csv(
        output_dir / "d_g1_actual_full_sample_level.csv",
        comparison_rows,
        list(comparison_rows[0]),
    )
    write_csv(
        output_dir / "d_g1_actual_full_paired_summary.csv",
        paired_rows,
        list(paired_rows[0]),
    )
    write_csv(
        output_dir / "f_attempts.csv",
        f_attempt_rows,
        F_REPAIR_COLUMNS,
    )
    write_csv(
        output_dir / "f_applied_repairs.csv",
        f_applied_rows,
        F_REPAIR_COLUMNS,
    )
    write_csv(
        output_dir / "f_materialized_repairs.csv",
        f_materialized_rows,
        F_REPAIR_COLUMNS,
    )
    write_csv(
        output_dir / "f_sample_outcomes.csv",
        f_sample_rows,
        list(f_sample_rows[0]) if f_sample_rows else [
            "sample_id",
            "F_attempt_count",
            "F_exact_name_attempt_count",
            "F_applied_exact_name_repair_count",
            "F_materialized_exact_name_repair_count",
            "D_G1_target_state_correct",
            "ACTUAL_D_F_G1_target_state_correct",
            "FULL_target_state_correct",
            "D_G1_to_ACTUAL_D_F_G1",
            "ACTUAL_D_F_G1_to_FULL",
            "ACTUAL_D_F_G1_first_failure_stage",
            "ACTUAL_D_F_G1_error_type",
        ],
    )
    write_csv(
        output_dir / "d_f_g1_actual_f_repairs.csv",
        f_materialized_rows,
        F_REPAIR_COLUMNS,
    )
    write_json(output_dir / "stage4r2_actual_replay_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-root", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--actual-run-dir", required=True)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args(argv)
    summary = run_stage4r2(
        protocol_root=Path(args.protocol_root),
        result_root=Path(args.result_root),
        config_path=Path(args.config),
        output_dir=Path(args.output_dir),
        actual_run_dir=Path(args.actual_run_dir),
        skip_replay=args.skip_replay,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
