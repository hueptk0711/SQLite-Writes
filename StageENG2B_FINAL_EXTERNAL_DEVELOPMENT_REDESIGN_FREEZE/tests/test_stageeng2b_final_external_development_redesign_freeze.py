from __future__ import annotations

import json
import inspect
import shutil
import subprocess
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

from scripts.data.validate_stageeng2b_final_external_development_redesign_freeze import validate_stage
from scripts.server.run_eng2_final_method import (
    EXPECTED_CHAT_TEMPLATE_SHA256,
    MODEL_REVISION,
    compile_column_conditioned_prediction,
    verify_live_model_identity,
)
from nldbwrite_v3.v2_a1.types import V2A1Error


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGE_NAME = "StageENG2B_FINAL_EXTERNAL_DEVELOPMENT_REDESIGN_FREEZE"
STAGE_DIR = PROJECT_ROOT / STAGE_NAME


pytestmark = pytest.mark.skipif(not STAGE_DIR.exists(), reason="Stage ENG2B artifacts have not been built yet")


def make_local_test_dir(name: str) -> Path:
    tmp_root = PROJECT_ROOT / "pytest_local_tmp"
    tmp_root.mkdir(exist_ok=True)
    tmp_path = tmp_root / f"{name}_{uuid.uuid4().hex}"
    tmp_path.mkdir()
    return tmp_path


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
    sample_metrics = representability["sample_level_representability"]
    assert sample_metrics["samples_with_no_new_semantic_suppression"] == 828
    assert sample_metrics["fully_semantically_represented_samples"] == 823
    assert sample_metrics["candidate_miss_count"] == 5
    assert sample_metrics["newly_semantically_suppressed_gold"] == 0
    assert sample_metrics["admissibility_runtime_mismatch"] == 0


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
    assert domain["admissibility_runtime_equivalence"]["status"] == "PASS"
    assert domain["admissibility_runtime_equivalence"]["admissibility_runtime_mismatch"] == 0
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
    runner_cli = runtime["runner_cli_contract"]
    assert runner_cli["method_compile_path"] == "compile_column_conditioned_prediction"
    assert runner_cli["method_compile_path_reads_gold"] is False
    assert runner_cli["method_compile_path_accepts_label_side_expected"] is False
    assert runner_cli["live_identity_fail_closed"] is True
    for row in runtime["rows"]:
        assert row["generation_schema_sha256"] == row["eng2b_dynamic_schema_sha256"]
        assert row["generation_schema_sha256"] == row["parser_schema_sha256"]
        assert row["domain_construction_uses_gold"] is False


def test_stageeng2b_domain_semantics_are_effective() -> None:
    domain = read_json(STAGE_DIR / "audits" / "column_specific_domain_audit.json")
    summary = domain["summary"]
    assert domain["domain_semantics_status"]["text_strong_local_rule_restricts_domain"] is True
    assert summary["text_strong_evidence_columns"] > 0
    assert summary["text_strong_evidence_restricted_columns"] > 0
    assert summary["text_strong_evidence_unrestricted_columns"] < summary["text_strong_evidence_columns"]
    assert domain["domain_semantics_status"]["boundary_dominance_suppressed_any"] is True
    assert summary["dominated_boundary_suppressed_total"] > 0


def test_compile_column_conditioned_prediction_is_gold_isolated() -> None:
    tmp_path = make_local_test_dir("compile_gold_isolation")
    try:
        db_path = tmp_path / "toy.sqlite"
        with sqlite3.connect(db_path) as con:
            con.execute("CREATE TABLE items (id INTEGER NOT NULL, status TEXT NOT NULL)")
            con.commit()
        model_side_input = {
            "question": "Insert item 5 with status completed.",
            "schema_inventory": {
                "columns": [
                    {"column_ref": "COL_ID", "column_name": "id", "source_type": "INTEGER", "nullable": False, "has_default": False, "table_ref": "TAB_1"},
                    {"column_ref": "COL_STATUS", "column_name": "status", "source_type": "TEXT", "nullable": False, "has_default": False, "table_ref": "TAB_1"},
                ],
                "tables": [{"table_ref": "TAB_1", "table_name": "items"}],
            },
        }
        runtime_constraints = {
            "candidate_inventory": [
                {"span_ref": "SPAN_ID", "text": "5", "start_char": 12, "end_char": 13},
                {"span_ref": "SPAN_STATUS", "text": "completed", "start_char": 26, "end_char": 35},
            ]
        }
        prediction = {"operation": "INSERT", "table_ref": "TAB_1", "column_span_refs": {"COL_ID": "SPAN_ID", "COL_STATUS": "SPAN_STATUS"}}
        signature = inspect.signature(compile_column_conditioned_prediction)
        assert "label_side_expected" not in signature.parameters
        first = compile_column_conditioned_prediction(
            sample_id="toy",
            model_side_input=model_side_input,
            runtime_constraints=runtime_constraints,
            phase_o_prediction=prediction,
            db_path=db_path,
        )
        noisy_gold_side = {"label_side_expected": {"phase_o": {"column_span_refs": {"COL_ID": "SPAN_WRONG"}}, "target_state": {"typed_target_rows": [{"id": 999}]}}}
        second = compile_column_conditioned_prediction(
            sample_id="toy",
            model_side_input=model_side_input,
            runtime_constraints=runtime_constraints,
            phase_o_prediction=prediction,
            db_path=db_path,
        )
        assert noisy_gold_side
        assert first["compiled_sql"] == second["compiled_sql"]
        assert first["compiled_parameters"] == second["compiled_parameters"]
        assert first["preflight"] == second["preflight"] == "ADMITTED"
        assert "candidate_inventory_contains_all_gold_spans" not in first
        assert "expected_target_state_hash" not in first
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_live_model_identity_checks_fail_closed() -> None:
    tmp_path = make_local_test_dir("live_identity")
    try:
        frozen_path = tmp_path / MODEL_REVISION
        frozen_path.mkdir()
        assert verify_live_model_identity(
            model_name_or_path=str(frozen_path),
            tokenizer_name_or_path=str(frozen_path),
            chat_template_sha256=EXPECTED_CHAT_TEMPLATE_SHA256,
        )["chat_template_hash_verified"] is True
        wrong_revision = tmp_path / "wrong-revision"
        wrong_revision.mkdir()
        with pytest.raises(V2A1Error):
            verify_live_model_identity(
                model_name_or_path=str(wrong_revision),
                tokenizer_name_or_path=str(frozen_path),
                chat_template_sha256=EXPECTED_CHAT_TEMPLATE_SHA256,
            )
        with pytest.raises(V2A1Error):
            verify_live_model_identity(
                model_name_or_path=str(frozen_path),
                tokenizer_name_or_path=str(frozen_path),
                chat_template_sha256="0" * 64,
            )
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_stageeng2b_final_runner_cli_is_self_contained() -> None:
    help_proc = subprocess.run(
        [sys.executable, "scripts/server/run_eng2_final_method.py", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--mode" in help_proc.stdout
    assert "replay" in help_proc.stdout
    assert "live" in help_proc.stdout
    dry_proc = subprocess.run(
        [sys.executable, "scripts/server/run_eng2_final_method.py", "--mode", "live", "--dry-run-live-config"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert dry_proc.returncode == 0, dry_proc.stderr
    config = json.loads(dry_proc.stdout)
    assert config["method_id"] == "M2_FINAL_ENG2B"
    assert config["generation_settings"]["calls_per_sample"] == 1
    assert config["generation_settings"]["retry"] == 0
