from __future__ import annotations

from pathlib import Path

import pytest

from scripts.analysis.run_stage3_causal_replay import (
    collect_repair_traces,
    first_failure,
    repair_flags,
    safe_extract_dataset,
    semantic_fingerprint,
    validate_replay_results,
)


def _evaluation(**overrides):
    value = {
        "execution_success": True,
        "target_state_correct": True,
        "strict_full_state_correct": True,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"parse_success": False}, "parse"),
        ({"build_success": False, "pipeline_stage": "materialization"}, "materialization"),
        ({"semantic_gate": {"accepted": False}}, "semantic_gate"),
        ({"preflight": {"accepted": False}}, "preflight"),
        ({"evaluation": _evaluation(execution_success=False)}, "execution"),
        ({"evaluation": _evaluation(target_state_correct=False)}, "state_mismatch"),
        ({"evaluation": _evaluation(strict_full_state_correct=False)}, "off_target_state_change"),
        ({}, "none"),
    ],
)
def test_first_failure_is_ordered(overrides, expected) -> None:
    values = {
        "parse_success": True,
        "pipeline_stage": "complete",
        "build_success": True,
        "semantic_gate": {"accepted": True},
        "preflight": {"accepted": True},
        "evaluation": _evaluation(),
    }
    values.update(overrides)
    assert first_failure(**values) == expected


def test_collect_repair_traces_separates_f_g1_g2_and_deduplicates() -> None:
    f = {
        "repair_rule": "unique_exact_identifier_name",
        "repair_attempted": True,
        "repair_applied": True,
        "repair_succeeded": True,
        "slot_path": "/x",
        "reference_kind": "column",
    }
    g1 = {
        "stage2_intervention": "G1_evidence_span_boundary_repair",
        "repair_rule": "trim_terminal_punctuation",
        "repair_attempted": True,
        "repair_applied": False,
        "repair_succeeded": False,
    }
    g2 = {
        "stage2_intervention": "G2_temporal_evidence_selection_repair",
        "repair_rule": "replace_temporal_evidence_selection",
        "repair_attempted": True,
        "repair_applied": True,
        "repair_succeeded": False,
    }
    traces = collect_repair_traces({"one": f, "duplicate": f, "g": [g1, g2]})
    assert traces == {"F": [f], "G1": [g1], "G2": [g2]}
    assert repair_flags(traces["F"]) == {
        "attempted": True,
        "applied": True,
        "succeeded": True,
        "rules": ["unique_exact_identifier_name"],
    }


def test_semantic_fingerprint_ignores_provenance_only() -> None:
    base = {
        "sample_id": "x",
        "source_payload": {"mode": "free_text", "instruction_text": "x", "collections": []},
        "parse_success": True,
        "pipeline_stage": "complete",
        "write_plan": {"write_groups": [{"table": "t"}]},
        "verification_errors": [],
        "compiled_program": {"status": "success", "statements": []},
        "semantic_risk_gate": {"accepted": True},
        "preflight": {"accepted": True, "error_class": None},
        "evaluation": _evaluation(any_off_target_change=False, error_type=None),
    }
    with_trace = {
        **base,
        "write_plan": {
            "write_groups": [{"table": "t", "reference_trace": {"x": 1}}],
            "consumed_control_refs": [{"source_field": "operation"}],
        },
    }
    assert semantic_fingerprint(base) == semantic_fingerprint(with_trace)
    changed = {**base, "pipeline_stage": "verification"}
    assert semantic_fingerprint(base) != semantic_fingerprint(changed)


def test_safe_extract_dataset_rejects_parent_traversal(tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("root/../escape.txt", "unsafe")
    with pytest.raises(ValueError, match="Unsafe dataset archive member"):
        safe_extract_dataset(archive, tmp_path / "out")


def test_validate_replay_results_rejects_success_without_application() -> None:
    empty = {"F": [], "G1": [], "G2": []}
    all_results = {
        f"V{index}": {"sample": {"repair_traces": empty}}
        for index in range(9)
    }
    all_results["V8"]["sample"] = {
        "repair_traces": {
            "F": [],
            "G1": [],
            "G2": [
                {
                    "repair_attempted": True,
                    "repair_applied": False,
                    "repair_succeeded": True,
                    "revalidation_attempts": 0,
                }
            ],
        }
    }
    with pytest.raises(ValueError, match="succeeded_implies_applied"):
        validate_replay_results(all_results, {"sample"})
