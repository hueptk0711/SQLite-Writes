from __future__ import annotations

import argparse
import json
from pathlib import Path

from nldbwrite_v3.data import audit_external_holdout_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Skip final size/distribution/reviewer gates.",
    )
    args = parser.parse_args()
    samples = json.loads(Path(args.data).read_text(encoding="utf-8"))
    if isinstance(samples, dict) and args.allow_draft:
        samples = [samples]
    if not isinstance(samples, list):
        raise ValueError("External holdout dataset must be a JSON array.")
    issues, summary = audit_external_holdout_metadata(
        samples,
        strict_final=not args.allow_draft,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"summary": summary, "issues": issues},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
