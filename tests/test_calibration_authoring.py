from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
from collections import Counter
from pathlib import Path

from nldbwrite_v3.data.authoring import (
    assign_calibration_participants,
    assemble_calibration_samples,
    audit_authoring_assets,
    audit_calibration_authoring_completion,
    audit_frozen_allocation,
    audit_review_ledger,
    create_calibration_authoring_kit,
    record_calibration_review,
    start_calibration_revision,
)
from nldbwrite_v3.data.calibration_semantics import (
    audit_calibration_semantics,
)
from nldbwrite_v3.data.gold_sql import parse_gold_sql
from nldbwrite_v3.schema import load_profile


def _create_source_database(root: Path, db_id: str) -> None:
    directory = root / db_id
    directory.mkdir(parents=True)
    database = directory / f"{db_id}_template.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE parent(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL
            );
            CREATE TABLE child(
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id),
                value TEXT
            );
            INSERT INTO parent(id, code, name) VALUES (1, 'A', 'Alpha');
            INSERT INTO child(id, parent_id, value) VALUES (1, 1, 'one');
            """
        )
        connection.commit()
    finally:
        connection.close()
    (directory / f"{db_id}_schema.txt").write_text(
        "schema", encoding="utf-8"
    )
    (directory / f"{db_id}_column_meaning_base.json").write_text(
        "{}", encoding="utf-8"
    )
    (directory / f"{db_id}_kb.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )


def _create_candidate_pool(root: Path) -> None:
    for db_id in ("cal_a", "cal_b", "final_a", "final_b", "final_c"):
        _create_source_database(root, db_id)


class CalibrationAuthoringTests(unittest.TestCase):
    def runtime_root(self) -> Path:
        root = (
            Path(__file__).parent
            / "_runtime_authoring"
            / self._testMethodName
        )
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_kit_has_balanced_draft_and_excludes_final_databases(self):
        root = self.runtime_root()
        source = root / "source"
        _create_candidate_pool(source)
        output = root / "kit"
        manifest = create_calibration_authoring_kit(
            source_root=source,
            output_dir=output,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
            source_url="https://example.invalid/dataset",
            source_license="CC-BY-SA-4.0",
            source_revision="test-revision",
            expected_candidate_count=5,
        )
        samples = json.loads(
            (output / "dataset.draft.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(samples), 60)
        self.assertEqual(
            Counter(row["db_id"] for row in samples),
            {"cal_a": 30, "cal_b": 30},
        )
        self.assertEqual(
            Counter(row["operation_semantics"] for row in samples),
            {
                "plain_insert": 20,
                "insert_ignore": 20,
                "upsert_update": 20,
            },
        )
        self.assertEqual(
            Counter(row["input_mode"] for row in samples),
            {"free_text": 20, "semi_structured": 40},
        )
        self.assertEqual(
            Counter(row["complexity"] for row in samples),
            {
                "single_row": 20,
                "small_batch": 20,
                "large_or_relational": 20,
            },
        )
        self.assertEqual(
            sum(row["multi_table"] is True for row in samples),
            20,
        )
        self.assertEqual(
            Counter(row["workload_shape"] for row in samples),
            {
                "single_row__single_table": 20,
                "small_batch__single_table": 10,
                "small_batch__multi_table": 10,
                "large_or_relational__single_table": 10,
                "large_or_relational__multi_table": 10,
            },
        )
        self.assertFalse(
            any(
                row["operation_semantics"] == "insert_ignore"
                and row["complexity"] == "single_row"
                for row in samples
            )
        )
        self.assertEqual(
            Counter(
                (
                    row["workload_shape"],
                    row["operation_semantics"],
                )
                for row in samples
            ),
            {
                ("single_row__single_table", "plain_insert"): 10,
                ("single_row__single_table", "upsert_update"): 10,
                ("small_batch__single_table", "plain_insert"): 2,
                ("small_batch__single_table", "insert_ignore"): 5,
                ("small_batch__single_table", "upsert_update"): 3,
                ("small_batch__multi_table", "plain_insert"): 2,
                ("small_batch__multi_table", "insert_ignore"): 5,
                ("small_batch__multi_table", "upsert_update"): 3,
                ("large_or_relational__single_table", "plain_insert"): 3,
                ("large_or_relational__single_table", "insert_ignore"): 5,
                ("large_or_relational__single_table", "upsert_update"): 2,
                ("large_or_relational__multi_table", "plain_insert"): 3,
                ("large_or_relational__multi_table", "insert_ignore"): 5,
                ("large_or_relational__multi_table", "upsert_update"): 2,
            },
        )
        self.assertFalse(any(output.rglob("final_a*.sqlite")))
        self.assertFalse(manifest["reserved_final_database_files_included"])
        profile = json.loads(
            (output / "profiles" / "cal_a.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            profile["db_path"], "databases/cal_a/cal_a.sqlite"
        )
        asset_issues, asset_summary = audit_authoring_assets(output)
        self.assertEqual(asset_issues, [])
        self.assertEqual(asset_summary["asset_status"], "valid")

    def test_draft_is_blocked_and_individual_files_assemble(self):
        root = self.runtime_root()
        source = root / "source"
        _create_candidate_pool(source)
        output = root / "kit"
        create_calibration_authoring_kit(
            source_root=source,
            output_dir=output,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
            source_url="https://example.invalid/dataset",
            source_license="CC-BY-SA-4.0",
            expected_candidate_count=5,
        )
        assembled = assemble_calibration_samples(
            samples_dir=output / "samples",
            ids_path=output / "calibration_ids.txt",
            output_path=output / "dataset.json",
        )
        issues, summary = audit_calibration_authoring_completion(
            assembled,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
        )
        codes = {row["error_code"] for row in issues}
        self.assertIn("PLACEHOLDER_AUTHOR_ID", codes)
        self.assertIn("AUTHORING_NOT_APPROVED", codes)
        self.assertIn("EMPTY_GOLD_WRITE_PLAN", codes)
        self.assertEqual(summary["authoring_status"], "draft_or_invalid")
        self.assertFalse(summary["gpu_run_authorized"])
        self.assertFalse(summary["paper_result_eligible"])

    def test_participant_assignment_never_approves_reviews(self):
        root = self.runtime_root()
        source = root / "source"
        _create_candidate_pool(source)
        output = root / "kit"
        create_calibration_authoring_kit(
            source_root=source,
            output_dir=output,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
            source_url="https://example.invalid/dataset",
            source_license="CC-BY-SA-4.0",
            expected_candidate_count=5,
        )
        updated = assign_calibration_participants(
            samples_dir=output / "samples",
            author_id="human_01",
            reviewer_ids=["human_02", "human_03"],
        )
        self.assertEqual(updated, 60)
        sample = json.loads(
            next((output / "samples").glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(sample["author_id"], "human_01")
        self.assertEqual(
            [row["reviewer_id"] for row in sample["qa_reviews"]],
            ["human_02", "human_03"],
        )
        self.assertEqual(
            [row["decision"] for row in sample["qa_reviews"]],
            ["pending", "pending"],
        )
        with self.assertRaises(ValueError):
            assign_calibration_participants(
                samples_dir=output / "samples",
                author_id="human_01",
                reviewer_ids=["human_01", "human_03"],
            )

    def test_frozen_field_change_is_blocked(self):
        root = self.runtime_root()
        source = root / "source"
        _create_candidate_pool(source)
        output = root / "kit"
        create_calibration_authoring_kit(
            source_root=source,
            output_dir=output,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
            source_url="https://example.invalid/dataset",
            source_license="CC-BY-SA-4.0",
            expected_candidate_count=5,
        )
        samples = json.loads(
            (output / "dataset.draft.json").read_text(encoding="utf-8")
        )
        samples[0]["multi_table"] = not samples[0]["multi_table"]
        issues, summary = audit_frozen_allocation(
            samples,
            output / "frozen_allocation_manifest.json",
        )
        self.assertIn(
            "FROZEN_ALLOCATION_CHANGED",
            {row["error_code"] for row in issues},
        )
        self.assertEqual(summary["frozen_status"], "invalid")

    def test_review_ledger_is_bound_to_current_content_hash(self):
        root = self.runtime_root()
        source = root / "source"
        _create_candidate_pool(source)
        output = root / "kit"
        create_calibration_authoring_kit(
            source_root=source,
            output_dir=output,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
            source_url="https://example.invalid/dataset",
            source_license="CC-BY-SA-4.0",
            expected_candidate_count=5,
        )
        assign_calibration_participants(
            samples_dir=output / "samples",
            author_id="human_01",
            reviewer_ids=["human_02", "human_03"],
        )
        sample_path = next((output / "samples").glob("*.json"))
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        sample["independently_authored"] = True
        sample["authoring_status"] = "authored_pending_review"
        sample["input_text"] = "A newly authored request."
        sample_path.write_text(
            json.dumps(sample, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for reviewer in ("human_02", "human_03"):
            record_calibration_review(
                sample_path=sample_path,
                ledger_path=output / "review_ledger.csv",
                reviewer_id=reviewer,
                decision="approved",
                reviewed_at_utc="2026-07-27T00:00:00Z",
            )
        approved = json.loads(sample_path.read_text(encoding="utf-8"))
        self.assertEqual(approved["authoring_status"], "approved")
        issues, _ = audit_review_ledger(
            [approved],
            output / "review_ledger.csv",
        )
        self.assertEqual(issues, [])
        approved["input_text"] = "Changed after approval."
        sample_path.write_text(
            json.dumps(approved, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        issues, _ = audit_review_ledger(
            [json.loads(sample_path.read_text(encoding="utf-8"))],
            output / "review_ledger.csv",
        )
        self.assertIn(
            "MISSING_CURRENT_REVISION_LEDGER_APPROVALS",
            {row["error_code"] for row in issues},
        )
        revision = start_calibration_revision(sample_path)
        self.assertEqual(revision, 2)

    def test_semantic_audit_accepts_new_key_plain_insert(self):
        root = self.runtime_root()
        source = root / "source"
        _create_candidate_pool(source)
        output = root / "kit"
        create_calibration_authoring_kit(
            source_root=source,
            output_dir=output,
            calibration_database_ids=["cal_a", "cal_b"],
            reserved_final_database_ids=["final_a", "final_b", "final_c"],
            source_url="https://example.invalid/dataset",
            source_license="CC-BY-SA-4.0",
            expected_candidate_count=5,
        )
        sample = next(
            candidate
            for path in sorted((output / "samples").glob("cal_cal_a_*.json"))
            for candidate in [json.loads(path.read_text(encoding="utf-8"))]
            if candidate["operation_semantics"] == "plain_insert"
        )
        sample.update(
            {
                "complexity": "single_row",
                "multi_table": False,
                "workload_shape": "single_row__single_table",
                "gold_sql": [
                    "INSERT INTO parent(id, code, name) "
                    "VALUES (2, 'B', 'Beta');"
                ],
                "gold_records": [
                    {
                        "table": "parent",
                        "values": {"id": 2, "code": "B", "name": "Beta"},
                    }
                ],
                "gold_tables": ["parent"],
                "conflict_target": [],
                "update_columns": [],
            }
        )
        profile = load_profile(output / "profiles" / "cal_a.json")
        sample["gold_plan"] = parse_gold_sql(
            sample["gold_sql"],
            sample_id=sample["id"],
            profile=profile,
        )
        issues, summary, _ = audit_calibration_semantics(
            [sample],
            kit_dir=output,
        )
        self.assertEqual(issues, [])
        self.assertEqual(summary["semantic_status"], "valid")


if __name__ == "__main__":
    unittest.main()
