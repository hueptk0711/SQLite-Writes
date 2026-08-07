from __future__ import annotations

import sys

from nldbwrite_v3.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["oracle", *sys.argv[1:]]))

