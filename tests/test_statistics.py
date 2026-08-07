from __future__ import annotations

import unittest

from nldbwrite_v3.analysis import (
    adjust_comparison_family,
    exact_mcnemar,
    holm_bonferroni,
    paired_cluster_bootstrap,
    paired_database_macro_bootstrap,
)


class StatisticsTests(unittest.TestCase):
    def test_exact_mcnemar(self):
        result = exact_mcnemar(
            [False, False, False, True],
            [True, True, True, True],
        )
        self.assertEqual(result["right_only_correct"], 3)
        self.assertAlmostEqual(result["p_value_two_sided_exact"], 0.25)

    def test_holm_is_monotone_in_sorted_order(self):
        adjusted = holm_bonferroni([0.01, 0.04, 0.03])
        self.assertEqual(adjusted, [0.03, 0.06, 0.06])

    def test_cluster_bootstrap_is_seeded(self):
        first = paired_cluster_bootstrap(
            [1.0, 1.0, -1.0],
            ["a", "a", "b"],
            iterations=200,
            seed=7,
        )
        second = paired_cluster_bootstrap(
            [1.0, 1.0, -1.0],
            ["a", "a", "b"],
            iterations=200,
            seed=7,
        )
        self.assertEqual(first, second)
        self.assertAlmostEqual(first["observed_mean_difference"], 1 / 3)

    def test_database_macro_bootstrap_weights_databases_equally(self):
        result = paired_database_macro_bootstrap(
            [1.0, 1.0, -1.0],
            ["large", "large", "small"],
            iterations=200,
            seed=5,
        )
        self.assertEqual(result["database_count"], 2)
        self.assertEqual(result["observed_database_macro_difference"], 0.0)

    def test_holm_adjustment_uses_whole_comparison_family(self):
        adjusted = adjust_comparison_family(
            [
                {"mcnemar": {"p_value_two_sided_exact": 0.01}},
                {"mcnemar": {"p_value_two_sided_exact": 0.04}},
                {"mcnemar": {"p_value_two_sided_exact": 0.03}},
            ]
        )
        self.assertEqual(
            [
                row["mcnemar"]["p_value_holm_family"]
                for row in adjusted
            ],
            [0.03, 0.06, 0.06],
        )


if __name__ == "__main__":
    unittest.main()
