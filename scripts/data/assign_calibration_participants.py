from __future__ import annotations

import argparse
import json

from nldbwrite_v3.data.authoring import assign_calibration_participants


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assign three distinct human IDs without approving any review."
    )
    parser.add_argument("--samples-dir", required=True)
    parser.add_argument("--author-id", required=True)
    parser.add_argument(
        "--reviewer-id",
        action="append",
        required=True,
        help="Human reviewer ID; pass exactly twice.",
    )
    args = parser.parse_args()
    updated = assign_calibration_participants(
        samples_dir=args.samples_dir,
        author_id=args.author_id,
        reviewer_ids=args.reviewer_id,
    )
    print(
        json.dumps(
            {
                "updated_samples": updated,
                "reviews_marked_approved": 0,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
