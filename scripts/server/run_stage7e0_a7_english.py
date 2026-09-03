#!/usr/bin/env python3
"""Stage7E0-A7 one-call final A5 feasibility runner."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.v2_a1.types import V2A1Error  # noqa: E402
from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import (  # noqa: E402
    canonical_json,
    oracle_column_conditioned_path,
    render_phase_o_messages,
    sha256_file,
)
from scripts.data.build_stage7e0_a7_final_a5_real_generation_feasibility import (  # noqa: E402
    EXPECTED_PRIMARY_COUNT,
    PRIMARY_RESULT_DIR_NAME,
    STAGE_NAME,
    read_jsonl,
    write_json,
    write_jsonl,
)
from scripts.server.run_stage7e0_a4_english import DEFAULT_MODEL_PATH, runtime_versions  # noqa: E402
from scripts.server.run_stage7e0_a6_english import (  # noqa: E402
    CONSTRAINED_BACKEND_ID,
    PHASE_O_MAX_NEW_TOKENS,
    CallResult,
    ConstrainedTransformersChatGenerator,
    LabelMockGenerator,
    OneCallGenerator,
    assert_result_root_policy,
    parse_phase_o_column_conditioned_output,
    validate_generation_config,
)


def load_stage7e0_a7_rows(root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    rows = read_jsonl(root / STAGE_NAME / "FRESH_ENGLISH_A7_PRIMARY_FEASIBILITY_SET.jsonl")
    if len(rows) != EXPECTED_PRIMARY_COUNT:
        raise SystemExit(f"STOP: expected {EXPECTED_PRIMARY_COUNT} A7 rows, found {len(rows)}")
    for row in rows:
        if not str(row.get("sample_id")).startswith("stage7e0_a7_fresh_english_"):
            raise SystemExit(f"STOP: A7 row id prefix drifted for {row.get('sample_id')}")
        if set(row.get("model_side_input", {})) != {"question", "schema_inventory", "candidate_inventory_text"}:
            raise SystemExit(f"STOP: model-side leakage boundary changed for {row.get('sample_id')}")
        if row.get("runtime_constraints", {}).get("phase_m_removed") is not True:
            raise SystemExit(f"STOP: Phase M must remain removed for {row.get('sample_id')}")
    return rows


def failure_code_for_exception(exc: Exception) -> str:
    if isinstance(exc, V2A1Error):
        if exc.reason_code == "phase_o_json_extract":
            return "MODEL_PARSE_FAILURE"
        if exc.reason_code in {"phase_o_schema_failure", "phase_o_duplicate_span_ref_reuse", "required_column_omitted"}:
            return "INVALID_SELECTION"
        return exc.reason_code
    if isinstance(exc, sqlite3.Error):
        return "EXECUTION_FAILURE"
    text = str(exc)
    if "required_column_omitted" in text:
        return "INVALID_SELECTION"
    if "Unknown span_refs" in text or "Duplicate span_refs" in text:
        return "SPAN_RESOLUTION_FAILURE"
    if "invalid literal" in text or "could not convert" in text:
        return "TYPE_MATERIALIZATION_FAILURE"
    return "STATE_MISMATCH"


def failed_result(row: dict[str, Any], failure_code: str, error: str | None, prompt_hash: str, raw_o: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": row["sample_id"],
        "status": "FAIL",
        "failure_code": failure_code,
        "error": error,
        "prompt_hash": prompt_hash,
        "candidate_domain_size": row["runtime_constraints"]["candidate_count"],
        "raw_model_output": raw_o.get("raw_output"),
        "parsed_selection": None,
        "selected_span": None,
        "typed_value": None,
        "verification_result": None,
        "compiled_sql": None,
        "parameters": None,
        "preflight_result": None,
        "execution_result": None,
        "target_state_correct": False,
        "token_count": {"input_tokens": raw_o.get("input_tokens"), "output_tokens": raw_o.get("output_tokens")},
        "latency": raw_o.get("latency_sec"),
    }


def dict_field(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def evaluate_case(row: dict[str, Any], generator: OneCallGenerator, *, phase_o_max_new_tokens: int, root: Path = PROJECT_ROOT) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    sample_id = row["sample_id"]
    messages, _user, prompt_hash = render_phase_o_messages(row)
    call = generator.generate(sample_id=sample_id, messages=messages, max_new_tokens=phase_o_max_new_tokens, row=row)
    raw_o = asdict(call) | {"messages_sha256": prompt_hash, "prompt_hash": prompt_hash}
    prompt_row = {"sample_id": sample_id, "prompt_hash": prompt_hash, "message_count": len(messages)}
    candidate_row = {
        "sample_id": sample_id,
        "candidate_domain": row["runtime_constraints"]["candidate_inventory"],
        "candidate_domain_size": row["runtime_constraints"]["candidate_count"],
        "phase_o_schema_sha256": row["runtime_constraints"].get("phase_o_schema_sha256"),
    }
    if call.status != "success":
        return failed_result(row, "MODEL_PARSE_FAILURE", call.error, prompt_hash, raw_o), raw_o, candidate_row, prompt_row
    try:
        phase_o = parse_phase_o_column_conditioned_output(call.raw_output, row["runtime_constraints"]["phase_o_schema"])
        predicted = json.loads(canonical_json(row))
        predicted["label_side_expected"]["phase_o"] = phase_o
        db_path = root / STAGE_NAME / row["synthetic_db_spec"]["sqlite_db_path"]
        oracle = oracle_column_conditioned_path(predicted, db_path)
    except (V2A1Error, ValueError, sqlite3.Error) as exc:
        return failed_result(row, failure_code_for_exception(exc), str(exc), prompt_hash, raw_o), raw_o, candidate_row, prompt_row
    expected = row["label_side_expected"]["phase_o"]
    checks = {
        "operation_exact": phase_o["operation"] == expected["operation"],
        "table_ref_exact": phase_o["table_ref"] == expected["table_ref"],
        "column_span_refs_mapping_exact": phase_o["column_span_refs"] == expected["column_span_refs"],
        "resolver_pass": oracle["resolver"] == "PASS",
        "typed_materialization_pass": oracle["typed_materialization"] == "PASS",
        "completeness_pass": oracle["completeness"] == "PASS",
        "compile_pass": oracle["compilation"] == "PASS",
        "preflight_admitted": oracle["preflight"] == "ADMITTED",
        "target_state_correct": oracle["canonical_target_state_exact"],
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    return (
        {
            "sample_id": sample_id,
            "status": status,
            "failure_code": None if status == "PASS" else "STATE_MISMATCH",
            "checks": checks,
            "prompt_hash": prompt_hash,
            "candidate_domain_size": row["runtime_constraints"]["candidate_count"],
            "raw_model_output": call.raw_output,
            "parsed_selection": phase_o,
            "selected_span": oracle["resolved_column_spans"],
            "typed_value": [
                {**resolved, "value": value}
                for resolved, value in zip(oracle["resolved_column_spans"], oracle["compiled_parameters"], strict=False)
            ],
            "verification_result": {"completeness": oracle["completeness"], "resolver": oracle["resolver"]},
            "compiled_sql": oracle["compiled_sql"],
            "parameters": oracle["compiled_parameters"],
            "preflight_result": {"status": oracle["preflight"], "reason_code": oracle["preflight_reason_code"]},
            "execution_result": {"target_state_hash": oracle["observed_target_state_hash"]},
            "target_state_correct": oracle["canonical_target_state_exact"],
            "failure_stage": None if status == "PASS" else "acceptance_gate",
            "token_count": {"input_tokens": call.input_tokens, "output_tokens": call.output_tokens},
            "latency": call.latency_sec,
        },
        raw_o,
        candidate_row,
        prompt_row,
    )


def write_summary(result_root: Path, backend: str, metadata: dict[str, Any], cases: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    pass_count = sum(1 for row in cases if row["status"] == "PASS")
    target_correct = sum(1 for row in cases if row.get("target_state_correct") is True)
    summary = {
        "stage": STAGE_NAME,
        "status": "PASS" if target_correct == EXPECTED_PRIMARY_COUNT else "FAIL",
        "backend": backend,
        "protocol_backend": metadata.get("backend"),
        "target_state_correct_count": target_correct,
        "target_state_accuracy": f"{target_correct}/{EXPECTED_PRIMARY_COUNT}",
        "primary_pass_count": f"{pass_count}/{EXPECTED_PRIMARY_COUNT}",
        "parse_success_count": sum(1 for row in cases if row.get("parsed_selection") is not None),
        "valid_candidate_selection_count": sum(1 for row in cases if row.get("parsed_selection") is not None and row.get("failure_code") not in {"INVALID_SELECTION", "SPAN_RESOLUTION_FAILURE"}),
        "materialization_success_count": sum(1 for row in cases if row.get("typed_value") is not None),
        "completeness_pass_count": sum(1 for row in cases if dict_field(row, "checks").get("completeness_pass") is True),
        "compilation_success_count": sum(1 for row in cases if row.get("compiled_sql") is not None),
        "preflight_pass_count": sum(1 for row in cases if dict_field(row, "preflight_result").get("status") == "ADMITTED"),
        "execution_success_count": target_correct,
        "off_target_effects": 0,
        "rejected_samples": [row["sample_id"] for row in cases if row["status"] != "PASS"],
        "model_calls_total": len(raw_rows),
        "model_calls_per_sample": 1,
        "phase_m_invocations": 0,
        "retry_count": 0,
        "unexpected_fallback_count": 0,
        "eleven_of_twelve_allowed": False,
        "model_called": backend == "constrained_hf",
        "gpu_called": backend == "constrained_hf",
        "mock_uses_label_side_expected": backend == "mock",
        "raw_model_outputs_sha256": sha256_file(result_root / "raw" / "model_outputs.jsonl"),
        "per_sample_results_sha256": sha256_file(result_root / "results" / "per_sample_results.jsonl"),
    }
    write_json(result_root / "results" / "summary.json", summary)
    write_json(result_root / "results" / "failure_analysis.json", {"stage": STAGE_NAME, "failures": [row for row in cases if row["status"] != "PASS"]})
    (result_root / "results" / "summary.md").write_text(f"# Stage7E0-A7 Summary\n\nTarget-state accuracy: {summary['target_state_accuracy']}\nStatus: {summary['status']}\n", encoding="utf-8", newline="\n")
    return summary


def finalize_existing_result(args: argparse.Namespace, root: Path, result_root: Path) -> dict[str, Any]:
    rows = load_stage7e0_a7_rows(root)
    cases = read_jsonl(result_root / "results" / "per_sample_results.jsonl")
    raw_rows = read_jsonl(result_root / "raw" / "model_outputs.jsonl")
    manifest = json.loads((result_root / "run_manifest.json").read_text(encoding="utf-8"))
    frozen_ids = [row["sample_id"] for row in rows]
    if [row.get("sample_id") for row in cases] != frozen_ids:
        raise SystemExit("STOP: existing A7 result cannot be finalized because per-sample ids/count drifted")
    if [row.get("sample_id") for row in raw_rows] != frozen_ids:
        raise SystemExit("STOP: existing A7 result cannot be finalized because raw model-output ids/count drifted")
    backend = str(manifest.get("backend") or args.backend).lower()
    metadata = manifest.get("model") if isinstance(manifest.get("model"), dict) else {}
    write_jsonl(result_root / "runtime" / "token_usage.jsonl", [{"sample_id": row["sample_id"], **dict_field(row, "token_count")} for row in cases])
    write_jsonl(result_root / "runtime" / "latency.jsonl", [{"sample_id": row["sample_id"], "latency": row.get("latency")} for row in cases])
    write_json(result_root / "audits" / "model_call_audit.json", {"stage": STAGE_NAME, "status": "PASS", "model_calls_total": len(raw_rows), "model_calls_per_sample": 1, "phase_m_invocations": 0, "finalized_existing_result": True})
    write_json(result_root / "audits" / "retry_audit.json", {"stage": STAGE_NAME, "status": "PASS", "retry_count": 0, "allowed_retries": 0, "finalized_existing_result": True})
    write_json(result_root / "audits" / "denominator_audit.json", {"stage": STAGE_NAME, "status": "PASS", "expected_primary_n": EXPECTED_PRIMARY_COUNT, "observed_primary_n": len(cases), "silent_skip_count": 0, "dropped_sample_count": 0, "finalized_existing_result": True})
    return write_summary(result_root, backend, metadata, cases, raw_rows)


def run_stage7e0_a7(args: argparse.Namespace) -> dict[str, Any]:
    result_root = Path(args.result_root).resolve()
    backend = str(args.backend).lower()
    if not hasattr(args, "resume"):
        args.resume = False
    validate_generation_config(args)
    assert_result_root_policy(result_root, backend=backend, allow_inside_git=args.allow_result_root_inside_git)
    root = Path(getattr(args, "stage_root", PROJECT_ROOT)).resolve()
    rows = load_stage7e0_a7_rows(root)
    if getattr(args, "finalize_existing_result", False):
        if not result_root.exists():
            raise SystemExit("STOP: --finalize-existing-result requires an existing A7 result-root")
        return finalize_existing_result(args, root, result_root)
    if result_root.exists():
        raise SystemExit("STOP: result-root already exists; A7 allows exactly one official run directory")
    result_root.mkdir(parents=True, exist_ok=True)
    if backend == "mock":
        generator: OneCallGenerator = LabelMockGenerator(rows)
    else:
        generator = ConstrainedTransformersChatGenerator(model_name_or_path=args.model_name_or_path, quantization=args.quantization, trust_remote_code=args.trust_remote_code, max_input_tokens=args.max_input_tokens, seed=args.seed)
    metadata = generator.metadata()
    if backend == "constrained_hf" and metadata.get("backend") != CONSTRAINED_BACKEND_ID:
        raise SystemExit("STOP: constrained backend unavailable; no fallback to unconstrained generation")
    if backend == "constrained_hf" and metadata.get("cuda_available") is not True:
        raise SystemExit("STOP: real Stage7E0-A7 generation requires cuda_available=true")
    write_json(
        result_root / "run_manifest.json",
        {
            "stage": STAGE_NAME,
            "accepted_protocol_commit": args.accepted_protocol_commit,
            "backend": backend,
            "model": metadata,
            "runtime_versions": runtime_versions(),
            "primary_case_count": len(rows),
            "phase_m_removed": True,
            "retry": 0,
            "repair": "none",
            "phase_o_max_new_tokens": int(args.phase_o_max_new_tokens),
            "result_dir_name": PRIMARY_RESULT_DIR_NAME,
        },
    )
    cases: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    prompt_rows: list[dict[str, Any]] = []
    for row in rows:
        case_result, raw_o, candidate_row, prompt_row = evaluate_case(row, generator, phase_o_max_new_tokens=int(args.phase_o_max_new_tokens), root=root)
        cases.append(case_result)
        raw_rows.append(raw_o)
        candidate_rows.append(candidate_row)
        prompt_rows.append(prompt_row)
        write_jsonl(result_root / "results" / "per_sample_results.jsonl", cases)
        write_jsonl(result_root / "raw" / "model_outputs.jsonl", raw_rows)
        write_jsonl(result_root / "raw" / "candidate_domains.jsonl", candidate_rows)
        write_jsonl(result_root / "raw" / "prompts_or_prompt_hashes.jsonl", prompt_rows)
    write_jsonl(result_root / "runtime" / "token_usage.jsonl", [{"sample_id": row["sample_id"], **dict_field(row, "token_count")} for row in cases])
    write_jsonl(result_root / "runtime" / "latency.jsonl", [{"sample_id": row["sample_id"], "latency": row["latency"]} for row in cases])
    write_json(result_root / "runtime" / "environment.json", {"stage": STAGE_NAME, "backend": backend, "runtime_versions": runtime_versions(), "model": metadata})
    write_json(result_root / "audits" / "model_call_audit.json", {"stage": STAGE_NAME, "status": "PASS", "model_calls_total": len(raw_rows), "model_calls_per_sample": 1, "phase_m_invocations": 0})
    write_json(result_root / "audits" / "retry_audit.json", {"stage": STAGE_NAME, "status": "PASS", "retry_count": 0, "allowed_retries": 0})
    write_json(result_root / "audits" / "denominator_audit.json", {"stage": STAGE_NAME, "status": "PASS", "expected_primary_n": EXPECTED_PRIMARY_COUNT, "observed_primary_n": len(cases), "silent_skip_count": 0, "dropped_sample_count": 0})
    return write_summary(result_root, backend, metadata, cases, raw_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accepted-protocol-commit", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--backend", choices=["constrained_hf", "mock"], default="constrained_hf")
    parser.add_argument("--model-name-or-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--quantization", default="none")
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=PHASE_O_MAX_NEW_TOKENS)
    parser.add_argument("--max-input-tokens", type=int, default=28672)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--skip-git-assertions", action="store_true")
    parser.add_argument("--allow-result-root-inside-git", action="store_true")
    parser.add_argument("--finalize-existing-result", action="store_true", help="Finalize an existing complete A7 result-root without new model calls.")
    parser.add_argument("--stage-root", type=Path, default=PROJECT_ROOT, help="Root containing the frozen A7 stage directory.")
    parser.set_defaults(resume=False)
    args = parser.parse_args()
    print(json.dumps(run_stage7e0_a7(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
