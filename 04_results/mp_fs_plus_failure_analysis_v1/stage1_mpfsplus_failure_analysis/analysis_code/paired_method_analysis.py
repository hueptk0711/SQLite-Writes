from __future__ import annotations

import json

from .stage1_failure_analysis import build_master, build_paired


def main() -> None:
    rows, _ = build_master()
    print(json.dumps(build_paired(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
