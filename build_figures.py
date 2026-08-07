from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nldbwrite_v3.analysis.figures_v2_2 import build_figures


def main() -> int:
    workspace = Path(__file__).resolve().parents[3]
    output_dir = (
        workspace / "04_results" / "03_analysis_work" / "reporting_v2_3_20260801"
    )
    report = json.loads((output_dir / "reporting_v2_3_results.json").read_text(encoding="utf-8"))
    taxonomy_rows = []
    import csv

    with (output_dir / "error_taxonomy.csv").open(encoding="utf-8", newline="") as handle:
        taxonomy_rows = list(csv.DictReader(handle))
    paths = build_figures(report["methods"], taxonomy_rows, output_dir)
    print(json.dumps({"status": "pass", "figures": [str(path) for path in paths]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
