from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class RuntimeSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.script = (
            self.root / "scripts" / "server" / "verify_runtime_source.py"
        )
        self.environment = dict(os.environ)
        current = self.environment.get("PYTHONPATH")
        source = str(self.root / "src")
        self.environment["PYTHONPATH"] = (
            source + (os.pathsep + current if current else "")
        )
        self.temporary_root = self.root / "artifacts" / "test_tmp"
        self.temporary_root.mkdir(parents=True, exist_ok=True)

    def test_current_bundle_source_passes(self):
        output = self.temporary_root / "runtime_source_current_test.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--project-root",
                str(self.root),
                "--output",
                str(output),
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["source_is_current_bundle"])

    def test_different_project_root_is_rejected(self):
        false_root = self.root.parent
        output = self.temporary_root / "runtime_source_mismatch_test.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--project-root",
                str(false_root),
                "--output",
                str(output),
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            2,
            completed.stdout + completed.stderr,
        )
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "mismatch")
        self.assertFalse(result["source_is_current_bundle"])


if __name__ == "__main__":
    unittest.main()
