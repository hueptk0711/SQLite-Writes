from __future__ import annotations

import unittest
from pathlib import Path

from nldbwrite_v3.analysis.figures_v2_2 import build_figures


class FigureV22Tests(unittest.TestCase):
    def test_build_figures_creates_three_svg_files(self):
        methods = {}
        for method in (
            "D-FS-M",
            "J-FS-M",
            "S-FS-v2-M",
            "MP-FS-M",
            "MP-FS+",
        ):
            methods[method] = {
                "target_state_accuracy": 0.5,
                "method_specific_admission_coverage": 0.6,
                "slices": {
                    f"input_format:{input_format}": {
                        "target_state_accuracy": 0.5
                    }
                    for input_format in (
                        "csv_or_mixed",
                        "free_text",
                        "json",
                        "key_value",
                        "markdown",
                    )
                },
            }
        taxonomy = [
            {
                "method_id": "MP-FS+",
                "error_category": "E3_unknown_column",
                "count": 2,
            }
        ]
        output = Path(__file__).resolve().parent / "test_tmp" / "figure_test_output"
        output.mkdir(parents=True, exist_ok=True)
        paths = build_figures(methods, taxonomy, output)
        self.assertEqual(len(paths), 3)
        for path in paths:
            self.assertTrue(path.is_file())
            self.assertIn("<svg", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
