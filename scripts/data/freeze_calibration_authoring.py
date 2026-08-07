from __future__ import annotations

import argparse
import json
from pathlib import Path

from nldbwrite_v3.data.calibration_freeze import (
    evaluate_calibration_freeze_readiness,
    freeze_calibration_authoring,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strictly validate and freeze canonical calibration artifacts."
    )
    parser.add_argument("--kit-dir", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--audit-output", required=True)
    args = parser.parse_args()
    issues, summary, _ = evaluate_calibration_freeze_readiness(
        kit_dir=args.kit_dir,
        data_path=args.data,
    )
    report = {"summary": summary, "issues": issues}
    audit_output = Path(args.audit_output)
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    audit_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if issues:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    manifest = freeze_calibration_authoring(
        kit_dir=args.kit_dir,
        data_path=args.data,
        output_dir=args.output_dir,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
