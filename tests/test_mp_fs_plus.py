from __future__ import annotations

import json
import sqlite3
import unittest

from nldbwrite_v3.compiler import (
    apply_declared_normalization,
    check_semantic_risk_gate,
    compile_write_plan,
    preflight_program,
)
from nldbwrite_v3.data import audit_external_holdout_metadata
from nldbwrite_v3.experiments.run_method import (
    _load_method_config,
    _prompt_for_sample,
)
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import (
    extract_evidence_candidates,
    parse_llm_plan,
)
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.source_parser import parse_source_payload
from tests.helpers import group, plan, test_profile


class ReferencePlanningTests(unittest.TestCase):
    def setUp(self):
        self.profile = ensure_reference_ids(test_profile())

    def test_source_and_schema_ids_are_stable_and_enumerated(self):
        payload = parse_source_payload(
            '[{"code":"001","label":"One"}]'
        )
        self.assertEqual(payload.collections[0].reference_id, "c1")
        self.assertEqual(payload.collections[0].selector_id, "s1")
        self.assertEqual(
            payload.collections[0].field_ids,
            {"code": "c1.f1", "label": "c1.f2"},
        )
        parent = next(
            table
            for table in self.profile["tables"]
            if table["name"] == "parent"
        )
        self.assertEqual(parent["table_id"], "t3")
        self.assertEqual(
            {column["name"]: column["column_id"] for column in parent["columns"]},
            {"id": "t3.c2", "name": "t3.c3", "count": "t3.c1"},
        )
        self.assertEqual(
            parent["unique_indexes"][0]["constraint_id"],
            "t3.u1",
        )

    def test_reference_mapping_plan_compiles_without_free_identifiers(self):
        request = '[{"code":"001","label":"One"}]'
        predicted = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "source_collection_id": "c1",
                    "source_selector_id": "s1",
                    "table_id": "t3",
                    "field_mapping": {
                        "c1.f1": "t3.c2",
                        "c1.f2": "t3.c3",
                    },
                    "constants": {},
                    "write_semantics": "insert_ignore",
                    "conflict_target_id": "t3.u1",
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())
        self.assertIn(
            'ON CONFLICT ("id") DO NOTHING',
            result.program.statements[0].sql,
        )
        self.assertEqual(result.program.statements[0].params, ["001", "One"])

    def test_unknown_reference_abstains_before_materialization(self):
        predicted = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "source_collection_id": "c1",
                    "source_selector_id": "s999",
                    "table_id": "t3",
                    "field_mapping": {"c1.f1": "t3.c2"},
                    "constants": {},
                    "write_semantics": "plain_insert",
                    "conflict_target_id": None,
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
        ).run('[{"code":"001"}]', predicted)
        self.assertEqual(result.stage, "reference_resolution")
        self.assertIn(
            "UNKNOWN_SOURCE_SELECTOR_ID",
            {error.error_code for error in result.verification.errors},
        )

    def test_exact_reference_grounding_completes_omitted_group_and_policy(self):
        request = (
            "Upsert the existing parent row using id as the conflict key. "
            "On conflict, update only name.\n"
            "| id | name |\n"
            "| --- | --- |\n"
            "| p1 | One |"
        )
        predicted = {
            "target_groups": [],
            "dependencies": [],
            "ignored_fields": {},
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(
            result.write_plan["write_groups"][0]["conflict"],
            {
                "action": "do_update",
                "target": ["id"],
                "update_columns": ["name"],
            },
        )
        self.assertIn(
            "COMPLETED_EXACT_REFERENCE_COLLECTION",
            {
                warning.error_code
                for warning in result.verification.warnings
            },
        )

    def test_free_text_evidence_ids_materialize_verbatim(self):
        request = "Add parent P001 named Alpha."
        candidates = extract_evidence_candidates(request)
        by_text = {candidate["text"]: candidate["evidence_id"] for candidate in candidates}
        predicted = {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table_id": "t3",
                    "rows": [
                        {
                            "t3.c2": {
                                "value_from": by_text["P001"],
                                "normalization": "identity",
                            },
                            "t3.c3": {
                                "value_from": by_text["Alpha"],
                                "normalization": "identity",
                            },
                        }
                    ],
                    "write_semantics": "plain_insert",
                    "conflict_target_id": None,
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(result.program.statements[0].params, ["P001", "Alpha"])
        evidence = result.write_plan["write_groups"][0]["value_evidence"][0]
        self.assertEqual(evidence["id"]["exact_span"], "P001")

    def test_fenced_control_prelude_overrides_extra_update_columns(self):
        request = (
            "Upsert the existing parent row.\n\n"
            "parent_conflict_key=id\n"
            "parent_update_columns=name\n"
            "```csv\n"
            "id,name,count\n"
            "P001,Alpha,7\n"
            "```"
        )
        payload = parse_source_payload(request)
        self.assertEqual(
            payload.collections[0].metadata["control_metadata"],
            [
                {
                    "parent_conflict_key": "id",
                    "parent_update_columns": "name",
                }
            ],
        )
        predicted = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "source_collection_id": "c1",
                    "source_selector_id": "s1",
                    "table_id": "t3",
                    "field_mapping": {
                        "c1.f1": "t3.c2",
                        "c1.f2": "t3.c3",
                        "c1.f3": "t3.c1",
                    },
                    "constants": {},
                    "write_semantics": "upsert_update",
                    "conflict_target_id": "t3.u1",
                    "update_column_ids": ["t3.c1", "t3.c3"],
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(
            result.write_plan["write_groups"][0]["conflict"],
            {
                "action": "do_update",
                "target": ["id"],
                "update_columns": ["name"],
            },
        )

    def test_free_text_exact_identifier_repairs_column_and_policy_id(self):
        request = (
            "Upsert parent P001 using id as the conflict key; "
            "update only name to Alpha."
        )
        candidates = extract_evidence_candidates(request)
        by_text = {
            candidate["text"]: candidate["evidence_id"]
            for candidate in candidates
        }
        predicted = {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table_id": "t3",
                    "rows": [
                        {
                            "t3.c2": {
                                "value_from": by_text["P001"],
                                "normalization": "identity",
                            },
                            # Deliberately wrong: count instead of exact name.
                            "t3.c1": {
                                "value_from": by_text["Alpha"],
                                "normalization": "identity",
                            },
                        }
                    ],
                    "write_semantics": "upsert_update",
                    "conflict_target_id": "t3.u1",
                    "update_column_ids": ["t3.c1"],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())
        group = result.write_plan["write_groups"][0]
        self.assertEqual(group["rows"], [{"id": "P001", "name": "Alpha"}])
        self.assertEqual(group["conflict"]["update_columns"], ["name"])
        self.assertEqual(
            group["reference_trace"]["evidence_column_groundings"],
            [
                {
                    "row_index": 0,
                    "evidence_id": by_text["Alpha"],
                    "from_column_id": "t3.c1",
                    "to_column_id": "t3.c3",
                    "to_column": "name",
                    "reason": "immediately_preceding_exact_identifier",
                }
            ],
        )

    def test_free_text_exact_identifier_from_other_table_abstains(self):
        request = "Add parent P001 with note to Alpha."
        candidates = extract_evidence_candidates(request)
        by_text = {
            candidate["text"]: candidate["evidence_id"]
            for candidate in candidates
        }
        predicted = {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table_id": "t3",
                    "rows": [
                        {
                            "t3.c2": {
                                "value_from": by_text["P001"],
                                "normalization": "identity",
                            },
                            "t3.c3": {
                                "value_from": by_text["Alpha"],
                                "normalization": "identity",
                            },
                        }
                    ],
                    "write_semantics": "plain_insert",
                    "conflict_target_id": None,
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())
        self.assertIn(
            "EVIDENCE_COLUMN_TABLE_MISMATCH",
            {
                warning.error_code
                for warning in result.program.warnings
            },
        )
        semantic_gate = check_semantic_risk_gate(result.program)
        self.assertFalse(semantic_gate["accepted"])
        self.assertEqual(
            semantic_gate["error_class"],
            "semantic_grounding_risk",
        )
        self.assertIn(
            "EVIDENCE_COLUMN_TABLE_MISMATCH",
            semantic_gate["error_codes"],
        )

    def test_free_text_evidence_enumerates_boolean_literals(self):
        candidates = extract_evidence_candidates(
            "Add expense E001 with approved true and archived false."
        )
        by_text = {
            candidate["text"].casefold(): candidate
            for candidate in candidates
        }
        self.assertEqual(by_text["true"]["candidate_type"], "literal")
        self.assertEqual(by_text["false"]["candidate_type"], "literal")

    def test_assignment_evidence_prefers_complete_value_without_separator(self):
        candidates = extract_evidence_candidates(
            "Set id to 990001 and name to Smoke Validation Country. "
            "Set Segment to Technical smoke."
        )
        texts = [candidate["text"] for candidate in candidates]
        self.assertIn("990001", texts)
        self.assertIn("Smoke Validation Country", texts)
        self.assertIn("Technical smoke", texts)
        self.assertNotIn("to Smoke Validation Country", texts)
        self.assertLess(
            texts.index("Smoke Validation Country"),
            texts.index("Smoke"),
        )
        self.assertLess(
            texts.index("Technical smoke"),
            texts.index("Technical"),
        )

    def test_identifier_label_stops_before_named_value(self):
        candidates = extract_evidence_candidates(
            "Add a student with id S001 named Nguyen Van A."
        )
        texts = [candidate["text"] for candidate in candidates]
        self.assertIn("S001", texts)
        self.assertIn("Nguyen Van A", texts)
        self.assertNotIn("S001 named Nguyen Van A", texts)

    def test_reference_prompt_explains_quote_and_conflict_precedence(self):
        config, _ = _load_method_config(
            "configs/final/mp_fs_plus.json"
        )
        prompt, payload = _prompt_for_sample(
            "MP-FS+",
            {
                "input_text": (
                    'Use a plain insert for parent P001 named "Alpha"; '
                    "duplicates must fail."
                )
            },
            self.profile,
            config,
        )
        self.assertEqual(payload.mode, "free_text")
        self.assertIn(
            "quoted_text candidates already exclude their surrounding quotes",
            prompt,
        )
        self.assertIn("fail/error means plain_insert", prompt)
        self.assertIn("literal evidence true/false", prompt)

    def test_ambiguous_duplicate_policy_abstains_deterministically(self):
        predicted = {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table_id": "t3",
                    "rows": [],
                    "write_semantics": "insert_ignore",
                    "conflict_target_id": "t3.u1",
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        requests = [
            (
                "Add an expense with expense id recA1b2C3d4E5f6G7H, "
                "expense date 2019-12-01, and approved true. If that "
                "expense ID already exists, handle the conflict "
                "appropriately."
            ),
            (
                "Please register expense recB2c3D4e5F6g7H8I dated "
                "2019-12-05 with approved false. When a duplicate is "
                "found, use whichever duplicate policy is suitable."
            ),
        ]
        for request in requests:
            with self.subTest(request=request):
                result = MappingFirstPipeline(
                    self.profile,
                    reference_planning=True,
                    normalization_mode="lossless",
                ).run(request, predicted)
                self.assertEqual(result.stage, "policy_resolution")
                self.assertEqual(
                    [
                        error.error_code
                        for error in result.verification.errors
                    ],
                    ["NEEDS_CLARIFICATION"],
                )

    def test_explicit_duplicate_policy_is_not_marked_ambiguous(self):
        request = (
            "Add parent P001 named Alpha. If that ID already exists, "
            "ignore the duplicate and do not update it."
        )
        candidates = extract_evidence_candidates(request)
        by_text = {
            candidate["text"]: candidate["evidence_id"]
            for candidate in candidates
        }
        predicted = {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table_id": "t3",
                    "rows": [
                        {
                            "t3.c2": {
                                "value_from": by_text["P001"],
                                "normalization": "identity",
                            },
                            "t3.c3": {
                                "value_from": by_text["Alpha"],
                                "normalization": "identity",
                            },
                        }
                    ],
                    "write_semantics": "insert_ignore",
                    "conflict_target_id": "t3.u1",
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(
            self.profile,
            reference_planning=True,
            normalization_mode="lossless",
        ).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())

    def test_reference_schema_parser_rejects_legacy_surface(self):
        legacy = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "source_collection": "rows",
                    "source_rows": "$[*]",
                    "table": "parent",
                    "field_mapping": {"code": "id"},
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
        parsed = parse_llm_plan(
            json.dumps(legacy),
            plan_kind="mapping",
            reference_mode=True,
        )
        self.assertEqual(parsed.parse_status, "schema_error")

    def test_matched_bank_resolves_four_plus_two_for_mp_fs_plus(self):
        config, _ = _load_method_config(
            "configs/final/mp_fs_plus.json"
        )
        self.assertEqual(
            len(config["demonstrations"]["semi_structured"]),
            4,
        )
        self.assertEqual(
            len(config["demonstrations"]["free_text"]),
            2,
        )
        prompt, payload = _prompt_for_sample(
            "MP-FS+",
            {
                "input_text": (
                    "Add the listed rows.\n"
                    '[{"code":"SECRET-001","label":"Private"}]'
                )
            },
            self.profile,
            config,
        )
        self.assertEqual(payload.mode, "semi_structured")
        self.assertIn("source_selector_id", prompt)
        self.assertIn("table_id", prompt)
        self.assertNotIn("SECRET-001", prompt)
        self.assertNotIn('"source_rows"', prompt)


class LosslessAndPreflightTests(unittest.TestCase):
    def test_lossless_normalization_rejects_leading_zero_conversion(self):
        integer_column = {
            "name": "count",
            "type": "INTEGER",
            "semantic_type": "measure",
        }
        normalized, audit, error = apply_declared_normalization(
            "00123",
            integer_column,
            "lossless_integer_parsing",
        )
        self.assertEqual(normalized, "00123")
        self.assertFalse(audit["lossless"])
        self.assertIn("leading zeros", error)

    def test_thousands_separator_is_declared_and_audited(self):
        normalized, audit, error = apply_declared_normalization(
            "1,200",
            {"name": "count", "type": "INTEGER", "semantic_type": "measure"},
            "remove_thousands_separator",
        )
        self.assertIsNone(error)
        self.assertEqual(normalized, 1200)
        self.assertTrue(audit["applied"])
        self.assertEqual(audit["sqlite_storage_class"], "integer")

    def test_transactional_preflight_abstains_without_mutating_database(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO parent(id, name) VALUES ('existing', 'Old')"
            )
            candidate = plan(
                [
                    group(
                        "g1",
                        "parent",
                        [{"id": "existing", "name": "New"}],
                    )
                ]
            )
            program = compile_write_plan(candidate, test_profile())
            preflight = preflight_program(conn, program)
            self.assertFalse(preflight["accepted"])
            self.assertEqual(preflight["error_class"], "unique_violation")
            self.assertEqual(
                conn.execute(
                    "SELECT name FROM parent WHERE id='existing'"
                ).fetchone()[0],
                "Old",
            )
        finally:
            conn.close()


class HoldoutAuditTests(unittest.TestCase):
    def test_hidden_policy_and_augmentation_are_blocking(self):
        issues, summary = audit_external_holdout_metadata(
            [
                {
                    "id": "draft_1",
                    "db_id": "new_db",
                    "input_text": "Add this record.",
                    "input_format": "free_text",
                    "complexity": "single_row",
                    "operation_semantics": "insert_ignore",
                    "semantics_explicit_in_request": False,
                    "semantics_source": "hidden_gold",
                    "state_changing": True,
                    "conflict_sensitive": True,
                    "multi_table": False,
                    "conflict_target": ["id"],
                    "update_columns": [],
                    "gold_sql": ["INSERT OR IGNORE INTO t(id) VALUES (1);"],
                    "gold_plan": {},
                    "source_group": "draft_1",
                    "independently_authored": False,
                }
            ],
            strict_final=False,
        )
        codes = {issue["error_code"] for issue in issues}
        self.assertIn("HIDDEN_CONFLICT_POLICY", codes)
        self.assertIn("NOT_INDEPENDENTLY_AUTHORED", codes)
        self.assertEqual(summary["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
