#!/usr/bin/env python3
"""Validate Stage ENG2B final external-development redesign freeze artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE_NAME = "StageENG2B_FINAL_EXTERNAL_DEVELOPMENT_REDESIGN_FREEZE"
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, failures: list[str], message: str) -> None:
    if not condition:
        failures.append(message)


def validate_manifest(stage_dir: Path, failures: list[str]) -> None:
    manifest = read_json(stage_dir / "MANIFEST.json")
    sha_lines = (stage_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    manifest_by_path = {row["path"]: row for row in manifest["files"]}
    sha_by_path = {}
    for line in sha_lines:
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        sha_by_path[rel] = digest
    check(set(manifest_by_path) == set(sha_by_path), failures, "MANIFEST and SHA256SUMS file sets differ")
    for rel, row in manifest_by_path.items():
        file_path = stage_dir / rel
        check(file_path.is_file(), failures, f"manifested file missing: {rel}")
        if file_path.is_file():
            observed = sha256_file(file_path)
            check(observed == row["sha256"], failures, f"MANIFEST sha mismatch: {rel}")
            check(observed == sha_by_path.get(rel), failures, f"SHA256SUMS sha mismatch: {rel}")


def validate_stage(stage_dir: Path) -> dict[str, Any]:
    stage_dir = stage_dir.resolve()
    failures: list[str] = []
    check(stage_dir.name == STAGE_NAME, failures, f"unexpected stage dir name: {stage_dir.name}")
    for rel in ENG2B_ARTIFACTS:
        check((stage_dir / rel).exists(), failures, f"required artifact missing: {rel}")
    if (stage_dir / "MANIFEST.json").is_file() and (stage_dir / "SHA256SUMS").is_file():
        validate_manifest(stage_dir, failures)

    replay = read_json(stage_dir / "replay" / "replay_summary.json")
    replay_rows = read_jsonl(stage_dir / "replay" / "replay_per_sample.jsonl")
    raw_rows = read_jsonl(stage_dir / "replay" / "frozen_eng2a_raw_outputs.jsonl")
    check(replay["status"] == "PASS", failures, "replay summary is not PASS")
    check(replay["model_calls_new"] == 0, failures, "ENG2B must not make new model calls")
    check(replay["raw_outputs_replayed"] == 100, failures, "expected replay of 100 frozen ENG2A raw outputs")
    check(len(raw_rows) == 100 and len(replay_rows) == 100, failures, "replay/raw JSONL row count mismatch")
    check(replay["previous_target_state_correct"] == 50, failures, "expected 50 previously correct frozen A7 samples")
    check(replay["previously_correct_regression_count"] == 0, failures, "previously correct samples regressed")
    check(not replay["previously_correct_regressions"], failures, "regression list is not empty")
    check(replay["exact_gold_temporal_false_reject_count"] == 13, failures, "expected 13 exact-gold temporal false rejects")
    check(replay["exact_gold_temporal_recovered_count"] == 13, failures, "expected all 13 temporal false rejects recovered")
    check(not replay["exact_gold_temporal_not_recovered"], failures, "some exact-gold temporal false rejects were not recovered")
    check(all(row["method_id"] == "M2_FROZEN_A7" for row in raw_rows), failures, "raw replay includes non-A7 method output")

    prompt_audit = read_json(stage_dir / "baselines" / "prompt_demo_audit.json")
    check(prompt_audit["status"] == "PASS", failures, "prompt demo audit is not PASS")
    for method_id in ("M0_DIRECT_SQL", "M1_J_FS"):
        method = prompt_audit["methods"][method_id]
        check(method["example_input_count"] == 2, failures, f"{method_id} does not contain two free-text examples")
        check(method["frozen_demonstration_ids"] == ["free_plain_insert", "free_conflict_aware"], failures, f"{method_id} demo ids drifted")

    temporal = read_json(stage_dir / "audits" / "temporal_materialization_audit.json")
    check(temporal["status"] == "PASS", failures, "temporal materialization audit is not PASS")
    check(temporal["date_no_longer_numeric"], failures, "DATE still materializes as numeric")
    check(temporal["timestamp_no_longer_numeric"], failures, "TIMESTAMP still materializes as numeric")

    representability = read_json(stage_dir / "audits" / "candidate_representability.json")
    check(representability["status"] == "PASS", failures, "candidate representability audit is not PASS")
    check(representability["unique_development_train_samples"] == 828, failures, "unique development_train audit count drifted")
    check(representability["consumed_pilot_subset_samples"] == 100, failures, "ENG2A consumed pilot subset count drifted")
    check(representability["remaining_train_only_samples"] == 728, failures, "remaining train-only count drifted")
    check(representability["audited_samples"] == 828, failures, "expected 828 unique audited external-development samples")
    check(representability["sample_level_representability"]["newly_semantically_suppressed_gold"] == 0, failures, "ENG2B filtering introduced semantic gold suppression")
    check(representability["sample_level_representability"]["admissibility_runtime_mismatch"] == 0, failures, "domain admissibility does not match raw runtime materializer")
    check(representability["sample_level_representability"]["samples_with_no_new_semantic_suppression"] == 828, failures, "samples with no new semantic suppression drifted")
    check(representability["sample_level_representability"]["fully_semantically_represented_samples"] < 828, failures, "fully represented count is misleadingly equal to all samples")
    check(representability["admissibility_runtime_equivalence"]["status"] == "PASS", failures, "admissibility/runtime equivalence audit is not PASS")
    check(representability["admissibility_runtime_equivalence"]["admissibility_runtime_mismatch"] == 0, failures, "admissibility/runtime mismatch audit is nonzero")
    check("official" in representability["scope"] and "excluded" in representability["scope"], failures, "representability scope does not exclude official data")
    check(representability["domain_semantics_status"]["text_strong_local_rule_restricts_domain"] is True, failures, "TEXT strong-local rule does not restrict domains")
    check(representability["domain_semantics_status"]["boundary_dominance_suppressed_any"] is True, failures, "boundary dominance did not suppress any overlapping variants")

    domain = read_json(stage_dir / "audits" / "column_specific_domain_audit.json")
    check(domain["status"] == "PASS", failures, "column-specific domain audit is not PASS")
    check(domain["domain_construction_uses_gold"] is False, failures, "domain construction must not use gold")
    check(domain["summary"]["column_count"] > 0, failures, "domain audit has no columns")
    check(domain["semantic_representability_primary_metric"]["newly_semantically_suppressed_gold"] == 0, failures, "domain audit semantic primary metric failed")
    check(domain["summary"]["text_strong_evidence_columns"] > 0, failures, "domain audit has no TEXT strong-evidence columns")
    check(domain["summary"]["text_strong_evidence_restricted_columns"] > 0, failures, "TEXT strong-evidence domains were never restricted")
    check(domain["summary"]["text_strong_evidence_unrestricted_columns"] < domain["summary"]["text_strong_evidence_columns"], failures, "all TEXT strong-evidence domains stayed unrestricted")
    check(domain["summary"]["dominated_boundary_suppressed_total"] > 0, failures, "overlapping boundary dominance suppressed zero candidates")
    check(domain["admissibility_runtime_equivalence"]["admissibility_runtime_mismatch"] == 0, failures, "domain audit reports admissibility/runtime mismatches")
    check(domain["domain_semantics_status"]["text_strong_local_rule_restricts_domain"] is True, failures, "domain semantics status reports TEXT restriction failure")
    check(domain["domain_semantics_status"]["boundary_dominance_suppressed_any"] is True, failures, "domain semantics status reports boundary dominance failure")

    duplicate = read_json(stage_dir / "audits" / "duplicate_span_constraint_audit.json")
    check(duplicate["status"] == "PASS", failures, "duplicate span constraint audit is not PASS")
    check(duplicate["during_decoding"] is True, failures, "duplicate span uniqueness is not recorded as decoding-time")
    check("Eng2BConstraintGrammar" in duplicate["implementation"], failures, "stateful grammar implementation not recorded")

    runtime = read_json(stage_dir / "audits" / "final_runtime_integration_audit.json")
    check(runtime["status"] == "PASS", failures, "final runtime integration audit is not PASS")
    check(runtime["method_id"] == "M2_FINAL_ENG2B", failures, "final runtime method id drifted")
    check(runtime["model_calls_new"] == 0, failures, "runtime integration audit must not use model calls")
    check(runtime["runtime_uses_eng2b_dynamic_schema"] is True, failures, "final runner does not use ENG2B dynamic schema")
    check(runtime["generation_schema_hash_equals_parser_schema_hash"] is True, failures, "generation/parser schema hash mismatch")
    check(runtime["duplicate_span_impossible_in_stateful_grammar"] is True, failures, "duplicate span is not impossible in grammar")
    runner_cli = runtime["runner_cli_contract"]
    check(runner_cli["help_exit_code"] == 0, failures, "canonical final runner --help failed")
    check(runner_cli["help_mentions_mode"] is True, failures, "canonical final runner --help does not advertise live/replay modes")
    check(runner_cli["live_dry_run_exit_code"] == 0, failures, "canonical final runner live dry-run config failed")
    check(runner_cli["live_dry_run_method_id"] == "M2_FINAL_ENG2B", failures, "live dry-run method id drifted")
    check(runner_cli["mode_paths_share_evaluate_final_method"] is True, failures, "live/replay do not share final implementation")
    check(runner_cli["method_compile_path"] == "compile_column_conditioned_prediction", failures, "final compile path is not the gold-isolated compile API")
    check(runner_cli["method_compile_path_reads_gold"] is False, failures, "final compile path is recorded as reading gold")
    check(runner_cli["method_compile_path_accepts_label_side_expected"] is False, failures, "final compile API accepts label_side_expected")
    check(runner_cli["one_call_per_sample_no_retry"] is True, failures, "live runtime is not frozen to one call/sample with no retry")
    check(runner_cli["live_identity_fail_closed"] is True, failures, "live model/tokenizer/chat-template identity is not fail-closed")
    for row in runtime.get("rows", []):
        check(row["generation_schema_sha256"] == row["eng2b_dynamic_schema_sha256"], failures, f"generation schema not ENG2B dynamic for {row['sample_id']}")
        check(row["generation_schema_sha256"] == row["parser_schema_sha256"], failures, f"parser schema hash mismatch for {row['sample_id']}")
        check(row["domain_construction_uses_gold"] is False, failures, f"runtime domain uses gold for {row['sample_id']}")

    official = read_json(stage_dir / "audits" / "official_test_isolation.json")
    check(official["status"] == "PASS", failures, "official test isolation audit is not PASS")
    check(official["official_51_opened"] is False, failures, "official 51 was opened")
    check(official["official_confirmation_raw_question_context_sql_opened"] is False, failures, "official raw/context/sql was opened")

    method_freeze = read_json(stage_dir / "ENG2B_FINAL_METHOD_FREEZE.json")
    check(method_freeze["frozen_before_untouched_dev100"] is True, failures, "method not frozen before untouched dev100")
    check(method_freeze["frozen_before_official_51"] is True, failures, "method not frozen before official 51")
    check("No model calls in ENG2B" in method_freeze["model_call_policy"], failures, "model-call policy missing from freeze")
    check(method_freeze["final_method_id"] == "M2_FINAL_ENG2B", failures, "final method id missing from freeze")

    amendment = read_json(stage_dir / "ENG2B_METHOD_AMENDMENT.json")
    check(amendment["no_new_model_calls"] is True, failures, "method amendment permits new model calls")

    return {
        "stage": STAGE_NAME,
        "stage_dir": str(stage_dir),
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "replay": {
            "model_calls_new": replay.get("model_calls_new"),
            "raw_outputs_replayed": replay.get("raw_outputs_replayed"),
            "previous_target_state_correct": replay.get("previous_target_state_correct"),
            "previously_correct_regression_count": replay.get("previously_correct_regression_count"),
            "exact_gold_temporal_recovered": f"{replay.get('exact_gold_temporal_recovered_count')}/{replay.get('exact_gold_temporal_false_reject_count')}",
        },
        "representability": {
            "unique_development_train_samples": representability.get("unique_development_train_samples"),
            "consumed_pilot_subset_samples": representability.get("consumed_pilot_subset_samples"),
            "audited_samples": representability.get("audited_samples"),
            "newly_semantically_suppressed_gold": representability.get("sample_level_representability", {}).get("newly_semantically_suppressed_gold"),
            "samples_with_no_new_semantic_suppression": representability.get("sample_level_representability", {}).get("samples_with_no_new_semantic_suppression"),
            "fully_semantically_represented_samples": representability.get("sample_level_representability", {}).get("fully_semantically_represented_samples"),
            "admissibility_runtime_mismatch": representability.get("sample_level_representability", {}).get("admissibility_runtime_mismatch"),
            "text_strong_evidence_restricted_columns": representability.get("column_level_representability", {}).get("text_strong_evidence_restricted_columns"),
            "dominated_boundary_suppressed_total": representability.get("column_level_representability", {}).get("dominated_boundary_suppressed_total"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    args = parser.parse_args()
    result = validate_stage(args.stage_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
