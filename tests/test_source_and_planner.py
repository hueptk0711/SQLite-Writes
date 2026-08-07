from __future__ import annotations

import unittest

from nldbwrite_v3.planner import materialize_mapping_plan
from nldbwrite_v3.source_parser import parse_source_payload
from nldbwrite_v3.verifier import verify_write_plan
from tests.helpers import test_profile


class SourceParserTests(unittest.TestCase):
    def test_json_array_preserves_rows_and_leading_zero(self):
        payload = parse_source_payload(
            'Add these rows:\n```json\n[{"code":"00123","label":"A"},'
            '{"code":"00456","label":"B"}]\n```'
        )
        self.assertEqual(payload.source_format, "json_array")
        self.assertEqual(len(payload.rows), 2)
        self.assertEqual(payload.rows[0]["code"], "00123")

    def test_markdown_table(self):
        payload = parse_source_payload(
            "Add rows:\n| code | label |\n| --- | --- |\n| 001 | A |\n| 002 | B |"
        )
        self.assertEqual(payload.source_format, "markdown_table")
        self.assertEqual(payload.rows[1], {"code": "002", "label": "B"})

    def test_textual_null_markers_become_null_values(self):
        payload = parse_source_payload(
            "Add rows:\n"
            "| code | note |\n"
            "| --- | --- |\n"
            "| 001 | NULL |\n"
            "| 002 | none |"
        )
        self.assertIsNone(payload.rows[0]["note"])
        self.assertIsNone(payload.rows[1]["note"])

    def test_python_style_list_of_dicts(self):
        payload = parse_source_payload(
            "Insert these records:\n"
            "[{'code': '001', 'name': 'Alpha'},\n"
            " {'code': '002', 'name': 'Beta'}]"
        )
        self.assertEqual(payload.mode, "semi_structured")
        self.assertEqual(payload.source_format, "json_array")
        self.assertEqual(len(payload.rows), 2)
        self.assertEqual(payload.rows[0]["code"], "001")
        self.assertNotIn("Alpha", payload.instruction_text)

    def test_numbered_key_value_records(self):
        payload = parse_source_payload(
            "Please add: 1. code: A, label: Alpha\n"
            "2. code: B, label: Beta\n"
            "3. code: C, label: Gamma"
        )
        self.assertEqual(payload.mode, "semi_structured")
        self.assertEqual(payload.source_format, "key_value")
        self.assertEqual(len(payload.rows), 3)
        self.assertEqual(payload.rows[2]["label"], "Gamma")

    def test_numbered_records_preserve_blank_line_postamble(self):
        payload = parse_source_payload(
            "Please add these records:\n"
            "1. Id: 1, Name: Alpha\n"
            "2. Id: 2, Name: Beta\n\n"
            "Update an existing ID with the supplied values."
        )
        self.assertEqual(payload.rows[-1]["Name"], "Beta")
        self.assertIn(
            "Update an existing ID",
            payload.instruction_text,
        )

    def test_numbered_records_recover_trailing_bare_scalar_field(self):
        payload = parse_source_payload(
            "Please add these records:\n"
            "1. Id: 6, ViewCount: 300, Score 15\n"
            "2. Id: 7, ViewCount: 1500, Score 34"
        )
        self.assertEqual(
            payload.rows[0],
            {"Id": "6", "ViewCount": "300", "Score": "15"},
        )
        self.assertEqual(payload.rows[1]["Score"], "34")

    def test_numbered_relational_records_become_multiple_collections(self):
        payload = parse_source_payload(
            "Please add:\n"
            "1. customers: id=1, name=Alpha\n"
            "2. customers: id=2, name=Beta\n"
            "3. orders: order_id=10, customer_id=1"
        )
        self.assertEqual(payload.source_format, "multi_table")
        self.assertEqual(len(payload.collections), 2)
        self.assertEqual(payload.collections[0].collection_id, "customers")
        self.assertEqual(len(payload.collections[0].rows), 2)
        self.assertEqual(payload.collections[1].collection_id, "orders")

    def test_inline_bulleted_key_value_records(self):
        payload = parse_source_payload(
            "Add these:\n- id: 1, name: Alpha; "
            "- id: 2, name: Beta; "
            "- id: 3, name: Gamma"
        )
        self.assertEqual(payload.source_format, "key_value")
        self.assertEqual(len(payload.rows), 3)
        self.assertEqual(payload.rows[1]["name"], "Beta")

    def test_tolerant_csv_preserves_unquoted_comma_in_name(self):
        payload = parse_source_payload(
            "Add these:\n"
            "id,name,score\n"
            "1,Teferi, Time Raveler,9\n"
            "2,Serra Angel,8"
        )
        self.assertEqual(payload.source_format, "csv")
        self.assertEqual(payload.rows[0]["name"], "Teferi, Time Raveler")
        self.assertEqual(payload.rows[0]["score"], "9")

    def test_equals_records_are_grouped_and_control_lines_stay_metadata(self):
        payload = parse_source_payload(
            "operation=insert_ignore\n"
            "target_table=parent\n"
            "parent.1.id=p1\n"
            "parent.1.name=One\n\n"
            "parent.2.id=p2\n"
            "parent.2.name=Two"
        )
        self.assertEqual(payload.mode, "semi_structured")
        self.assertEqual(len(payload.collections), 1)
        self.assertEqual(
            payload.collections[0].rows,
            [
                {"id": "p1", "name": "One"},
                {"id": "p2", "name": "Two"},
            ],
        )
        self.assertNotIn("operation", payload.collections[0].fields)
        self.assertEqual(
            payload.collections[0].metadata["control_metadata"][0][
                "operation"
            ],
            "insert_ignore",
        )

    def test_json_table_discriminator_splits_heterogeneous_rows(self):
        payload = parse_source_payload(
            '{"rows":['
            '{"table":"parent","id":"p1","name":"One"},'
            '{"table":"child","id":1,"parent_id":"p1","note":"N"}'
            "]}"
        )
        self.assertEqual(
            [collection.collection_id for collection in payload.collections],
            ["parent", "child"],
        )
        self.assertEqual(payload.collections[0].rows[0]["id"], "p1")
        self.assertEqual(payload.collections[1].rows[0]["parent_id"], "p1")

    def test_json_record_wrapper_preserves_controls_outside_data_fields(self):
        payload = parse_source_payload(
            '{"operation":"upsert","table":"parent",'
            '"record":{"id":"p1","name":"One"},'
            '"on_conflict":{"action":"update","columns":["name"]}}'
        )
        collection = payload.collections[0]
        self.assertEqual(collection.fields, ["id", "name", "table"])
        self.assertNotIn("operation", collection.rows[0])
        self.assertEqual(
            collection.metadata["control_metadata"][0]["operation"],
            "upsert",
        )

    def test_decimal_fragments_do_not_start_numbered_record_detection(self):
        payload = parse_source_payload(
            "Update record 10: score=30.1. "
            "Record 11: score=4.2. "
            "Record 12: score=7.1."
        )
        self.assertEqual(payload.mode, "free_text")

    def test_free_text_fallback(self):
        payload = parse_source_payload("Add a customer called Minh.")
        self.assertEqual(payload.mode, "free_text")
        self.assertEqual(payload.rows, [])


class MaterializationTests(unittest.TestCase):
    def test_mapping_is_applied_once_to_every_row(self):
        payload = parse_source_payload(
            '[{"code":"001","label":"One"},{"code":"002","label":"Two"}]'
        )
        mapping = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "source_rows": "$[*]",
                    "field_mapping": {"code": "id", "label": "name"},
                    "constants": {},
                    "action": "insert",
                    "conflict": {
                        "action": "do_nothing",
                        "target": ["id"],
                        "update_columns": [],
                    },
                }
            ],
            "dependencies": [],
            "ignored_fields": {},
        }
        write_plan = materialize_mapping_plan(mapping, payload)
        self.assertEqual(
            write_plan["write_groups"][0]["rows"],
            [{"id": "001", "name": "One"}, {"id": "002", "name": "Two"}],
        )
        self.assertTrue(verify_write_plan(write_plan, test_profile()).valid)

    def test_provenance_tracks_case_normalized_target_columns(self):
        payload = parse_source_payload(
            '[{"code":"001","label":"One"}]'
        )
        mapping = {
            "target_groups": [
                {
                    "group_id": "g1",
                    "table": "parent",
                    "source_rows": "$[*]",
                    "field_mapping": {"code": "ID", "label": "NAME"},
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
        result = verify_write_plan(
            materialize_mapping_plan(mapping, payload),
            test_profile(),
        )
        self.assertTrue(result.valid, result.to_dict())
        provenance = result.normalized_plan[
            "write_groups"
        ][0]["provenance"][0]["value_sources"]
        self.assertEqual(set(provenance), {"id", "name"})

    def test_unmapped_field_requires_reason(self):
        payload = parse_source_payload(
            '[{"code":"001","label":"One","unused":"x"}]'
        )
        mapping = {
            "target_groups": [
                {
                    "table": "parent",
                    "field_mapping": {"code": "id", "label": "name"},
                    "constants": {},
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
        result = verify_write_plan(
            materialize_mapping_plan(mapping, payload),
            test_profile(),
        )
        self.assertIn(
            "UNRESOLVED_SOURCE_FIELD",
            {error.error_code for error in result.errors},
        )

    def test_unjustified_constant_is_rejected(self):
        payload = parse_source_payload('[{"code":"001"}]')
        mapping = {
            "target_groups": [
                {
                    "table": "parent",
                    "field_mapping": {"code": "id"},
                    "constants": {"name": "invented"},
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
        result = verify_write_plan(
            materialize_mapping_plan(mapping, payload),
            test_profile(),
        )
        self.assertIn(
            "UNSUPPORTED_CONSTANT",
            {error.error_code for error in result.errors},
        )

    def test_multiple_json_collections_are_all_materialized(self):
        payload = parse_source_payload(
            "Add these records:\n"
            '{"parents":[{"code":"001","label":"One"}],'
            '"children":[{"parent":"001","note":"N"}]}'
        )
        self.assertEqual(
            [collection.collection_id for collection in payload.collections],
            ["parents", "children"],
        )
        mapping = {
            "target_groups": [
                {
                    "group_id": "parents",
                    "source_collection": "parents",
                    "table": "parent",
                    "source_rows": "$.parents[*]",
                    "field_mapping": {"code": "id", "label": "name"},
                    "constants": {},
                    "action": "insert",
                    "conflict": {
                        "action": "error",
                        "target": [],
                        "update_columns": [],
                    },
                },
                {
                    "group_id": "children",
                    "source_collection": "children",
                    "table": "child",
                    "source_rows": "$.children[*]",
                    "field_mapping": {
                        "parent": "parent_id",
                        "note": "note",
                    },
                    "constants": {},
                    "action": "insert",
                    "conflict": {
                        "action": "error",
                        "target": [],
                        "update_columns": [],
                    },
                },
            ],
            "dependencies": [{"before": "parents", "after": "children"}],
            "ignored_fields": {},
        }
        result = verify_write_plan(
            materialize_mapping_plan(mapping, payload),
            test_profile(),
        )
        self.assertTrue(result.valid, result.to_dict())


if __name__ == "__main__":
    unittest.main()
