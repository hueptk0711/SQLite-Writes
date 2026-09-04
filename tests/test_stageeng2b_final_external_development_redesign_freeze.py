from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.data.validate_stageeng2b_final_external_development_redesign_freeze import validate_stage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGE_NAME = "StageENG2B_FINAL_EXTERNAL_DEVELOPMENT_REDESIGN_FREEZE"
STAGE_DIR = PROJECT_ROOT / STAGE_NAME


pytestmark = pytest.mark.skipif(not STAGE_DIR.exists(), reason="Stage ENG2B artifacts have not been built yet")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stageeng2b_artifacts_validate() -> None:
    result = validate_stage(STAGE_DIR)
    assert result["status"] == "PASS", result["failures"]


def test_stageeng2b_replay_is_deterministic_no_model_call() -> None:
    replay = read_json(STAGE_DIR / "replay" / "replay_summary.json")
    assert replay["model_calls_new"] == 0
    assert replay["raw_outputs_replayed"] == 100
    assert replay["previous_target_state_correct"] == 50
    assert replay["previously_correct_regression_count"] == 0
    assert replay["exact_gold_temporal_false_reject_count"] == 13
    assert replay["exact_gold_temporal_recovered_count"] == 13
    assert replay["exact_gold_temporal_not_recovered"] == []


def test_stageeng2b_freeze_excludes_untouched_dev_and_official_51() -> None:
    isolation = read_json(STAGE_DIR / "audits" / "official_test_isolation.json")
    freeze = read_json(STAGE_DIR / "ENG2B_FINAL_METHOD_FREEZE.json")
    representability = read_json(STAGE_DIR / "audits" / "candidate_representability.json")
    assert isolation["official_51_opened"] is False
    assert isolation["official_confirmation_raw_question_context_sql_opened"] is False
    assert freeze["frozen_before_untouched_dev100"] is True
    assert freeze["frozen_before_official_51"] is True
    assert freeze["final_method_id"] == "M2_FINAL_ENG2B"
    assert representability["unique_development_train_samples"] == 828
    assert representability["consumed_pilot_subset_samples"] == 100
    assert representability["remaining_train_only_samples"] == 728
    assert representability["audited_samples"] == 828


def test_stageeng2b_corrected_baseline_prompt_plumbing_is_frozen() -> None:
    audit = read_json(STAGE_DIR / "baselines" / "prompt_demo_audit.json")
    assert audit["status"] == "PASS"
    assert audit["mode"] == "free_text"
    for method_id in ("M0_DIRECT_SQL", "M1_J_FS"):
        method = audit["methods"][method_id]
        assert method["example_input_count"] == 2
        assert method["frozen_demonstration_ids"] == ["free_plain_insert", "free_conflict_aware"]
        assert method["prompt_contains_example_1"] is True
        assert method["prompt_contains_example_2"] is True


def test_stageeng2b_column_domains_do_not_use_gold() -> None:
    domain = read_json(STAGE_DIR / "audits" / "column_specific_domain_audit.json")
    duplicate = read_json(STAGE_DIR / "audits" / "duplicate_span_constraint_audit.json")
    assert domain["status"] == "PASS"
    assert domain["domain_construction_uses_gold"] is False
    assert "declared column type" in domain["model_visible_inputs"]
    assert domain["semantic_representability_primary_metric"]["newly_semantically_suppressed_gold"] == 0
    assert duplicate["status"] == "PASS"
    assert duplicate["during_decoding"] is True
    assert "Eng2BConstraintGrammar" in duplicate["implementation"]


def test_stageeng2b_final_runner_uses_same_dynamic_schema_for_generation_and_parse() -> None:
    runtime = read_json(STAGE_DIR / "audits" / "final_runtime_integration_audit.json")
    assert runtime["status"] == "PASS"
    assert runtime["method_id"] == "M2_FINAL_ENG2B"
    assert runtime["runtime_uses_eng2b_dynamic_schema"] is True
    assert runtime["generation_schema_hash_equals_parser_schema_hash"] is True
    assert runtime["duplicate_span_impossible_in_stateful_grammar"] is True
    for row in runtime["rows"]:
        assert row["generation_schema_sha256"] == row["eng2b_dynamic_schema_sha256"]
        assert row["generation_schema_sha256"] == row["parser_schema_sha256"]
        assert row["domain_construction_uses_gold"] is False
