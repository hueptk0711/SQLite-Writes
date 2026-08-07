from __future__ import annotations

import unittest

from nldbwrite_v3.data import audit_calibration_metadata


def _sample(index: int) -> dict:
    operation = (
        "plain_insert"
        if index < 20
        else "insert_ignore"
        if index < 40
        else "upsert_update"
    )
    input_mode = "free_text" if index < 20 else "semi_structured"
    complexity = (
        "single_row"
        if index % 3 == 0
        else "small_batch"
        if index % 3 == 1
        else "large_or_relational"
    )
    return {
        "id": f"cal_{index:03d}",
        "db_id": "cal_db_a" if index < 30 else "cal_db_b",
        "input_text": f"Original calibration request {index}",
        "input_mode": input_mode,
        "input_format": "free_text" if input_mode == "free_text" else "json",
        "complexity": complexity,
        "operation_semantics": operation,
        "semantics_explicit_in_request": True,
        "semantics_source": "request",
        "state_changing": True,
        "conflict_sensitive": operation != "plain_insert",
        "multi_table": index % 3 == 2,
        "conflict_target": [] if operation == "plain_insert" else ["id"],
        "update_columns": ["name"] if operation == "upsert_update" else [],
        "gold_sql": ["INSERT INTO t(id) VALUES (1);"],
        "gold_plan": {},
        "source_group": f"cal_{index:03d}",
        "author_id": "author_a",
        "independently_authored": True,
        "is_augmented": False,
        "qa_reviews": [
            {
                "reviewer_id": "reviewer_1",
                "decision": "approved",
                "semantics_correct": True,
                "gold_target_correct": True,
                "conflict_target_correct": True,
                "update_columns_correct": True,
                "hidden_policy": False,
            },
            {
                "reviewer_id": "reviewer_2",
                "decision": "approved",
                "semantics_correct": True,
                "gold_target_correct": True,
                "conflict_target_correct": True,
                "update_columns_correct": True,
                "hidden_policy": False,
            },
        ],
    }


class CalibrationAuditTests(unittest.TestCase):
    def test_balanced_independent_calibration_metadata_passes(self):
        issues, summary = audit_calibration_metadata(
            [_sample(index) for index in range(60)],
            reserved_final_db_ids={"final_a", "final_b", "final_c"},
        )
        self.assertEqual(issues, [])
        self.assertEqual(summary["status"], "valid")
        self.assertEqual(summary["multi_table_samples"], 20)

    def test_consumed_database_and_source_overlap_are_blocking(self):
        samples = [_sample(index) for index in range(60)]
        samples[0]["db_id"] = "formula_1"
        issues, _ = audit_calibration_metadata(
            samples,
            reserved_final_db_ids={"final_a", "final_b", "final_c"},
            consumed_source_groups={"cal_000"},
        )
        codes = {issue["error_code"] for issue in issues}
        self.assertIn("CONSUMED_DATABASE_OVERLAP", codes)
        self.assertIn("CONSUMED_SOURCE_GROUP_OVERLAP", codes)

    def test_author_cannot_approve_own_sample(self):
        samples = [_sample(index) for index in range(60)]
        samples[0]["qa_reviews"][0]["reviewer_id"] = "author_a"
        issues, _ = audit_calibration_metadata(
            samples,
            reserved_final_db_ids={"final_a", "final_b", "final_c"},
        )
        self.assertIn(
            "AUTHOR_REVIEWER_NOT_INDEPENDENT",
            {issue["error_code"] for issue in issues},
        )


if __name__ == "__main__":
    unittest.main()
