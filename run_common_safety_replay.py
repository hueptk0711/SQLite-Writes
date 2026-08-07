from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nldbwrite_v3.analysis.common_safety_replay import run_common_safety_replay


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
        else workspace
        / "04_results"
        / "03_analysis_work"
        / "reporting_v2_3_20260801"
    )
    result = run_common_safety_replay(workspace, output_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
