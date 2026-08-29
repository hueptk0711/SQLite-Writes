#!/usr/bin/env python3
"""Validate Stage7C-A2 Phase O prompt amendment artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORT))

from scripts.data.build_stage7c_a2_phase_o_prompt_amendment import (
    A2_PHASE_O_SYSTEM_PROMPT,
    A2_PHASE_O_USER_PROMPT_TEMPLATE,
    ARTIFACTS,
    HASH_POLICY,
    LOCK_FILE,
    OUT_DIR,
    PASS_STATUS,
    PROJECT_ROOT,
    STAGE,
    artifact_hashes,
    canonical_json,
    fresh_smoke_rows,
    input_hashes,
    prompt_hashes,
    sha256_file,
    sha256_text,
    validation_report_text,
    write_json,
)


def require(condition: bool, violations: list[str], code: str) -> None:
    if not condition:
        violations.append(code)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(output_dir: Path = OUT_DIR, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    violations: list[str] = []
    checks = {
        "input_hashes_recomputed": False,
        "artifact_hashes_recomputed": False,
        "a1_inputs_checked": False,
        "prompt_amendment_checked": False,
        "phase_m_unchanged_checked": False,
        "fresh_smoke_lock_checked": False,
        "no_tuning_audit_checked": False,
    }

    for rel in ARTIFACTS + (LOCK_FILE,):
        if not (output_dir / rel).is_file():
            violations.append(f"missing_artifact:{rel}")
    if violations:
        return {"stage": STAGE, "status": "FAIL", "violations": violations, **checks}

    manifest = read_json(output_dir / "STAGE7C_A2_INPUT_MANIFEST.json")
    lock = read_json(output_dir / LOCK_FILE)
    require(manifest.get("hash_policy") == HASH_POLICY, violations, "manifest_hash_policy_mismatch")
    require(lock.get("hash_policy") == HASH_POLICY, violations, "lock_hash_policy_mismatch")
    hashes = input_hashes(root)
    require(manifest.get("input_hashes") == hashes, violations, "manifest_input_hashes_mismatch")
    require(lock.get("input_hashes") == hashes, violations, "lock_input_hashes_mismatch")
    checks["input_hashes_recomputed"] = True
    require(lock.get("artifact_hashes") == artifact_hashes(output_dir), violations, "lock_artifact_hashes_mismatch")
    checks["artifact_hashes_recomputed"] = True
    require(lock.get("status") in {"BUILT_PENDING_VALIDATION", PASS_STATUS}, violations, "lock_status_invalid")

    a1_lock = read_json(root / "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json")
    a1_phase_o = read_json(root / "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json")
    a1_phase_m = read_json(root / "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")
    a1_generation = read_json(root / "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")
    stage7d_lock = read_json(root / "stage7d_v2_a1_implementation/STAGE7D_IMPLEMENTATION_LOCK.json")
    require(a1_lock.get("status") == "PASS_STAGE7C_A1_V2_DEVELOPMENT_PROTOCOL_LOCKED", violations, "stage7c_a1_not_locked")
    require(stage7d_lock.get("status") == "PASS_STAGE7D_V2_A1_IMPLEMENTATION_LOCKED", violations, "stage7d_not_locked")
    require(a1_generation.get("model_config") == lock.get("model_config"), violations, "model_config_changed")
    checks["a1_inputs_checked"] = True

    spec = read_json(output_dir / "PHASE_O_PROMPT_SPEC_A2.json")
    expected_prompt_hashes = prompt_hashes(a1_phase_m)
    require(spec.get("system_prompt") == A2_PHASE_O_SYSTEM_PROMPT, violations, "phase_o_system_prompt_text_mismatch")
    require(spec.get("user_prompt_template") == A2_PHASE_O_USER_PROMPT_TEMPLATE, violations, "phase_o_user_prompt_text_mismatch")
    require(spec.get("prompt_hashes") == expected_prompt_hashes, violations, "phase_o_prompt_hashes_mismatch")
    require(spec.get("zero_shot") is True, violations, "phase_o_not_zero_shot")
    require(spec.get("few_shot_examples_in_prompt") is False, violations, "few_shot_examples_added")
    require(spec.get("gold_visible") is False, violations, "phase_o_gold_visible")
    require(spec.get("schema_sha256") == a1_phase_o.get("schema_sha256"), violations, "phase_o_schema_changed")
    for phrase in ("smallest contiguous source span", "exactly one literal database value", "Do not select operation or instruction words", "whole clause or sentence"):
        require(phrase in spec.get("system_prompt", ""), violations, f"phase_o_atomic_instruction_missing:{phrase}")
    require("Exclude instruction words" in spec.get("user_prompt_template", ""), violations, "phase_o_user_atomic_instruction_missing")
    checks["prompt_amendment_checked"] = True

    diff = read_json(output_dir / "PROMPT_CHANGE_DIFF.json")
    require(diff.get("changed_component") == "Phase O prompt only", violations, "changed_component_not_phase_o_only")
    require(diff.get("phase_o_system_prompt", {}).get("old_sha256") != diff.get("phase_o_system_prompt", {}).get("new_sha256"), violations, "phase_o_system_hash_not_changed")
    require(diff.get("phase_o_user_prompt_template", {}).get("old_sha256") != diff.get("phase_o_user_prompt_template", {}).get("new_sha256"), violations, "phase_o_user_hash_not_changed")
    require(diff.get("phase_m_system_prompt_sha256", {}).get("changed") is False, violations, "phase_m_system_marked_changed")
    require(diff.get("phase_m_user_prompt_template_sha256", {}).get("changed") is False, violations, "phase_m_user_marked_changed")
    require(expected_prompt_hashes["phase_m_system_prompt_sha256"] == a1_phase_m["prompt_hashes"]["phase_m_system_prompt_sha256"], violations, "phase_m_system_hash_changed")
    require(expected_prompt_hashes["phase_m_user_prompt_template_sha256"] == a1_phase_m["prompt_hashes"]["phase_m_user_prompt_template_sha256"], violations, "phase_m_user_hash_changed")
    require(lock.get("phase_m_changed") is False, violations, "lock_phase_m_changed")
    require(lock.get("backend_changed") is False, violations, "lock_backend_changed")
    require(lock.get("architecture_changed") is False, violations, "lock_architecture_changed")
    checks["phase_m_unchanged_checked"] = True

    smoke_rows = read_jsonl(output_dir / "FRESH_SYNTHETIC_SMOKE_SET.jsonl")
    expected_smoke_rows = fresh_smoke_rows()
    smoke_lock = read_json(output_dir / "SMOKE_SET_LOCK.json")
    require(smoke_rows == expected_smoke_rows, violations, "fresh_smoke_rows_mismatch")
    require(len(smoke_rows) == 4, violations, "fresh_smoke_count_not_4")
    require({row["language"] for row in smoke_rows} == {"en", "zh"}, violations, "fresh_smoke_languages_changed")
    require(sum(len(row["expected_value_texts"]) == 2 for row in smoke_rows) == 2, violations, "fresh_two_value_count_changed")
    require(sum(len(row["expected_value_texts"]) == 3 for row in smoke_rows) == 2, violations, "fresh_three_value_count_changed")
    require(all(row["label_side_only"] and not row["model_side_visible"] and row["locked_before_model_run"] for row in smoke_rows), violations, "fresh_smoke_visibility_or_lock_changed")
    require(all(row["sample_id"] not in {"stage7e0_ascii_smoke_0001", "stage7e0_unicode_smoke_0002"} for row in smoke_rows), violations, "old_stage7e0_smoke_reused_as_fresh")
    for row in smoke_rows:
        question = row["question"]
        extracted = [question[span["start_char"] : span["end_char"]] for span in row["expected_phase_o_label"]["value_spans"]]
        require(extracted == row["expected_value_texts"], violations, f"fresh_smoke_span_text_mismatch:{row['sample_id']}")
    smoke_payload = "".join(canonical_json(row) + "\n" for row in smoke_rows)
    require(smoke_lock.get("smoke_set_sha256") == sha256_text(smoke_payload), violations, "smoke_set_hash_mismatch")
    require(smoke_lock.get("status") == "LOCKED_BEFORE_MODEL_RUN", violations, "smoke_set_not_locked_before_model")
    require(smoke_lock.get("old_stage7e0_failed_smokes_are_diagnostic_regression_only") is True, violations, "old_smoke_policy_changed")
    checks["fresh_smoke_lock_checked"] = True

    audit = read_json(output_dir / "NO_TRAIN_DEV_TUNING_AUDIT.json")
    for key in ("model_called", "gpu_called", "train_dev_generation_run", "confirmation_481_evaluated", "live_sql_bench_gt_opened"):
        require(audit.get(key) is False, violations, f"forbidden_execution_flag:{key}")
        require(lock.get(key) is False, violations, f"lock_forbidden_execution_flag:{key}")
    for key in ("crudsql_train_outputs_inspected_for_prompt_tuning", "crudsql_dev_outputs_inspected_for_prompt_tuning", "gold_labels_modified", "datasets_modified", "metrics_modified", "backend_modified", "phase_m_modified", "architecture_modified"):
        require(audit.get(key) is False, violations, f"forbidden_tuning_or_change_flag:{key}")
    require(audit.get("status") == "PASS", violations, "no_tuning_audit_not_pass")
    checks["no_tuning_audit_checked"] = True

    return {"stage": STAGE, "status": "PASS" if not violations else "FAIL", "violations": violations, "fresh_smoke_count": len(smoke_rows), "model_called": False, "gpu_called": False, "train_dev_generation_run": False, "confirmation_481_evaluated": False, "live_sql_bench_gt_opened": False, **checks}


def report_text(report: dict[str, Any]) -> str:
    lines = [
        "# Stage7C-A2 Validation Report",
        "",
        f"Status: {report['status']}",
        "",
        f"violations: {json.dumps(report['violations'], ensure_ascii=False, sort_keys=True)}",
        "",
        f"fresh_smoke_count: {report.get('fresh_smoke_count')}",
    ]
    for key in (
        "input_hashes_recomputed",
        "artifact_hashes_recomputed",
        "a1_inputs_checked",
        "prompt_amendment_checked",
        "phase_m_unchanged_checked",
        "fresh_smoke_lock_checked",
        "no_tuning_audit_checked",
    ):
        lines.append(f"{key}: {str(report.get(key)).lower()}")
    lines.extend(
        [
            "",
            f"model_called: {str(report.get('model_called')).lower()}",
            f"gpu_called: {str(report.get('gpu_called')).lower()}",
            f"train_dev_generation_run: {str(report.get('train_dev_generation_run')).lower()}",
            f"confirmation_481_evaluated: {str(report.get('confirmation_481_evaluated')).lower()}",
            f"live_sql_bench_gt_opened: {str(report.get('live_sql_bench_gt_opened')).lower()}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report_and_update_lock(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = output_dir / "VALIDATION_REPORT.md"
    report_path.write_text(report_text(report) if report["status"] == "PASS" else validation_report_text(report["status"], report["violations"]), encoding="utf-8")
    lock_path = output_dir / LOCK_FILE
    lock = read_json(lock_path)
    if report["status"] == "PASS":
        lock["status"] = PASS_STATUS
    lock["artifact_hashes"] = artifact_hashes(output_dir)
    lock["artifact_hashes"]["VALIDATION_REPORT.md"] = sha256_file(report_path)
    write_json(lock_path, lock)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.root)
    if not args.no_write_report:
        write_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
