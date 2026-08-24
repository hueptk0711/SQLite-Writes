from pathlib import Path

from scripts.analysis.validate_stage3_causal_replay import validate_results


def test_frozen_stage3_outputs_are_internally_consistent() -> None:
    root = Path(__file__).resolve().parents[1] / "stage3_full_causal_replay"
    report = validate_results(root)
    assert report == {
        "status": "PASS",
        "sample_rows": 300,
        "trace_rows": 300,
        "variants": 9,
        "manifest_entries_verified": 20,
        "violations": [],
    }
