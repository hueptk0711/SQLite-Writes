from __future__ import annotations

import json

from .stage1_failure_analysis import build_downstream_bypass_analysis, build_master


def main() -> None:
    rows, _ = build_master()
    detail, summary = build_downstream_bypass_analysis(rows)
    print(json.dumps({"detail": detail, "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
