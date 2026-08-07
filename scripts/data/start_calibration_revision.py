from __future__ import annotations

import argparse
import json

from nldbwrite_v3.data.authoring import start_calibration_revision


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start a new authored revision and invalidate old approvals."
    )
    parser.add_argument("--sample", required=True)
    args = parser.parse_args()
    revision = start_calibration_revision(args.sample)
    print(json.dumps({"sample": args.sample, "revision": revision}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
