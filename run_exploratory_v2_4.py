from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nldbwrite_v3.analysis.exploratory_v2_4 import run_exploratory_v2_4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    workspace = (
        Path(args.workspace_root).resolve()
        if args.workspace_root
        else Path(__file__).resolve().parents[3]
    )
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else workspace / "04_results" / "03_analysis_work" / "reporting_v2_4_20260801"
    )
    print(json.dumps(run_exploratory_v2_4(workspace, output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
