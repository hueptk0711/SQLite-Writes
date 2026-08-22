from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import nldbwrite_v3.pipeline as pipeline_module
from nldbwrite_v3.experiments.run_method import _load_method_config, _prompt_for_sample
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import (
    MaterializationError,
    extract_evidence_candidates,
    materialize_reference_free_text_plan,
    resolve_explicit_column_grounding,
)
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.vnext.targeted_repair import (
    DiagnosticTargetedRepairConfig,
    diagnose_temporal_evidence_selections,
    repair_temporal_evidence_selection_after_diagnostic,
)


E_CONFIG = {
    "enabled": True,
    "date_normalization": True,
    "datetime_normalization": True,
    "preserve_raw_evidence": True,
    "fail_closed_on_ambiguous_format": True,
}

F_CONFIG = {
    "enabled": True,
    "max_attempts_per_slot": 1,
    "require_unique_candidate": True,
    "preserve_non_reference_semantics": True,
    "emit_repair_provenance": True,
}

G2_CONFIG = {
    "enabled": True,
    "evidence_span_boundary": True,
    "evidence_span_selection": True,
    "selection_policy": "temporal_type_contradiction_forward_same_sentence",
    "allowed_terminal_punctuation": [".", ","],
    "max_revalidation_attempts": 1,
    "require_deterministic_diagnostic": True,
    "require_single_diagnosed_slot": True,
    "require_unique_candidate": True,
    "preserve_other_semantics": True,
    "preserve_effective_target_grounding": True,
    "emit_repair_provenance": True,
}


def _config() -> DiagnosticTargetedRepairConfig:
    return DiagnosticTargetedRepairConfig.from_mapping(G2_CONFIG)


def _profile(
    *,
    temporal_semantic: str = "text",
    require_event_id: bool = False,
) -> dict:
    profile = {
        "db_id": "stage2_g2",
        "tables": [
            {
                "name": "events",
                "columns": [
                    {
                        "name": "event_id",
                        "type": "TEXT",
                        "is_primary_key": require_event_id,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "timemark",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": temporal_semantic,
                        "preserve_as_text": True,
                    },
                    {
                        "name": "note",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "count",
                        "type": "INTEGER",
                        "is_insertable": True,
                        "semantic_type": "integer",
                    },
                ],
                "required_insert_columns": (
                    ["event_id"] if require_event_id else []
                ),
                "primary_keys": ["event_id"] if require_event_id else [],
                "unique_indexes": (
                    [
                        {
                            "name": "PRIMARY_KEY",
                            "columns": ["event_id"],
                            "origin": "pk",
                            "is_primary_key": True,
                        }
                    ]
                    if require_event_id
                    else []
                ),
                "foreign_keys": [],
            }
        ],
    }
    ensure_reference_ids(profile)
    return profile


def _effective_grounding_profile() -> dict:
    profile = {
        "db_id": "stage2_g2_effective_grounding",
        "tables": [
            {
                "name": "events",
                "columns": [
                    {
                        "name": "start_date",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": "date",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "end_date",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": "date",
                        "preserve_as_text": True,
                    },
                ],
                "required_insert_columns": [],
                "primary_keys": [],
                "unique_indexes": [],
                "foreign_keys": [],
            },
            {
                "name": "archive",
                "columns": [
                    {
                        "name": "archive_date",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": "date",
                        "preserve_as_text": True,
                    }
                ],
                "required_insert_columns": [],
                "primary_keys": [],
                "unique_indexes": [],
                "foreign_keys": [],
            },
        ],
    }
    ensure_reference_ids(profile)
    return profile


def _column_ids(profile: dict) -> dict[str, str]:
    return {
        item["name"]: item["column_id"]
        for item in profile["tables"][0]["columns"]
    }


def _plan(
    profile: dict,
    rows: list[dict[str, dict]],
) -> dict:
    return {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": profile["tables"][0]["table_id"],
                "rows": rows,
                "write_semantics": "plain_insert",
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }


def _selected(candidates: list[dict], text: str) -> dict:
    matches = [item for item in candidates if item["text"] == text]
    assert len(matches) == 1, (text, matches)
    return matches[0]


def _temporal_plan_for_request(
    request: str,
    selected_text: str,
    *,
    profile: dict | None = None,
    selected_id: str | None = None,
) -> tuple[dict, dict, list[dict], dict]:
    profile = profile or _profile()
    candidates = extract_evidence_candidates(request)
    selected = (
        next(
            item
            for item in candidates
            if item["evidence_id"] == selected_id
            and item["text"] == selected_text
        )
        if selected_id is not None
        else _selected(candidates, selected_text)
    )
    columns = _column_ids(profile)
    plan = _plan(
        profile,
        [
            {
                columns["timemark"]: {
                    "value_from": selected["evidence_id"],
                    "normalization": "iso_date_normalization",
                }
            }
        ],
    )
    return profile, plan, candidates, selected


def _effective_grounding_case(
    request: str,
) -> tuple[dict, dict, list[dict], dict]:
    profile = _effective_grounding_profile()
    candidates = extract_evidence_candidates(request)
    selected = _selected(candidates, "For")
    start_date = _column_ids(profile)["start_date"]
    plan = _plan(
        profile,
        [
            {
                start_date: {
                    "value_from": selected["evidence_id"],
                    "normalization": "iso_date_normalization",
                }
            }
        ],
    )
    return profile, plan, candidates, selected


def _materialization_errors(
    plan: dict,
    request: str,
    profile: dict,
) -> list:
    with pytest.raises(MaterializationError) as exc:
        materialize_reference_free_text_plan(
            plan,
            request,
            profile,
            free_text_typed_normalization=E_CONFIG,
        )
    return exc.value.diagnostics


def _diagnose(
    plan: dict,
    request: str,
    candidates: list[dict],
    profile: dict,
) -> list:
    return diagnose_temporal_evidence_selections(
        plan,
        request,
        candidates,
        profile,
        _materialization_errors(plan, request, profile),
        E_CONFIG,
        _config(),
    )


def _fixture() -> dict:
    return json.loads(
        Path(
            "tests/fixtures/stage2_g2_stage1_evidence_selection_cases.json"
        ).read_text(encoding="utf-8")
    )


def test_g2_v8_is_direct_v7_plus_selection_ablation() -> None:
    v7, _ = _load_method_config(
        Path("configs/stage2/v7_diagnostic_targeted_repair_g1.json")
    )
    v8, _ = _load_method_config(
        Path("configs/stage2/v8_diagnostic_targeted_repair_g2.json")
    )
    assert v8["method_variant"] == "vnext-v8-diagnostic-targeted-repair-g2"
    assert v8["method_version"] == (
        "stage2-v8-abcdefg1g2-temporal-evidence-selection"
    )
    for key, value in v7.items():
        if key in {
            "method_variant",
            "method_version",
            "diagnostic_targeted_repair",
        }:
            continue
        assert v8[key] == value, key
    assert v8["diagnostic_targeted_repair"] == G2_CONFIG


def test_g2_prompt_is_identical_to_frozen_g1_prompt() -> None:
    v7, _ = _load_method_config(
        Path("configs/stage2/v7_diagnostic_targeted_repair_g1.json")
    )
    v8, _ = _load_method_config(
        Path("configs/stage2/v8_diagnostic_targeted_repair_g2.json")
    )
    sample = {"input_text": "Use timemark For 2024-07-17 11:50:00."}
    prompt7, payload7 = _prompt_for_sample("MP-FS+", sample, _profile(), v7)
    prompt8, payload8 = _prompt_for_sample("MP-FS+", sample, _profile(), v8)
    assert prompt8 == prompt7
    assert payload8.to_dict() == payload7.to_dict()


def test_g2_rejects_non_frozen_selection_policy() -> None:
    with pytest.raises(ValueError):
        DiagnosticTargetedRepairConfig.from_mapping(
            {**G2_CONFIG, "selection_policy": "best_candidate"}
        )


def test_g2_effective_target_grounding_guard_cannot_be_disabled() -> None:
    with pytest.raises(ValueError):
        DiagnosticTargetedRepairConfig.from_mapping(
            {**G2_CONFIG, "preserve_effective_target_grounding": False}
        )


@pytest.mark.parametrize("case_index", [0, 1])
def test_g2_stage1_temporal_cases_have_one_closed_candidate(
    case_index: int,
) -> None:
    case = _fixture()["cases"][case_index]
    profile, plan, candidates, selected = _temporal_plan_for_request(
        case["request"],
        case["selected_evidence"]["text"],
        selected_id=case["selected_evidence"]["evidence_id"],
    )
    for key in ("evidence_id", "text", "start", "end", "candidate_type"):
        assert selected[key] == case["selected_evidence"][key]
    diagnostics = _diagnose(plan, case["request"], candidates, profile)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.error_code == "TEMPORAL_EVIDENCE_SELECTION_INCOMPATIBLE"
    assert diagnostic.details["old_candidate_type"] == "text"
    assert diagnostic.details["diagnostic_source"]["details"][
        "typed_error_code"
    ] == "TEMPORAL_EVIDENCE_TYPE_MISMATCH"
    assert len(diagnostic.details["candidate_set"]) == 1
    compatible = diagnostic.details["candidate_set"][0]
    for key in (
        "evidence_id",
        "text",
        "start",
        "end",
        "candidate_type",
        "candidate_role",
    ):
        assert compatible[key] == case["compatible_candidate"][key]
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert outcome.applied
    column_id = _column_ids(profile)["timemark"]
    assert outcome.plan["write_groups"][0]["rows"][0][column_id][
        "value_from"
    ] == case["compatible_candidate"]["evidence_id"]


def test_g2_excludes_component_and_other_sentence_candidates() -> None:
    case = _fixture()["cases"][0]
    profile, plan, candidates, _ = _temporal_plan_for_request(
        case["request"], "Attempt"
    )
    diagnostic = _diagnose(plan, case["request"], candidates, profile)[0]
    selected_ids = {
        item["evidence_id"] for item in diagnostic.details["candidate_set"]
    }
    assert selected_ids == {"e4"}
    assert {"e5", "e17", "e18"}.isdisjoint(selected_ids)


def test_g2_generic_text_selection_case_remains_out_of_scope() -> None:
    case = _fixture()["cases"][2]
    assert case["eligible_g2_patch1"] is False
    candidates = extract_evidence_candidates(case["request"])
    assert _selected(candidates, "Use")["evidence_id"] == "e1"
    assert diagnose_temporal_evidence_selections(
        {},
        case["request"],
        candidates,
        _profile(),
        [],
        E_CONFIG,
        _config(),
    ) == []


def test_g2_requires_frozen_stage_e_type_mismatch_diagnostic() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    errors = _materialization_errors(plan, request, profile)
    errors[0].details["typed_error_code"] = "AMBIGUOUS_FORMAT"
    assert diagnose_temporal_evidence_selections(
        plan,
        request,
        candidates,
        profile,
        errors,
        E_CONFIG,
        _config(),
    ) == []


def test_g2_requires_iso_date_normalization_slot() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    errors = _materialization_errors(plan, request, profile)
    column_id = _column_ids(profile)["timemark"]
    plan["write_groups"][0]["rows"][0][column_id]["normalization"] = "identity"
    assert diagnose_temporal_evidence_selections(
        plan,
        request,
        candidates,
        profile,
        errors,
        E_CONFIG,
        _config(),
    ) == []


def test_g2_excludes_temporal_candidate_before_selected_evidence() -> None:
    request = "Timestamp 2024-07-17 11:50:00 is followed by marker For."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["candidate_set"] == []
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.traces[0]["repair_rule"] == (
        "non_unique_compatible_candidate_set"
    )


def test_g2_target_type_validator_can_reduce_candidate_set_to_zero() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile = _profile(temporal_semantic="identifier")
    profile, plan, candidates, _ = _temporal_plan_for_request(
        request, "For", profile=profile
    )
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["target_semantic_type"] == "identifier"
    assert diagnostic.details["candidate_set"] == []


def test_g2_accepts_one_primary_date_when_no_datetime_encloses_it() -> None:
    request = "Use timemark For 2024-07-17."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert [
        (item["text"], item["candidate_type"])
        for item in diagnostic.details["candidate_set"]
    ] == [("2024-07-17", "date")]
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert outcome.applied


def test_g2_excludes_different_same_table_explicit_target() -> None:
    request = "Use start_date For end_date 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["candidate_set"] == []
    assert diagnostic.details["target_grounding_rejections"] == [
        {
            "evidence_id": _selected(candidates, "2024-07-17")[
                "evidence_id"
            ],
            "text": "2024-07-17",
            "reason": "candidate_target_grounding_mismatch",
            "diagnosed_column_id": _column_ids(profile)["start_date"],
            "diagnosed_column": "start_date",
            "explicit_column_id": _column_ids(profile)["end_date"],
            "explicit_column": "end_date",
        }
    ]
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan


def test_g2_keeps_same_explicit_target_eligible() -> None:
    request = "Use start_date For start_date 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["target_grounding_rejections"] == []
    assert len(diagnostic.details["candidate_set"]) == 1
    grounding = diagnostic.details["candidate_set"][0][
        "effective_target_grounding"
    ]
    assert grounding == {
        "kind": "same_diagnosed_column",
        "column_id": _column_ids(profile)["start_date"],
        "column": "start_date",
    }


def test_g2_keeps_candidate_without_explicit_target_eligible() -> None:
    request = "Use start_date For the timestamp 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["target_grounding_rejections"] == []
    assert len(diagnostic.details["candidate_set"]) == 1
    assert diagnostic.details["candidate_set"][0][
        "effective_target_grounding"
    ] == {"kind": "no_explicit_column"}


def test_g2_excludes_other_table_explicit_target() -> None:
    request = "Use start_date For archive_date 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["candidate_set"] == []
    rejection = diagnostic.details["target_grounding_rejections"]
    assert len(rejection) == 1
    assert rejection[0]["reason"] == (
        "candidate_cross_table_grounding_conflict"
    )
    assert rejection[0]["explicit_column_tables"] == ["archive"]


def test_g2_grounding_helper_matches_frozen_materializer_remap() -> None:
    request = "Use start_date For end_date 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    replacement = _selected(candidates, "2024-07-17")
    events = profile["tables"][0]
    explicit, owners = resolve_explicit_column_grounding(
        replacement,
        profile,
        events,
    )
    assert owners == []
    assert explicit is not None
    assert explicit["column_id"] == _column_ids(profile)["end_date"]

    manually_replaced = deepcopy(plan)
    manually_replaced["write_groups"][0]["rows"][0][
        _column_ids(profile)["start_date"]
    ]["value_from"] = replacement["evidence_id"]
    materialized = materialize_reference_free_text_plan(
        manually_replaced,
        request,
        profile,
        free_text_typed_normalization=E_CONFIG,
    )
    group = materialized["write_groups"][0]
    assert group["rows"] == [{"end_date": "2024-07-17"}]
    assert group["reference_trace"]["evidence_column_groundings"] == [
        {
            "row_index": 0,
            "evidence_id": replacement["evidence_id"],
            "from_column_id": _column_ids(profile)["start_date"],
            "to_column_id": _column_ids(profile)["end_date"],
            "to_column": "end_date",
            "reason": "immediately_preceding_exact_identifier",
        }
    ]


def test_g2_pipeline_fails_closed_before_different_target_remap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = "Use start_date For end_date 2024-07-17."
    profile, plan, _, _ = _effective_grounding_case(request)
    original = pipeline_module.materialize_reference_free_text_plan
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_module,
        "materialize_reference_free_text_plan",
        counted,
    )
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "diagnostic_targeted_repair"
    assert result.write_plan is None
    assert result.program is None
    assert calls == 1
    diagnostic = result.verification.errors[0]
    assert diagnostic.details["candidate_set"] == []
    assert diagnostic.details["targeted_repair"]["repair_applied"] is False
    assert diagnostic.details["targeted_repair"][
        "target_grounding_rejections"
    ][0]["reason"] == "candidate_target_grounding_mismatch"


def test_g2_excludes_old_evidence_grounded_to_different_column() -> None:
    request = "Use end_date For 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["candidate_set"] == []
    grounding = diagnostic.details[
        "selected_evidence_effective_target_grounding"
    ]
    assert grounding == {
        "kind": "different_same_table_column",
        "diagnosed_column_id": _column_ids(profile)["start_date"],
        "diagnosed_column": "start_date",
        "explicit_column_id": _column_ids(profile)["end_date"],
        "explicit_column": "end_date",
        "reason": "source_evidence_effective_target_mismatch",
    }
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["repair_rule"] == (
        "invalid_source_grounding_provenance"
    )


def test_g2_keeps_old_evidence_grounded_to_diagnosed_column() -> None:
    request = "Use start_date For 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    grounding = diagnostic.details[
        "selected_evidence_effective_target_grounding"
    ]
    assert grounding == {
        "kind": "same_diagnosed_column",
        "column_id": _column_ids(profile)["start_date"],
        "column": "start_date",
    }
    assert len(diagnostic.details["candidate_set"]) == 1


def test_g2_keeps_old_evidence_without_explicit_target() -> None:
    request = "Marker For 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details[
        "selected_evidence_effective_target_grounding"
    ] == {"kind": "no_explicit_column"}
    assert len(diagnostic.details["candidate_set"]) == 1


def test_g2_excludes_old_evidence_with_cross_table_target() -> None:
    request = "Use archive_date For 2024-07-17."
    profile, plan, candidates, _ = _effective_grounding_case(request)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert diagnostic.details["candidate_set"] == []
    grounding = diagnostic.details[
        "selected_evidence_effective_target_grounding"
    ]
    assert grounding["kind"] == "cross_table_only_column"
    assert grounding["explicit_column_tables"] == ["archive"]
    assert grounding["reason"] == (
        "selected_evidence_cross_table_grounding_conflict"
    )


def test_g2_pipeline_fails_before_retry_when_old_target_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = "Use end_date For 2024-07-17."
    profile, plan, _, _ = _effective_grounding_case(request)
    original = pipeline_module.materialize_reference_free_text_plan
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_module,
        "materialize_reference_free_text_plan",
        counted,
    )
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "diagnostic_targeted_repair"
    assert result.write_plan is None
    assert result.program is None
    assert calls == 1
    targeted = result.verification.errors[0].details["targeted_repair"]
    assert targeted["repair_applied"] is False
    assert targeted["repair_rule"] == "invalid_source_grounding_provenance"
    assert targeted["selected_evidence_effective_target_grounding"][
        "reason"
    ] == "source_evidence_effective_target_mismatch"


def test_g2_two_primary_temporal_candidates_fail_closed() -> None:
    request = (
        "Use timemark For 2024-07-17 11:50:00 and "
        "2025-08-18 12:51:01."
    )
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    assert len(diagnostic.details["candidate_set"]) == 2
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["repair_rule"] == (
        "non_unique_compatible_candidate_set"
    )


def test_g2_deep_copy_changes_only_one_value_from() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, selected = _temporal_plan_for_request(
        request, "For"
    )
    original = deepcopy(plan)
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert outcome.applied
    assert plan == original
    replacement = diagnostic.details["candidate_set"][0]["evidence_id"]
    expected = deepcopy(original)
    column_id = _column_ids(profile)["timemark"]
    expected["write_groups"][0]["rows"][0][column_id][
        "value_from"
    ] = replacement
    assert outcome.plan == expected
    assert selected["evidence_id"] != replacement


def test_g2_repair_rejects_forged_source_diagnostic() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    diagnostic.details["diagnostic_source"]["details"][
        "typed_error_code"
    ] = "SYNTHETIC_DIAGNOSTIC"
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["repair_rule"] == (
        "invalid_deterministic_source_diagnostic"
    )


def test_g2_repair_rejects_forged_effective_target_grounding() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    diagnostic.details["candidate_set"][0][
        "effective_target_grounding"
    ] = {
        "kind": "same_diagnosed_column",
        "column_id": "different-column-id",
        "column": "other",
    }
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["repair_rule"] == "invalid_closed_candidate"


def test_g2_repair_rejects_forged_old_evidence_grounding() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    diagnostic.details[
        "selected_evidence_effective_target_grounding"
    ] = {
        "kind": "different_same_table_column",
        "column_id": "different-column-id",
    }
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["repair_rule"] == (
        "invalid_source_grounding_provenance"
    )


def test_g2_multiple_diagnosed_slots_fail_closed() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, selected = _temporal_plan_for_request(
        request, "For"
    )
    column_id = _column_ids(profile)["timemark"]
    plan["write_groups"][0]["rows"].append(
        {
            column_id: {
                "value_from": selected["evidence_id"],
                "normalization": "iso_date_normalization",
            }
        }
    )
    diagnostics = _diagnose(plan, request, candidates, profile)
    assert len(diagnostics) == 2
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, diagnostics, _config()
    )
    assert not outcome.applied
    assert all(
        trace["repair_rule"] == "multiple_diagnosed_slots"
        for trace in outcome.traces
    )


def test_g2_replacement_evidence_collision_fails_closed() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    replacement = diagnostic.details["candidate_set"][0]["evidence_id"]
    plan["write_groups"][0]["rows"][0][_column_ids(profile)["note"]] = {
        "value_from": replacement,
        "normalization": "identity",
    }
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.traces[0]["repair_rule"] == (
        "replacement_evidence_reference_collision"
    )


def test_g2_pipeline_repairs_temporal_selection_and_emits_provenance() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, _, selected = _temporal_plan_for_request(request, "For")
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert result.success
    assert result.program is not None
    assert result.program.statements[0].params == ["2024-07-17 11:50:00"]
    group = result.write_plan["write_groups"][0]
    trace = group["reference_trace"]["diagnostic_targeted_repairs"][0]
    assert trace["stage2_intervention"] == (
        "G2_temporal_evidence_selection_repair"
    )
    assert trace["old_reference"] == selected["evidence_id"]
    assert trace["old_value"] == "For"
    assert trace["old_candidate_type"] == "text"
    assert trace["repair_rule"] == (
        "unique_primary_temporal_candidate_in_forward_same_sentence"
    )
    assert trace["repair_applied"] is True
    assert trace["repair_succeeded"] is True
    assert trace["revalidation_result"] == "PASS"
    assert trace["revalidation_attempts"] == 1
    assert trace["atomic_rollback"] is False
    assert group["value_evidence"][0]["timemark"]["exact_span"] == (
        "2024-07-17 11:50:00"
    )


def test_g2_pipeline_retries_materializer_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, _, _ = _temporal_plan_for_request(request, "For")
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
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert result.success
    assert calls == 2


def test_g2_retry_error_fails_closed_without_recursive_repair() -> None:
    request = (
        "Use timemark For 2024-07-17 11:50:00 and set count to Bad."
    )
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    bad = _selected(candidates, "Bad")
    plan["write_groups"][0]["rows"][0][_column_ids(profile)["count"]] = {
        "value_from": bad["evidence_id"],
        "normalization": "lossless_integer_parsing",
    }
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "diagnostic_targeted_revalidation"
    assert result.write_plan is None
    assert any(
        item.error_code == "LOSSY_NORMALIZATION_REJECTED"
        for item in result.verification.errors
    )
    warning = next(
        item
        for item in result.verification.warnings
        if item.error_code == "DIAGNOSTIC_TARGETED_REPAIR_APPLIED"
    )
    assert warning.details["repair_succeeded"] is False
    assert warning.details["atomic_rollback"] is True
    assert warning.details["revalidation_attempts"] == 1


def test_g2_verifier_failure_rolls_back_to_no_materialized_plan() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile = _profile(require_event_id=True)
    profile, plan, _, _ = _temporal_plan_for_request(
        request, "For", profile=profile
    )
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "verification"
    assert result.write_plan is None
    warning = next(
        item
        for item in result.verification.warnings
        if item.error_code == "DIAGNOSTIC_TARGETED_REPAIR_APPLIED"
    )
    assert warning.details["repair_succeeded"] is False
    assert warning.details["atomic_rollback"] is True
    assert "MISSING_REQUIRED_COLUMN" in warning.details[
        "revalidation_error_codes"
    ]


def test_g2_does_not_chain_with_g1_in_same_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = "Use event EVT1. For timemark use 2024-07-17 11:50:00."
    profile = _profile()
    candidates = extract_evidence_candidates(request)
    boundary = _selected(candidates, "EVT1.")
    wrong = _selected(candidates, "For")
    columns = _column_ids(profile)
    plan = _plan(
        profile,
        [
            {
                columns["event_id"]: {
                    "value_from": boundary["evidence_id"],
                    "normalization": "identity",
                },
                columns["timemark"]: {
                    "value_from": wrong["evidence_id"],
                    "normalization": "iso_date_normalization",
                },
            }
        ],
    )
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
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "diagnostic_targeted_revalidation"
    assert result.write_plan is None
    assert calls == 2
    assert any(
        item.error_code == "EVIDENCE_SPAN_TERMINAL_PUNCTUATION"
        for item in result.verification.errors
    )


def test_g2_disabled_preserves_frozen_g1_failure() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, _, _ = _temporal_plan_for_request(request, "For")
    disabled = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair={
            **G2_CONFIG,
            "evidence_span_selection": False,
        },
    ).run(request, plan)
    enabled = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        constrained_reference_repair=F_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert not disabled.success
    assert disabled.stage == "evidence_materialization"
    assert any(
        item.error_code == "TYPED_NORMALIZATION_REJECTED"
        for item in disabled.verification.errors
    )
    assert enabled.success


def test_g2_trace_contains_reviewer_requested_provenance() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile, plan, candidates, _ = _temporal_plan_for_request(request, "For")
    diagnostic = _diagnose(plan, request, candidates, profile)[0]
    outcome = repair_temporal_evidence_selection_after_diagnostic(
        plan, [diagnostic], _config()
    )
    trace = outcome.traces[0]
    required = {
        "diagnostic",
        "diagnostic_source",
        "diagnosed_slot",
        "old_reference",
        "old_value",
        "old_candidate_type",
        "selected_evidence_effective_target_grounding",
        "candidate_set",
        "selected_repair",
        "repair_rule",
        "selection_policy",
        "context_window",
        "repair_attempted",
        "repair_applied",
        "repair_succeeded",
        "revalidation_result",
        "revalidation_attempts",
        "atomic_rollback",
    }
    assert required <= set(trace)
    assert trace["repair_applied"] is True
    assert trace["repair_succeeded"] is False
    assert trace["revalidation_result"] == "NOT_RUN"
