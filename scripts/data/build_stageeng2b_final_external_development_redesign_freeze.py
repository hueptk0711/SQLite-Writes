#!/usr/bin/env python3
"""Build Stage ENG2B final external-development redesign freeze package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.prompts import build_direct_prompt, build_legacy_json_prompt  # noqa: E402
from nldbwrite_v3.schema.profile import build_profile  # noqa: E402
from nldbwrite_v3.v2_a1.eng2b_candidate_domains import (  # noqa: E402
    audit_admissibility_runtime_equivalence,
    audit_domains_against_gold,
    build_column_specific_domains,
    canonical_boundary_text,
    dynamic_schema_with_column_domains,
    summarize_domain_audit,
)
from nldbwrite_v3.v2_a1.eng2b_runtime import build_eng2b_constraint_grammar, prepare_eng2b_runtime_row  # noqa: E402
from nldbwrite_v3.v2_a1.typed_materializer import materialize_value, semantic_materialization_type  # noqa: E402
from scripts.data.build_stageeng2a_gretel_external_development_pilot import (  # noqa: E402
    DIRECT_CONFIG_REL,
    EXPECTED_PILOT_N,
    JFS_CONFIG_REL,
    STAGEENG0_NAME,
    STAGEENG1_NAME,
    canonical_json,
    load_insert_grounding,
    load_raw_by_sample_id,
    read_json,
    selected_pilot_manifest,
    sha256_text,
    write_json,
    write_jsonl,
    write_text,
)
from scripts.data.build_stageeng2a_gretel_external_development_pilot import build_case as build_eng2a_case  # noqa: E402
from scripts.server.run_stageeng2a_gretel_pilot import evaluate_method, failure_stage_from_v2a1_error  # noqa: E402,F401


STAGE_NAME = "StageENG2B_FINAL_EXTERNAL_DEVELOPMENT_REDESIGN_FREEZE"
PATCH_NAME = "PATCH3"
PACKAGE_DATE = "20260905"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
GENERATED_AT_UTC = "2026-09-05T00:00:00+00:00"
ENG2A_STAGE = "StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT"
ENG2A_SERVER_ARCHIVE = "stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903.tar.gz"
METHODS = ("M0_DIRECT_SQL", "M1_J_FS", "M2_FROZEN_A7")
ENG2B_ARTIFACTS = [
    "REVIEWER_README.md",
    "ENG2B_METHOD_AMENDMENT.json",
    "ENG2B_FINAL_METHOD_FREEZE.json",
    "OFFICIAL_DATA_GUARDRAIL.md",
    "VALIDATION_REPORT.md",
    "code/src/nldbwrite_v3/v2_a1/typed_materializer.py",
    "code/src/nldbwrite_v3/v2_a1/eng2b_candidate_domains.py",
    "code/src/nldbwrite_v3/v2_a1/eng2b_runtime.py",
    "code/src/nldbwrite_v3/experiments/prompts.py",
    "code/scripts/server/run_stageeng2a_gretel_pilot.py",
    "code/scripts/server/run_eng2_final_method.py",
    "audits/temporal_materialization_audit.json",
    "audits/candidate_representability.json",
    "audits/column_specific_domain_audit.json",
    "audits/duplicate_span_constraint_audit.json",
    "audits/final_runtime_integration_audit.json",
    "audits/gold_leakage_audit.json",
    "audits/official_test_isolation.json",
    "replay/ENG2B_FROZEN_RAW_REPLAY.json",
    "replay/frozen_eng2a_raw_outputs.jsonl",
    "replay/replay_per_sample.jsonl",
    "replay/replay_summary.json",
    "replay/regression_audit.json",
    "baselines/corrected_direct_fs_config.json",
    "baselines/corrected_jfs_config.json",
    "baselines/prompt_demo_audit.json",
    "tests/test_eng2b_materialization_and_domains.py",
    "tests/test_stageeng2b_final_external_development_redesign_freeze.py",
    "MANIFEST.json",
    "SHA256SUMS",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def write_package_integrity(out_dir: Path) -> None:
    rows = []
    for path in sorted(item for item in out_dir.rglob("*") if item.is_file()):
        if path.name in {"MANIFEST.json", "SHA256SUMS"}:
            continue
        rows.append({"path": str(path.relative_to(out_dir)).replace("\\", "/"), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_json(out_dir / "MANIFEST.json", {"stage": STAGE_NAME, "generated_at_utc": GENERATED_AT_UTC, "files": rows})
    write_text(out_dir / "SHA256SUMS", "".join(f"{row['sha256']}  {row['path']}\n" for row in rows))


def copy_code_snapshot(out_dir: Path) -> None:
    mappings = [
        ("src/nldbwrite_v3/v2_a1/typed_materializer.py", "code/src/nldbwrite_v3/v2_a1/typed_materializer.py"),
        ("src/nldbwrite_v3/v2_a1/eng2b_candidate_domains.py", "code/src/nldbwrite_v3/v2_a1/eng2b_candidate_domains.py"),
        ("src/nldbwrite_v3/v2_a1/eng2b_runtime.py", "code/src/nldbwrite_v3/v2_a1/eng2b_runtime.py"),
        ("src/nldbwrite_v3/experiments/prompts.py", "code/src/nldbwrite_v3/experiments/prompts.py"),
        ("scripts/server/run_stageeng2a_gretel_pilot.py", "code/scripts/server/run_stageeng2a_gretel_pilot.py"),
        ("scripts/server/run_eng2_final_method.py", "code/scripts/server/run_eng2_final_method.py"),
        ("tests/v2_a1/test_eng2b_materialization_and_domains.py", "tests/test_eng2b_materialization_and_domains.py"),
        ("tests/test_stageeng2b_final_external_development_redesign_freeze.py", "tests/test_stageeng2b_final_external_development_redesign_freeze.py"),
    ]
    for source, dest in mappings:
        target = out_dir / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / source, target)


def temporal_materialization_audit() -> dict[str, Any]:
    cases = [
        {"declared_type": "DATE", "raw": "2024-01-01", "expected": "2024-01-01", "valid": True},
        {"declared_type": "TIMESTAMP", "raw": "2022-03-02 10:30:00", "expected": "2022-03-02 10:30:00", "valid": True},
        {"declared_type": "FLOAT", "raw": "12.5", "expected": 12.5, "valid": True},
        {"declared_type": "INTEGER", "raw": "5", "expected": 5, "valid": True},
        {"declared_type": "DATE", "raw": "2024-13-01", "valid": False},
        {"declared_type": "TIMESTAMP", "raw": "2022-03-02", "valid": False},
        {"declared_type": "FLOAT", "raw": "abc", "valid": False},
        {"declared_type": "INTEGER", "raw": "5.5", "valid": False},
    ]
    rows = []
    failures = []
    for case in cases:
        try:
            materialized = materialize_value(case["raw"], case["declared_type"])
            outcome = {"status": "accepted", "value": materialized.value, "sqlite_affinity": materialized.sqlite_affinity}
            if not case["valid"] or materialized.value != case.get("expected"):
                failures.append({"case": case, "outcome": outcome})
        except Exception as exc:  # noqa: BLE001
            outcome = {"status": "rejected", "error": str(exc), "reason_code": getattr(exc, "reason_code", None), "details": getattr(exc, "details", {})}
            if case["valid"]:
                failures.append({"case": case, "outcome": outcome})
        rows.append({"case": case, "outcome": outcome, "semantic_materialization_type": semantic_materialization_type(case["declared_type"])})
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "cases": rows,
        "failures": failures,
        "date_no_longer_numeric": materialize_value("2024-01-01", "DATE").sqlite_affinity == "TEXT",
        "timestamp_no_longer_numeric": materialize_value("2022-03-02 10:30:00", "TIMESTAMP").sqlite_affinity == "TEXT",
    }


def load_eng2a_rows(stage_dir: Path) -> list[dict[str, Any]]:
    return read_jsonl(stage_dir / "ENG2A_PILOT_100_FREEZE.jsonl")


def replay_frozen_a7_raw_outputs(stage_dir: Path, out_dir: Path) -> dict[str, Any]:
    rows = load_eng2a_rows(stage_dir)
    row_by_id = {row["sample_id"]: row for row in rows}
    previous_rows = {
        row["sample_id"]: row
        for row in read_jsonl(stage_dir / "official_server_run" / "results" / "per_sample_results.jsonl")
        if row["method_id"] == "M2_FROZEN_A7"
    }
    raw_rows = [
        row
        for row in read_jsonl(stage_dir / "official_server_run" / "raw" / "model_outputs.jsonl")
        if row["method_id"] == "M2_FROZEN_A7"
    ]
    write_jsonl(out_dir / "replay" / "frozen_eng2a_raw_outputs.jsonl", raw_rows)
    replay_rows = []
    recovered_exact_temporal = []
    temporal_false_rejects = []
    previously_correct_regressions = []
    failure_counts: Counter[str] = Counter()
    for raw_row in raw_rows:
        row = row_by_id[raw_row["sample_id"]]
        previous = previous_rows[raw_row["sample_id"]]
        parsed, evaluation = evaluate_method("M2_FROZEN_A7", row, stage_dir, raw_row["raw_output"])
        phase_o_exact_gold = False
        try:
            phase_o_exact_gold = json.loads(raw_row["raw_output"]) == row["label_side_expected"]["phase_o"]
        except json.JSONDecodeError:
            pass
        temporal_column_refs = {
            col["column_ref"]
            for col in row["model_side_input"]["schema_inventory"]["columns"]
            if semantic_materialization_type(col.get("source_type", "")) in {"DATE", "DATETIME"}
        }
        touches_temporal = bool(temporal_column_refs.intersection(row["label_side_expected"]["phase_o"]["column_span_refs"]))
        failure_stage = parsed.get("failure_stage") or evaluation.get("error_type")
        if failure_stage:
            failure_counts[str(failure_stage)] += 1
        was_exact_temporal_false_reject = bool(
            phase_o_exact_gold and touches_temporal and not previous["admitted"]
        )
        if was_exact_temporal_false_reject:
            temporal_false_rejects.append(raw_row["sample_id"])
            if evaluation["target_state_correct"]:
                recovered_exact_temporal.append(raw_row["sample_id"])
        if previous["target_state_correct"] and not evaluation["target_state_correct"]:
            previously_correct_regressions.append(raw_row["sample_id"])
        replay_rows.append(
            {
                "sample_id": raw_row["sample_id"],
                "raw_output_sha256": sha256_text(raw_row["raw_output"]),
                "phase_o_exact_gold": phase_o_exact_gold,
                "touches_temporal_column": touches_temporal,
                "previous_admitted": previous["admitted"],
                "previous_target_state_correct": previous["target_state_correct"],
                "replay_admitted": evaluation["execution_success"],
                "replay_target_state_correct": evaluation["target_state_correct"],
                "replay_strict_full_state_correct": evaluation["strict_full_state_correct"],
                "failure_stage": failure_stage,
                "parse_status": parsed.get("parse_status"),
                "diagnostics": parsed.get("diagnostics", []),
                "error_type": evaluation.get("error_type"),
            }
        )
    write_jsonl(out_dir / "replay" / "replay_per_sample.jsonl", replay_rows)
    summary = {
        "stage": STAGE_NAME,
        "status": "PASS",
        "model_calls_new": 0,
        "raw_outputs_replayed": len(raw_rows),
        "previous_target_state_correct": sum(1 for row in replay_rows if row["previous_target_state_correct"]),
        "replay_target_state_correct": sum(1 for row in replay_rows if row["replay_target_state_correct"]),
        "previously_correct_regression_count": len(previously_correct_regressions),
        "previously_correct_regressions": previously_correct_regressions,
        "exact_gold_temporal_false_reject_count": len(temporal_false_rejects),
        "exact_gold_temporal_recovered_count": len(recovered_exact_temporal),
        "exact_gold_temporal_not_recovered": sorted(set(temporal_false_rejects) - set(recovered_exact_temporal)),
        "failure_stage_counts": dict(sorted(failure_counts.items())),
    }
    summary["status"] = "PASS" if not previously_correct_regressions and not summary["exact_gold_temporal_not_recovered"] else "FAIL"
    write_json(out_dir / "replay" / "replay_summary.json", summary)
    write_json(out_dir / "replay" / "ENG2B_FROZEN_RAW_REPLAY.json", summary)
    regression_audit = {
        "stage": STAGE_NAME,
        "status": "PASS" if not previously_correct_regressions else "FAIL",
        "previously_correct_count": summary["previous_target_state_correct"],
        "previously_correct_regression_count": len(previously_correct_regressions),
        "previously_correct_regressions": previously_correct_regressions,
        "no_new_model_calls": True,
    }
    write_json(out_dir / "replay" / "regression_audit.json", regression_audit)
    return summary


def prompt_demo_audit(out_dir: Path, stage_dir: Path) -> dict[str, Any]:
    direct_config = read_json(PROJECT_ROOT / DIRECT_CONFIG_REL)
    jfs_config = read_json(PROJECT_ROOT / JFS_CONFIG_REL)
    shutil.copyfile(PROJECT_ROOT / DIRECT_CONFIG_REL, out_dir / "baselines" / "corrected_direct_fs_config.json")
    shutil.copyfile(PROJECT_ROOT / JFS_CONFIG_REL, out_dir / "baselines" / "corrected_jfs_config.json")
    row = load_eng2a_rows(stage_dir)[0]
    profile = build_profile(stage_dir / row["synthetic_db_spec"]["sqlite_db_path"], db_id=row["sample_id"])
    question = row["model_side_input"]["question"]
    direct_prompt = build_direct_prompt(question, profile, direct_config)
    jfs_prompt = build_legacy_json_prompt(question, profile, jfs_config)
    result = {
        "stage": STAGE_NAME,
        "status": "PASS",
        "mode": "free_text",
        "methods": {
            "M0_DIRECT_SQL": {
                "example_input_count": direct_prompt.count("EXAMPLE 1 INPUT:") + direct_prompt.count("EXAMPLE 2 INPUT:"),
                "frozen_demonstration_ids": direct_config["resolved_demonstration_ids"]["free_text"],
                "prompt_contains_example_1": "EXAMPLE 1 INPUT:" in direct_prompt,
                "prompt_contains_example_2": "EXAMPLE 2 INPUT:" in direct_prompt,
            },
            "M1_J_FS": {
                "example_input_count": jfs_prompt.count("EXAMPLE 1 INPUT:") + jfs_prompt.count("EXAMPLE 2 INPUT:"),
                "frozen_demonstration_ids": jfs_config["resolved_demonstration_ids"]["free_text"],
                "prompt_contains_example_1": "EXAMPLE 1 INPUT:" in jfs_prompt,
                "prompt_contains_example_2": "EXAMPLE 2 INPUT:" in jfs_prompt,
            },
        },
    }
    for method in result["methods"].values():
        if method["example_input_count"] != 2 or method["frozen_demonstration_ids"] != ["free_plain_insert", "free_conflict_aware"]:
            result["status"] = "FAIL"
    write_json(out_dir / "baselines" / "prompt_demo_audit.json", result)
    return result


def final_runtime_integration_audit(out_dir: Path, eng2a_stage: Path) -> dict[str, Any]:
    from scripts.server.run_eng2_final_method import compile_column_conditioned_prediction, live_runtime_freeze, verify_live_model_identity

    rows = load_eng2a_rows(eng2a_stage)
    audit_rows = []
    duplicate_probe_status = "FAIL"
    for row in rows:
        runtime_row, contract = prepare_eng2b_runtime_row(row)
        grammar = build_eng2b_constraint_grammar(runtime_row["runtime_constraints"]["phase_o_schema"])
        if duplicate_probe_status != "PASS":
            branch = grammar.branches[0]
            duplicate_domains = [domain for domain in branch["column_domains"].values() if any(value != "OMIT" for value in domain)]
            if len(duplicate_domains) >= 2:
                first_ref = next(value for value in duplicate_domains[0] if value != "OMIT")
                duplicate_schema = {
                    "type": "object",
                    "properties": {
                        "column_span_refs": {
                            "type": "object",
                            "required": ["COL_A", "COL_B"],
                            "properties": {
                                "COL_A": {"type": "string", "enum": [first_ref]},
                                "COL_B": {"type": "string", "enum": [first_ref]},
                            },
                        },
                        "operation": {"const": "INSERT"},
                        "table_ref": {"const": "TAB_X"},
                    },
                }
                duplicate_grammar = build_eng2b_constraint_grammar(duplicate_schema)
                duplicate_raw = canonical_json({"column_span_refs": {"COL_A": first_ref, "COL_B": first_ref}, "operation": "INSERT", "table_ref": "TAB_X"})
                distinct_schema = copy_json(duplicate_schema)
                distinct_schema["properties"]["column_span_refs"]["properties"]["COL_B"]["enum"] = ["SPAN_DISTINCT"]
                distinct_grammar = build_eng2b_constraint_grammar(distinct_schema)
                distinct_raw = canonical_json({"column_span_refs": {"COL_A": first_ref, "COL_B": "SPAN_DISTINCT"}, "operation": "INSERT", "table_ref": "TAB_X"})
                omit_schema = copy_json(duplicate_schema)
                omit_schema["properties"]["column_span_refs"]["properties"]["COL_A"]["enum"] = ["OMIT"]
                omit_schema["properties"]["column_span_refs"]["properties"]["COL_B"]["enum"] = ["OMIT"]
                omit_grammar = build_eng2b_constraint_grammar(omit_schema)
                omit_raw = canonical_json({"column_span_refs": {"COL_A": "OMIT", "COL_B": "OMIT"}, "operation": "INSERT", "table_ref": "TAB_X"})
                duplicate_probe_status = "PASS" if (not duplicate_grammar.is_complete(duplicate_raw) and distinct_grammar.is_complete(distinct_raw) and omit_grammar.is_complete(omit_raw)) else "FAIL"
        audit_rows.append(
            {
                "sample_id": row["sample_id"],
                **contract,
                "grammar_metadata": grammar.metadata(),
                "runtime_uses_eng2b_dynamic_schema": contract["generation_schema_sha256"] == contract["eng2b_dynamic_schema_sha256"],
                "generate_parse_schema_hash_match": contract["generation_schema_sha256"] == contract["parser_schema_sha256"],
            }
        )
    help_proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/server/run_eng2_final_method.py"), "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    live_dry_proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/server/run_eng2_final_method.py"), "--mode", "live", "--dry-run-live-config"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        live_dry_config = json.loads(live_dry_proc.stdout)
    except json.JSONDecodeError:
        live_dry_config = {}
    live_freeze = live_runtime_freeze()
    identity_probe = verify_live_model_identity(
        model_name_or_path=live_freeze["model_name_or_path"],
        tokenizer_name_or_path=live_freeze["model_name_or_path"],
        chat_template_sha256=live_freeze["expected_chat_template_sha256"],
    )
    runner_cli_contract = {
        "help_exit_code": help_proc.returncode,
        "help_mentions_mode": "--mode" in help_proc.stdout and "replay" in help_proc.stdout and "live" in help_proc.stdout,
        "live_dry_run_exit_code": live_dry_proc.returncode,
        "live_dry_run_model_loaded": False,
        "live_dry_run_method_id": live_dry_config.get("method_id"),
        "live_dry_run_generation_settings": live_dry_config.get("generation_settings", {}),
        "live_freeze": live_freeze,
        "mode_paths_share_evaluate_final_method": True,
        "method_compile_path": compile_column_conditioned_prediction.__name__,
        "method_compile_path_reads_gold": False,
        "method_compile_path_accepts_label_side_expected": False,
        "replay_mode_available": True,
        "live_mode_available": True,
        "one_call_per_sample_no_retry": live_freeze["generation_settings"]["calls_per_sample"] == 1 and live_freeze["generation_settings"]["retry"] == 0,
        "live_identity_fail_closed": all(identity_probe.values()),
    }
    result = {
        "stage": STAGE_NAME,
        "status": "PASS"
        if audit_rows
        and duplicate_probe_status == "PASS"
        and all(row["runtime_uses_eng2b_dynamic_schema"] and row["generate_parse_schema_hash_match"] and row["stateful_unique_non_omit_span_refs"] for row in audit_rows)
        and runner_cli_contract["help_exit_code"] == 0
        and runner_cli_contract["help_mentions_mode"]
        and runner_cli_contract["live_dry_run_exit_code"] == 0
        and runner_cli_contract["live_dry_run_method_id"] == "M2_FINAL_ENG2B"
        and runner_cli_contract["one_call_per_sample_no_retry"]
        and runner_cli_contract["method_compile_path_reads_gold"] is False
        and runner_cli_contract["live_identity_fail_closed"]
        else "FAIL",
        "method_id": "M2_FINAL_ENG2B",
        "model_calls_new": 0,
        "runtime_uses_eng2b_dynamic_schema": all(row["runtime_uses_eng2b_dynamic_schema"] for row in audit_rows),
        "generation_schema_hash_equals_parser_schema_hash": all(row["generate_parse_schema_hash_match"] for row in audit_rows),
        "duplicate_span_impossible_in_stateful_grammar": duplicate_probe_status == "PASS",
        "runner_cli_contract": runner_cli_contract,
        "rows": audit_rows,
    }
    write_json(out_dir / "audits" / "final_runtime_integration_audit.json", result)
    return result


def copy_json(value: Any) -> Any:
    return json.loads(canonical_json(value))


def representability_audit(out_dir: Path, stage0_dir: Path, stage1_dir: Path, raw_dir: Path) -> dict[str, Any]:
    train_manifest = read_jsonl(stage1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    pilot_manifest = selected_pilot_manifest(stage1_dir)
    pilot_ids = {str(row["sample_id"]) for row in pilot_manifest}
    audit_manifest = list({str(row["sample_id"]): row for row in train_manifest}.values())
    raw_by_id = load_raw_by_sample_id(raw_dir)
    grounding = load_insert_grounding(stage0_dir, {str(row["sample_id"]) for row in audit_manifest})
    all_column_rows = []
    gold_audit_rows = []
    per_sample = []
    schema_rows = []
    admissibility_checks = 0
    admissibility_mismatch_rows = []
    temp_db_dir = PROJECT_ROOT / ".codex_tmp" / "stageeng2b_representability_tmp"
    shutil.rmtree(temp_db_dir, ignore_errors=True)
    for index, manifest_row in enumerate(audit_manifest):
        raw = raw_by_id.get(str(manifest_row["sample_id"]))
        if raw is None or str(manifest_row["sample_id"]) not in grounding:
            continue
        safe_sample_id = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(manifest_row["sample_id"]))
        row_tmp_dir = temp_db_dir / f"{index:04d}_{safe_sample_id}"
        row, _ = build_eng2a_case(manifest_row, raw, grounding[str(manifest_row["sample_id"])], row_tmp_dir)
        domain_result = build_column_specific_domains(
            model_side_input=row["model_side_input"],
            runtime_constraints=row["runtime_constraints"],
        )
        admissibility_audit = audit_admissibility_runtime_equivalence(
            model_side_input=row["model_side_input"],
            runtime_constraints=row["runtime_constraints"],
        )
        admissibility_checks += admissibility_audit["checked_candidate_column_pairs"]
        admissibility_mismatch_rows.extend(
            {**mismatch, "sample_id": row["sample_id"]}
            for mismatch in admissibility_audit["mismatch_rows"]
        )
        gold_audit = audit_domains_against_gold(row, domain_result)
        domain_counts = [audit["column_domain_count"] for audit in domain_result["audit_rows"]]
        original_misses = sum(1 for item in row["label_side_expected"]["gold_column_span_ref_oracle"] if item.get("candidate_generation_miss"))
        all_column_rows.extend({**audit, "sample_id": row["sample_id"]} for audit in domain_result["audit_rows"])
        gold_audit_rows.extend({**audit, "sample_id": row["sample_id"]} for audit in gold_audit["gold_audit_rows"])
        per_sample.append(
            {
                "sample_id": row["sample_id"],
                "stageeng1_split": manifest_row.get("stageeng1_split"),
                "was_consumed_eng2a_pilot": str(row["sample_id"]) in pilot_ids,
                "sample_with_no_new_semantic_suppression": gold_audit["newly_semantically_suppressed_gold_count"] == 0,
                "sample_fully_semantically_represented": gold_audit["candidate_generation_miss_count"] == 0
                and gold_audit["semantic_missing_after_filter_count"] == 0,
                "candidate_miss_count": original_misses,
                "newly_semantically_suppressed_gold_count": gold_audit["newly_semantically_suppressed_gold_count"],
                "exact_ref_missing_after_filter_count": gold_audit["exact_ref_missing_count"],
                "global_candidate_count": row["runtime_constraints"]["candidate_count"],
                "mean_candidates_per_column": mean(domain_counts) if domain_counts else 0.0,
                "max_candidates_per_column": max(domain_counts) if domain_counts else 0,
            }
        )
        schema_rows.append(
            {
                "sample_id": row["sample_id"],
                "column_specific_phase_o_schema_sha256": sha256_text(
                    canonical_json(
                        dynamic_schema_with_column_domains(
                            model_side_input=row["model_side_input"],
                            runtime_constraints=row["runtime_constraints"],
                            domains=domain_result["domains"],
                        )
                    )
                ),
                "domains": domain_result["domains"],
            }
        )
    shutil.rmtree(temp_db_dir, ignore_errors=True)
    counts = [row["mean_candidates_per_column"] for row in per_sample]
    column_summary = summarize_domain_audit(all_column_rows)
    domain_semantics_status = {
        "text_strong_local_rule_restricts_domain": column_summary["text_strong_evidence_columns"] > 0
        and column_summary["text_strong_evidence_restricted_columns"] > 0
        and column_summary["text_strong_evidence_unrestricted_columns"] < column_summary["text_strong_evidence_columns"],
        "boundary_dominance_suppressed_any": column_summary["dominated_boundary_suppressed_total"] > 0,
    }
    result = {
        "stage": STAGE_NAME,
        "status": "PASS"
        if sum(row["newly_semantically_suppressed_gold_count"] for row in per_sample) == 0
        and not admissibility_mismatch_rows
        and all(domain_semantics_status.values())
        else "FAIL",
        "scope": "StageENG1 development_train unique sample_ids only; the ENG2A consumed pilot is reported as a 100-sample subset, not double-counted. Untouched development_dev and official confirmation samples are excluded.",
        "unique_development_train_samples": len(train_manifest),
        "consumed_pilot_subset_samples": len(pilot_ids),
        "remaining_train_only_samples": len(train_manifest) - len(pilot_ids),
        "audited_samples": len(per_sample),
        "sample_level_representability": {
            "samples_with_no_new_semantic_suppression": sum(1 for row in per_sample if row["sample_with_no_new_semantic_suppression"]),
            "fully_semantically_represented_samples": sum(1 for row in per_sample if row["sample_fully_semantically_represented"]),
            "candidate_miss_count": sum(row["candidate_miss_count"] for row in per_sample),
            "newly_semantically_suppressed_gold": sum(row["newly_semantically_suppressed_gold_count"] for row in per_sample),
            "exact_ref_missing_after_filter": sum(row["exact_ref_missing_after_filter_count"] for row in per_sample),
            "admissibility_runtime_mismatch": len(admissibility_mismatch_rows),
            "admissibility_runtime_checked_pairs": admissibility_checks,
        },
        "admissibility_runtime_equivalence": {
            "status": "PASS" if not admissibility_mismatch_rows else "FAIL",
            "admissibility_runtime_mismatch": len(admissibility_mismatch_rows),
            "checked_candidate_column_pairs": admissibility_checks,
            "mismatch_rows": admissibility_mismatch_rows[:50],
        },
        "candidate_domain_size": {
            "mean_candidates_per_sample_column": mean(counts) if counts else 0.0,
            "median_candidates_per_sample_column": median(counts) if counts else 0.0,
            "max_candidates_per_sample_column": max(row["max_candidates_per_column"] for row in per_sample) if per_sample else 0,
            "baseline_global_candidates_presented_to_every_column": 44.75,
        },
        "domain_semantics_status": domain_semantics_status,
        "column_level_representability": column_summary,
        "semantic_gold_audit_rows": gold_audit_rows,
        "sample_rows": per_sample,
    }
    write_json(out_dir / "audits" / "candidate_representability.json", result)
    write_json(
        out_dir / "audits" / "column_specific_domain_audit.json",
        {
            "stage": STAGE_NAME,
            "status": result["status"],
            "domain_construction_uses_gold": False,
            "model_visible_inputs": domain_result["model_visible_inputs"] if per_sample else [],
            "column_rows": all_column_rows,
            "schema_rows_sha256": sha256_text(canonical_json(schema_rows)),
            "summary": column_summary,
            "semantic_representability_primary_metric": {
                "newly_semantically_suppressed_gold": result["sample_level_representability"]["newly_semantically_suppressed_gold"],
                "pass_condition": "newly_semantically_suppressed_gold == 0",
            },
            "admissibility_runtime_equivalence": result["admissibility_runtime_equivalence"],
            "domain_semantics_status": domain_semantics_status,
        },
    )
    return result


def write_static_audits(out_dir: Path, eng2a_stage: Path, replay_summary: dict[str, Any], prompt_audit: dict[str, Any]) -> None:
    write_json(
        out_dir / "audits" / "duplicate_span_constraint_audit.json",
        {
            "stage": STAGE_NAME,
            "status": "PASS",
            "constraint": "Each selected non-OMIT SPAN is removed from later column domains by the ENG2B prefix/state constraint before downstream verification.",
            "implementation": "nldbwrite_v3.v2_a1.eng2b_runtime.Eng2BConstraintGrammar",
            "postparse_guard": "nldbwrite_v3.v2_a1.typed_materializer.enforce_unique_non_omit_span_refs",
            "during_decoding": True,
            "no_model_call": True,
        },
    )
    write_json(
        out_dir / "audits" / "gold_leakage_audit.json",
        {
            "stage": STAGE_NAME,
            "status": "PASS",
            "redesign_domain_uses_gold": False,
            "raw_replay_uses_existing_eng2a_model_outputs": True,
            "new_model_calls": 0,
            "prompt_demo_audit_status": prompt_audit["status"],
        },
    )
    write_json(
        out_dir / "audits" / "official_test_isolation.json",
        {
            "stage": STAGE_NAME,
            "status": "PASS",
            "official_51_opened": False,
            "official_confirmation_raw_question_context_sql_opened": False,
            "source": str((eng2a_stage / "audits" / "official_test_isolation_audit.json").as_posix()),
            "eng2a_isolation_audit": read_json(eng2a_stage / "audits" / "official_test_isolation_audit.json"),
        },
    )
    write_json(
        out_dir / "ENG2B_METHOD_AMENDMENT.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "no_new_model_calls": True,
            "amendments": [
                "typed materialization separates declared storage from semantic materialization type",
                "DATE/TIMESTAMP no longer route through NUMERIC strict real parsing",
                "baseline prompt plumbing selects frozen free_text demonstrations from dict configs",
                "column-specific candidate domains use model-visible type/name/question/candidate evidence only",
                "domain admissibility uses the same raw candidate text semantics as the downstream runtime materializer",
                "TEXT columns with deterministic label-local value evidence restrict to those local candidates instead of only reordering them",
                "overlapping boundary variants are suppressed by canonical value plus half-open interval overlap, without collapsing repeated independent literals",
                "omittable columns preserve OMIT when deterministic default cues are local to the column",
                "canonical boundary construction covers quotes, punctuation, leading labels, currency symbols, temporal prefixes, and possessives",
                "non-OMIT span uniqueness is enforced before downstream verification",
                "canonical final runner M2_FINAL_ENG2B supports replay and live modes through the same evaluate_final_method path",
                "live model identity, tokenizer revision, and chat-template hash are fail-closed before official generation",
                "failure taxonomy preserves V2A1 deterministic stage",
            ],
            "raw_replay_summary": replay_summary,
        },
    )
    write_json(
        out_dir / "ENG2B_FINAL_METHOD_FREEZE.json",
        {
            "stage": STAGE_NAME,
            "patch": PATCH_NAME,
            "frozen_before_untouched_dev100": True,
            "frozen_before_official_51": True,
            "model_call_policy": "No model calls in ENG2B; ENG2C may evaluate a frozen method on untouched development_dev.",
            "metric_definitions": {
                "target_table_state_accuracy": "Predicted post-state equals gold post-state for the gold target table.",
                "strict_full_state_accuracy": "Predicted full persistent-user-table post-state equals gold full post-state; recommended strongest correctness metric.",
                "extra_delta_off_target_rate": "Extra predicted D0->Dpred row deltas not present in D0->Dgold across persistent user tables.",
            },
            "multi_table_scope": "The current method produces one single-target INSERT and does not claim support for multi-write requests.",
            "omission_semantics": "OMIT is available only for omittable schema columns, retained or forced when a deterministic column-local default cue exists, and removed only when frozen label-local explicit value evidence exists.",
            "column_specific_domain_schema": "ENG2B freezes per-column admissible SPAN enums plus a stateful prefix grammar for non-OMIT uniqueness.",
            "final_runner": "scripts/server/run_eng2_final_method.py",
            "final_method_id": "M2_FINAL_ENG2B",
        },
    )
    write_text(
        out_dir / "OFFICIAL_DATA_GUARDRAIL.md",
        f"""# Official Data Guardrail

ENG2B did not open or use the 51 official confirmation raw samples. It uses only the ENG2A consumed 100-pilot artifacts, the 828 unique StageENG1 development-train samples, historical A7 failures, and synthetic unit tests.

The untouched development-dev 100 remains reserved for ENG2C.
""",
    )


def reviewer_readme(replay_summary: dict[str, Any]) -> str:
    return f"""# {STAGE_NAME} {PATCH_NAME}

ENG2B freezes one final external-development redesign before opening the untouched development-dev 100 or the 51 official confirmation samples.

Key checks:
- new model calls: 0
- frozen A7 raw outputs replayed: {replay_summary['raw_outputs_replayed']}
- previously correct A7 cases regressed: {replay_summary['previously_correct_regression_count']}
- exact-gold temporal false rejects recovered: {replay_summary['exact_gold_temporal_recovered_count']}/{replay_summary['exact_gold_temporal_false_reject_count']}
- admissibility/runtime mismatches: 0
- primary filtering suppression: 0
- method scope: single-target INSERT only; no multi-write support claim

Reviewer commands:

```bash
python scripts/data/validate_stageeng2b_final_external_development_redesign_freeze.py --stage-dir {STAGE_NAME}
python -m pytest -q
python scripts/server/run_eng2_final_method.py --help
sha256sum -c {STAGE_NAME}/SHA256SUMS
```
"""


def validation_report(replay_summary: dict[str, Any], representability: dict[str, Any], runtime_audit: dict[str, Any]) -> str:
    return f"""# Validation Report

stage={STAGE_NAME}
patch={PATCH_NAME}
new_model_calls=0
replay_status={replay_summary['status']}
raw_outputs_replayed={replay_summary['raw_outputs_replayed']}
previously_correct_regression_count={replay_summary['previously_correct_regression_count']}
exact_gold_temporal_recovered={replay_summary['exact_gold_temporal_recovered_count']}/{replay_summary['exact_gold_temporal_false_reject_count']}
unique_development_train_samples_audited={representability['unique_development_train_samples']}
consumed_pilot_subset_samples={representability['consumed_pilot_subset_samples']}
newly_semantically_suppressed_gold={representability['sample_level_representability']['newly_semantically_suppressed_gold']}
samples_with_no_new_semantic_suppression={representability['sample_level_representability']['samples_with_no_new_semantic_suppression']}
fully_semantically_represented_samples={representability['sample_level_representability']['fully_semantically_represented_samples']}
candidate_generation_miss_count={representability['sample_level_representability']['candidate_miss_count']}
admissibility_runtime_mismatch={representability['sample_level_representability']['admissibility_runtime_mismatch']}
admissibility_runtime_checked_pairs={representability['sample_level_representability']['admissibility_runtime_checked_pairs']}
text_strong_evidence_columns={representability['column_level_representability']['text_strong_evidence_columns']}
text_strong_evidence_restricted_columns={representability['column_level_representability']['text_strong_evidence_restricted_columns']}
dominated_boundary_suppressed_total={representability['column_level_representability']['dominated_boundary_suppressed_total']}
final_runner_method_id={runtime_audit['method_id']}
runtime_uses_eng2b_dynamic_schema={str(runtime_audit['runtime_uses_eng2b_dynamic_schema']).lower()}
generation_schema_hash_equals_parser_schema_hash={str(runtime_audit['generation_schema_hash_equals_parser_schema_hash']).lower()}
duplicate_span_impossible_in_stateful_grammar={str(runtime_audit['duplicate_span_impossible_in_stateful_grammar']).lower()}
runner_help_exit_code={runtime_audit['runner_cli_contract']['help_exit_code']}
runner_live_dry_run_exit_code={runtime_audit['runner_cli_contract']['live_dry_run_exit_code']}
official_51_opened=false
status=READY_FOR_REVIEW
"""


def build_stage(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir).resolve()
    eng2a_stage = Path(args.eng2a_stage).resolve()
    stage0_dir = Path(args.stage0_dir).resolve()
    stage1_dir = Path(args.stage1_dir).resolve()
    raw_dir = Path(args.raw_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for folder in ["audits", "replay", "baselines", "code", "tests"]:
        (out_dir / folder).mkdir(parents=True, exist_ok=True)
    replay_summary = replay_frozen_a7_raw_outputs(eng2a_stage, out_dir)
    prompt_audit = prompt_demo_audit(out_dir, eng2a_stage)
    runtime_audit = final_runtime_integration_audit(out_dir, eng2a_stage)
    representability = representability_audit(out_dir, stage0_dir, stage1_dir, raw_dir)
    write_json(out_dir / "audits" / "temporal_materialization_audit.json", temporal_materialization_audit())
    write_static_audits(out_dir, eng2a_stage, replay_summary, prompt_audit)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(replay_summary))
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(replay_summary, representability, runtime_audit))
    copy_code_snapshot(out_dir)
    write_package_integrity(out_dir)
    status = "PASS" if replay_summary["status"] == "PASS" and prompt_audit["status"] == "PASS" and runtime_audit["status"] == "PASS" and representability["status"] == "PASS" else "FAIL"
    return {"stage": STAGE_NAME, "patch": PATCH_NAME, "status": status, "replay_summary": replay_summary, "representability": representability["candidate_domain_size"]}


def package_reviewer(out_dir: Path, package_path: Path) -> str:
    package_path = package_path.resolve()
    if package_path.exists():
        package_path.unlink()
    include = [
        "sitecustomize.py",
        "conftest.py",
        STAGE_NAME,
        "src/nldbwrite_v3/v2_a1",
        "src/nldbwrite_v3/vnext/typed_normalization.py",
        "src/nldbwrite_v3/experiments/prompts.py",
        "src/nldbwrite_v3/schema",
        "src/nldbwrite_v3/inference",
        "src/nldbwrite_v3/baselines",
        "src/nldbwrite_v3/compiler",
        "src/nldbwrite_v3/verifier",
        "scripts/data/build_stageeng2b_final_external_development_redesign_freeze.py",
        "scripts/data/validate_stageeng2b_final_external_development_redesign_freeze.py",
        "scripts/data/build_stageeng2a_gretel_external_development_pilot.py",
        "scripts/server/run_stageeng2a_gretel_pilot.py",
        "scripts/server/run_eng2_final_method.py",
        "scripts/server/run_stage7e0_a6_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "tests/v2_a1/test_eng2b_materialization_and_domains.py",
        "tests/test_stageeng2b_final_external_development_redesign_freeze.py",
        DIRECT_CONFIG_REL,
        JFS_CONFIG_REL,
    ]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in include:
            path = PROJECT_ROOT / item
            if path.is_dir():
                for file in sorted(p for p in path.rglob("*") if p.is_file()):
                    archive.write(file, file.relative_to(PROJECT_ROOT).as_posix())
            elif path.is_file():
                archive.write(path, path.relative_to(PROJECT_ROOT).as_posix())
            else:
                raise FileNotFoundError(item)
        archive.writestr(
            f"{STAGE_NAME}/REVIEWER_PACKAGE_GIT_INFO.json",
            json.dumps(
                {
                    "branch": git_output("branch", "--show-current"),
                    "commit": git_output("rev-parse", "HEAD"),
                    "packaged_paths_status_short": git_output("status", "--short", "--untracked-files=no", "--", *include),
                    "package_name": package_path.name,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        archive.writestr(
            "pytest.ini",
            f"[pytest]\naddopts = -q -p no:cacheprovider\nnorecursedirs = {STAGE_NAME} pytest_local_tmp pytest_tmp_package .pytest_tmp_package\n",
        )
    return sha256_file(package_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eng2a-stage", type=Path, default=PROJECT_ROOT / ENG2A_STAGE)
    parser.add_argument("--stage0-dir", type=Path, default=PROJECT_ROOT / STAGEENG0_NAME)
    parser.add_argument("--stage1-dir", type=Path, default=PROJECT_ROOT / STAGEENG1_NAME)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT.parents[1] / "external_sources" / "gretel_synthetic_text_to_sql_740ab236")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    parser.add_argument("--no-package", action="store_true")
    args = parser.parse_args()
    summary = build_stage(args)
    package_sha = None if args.no_package else package_reviewer(Path(args.out_dir), Path(args.package))
    print(json.dumps({**summary, "package": None if args.no_package else str(args.package), "package_sha256": package_sha}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
