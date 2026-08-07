from __future__ import annotations

import unittest

from nldbwrite_v3.analysis.exploratory_v2_4 import (
    build_cascade,
    exact_mcnemar_from_counts,
    wilson_interval,
    zero_event_upper_bound,
)
from nldbwrite_v3.verifier import verify_write_plan
from tests.helpers import group, plan, test_profile


class ExploratoryV24Tests(unittest.TestCase):
    def test_wilson_interval_matches_primary_258_of_300(self):
        low, high = wilson_interval(258, 300)
        self.assertAlmostEqual(low, 0.8161683876490509)
        self.assertAlmostEqual(high, 0.8947286730692758)

    def test_zero_event_one_sided_bound(self):
        self.assertAlmostEqual(zero_event_upper_bound(300), 0.00993608194445772)

    def test_exact_mcnemar_for_24_wins_and_zero_losses(self):
        self.assertAlmostEqual(
            exact_mcnemar_from_counts(24, 0),
            1.1920928955078125e-7,
        )

    def test_cascade_uses_j_then_d_then_abstains(self):
        samples = {
            sample_id: {"id": sample_id, "db_id": "db", "gold_tables": ["t"]}
            for sample_id in ("s1", "s2", "s3")
        }
        common_rows = [
            {"method_id": "J-FS-M", "sample_id": "s1", "preflight_accepted": True},
            {"method_id": "D-FS-M", "sample_id": "s1", "preflight_accepted": True},
            {"method_id": "J-FS-M", "sample_id": "s2", "preflight_accepted": False},
            {"method_id": "D-FS-M", "sample_id": "s2", "preflight_accepted": True},
            {"method_id": "J-FS-M", "sample_id": "s3", "preflight_accepted": False},
            {"method_id": "D-FS-M", "sample_id": "s3", "preflight_accepted": False},
        ]
        primary_rows = {
            "J-FS-M": [
                {"sample_id": "s1", "target_state_correct": True},
                {"sample_id": "s2", "target_state_correct": False},
                {"sample_id": "s3", "target_state_correct": False},
            ],
            "D-FS-M": [
                {"sample_id": "s1", "target_state_correct": False},
                {"sample_id": "s2", "target_state_correct": True},
                {"sample_id": "s3", "target_state_correct": False},
            ],
            "MP-FS+": [
                {"sample_id": "s1", "target_state_correct": False},
                {"sample_id": "s2", "target_state_correct": False},
                {"sample_id": "s3", "target_state_correct": False},
            ],
        }
        summary, rows = build_cascade(common_rows, primary_rows, samples)
        self.assertEqual([row["selected_method"] for row in rows], ["J-FS-M", "D-FS-M", None])
        self.assertEqual(summary["admitted"], 2)
        self.assertEqual(summary["correct"], 2)

    def test_provenance_switch_changes_only_provenance_gate(self):
        candidate = plan(
            [group("g1", "parent", [{"id": "001", "name": "Alice"}])]
        )
        candidate["source"] = {
            "mode": "semi_structured",
            "format": "json",
            "row_count": 1,
        }
        strict = verify_write_plan(candidate, test_profile())
        without_provenance = verify_write_plan(
            candidate,
            test_profile(),
            check_provenance=False,
        )
        self.assertFalse(strict.valid)
        self.assertEqual({error.error_code for error in strict.errors}, {"PROVENANCE_MISMATCH"})
        self.assertTrue(without_provenance.valid)


if __name__ == "__main__":
    unittest.main()
