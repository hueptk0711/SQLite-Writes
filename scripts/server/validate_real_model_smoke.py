from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, iter_jsonl, load_json


def _count_truthy(rows: list[dict[str, Any]], key: str) -> int:
    return sum(bool(row.get(key)) for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply technical, non-accuracy gates to a GPU smoke run."
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--selection-manifest",
        default="data/smoke/real_model_smoke15/selection_manifest.json",
    )
    parser.add_argument(
        "--runtime-source-manifest",
        default="artifacts/environment/runtime_source_server.json",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    manifest = load_json(run_dir / "manifest.json")
    metrics = load_json(run_dir / "metrics.json")
    model_manifest = load_json(run_dir / "model_manifest.json")
    selection = load_json(args.selection_manifest)
    runtime_source = load_json(args.runtime_source_manifest)
    raw = list(iter_jsonl(run_dir / "raw_generations.jsonl"))
    parsed = list(iter_jsonl(run_dir / "parsed_mapping_plans.jsonl"))
    evaluations = list(iter_jsonl(run_dir / "evaluation.jsonl"))
    verification = list(iter_jsonl(run_dir / "verification.jsonl"))

    by_evaluation = {
        str(row.get("sample_id") or ""): row for row in evaluations
    }
    by_verification = {
        str(row.get("sample_id") or ""): row for row in verification
    }
    expected_abstentions = list(selection.get("expected_abstention_ids") or [])
    expected_accepts = list(
        selection.get("expected_accept_and_execute_ids") or []
    )
    correctly_abstained = [
        sample_id
        for sample_id in expected_abstentions
        if not bool(
            (by_evaluation.get(sample_id) or {}).get("accepted_output", False)
        )
    ]
    accepted_probes = [
        sample_id
        for sample_id in expected_accepts
        if bool((by_evaluation.get(sample_id) or {}).get("accepted_output"))
    ]
    executed_probes = [
        sample_id
        for sample_id in expected_accepts
        if bool((by_evaluation.get(sample_id) or {}).get("execution_success"))
    ]
    correct_probes = [
        sample_id
        for sample_id in expected_accepts
        if bool(
            (by_evaluation.get(sample_id) or {}).get("target_state_correct")
        )
    ]
    clarification_reason_probe_ids = [
        sample_id
        for sample_id in expected_abstentions
        if any(
            str(error.get("error_code") or "") == "NEEDS_CLARIFICATION"
            for error in (
                (by_verification.get(sample_id) or {}).get("errors") or []
            )
            if isinstance(error, dict)
        )
    ]
    successful_generation = [
        row for row in raw if str(row.get("status") or "") == "success"
    ]
    output_token_total = sum(int(row.get("output_tokens") or 0) for row in raw)
    latency_total = sum(float(row.get("latency_sec") or 0.0) for row in raw)
    parse_successes = sum(
        str(row.get("parse_status") or "") == "success" for row in parsed
    )
    accepted = _count_truthy(evaluations, "accepted_output")
    executed = _count_truthy(evaluations, "execution_success")
    generation_failures = [
        {
            "sample_id": row.get("sample_id"),
            "status": row.get("status"),
            "error": row.get("error"),
        }
        for row in raw
        if str(row.get("status") or "") != "success"
    ]
    current_root = Path.cwd().resolve()
    expected_runtime_package_root = (
        current_root / "src" / "nldbwrite_v3"
    ).resolve()
    try:
        recorded_project_root = Path(
            str(runtime_source.get("project_root") or "")
        ).resolve()
        recorded_package_root = Path(
            str(runtime_source.get("package_root") or "")
        ).resolve()
        recorded_python = Path(
            str(runtime_source.get("python_executable") or "")
        ).resolve()
    except (OSError, RuntimeError, ValueError):
        recorded_project_root = Path()
        recorded_package_root = Path()
        recorded_python = Path()
    runtime_source_is_current_bundle = (
        runtime_source.get("status") == "ok"
        and runtime_source.get("source_is_current_bundle") is True
        and recorded_project_root == current_root
        and recorded_package_root == expected_runtime_package_root
        and recorded_python == Path(sys.executable).resolve()
    )
    expected_accept_set = set(expected_accepts)
    expected_abstention_set = set(expected_abstentions)

    checks = {
        "method_is_mp_fs_plus": manifest.get("method_id") == "MP-FS+",
        "backend_is_huggingface": (
            (manifest.get("inference") or {}).get("backend") == "hf"
        ),
        "model_identity_recorded": bool(
            model_manifest.get("aggregate_sha256")
            or model_manifest.get("revision")
        ),
        "sample_count_is_15": len(raw) == 15 and metrics.get("samples") == 15,
        "all_generation_calls_succeeded": len(successful_generation) == 15,
        "no_input_truncation": _count_truthy(raw, "input_truncated") == 0,
        "no_output_limit_hit": _count_truthy(raw, "hit_max_new_tokens") == 0,
        "real_output_tokens_recorded": output_token_total > 15,
        "real_latency_recorded": latency_total > 0.0,
        "structured_parse_rate_at_least_40_percent": parse_successes >= 6,
        "all_expected_accept_probes_accepted": (
            bool(expected_accepts)
            and set(accepted_probes) == expected_accept_set
        ),
        "all_expected_accept_probes_executed": (
            bool(expected_accepts)
            and set(executed_probes) == expected_accept_set
        ),
        "all_expected_accept_probes_target_state_correct": (
            bool(expected_accepts)
            and set(correct_probes) == expected_accept_set
        ),
        "all_clarification_probes_abstained": (
            bool(expected_abstentions)
            and set(correctly_abstained) == expected_abstention_set
        ),
        "all_clarification_probes_reported_needs_clarification": (
            bool(expected_abstentions)
            and set(clarification_reason_probe_ids)
            == expected_abstention_set
        ),
        "verification_artifact_complete": len(verification) == 15,
        "runtime_source_is_current_bundle": (
            runtime_source_is_current_bundle
        ),
    }
    result = {
        "status": "pass" if all(checks.values()) else "fail",
        "purpose": "technical_model_backend_validation_only",
        "paper_result_eligible": False,
        "checks": checks,
        "counts": {
            "samples": len(raw),
            "parse_successes": parse_successes,
            "accepted_outputs": accepted,
            "executed_outputs": executed,
            "total_output_tokens": output_token_total,
            "total_latency_sec": latency_total,
            "expected_abstentions": len(expected_abstentions),
            "correctly_abstained_probe_ids": correctly_abstained,
            "expected_accepts": len(expected_accepts),
            "accepted_probe_ids": accepted_probes,
            "executed_probe_ids": executed_probes,
            "target_state_correct_probe_ids": correct_probes,
            "needs_clarification_probe_ids": (
                clarification_reason_probe_ids
            ),
        },
        "runtime_source": runtime_source,
        "generation_failures": generation_failures,
    }
    dump_json(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
