from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from nldbwrite_v3.compiler import compile_write_plan, execute_program
from nldbwrite_v3.data.gold_sql import parse_gold_sql
from nldbwrite_v3.evaluator import (
    evaluate_candidate_sample,
    evaluate_oracle_sample,
)
from nldbwrite_v3.repair import (
    PatchError,
    apply_plan_patch,
    evaluate_repair_candidate,
    repair_and_validate,
)
from nldbwrite_v3.source_parser import parse_source_payload
from tests.helpers import conflict, group, plan, test_profile


def create_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE parent (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            count INTEGER
        );
        CREATE TABLE child (
            id INTEGER PRIMARY KEY,
            parent_id TEXT NOT NULL REFERENCES parent(id) ON DELETE CASCADE,
            note TEXT NOT NULL
        );
        CREATE TABLE pair (
            a TEXT NOT NULL,
            b TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (a, b)
        );
        CREATE TABLE "order details" (
            id INTEGER PRIMARY KEY,
            note TEXT NOT NULL
        );
        INSERT INTO parent(id, name, count) VALUES ('existing', 'Old', 1);
        """
    )
    conn.commit()
    return conn


class GoldParserTests(unittest.TestCase):
    def test_insert_or_ignore(self):
        parsed = parse_gold_sql(
            ["INSERT OR IGNORE INTO parent (id, name) VALUES ('001', 'One');"]
        )
        conflict_policy = parsed["write_groups"][0]["conflict"]
        self.assertEqual(conflict_policy["action"], "do_nothing")
        self.assertEqual(conflict_policy["target"], [])
        self.assertEqual(parsed["write_groups"][0]["rows"][0]["id"], "001")

    def test_composite_do_update_and_returning(self):
        parsed = parse_gold_sql(
            [
                'INSERT INTO pair ("a", "b", "value") VALUES '
                "('x', 'y', 'new') "
                'ON CONFLICT ("a", "b") DO UPDATE SET '
                '"value" = excluded."value" RETURNING "a";'
            ]
        )
        conflict_policy = parsed["write_groups"][0]["conflict"]
        self.assertEqual(conflict_policy["action"], "do_update")
        self.assertEqual(conflict_policy["target"], ["a", "b"])
        self.assertEqual(conflict_policy["update_columns"], ["value"])


class ExecutionTests(unittest.TestCase):
    def test_atomic_rollback(self):
        conn = create_database()
        try:
            candidate = plan(
                [
                    group("a", "parent", [{"id": "new", "name": "New"}]),
                    group("b", "parent", [{"id": "existing", "name": "Dup"}]),
                ]
            )
            program = compile_write_plan(candidate, test_profile())
            result = execute_program(conn, program)
            self.assertEqual(result["status"], "execution_error")
            count = conn.execute(
                "SELECT COUNT(*) FROM parent WHERE id='new'"
            ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            conn.close()

    def test_oracle_compiler_matches_gold_state(self):
        conn = create_database()
        try:
            sql = (
                "INSERT INTO parent (id, name, count) "
                "VALUES ('existing', 'New', 2) "
                "ON CONFLICT (id) DO UPDATE SET "
                "name=excluded.name, count=excluded.count;"
            )
            sample = {
                "id": "s1",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [sql],
            }
            gold_plan = parse_gold_sql(
                [sql],
                sample_id="s1",
                profile=test_profile(),
            )
            result = evaluate_oracle_sample(
                sample,
                gold_plan,
                test_profile(),
                conn,
            )
            self.assertTrue(result["execution_success"])
            self.assertTrue(result["target_state_correct"])
            self.assertTrue(result["strict_full_state_correct"])
        finally:
            conn.close()

    def test_candidate_wrong_state_is_reported_without_crashing(self):
        conn = create_database()
        try:
            sample = {
                "id": "s2",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "INSERT INTO parent(id, name) VALUES ('wrong', 'Wrong');"
                ],
            )
            self.assertTrue(result["execution_success"])
            self.assertFalse(result["target_state_correct"])
            self.assertEqual(result["error_type"], "wrong_state")
        finally:
            conn.close()

    def test_candidate_side_effect_is_detected_in_fk_closure(self):
        conn = create_database()
        try:
            sample = {
                "id": "s3",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');",
                    "INSERT INTO child(id, parent_id, note) "
                    "VALUES (99, 'gold', 'extra');",
                ],
            )
            self.assertTrue(result["target_state_correct"])
            self.assertFalse(result["strict_full_state_correct"])
            self.assertTrue(result["side_effect"])
            self.assertTrue(result["any_off_target_change"])
            self.assertTrue(result["target_correct_with_side_effect"])
            self.assertEqual(result["off_target_mismatched_tables"], ["child"])
            self.assertEqual(result["error_type"], "unintended_side_effect")
        finally:
            conn.close()

    def test_wrong_target_and_off_target_change_are_both_reported(self):
        conn = create_database()
        try:
            sample = {
                "id": "s4",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "INSERT INTO parent(id, name) VALUES ('wrong', 'Wrong');",
                    "INSERT INTO child(id, parent_id, note) "
                    "VALUES (100, 'wrong', 'off target');",
                ],
            )
            self.assertFalse(result["target_state_correct"])
            self.assertFalse(result["strict_full_state_correct"])
            self.assertTrue(result["side_effect"])
            self.assertTrue(result["any_off_target_change"])
            self.assertFalse(result["target_correct_with_side_effect"])
            self.assertEqual(result["target_mismatched_tables"], ["parent"])
            self.assertEqual(result["off_target_mismatched_tables"], ["child"])
            self.assertEqual(
                result["error_type"],
                "wrong_state_with_off_target_change",
            )
        finally:
            conn.close()

    def test_all_table_scope_catches_schema_qualified_side_effect(self):
        conn = create_database()
        try:
            sample = {
                "id": "s-schema-qualified",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');",
                    "INSERT INTO main.child(id, parent_id, note) "
                    "VALUES (101, 'gold', 'qualified side effect');",
                ],
            )
            self.assertEqual(result["state_comparison_scope"], "all_user_tables")
            self.assertTrue(result["target_state_correct"])
            self.assertFalse(result["strict_full_state_correct"])
            self.assertTrue(result["any_off_target_change"])
            self.assertEqual(result["off_target_mismatched_tables"], ["child"])
        finally:
            conn.close()

    def test_all_table_scope_handles_quoted_off_target_identifiers(self):
        quoted_names = ('"order details"', "[order details]", "`order details`")
        for index, quoted_name in enumerate(quoted_names, start=1):
            with self.subTest(quoted_name=quoted_name):
                conn = create_database()
                try:
                    sample = {
                        "id": f"s-quoted-{index}",
                        "db_id": "test",
                        "gold_tables": ["parent"],
                        "gold_sql": [
                            "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');"
                        ],
                    }
                    result = evaluate_candidate_sample(
                        sample,
                        conn,
                        direct_sql=[
                            "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');",
                            f"INSERT INTO {quoted_name}(id, note) "
                            f"VALUES ({index}, 'extra');",
                        ],
                    )
                    self.assertTrue(result["target_state_correct"])
                    self.assertFalse(result["strict_full_state_correct"])
                    self.assertEqual(
                        result["off_target_mismatched_tables"],
                        ["order details"],
                    )
                finally:
                    conn.close()

    def test_trigger_generated_expected_state_is_not_off_target(self):
        conn = create_database()
        try:
            conn.executescript(
                """
                CREATE TRIGGER parent_audit AFTER INSERT ON parent
                BEGIN
                  INSERT INTO child(id, parent_id, note)
                  VALUES (NEW.count, NEW.id, 'expected trigger event');
                END;
                """
            )
            sample = {
                "id": "s-trigger",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name, count) "
                    "VALUES ('gold', 'Gold', 102);"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "INSERT INTO parent(id, name, count) "
                    "VALUES ('gold', 'Gold', 102);"
                ],
            )
            self.assertTrue(result["target_state_correct"])
            self.assertTrue(result["strict_full_state_correct"])
            self.assertFalse(result["any_off_target_change"])
        finally:
            conn.close()

    def test_replace_foreign_key_cascade_is_detected_off_target(self):
        conn = create_database()
        try:
            conn.execute(
                "INSERT INTO child(id, parent_id, note) "
                "VALUES (103, 'existing', 'must remain')"
            )
            conn.commit()
            sample = {
                "id": "s-fk-cascade",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name, count) "
                    "VALUES ('existing', 'New', 1) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name;"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "REPLACE INTO parent(id, name, count) "
                    "VALUES ('existing', 'New', 1);"
                ],
            )
            self.assertTrue(result["target_state_correct"])
            self.assertFalse(result["strict_full_state_correct"])
            self.assertTrue(result["any_off_target_change"])
            self.assertEqual(result["off_target_mismatched_tables"], ["child"])
        finally:
            conn.close()

    def test_comments_whitespace_and_multiple_statements_do_not_hide_write(self):
        conn = create_database()
        try:
            sample = {
                "id": "s-comments-multiple",
                "db_id": "test",
                "gold_tables": ["parent"],
                "gold_sql": [
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');"
                ],
            }
            result = evaluate_candidate_sample(
                sample,
                conn,
                direct_sql=[
                    "  -- target write\n"
                    "INSERT INTO parent(id, name) VALUES ('gold', 'Gold');",
                    "/* unrelated write */\n"
                    "INSERT INTO `order details`(id, note) VALUES (104, 'extra');",
                ],
            )
            self.assertTrue(result["execution_success"])
            self.assertTrue(result["target_state_correct"])
            self.assertFalse(result["strict_full_state_correct"])
            self.assertEqual(
                result["off_target_mismatched_tables"],
                ["order details"],
            )
        finally:
            conn.close()


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.payload = parse_source_payload('[{"code":"001","label":"One"}]')
        self.mapping = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "source_rows": "$[*]",
                    "field_mapping": {"code": "identifier", "label": "name"},
                    "constants": {},
                    "action": "insert",
                    "conflict": {
                        "action": "error",
                        "target": [],
                        "update_columns": [],
                    },
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }

    def test_patch_repairs_mapping_without_rewriting_values(self):
        repaired, materialized, verification = repair_and_validate(
            self.mapping,
            [
                {
                    "op": "replace",
                    "path": "/target_groups/0/field_mapping/code",
                    "value": "id",
                }
            ],
            self.payload,
            test_profile(),
        )
        self.assertTrue(verification.valid)
        self.assertEqual(materialized["write_groups"][0]["rows"][0]["id"], "001")
        self.assertEqual(
            repaired["target_groups"][0]["field_mapping"]["code"],
            "id",
        )

    def test_patch_cannot_modify_source_rows(self):
        with self.assertRaises(PatchError):
            apply_plan_patch(
                self.mapping,
                [
                    {
                        "op": "replace",
                        "path": "/source_rows/0/code",
                        "value": "999",
                    }
                ],
            )

    def test_complete_repair_acceptance_policy(self):
        original = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "source_collection": "collection_1",
                    "table": "parent",
                    "source_rows": "$[*]",
                    "field_mapping": {
                        "code": "identifier",
                        "label": "name",
                    },
                    "constants": {},
                    "action": "insert",
                    "conflict": conflict(),
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }
        repaired = {
            **original,
            "target_groups": [
                {
                    **original["target_groups"][0],
                    "field_mapping": {"code": "id", "label": "name"},
                }
            ],
        }
        conn = create_database()
        try:
            result = evaluate_repair_candidate(
                original,
                repaired,
                self.payload,
                test_profile(),
                conn,
                repair_reason="UNKNOWN_COLUMN verifier error",
            )
        finally:
            conn.close()
        self.assertTrue(result["accepted"], result)
        self.assertTrue(all(result["checks"].values()))

    def test_repair_dry_run_uses_and_closes_snapshot(self):
        original = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "source_collection": "collection_1",
                    "table": "parent",
                    "source_rows": "$[*]",
                    "field_mapping": {
                        "code": "identifier",
                        "label": "name",
                    },
                    "constants": {},
                    "action": "insert",
                    "conflict": conflict(),
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }
        repaired = {
            **original,
            "target_groups": [
                {
                    **original["target_groups"][0],
                    "field_mapping": {"code": "id", "label": "name"},
                }
            ],
        }
        database_path = Path("source.sqlite")
        snapshot = create_database()
        with (
            patch(
                "nldbwrite_v3.repair.patch.snapshot_database",
                return_value=snapshot,
            ) as snapshot_mock,
            patch(
                "nldbwrite_v3.repair.patch.execute_program",
                wraps=execute_program,
            ) as execute_mock,
        ):
            result = evaluate_repair_candidate(
                original,
                repaired,
                self.payload,
                test_profile(),
                database_path,
                repair_reason="UNKNOWN_COLUMN verifier error",
            )

        self.assertTrue(result["accepted"], result)
        snapshot_mock.assert_called_once_with(database_path)
        self.assertIs(execute_mock.call_args.args[0], snapshot)
        with self.assertRaises(sqlite3.ProgrammingError):
            snapshot.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
