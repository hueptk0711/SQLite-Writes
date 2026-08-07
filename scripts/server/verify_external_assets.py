from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from nldbwrite_v3.common import load_json, sha256_file
from nldbwrite_v3.evaluator import find_database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default="data/frozen/dev/frozen_manifest_dev.json",
    )
    parser.add_argument(
        "--profile-dir",
        default=os.environ.get("NLDB_PROFILE_DIR"),
    )
    parser.add_argument(
        "--db-root",
        default=os.environ.get("NLDB_DATABASE_ROOT"),
    )
    args = parser.parse_args()
    if not args.profile_dir or not args.db_root:
        raise ValueError(
            "Set NLDB_PROFILE_DIR and NLDB_DATABASE_ROOT or pass both paths"
        )
    manifest = load_json(args.manifest)
    expected_profiles = manifest.get("hashes", {}).get("profile_sha256") or {}
    expected_databases = manifest.get("hashes", {}).get("database_sha256") or {}
    checks = []
    for db_id, expected in sorted(expected_profiles.items()):
        path = Path(args.profile_dir) / f"{db_id}.json"
        actual = sha256_file(path) if path.exists() else None
        checks.append(
            {
                "kind": "profile",
                "db_id": db_id,
                "path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "valid": actual == expected,
            }
        )
    for db_id, expected in sorted(expected_databases.items()):
        try:
            path = find_database(args.db_root, db_id)
            actual = sha256_file(path)
        except FileNotFoundError:
            path = Path(args.db_root) / db_id
            actual = None
        checks.append(
            {
                "kind": "database",
                "db_id": db_id,
                "path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "valid": actual == expected,
            }
        )
    result = {
        "manifest": str(Path(args.manifest).resolve()),
        "checks": len(checks),
        "valid": sum(bool(item["valid"]) for item in checks),
        "invalid": sum(not bool(item["valid"]) for item in checks),
        "details": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["invalid"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
