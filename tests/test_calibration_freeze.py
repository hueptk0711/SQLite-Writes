from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
from pathlib import Path

from nldbwrite_v3.data.calibration_freeze import audit_calibration_gold_mp
from nldbwrite_v3.schema import build_profile


class CalibrationGoldMpTests(unittest.TestCase):
    def runtime_root(self) -> Path:
        root = (
            Path(__file__).parent
            / "_runtime_calibration_freeze"
            / self._testMethodName
        )
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_gold_mp_gate_authorizes_only_complete_sixty_of_sixty(self):
        root = self.runtime_root()
        db_root = root / "databases"
        db_dir = db_root / "cal_a"
        db_dir.mkdir(parents=True)
        database = db_dir / "cal_a.sqlite"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE records(
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL
                );
                INSERT INTO records(id, code, value)
                VALUES (1, 'A', 'old');
                """
            )
            connection.commit()
        finally:
            connection.close()
        profile_dir = root / "profiles"
        profile_dir.mkdir()
        profile = build_profile(database, db_id="cal_a")
        (profile_dir / "cal_a.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        samples = [
            {
                "id": f"cal_{index:03d}",
                "db_id": "cal_a",
                "gold_sql": [
                    "INSERT INTO records(id, code, value) "
                    "VALUES (2, 'B', 'new');"
                ],
                "gold_records": [
                    {
                        "table": "records",
                        "values": {"id": 2, "code": "B", "value": "new"},
                    }
                ],
                "gold_tables": ["records"],
            }
            for index in range(60)
        ]
        dataset = root / "dataset.json"
        dataset.write_text(
            json.dumps(samples, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        issues, summary = audit_calibration_gold_mp(
            dataset_path=dataset,
            profile_dir=profile_dir,
            db_root=db_root,
        )
        self.assertEqual(issues, [])
        self.assertEqual(summary["strict_full_state_correct"], 60)
        self.assertTrue(summary["gpu_run_authorized"])
        self.assertFalse(summary["paper_result_eligible"])


if __name__ == "__main__":
    unittest.main()
