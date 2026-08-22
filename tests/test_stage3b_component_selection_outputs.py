from pathlib import Path

from scripts.analysis.validate_stage3b_component_selection import validate_results


def test_frozen_stage3b_outputs_are_internally_consistent() -> None:
    root = Path(__file__).resolve().parents[1] / "stage3b_final_component_selection"
    report = validate_results(root)
    assert report["status"] == "PASS"
    assert report["sample_rows"] == 300
    assert report["prompt_rows"] == 300
    assert report["candidates"] == 4
    assert report["violations"] == []
