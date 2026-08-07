from __future__ import annotations

import unittest

from nldbwrite_v3.analysis.independent_audit import (
    _conditional_accuracy,
    _database_macro,
)


class IndependentAuditTests(unittest.TestCase):
    def test_database_macro_weights_databases_equally(self):
        rows = [
            {"db_id": "a", "target_state_correct": True},
            {"db_id": "b", "target_state_correct": False},
            {"db_id": "b", "target_state_correct": False},
            {"db_id": "b", "target_state_correct": False},
        ]
        self.assertEqual(_database_macro(rows), 0.5)

    def test_conditional_accuracy_uses_only_admitted_rows(self):
        rows = [
            {"accepted": True, "correct": True},
            {"accepted": True, "correct": False},
            {"accepted": False, "correct": False},
        ]
        self.assertEqual(
            _conditional_accuracy(rows, "accepted", "correct"),
            0.5,
        )


if __name__ == "__main__":
    unittest.main()

