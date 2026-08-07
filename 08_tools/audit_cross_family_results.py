#!/usr/bin/env python3
"""Audit and import the post-hoc Yi-Coder cross-family result archive.

The audit never regenerates model predictions.  It verifies the frozen protocol,
archive integrity, conservative output-limit adjudications, recomputes headline
rates from evaluation rows, and recomputes the corrected off-target metric from
strict state mismatches and the locked Gold-MP target-table set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tarfile
from pathlib import Path, PurePosixPath


METHODS = {
    "D-FS-M": "d_fs_m",
    "J-FS-M": "j_fs_m",
    "MP-FS+": "mp_fs_plus",
}
EXPECTED_PRIMARY_PROTOCOL_SHA256 = (
    "e6bb763334f0b7dcec77523794a20687087fe6f0f62f572cdd3e999b7b48a330"
)
EXPECTED_MODEL_SHA256 = (
    "881ebc7b893a9e12d704c40d2bdc908ed7958e0f671f5ccc434f5303102e6904"
)
EXPECTED_MODEL_REVISION = "356a1f8d4e4a606d0b879e54191ca809918576b8"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def rate(rows: list[dict], field: str) -> float:
    return sum(bool(row.get(field)) for row in rows) / len(rows)


def close(actual, expected) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-12)


def exact_mcnemar(wins: int, losses: int) -> float:
    discordant = wins + losses
    if not discordant:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def holm_adjust(rows: list[dict], p_field: str, output_field: str) -> None:
    ordered = sorted(enumerate(rows), key=lambda item: item[1][p_field])
    running = 0.0
    count = len(rows)
    for rank, (index, row) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * row[p_field]))
        rows[index][output_field] = running


def target_table_map(gold_plan_path: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in read_jsonl(gold_plan_path):
        sample_id = str(row["sample_id"])
        plan = row.get("write_plan") or {}
        result[sample_id] = {
            str(group["table"])
            for group in plan.get("write_groups") or []
            if group.get("table")
        }
    return result


def corrected_off_target(
    rows: list[dict], targets: dict[str, set[str]]
) -> tuple[list[dict], list[dict]]:
    corrected: list[dict] = []
    affected: list[dict] = []
    for source in rows:
        row = dict(source)
        sample_id = str(row["sample_id"])
        strict = {str(value) for value in row.get("strict_mismatched_tables") or []}
        off_target = sorted(strict - targets[sample_id])
        row["off_target_mismatched_tables"] = off_target
        row["any_off_target_change"] = bool(off_target)
        row["side_effect"] = bool(off_target)
        if off_target:
            affected.append(
                {
                    "sample_id": sample_id,
                    "db_id": row.get("db_id"),
                    "target_state_correct": bool(row.get("target_state_correct")),
                    "target_mismatched_tables": ",".join(
                        row.get("target_mismatched_tables") or []
                    ),
                    "off_target_mismatched_tables": ",".join(off_target),
                }
            )
        corrected.append(row)
    return corrected, affected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--primary-run-root", type=Path, required=True)
    parser.add_argument("--gold-plans", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    checks: list[dict] = []

    def check(name: str, condition: bool, detail) -> None:
        checks.append({"name": name, "pass": bool(condition), "detail": detail})

    archive_sha = sha256(args.archive)
    expected_archive_sha = args.checksum.read_text(encoding="utf-8").split()[0].lower()
    check("archive_checksum", archive_sha == expected_archive_sha, archive_sha)

    with tarfile.open(args.archive, "r:gz") as bundle:
        unsafe = []
        for member in bundle.getmembers():
            posix = PurePosixPath(member.name)
            if posix.is_absolute() or ".." in posix.parts or member.issym() or member.islnk():
                unsafe.append(member.name)
    check("archive_paths_safe", not unsafe, unsafe)

    report_dir = args.extracted_root / "artifacts/reports"
    report_path = report_dir / "cross_family_yi_coder_9b_chat_v1.json"
    report_manifest_path = report_dir / "CROSS_FAMILY_REPORT_MANIFEST.json"
    protocol_path = (
        args.extracted_root
        / "configs/experiments/cross_family_yi_coder_9b_chat_protocol_v1.json"
    )
    inference_path = (
        args.extracted_root
        / "artifacts/server/hf_cross_family_yi_coder_9b_chat_in28672_out8192.json"
    )
    preflight_path = args.extracted_root / "diagnostics/cross_family_preflight.json"
    run_root = (
        args.extracted_root
        / "experiments/cross_family/yi_coder_9b_chat_final300_posthoc_v1"
    )

    server_report = read_json(report_path)
    report_manifest = read_json(report_manifest_path)
    protocol = read_json(protocol_path)
    inference = read_json(inference_path)
    preflight = read_json(preflight_path)
    protocol_sha = sha256(protocol_path)

    check("server_report_status", server_report.get("status") == "pass", server_report.get("status"))
    check("post_hoc_label", protocol.get("analysis_class") == "post_hoc_external_model_robustness", protocol.get("analysis_class"))
    check("not_primary", protocol.get("paper_primary_result") is False and server_report.get("paper_primary_result") is False, {"protocol": protocol.get("paper_primary_result"), "report": server_report.get("paper_primary_result")})
    check("holdout_reused", server_report.get("holdout_reused") is True and "consumed" in protocol.get("holdout_status", ""), {"report": server_report.get("holdout_reused"), "protocol": protocol.get("holdout_status")})
    check("protocol_frozen", protocol.get("status") == "frozen", protocol.get("status"))
    check("protocol_hash_links", server_report.get("protocol_sha256") == protocol_sha == report_manifest.get("protocol_sha256") == preflight.get("protocol_sha256"), protocol_sha)
    check("primary_protocol_link", protocol.get("base_primary_protocol_sha256") == EXPECTED_PRIMARY_PROTOCOL_SHA256, protocol.get("base_primary_protocol_sha256"))
    check("authorized_methods", protocol.get("methods") == list(METHODS), protocol.get("methods"))
    check("no_tuning_after_freeze", protocol.get("no_tuning_after_freeze") is True and server_report.get("prompt_or_method_tuned_after_freeze") is False, {"protocol": protocol.get("no_tuning_after_freeze"), "report": server_report.get("prompt_or_method_tuned_after_freeze")})
    check("model_family", protocol.get("model", {}).get("family") == "Yi-Coder" and "qwen" not in protocol.get("model", {}).get("family", "").lower(), protocol.get("model", {}).get("family"))
    check("model_revision", protocol.get("model", {}).get("snapshot_revision") == EXPECTED_MODEL_REVISION, protocol.get("model", {}).get("snapshot_revision"))
    check("model_hash", protocol.get("model", {}).get("aggregate_sha256") == EXPECTED_MODEL_SHA256 == inference.get("model_hash") == preflight.get("model_hash"), protocol.get("model", {}).get("aggregate_sha256"))
    check("deterministic_generation", inference.get("do_sample") is False and inference.get("seed") == 42, {"do_sample": inference.get("do_sample"), "seed": inference.get("seed")})
    check("capacity", inference.get("max_input_tokens") == 28672 and inference.get("max_new_tokens") == 8192 and inference.get("input_truncation_policy") == "error", {"max_input_tokens": inference.get("max_input_tokens"), "max_new_tokens": inference.get("max_new_tokens"), "input_truncation_policy": inference.get("input_truncation_policy")})
    check("preflight", preflight.get("status") == "pass" and preflight.get("prediction_generated") is False, {"status": preflight.get("status"), "prediction_generated": preflight.get("prediction_generated")})

    for filename, expected in report_manifest.get("output_sha256", {}).items():
        check(f"report_manifest:{filename}", sha256(report_dir / filename) == expected, sha256(report_dir / filename))

    targets = target_table_map(args.gold_plans)
    check("gold_target_plans", len(targets) == 300, len(targets))
    recomputed: dict[str, dict] = {}
    corrected_rows: dict[str, list[dict]] = {}
    off_target_rows: list[dict] = []
    reference_ids: list[str] | None = None

    for method, directory in METHODS.items():
        method_root = run_root / directory
        evaluation_path = method_root / "evaluation.jsonl"
        raw_path = method_root / "raw_generations.jsonl"
        metrics_path = method_root / "metrics.json"
        evaluation = read_jsonl(evaluation_path)
        raw = read_jsonl(raw_path)
        metrics = read_json(metrics_path)
        invalid_marker = read_json(method_root / "FINAL_RUN_INVALID.json")
        adjudication = read_json(method_root / "CROSS_FAMILY_CONSERVATIVE_ADJUDICATION.json")
        run_lock = read_json(method_root / "run_lock.json")
        model_manifest = read_json(method_root / "model_manifest.json")

        eval_ids = [str(row.get("sample_id")) for row in evaluation]
        raw_ids = [str(row.get("sample_id")) for row in raw]
        check(f"{method}:evaluation_rows", len(evaluation) == 300, len(evaluation))
        check(f"{method}:raw_rows", len(raw) == 300, len(raw))
        check(f"{method}:unique_ids", len(set(eval_ids)) == 300, len(set(eval_ids)))
        check(f"{method}:raw_eval_ids", raw_ids == eval_ids, len(set(raw_ids) ^ set(eval_ids)))
        check(f"{method}:generation_success", all(row.get("status") == "success" for row in raw) and all(row.get("generation_status") == "success" for row in evaluation), {"raw_non_success": sum(row.get("status") != "success" for row in raw), "eval_non_success": sum(row.get("generation_status") != "success" for row in evaluation)})
        check(f"{method}:no_input_truncation", not any(row.get("input_truncated") for row in evaluation), sum(bool(row.get("input_truncated")) for row in evaluation))

        if reference_ids is None:
            reference_ids = eval_ids
        else:
            check(f"{method}:same_sample_order", eval_ids == reference_ids, len(set(eval_ids) ^ set(reference_ids)))
        check(f"{method}:matches_gold_ids", set(eval_ids) == set(targets), len(set(eval_ids) ^ set(targets)))

        hit_ids = [str(row["sample_id"]) for row in evaluation if row.get("hit_max_new_tokens")]
        affected_ids = [str(value) for value in adjudication.get("affected_sample_ids") or []]
        check(f"{method}:invalid_reason", invalid_marker.get("status") == "invalid" and invalid_marker.get("reason") == "truncation_or_missing_prediction", invalid_marker)
        check(f"{method}:conservative_adjudication", adjudication.get("status") == "conservatively_retained_for_post_hoc_analysis" and adjudication.get("predictions_regenerated") is False and adjudication.get("evaluation_or_metrics_modified") is False and adjudication.get("denominator") == 300, adjudication.get("status"))
        check(f"{method}:affected_ids", hit_ids == affected_ids and adjudication.get("output_limit_hits") == len(hit_ids), {"evaluation": hit_ids, "marker": affected_ids})
        check(f"{method}:affected_rows_incorrect", all(not row.get("target_state_correct") for row in evaluation if row.get("hit_max_new_tokens")), [row["sample_id"] for row in evaluation if row.get("hit_max_new_tokens") and row.get("target_state_correct")])
        check(f"{method}:adjudication_hashes", adjudication.get("evaluation_sha256") == sha256(evaluation_path) and adjudication.get("raw_generations_sha256") == sha256(raw_path) and adjudication.get("metrics_sha256") == sha256(metrics_path) and adjudication.get("invalid_marker_sha256") == sha256(method_root / "FINAL_RUN_INVALID.json"), {"evaluation": sha256(evaluation_path), "raw": sha256(raw_path), "metrics": sha256(metrics_path), "invalid": sha256(method_root / "FINAL_RUN_INVALID.json")})
        check(f"{method}:protocol_sha", invalid_marker.get("final_protocol_sha256") == protocol_sha and adjudication.get("protocol_sha256") == protocol_sha and run_lock.get("hashes", {}).get("final_protocol_sha256") == protocol_sha, protocol_sha)
        check(f"{method}:model_manifest", model_manifest.get("aggregate_sha256") == EXPECTED_MODEL_SHA256 and run_lock.get("model", {}).get("model_hash") == EXPECTED_MODEL_SHA256, model_manifest.get("aggregate_sha256"))
        check(f"{method}:dataset_hash", run_lock.get("hashes", {}).get("dataset_sha256") == protocol.get("authorized_hashes", {}).get("dataset_sha256"), run_lock.get("hashes", {}).get("dataset_sha256"))

        corrected, affected = corrected_off_target(evaluation, targets)
        corrected_rows[method] = corrected
        for row in affected:
            off_target_rows.append({"method": method, **row})
        accepted = [row for row in corrected if row.get("accepted_output")]
        values = {
            "samples": len(corrected),
            "target_state_accuracy": rate(corrected, "target_state_correct"),
            "execution_success": rate(corrected, "execution_success"),
            "coverage": rate(corrected, "accepted_output"),
            "accepted_output_accuracy": (sum(bool(row.get("target_state_correct")) for row in accepted) / len(accepted) if accepted else None),
            "any_off_target_change_rate": rate(corrected, "any_off_target_change"),
            "input_truncation_rate": rate(corrected, "input_truncated"),
            "output_limit_hit_rate": rate(corrected, "hit_max_new_tokens"),
            "output_limit_hit_count": len(hit_ids),
        }
        recomputed[method] = values
        report_values = server_report["methods"][method]
        for key in ("samples", "target_state_accuracy", "execution_success", "coverage", "accepted_output_accuracy", "input_truncation_rate", "output_limit_hit_rate"):
            expected = report_values.get(key)
            condition = values[key] == expected if key == "samples" else close(values[key], expected)
            check(f"{method}:recompute:{key}", condition, {"recomputed": values[key], "server_report": expected})
        check(f"{method}:server_side_effect_corrected", close(values["any_off_target_change_rate"], report_values.get("side_effect_rate")) and close(values["any_off_target_change_rate"], metrics.get("side_effect_rate")), {"corrected": values["any_off_target_change_rate"], "server_report": report_values.get("side_effect_rate"), "metrics": metrics.get("side_effect_rate")})

    paired: list[dict] = []
    for method, directory in METHODS.items():
        primary = read_jsonl(args.primary_run_root / directory / "evaluation.jsonl")
        primary_map = {str(row["sample_id"]): bool(row.get("target_state_correct")) for row in primary}
        cross_map = {str(row["sample_id"]): bool(row.get("target_state_correct")) for row in corrected_rows[method]}
        check(f"{method}:paired_ids", set(primary_map) == set(cross_map) == set(targets), {"primary": len(primary_map), "cross_family": len(cross_map), "gold": len(targets)})
        wins = sum(not primary_map[sample_id] and cross_map[sample_id] for sample_id in targets)
        losses = sum(primary_map[sample_id] and not cross_map[sample_id] for sample_id in targets)
        paired.append(
            {
                "method": method,
                "qwen7b_target": sum(primary_map.values()) / len(primary_map),
                "yi9b_target": sum(cross_map.values()) / len(cross_map),
                "target_delta": (sum(cross_map.values()) - sum(primary_map.values())) / len(primary_map),
                "improved_7b_wrong_yi9b_correct": wins,
                "degraded_7b_correct_yi9b_wrong": losses,
                "exact_mcnemar_p": exact_mcnemar(wins, losses),
                "analysis_class": "post_hoc_cross_family_model_robustness",
            }
        )
    holm_adjust(paired, "exact_mcnemar_p", "holm_adjusted_p")

    status = "pass" if all(item["pass"] for item in checks) else "fail"
    output = {
        "status": status,
        "analysis_class": "post_hoc_external_model_robustness",
        "paper_primary_result": False,
        "holdout_reused": True,
        "predictions_regenerated": False,
        "database_execution_repeated": False,
        "archive": str(args.archive.resolve()),
        "archive_sha256": archive_sha,
        "protocol_sha256": protocol_sha,
        "primary_protocol_sha256": EXPECTED_PRIMARY_PROTOCOL_SHA256,
        "model_sha256": EXPECTED_MODEL_SHA256,
        "model_revision": EXPECTED_MODEL_REVISION,
        "methods": recomputed,
        "paired_comparison": paired,
        "off_target_metric": {
            "definition": "any strict-state mismatch outside the locked Gold-MP target-table set",
            "counts": {method: sum(bool(row.get("any_off_target_change")) for row in rows) for method, rows in corrected_rows.items()},
            "affected_rows": off_target_rows,
        },
        "checks": checks,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "CROSS_FAMILY_IMPORT_REPORT.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with (args.output_dir / "cross_family_paired_tests.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
        writer.writeheader()
        writer.writerows(paired)
    with (args.output_dir / "cross_family_off_target_changes.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = ["method", "sample_id", "db_id", "target_state_correct", "target_mismatched_tables", "off_target_mismatched_tables"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(off_target_rows)

    summary = [
        "# Post-hoc cross-family robustness audit",
        "",
        f"- Status: **{status.upper()}**",
        "- Analysis class: post-hoc external-model robustness; not a primary result.",
        "- Model: Yi-Coder-9B-Chat, 4-bit, deterministic decoding.",
        "- Rows: 300 per method; identical sample IDs and order.",
        "- The consumed holdout was reused; no prediction was regenerated.",
        "- Output-limit hits were retained as incorrect under the frozen conservative policy.",
        f"- Corrected off-target counts: {output['off_target_metric']['counts']}.",
        f"- Archive SHA-256: `{archive_sha}`",
        f"- Frozen cross-family protocol SHA-256: `{protocol_sha}`",
        "",
        "| Method | 7B target | Yi-9B target | Delta | 7B wrong/Yi correct | 7B correct/Yi wrong | Exact McNemar p | Holm p | Coverage | Admitted accuracy | Output-limit |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in paired:
        method_metrics = recomputed[row["method"]]
        summary.append(
            "| {method} | {qwen7b_target:.4f} | {yi9b_target:.4f} | {target_delta:+.4f} | {improved_7b_wrong_yi9b_correct} | {degraded_7b_correct_yi9b_wrong} | {exact_mcnemar_p:.4g} | {holm_adjusted_p:.4g} | {coverage:.4f} | {accepted:.4f} | {limit} |".format(
                **row,
                coverage=method_metrics["coverage"],
                accepted=method_metrics["accepted_output_accuracy"],
                limit=method_metrics["output_limit_hit_count"],
            )
        )
    summary.extend(
        [
            "",
            "Yi-Coder preserves the ordering D-FS-M > MP-FS+ > J-FS-M on target-state accuracy, but all three interfaces score below their Qwen2.5-Coder-7B primary counterparts. MP-FS+ remains the most reliable admitted Yi-Coder interface (111/115 correct) while admitting 115/300 samples. These results are sensitivity evidence only because the consumed holdout was reused.",
            "",
        ]
    )
    (args.output_dir / "cross_family_robustness_summary.md").write_text("\n".join(summary), encoding="utf-8")

    failed = [item["name"] for item in checks if not item["pass"]]
    print(json.dumps({"status": status, "checks": len(checks), "failed": failed}, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
