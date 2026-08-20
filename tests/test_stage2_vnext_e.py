from __future__ import annotations

import json
from pathlib import Path

import pytest

from nldbwrite_v3.experiments.run_method import _load_method_config, _prompt_for_sample
from nldbwrite_v3.planner.evidence import (
    extract_evidence_candidates,
    materialize_reference_free_text_plan,
)
from nldbwrite_v3.planner.materialize import MaterializationError
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.vnext.typed_normalization import (
    FreeTextTypedNormalizationConfig,
    normalize_free_text_typed_candidate,
)


E_CONFIG = {
    "enabled": True,
    "date_normalization": True,
    "datetime_normalization": True,
    "preserve_raw_evidence": True,
    "fail_closed_on_ambiguous_format": True,
}


def _temporal_column(
    *,
    column_type: str = "TEXT",
    semantic_type: str = "text",
) -> dict:
    return {
        "name": "observed_at",
        "type": column_type,
        "semantic_type": semantic_type,
        "preserve_as_text": True,
        "is_insertable": True,
    }


def _profile() -> dict:
    profile = {
        "db_id": "stage2_e",
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
                        "name": "observed_at",
                        "type": "TEXT",
                        "not_null": True,
                        "is_insertable": True,
                        "semantic_type": "text",
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
                "required_insert_columns": ["event_id", "observed_at"],
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


def _ids(profile: dict) -> tuple[str, dict[str, str]]:
    table = profile["tables"][0]
    return str(table["table_id"]), {
        str(column["name"]): str(column["column_id"])
        for column in table["columns"]
    }


def _evidence_id(request: str, surface: str) -> str:
    matches = [
        row
        for row in extract_evidence_candidates(request)
        if row["text"] == surface
    ]
    assert matches, (surface, extract_evidence_candidates(request))
    return str(matches[0]["evidence_id"])


def _plan_for(
    request: str,
    profile: dict,
    *,
    event_id_surface: str,
    temporal_surface: str,
    temporal_rule: str = "iso_date_normalization",
) -> dict:
    table_id, columns = _ids(profile)
    return {
        "version": "3.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": table_id,
                "rows": [
                    {
                        columns["event_id"]: {
                            "value_from": _evidence_id(request, event_id_surface),
                            "normalization": "identity",
                        },
                        columns["observed_at"]: {
                            "value_from": _evidence_id(request, temporal_surface),
                            "normalization": temporal_rule,
                        },
                    }
                ],
                "write_semantics": "plain_insert",
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }


def _typed(
    value: str,
    *,
    rule: str = "iso_date_normalization",
    column_type: str = "TEXT",
    semantic_type: str = "text",
    candidate_type: str | None = None,
):
    selected_candidate_type = candidate_type
    if selected_candidate_type is None:
        selected_candidate_type = "datetime" if " " in value or "T" in value else "date"
    return normalize_free_text_typed_candidate(
        value,
        _temporal_column(column_type=column_type, semantic_type=semantic_type),
        requested_rule=rule,
        candidate_type=selected_candidate_type,
        config=E_CONFIG,
        evidence_id="e1",
        evidence_start=10,
        evidence_end=10 + len(value),
    )


def test_v5_config_is_direct_v4_plus_e_ablation() -> None:
    v4, _ = _load_method_config(Path("configs/stage2/v4_structured_parser.json"))
    v5, _ = _load_method_config(Path("configs/stage2/v5_free_text_typed_normalization.json"))
    assert v5["method_id"] == "MP-FS+"
    assert v5["stage2_interventions"] == v4["stage2_interventions"]
    assert v5["structured_source_parser"] == v4["structured_source_parser"]
    assert v5["free_text_typed_normalization"] == E_CONFIG
    for key, value in v4.items():
        if key in {"method_variant", "method_version"}:
            continue
        assert v5[key] == value, key


def test_e_accepts_stage1_style_datetime_without_mutating_surface() -> None:
    result = _typed("2026-07-30 14:47:00")
    assert result.handled and result.error is None
    assert result.value == "2026-07-30 14:47:00"
    assert result.audit["semantic_type"] == "datetime"
    assert result.audit["normalization_confidence"] == "high"
    assert result.audit["raw_evidence_span"] == "2026-07-30 14:47:00"
    assert result.audit["evidence_start"] == 10
    assert result.audit["intervention_applied"] is True
    assert result.audit["applied"] is True
    assert result.audit["value_changed"] is False
    assert result.audit["accepted"] is True
    assert result.audit["outcome"] == "ACCEPT"


def test_e_accepts_fractional_datetime_precision_used_by_stage1() -> None:
    result = _typed("2024-10-29 17:30:55.954446")
    assert result.handled and result.error is None
    assert result.value == "2024-10-29 17:30:55.954446"


def test_e_canonicalizes_unambiguous_year_first_date_and_single_boundary_punctuation() -> None:
    slash = _typed("2026/08/19")
    dotted = _typed("2026-08-19.")
    assert slash.value == "2026-08-19"
    assert slash.audit["normalization_rule"] == "free_text_date_canonical_year_first"
    assert dotted.value == "2026-08-19"
    assert dotted.audit["sentence_boundary_punctuation"] == "."


def test_e_datetime_boundary_punctuation_is_removed_once() -> None:
    result = _typed("2026-08-19 12:30:00.")
    assert result.handled and result.error is None
    assert result.value == "2026-08-19 12:30:00"
    assert result.audit["normalization_rule"] == "free_text_datetime_sentence_boundary_punctuation"


def test_e_identity_text_does_not_trigger_typed_normalization_or_punctuation_strip() -> None:
    result = _typed("SC9081.", rule="identity")
    assert result.handled is False
    assert result.value == "SC9081."


@pytest.mark.parametrize(
    "value,candidate_type",
    [
        ("01/02/2026", "date"),
        ("01/02/03", "date"),
        ("2026-13-40", "date"),
        ("2026-08-19..", "date"),
    ],
)
def test_e_ambiguous_or_invalid_temporal_value_fails_closed(
    value: str,
    candidate_type: str,
) -> None:
    result = _typed(value, candidate_type=candidate_type)
    assert result.handled is True
    assert result.error_code == "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_FORMAT"
    assert result.value == value
    assert result.audit["lossless"] is False
    assert result.audit["intervention_applied"] is True
    assert result.audit["accepted"] is False



def test_e_rejects_temporal_rule_for_clearly_numeric_target() -> None:
    result = _typed("2026-08-19", column_type="INTEGER")
    assert result.handled
    assert result.error_code == "TEMPORAL_TARGET_TYPE_MISMATCH"


def test_e_materializer_accepts_datetime_that_v4_iso_date_rule_rejects() -> None:
    request = "Create event EVT1 observed at 2026-07-30 14:47:00."
    profile = _profile()
    plan = _plan_for(
        request,
        profile,
        event_id_surface="EVT1",
        temporal_surface="2026-07-30 14:47:00",
    )
    materialized = materialize_reference_free_text_plan(
        plan,
        request,
        profile,
        free_text_typed_normalization=E_CONFIG,
    )
    group = materialized["write_groups"][0]
    assert group["rows"][0]["observed_at"] == "2026-07-30 14:47:00"
    audit = group["normalization_audit"][0]["observed_at"]
    assert audit["stage2_intervention"] == "E_free_text_typed_normalization"
    assert audit["semantic_type"] == "datetime"
    assert audit["raw_evidence_span"] == "2026-07-30 14:47:00"


def test_e_disabled_is_exact_v4_behavior_for_datetime_iso_date_rule() -> None:
    request = "Create event EVT1 observed at 2026-07-30 14:47:00."
    profile = _profile()
    plan = _plan_for(
        request,
        profile,
        event_id_surface="EVT1",
        temporal_surface="2026-07-30 14:47:00",
    )
    with pytest.raises(MaterializationError) as old_error:
        materialize_reference_free_text_plan(plan, request, profile)
    with pytest.raises(MaterializationError) as disabled_error:
        materialize_reference_free_text_plan(
            plan,
            request,
            profile,
            free_text_typed_normalization={"enabled": False},
        )
    assert [row.to_dict() for row in old_error.value.diagnostics] == [
        row.to_dict() for row in disabled_error.value.diagnostics
    ]
    assert old_error.value.diagnostics[0].error_code == "LOSSY_NORMALIZATION_REJECTED"


def test_e_wrong_evidence_remains_fail_closed_not_repaired() -> None:
    request = "Create event EVT1 observed at For."
    profile = _profile()
    plan = _plan_for(
        request,
        profile,
        event_id_surface="EVT1",
        temporal_surface="For",
    )
    with pytest.raises(MaterializationError) as exc:
        materialize_reference_free_text_plan(
            plan,
            request,
            profile,
            free_text_typed_normalization=E_CONFIG,
        )
    diagnostic = exc.value.diagnostics[0]
    assert diagnostic.error_code == "TYPED_NORMALIZATION_REJECTED"
    assert diagnostic.details["typed_error_code"] == "TEMPORAL_EVIDENCE_TYPE_MISMATCH"
    assert diagnostic.details["raw_value"] == "For"


def test_e_stage1_date_audit_is_regression_fixture_not_rescue_claim() -> None:
    fixture = json.loads(
        Path("tests/fixtures/stage2_e_stage1_normalization_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["claim_scope"].startswith("diagnostic/regression evidence")
    assert len(fixture["cases"]) == 14
    passed = rejected = 0
    for case in fixture["cases"]:
        result = normalize_free_text_typed_candidate(
            case["raw_value"],
            _temporal_column(
                column_type=case["target_column_type"],
                semantic_type=case["target_semantic_type"],
            ),
            requested_rule=case["requested_rule"],
            candidate_type=case["candidate_type"],
            config=E_CONFIG,
        )
        if case["expected_status"] == "pass":
            assert result.error is None, case["case_id"]
            assert result.value == case["expected_normalized_value"], case["case_id"]
            passed += 1
        else:
            assert result.error_code == "TEMPORAL_EVIDENCE_TYPE_MISMATCH", case["case_id"]
            rejected += 1
    assert (passed, rejected) == (12, 2)


def test_e_rejects_identifier_semantic_target() -> None:
    result = _typed("2026-08-19", semantic_type="identifier", candidate_type="date")
    assert result.handled
    assert result.error_code == "TEMPORAL_TARGET_SEMANTIC_MISMATCH"
    assert result.audit["accepted"] is False


def test_e_rejects_boolean_semantic_target() -> None:
    result = _typed(
        "2026-08-19",
        column_type="BOOLEAN",
        semantic_type="boolean",
        candidate_type="date",
    )
    assert result.handled
    assert result.error_code == "TEMPORAL_TARGET_SEMANTIC_MISMATCH"


def test_e_rejects_json_semantic_target() -> None:
    result = _typed(
        "2026-08-19",
        column_type="JSON",
        semantic_type="json",
        candidate_type="date",
    )
    assert result.handled
    assert result.error_code == "TEMPORAL_TARGET_SEMANTIC_MISMATCH"


def test_e_allows_text_temporal_storage() -> None:
    result = _typed(
        "2026-08-19",
        column_type="TEXT",
        semantic_type="text",
        candidate_type="date",
    )
    assert result.handled and result.error is None
    assert result.value == "2026-08-19"


def test_e_datetime_acceptance_marks_intervention_applied_even_when_value_unchanged() -> None:
    result = _typed("2026-07-30 14:47:00", candidate_type="datetime")
    assert result.error is None
    assert result.audit["intervention_applied"] is True
    assert result.audit["applied"] is True
    assert result.audit["value_changed"] is False
    assert result.audit["accepted"] is True
    assert result.audit["outcome"] == "ACCEPT"


def test_e_slash_date_marks_value_changed() -> None:
    result = _typed("2026/08/19", candidate_type="date")
    assert result.error is None
    assert result.value == "2026-08-19"
    assert result.audit["applied"] is True
    assert result.audit["value_changed"] is True


def test_e_missing_candidate_type_fails_closed() -> None:
    result = _typed("2026-08-19", candidate_type="")
    assert result.handled
    assert result.error_code == "TEMPORAL_EVIDENCE_TYPE_MISSING"
    assert result.audit["applied"] is True
    assert result.audit["accepted"] is False


def test_e_date_candidate_cannot_normalize_datetime() -> None:
    result = _typed("2026-08-19 12:30:00", candidate_type="date")
    assert result.handled
    assert result.error_code == "TEMPORAL_EVIDENCE_SUBTYPE_MISMATCH"


def test_e_datetime_candidate_cannot_normalize_date() -> None:
    result = _typed("2026-08-19", candidate_type="datetime")
    assert result.handled
    assert result.error_code == "TEMPORAL_EVIDENCE_SUBTYPE_MISMATCH"


def test_e_quoted_text_candidate_is_not_temporal() -> None:
    result = _typed("2026-08-19", candidate_type="quoted_text")
    assert result.handled
    assert result.error_code == "TEMPORAL_EVIDENCE_TYPE_MISMATCH"


def test_e_fullwidth_digits_are_not_accepted_as_canonical_temporal() -> None:
    result = _typed("２０２６-０８-１９", candidate_type="date")
    assert result.handled
    assert result.error_code == "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_FORMAT"


def test_e_raw_evidence_preservation_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="preserve_raw_evidence=true"):
        FreeTextTypedNormalizationConfig.from_mapping(
            {"enabled": True, "preserve_raw_evidence": False}
        )


def test_e_v5_prompt_is_identical_to_v4_prompt() -> None:
    v4, _ = _load_method_config(Path("configs/stage2/v4_structured_parser.json"))
    v5, _ = _load_method_config(Path("configs/stage2/v5_free_text_typed_normalization.json"))
    sample = {
        "input_text": "Create event EVT1 observed at 2026-07-30 14:47:00."
    }
    profile = _profile()
    prompt4, payload4 = _prompt_for_sample("MP-FS+", sample, profile, v4)
    prompt5, payload5 = _prompt_for_sample("MP-FS+", sample, profile, v5)
    assert prompt5 == prompt4
    assert payload5.to_dict() == payload4.to_dict()
