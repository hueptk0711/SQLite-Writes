from __future__ import annotations

import argparse
import json

from nldbwrite_v3.data.authoring import record_calibration_review


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record a human review bound to the current content SHA256."
    )
    parser.add_argument("--sample", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument(
        "--decision",
        choices=("approved", "rejected"),
        required=True,
    )
    parser.add_argument(
        "--issue-code",
        action="append",
        default=[],
        help="Required at least once for a rejected review.",
    )
    args = parser.parse_args()
    record = record_calibration_review(
        sample_path=args.sample,
        ledger_path=args.ledger,
        reviewer_id=args.reviewer_id,
        decision=args.decision,
        issue_codes=args.issue_code,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
