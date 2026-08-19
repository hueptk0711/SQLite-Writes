from __future__ import annotations

import json

from .stage1_failure_analysis import build_master, build_state_mismatch_audit


def main() -> None:
    rows, _ = build_master()
    print(json.dumps(build_state_mismatch_audit(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
