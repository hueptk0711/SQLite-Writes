#!/usr/bin/env python3
"""Validate Stage ENG2C untouched development-dev evaluation package/results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng2c_untouched_dev_evaluation import EXPECTED_N, SCIENTIFIC_ARTIFACTS, STAGE_NAME  # noqa: E402
from scripts.data.build_stageeng2a_gretel_external_development_pilot import sha256_file  # noqa: E402
from scripts.server.run_eng2_final_method import MODEL_REVISION  # noqa: E402
from scripts.server.run_stageeng2c_dev100_evaluation import METHODS  # noqa: E402


FORBIDDEN_MODEL_KEYS = {"gold_sql", "gold_assignments", "gold_post_state", "target_state", "evaluator_side_expected", "label_side_expected"}
RESULT_FILES = {
    "raw/model_outputs.jsonl": EXPECTED_N * len(METHODS),
    "parsed/parsed_outputs.jsonl": EXPECTED_N * len(METHODS),
    "results/per_sample_results.jsonl": EXPECTED_N * len(METHODS),
    "efficiency/tokens.jsonl": EXPECTED_N * len(METHODS),
    "efficiency/latency.jsonl": EXPECTED_N * len(METHODS),
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(condition: bool, failures: list[dict[str, Any]], rule: str, **details: Any) -> None:
    if not condition:
        failures.append({"rule": rule, **details})


def validate_result_root(result_root: Path, failures: list[dict[str, Any]], *, require_hf: bool) -> None:
    check(result_root.is_dir(), failures, "missing_result_root", path=str(result_root))
    if not result_root.is_dir():
        return
    summary_path = result_root / "results" / "aggregate_results.json"
    check(summary_path.is_file(), failures, "missing_aggregate_results")
    if not summary_path.is_file():
        return
    summary = read_json(summary_path)
    check(summary.get("stage") == STAGE_NAME, failures, "result_stage_name", observed=summary.get("stage"))
    check(summary.get("status") == "PASS", failures, "result_status", observed=summary.get("status"))
    check(summary.get("denominator") == EXPECTED_N, failures, "result_denominator", observed=summary.get("denominator"))
    check(set(summary.get("methods", {}).keys()) == set(METHODS), failures, "result_methods", observed=sorted(summary.get("methods", {}).keys()))
    check(summary.get("primary_metric") == "strict_full_state_accuracy", failures, "primary_metric", observed=summary.get("primary_metric"))
    check(summary.get("model_calls_total") == EXPECTED_N * len(METHODS), failures, "model_calls_total", observed=summary.get("model_calls_total"))
    check(summary.get("model_calls_per_sample_per_method") == 1, failures, "model_calls_per_sample_per_method", observed=summary.get("model_calls_per_sample_per_method"))
    check(summary.get("retry_count") == 0, failures, "retry_count", observed=summary.get("retry_count"))
    if require_hf:
        check(summary.get("backend") == "hf", failures, "official_backend_not_hf", observed=summary.get("backend"))
        generator = summary.get("generation_metadata", {}).get("generator", {})
        check(generator.get("model_revision") == MODEL_REVISION, failures, "official_model_revision", observed=generator.get("model_revision"))
        check(generator.get("model_called") is True, failures, "official_model_called", observed=generator.get("model_called"))
        check(generator.get("model_revision_verified") is True, failures, "official_model_revision_verified")
        check(generator.get("tokenizer_revision_verified") is True, failures, "official_tokenizer_revision_verified")
        check(generator.get("chat_template_hash_verified") is True, failures, "official_chat_template_verified")
    for method_id, item in summary.get("methods", {}).items():
        check(item.get("samples") == EXPECTED_N, failures, "method_denominator", method_id=method_id, observed=item.get("samples"))
        check(item.get("model_calls") == EXPECTED_N, failures, "method_calls", method_id=method_id, observed=item.get("model_calls"))
        check("strict_full_state_accuracy" in item, failures, "missing_strict_metric", method_id=method_id)
        check("target_state_accuracy" in item, failures, "missing_target_metric", method_id=method_id)
    for rel, expected_rows in RESULT_FILES.items():
        path = result_root / rel
        check(path.is_file(), failures, "missing_result_file", path=rel)
        if path.is_file():
            check(len(read_jsonl(path)) == expected_rows, failures, "result_row_count", path=rel, observed=len(read_jsonl(path)), expected=expected_rows)
    for rel in [
        "audits/denominator_audit.json",
        "audits/call_retry_audit.json",
        "audits/model_identity_audit.json",
        "audits/evaluator_commonality.json",
        "audits/method_freeze_integrity.json",
    ]:
        path = result_root / rel
        check(path.is_file(), failures, "missing_result_audit", path=rel)
        if path.is_file():
            check(read_json(path).get("status") == "PASS", failures, "result_audit_status", path=rel, observed=read_json(path).get("status"))


def validate_stage(stage_dir: Path, *, skip_official: bool, require_official: bool, official_result_root: Path | None) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for rel in SCIENTIFIC_ARTIFACTS:
        check((stage_dir / rel).is_file(), failures, "missing_artifact", path=rel)
    if not (stage_dir / "ENG2C_DEV100_FREEZE.jsonl").is_file():
        return {"stage": STAGE_NAME, "status": "FAIL", "failures": failures}
    rows = read_jsonl(stage_dir / "ENG2C_DEV100_FREEZE.jsonl")
    manifest = read_jsonl(stage_dir / "ENG2C_DEV100_MANIFEST.jsonl")
    check(len(rows) == EXPECTED_N, failures, "frozen_row_count", observed=len(rows))
    check(len(manifest) == EXPECTED_N, failures, "manifest_row_count", observed=len(manifest))
    check([row.get("sample_id") for row in rows] == [row.get("sample_id") for row in manifest], failures, "manifest_freeze_order_or_ids")
    check(len({row.get("sample_id") for row in rows}) == EXPECTED_N, failures, "duplicate_sample_ids")
    for row in rows:
        model_keys = set((row.get("model_side_input") or {}).keys())
        check(model_keys == {"question", "schema_inventory", "candidate_inventory_text"}, failures, "model_side_input_keys", sample_id=row.get("sample_id"), keys=sorted(model_keys))
        check(not (FORBIDDEN_MODEL_KEYS & model_keys), failures, "model_side_gold_leakage", sample_id=row.get("sample_id"), keys=sorted(FORBIDDEN_MODEL_KEYS & model_keys))
        check(row.get("external_development_dev") is True, failures, "dev_flag_missing", sample_id=row.get("sample_id"))
        check(row.get("external_development_pilot") is False, failures, "pilot_flag_not_false", sample_id=row.get("sample_id"))
        check(row.get("gretel_source", {}).get("source_split") == "train", failures, "source_split", sample_id=row.get("sample_id"))
        check(row.get("runtime_constraints", {}).get("retry") == 0, failures, "retry_not_zero", sample_id=row.get("sample_id"))
        db_rel = row.get("synthetic_db_spec", {}).get("sqlite_db_path")
        check(bool(db_rel) and (stage_dir / db_rel).is_file(), failures, "missing_sqlite_db", sample_id=row.get("sample_id"), path=db_rel)
    protocol = read_json(stage_dir / "ENG2C_PROTOCOL_FREEZE.json")
    check(protocol.get("status") == "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN", failures, "protocol_status", observed=protocol.get("status"))
    check(protocol.get("primary_metric") == "strict_full_state_accuracy", failures, "protocol_primary_metric", observed=protocol.get("primary_metric"))
    check(protocol.get("official51_remains_unopened") is True, failures, "official51_not_guarded")
    check([item.get("method_id") for item in protocol.get("methods", [])] == list(METHODS), failures, "protocol_methods", observed=[item.get("method_id") for item in protocol.get("methods", [])])
    check(protocol.get("dataset", {}).get("denominator") == EXPECTED_N, failures, "protocol_denominator", observed=protocol.get("dataset", {}).get("denominator"))
    isolation = read_json(stage_dir / "audits" / "split_isolation.json")
    check(isolation.get("status") == "PASS", failures, "split_isolation_status", observed=isolation.get("status"))
    check(isolation.get("development_train_overlap_total") == 0, failures, "development_train_overlap", observed=isolation.get("development_train_overlap_total"))
    check(isolation.get("eng2a_pilot_overlap_total") == 0, failures, "eng2a_pilot_overlap", observed=isolation.get("eng2a_pilot_overlap_total"))
    check(isolation.get("official51_overlap_total") == 0, failures, "official51_overlap", observed=isolation.get("official51_overlap_total"))
    for method_id, filename in {
        "M0_DIRECT_ZERO": "m0_direct_zero.jsonl",
        "M0_DIRECT_FS": "m0_direct_fewshot.jsonl",
        "M1_J_FS": "m1_jfs.jsonl",
        "M2_FINAL_ENG2B": "m2_final_eng2b.jsonl",
    }.items():
        prompt_rows = read_jsonl(stage_dir / "prompts" / filename)
        check(len(prompt_rows) == EXPECTED_N, failures, "prompt_row_count", method_id=method_id, observed=len(prompt_rows))
    validate_result_root(stage_dir / "mock_dry_run", failures, require_hf=False)
    manifest_file = read_json(stage_dir / "MANIFEST.json")
    for item in manifest_file.get("files", []):
        rel = item["path"]
        path = stage_dir / rel
        if path.is_file():
            check(sha256_file(path) == item["sha256"], failures, "manifest_hash_mismatch", path=rel)
    if require_official:
        result_root = official_result_root or stage_dir / "official_server_run"
        validate_result_root(result_root, failures, require_hf=True)
    elif not skip_official and official_result_root is not None:
        validate_result_root(official_result_root, failures, require_hf=True)
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "dev100_n": len(rows),
        "methods": list(METHODS),
        "official_checked": require_official or official_result_root is not None,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--official-result-root", type=Path)
    parser.add_argument("--skip-official", action="store_true")
    parser.add_argument("--require-official", action="store_true")
    args = parser.parse_args()
    result = validate_stage(args.stage_dir.resolve(), skip_official=args.skip_official, require_official=args.require_official, official_result_root=args.official_result_root.resolve() if args.official_result_root else None)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
