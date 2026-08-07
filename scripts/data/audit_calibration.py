from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nldbwrite_v3.data import audit_calibration_metadata


DEFAULT_CONSUMED_DATA = (
    "data/frozen/dev/dataset_dev_v3.json",
    "data/frozen/test/dataset_test_v3.json",
)


def _load_samples(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"Expected a JSON array in {path}")
    return [row for row in value if isinstance(row, dict)]


def _read_ids(path: str | Path) -> list[str]:
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit the 60-sample independently authored calibration set."
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--reserved-final-db-ids", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--consumed-data",
        action="append",
        default=[],
        help="Consumed dataset JSON; may be repeated.",
    )
    args = parser.parse_args()

    samples = _load_samples(args.data)
    consumed_paths = args.consumed_data or list(DEFAULT_CONSUMED_DATA)
    consumed = [
        row
        for path in consumed_paths
        for row in _load_samples(path)
    ]
    consumed_ids = {
        str(row.get("id") or row.get("sample_id") or "")
        for row in consumed
    }
    consumed_groups = {
        str(row.get("source_group") or row.get("source_group_id") or "")
        for row in consumed
    }
    issues, summary = audit_calibration_metadata(
        samples,
        reserved_final_db_ids=_read_ids(args.reserved_final_db_ids),
        consumed_sample_ids=consumed_ids,
        consumed_source_groups=consumed_groups,
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
