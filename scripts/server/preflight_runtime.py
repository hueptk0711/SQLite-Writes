#!/usr/bin/env python3
"""Fail-fast runtime preflight for Stage7E0-A4 Kaggle primary runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.server.run_stage7e0_a4_english import PRIMARY_RUNTIME_PROFILE_ID, runtime_versions, validate_runtime_versions  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-profile", default=PRIMARY_RUNTIME_PROFILE_ID)
    args = parser.parse_args()
    if args.expected_profile != PRIMARY_RUNTIME_PROFILE_ID:
        raise SystemExit(f"STOP: Stage7E0-A4 primary runtime is locked to {PRIMARY_RUNTIME_PROFILE_ID}")
    observed = runtime_versions()
    report = validate_runtime_versions(observed)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
