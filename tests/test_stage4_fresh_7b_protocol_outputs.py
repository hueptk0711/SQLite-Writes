from __future__ import annotations

from pathlib import Path
import json

from scripts.analysis.run_stage4_fresh_7b_protocol import validate_protocol


def test_stage4_protocol_outputs_are_internally_consistent() -> None:
    root = Path(__file__).resolve().parents[1] / "stage4_fresh_7b_protocol"
    report = validate_protocol(root)
    assert report["status"] == "PASS"
    assert report["sample_count"] == 300
    assert report["prompt_rows"] == 2100
    assert report["violations"] == []


def test_stage4_protocol_outputs_lock_patch1_execution_contract() -> None:
    root = Path(__file__).resolve().parents[1] / "stage4_fresh_7b_protocol"
    run_lock = json.loads((root / "provenance" / "run_lock_TEMPLATE.json").read_text(encoding="utf-8"))
    source_audit = json.loads((root / "data" / "source_group_audit.json").read_text(encoding="utf-8"))
    assert run_lock["generation_arms"] == ["direct", "j_fs", "mp_fs_plus_shared"]
    assert "repository_head" not in run_lock
    assert run_lock["runtime_assertions"]["working_tree_clean_before_generation"] is True
    assert run_lock["statistics"]["confidence_interval"] == "cluster_bootstrap_percentile_95"
    assert run_lock["statistics"]["bootstrap_replicates"] == 10000
    assert source_audit["sample_count"] == 300
    assert source_audit["source_group_source_counts"] == {"source_group_id": 300}
