from __future__ import annotations

import unittest

from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import ground_mapping_plan
from nldbwrite_v3.source_parser import parse_source_payload
from tests.helpers import test_profile


def mapping_plan(
    *,
    table: str,
    field_mapping: dict[str, str],
    conflict_action: str = "error",
) -> dict:
    return {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection": "collection_1",
                "table": table,
                "source_rows": "$[*]",
                "field_mapping": field_mapping,
                "constants": {},
                "action": "insert",
                "conflict": {
                    "action": conflict_action,
                    "target": [],
                    "update_columns": [],
                },
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }


class GroundingTests(unittest.TestCase):
    def test_dominant_exact_identifiers_ground_table_and_columns(self):
        request = (
            "Add the listed records.\n"
            '[{"id":"001","name":"One","count":3}]'
        )
        predicted = mapping_plan(
            table="child",
            field_mapping={
                "id": "id",
                "name": "note",
                "count": "parent_id",
            },
        )
        result = MappingFirstPipeline(test_profile()).run(
            request,
            predicted,
        )
        self.assertTrue(result.success, result.to_dict())
        group = result.write_plan["write_groups"][0]
        self.assertEqual(group["table"], "parent")
        self.assertEqual(
            group["rows"],
            [{"id": "001", "name": "One", "count": 3}],
        )
        warning_codes = {
            item.error_code for item in result.verification.warnings
        }
        self.assertIn("GROUNDED_TARGET_TABLE", warning_codes)
        self.assertIn("GROUNDED_TARGET_COLUMN", warning_codes)

    def test_uniform_table_metadata_is_hint_not_payload(self):
        request = (
            "Add the listed record.\n"
            '[{"table":"parent","id":"001","name":"One"}]'
        )
        predicted = mapping_plan(
            table="child",
            field_mapping={
                "table": "parent_id",
                "id": "id",
                "name": "note",
            },
        )
        result = MappingFirstPipeline(test_profile()).run(
            request,
            predicted,
        )
        self.assertTrue(result.success, result.to_dict())
        group = result.write_plan["write_groups"][0]
        self.assertEqual(group["table"], "parent")
        self.assertEqual(group["rows"], [{"id": "001", "name": "One"}])
        table_status = [
            item
            for item in result.write_plan["unresolved_fields"]
            if item["field"] == "table"
        ]
        self.assertEqual(table_status[0]["status"], "ignored")

    def test_conflict_postamble_grounds_do_update_mask(self):
        request = (
            "Please add these records:\n"
            "1. id: 001, name: One\n"
            "2. id: 002, name: Two\n\n"
            "Update existing IDs with the supplied values."
        )
        predicted = mapping_plan(
            table="parent",
            field_mapping={"id": "id", "name": "name"},
        )
        predicted["target_groups"][0]["source_collection"] = "section_1"
        result = MappingFirstPipeline(test_profile()).run(
            request,
            predicted,
        )
        self.assertTrue(result.success, result.to_dict())
        policy = result.write_plan["write_groups"][0]["conflict"]
        self.assertEqual(
            policy,
            {
                "action": "do_update",
                "target": ["id"],
                "update_columns": ["name"],
            },
        )
        self.assertIn(
            "GROUNDED_CONFLICT_POLICY",
            {
                item.error_code
                for item in result.verification.warnings
            },
        )

    def test_ambiguous_low_coverage_does_not_override_table(self):
        payload = parse_source_payload(
            'Add it.\n[{"id":"001","mystery":"x"}]'
        )
        predicted = mapping_plan(
            table="pair",
            field_mapping={"id": "a", "mystery": "value"},
        )
        grounded, diagnostics = ground_mapping_plan(
            predicted,
            payload,
            test_profile(),
        )
        self.assertEqual(
            grounded["target_groups"][0]["table"],
            "pair",
        )
        self.assertNotIn(
            "GROUNDED_TARGET_TABLE",
            {item.error_code for item in diagnostics},
        )

    def test_positional_table_labels_complete_omitted_collection(self):
        request = (
            "Please import the following table data:\n\n"
            "Table: parent\n\n"
            "Table: child\n\n"
            "| id | name |\n"
            "| --- | --- |\n"
            "| p1 | One |\n\n"
            "| id | parent_id | note |\n"
            "| --- | --- | --- |\n"
            "| 1 | p1 | N |"
        )
        payload = parse_source_payload(request)
        self.assertEqual(len(payload.collections), 2)
        child = payload.collections[1]
        predicted = {
            "target_groups": [
                {
                    "group_id": "child_group",
                    "source_collection": child.collection_id,
                    "table": "child",
                    "source_rows": child.source_path,
                    "field_mapping": {
                        "id": "id",
                        "parent_id": "parent_id",
                        "note": "note",
                    },
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
        grounded, diagnostics = ground_mapping_plan(
            predicted,
            payload,
            test_profile(),
        )
        self.assertEqual(
            {group["table"] for group in grounded["target_groups"]},
            {"parent", "child"},
        )
        self.assertIn(
            "COMPLETED_EXACT_SOURCE_COLLECTION",
            {item.error_code for item in diagnostics},
        )
        parent_group = next(
            group
            for group in grounded["target_groups"]
            if group["table"] == "parent"
        )
        self.assertIn(
            {
                "before": parent_group["group_id"],
                "after": "child_group",
                "foreign_key": {
                    "from_column": "parent_id",
                    "to_table": "parent",
                    "to_column": "id",
                },
            },
            grounded["dependencies"],
        )

    def test_key_only_parent_in_multicollection_becomes_idempotent(self):
        request = (
            "Please add the following database records:\n"
            '{"parent":[{"id":"p1"}],'
            '"child":[{"id":1,"parent_id":"p1","note":"N"}]}'
        )
        payload = parse_source_payload(request)
        parent = payload.collections[0]
        predicted = {
            "target_groups": [
                {
                    "group_id": "parent_group",
                    "source_collection": parent.collection_id,
                    "table": "parent",
                    "source_rows": parent.source_path,
                    "field_mapping": {"id": "id"},
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
        grounded, diagnostics = ground_mapping_plan(
            predicted,
            payload,
            test_profile(),
        )
        parent_policy = next(
            group["conflict"]
            for group in grounded["target_groups"]
            if group["table"] == "parent"
        )
        self.assertEqual(
            parent_policy,
            {
                "action": "do_nothing",
                "target": ["id"],
                "update_columns": [],
            },
        )
        self.assertIn(
            "GROUNDED_CONFLICT_POLICY",
            {item.error_code for item in diagnostics},
        )

    def test_shared_collection_is_not_forced_to_one_dominant_table(self):
        payload = parse_source_payload(
            '[{"id":"p1","name":"One","count":3}]'
        )
        predicted = mapping_plan(
            table="parent",
            field_mapping={"id": "id", "name": "name"},
        )
        second = mapping_plan(
            table="child",
            field_mapping={"id": "id", "name": "note"},
        )["target_groups"][0]
        second["group_id"] = "g2"
        predicted["target_groups"].append(second)
        grounded, diagnostics = ground_mapping_plan(
            predicted,
            payload,
            test_profile(),
        )
        self.assertEqual(
            [group["table"] for group in grounded["target_groups"]],
            ["parent", "child"],
        )
        self.assertNotIn(
            "GROUNDED_TARGET_TABLE",
            {item.error_code for item in diagnostics},
        )


if __name__ == "__main__":
    unittest.main()
