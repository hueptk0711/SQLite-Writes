from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def build_sql_with_v2(
    predicted_json: dict[str, Any],
    profile: dict[str, Any],
    *,
    v2_source_path: str | Path | None = None,
) -> tuple[str, list[str], list[Any], list[Any]]:
    """Call the frozen paper-v2 builder without substituting v3 compilation."""
    configured = v2_source_path or os.environ.get("NLDB_V2_SOURCE")
    if not configured:
        raise ValueError(
            "S-FS-v2 requires --v2-source-path or NLDB_V2_SOURCE"
        )
    root = Path(configured).resolve()
    package = root / "nldbwrite"
    if not package.is_dir():
        raise ValueError(
            "v2 source path must be the directory containing nldbwrite/"
        )
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from nldbwrite.sql.build_sql import build_sql_from_json

    status, sqls, errors, metadata = build_sql_from_json(
        predicted_json,
        profile,
        {},
    )
    return (
        str(status),
        list(sqls or []),
        list(errors or []),
        list(metadata or []),
    )
