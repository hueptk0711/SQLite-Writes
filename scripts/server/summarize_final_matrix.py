from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.analysis.statistics import (
    adjust_comparison_family,
    exact_mcnemar,
    paired_cluster_bootstrap,
    paired_database_macro_bootstrap,
)


METHODS = (
    ("d_fs_m", "D-FS-M", "gpu"),
    ("j_fs_m", "J-FS-M", "gpu"),
    ("s_fs_v2_m", "S-FS-v2-M", "gpu"),
    ("mp_fs_m", "MP-FS-M", "gpu"),
    ("mp_fs_plus", "MP-FS+", "gpu"),
    ("gold_mp", "Gold-MP", "oracle"),
)
COMPARISONS = (
    ("D-FS-M", "MP-FS+", "MP-FS+ vs D-FS-M"),
    ("J-FS-M", "MP-FS+", "MP-FS+ vs J-FS-M"),
    ("MP-FS-M", "MP-FS+", "MP-FS+ vs MP-FS-M"),
    ("D-FS-M", "J-FS-M", "J-FS-M vs D-FS-M"),
)
PRIMARY_METRICS = (
    "target_state_accuracy",
    "original_request_accuracy",
    "state_changing_accuracy",
    "conflict_sensitive_accuracy",
    "database_macro_accuracy",
    "accepted_output_accuracy",
    "coverage",
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def source_group(sample: dict[str, Any]) -> str:
    value = sample.get("source_group")
    if value is not None:
        return str(value)
    provenance = sample.get("provenance")
    if isinstance(provenance, dict) and provenance.get("source_group") is not None:
        return str(provenance["source_group"])
    return str(sample.get("id"))


def compare(
    left_id: str,
    right_id: str,
    label: str,
    evaluations: dict[str, dict[str, dict[str, Any]]],
    samples: dict[str, dict[str, Any]],
    ordered_ids: list[str],
    iterations: int,
) -> dict[str, Any]:
    left = evaluations[left_id]
    right = evaluations[right_id]
    left_values = [
        bool(left[sample_id].get("target_state_correct"))
        for sample_id in ordered_ids
    ]
    right_values = [
        bool(right[sample_id].get("target_state_correct"))
        for sample_id in ordered_ids
    ]
    differences = [
        float(right_value) - float(left_value)
        for left_value, right_value in zip(left_values, right_values)
    ]
    return {
        "comparison": label,
        "metric": "target_state_correct",
        "direction": f"{right_id}_minus_{left_id}",
        "paired_samples": len(ordered_ids),
        "left_method": left_id,
        "right_method": right_id,
        "left_accuracy": sum(left_values) / len(left_values),
        "right_accuracy": sum(right_values) / len(right_values),
        "absolute_difference": sum(differences) / len(differences),
        "mcnemar": exact_mcnemar(left_values, right_values),
        "paired_cluster_bootstrap": paired_cluster_bootstrap(
            differences,
            [source_group(samples[sample_id]) for sample_id in ordered_ids],
            iterations=iterations,
            seed=13,
        ),
        "paired_database_macro_bootstrap": paired_database_macro_bootstrap(
            differences,
            [str(samples[sample_id]["db_id"]) for sample_id in ordered_ids],
            iterations=iterations,
            seed=17,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--csv-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    parser.add_argument("--bootstrap-iterations", type=int, default=10_000)
    args = parser.parse_args()

    result_root = Path(args.result_root)
    samples = {
        str(row["id"]): row for row in load_json(Path(args.dataset))
    }
    ordered_ids = [
        line.strip()
        for line in Path(args.ids).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    protocol = load_json(Path(args.protocol))
    if protocol.get("status") != "frozen":
        raise ValueError("Final protocol must be frozen")
    if len(ordered_ids) != 300 or len(set(ordered_ids)) != 300:
        raise ValueError("Final split must contain 300 unique IDs")

    method_summaries: dict[str, dict[str, Any]] = {}
    evaluations: dict[str, dict[str, dict[str, Any]]] = {}
    issues: list[dict[str, Any]] = []

    for slug, method_id, backend in METHODS:
        run_dir = result_root / slug
        required = (
            "manifest.json",
            "run_lock.json",
            "raw_generations.jsonl",
            "evaluation.jsonl",
            "metrics.json",
            "error_analysis.csv",
            "FINAL_RUN_CONSUMED.json",
        )
        missing = [name for name in required if not (run_dir / name).is_file()]
        if backend == "gpu" and not (run_dir / "model_manifest.json").is_file():
            missing.append("model_manifest.json")
        if missing:
            issues.append(
                {"method_id": method_id, "code": "MISSING_ARTIFACT", "files": missing}
            )
            continue

        raw = load_jsonl(run_dir / "raw_generations.jsonl")
        evaluation_rows = load_jsonl(run_dir / "evaluation.jsonl")
        evaluation = {
            str(row["sample_id"]): row for row in evaluation_rows
        }
        metrics = load_json(run_dir / "metrics.json")
        manifest = load_json(run_dir / "manifest.json")
        consumed = load_json(run_dir / "FINAL_RUN_CONSUMED.json")
        raw_ids = [str(row["sample_id"]) for row in raw]
        if (
            len(raw) != 300
            or len(set(raw_ids)) != 300
            or set(raw_ids) != set(ordered_ids)
            or len(evaluation) != 300
            or set(evaluation) != set(ordered_ids)
        ):
            issues.append(
                {"method_id": method_id, "code": "INCOMPLETE_OR_DUPLICATE_ROWS"}
            )
        input_truncated = sum(bool(row.get("input_truncated")) for row in raw)
        output_limited = sum(bool(row.get("hit_max_new_tokens")) for row in raw)
        failed = sum(
            row.get("status")
            not in ({"success"} if backend == "gpu" else {"not_applicable"})
            for row in raw
        )
        if input_truncated or output_limited or failed:
            issues.append(
                {
                    "method_id": method_id,
                    "code": "INVALID_GENERATION_ROWS",
                    "input_truncated": input_truncated,
                    "output_limited": output_limited,
                    "failed": failed,
                }
            )
        if consumed.get("status") != "consumed":
            issues.append(
                {"method_id": method_id, "code": "NOT_MARKED_CONSUMED"}
            )
        method_summaries[method_id] = {
            "slug": slug,
            "backend": backend,
            "samples": metrics.get("samples"),
            **{key: metrics.get(key) for key in PRIMARY_METRICS},
            "parse_success": metrics.get("parse_success"),
            "plan_validation_success": metrics.get("plan_validation_success"),
            "build_success": metrics.get("build_success"),
            "execution_success": metrics.get("execution_success"),
            "strict_full_state_accuracy": metrics.get(
                "strict_full_state_accuracy"
            ),
            "side_effect_rate": metrics.get("side_effect_rate"),
            "abstention_rate": metrics.get("abstention_rate"),
            "selective_risk": metrics.get("selective_risk"),
            "mean_input_tokens": metrics.get("mean_input_tokens"),
            "mean_output_tokens": metrics.get("mean_output_tokens"),
            "mean_latency_sec": metrics.get("mean_latency_sec"),
            "input_truncated_count": input_truncated,
            "output_limited_count": output_limited,
            "generation_failure_count": failed,
            "run_lock_sha256": manifest.get("run_lock_sha256"),
            "final_protocol_sha256": manifest.get("final_protocol_sha256"),
            "metrics": metrics,
        }
        evaluations[method_id] = evaluation

    if set(method_summaries) == {item[1] for item in METHODS}:
        comparisons = adjust_comparison_family(
            [
                compare(
                    left,
                    right,
                    label,
                    evaluations,
                    samples,
                    ordered_ids,
                    args.bootstrap_iterations,
                )
                for left, right, label in COMPARISONS
            ]
        )
    else:
        comparisons = []

    if method_summaries.get("Gold-MP", {}).get("target_state_accuracy") != 1.0:
        issues.append({"method_id": "Gold-MP", "code": "GOLD_ACCURACY_NOT_ONE"})

    report = {
        "report_version": 1,
        "status": "pass" if not issues else "fail",
        "paper_result_eligible": not issues,
        "protocol_id": protocol.get("protocol_id"),
        "sample_count": len(ordered_ids),
        "method_count": len(method_summaries),
        "database_counts": dict(
            sorted(Counter(samples[item]["db_id"] for item in ordered_ids).items())
        ),
        "methods": method_summaries,
        "pre_registered_comparisons": comparisons,
        "issues": issues,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    csv_output = Path(args.csv_output)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    csv_fields = (
        "method_id",
        "target_state_accuracy",
        "original_request_accuracy",
        "state_changing_accuracy",
        "conflict_sensitive_accuracy",
        "database_macro_accuracy",
        "accepted_output_accuracy",
        "coverage",
        "side_effect_rate",
        "abstention_rate",
        "selective_risk",
        "mean_latency_sec",
    )
    with csv_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for _slug, method_id, _backend in METHODS:
            item = method_summaries.get(method_id, {})
            writer.writerow(
                {"method_id": method_id, **{key: item.get(key) for key in csv_fields[1:]}}
            )

    markdown = [
        "# Final external-holdout matrix",
        "",
        f"- Status: **{report['status'].upper()}**",
        f"- Paper-result eligible: `{str(report['paper_result_eligible']).lower()}`",
        f"- Samples: `{len(ordered_ids)}`",
        f"- Methods: `{len(method_summaries)}`",
        "",
        "| Method | Target | Original request | State-changing | Conflict-sensitive | DB macro | Accepted accuracy | Coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _slug, method_id, _backend in METHODS:
        item = method_summaries.get(method_id, {})
        values = [item.get(key) for key in PRIMARY_METRICS]
        formatted = [
            "n/a" if value is None else f"{float(value):.4f}" for value in values
        ]
        markdown.append(f"| {method_id} | " + " | ".join(formatted) + " |")
    markdown.extend(["", "## Pre-registered paired comparisons", ""])
    for item in comparisons:
        mcnemar = item["mcnemar"]
        ci = item["paired_cluster_bootstrap"]["confidence_interval_95"]
        markdown.append(
            f"- {item['comparison']}: difference={item['absolute_difference']:.4f}; "
            f"wins/losses={mcnemar['right_only_correct']}/"
            f"{mcnemar['left_only_correct']}; exact p="
            f"{mcnemar['p_value_two_sided_exact']:.6g}; Holm p="
            f"{mcnemar['p_value_holm_family']:.6g}; clustered 95% CI="
            f"[{ci[0]:.4f}, {ci[1]:.4f}]."
        )
    if issues:
        markdown.extend(["", "## Blocking issues", ""])
        markdown.extend(f"- `{item}`" for item in issues)
    markdown_output = Path(args.markdown_output)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(
        "\n".join(markdown) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
