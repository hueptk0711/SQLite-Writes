from __future__ import annotations

from pathlib import Path

from scripts.analysis.run_stage4_fresh_7b_protocol import validate_protocol


def test_stage4_protocol_outputs_are_internally_consistent() -> None:
    root = Path(__file__).resolve().parents[1] / "stage4_fresh_7b_protocol"
    report = validate_protocol(root)
    assert report["status"] == "PASS"
    assert report["sample_count"] == 300
    assert report["prompt_rows"] == 2100
    assert report["violations"] == []
