from __future__ import annotations

from pathlib import Path


def primary_pipeline_source_uses_oracle(root: Path) -> bool:
    source = (root / "src/nldbwrite_v3/v2_a1/pipeline.py").read_text(encoding="utf-8")
    return "oracle_span_provider" in source or "SOURCE_SPAN_LABEL_MANIFEST" in source
