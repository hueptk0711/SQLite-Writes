from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


METHODS = (
    ("d_fs_m", "D-FS-M", "gpu"),
    ("j_fs_m", "J-FS-M", "gpu"),
    ("mp_fs_m", "MP-FS-M", "gpu"),
    ("mp_fs_plus", "MP-FS+", "gpu"),
    ("gold_mp", "Gold-MP", "oracle"),
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--result-root",
        default="experiments/calibration/full_locked_v3_in28672_out4096",
    )
    parser.add_argument(
        "--protocol",
        default="configs/experiments/calibration_protocol.json",
    )
    parser.add_argument(
        "--output",
        default="artifacts/reports/calibration_go_decision.json",
    )
    parser.add_argument(
        "--markdown-output",
        default="artifacts/reports/calibration_matrix_summary.md",
    )
    args = parser.parse_args()

    result_root = Path(args.result_root)
    protocol = load_json(Path(args.protocol))
    expected_ids = [
        line.strip()
        for line in Path("data/calibration/calibration_ids.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    expected_set = set(expected_ids)
    method_summaries: dict[str, dict[str, Any]] = {}
    artifact_issues: list[dict[str, Any]] = []

    for slug, method_id, backend in METHODS:
        run_dir = result_root / slug
        required = (
            "config.json",
            "run_lock.json",
            "manifest.json",
            "raw_generations.jsonl",
            "evaluation.jsonl",
            "metrics.json",
            "error_analysis.csv",
        )
        missing = [name for name in required if not (run_dir / name).is_file()]
        if backend == "gpu" and not (run_dir / "model_manifest.json").is_file():
            missing.append("model_manifest.json")
        if missing:
            artifact_issues.append(
                {"method": method_id, "code": "MISSING_ARTIFACTS", "files": missing}
            )
            continue

        raw = load_jsonl(run_dir / "raw_generations.jsonl")
        evaluation = load_jsonl(run_dir / "evaluation.jsonl")
        metrics = load_json(run_dir / "metrics.json")
        raw_ids = [str(row.get("sample_id")) for row in raw]
        evaluation_ids = [str(row.get("sample_id")) for row in evaluation]
        missing_predictions = sorted(expected_set - set(raw_ids))
        duplicate_predictions = len(raw_ids) - len(set(raw_ids))
        evaluation_missing = sorted(expected_set - set(evaluation_ids))

        error_categories: dict[str, int] = {}
        with (run_dir / "error_analysis.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                category = str(row.get("error_category") or "")
                error_categories[category] = error_categories.get(category, 0) + 1

        invalid_selector_count = sum(
            str(row.get("error_type") or "") == "INVALID_SOURCE_SELECTOR"
            for row in evaluation
        )
        unknown_column_count = error_categories.get("E3_unknown_column", 0)
        input_truncated_count = sum(
            bool(row.get("input_truncated")) for row in raw
        )
        output_limit_hit_count = sum(
            bool(row.get("hit_max_new_tokens")) for row in raw
        )
        generation_failure_count = sum(
            row.get("status") not in (
                {"success"} if backend == "gpu" else {"not_applicable"}
            )
            for row in raw
        )

        summary = {
            "method_id": method_id,
            "backend": backend,
            "samples": metrics.get("samples"),
            "parse_success": metrics.get("parse_success"),
            "plan_validation_success": metrics.get("plan_validation_success"),
            "build_success": metrics.get("build_success"),
            "execution_success": metrics.get("execution_success"),
            "target_state_accuracy": metrics.get("target_state_accuracy"),
            "strict_full_state_accuracy": metrics.get(
                "strict_full_state_accuracy"
            ),
            "accepted_output_accuracy": metrics.get(
                "accepted_output_accuracy"
            ),
            "coverage": metrics.get("coverage"),
            "side_effect_rate": metrics.get("side_effect_rate"),
            "input_truncated_count": input_truncated_count,
            "output_limit_hit_count": output_limit_hit_count,
            "generation_failure_count": generation_failure_count,
            "missing_prediction_count": len(missing_predictions),
            "duplicate_prediction_count": duplicate_predictions,
            "missing_evaluation_count": len(evaluation_missing),
            "invalid_source_selector_count": invalid_selector_count,
            "unknown_column_count": unknown_column_count,
            "run_lock_sha256": load_json(run_dir / "manifest.json").get(
                "run_lock_sha256"
            ),
        }
        method_summaries[slug] = summary
        if (
            len(raw) != len(expected_ids)
            or len(evaluation) != len(expected_ids)
            or missing_predictions
            or duplicate_predictions
            or evaluation_missing
        ):
            artifact_issues.append(
                {
                    "method": method_id,
                    "code": "INCOMPLETE_OR_DUPLICATE_ROWS",
                    "raw_rows": len(raw),
                    "evaluation_rows": len(evaluation),
                    "missing_predictions": missing_predictions,
                    "duplicate_predictions": duplicate_predictions,
                    "missing_evaluations": evaluation_missing,
                }
            )

    thresholds = protocol["go_no_go"]
    checks: dict[str, dict[str, Any]] = {}

    def check(name: str, actual: Any, operator: str, expected: Any) -> None:
        if actual is None:
            passed = False
        elif operator == ">=":
            passed = actual >= expected
        elif operator == "<=":
            passed = actual <= expected
        elif operator == "==":
            passed = actual == expected
        else:
            raise ValueError(operator)
        checks[name] = {
            "actual": actual,
            "operator": operator,
            "expected": expected,
            "pass": passed,
        }

    proposed = method_summaries.get("mp_fs_plus", {})
    gold = method_summaries.get("gold_mp", {})
    check(
        "gold_mp_accuracy",
        gold.get("target_state_accuracy"),
        "==",
        thresholds["gold_mp_accuracy"],
    )
    check(
        "mp_fs_plus_parse_success",
        proposed.get("parse_success"),
        ">=",
        thresholds["parse_success_min"],
    )
    check(
        "mp_fs_plus_build_success",
        proposed.get("build_success"),
        ">=",
        thresholds["plan_build_success_min"],
    )
    check(
        "mp_fs_plus_execution_success",
        proposed.get("execution_success"),
        ">=",
        thresholds["execution_success_min"],
    )
    check(
        "mp_fs_plus_accepted_output_accuracy",
        proposed.get("accepted_output_accuracy"),
        ">=",
        thresholds["accepted_output_accuracy_min"],
    )
    check(
        "mp_fs_plus_side_effect_rate",
        proposed.get("side_effect_rate"),
        "<=",
        thresholds["side_effect_rate_max"],
    )
    check(
        "mp_fs_plus_invalid_source_selector_count",
        proposed.get("invalid_source_selector_count"),
        "<=",
        thresholds["invalid_source_selector_count_max"],
    )
    check(
        "mp_fs_plus_unknown_column_count",
        proposed.get("unknown_column_count"),
        "<=",
        thresholds["unknown_column_count_max"],
    )

    gpu_summaries = [
        summary
        for summary in method_summaries.values()
        if summary["backend"] == "gpu"
    ]
    all_summaries = list(method_summaries.values())
    check(
        "all_gpu_input_truncation_count",
        sum(item["input_truncated_count"] for item in gpu_summaries),
        "<=",
        thresholds["input_truncation_count_max_all_gpu_methods"],
    )
    check(
        "all_gpu_output_limit_hit_count",
        sum(item["output_limit_hit_count"] for item in gpu_summaries),
        "<=",
        thresholds["output_limit_hit_count_max_all_gpu_methods"],
    )
    check(
        "all_methods_missing_prediction_count",
        sum(item["missing_prediction_count"] for item in all_summaries),
        "<=",
        thresholds["missing_prediction_count_max_all_methods"],
    )
    check(
        "all_gpu_generation_failure_count",
        sum(item["generation_failure_count"] for item in gpu_summaries),
        "==",
        0,
    )

    all_checks_pass = bool(checks) and all(item["pass"] for item in checks.values())
    complete = len(method_summaries) == len(METHODS) and not artifact_issues
    decision = "go" if complete and all_checks_pass else "no_go"
    report = {
        "report_version": 1,
        "protocol_id": protocol["protocol_id"],
        "status": decision,
        "matrix_complete": complete,
        "gpu_run_complete": complete,
        "final_protocol_freeze_authorized": decision == "go",
        "paper_result_eligible": False,
        "methods": method_summaries,
        "checks": checks,
        "artifact_issues": artifact_issues,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    markdown = [
        "# Calibration matrix decision",
        "",
        f"- Protocol: `{protocol['protocol_id']}`",
        f"- Decision: **{decision.upper()}**",
        f"- Matrix complete: `{str(complete).lower()}`",
        "- Paper-result eligible: `false` (calibration stage)",
        "",
        "| Method | Parse | Build | Execute | Target | Strict | Accepted accuracy | Coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for slug, method_id, _backend in METHODS:
        item = method_summaries.get(slug, {})
        values = [
            item.get("parse_success"),
            item.get("build_success"),
            item.get("execution_success"),
            item.get("target_state_accuracy"),
            item.get("strict_full_state_accuracy"),
            item.get("accepted_output_accuracy"),
            item.get("coverage"),
        ]
        formatted = ["n/a" if value is None else f"{value:.4f}" for value in values]
        markdown.append(f"| {method_id} | " + " | ".join(formatted) + " |")
    markdown.extend(["", "## Locked Go/No-Go checks", ""])
    for name, item in checks.items():
        marker = "PASS" if item["pass"] else "FAIL"
        markdown.append(
            f"- {marker}: `{name}` — {item['actual']} "
            f"{item['operator']} {item['expected']}"
        )
    markdown_path = Path(args.markdown_output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"DECISION: {decision.upper()}")
    print(f"JSON: {output}")
    print(f"MARKDOWN: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
