from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

import pytest


class CleanReleaseReproductionTests(unittest.TestCase):
    @pytest.mark.integration
    @pytest.mark.slow
    @unittest.skipUnless(
        os.environ.get("NLDB_CLEAN_RELEASE_ARCHIVE"),
        "set NLDB_CLEAN_RELEASE_ARCHIVE to run the clean-extraction integration test",
    )
    def test_release_reproduces_without_old_paths_or_symlinks(self):
        workspace = Path(__file__).resolve().parents[1]
        validator_path = workspace / "08_tools" / "validate_clean_release.py"
        spec = importlib.util.spec_from_file_location(
            "validate_clean_release", validator_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.validate_clean_release(
            Path(os.environ["NLDB_CLEAN_RELEASE_ARCHIVE"])
        )
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["clean_extraction"])
        self.assertFalse(result["symlinks_created"])


if __name__ == "__main__":
    unittest.main()
