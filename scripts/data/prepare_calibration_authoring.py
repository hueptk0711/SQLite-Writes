from __future__ import annotations

import argparse
import json

from nldbwrite_v3.data.authoring import (
    create_calibration_authoring_kit,
    read_noncomment_ids,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the frozen 60-sample calibration authoring kit."
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--calibration-db-ids", required=True)
    parser.add_argument("--reserved-final-db-ids", required=True)
    parser.add_argument(
        "--source-url",
        default=(
            "https://huggingface.co/datasets/"
            "birdsql/livesqlbench-base-lite-sqlite"
        ),
    )
    parser.add_argument("--source-license", default="CC-BY-SA-4.0")
    parser.add_argument("--source-archive-sha256")
    parser.add_argument("--source-revision")
    parser.add_argument("--expected-candidate-count", type=int, default=18)
    args = parser.parse_args()
    manifest = create_calibration_authoring_kit(
        source_root=args.source_root,
        output_dir=args.output_dir,
        calibration_database_ids=read_noncomment_ids(
            args.calibration_db_ids
        ),
        reserved_final_database_ids=read_noncomment_ids(
            args.reserved_final_db_ids
        ),
        source_url=args.source_url,
        source_license=args.source_license,
        source_archive_sha256=args.source_archive_sha256,
        source_revision=args.source_revision,
        expected_candidate_count=args.expected_candidate_count,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
