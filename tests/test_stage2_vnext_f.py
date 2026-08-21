from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from nldbwrite_v3.experiments.run_method import _load_method_config, _prompt_for_sample
from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner.evidence import extract_evidence_candidates
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.vnext.reference_repair import (
    ConstrainedReferenceRepairConfig,
    annotate_reference_diagnostics,
    attempt_constrained_reference_repair,
    mark_revalidation_outcome,
    repair_free_text_plan_after_diagnostics,
    repair_mapping_plan_after_diagnostics,
)
from tests.helpers import test_profile


F_CONFIG = {
    "enabled": True,
    "max_attempts_per_slot": 1,
    "require_unique_candidate": True,
    "preserve_non_reference_semantics": True,
    "emit_repair_provenance": True,
}


def _config() -> ConstrainedReferenceRepairConfig:
    return ConstrainedReferenceRepairConfig.from_mapping(F_CONFIG)


def _payload(row: dict[str, object], collection_id: str = "records") -> SourcePayload:
    collection = SourceCollection(
        collection_id=collection_id,
        source_path="$[*]",
        source_format="json_array",
        rows=[row],
        fields=list(row),
        reference_id="c1",
        selector_id="s1",
        field_ids={field: f"c1.f{i}" for i, field in enumerate(row, start=1)},
        metadata={"control_metadata": []},
    )
    return SourcePayload(
        mode="semi_structured",
        source_format="json_array",
        collections=[collection],
        instruction_text="",
        raw_text="",
    )


def _semi_profile_and_plan() -> tuple[dict, SourcePayload, dict, dict, dict]:
    profile = test_profile()
    ensure_reference_ids(profile)
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    columns = {c["name"]: c["column_id"] for c in parent["columns"]}
    payload = _payload({"id": "p1", "name": "One"})
    collection = payload.collections[0]
    plan = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection_id": collection.reference_id,
                "source_selector_id": collection.selector_id,
                "table_id": parent["table_id"],
                "field_mapping": {
                    collection.field_ids["id"]: columns["id"],
                    collection.field_ids["name"]: columns["name"],
                },
                "constants": {},
                "write_semantics": "plain_insert",
                "conflict_target_id": None,
                "update_column_ids": [],
                "deduplicate_projected_rows": True,
                "require_existing_row_match": False,
            }
        ],
        "ignored_fields": {},
        "dependencies": [],
    }
    return profile, payload, plan, parent, columns


def _free_text_profile() -> dict:
    profile = {
        "db_id": "stage2_f",
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


def _free_text_plan(
    request: str,
    profile: dict,
    *,
    raw_column: str | None = None,
    evidence_id: str | None = None,
    raw_table: str | None = None,
) -> dict:
    table = profile["tables"][0]
    columns = {c["name"]: c["column_id"] for c in table["columns"]}
    candidates = extract_evidence_candidates(request)
    selected = evidence_id or candidates[0]["evidence_id"]
    column = raw_column or columns["event_id"]
    return {
        "version": "3.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": raw_table or table["table_id"],
                "rows": [
                    {column: {"value_from": selected, "normalization": "identity"}}
                ],
                "write_semantics": "plain_insert",
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }


def _diag(
    code: str,
    path: str,
    candidates: list[str],
    *,
    details: dict | None = None,
) -> Diagnostic:
    return Diagnostic(
        code,
        code,
        path=path,
        candidates=candidates,
        details=details or {},
    )


def test_f_v6_is_direct_v5_plus_f_ablation() -> None:
    v5, _ = _load_method_config(
        Path("configs/stage2/v5_free_text_typed_normalization.json")
    )
    v6, _ = _load_method_config(
        Path("configs/stage2/v6_constrained_reference_repair.json")
    )
    assert v6["method_variant"] == "vnext-v6-constrained-reference-repair"
    assert v6["method_version"] == "stage2-v6-abcdef-constrained-reference-repair"
    assert v6["constrained_reference_repair"] == F_CONFIG
    for key, value in v5.items():
        if key in {"method_variant", "method_version"}:
            continue
        assert v6[key] == value, key


def test_f_v6_prompt_is_identical_to_v5_prompt() -> None:
    v5, _ = _load_method_config(
        Path("configs/stage2/v5_free_text_typed_normalization.json")
    )
    v6, _ = _load_method_config(
        Path("configs/stage2/v6_constrained_reference_repair.json")
    )
    sample = {"input_text": "Create code Z9"}
    profile = _free_text_profile()
    prompt5, payload5 = _prompt_for_sample("MP-FS+", sample, profile, v5)
    prompt6, payload6 = _prompt_for_sample("MP-FS+", sample, profile, v6)
    assert prompt6 == prompt5
    assert payload6.to_dict() == payload5.to_dict()


def test_f_exact_identifier_name_uniquely_repairs_invalid_reference() -> None:
    result = attempt_constrained_reference_repair(
        "t5.userregistry",
        ["t5.c1", "t5.c7"],
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        named_references={"t5.c1": "nicklabel", "t5.c7": "userregistry"},
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert result.applied
    assert result.replacement == "t5.c7"
    assert result.trace["repair_rule"] == "unique_exact_identifier_name"
    assert result.trace["repair_succeeded"] is False
    assert result.trace["validation_after"] == "PENDING_REVALIDATION"


def test_f_identifier_matching_is_casefold_exact_and_quote_tolerant() -> None:
    result = attempt_constrained_reference_repair(
        "t5.[UserRegistry]",
        ["t5.c1", "t5.c7"],
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        named_references={"t5.c1": "nicklabel", "t5.c7": "userregistry"},
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert result.replacement == "t5.c7"


def test_f_does_not_use_fuzzy_or_punctuation_collapsing() -> None:
    result = attempt_constrained_reference_repair(
        "t5.userregistry",
        ["t5.c1", "t5.c7"],
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        named_references={"t5.c1": "other", "t5.c7": "user_registry"},
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert not result.applied
    assert result.trace["repair_rule"] == "ambiguous_closed_set"


def test_f_singleton_closed_set_can_select_one_invalid_reference_replacement() -> None:
    result = attempt_constrained_reference_repair(
        "bad",
        ["only"],
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert result.replacement == "only"
    assert result.trace["repair_rule"] == "unique_closed_set_candidate"


@pytest.mark.parametrize("candidates", [[], ["c1", "c2"]])
def test_f_zero_or_ambiguous_closed_set_fails_closed(candidates: list[str]) -> None:
    result = attempt_constrained_reference_repair(
        "bad",
        candidates,
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert result.attempted
    assert not result.applied
    assert result.trace["validation_after"] == "FAIL_CLOSED"


def test_f_valid_reference_is_never_repaired() -> None:
    result = attempt_constrained_reference_repair(
        "c1",
        ["c1", "c2"],
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        validation_before="PASS",
    )
    assert not result.attempted
    assert not result.applied
    assert result.trace["repair_rule"] == "already_valid_reference"


def test_f_missing_reference_is_not_repair_attempted_or_autofilled() -> None:
    result = attempt_constrained_reference_repair(
        "",
        ["only"],
        reference_kind="column",
        slot_path="/x",
        config=_config(),
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert not result.attempted
    assert not result.applied
    assert result.trace["repair_rule"] == "missing_reference_not_repairable"


@pytest.mark.parametrize(
    "override,match",
    [
        ({"max_attempts_per_slot": 2}, "max_attempts_per_slot=1"),
        ({"require_unique_candidate": False}, "unique-candidate"),
        ({"preserve_non_reference_semantics": False}, "preserve non-reference"),
        ({"emit_repair_provenance": False}, "provenance is mandatory"),
    ],
)
def test_f_safety_config_cannot_be_relaxed(override: dict, match: str) -> None:
    cfg = dict(F_CONFIG)
    cfg.update(override)
    with pytest.raises(ValueError, match=match):
        ConstrainedReferenceRepairConfig.from_mapping(cfg)


def test_f_mapping_repair_exact_column_name_changes_only_reference_slot() -> None:
    profile, payload, plan, parent, columns = _semi_profile_and_plan()
    collection = payload.collections[0]
    revised = deepcopy(plan)
    raw = f"{parent['table_id']}.name"
    revised["target_groups"][0]["field_mapping"][collection.field_ids["name"]] = raw
    original = deepcopy(revised)
    diagnostic = _diag(
        "UNKNOWN_COLUMN_ID",
        f"/target_groups/0/field_mapping/{collection.field_ids['name']}",
        list(columns.values()),
        details={"predicted_column_id": raw},
    )
    outcome = repair_mapping_plan_after_diagnostics(
        revised, payload, profile, [diagnostic], _config()
    )
    assert revised == original
    assert outcome.applied
    assert (
        outcome.plan["target_groups"][0]["field_mapping"][collection.field_ids["name"]]
        == columns["name"]
    )
    group = outcome.plan["target_groups"][0]
    assert group["write_semantics"] == "plain_insert"
    assert group["conflict_target_id"] is None
    assert group["update_column_ids"] == []


def test_f_mapping_repair_ambiguous_column_fails_closed() -> None:
    profile, payload, plan, parent, columns = _semi_profile_and_plan()
    collection = payload.collections[0]
    raw = f"{parent['table_id']}.nam"
    revised = deepcopy(plan)
    revised["target_groups"][0]["field_mapping"][collection.field_ids["name"]] = raw
    diagnostic = _diag(
        "UNKNOWN_COLUMN_ID",
        f"/target_groups/0/field_mapping/{collection.field_ids['name']}",
        list(columns.values()),
        details={"predicted_column_id": raw},
    )
    outcome = repair_mapping_plan_after_diagnostics(
        revised, payload, profile, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == revised
    assert outcome.traces[0]["repair_rule"] == "ambiguous_closed_set"


def test_f_mapping_repair_exact_source_field_name_is_slot_local() -> None:
    profile, payload, plan, _, _ = _semi_profile_and_plan()
    collection = payload.collections[0]
    revised = deepcopy(plan)
    target = revised["target_groups"][0]["field_mapping"].pop(collection.field_ids["id"])
    revised["target_groups"][0]["field_mapping"]["c9.id"] = target
    diagnostic = _diag(
        "UNKNOWN_SOURCE_FIELD_ID",
        "/target_groups/0/field_mapping/c9.id",
        list(collection.field_ids.values()),
    )
    outcome = repair_mapping_plan_after_diagnostics(
        revised, payload, profile, [diagnostic], _config()
    )
    assert outcome.applied
    mapping = outcome.plan["target_groups"][0]["field_mapping"]
    assert "c9.id" not in mapping
    assert collection.field_ids["id"] in mapping
    assert mapping[collection.field_ids["id"]] == target


def test_f_mapping_repair_exact_table_name_is_slot_local() -> None:
    profile, payload, plan, parent, _ = _semi_profile_and_plan()
    revised = deepcopy(plan)
    revised["target_groups"][0]["table_id"] = "t99.parent"
    table_ids = [str(table["table_id"]) for table in profile["tables"]]
    diagnostic = _diag(
        "UNKNOWN_TABLE_ID",
        "/target_groups/0/table_id",
        table_ids,
    )
    outcome = repair_mapping_plan_after_diagnostics(
        revised, payload, profile, [diagnostic], _config()
    )
    assert outcome.applied
    assert outcome.plan["target_groups"][0]["table_id"] == parent["table_id"]


def test_f_mapping_update_column_semantics_are_protected() -> None:
    profile, payload, plan, parent, columns = _semi_profile_and_plan()
    revised = deepcopy(plan)
    revised["target_groups"][0]["update_column_ids"] = [f"{parent['table_id']}.name"]
    diagnostic = _diag(
        "UNKNOWN_COLUMN_ID",
        "/target_groups/0/update_column_ids/0",
        list(columns.values()),
        details={"predicted_column_id": f"{parent['table_id']}.name"},
    )
    outcome = repair_mapping_plan_after_diagnostics(
        revised, payload, profile, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == revised
    assert outcome.traces[0]["repair_rule"] == "protected_semantics_not_repairable"


def test_f_mapping_conflict_target_semantics_are_protected() -> None:
    profile, payload, plan, parent, _ = _semi_profile_and_plan()
    revised = deepcopy(plan)
    revised["target_groups"][0]["conflict_target_id"] = f"{parent['table_id']}.bogus"
    diagnostic = _diag(
        "UNKNOWN_CONSTRAINT_ID",
        "/target_groups/0/conflict_target_id",
        [f"{parent['table_id']}.u1"],
    )
    outcome = repair_mapping_plan_after_diagnostics(
        revised, payload, profile, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == revised
    assert outcome.traces[0]["reference_kind"] == "conflict_target"
    assert outcome.traces[0]["repair_rule"] == "protected_semantics_not_repairable"


def test_f_free_text_repair_exact_column_name_changes_only_reference_key() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    table = profile["tables"][0]
    columns = {c["name"]: c["column_id"] for c in table["columns"]}
    raw = f"{table['table_id']}.event_id"
    plan = _free_text_plan(request, profile, raw_column=raw)
    original = deepcopy(plan)
    diagnostic = _diag(
        "UNKNOWN_COLUMN_ID",
        f"/write_groups/0/rows/0/{raw}",
        list(columns.values()),
    )
    outcome = repair_free_text_plan_after_diagnostics(
        plan, profile, [diagnostic], _config()
    )
    assert plan == original
    assert outcome.applied
    row = outcome.plan["write_groups"][0]["rows"][0]
    assert list(row) == [columns["event_id"]]
    assert row[columns["event_id"]] == original["write_groups"][0]["rows"][0][raw]


def test_f_free_text_evidence_selection_is_protected() -> None:
    request = "Set id P1 name One."
    profile = _free_text_profile()
    plan = _free_text_plan(request, profile, evidence_id="e999")
    table = profile["tables"][0]
    column = table["columns"][0]["column_id"]
    diagnostic = _diag(
        "UNKNOWN_EVIDENCE_ID",
        f"/write_groups/0/rows/0/{column}/value_from",
        ["e1", "e2"],
    )
    outcome = repair_free_text_plan_after_diagnostics(
        plan, profile, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["reference_kind"] == "evidence"
    assert outcome.traces[0]["repair_rule"] == "protected_semantics_not_repairable"


def test_f_free_text_conflict_target_is_protected() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    plan = _free_text_plan(request, profile)
    plan["write_groups"][0]["conflict_target_id"] = "t1.bad"
    diagnostic = _diag(
        "UNKNOWN_CONSTRAINT_ID",
        "/write_groups/0/conflict_target_id",
        ["t1.u1"],
    )
    outcome = repair_free_text_plan_after_diagnostics(
        plan, profile, [diagnostic], _config()
    )
    assert not outcome.applied
    assert outcome.plan == plan
    assert outcome.traces[0]["reference_kind"] == "conflict_target"


def test_f_revalidation_marks_applied_repair_success_only_after_boundary_passes() -> None:
    result = attempt_constrained_reference_repair(
        "bad", ["good"], reference_kind="column", slot_path="/x", config=_config(),
        validation_before="UNKNOWN_COLUMN_ID",
    )
    final = mark_revalidation_outcome([result.trace], [])
    assert final[0]["repair_succeeded"] is True
    assert final[0]["validation_after"] == "PASS"


def test_f_revalidation_marks_same_boundary_failure_as_unsuccessful() -> None:
    result = attempt_constrained_reference_repair(
        "bad", ["good"], reference_kind="column", slot_path="/x", config=_config(),
        validation_before="UNKNOWN_COLUMN_ID",
    )
    final = mark_revalidation_outcome(
        [result.trace],
        [_diag("UNKNOWN_COLUMN_ID", "/x", ["good"])],
    )
    assert final[0]["repair_succeeded"] is False
    assert final[0]["validation_after"] == "UNKNOWN_COLUMN_ID"


def test_f_annotation_attaches_only_matching_repair_trace() -> None:
    result = attempt_constrained_reference_repair(
        "bad", ["c1", "c2"], reference_kind="column", slot_path="/x", config=_config(),
        validation_before="UNKNOWN_COLUMN_ID",
    )
    diagnostics = annotate_reference_diagnostics(
        [_diag("UNKNOWN_COLUMN_ID", "/x", ["c1", "c2"])],
        [result.trace],
    )
    assert diagnostics[0].details["reference_repair"]["repair_rule"] == "ambiguous_closed_set"


def test_f_pipeline_free_text_exact_column_name_repairs_then_revalidates_once() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    table = profile["tables"][0]
    plan = _free_text_plan(
        request, profile, raw_column=f"{table['table_id']}.event_id"
    )
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=F_CONFIG,
    ).run(request, plan)
    assert result.success
    assert result.write_plan is not None
    group = result.write_plan["write_groups"][0]
    assert group["rows"] == [{"event_id": "Z9"}]
    trace = group["reference_trace"]["constrained_reference_repairs"]
    assert len(trace) == 1
    assert trace[0]["repair_rule"] == "unique_exact_identifier_name"
    assert trace[0]["repair_succeeded"] is True
    assert trace[0]["validation_after"] == "PASS"
    assert group["value_evidence"][0]["event_id"]["exact_span"] == "Z9"


def test_f_pipeline_free_text_ambiguous_reference_fails_closed_with_trace() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    table = profile["tables"][0]
    plan = _free_text_plan(request, profile, raw_column=f"{table['table_id']}.event")
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=F_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "evidence_materialization"
    error = next(item for item in result.verification.errors if item.error_code == "UNKNOWN_COLUMN_ID")
    trace = error.details["reference_repair"]
    assert trace["repair_applied"] is False
    assert trace["repair_rule"] == "ambiguous_closed_set"


def test_f_pipeline_free_text_wrong_evidence_reference_is_not_repaired() -> None:
    request = "Set id P1 name One."
    profile = _free_text_profile()
    plan = _free_text_plan(request, profile, evidence_id="e999")
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=F_CONFIG,
    ).run(request, plan)
    assert result.stage == "evidence_materialization"
    error = next(item for item in result.verification.errors if item.error_code == "UNKNOWN_EVIDENCE_ID")
    assert error.details["reference_repair"]["repair_rule"] == "protected_semantics_not_repairable"


def test_f_pipeline_does_not_recursively_repair_new_failure_after_one_retry() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    table = profile["tables"][0]
    plan = _free_text_plan(
        request,
        profile,
        raw_table=f"t99.{table['name']}",
        raw_column="t99.event_id",
    )
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=F_CONFIG,
    ).run(request, plan)
    assert not result.success
    assert result.stage == "evidence_materialization"
    # First boundary repairs table. The one retry then exposes the invalid column.
    # F must stop instead of entering a second repair cycle.
    assert any(item.error_code == "UNKNOWN_COLUMN_ID" for item in result.verification.errors)
    warnings = [w for w in result.verification.warnings if w.error_code == "CONSTRAINED_REFERENCE_REPAIR_APPLIED"]
    assert len(warnings) == 1
    assert warnings[0].details["reference_kind"] == "table"


def test_f_pipeline_valid_reference_does_not_emit_repair_trace() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    plan = _free_text_plan(request, profile)
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=F_CONFIG,
    ).run(request, plan)
    assert result.success
    group = result.write_plan["write_groups"][0]
    assert "constrained_reference_repairs" not in group["reference_trace"]
    assert not any(
        w.error_code == "CONSTRAINED_REFERENCE_REPAIR_APPLIED"
        for w in result.verification.warnings
    )


def test_f_disabled_pipeline_output_is_identical_to_v5_for_valid_plan() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    plan = _free_text_plan(request, profile)
    baseline = MappingFirstPipeline(profile, reference_planning=True).run(request, plan)
    disabled = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair={"enabled": False},
    ).run(request, plan)
    assert disabled.to_dict() == baseline.to_dict()


def test_f_disabled_does_not_rescue_invalid_exact_name_reference() -> None:
    request = "Insert value Z9"
    profile = _free_text_profile()
    table = profile["tables"][0]
    plan = _free_text_plan(
        request, profile, raw_column=f"{table['table_id']}.event_id"
    )
    disabled = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair={"enabled": False},
    ).run(request, plan)
    enabled = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=F_CONFIG,
    ).run(request, plan)
    assert disabled.stage == "evidence_materialization"
    assert not disabled.success
    assert enabled.success


def test_f_stage1_classification_fixture_is_diagnostic_only_and_locked() -> None:
    fixture = json.loads(
        Path("tests/fixtures/stage2_f_stage1_reference_cases.json").read_text()
    )
    assert fixture["evidence_scope"] == "development_diagnostic_regression_only"
    assert fixture["counts"] == {
        "NON_REPAIRABLE_REFERENCE": 12,
        "REFERENCE_REPAIR_PARTIAL_BUT_SAMPLE_NOT_SAFE": 10,
        "REPAIRABLE_REFERENCE_ONLY": 13,
    }
    assert fixture["f_eligible_repair_rule_counts"] == {
        "unique_exact_identifier_name": 70,
        "unique_closed_set_candidate": 0,
    }
    assert len(fixture["cases"]) == 35
    assert sum(case["reference_error_count"] for case in fixture["cases"]) > 35


def test_f_stage1_known_examples_keep_expected_classification() -> None:
    fixture = json.loads(
        Path("tests/fixtures/stage2_f_stage1_reference_cases.json").read_text()
    )
    rows = {item["sample_id"]: item for item in fixture["cases"]}
    assert rows["final_archeology_027"]["classification"] == "REPAIRABLE_REFERENCE_ONLY"
    assert rows["final_archeology_016"]["classification"] == "NON_REPAIRABLE_REFERENCE"
    assert rows["final_archeology_021"]["classification"] == "REFERENCE_REPAIR_PARTIAL_BUT_SAMPLE_NOT_SAFE"
    assert rows["final_vaccine_018"]["classification"] == "NON_REPAIRABLE_REFERENCE"
    assert rows["final_vaccine_018"]["repairable_reference_error_count"] == 0
    assert rows["final_vaccine_033"]["classification"] == "REFERENCE_REPAIR_PARTIAL_BUT_SAMPLE_NOT_SAFE"
    assert rows["final_vaccine_033"]["repairable_reference_error_count"] == 8


def test_f_free_text_repair_does_not_overwrite_existing_valid_column_key() -> None:
    profile = _free_text_profile()
    table = profile["tables"][0]
    columns = {c["name"]: c["column_id"] for c in table["columns"]}
    raw = f"{table['table_id']}.event_id"
    plan = _free_text_plan("Insert value Z9", profile)
    row = plan["write_groups"][0]["rows"][0]
    row[raw] = {"value_from": "e2", "normalization": "identity"}
    original = deepcopy(plan)
    diagnostic = _diag(
        "UNKNOWN_COLUMN_ID",
        f"/write_groups/0/rows/0/{raw}",
        list(columns.values()),
    )
    outcome = repair_free_text_plan_after_diagnostics(
        plan, profile, [diagnostic], _config()
    )
    assert plan == original
    assert outcome.plan == original
    assert not outcome.applied
    assert len(outcome.traces) == 1
    trace = outcome.traces[0]
    assert trace["repair_attempted"] is True
    assert trace["repair_applied"] is False
    assert trace["repair_succeeded"] is False
    assert trace["replacement_reference"] == columns["event_id"]
    assert trace["repair_rule"] == "replacement_slot_collision"
    assert trace["validation_after"] == "FAIL_CLOSED"


def test_f_mapping_source_field_repair_does_not_overwrite_existing_source_key() -> None:
    profile, payload, plan, _, columns = _semi_profile_and_plan()
    group = plan["target_groups"][0]
    group["field_mapping"]["c9.id"] = columns["name"]
    original = deepcopy(plan)
    collection = payload.collections[0]
    diagnostic = _diag(
        "UNKNOWN_SOURCE_FIELD_ID",
        "/target_groups/0/field_mapping/c9.id",
        list(collection.field_ids.values()),
    )
    outcome = repair_mapping_plan_after_diagnostics(
        plan, payload, profile, [diagnostic], _config()
    )
    assert plan == original
    assert outcome.plan == original
    assert not outcome.applied
    trace = outcome.traces[0]
    assert trace["repair_applied"] is False
    assert trace["replacement_reference"] == collection.field_ids["id"]
    assert trace["repair_rule"] == "replacement_slot_collision"
    assert trace["validation_after"] == "FAIL_CLOSED"


def test_f_mapping_constant_repair_does_not_overwrite_existing_column_key() -> None:
    profile, payload, plan, parent, columns = _semi_profile_and_plan()
    group = plan["target_groups"][0]
    raw = f"{parent['table_id']}.id"
    group["constants"] = {
        columns["id"]: "Alice",
        raw: "Bob",
    }
    original = deepcopy(plan)
    diagnostic = _diag(
        "UNKNOWN_COLUMN_ID",
        f"/target_groups/0/constants/{raw}",
        list(columns.values()),
        details={"predicted_column_id": raw},
    )
    outcome = repair_mapping_plan_after_diagnostics(
        plan, payload, profile, [diagnostic], _config()
    )
    assert plan == original
    assert outcome.plan == original
    assert not outcome.applied
    trace = outcome.traces[0]
    assert trace["repair_applied"] is False
    assert trace["replacement_reference"] == columns["id"]
    assert trace["repair_rule"] == "replacement_slot_collision"
    assert trace["validation_after"] == "FAIL_CLOSED"


def test_f_two_invalid_references_cannot_collapse_to_same_replacement() -> None:
    profile = _free_text_profile()
    table = profile["tables"][0]
    columns = {c["name"]: c["column_id"] for c in table["columns"]}
    raw_a = f"{table['table_id']}.event_id"
    raw_b = f"{table['table_id']}.EVENT_ID"
    plan = _free_text_plan("Insert value Z9", profile, raw_column=raw_a)
    row = plan["write_groups"][0]["rows"][0]
    row[raw_b] = {"value_from": "e2", "normalization": "identity"}
    original = deepcopy(plan)
    diagnostics = [
        _diag(
            "UNKNOWN_COLUMN_ID",
            f"/write_groups/0/rows/0/{raw_a}",
            list(columns.values()),
        ),
        _diag(
            "UNKNOWN_COLUMN_ID",
            f"/write_groups/0/rows/0/{raw_b}",
            list(columns.values()),
        ),
    ]
    outcome = repair_free_text_plan_after_diagnostics(
        plan, profile, diagnostics, _config()
    )
    assert plan == original
    assert outcome.plan == original
    assert not outcome.applied
    assert len(outcome.traces) == 2
    assert all(trace["repair_applied"] is False for trace in outcome.traces)
    assert outcome.traces[-1]["repair_rule"] == "replacement_slot_collision"
    assert all(trace["validation_after"] == "FAIL_CLOSED" for trace in outcome.traces)


def test_f_source_field_alias_collision_fails_closed() -> None:
    profile, payload, plan, _, columns = _semi_profile_and_plan()
    collection = payload.collections[0]
    group = plan["target_groups"][0]
    # Frozen source-field resolution accepts either the semantic field name or
    # the enumerated field ID. These two raw keys therefore denote one slot.
    group["field_mapping"].pop(collection.field_ids["id"])
    group["field_mapping"]["id"] = columns["id"]
    group["field_mapping"]["c9.id"] = columns["name"]
    original = deepcopy(plan)

    diagnostic = _diag(
        "UNKNOWN_SOURCE_FIELD_ID",
        "/target_groups/0/field_mapping/c9.id",
        list(collection.field_ids.values()),
    )
    outcome = repair_mapping_plan_after_diagnostics(
        plan, payload, profile, [diagnostic], _config()
    )

    assert plan == original
    assert outcome.plan == original
    assert not outcome.applied
    assert outcome.plan["target_groups"][0]["field_mapping"]["id"] == columns["id"]
    assert "c1.f1" not in outcome.plan["target_groups"][0]["field_mapping"]
    trace = outcome.traces[0]
    assert trace["repair_attempted"] is True
    assert trace["repair_applied"] is False
    assert trace["repair_succeeded"] is False
    assert trace["replacement_reference"] == collection.field_ids["id"]
    assert trace["repair_rule"] == "replacement_semantic_slot_collision"
    assert trace["validation_after"] == "FAIL_CLOSED"


def test_f_source_field_alias_collision_rolls_back_earlier_safe_repair() -> None:
    profile, payload, plan, table, columns = _semi_profile_and_plan()
    collection = payload.collections[0]
    group = plan["target_groups"][0]

    # Diagnostic 1 is independently repairable and would normally be applied.
    raw_target = f"{table['table_id']}.name"
    group["field_mapping"][collection.field_ids["name"]] = raw_target

    # Diagnostic 2 repairs c9.id -> c1.f1, but "id" already represents the
    # same source-field identity. This must roll back the whole repair batch.
    group["field_mapping"].pop(collection.field_ids["id"])
    group["field_mapping"]["id"] = columns["id"]
    group["field_mapping"]["c9.id"] = columns["name"]
    original = deepcopy(plan)

    diagnostics = [
        _diag(
            "UNKNOWN_COLUMN_ID",
            f"/target_groups/0/field_mapping/{collection.field_ids['name']}",
            list(columns.values()),
            details={"predicted_column_id": raw_target},
        ),
        _diag(
            "UNKNOWN_SOURCE_FIELD_ID",
            "/target_groups/0/field_mapping/c9.id",
            list(collection.field_ids.values()),
        ),
    ]
    outcome = repair_mapping_plan_after_diagnostics(
        plan, payload, profile, diagnostics, _config()
    )

    assert plan == original
    assert outcome.plan == original
    assert not outcome.applied
    assert len(outcome.traces) == 2
    assert outcome.traces[0]["repair_rule"] == "unique_exact_identifier_name"
    assert outcome.traces[0]["repair_applied"] is False
    assert outcome.traces[0]["repair_succeeded"] is False
    assert outcome.traces[0]["validation_after"] == "FAIL_CLOSED"
    assert outcome.traces[1]["repair_rule"] == "replacement_semantic_slot_collision"
    assert outcome.traces[1]["repair_applied"] is False
