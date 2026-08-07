from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nldbwrite_v3.analysis.backend_swap import run_backend_swap


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--v2-source")
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
    try:
        result = run_backend_swap(
            workspace,
            output_dir,
            v2_source=Path(args.v2_source) if args.v2_source else None,
        )
    except ValueError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result["status"],
        "analysis_id": result["analysis_id"],
        "sample_count_per_cell": result["sample_count_per_cell"],
        "anchor_checks": result["anchor_checks"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
