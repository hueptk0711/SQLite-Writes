from __future__ import annotations

import json
import unittest

from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import build_planner_prompt, parse_llm_plan
from nldbwrite_v3.source_parser import parse_source_payload
from tests.helpers import conflict, group, test_profile


def valid_mapping_plan() -> dict:
    return {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection": "collection_1",
                "table": "parent",
                "source_rows": "$[*]",
                "field_mapping": {"code": "id", "label": "name"},
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


class DualModeTests(unittest.TestCase):
    def test_mapping_prompt_withholds_source_values(self):
        request = (
            "Add the listed records.\n"
            '[{"code":"SECRET-001","label":"Private value"}]'
        )
        payload = parse_source_payload(request)
        prompt = build_planner_prompt(request, payload, test_profile())
        self.assertIn("code", prompt)
        self.assertIn("label", prompt)
        self.assertNotIn("SECRET-001", prompt)
        self.assertNotIn("Private value", prompt)
        self.assertEqual(payload.instruction_text, "Add the listed records.")

    def test_mapping_prompt_includes_deterministic_table_candidates(self):
        request = (
            "Add the listed records.\n"
            '[{"id":"SECRET-001","name":"Private value"}]'
        )
        payload = parse_source_payload(request)
        prompt = build_planner_prompt(
            request,
            payload,
            test_profile(),
        )
        self.assertIn('"candidate_tables"', prompt)
        self.assertIn('"table": "parent"', prompt)
        self.assertIn('"id": "id"', prompt)
        self.assertIn("Cardinality guard", prompt)
        self.assertIn("Never generate a numbered dependency chain", prompt)
        self.assertNotIn("SECRET-001", prompt)
        self.assertNotIn("Private value", prompt)

    def test_free_text_branch_compiles_with_evidence(self):
        request = "Add a product with ProductID 650 and Description Natural 95."
        predicted = {
            "write_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "action": "insert",
                    "rows": [{"id": "650", "name": "Natural 95"}],
                    "value_evidence": [
                        {
                            "id": {
                                "source": "instruction_text",
                                "exact_span": "ProductID 650",
                            },
                            "name": {
                                "source": "instruction_text",
                                "exact_span": "Description Natural 95",
                            },
                        }
                    ],
                    "conflict": conflict(),
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(test_profile()).run(request, predicted)
        self.assertTrue(result.success, result.to_dict())

    def test_free_text_branch_rejects_invented_value(self):
        request = "Add a product with ProductID 650."
        predicted = {
            "write_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "action": "insert",
                    "rows": [{"id": "650", "name": "Invented"}],
                    "value_evidence": [
                        {
                            "id": {
                                "source": "instruction_text",
                                "exact_span": "ProductID 650",
                            },
                            "name": {
                                "source": "instruction_text",
                                "exact_span": "ProductID 650",
                            },
                        }
                    ],
                    "conflict": conflict(),
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(test_profile()).run(request, predicted)
        self.assertFalse(result.success)
        self.assertIn(
            "UNSUPPORTED_EXTRACTED_VALUE",
            {error.error_code for error in result.verification.errors},
        )

    def test_free_text_null_value_accepts_verbatim_null_evidence(self):
        request = "Add parent id 650 with name One and count NULL."
        predicted = {
            "write_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "action": "insert",
                    "rows": [
                        {"id": "650", "name": "One", "count": None}
                    ],
                    "value_evidence": [
                        {
                            "id": {
                                "source": "instruction_text",
                                "exact_span": "id 650",
                            },
                            "name": {
                                "source": "instruction_text",
                                "exact_span": "name One",
                            },
                            "count": {
                                "source": "instruction_text",
                                "exact_span": "count NULL",
                            },
                        }
                    ],
                    "conflict": conflict(),
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = MappingFirstPipeline(test_profile()).run(
            request,
            predicted,
        )
        self.assertTrue(result.success, result.to_dict())

    def test_free_text_cannot_use_mapping_only_plan(self):
        result = MappingFirstPipeline(test_profile()).run(
            "Add a product called One.",
            valid_mapping_plan(),
        )
        self.assertEqual(result.stage, "materialization")
        self.assertIn(
            "FREE_TEXT_REQUIRES_EXTRACTION_PLAN",
            {error.error_code for error in result.verification.errors},
        )


class PlannerOutputParserTests(unittest.TestCase):
    def test_valid_json(self):
        result = parse_llm_plan(
            json.dumps(valid_mapping_plan()),
            plan_kind="mapping",
        )
        self.assertTrue(result.success, result.to_dict())

    def test_markdown_fence_and_trailing_text(self):
        raw = "Here is the plan:\n```json\n" + json.dumps(valid_mapping_plan()) + "\n```\nDone."
        result = parse_llm_plan(raw, plan_kind="mapping")
        self.assertTrue(result.success, result.to_dict())

    def test_missing_target_groups(self):
        result = parse_llm_plan(
            '{"dependencies":[],"ignored_fields":{}}',
            plan_kind="mapping",
        )
        self.assertEqual(result.parse_status, "schema_error")
        self.assertIn(
            "/target_groups",
            {diagnostic.path for diagnostic in result.diagnostics},
        )

    def test_invalid_conflict_action_has_json_path(self):
        candidate = valid_mapping_plan()
        candidate["target_groups"][0]["conflict"]["action"] = "ignore"
        result = parse_llm_plan(json.dumps(candidate), plan_kind="mapping")
        self.assertEqual(result.parse_status, "schema_error")
        self.assertIn(
            "/target_groups/0/conflict/action",
            {diagnostic.path for diagnostic in result.diagnostics},
        )

    def test_insert_or_update_alias_is_normalized_when_conflict_is_explicit(self):
        candidate = valid_mapping_plan()
        candidate["target_groups"][0]["action"] = "insert_or_update"
        candidate["target_groups"][0]["conflict"] = {
            "action": "do_update",
            "target": ["id"],
            "update_columns": ["name"],
        }
        result = parse_llm_plan(
            json.dumps(candidate),
            plan_kind="mapping",
        )
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(
            result.plan["target_groups"][0]["action"],
            "insert",
        )
        self.assertIn(
            "NORMALIZED_WRITE_ACTION_ALIAS",
            {item.error_code for item in result.diagnostics},
        )

    def test_dependency_aliases_are_normalized(self):
        candidate = valid_mapping_plan()
        second = json.loads(json.dumps(candidate["target_groups"][0]))
        second["group_id"] = "g2"
        candidate["target_groups"].append(second)
        candidate["dependencies"] = [
            {
                "parent_group_id": "g1",
                "child_group_id": "g2",
                "on": "id",
            }
        ]
        result = parse_llm_plan(
            json.dumps(candidate),
            plan_kind="mapping",
        )
        self.assertTrue(result.success, result.to_dict())
        dependency = result.plan["dependencies"][0]
        self.assertEqual(dependency["before"], "g1")
        self.assertEqual(dependency["after"], "g2")
        self.assertEqual(dependency["foreign_key"], {"on": "id"})

    def test_depends_on_alias_and_annotations_are_normalized(self):
        candidate = valid_mapping_plan()
        second = json.loads(json.dumps(candidate["target_groups"][0]))
        second["group_id"] = "g2"
        candidate["target_groups"].append(second)
        candidate["dependencies"] = [
            {
                "group_id": "g2",
                "depends_on_group_id": "g1",
                "order": "after",
                "constraint": "declared foreign key",
            }
        ]
        result = parse_llm_plan(
            json.dumps(candidate),
            plan_kind="mapping",
        )
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(
            result.plan["dependencies"],
            [
                {
                    "before": "g1",
                    "after": "g2",
                    "foreign_key": {
                        "constraint": "declared foreign key"
                    },
                }
            ],
        )

    def test_duplicate_group_ids(self):
        candidate = valid_mapping_plan()
        candidate["target_groups"].append(
            dict(candidate["target_groups"][0])
        )
        result = parse_llm_plan(json.dumps(candidate), plan_kind="mapping")
        self.assertIn(
            "DUPLICATE_GROUP_ID",
            {diagnostic.error_code for diagnostic in result.diagnostics},
        )

    def test_wrong_data_type(self):
        candidate = valid_mapping_plan()
        candidate["target_groups"] = "not-a-list"
        result = parse_llm_plan(json.dumps(candidate), plan_kind="mapping")
        self.assertEqual(result.parse_status, "schema_error")

    def test_free_text_empty_row_fails_min_properties(self):
        plan = {
            "version": "3.0",
            "plan_kind": "free_text_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "action": "insert",
                    "rows": [{}],
                    "conflict": conflict(),
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        }
        result = parse_llm_plan(json.dumps(plan), plan_kind="free_text")
        self.assertEqual(result.parse_status, "schema_error")
        self.assertTrue(
            any(item.path == "/write_groups/0/rows/0" for item in result.diagnostics)
        )

    def test_additional_property(self):
        candidate = valid_mapping_plan()
        candidate["unexpected"] = True
        result = parse_llm_plan(json.dumps(candidate), plan_kind="mapping")
        self.assertEqual(result.parse_status, "schema_error")
        self.assertIn(
            "/unexpected",
            {diagnostic.path for diagnostic in result.diagnostics},
        )

    def test_malformed_json(self):
        result = parse_llm_plan(
            '{"target_groups": [}',
            plan_kind="mapping",
        )
        self.assertEqual(result.parse_status, "json_error")


if __name__ == "__main__":
    unittest.main()
