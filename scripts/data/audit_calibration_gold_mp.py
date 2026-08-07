from __future__ import annotations

import argparse
import json
from pathlib import Path

from nldbwrite_v3.data.calibration_freeze import audit_calibration_gold_mp


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic CPU Gold-MP gate on all 60 samples."
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    issues, summary = audit_calibration_gold_mp(
        dataset_path=args.data,
        profile_dir=args.profile_dir,
        db_root=args.db_root,
    )
    report = {"summary": summary, "issues": issues}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["gpu_run_authorized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
