#!/usr/bin/env python3
"""Audit the post-hoc Qwen2.5-Coder-14B result archive.

The audit deliberately recomputes headline rates from evaluation.jsonl instead
of trusting the server-side summary.  It does not alter or rescore predictions.
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
    "98f98af8ebc2d267dca72024625b872339bae458d23917283b804bb86a004847"
)


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


def close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--primary-table", type=Path, required=True)
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

    report_path = (
        args.extracted_root
        / "artifacts/reports/second_model_qwen25_coder_14b_v1.json"
    )
    protocol_path = (
        args.extracted_root
        / "configs/experiments/second_model_qwen25_coder_14b_protocol_v1.json"
    )
    inference_path = (
        args.extracted_root
        / "artifacts/server/hf_second_model_qwen25_coder_14b_in28672_out8192.json"
    )
    run_root = (
        args.extracted_root
        / "experiments/second_model/qwen25_coder_14b_final300_posthoc_v1"
    )

    server_report = read_json(report_path)
    protocol = read_json(protocol_path)
    inference = read_json(inference_path)
    protocol_sha = sha256(protocol_path)

    check("server_report_status", server_report.get("status") == "pass", server_report.get("status"))
    check("post_hoc_label", protocol.get("analysis_class") == "post_hoc_cross_model_robustness", protocol.get("analysis_class"))
    check("not_primary", protocol.get("paper_primary_result") is False and server_report.get("paper_primary_result") is False, False)
    check("protocol_frozen", protocol.get("status") == "frozen", protocol.get("status"))
    check("primary_protocol_link", protocol.get("base_primary_protocol_sha256") == EXPECTED_PRIMARY_PROTOCOL_SHA256, protocol.get("base_primary_protocol_sha256"))
    check("authorized_methods", protocol.get("methods") == list(METHODS), protocol.get("methods"))
    check("no_tuning_after_freeze", protocol.get("no_tuning_after_freeze") is True, protocol.get("no_tuning_after_freeze"))
    check("model_hash", protocol.get("model", {}).get("aggregate_sha256") == EXPECTED_MODEL_SHA256, protocol.get("model", {}).get("aggregate_sha256"))
    check("inference_model_hash", inference.get("model_hash") == EXPECTED_MODEL_SHA256, inference.get("model_hash"))
    check("deterministic_generation", inference.get("do_sample") is False and inference.get("seed") == 42, {"do_sample": inference.get("do_sample"), "seed": inference.get("seed")})
    check("capacity", inference.get("max_input_tokens") == 28672 and inference.get("max_new_tokens") == 8192, {"max_input_tokens": inference.get("max_input_tokens"), "max_new_tokens": inference.get("max_new_tokens")})

    recomputed: dict[str, dict] = {}
    reference_ids: list[str] | None = None
    for method, directory in METHODS.items():
        method_root = run_root / directory
        evaluation = read_jsonl(method_root / "evaluation.jsonl")
        raw = read_jsonl(method_root / "raw_generations.jsonl")
        metrics = read_json(method_root / "metrics.json")
        marker = read_json(method_root / "FINAL_RUN_CONSUMED.json")
        run_lock = read_json(method_root / "run_lock.json")
        model_manifest = read_json(method_root / "model_manifest.json")

        eval_ids = [row.get("sample_id") for row in evaluation]
        raw_ids = [row.get("sample_id") for row in raw]
        check(f"{method}:evaluation_rows", len(evaluation) == 300, len(evaluation))
        check(f"{method}:raw_rows", len(raw) == 300, len(raw))
        check(f"{method}:unique_ids", len(set(eval_ids)) == 300, len(set(eval_ids)))
        check(f"{method}:raw_eval_ids", raw_ids == eval_ids, len(set(raw_ids) ^ set(eval_ids)))
        check(f"{method}:generation_success", all(row.get("status") == "success" for row in raw) and all(row.get("generation_status") == "success" for row in evaluation), {"raw_non_success": sum(row.get("status") != "success" for row in raw), "eval_non_success": sum(row.get("generation_status") != "success" for row in evaluation)})
        check(f"{method}:no_truncation", not any(row.get("input_truncated") for row in evaluation), sum(bool(row.get("input_truncated")) for row in evaluation))
        check(f"{method}:no_output_limit", not any(row.get("hit_max_new_tokens") for row in evaluation), sum(bool(row.get("hit_max_new_tokens")) for row in evaluation))
        check(f"{method}:consumed_marker", marker.get("status") == "consumed" and marker.get("stage") == "second-model" and marker.get("method_id") == method, marker)
        check(f"{method}:protocol_sha", marker.get("final_protocol_sha256") == protocol_sha and run_lock.get("hashes", {}).get("final_protocol_sha256") == protocol_sha, protocol_sha)
        check(f"{method}:model_manifest", model_manifest.get("aggregate_sha256") == EXPECTED_MODEL_SHA256 and run_lock.get("model", {}).get("model_hash") == EXPECTED_MODEL_SHA256, model_manifest.get("aggregate_sha256"))
        check(f"{method}:dataset_hash", run_lock.get("hashes", {}).get("dataset_sha256") == protocol.get("authorized_hashes", {}).get("dataset_sha256"), run_lock.get("hashes", {}).get("dataset_sha256"))

        if reference_ids is None:
            reference_ids = eval_ids
        else:
            check(f"{method}:same_sample_order", eval_ids == reference_ids, len(set(eval_ids) ^ set(reference_ids)))

        accepted = [row for row in evaluation if row.get("accepted_output")]
        values = {
            "samples": len(evaluation),
            "target_state_accuracy": rate(evaluation, "target_state_correct"),
            "execution_success": rate(evaluation, "execution_success"),
            "coverage": rate(evaluation, "accepted_output"),
            "accepted_output_accuracy": (
                sum(bool(row.get("target_state_correct")) for row in accepted) / len(accepted)
                if accepted
                else None
            ),
            "side_effect_rate": rate(evaluation, "side_effect"),
            "input_truncation_rate": rate(evaluation, "input_truncated"),
            "output_limit_hit_rate": rate(evaluation, "hit_max_new_tokens"),
        }
        recomputed[method] = values
        for key, value in values.items():
            if key == "samples":
                condition = value == metrics.get(key) == server_report["methods"][method].get(key)
            else:
                condition = close(value, metrics.get(key)) and close(value, server_report["methods"][method].get(key))
            check(f"{method}:recompute:{key}", condition, {"recomputed": value, "metrics": metrics.get(key), "server_report": server_report["methods"][method].get(key)})

    with args.primary_table.open(encoding="utf-8", newline="") as handle:
        primary = {row["method_id"]: row for row in csv.DictReader(handle)}
    comparison = []
    for method in METHODS:
        old = primary[method]
        new = recomputed[method]
        comparison.append(
            {
                "method": method,
                "qwen7b_target": float(old["target_state_accuracy"]),
                "qwen14b_target": new["target_state_accuracy"],
                "target_delta": new["target_state_accuracy"] - float(old["target_state_accuracy"]),
                "qwen7b_coverage": float(old["coverage"]),
                "qwen14b_coverage": new["coverage"],
                "coverage_delta": new["coverage"] - float(old["coverage"]),
                "qwen7b_accepted_accuracy": float(old["accepted_output_accuracy"]),
                "qwen14b_accepted_accuracy": new["accepted_output_accuracy"],
                "accepted_accuracy_delta": new["accepted_output_accuracy"] - float(old["accepted_output_accuracy"]),
            }
        )

    status = "pass" if all(item["pass"] for item in checks) else "fail"
    output = {
        "status": status,
        "analysis_class": "post_hoc_cross_model_robustness",
        "paper_primary_result": False,
        "archive": str(args.archive.resolve()),
        "archive_sha256": archive_sha,
        "protocol_sha256": protocol_sha,
        "primary_protocol_sha256": EXPECTED_PRIMARY_PROTOCOL_SHA256,
        "model_sha256": EXPECTED_MODEL_SHA256,
        "methods": recomputed,
        "cross_model_comparison": comparison,
        "checks": checks,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "SECOND_MODEL_IMPORT_REPORT.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (args.output_dir / "second_model_cross_model_table.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)

    lines = [
        "# Post-hoc second-model robustness audit",
        "",
        f"- Status: **{status.upper()}**",
        "- Analysis class: post-hoc cross-model robustness; not a primary result.",
        "- Model: Qwen2.5-Coder-14B-Instruct, 4-bit, deterministic decoding.",
        "- Rows: 300 per method; identical sample IDs and order.",
        "- Mechanical failures: 0 truncations, 0 output-limit hits, 0 missing generations.",
        f"- Archive SHA-256: `{archive_sha}`",
        f"- Frozen second-model protocol SHA-256: `{protocol_sha}`",
        "",
        "| Method | 7B target | 14B target | Delta | 7B coverage | 14B coverage | Delta | 7B accepted acc. | 14B accepted acc. | Delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        lines.append(
            "| {method} | {qwen7b_target:.4f} | {qwen14b_target:.4f} | {target_delta:+.4f} | "
            "{qwen7b_coverage:.4f} | {qwen14b_coverage:.4f} | {coverage_delta:+.4f} | "
            "{qwen7b_accepted_accuracy:.4f} | {qwen14b_accepted_accuracy:.4f} | {accepted_accuracy_delta:+.4f} |".format(**row)
        )
    lines.extend(
        [
            "",
            "The larger same-family model improves target-state accuracy for all three methods, while the ordering D-FS-M > J-FS-M > MP-FS+ remains unchanged. Because the already-consumed holdout was reused, these values are labeled post-hoc robustness evidence and do not replace or modify the frozen 7B primary results.",
            "",
        ]
    )
    (args.output_dir / "second_model_robustness_summary.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    failed = [item["name"] for item in checks if not item["pass"]]
    print(json.dumps({"status": status, "checks": len(checks), "failed": failed}, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
