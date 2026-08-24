from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import nldbwrite_v3.pipeline as pipeline_module
from nldbwrite_v3.experiments.run_method import _load_method_config, _prompt_for_sample
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import extract_evidence_candidates
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.vnext.targeted_repair import (
    DiagnosticTargetedRepairConfig,
    diagnose_evidence_span_boundaries,
    mark_targeted_revalidation,
    repair_evidence_span_boundary_after_diagnostic,
)


G1_CONFIG = {
    "enabled": True,
    "evidence_span_boundary": True,
    "allowed_terminal_punctuation": [".", ","],
    "max_revalidation_attempts": 1,
    "require_deterministic_diagnostic": True,
    "require_single_diagnosed_slot": True,
    "require_unique_candidate": True,
    "preserve_other_semantics": True,
    "emit_repair_provenance": True,
}


def _config() -> DiagnosticTargetedRepairConfig:
    return DiagnosticTargetedRepairConfig.from_mapping(G1_CONFIG)


def _profile() -> dict:
    profile = {
        "db_id": "stage2_g1",
        "tables": [
            {
                "name": "events",
                "columns": [
                    {
                        "name": "event_id",
                        "type": "TEXT",
                        "is_primary_key": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "note",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    },
                ],
                "required_insert_columns": ["event_id"],
                "primary_keys": ["event_id"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["event_id"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [],
            }
        ],
    }
    ensure_reference_ids(profile)
    return profile


def _candidate_pair(request: str) -> tuple[list[dict], dict, dict]:
    candidates = extract_evidence_candidates(request)
    selected = next(
        item
        for item in candidates
        if item["candidate_type"] == "number_or_identifier"
        and item["text"].endswith(".")
    )
    bounded = next(
        item
        for item in candidates
        if item["start"] == selected["start"]
        and item["end"] == selected["end"] - 1
        and item["text"] == selected["text"][:-1]
    )
    return candidates, selected, bounded


def _plan(
    request: str,
    *,
    target_column: str = "event_id",
    second_slot: bool = False,
) -> tuple[dict, list[dict], dict, dict]:
    profile = _profile()
    table = profile["tables"][0]
    columns = {item["name"]: item["column_id"] for item in table["columns"]}
    candidates, selected, bounded = _candidate_pair(request)
    row = {
        columns[target_column]: {
            "value_from": selected["evidence_id"],
            "normalization": "identity",
        }
    }
    if second_slot:
        row[columns["note"]] = {
            "value_from": selected["evidence_id"],
            "normalization": "identity",
        }
    plan = {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": table["table_id"],
                "rows": [row],
                "write_semantics": "plain_insert",
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }
    return plan, candidates, selected, bounded


def test_g1_v7_is_direct_v6_plus_independent_g1_ablation() -> None:
    v6, _ = _load_method_config(
        Path("configs/stage2/v6_constrained_reference_repair.json")
    )
    v7, _ = _load_method_config(
        Path("configs/stage2/v7_diagnostic_targeted_repair_g1.json")
    )
    assert v7["method_variant"] == "vnext-v7-diagnostic-targeted-repair-g1"
    assert v7["method_version"] == "stage2-v7-abcdefg1-evidence-boundary-repair"
    assert v7["diagnostic_targeted_repair"] == G1_CONFIG
    for key, value in v6.items():
        if key in {"method_variant", "method_version"}:
            continue
        assert v7[key] == value, key


def test_g1_prompt_is_identical_to_frozen_v6_prompt() -> None:
    v6, _ = _load_method_config(
        Path("configs/stage2/v6_constrained_reference_repair.json")
    )
    v7, _ = _load_method_config(
        Path("configs/stage2/v7_diagnostic_targeted_repair_g1.json")
    )
    sample = {"input_text": "Insert event_id EVT1."}
    prompt6, payload6 = _prompt_for_sample("MP-FS+", sample, _profile(), v6)
    prompt7, payload7 = _prompt_for_sample("MP-FS+", sample, _profile(), v7)
    assert prompt7 == prompt6
    assert payload7.to_dict() == payload6.to_dict()


@pytest.mark.parametrize(
    "relaxation",
    [
        {"max_revalidation_attempts": 2},
        {"require_deterministic_diagnostic": False},
        {"require_single_diagnosed_slot": False},
        {"require_unique_candidate": False},
        {"preserve_other_semantics": False},
        {"emit_repair_provenance": False},
        {"allowed_terminal_punctuation": ["!", "."]},
    ],
)
def test_g1_rejects_relaxed_safety_contract(relaxation: dict) -> None:
    with pytest.raises(ValueError):
        DiagnosticTargetedRepairConfig.from_mapping(
            {**G1_CONFIG, **relaxation}
        )


def test_g1_diagnostic_uses_only_same_start_pre_enumerated_boundary() -> None:
    request = "Insert event_id EVT9081. Leave all other fields unchanged."
    plan, candidates, selected, bounded = _plan(request)
    diagnostics = diagnose_evidence_span_boundaries(plan, candidates, _config())
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.error_code == "EVIDENCE_SPAN_TERMINAL_PUNCTUATION"
    assert diagnostic.details["old_reference"] == selected["evidence_id"]
    assert diagnostic.details["old_value"] == "EVT9081."
    assert diagnostic.candidates == [bounded["evidence_id"]]
    assert diagnostic.details["candidate_set"] == [
        {
            "evidence_id": bounded["evidence_id"],
            "text": "EVT9081",
            "start": bounded["start"],
            "end": bounded["end"],
            "candidate_type": "number_or_identifier",
        }
    ]


def test_g1_does_not_search_request_when_bounded_candidate_is_absent() -> None:
    request = "Insert event_id EVT9081. Another EVT9081 occurs later."
    plan, candidates, _, bounded = _plan(request)
    closed_without_bounded = [
        item for item in candidates if item["evidence_id"] != bounded["evidence_id"]
    ]
    assert diagnose_evidence_span_boundaries(
        plan, closed_without_bounded, _config()
    ) == []


def test_g1_deep_copy_changes_exactly_one_diagnosed_reference() -> None:
    request = "Insert event_id EVT9081."
    plan, candidates, selected, bounded = _plan(request)
    original = deepcopy(plan)
    diagnostics = diagnose_evidence_span_boundaries(plan, candidates, _config())
    outcome = repair_evidence_span_boundary_after_diagnostic(
        plan, diagnostics, _config()
    )
    assert outcome.applied
    assert plan == original
    repaired_spec = outcome.plan["write_groups"][0]["rows"][0]
    column_id = next(iter(repaired_spec))
    assert repaired_spec[column_id] == {
        "value_from": bounded["evidence_id"],
        "normalization": "identity",
    }
    expected = deepcopy(original)
    expected["write_groups"][0]["rows"][0][column_id]["value_from"] = bounded[
        "evidence_id"
    ]
    assert outcome.plan == expected
    trace = outcome.traces[0]
    assert trace["old_reference"] == selected["evidence_id"]
    assert trace["selected_repair"] == bounded["evidence_id"]
    assert trace["repair_applied"] is True
    assert trace["repair_succeeded"] is False
    assert trace["atomic_rollback"] is False
    assert trace["revalidation_result"] == "NOT_RUN"


def test_g1_multiple_diagnosed_slots_fail_closed_without_mutation() -> None:
    request = "Insert event_id EVT9081."
    plan, candidates, _, _ = _plan(request, second_slot=True)
    original = deepcopy(plan)
    diagnostics = diagnose_evidence_span_boundaries(plan, candidates, _config())
    assert len(diagnostics) == 2
    outcome = repair_evidence_span_boundary_after_diagnostic(
        plan, diagnostics, _config()
    )
    assert not outcome.applied
    assert outcome.plan == original
    assert all(
        trace["repair_rule"] == "multiple_diagnosed_slots"
        for trace in outcome.traces
    )


def test_g1_ambiguous_closed_candidate_set_fails_closed() -> None:
    request = "Insert event_id EVT9081."
    plan, candidates, _, bounded = _plan(request)
    duplicate = {**bounded, "evidence_id": "duplicate-bounded"}
    diagnostics = diagnose_evidence_span_boundaries(
        plan, [*candidates, duplicate], _config()
    )
    assert len(diagnostics) == 1
    assert len(diagnostics[0].details["candidate_set"]) == 2
    outcome = repair_evidence_span_boundary_after_diagnostic(
        plan, diagnostics, _config()
    )
    assert not outcome.applied
    assert outcome.traces[0]["repair_rule"] == "non_unique_closed_candidate_set"


def test_g1_replacement_evidence_collision_fails_closed() -> None:
    request = "Insert event_id EVT9081."
    plan, candidates, _, bounded = _plan(request)
    table = _profile()["tables"][0]
    note_id = next(
        item["column_id"] for item in table["columns"] if item["name"] == "note"
    )
    plan["write_groups"][0]["rows"][0][note_id] = {
        "value_from": bounded["evidence_id"],
        "normalization": "identity",
    }
    original = deepcopy(plan)
    diagnostics = diagnose_evidence_span_boundaries(plan, candidates, _config())
    outcome = repair_evidence_span_boundary_after_diagnostic(
        plan, diagnostics, _config()
    )
    assert not outcome.applied
    assert outcome.plan == original
    assert (
        outcome.traces[0]["repair_rule"]
        == "replacement_evidence_reference_collision"
    )


def test_g1_pipeline_repairs_stage1_boundary_case_and_emits_provenance() -> None:
    fixture = json.loads(
        Path("tests/fixtures/stage2_g1_stage1_evidence_boundary_cases.json").read_text()
    )["cases"][0]
    request = fixture["request"]
    profile = _profile()
    plan, _, selected, bounded = _plan(request)
    for key in ("evidence_id", "text", "start", "end", "candidate_type"):
        assert selected[key] == fixture["selected_evidence"][key]
        assert bounded[key] == fixture["bounded_candidate"][key]
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        diagnostic_targeted_repair=G1_CONFIG,
    ).run(request, plan)
    assert result.success
    assert result.write_plan is not None
    group = result.write_plan["write_groups"][0]
    assert group["rows"] == [{"event_id": "SC9081"}]
    assert result.program is not None
    assert result.program.statements[0].params == ["SC9081"]
    trace = group["reference_trace"]["diagnostic_targeted_repairs"]
    assert len(trace) == 1
    assert trace[0]["diagnostic"] == "EVIDENCE_SPAN_TERMINAL_PUNCTUATION"
    assert trace[0]["old_reference"] == selected["evidence_id"]
    assert trace[0]["old_value"] == "SC9081."
    assert trace[0]["selected_repair"] == bounded["evidence_id"]
    assert trace[0]["repair_rule"] == (
        "unique_pre_enumerated_terminal_punctuation_trim"
    )
    assert trace[0]["repair_applied"] is True
    assert trace[0]["repair_succeeded"] is True
    assert trace[0]["revalidation_result"] == "PASS"
    assert trace[0]["revalidation_attempts"] == 1
    assert group["value_evidence"][0]["event_id"]["exact_span"] == "SC9081"
    assert any(
        warning.error_code == "DIAGNOSTIC_TARGETED_REPAIR_APPLIED"
        for warning in result.verification.warnings
    )


def test_g1_pipeline_materializes_original_then_revalidates_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = "Insert event_id EVT9081."
    profile = _profile()
    plan, _, _, _ = _plan(request)
    original = pipeline_module.materialize_reference_free_text_plan
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_module, "materialize_reference_free_text_plan", counted
    )
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        diagnostic_targeted_repair=G1_CONFIG,
    ).run(request, plan)
    assert result.success
    assert calls == 2


def test_g1_pipeline_verifier_failure_marks_repair_failed_closed() -> None:
    request = "Create EVT9081."
    profile = _profile()
    plan, _, _, _ = _plan(request, target_column="note")
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        diagnostic_targeted_repair=G1_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "verification"
    assert any(
        item.error_code == "MISSING_REQUIRED_COLUMN"
        for item in result.verification.errors
    )
    trace = result.write_plan["write_groups"][0]["reference_trace"][
        "diagnostic_targeted_repairs"
    ][0]
    assert result.write_plan["write_groups"][0]["rows"] == [
        {"note": "EVT9081."}
    ]
    assert result.write_plan["write_groups"][0]["value_evidence"][0]["note"][
        "exact_span"
    ] == "EVT9081."
    assert trace["repair_applied"] is True
    assert trace["repair_succeeded"] is False
    assert trace["atomic_rollback"] is True
    assert trace["revalidation_result"] == "FAIL_CLOSED"
    assert trace["revalidation_attempts"] == 1


def test_g1_disabled_output_is_identical_to_frozen_v6_behavior() -> None:
    request = "Insert event_id EVT9081."
    profile = _profile()
    plan, _, _, _ = _plan(request)
    baseline = MappingFirstPipeline(profile, reference_planning=True).run(
        request, plan
    )
    disabled = MappingFirstPipeline(
        profile,
        reference_planning=True,
        diagnostic_targeted_repair={"enabled": False},
    ).run(request, plan)
    assert disabled.to_dict() == baseline.to_dict()
    assert baseline.program is not None
    assert baseline.program.statements[0].params == ["EVT9081."]


def test_g1_revalidation_marker_runs_once_and_never_claims_unrun_success() -> None:
    request = "Insert event_id EVT9081."
    plan, candidates, _, _ = _plan(request)
    diagnostics = diagnose_evidence_span_boundaries(plan, candidates, _config())
    outcome = repair_evidence_span_boundary_after_diagnostic(
        plan, diagnostics, _config()
    )
    passed = mark_targeted_revalidation(outcome.traces, passed=True)
    assert passed[0]["revalidation_attempts"] == 1
    assert passed[0]["repair_succeeded"] is True
    assert outcome.traces[0]["revalidation_attempts"] == 0
    assert outcome.traces[0]["repair_succeeded"] is False
