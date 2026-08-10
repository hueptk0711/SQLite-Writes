from __future__ import annotations

import unittest

from nldbwrite_v3.compiler import (
    check_semantic_risk_gate,
    compile_write_plan,
    normalize_value,
    preflight_program,
)
from nldbwrite_v3.verifier import verify_write_plan
from tests.helpers import conflict, group, plan, test_profile


class VerifierCompilerTests(unittest.TestCase):
    def setUp(self):
        self.profile = test_profile()

    def test_multi_row_sql_is_parameterized(self):
        result = compile_write_plan(
            plan(
                [
                    group(
                        "g1",
                        "parent",
                        [
                            {"id": "001", "name": "O'Brien"},
                            {"id": "002", "name": "Two"},
                        ],
                    )
                ]
            ),
            self.profile,
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.statements), 1)
        self.assertEqual(result.statements[0].row_count, 2)
        self.assertIn("VALUES (?, ?), (?, ?)", result.statements[0].sql)
        self.assertNotIn("O'Brien", result.statements[0].sql)
        self.assertIn("O'Brien", result.statements[0].params)

    def test_do_nothing_is_explicit(self):
        result = compile_write_plan(
            plan(
                [
                    group(
                        "g1",
                        "parent",
                        [{"id": "001", "name": "One"}],
                        conflict("do_nothing", ["id"]),
                    )
                ]
            ),
            self.profile,
        )
        self.assertIn(
            'ON CONFLICT ("id") DO NOTHING',
            result.statements[0].sql,
        )

    def test_do_update_has_explicit_mask(self):
        result = compile_write_plan(
            plan(
                [
                    group(
                        "g1",
                        "parent",
                        [{"id": "001", "name": "New", "count": 2}],
                        conflict("do_update", ["id"], ["name"]),
                    )
                ]
            ),
            self.profile,
        )
        sql = result.statements[0].sql
        self.assertIn('"name" = excluded."name"', sql)
        self.assertNotIn('"count" = excluded."count"', sql)

    def test_do_update_column_without_row_value_fails_semantic_risk_gate(self):
        candidate = plan(
            [
                group(
                    "g1",
                    "parent",
                    [{"id": "001", "name": "New"}],
                    conflict("do_update", ["id"], ["name", "count"]),
                )
            ]
        )
        verification = verify_write_plan(candidate, self.profile)
        self.assertTrue(verification.valid)
        self.assertIn(
            "UPDATE_COLUMN_MISSING_VALUE",
            {
                warning.error_code
                for warning in verification.warnings
            },
        )
        program = compile_write_plan(candidate, self.profile)
        self.assertEqual(program.status, "success")
        semantic_gate = check_semantic_risk_gate(program)
        self.assertFalse(semantic_gate["accepted"])
        self.assertEqual(
            semantic_gate["error_class"],
            "semantic_grounding_risk",
        )
        self.assertIn(
            "UPDATE_COLUMN_MISSING_VALUE",
            semantic_gate["error_codes"],
        )

    def test_key_only_upsert_becomes_do_nothing(self):
        profile = test_profile()
        profile["tables"][0]["required_insert_columns"] = ["id"]
        result = compile_write_plan(
            plan(
                [
                    group(
                        "g1",
                        "parent",
                        [{"id": "001"}],
                        conflict("do_update", ["id"], []),
                    )
                ]
            ),
            profile,
        )
        self.assertEqual(result.status, "success")
        self.assertIn("DO NOTHING", result.statements[0].sql)
        self.assertIn(
            "KEY_ONLY_UPSERT_DOWNGRADED",
            {warning.error_code for warning in result.warnings},
        )

    def test_invalid_conflict_target_is_rejected(self):
        result = verify_write_plan(
            plan(
                [
                    group(
                        "g1",
                        "parent",
                        [{"id": "001", "name": "One"}],
                        conflict("do_nothing", ["name"]),
                    )
                ]
            ),
            self.profile,
        )
        self.assertIn(
            "INVALID_CONFLICT_TARGET",
            {error.error_code for error in result.errors},
        )

    def test_semantic_alias_is_not_fuzzy_matched(self):
        result = verify_write_plan(
            plan(
                [
                    group(
                        "g1",
                        "parent",
                        [{"identifier": "001", "name": "One"}],
                    )
                ]
            ),
            self.profile,
        )
        self.assertIn(
            "UNKNOWN_COLUMN",
            {error.error_code for error in result.errors},
        )

    def test_strict_atomic_rejects_all_statements(self):
        candidate = plan(
            [
                group("good", "parent", [{"id": "001", "name": "One"}]),
                group("bad", "parent", [{"id": "002", "unknown": "x"}]),
            ]
        )
        strict = compile_write_plan(candidate, self.profile, strict_atomic=True)
        best_effort = compile_write_plan(
            candidate,
            self.profile,
            strict_atomic=False,
        )
        self.assertEqual(strict.status, "error")
        self.assertEqual(strict.statements, [])
        self.assertEqual(best_effort.status, "partial")
        self.assertEqual(len(best_effort.statements), 1)

    def test_fk_parent_is_compiled_before_child(self):
        candidate = plan(
            [
                group("child", "child", [{"parent_id": "p1", "note": "n"}]),
                group("parent", "parent", [{"id": "p1", "name": "P"}]),
            ]
        )
        result = compile_write_plan(candidate, self.profile)
        self.assertEqual(
            [statement.group_id for statement in result.statements],
            ["parent", "child"],
        )

    def test_dependency_cycle_is_rejected(self):
        candidate = plan(
            [
                group("a", "parent", [{"id": "a", "name": "A"}]),
                group("b", "parent", [{"id": "b", "name": "B"}]),
            ],
            [{"before": "a", "after": "b"}, {"before": "b", "after": "a"}],
        )
        result = verify_write_plan(candidate, self.profile)
        self.assertIn(
            "DEPENDENCY_CYCLE",
            {error.error_code for error in result.errors},
        )

    def test_identifier_leading_zero_is_preserved(self):
        identifier_column = self.profile["tables"][0]["columns"][0]
        count_column = self.profile["tables"][0]["columns"][2]
        self.assertEqual(normalize_value("00123", identifier_column), "00123")
        self.assertEqual(normalize_value("00123", count_column), 123)


if __name__ == "__main__":
    unittest.main()
