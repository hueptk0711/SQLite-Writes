from __future__ import annotations

import argparse
import json
from pathlib import Path

from nldbwrite_v3.data.authoring import (
    audit_authoring_assets,
    audit_calibration_authoring_completion,
    read_noncomment_ids,
)
from nldbwrite_v3.data.calibration_semantics import (
    audit_calibration_semantics,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate calibration assets, authorship, reviews, and gold fields."
    )
    parser.add_argument("--kit-dir", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Write the audit report but return success for an incomplete draft.",
    )
    args = parser.parse_args()
    root = Path(args.kit_dir)
    samples = json.loads(Path(args.data).read_text(encoding="utf-8"))
    if not isinstance(samples, list):
        raise ValueError("Calibration dataset must be a JSON array.")
    asset_issues, asset_summary = audit_authoring_assets(root)
    authoring_issues, authoring_summary = (
        audit_calibration_authoring_completion(
            samples,
            calibration_database_ids=read_noncomment_ids(
                root / "calibration_database_ids.txt"
            ),
            reserved_final_database_ids=read_noncomment_ids(
                root / "reserved_final_database_ids.txt"
            ),
            frozen_allocation_manifest=(
                root / "frozen_allocation_manifest.json"
            ),
            review_ledger_path=root / "review_ledger.csv",
        )
    )
    semantic_issues, semantic_summary, _ = audit_calibration_semantics(
        samples,
        kit_dir=root,
    )
    issues = [*asset_issues, *authoring_issues, *semantic_issues]
    report = {
        "summary": {
            **asset_summary,
            **authoring_summary,
            **semantic_summary,
            "total_blocking_issue_count": len(issues),
            "status": "ready_for_freeze" if not issues else "draft_or_invalid",
            "paper_result_eligible": False,
            "gpu_run_authorized": False,
        },
        "issues": issues,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if not issues or args.allow_draft else 1


if __name__ == "__main__":
    raise SystemExit(main())
