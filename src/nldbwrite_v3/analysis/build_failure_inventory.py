from __future__ import annotations

import json

from .stage1_failure_analysis import build_all


def main() -> None:
    print(json.dumps(build_all(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
