#!/usr/bin/env python3
"""Validate Stage-4 fresh 7B protocol artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analysis.run_stage4_fresh_7b_protocol import validate_protocol  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True)
    args = parser.parse_args()
    report = validate_protocol(Path(args.results_root).resolve())
    print(json.dumps(report, indent=2))
    if report["status"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
