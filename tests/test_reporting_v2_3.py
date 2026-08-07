from __future__ import annotations

import tarfile
import unittest
from pathlib import Path

from nldbwrite_v3.analysis.reporting_v2_3 import (
    _complexity,
    _validate_archive_members,
    corrected_evaluations,
    leave_one_database_out,
    portable_filename,
    set_f1,
    slice_failure_decomposition,
    target_columns,
    taxonomy_tables,
)


class ReportingV23Tests(unittest.TestCase):
    def test_target_columns_come_from_write_plan_rows(self):
        plan = {
            "write_groups": [
                {"table": "alpha", "rows": [{"id": 1, "name": "A"}]},
                {"table": "beta", "rows": [{"id": 2}]},
            ]
        }
        self.assertEqual(
            target_columns(plan),
            {"alpha.id", "alpha.name", "beta.id"},
        )
        self.assertEqual(set_f1(target_columns(plan), target_columns(plan)), 1.0)

    def test_corrected_target_column_f1_is_one_for_gold(self):
        plan = {"write_groups": [{"table": "t", "rows": [{"c": 1}]}]}
        rows = [
            {
                "sample_id": "s1",
                "plan_metrics_available": True,
                "target_column_f1": 0.0,
            }
        ]
        corrected = corrected_evaluations(
            rows,
            {"s1": plan},
            {"s1": plan},
            plan_metrics_applicable=True,
        )
        self.assertEqual(corrected[0]["target_column_f1"], 1.0)
        self.assertEqual(
            corrected[0]["target_column_metric_source"],
            "derived_from_gold_plan_v2_3",
        )

    def test_windows_archive_path_is_portable_on_posix(self):
        raw = r"D:\workspace\results\final.tar.gz"
        self.assertEqual(portable_filename(raw), "final.tar.gz")
        self.assertEqual(
            portable_filename("/workspace/results/final.tar.gz"),
            "final.tar.gz",
        )

    def test_corrected_rows_detect_wrong_target_and_off_target_change(self):
        gold = {
            "write_groups": [
                {"table": "personnel", "rows": [{"id": 1}]},
            ]
        }
        rows = [
            {
                "sample_id": "s-off-target",
                "plan_metrics_available": False,
                "target_state_correct": False,
                "strict_full_state_correct": False,
                "target_mismatched_tables": ["personnel"],
                "strict_mismatched_tables": ["equipment", "personnel"],
                "side_effect": False,
                "error_type": "wrong_state",
            }
        ]
        corrected = corrected_evaluations(
            rows,
            {"s-off-target": None},
            {"s-off-target": gold},
            plan_metrics_applicable=False,
        )[0]
        self.assertTrue(corrected["any_off_target_change"])
        self.assertFalse(corrected["target_correct_with_side_effect"])
        self.assertTrue(corrected["side_effect"])
        self.assertEqual(
            corrected["off_target_mismatched_tables"],
            ["equipment"],
        )
        self.assertEqual(
            corrected["error_type"],
            "wrong_state_with_off_target_change",
        )

    def test_leave_one_database_out_uses_remaining_rows(self):
        rows = {
            "D-FS-M": [
                {"sample_id": "a", "db_id": "x", "target_state_correct": True},
                {"sample_id": "b", "db_id": "y", "target_state_correct": False},
            ],
            "J-FS-M": [
                {"sample_id": "a", "db_id": "x", "target_state_correct": True},
                {"sample_id": "b", "db_id": "y", "target_state_correct": True},
            ],
            "S-FS-v2-M": [
                {"sample_id": "a", "db_id": "x", "target_state_correct": False},
                {"sample_id": "b", "db_id": "y", "target_state_correct": False},
            ],
            "MP-FS-M": [
                {"sample_id": "a", "db_id": "x", "target_state_correct": False},
                {"sample_id": "b", "db_id": "y", "target_state_correct": False},
            ],
            "MP-FS+": [
                {"sample_id": "a", "db_id": "x", "target_state_correct": True},
                {"sample_id": "b", "db_id": "y", "target_state_correct": False},
            ],
            "Gold-MP": [
                {"sample_id": "a", "db_id": "x", "target_state_correct": True},
                {"sample_id": "b", "db_id": "y", "target_state_correct": True},
            ],
        }
        result = leave_one_database_out(rows)
        target = next(
            row
            for row in result
            if row["comparison"] == "MP-FS+ vs MP-FS-M"
            and row["excluded_database"] == "y"
        )
        self.assertEqual(target["samples"], 1)
        self.assertEqual(target["absolute_difference"], 1.0)

    def test_archive_extraction_rejects_path_traversal(self):
        member = tarfile.TarInfo("../outside.txt")
        with self.assertRaisesRegex(ValueError, "Unsafe path"):
            _validate_archive_members([member], Path("safe_destination"))

    def test_complexity_comes_from_locked_slice_labels(self):
        self.assertEqual(_complexity({"slice_labels": ["multi_table"]}), "multi_table")
        self.assertEqual(_complexity({"slice_labels": ["single_table"]}), "single_table")
        self.assertEqual(_complexity({"slice_labels": []}), "unknown")

    def test_taxonomy_and_slice_decomposition_cover_complexity(self):
        row = {
            "sample_id": "s1",
            "db_id": "db",
            "slice_labels": ["input_format:csv", "multi_table"],
            "operation_semantics": "insert",
            "parse_success": True,
            "plan_validation_success": True,
            "build_success": True,
            "execution_success": True,
            "target_state_correct": True,
            "strict_full_state_correct": True,
            "side_effect_free": True,
            "accepted_output": True,
            "input_truncated": False,
            "hit_max_new_tokens": False,
        }
        overall, by_format, by_complexity = taxonomy_tables({"D-FS-M": [row]})
        self.assertEqual(sum(item["count"] for item in overall), 1)
        self.assertEqual(by_format[0]["input_format"], "csv")
        self.assertEqual(by_complexity[0]["complexity"], "multi_table")
        slices = slice_failure_decomposition({"D-FS-M": [row]})
        complexity = next(item for item in slices if item["dimension"] == "complexity")
        self.assertEqual(complexity["value"], "multi_table")
        self.assertEqual(complexity["target_state_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
