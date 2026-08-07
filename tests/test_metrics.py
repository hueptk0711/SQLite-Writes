from __future__ import annotations

import unittest
from types import SimpleNamespace

from nldbwrite_v3.experiments.metrics import error_taxonomy_row, summarize_run
from nldbwrite_v3.experiments.run_method import _plan_metrics


class MetricsTests(unittest.TestCase):
    def test_plan_metrics_and_slices_are_aggregated(self):
        rows = [
            {
                "parse_success": True,
                "plan_validation_success": True,
                "build_success": True,
                "execution_success": True,
                "target_state_correct": True,
                "strict_full_state_correct": True,
                "side_effect": False,
                "source_parse_row_count_exact": True,
                "plan_metrics_available": True,
                "row_count_exact": True,
                "row_coverage": 1.0,
                "row_exact_match": True,
                "cell_value_f1": 1.0,
                "payload_copy_integrity": 1.0,
                "conflict_action_correct": True,
                "conflict_target_exact": True,
                "conflict_update_column_f1": 1.0,
                "conflict_full_exact": True,
                "table_exact": True,
                "target_column_f1": 0.8,
                "slice_labels": ["semi_structured", "batch_large"],
            },
            {
                "parse_success": True,
                "plan_validation_success": True,
                "build_success": True,
                "execution_success": True,
                "target_state_correct": False,
                "strict_full_state_correct": False,
                "side_effect": False,
                "source_parse_row_count_exact": False,
                "plan_metrics_available": True,
                "row_count_exact": False,
                "row_coverage": 0.5,
                "row_exact_match": False,
                "cell_value_f1": 0.5,
                "payload_copy_integrity": 0.5,
                "conflict_action_correct": False,
                "conflict_target_exact": False,
                "conflict_update_column_f1": 0.5,
                "conflict_full_exact": False,
                "table_exact": False,
                "target_column_f1": 0.4,
                "slice_labels": ["semi_structured"],
            },
        ]
        metrics = summarize_run(rows)
        self.assertEqual(metrics["source_parse_row_count_accuracy"], 0.5)
        self.assertEqual(metrics["row_coverage"], 0.75)
        self.assertEqual(metrics["mapping_table_accuracy"], 0.5)
        self.assertAlmostEqual(metrics["target_column_f1"], 0.6)
        self.assertEqual(metrics["cell_value_f1"], 0.75)
        self.assertEqual(metrics["plan_metric_coverage"], 1.0)
        self.assertAlmostEqual(metrics["conditional_target_column_f1"], 0.6)
        self.assertAlmostEqual(metrics["end_to_end_target_column_f1"], 0.6)
        self.assertEqual(
            metrics["slices"]["batch_large"]["target_state_accuracy"],
            1.0,
        )

    def test_error_taxonomy_distinguishes_mapping_and_conflict_target(self):
        mapping = error_taxonomy_row(
            {"parse_success": True, "error_type": "INVALID_FIELD_MAPPING"}
        )
        conflict = error_taxonomy_row(
            {"parse_success": True, "error_type": "INVALID_CONFLICT_TARGET"}
        )
        self.assertEqual(mapping["error_category"], "E5_wrong_mapping")
        self.assertEqual(
            conflict["error_category"],
            "E11_conflict_target_or_mask",
        )
        self.assertEqual(mapping["error_stage"], "source_grounding")
        self.assertEqual(conflict["error_stage"], "interpretation")

    def test_off_target_metrics_count_changes_when_target_is_also_wrong(self):
        rows = [
            {
                "target_state_correct": False,
                "any_off_target_change": True,
                "target_correct_with_side_effect": False,
            },
            {
                "target_state_correct": True,
                "any_off_target_change": False,
                "target_correct_with_side_effect": False,
            },
        ]
        metrics = summarize_run(rows)
        self.assertEqual(metrics["side_effect_rate"], 0.5)
        self.assertEqual(metrics["any_off_target_change_rate"], 0.5)
        self.assertEqual(metrics["target_correct_with_side_effect_rate"], 0.0)
        taxonomy = error_taxonomy_row(
            {
                "parse_success": True,
                "error_type": "wrong_state_with_off_target_change",
            }
        )
        self.assertEqual(
            taxonomy["error_category"],
            "E17_state_mismatch_with_off_target_change",
        )

    def test_mp_fs_plus_error_codes_do_not_fall_into_other(self):
        expected = {
            "UNKNOWN_COLUMN_ID": "E3_unknown_column",
            "NEEDS_CLARIFICATION": "E10_conflict_semantics",
            "LOSSY_NORMALIZATION_REJECTED": "E8_wrong_or_hallucinated_value",
            "UNKNOWN_SOURCE_FIELD_ID": "E5_wrong_mapping",
            "DUPLICATE_TARGET_COLUMN_AFTER_EVIDENCE_GROUNDING": "E7_duplicate",
            "MISSING_UPDATE_COLUMN_IDS": "E11_conflict_target_or_mask",
            "UNKNOWN_CONSTRAINT_ID": "E11_conflict_target_or_mask",
        }
        for error_type, category in expected.items():
            with self.subTest(error_type=error_type):
                actual = error_taxonomy_row(
                    {"parse_success": True, "error_type": error_type}
                )
                self.assertEqual(actual["error_category"], category)

    def test_target_columns_are_derived_from_gold_plan(self):
        plan = {
            "write_groups": [
                {
                    "table": "equipment",
                    "rows": [{"equipment_id": "E1", "status": "ready"}],
                    "conflict": {"action": "error"},
                }
            ]
        }
        payload = SimpleNamespace(mode="free_text", rows=[], collections=[])
        result = _plan_metrics({}, payload, plan, plan)
        self.assertEqual(result["target_column_f1"], 1.0)

    def test_stage_funnel_and_plan_denominators_are_explicit(self):
        metrics = summarize_run(
            [
                {
                    "method": "MP-FS+",
                    "generation_status": "success",
                    "parse_success": True,
                    "plan_validation_success": True,
                    "build_success": True,
                    "execution_success": True,
                    "target_state_correct": True,
                    "accepted_output": True,
                    "preflight_accepted": True,
                    "plan_metrics_available": True,
                    "target_column_f1": 1.0,
                    "cell_value_f1": 1.0,
                    "row_count_exact": True,
                    "row_exact_match": True,
                    "conflict_target_exact": True,
                    "table_exact": True,
                },
                {
                    "method": "MP-FS+",
                    "generation_status": "success",
                    "parse_success": True,
                    "plan_validation_success": False,
                    "build_success": False,
                    "execution_success": False,
                    "target_state_correct": False,
                    "accepted_output": False,
                    "preflight_accepted": False,
                    "plan_metrics_available": False,
                },
            ]
        )
        self.assertEqual(metrics["generation_coverage"], 1.0)
        self.assertEqual(metrics["parse_coverage"], 1.0)
        self.assertEqual(metrics["validation_coverage"], 0.5)
        self.assertEqual(metrics["build_coverage"], 0.5)
        self.assertEqual(metrics["execution_coverage"], 0.5)
        self.assertEqual(metrics["execution_conditional_accuracy"], 1.0)
        self.assertEqual(metrics["plan_metric_coverage"], 0.5)
        self.assertEqual(metrics["conditional_target_column_f1"], 1.0)
        self.assertEqual(metrics["end_to_end_target_column_f1"], 0.5)
        self.assertEqual(metrics["admission_boundary"], "transactional_preflight")

    def test_selective_reliability_reports_coverage_and_risk(self):
        metrics = summarize_run(
            [
                {
                    "accepted_output": True,
                    "target_state_correct": True,
                },
                {
                    "accepted_output": True,
                    "target_state_correct": False,
                },
                {
                    "accepted_output": False,
                    "target_state_correct": False,
                },
                {
                    "accepted_output": False,
                    "target_state_correct": False,
                },
            ]
        )
        self.assertEqual(metrics["coverage"], 0.5)
        self.assertEqual(metrics["accepted_output_accuracy"], 0.5)
        self.assertEqual(metrics["abstention_rate"], 0.5)
        self.assertEqual(metrics["selective_risk"], 0.5)


if __name__ == "__main__":
    unittest.main()
