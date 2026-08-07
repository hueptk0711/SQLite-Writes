from __future__ import annotations

import unittest
from pathlib import Path

from nldbwrite_v3.experiments.run_lock import verify_or_create_run_lock
from nldbwrite_v3.experiments.run_method import _plan_metrics
from nldbwrite_v3.inference import (
    build_local_model_manifest,
    verify_local_model,
)
from nldbwrite_v3.source_parser import parse_source_payload


class ModelManifestTests(unittest.TestCase):
    def test_local_model_hash_is_computed_and_verified(self):
        root = Path(__file__).parent / "fixtures" / "model_manifest"
        manifest = build_local_model_manifest(root)
        verified = verify_local_model(
            root,
            manifest["aggregate_sha256"],
        )
        self.assertTrue(verified["verified"])
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_local_model(root, "0" * 64)

    def test_resume_lock_rejects_changed_hashes(self):
        path = Path(__file__).parent / "fixtures" / "run_lock.json"
        changed = {
            "hashes": {"dataset_sha256": "b"},
            "run_lock_sha256": "lock-b",
        }
        with self.assertRaisesRegex(ValueError, "dataset_sha256"):
            verify_or_create_run_lock(changed, path, resume=True)


class CrossMethodMetricTests(unittest.TestCase):
    def test_cell_value_metric_does_not_require_provenance(self):
        payload = parse_source_payload("Add product 1 named A.")
        plan = {
            "write_groups": [
                {
                    "table": "products",
                    "rows": [{"id": 1, "name": "A"}],
                    "conflict": {
                        "action": "error",
                        "target": [],
                        "update_columns": [],
                    },
                }
            ]
        }
        metrics = _plan_metrics(
            {
                "gold_tables": ["products"],
                "gold_columns": ["products.id", "products.name"],
                "gold_records": [{"id": 1, "name": "A"}],
            },
            payload,
            plan,
            plan,
        )
        self.assertEqual(metrics["cell_value_f1"], 1.0)
        self.assertTrue(metrics["row_exact_match"])
        self.assertIsNone(metrics["payload_copy_integrity"])

    def test_conflict_accuracy_uses_group_multiplicity(self):
        payload = parse_source_payload("Add products.")
        conflict = {
            "action": "error",
            "target": [],
            "update_columns": [],
        }
        predicted = {
            "write_groups": [
                {
                    "table": "products",
                    "rows": [{"id": 1}, {"id": 2}],
                    "conflict": conflict,
                }
            ]
        }
        gold = {
            "write_groups": [
                {
                    "table": "products",
                    "rows": [{"id": 1}],
                    "conflict": conflict,
                },
                {
                    "table": "products",
                    "rows": [{"id": 2}],
                    "conflict": conflict,
                },
            ]
        }
        metrics = _plan_metrics(
            {
                "gold_tables": ["products"],
                "gold_columns": ["products.id"],
                "gold_records": [{"id": 1}, {"id": 2}],
            },
            payload,
            predicted,
            gold,
        )
        self.assertFalse(metrics["conflict_action_correct"])
        self.assertFalse(metrics["conflict_full_exact"])


if __name__ == "__main__":
    unittest.main()
