from __future__ import annotations

import unittest
from pathlib import Path

from nldbwrite_v3.analysis.common_safety_replay import (
    _direct_sql_preflight,
    _local_archive_path,
    _summary,
)


class CommonSafetyReplayTests(unittest.TestCase):
    def test_archive_filename_overrides_windows_provenance_path(self):
        workspace = Path("clean-release")
        result = _local_archive_path(
            workspace,
            {
                "archive": r"D:\old\machine\wrong-name.tar.gz",
                "archive_filename": "final-archive.tar.gz",
            },
        )
        self.assertEqual(
            result,
            workspace
            / "04_results"
            / "00_incoming_from_server"
            / "final-archive.tar.gz",
        )

    def test_windows_archive_path_fallback_is_portable_on_posix(self):
        workspace = Path("clean-release")
        result = _local_archive_path(
            workspace,
            {"archive": r"D:\workspace\results\final_archive.tar.gz"},
        )
        self.assertEqual(result.name, "final_archive.tar.gz")

    def test_direct_sql_preflight_rejects_non_write_statement_before_database(self):
        result = _direct_sql_preflight(Path("not_used.sqlite"), ["DELETE FROM t"])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error_class"], "unsafe_sql")

    def test_summary_reports_false_accepts(self):
        rows = [
            {
                "preflight_accepted": True,
                "target_state_correct": True,
            },
            {
                "preflight_accepted": True,
                "target_state_correct": False,
            },
            {
                "preflight_accepted": False,
                "target_state_correct": False,
            },
        ]
        result = _summary(rows)
        self.assertEqual(result["false_accept_count"], 1)
        self.assertAlmostEqual(result["transactional_preflight_coverage"], 2 / 3)
        self.assertAlmostEqual(
            result["false_accept_rate_conditional_on_common_preflight"],
            0.5,
        )


if __name__ == "__main__":
    unittest.main()
