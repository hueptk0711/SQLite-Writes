from __future__ import annotations

from copy import deepcopy

from nldbwrite_v3.ir import SourceCollection, SourcePayload
from nldbwrite_v3.planner.materialize import materialize_mapping_plan
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.verifier import verify_write_plan
from nldbwrite_v3.vnext import (
    CONFLICT_CONTROL,
    OPERATION_CONTROL,
    PAYLOAD_VALUE,
    UPDATE_CONTROL,
    Stage2InterventionConfig,
    apply_free_text_reference_interventions,
    apply_reference_interventions,
    classify_source_field_role,
)
from tests.helpers import test_profile


def _profile_and_ids():
    profile = test_profile()
    ensure_reference_ids(profile)
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    parent_cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    parent_pk = next(item for item in parent["unique_indexes"] if item.get("is_primary_key"))
    child = next(table for table in profile["tables"] if table["name"] == "child")
    child_cols = {column["name"]: column["column_id"] for column in child["columns"]}
    child_pk = next(item for item in child["unique_indexes"] if item.get("is_primary_key"))
    return profile, parent, parent_cols, parent_pk, child, child_cols, child_pk


def _payload(row: dict[str, object], collection_id: str = "records") -> SourcePayload:
    collection = SourceCollection(
        collection_id=collection_id,
        source_path="$[*]",
        source_format="json_array",
        rows=[row],
        fields=list(row),
        reference_id="c1",
        selector_id="s1",
        field_ids={field: f"c1.f{index}" for index, field in enumerate(row, start=1)},
        metadata={"control_metadata": []},
    )
    return SourcePayload(
        mode="semi_structured",
        source_format="json_array",
        collections=[collection],
        instruction_text="",
        raw_text="",
    )


def test_control_role_classifier_is_typed_not_operation_only():
    assert classify_source_field_role("operation") == OPERATION_CONTROL
    assert classify_source_field_role("conflict_target") == CONFLICT_CONTROL
    assert classify_source_field_role("relationship_columns_not_updated") == UPDATE_CONTROL
    assert classify_source_field_role("customer_name") == PAYLOAD_VALUE


def test_v1_control_field_is_consumed_and_baseline_remains_fail_closed():
    profile, parent, *_ = _profile_and_ids()
    payload = _payload({"operation": "plain_insert", "id": "p1", "name": "One"})
    mapping = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection": "records",
                "source_rows": "$[*]",
                "field_mapping": {"id": "id", "name": "name"},
                "constants": {},
                "table": parent["name"],
                "conflict": {"action": "error", "target": [], "update_columns": []},
            }
        ],
        "ignored_fields": {},
        "dependencies": [],
    }

    baseline = materialize_mapping_plan(mapping, payload)
    baseline_result = verify_write_plan(baseline, profile)
    assert not baseline_result.valid
    assert "UNRESOLVED_SOURCE_FIELD" in {item.error_code for item in baseline_result.errors}

    v1 = materialize_mapping_plan(mapping, payload, control_field_roles=True)
    operation_record = next(item for item in v1["unresolved_fields"] if item["field"] == "operation")
    assert operation_record["status"] == "consumed_control"
    assert operation_record["role"] == OPERATION_CONTROL
    assert operation_record["consumed_by"] == "instruction_semantics.operation"
    assert verify_write_plan(v1, profile).valid


def test_v2_preserves_explicit_insert_ignore_and_conflict_target():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    payload = _payload(
        {
            "operation": "insert_ignore",
            "conflict_target": "id",
            "id": "p1",
            "name": "One",
        }
    )
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
                "write_semantics": "needs_clarification",
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }

    original, _ = apply_reference_interventions(
        plan, payload, profile, Stage2InterventionConfig()
    )
    assert original == plan

    revised, diagnostics = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    group = revised["target_groups"][0]
    assert group["write_semantics"] == "insert_ignore"
    assert group["conflict_target_id"] == pk["constraint_id"]
    assert group["update_column_ids"] == []
    assert any(item.error_code == "EXPLICIT_CONFLICT_SEMANTICS_DROPPED" for item in diagnostics)
    assert "EXPLICIT_CONFLICT_SEMANTICS_DROPPED" in group["stage2_intervention_trace"]["diagnostic_codes"]


def test_v3_restores_exact_requested_update_columns():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    payload = _payload(
        {
            "operation": "upsert_update",
            "conflict_target": "id",
            "update_columns": "name,count",
            "id": "p1",
            "name": "One",
            "count": 2,
        }
    )
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
                    collection.field_ids["count"]: columns["count"],
                },
                "constants": {},
                "write_semantics": "upsert_update",
                "conflict_target_id": pk["constraint_id"],
                "update_column_ids": [columns["name"]],
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }
    revised, diagnostics = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    group = revised["target_groups"][0]
    assert group["update_column_ids"] == [columns["name"], columns["count"]]
    assert any(item.error_code == "REQUIRED_UPDATE_COLUMNS_DROPPED" for item in diagnostics)
    trace = group["stage2_intervention_trace"]
    assert "REQUIRED_UPDATE_COLUMNS_DROPPED" in trace["diagnostic_codes"]
    assert trace["requested_update_column_ids"] == [columns["name"], columns["count"]]


def test_v3_removes_explicitly_excluded_relationship_column():
    profile, _, _, _, child, columns, pk = _profile_and_ids()
    payload = _payload(
        {
            "operation": "upsert_update",
            "conflict_target": "id",
            "update_columns": "note",
            "relationship_columns_not_updated": "parent_id",
            "id": 10,
            "parent_id": "p1",
            "note": "N",
        }
    )
    collection = payload.collections[0]
    plan = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection_id": collection.reference_id,
                "source_selector_id": collection.selector_id,
                "table_id": child["table_id"],
                "field_mapping": {
                    collection.field_ids["id"]: columns["id"],
                    collection.field_ids["parent_id"]: columns["parent_id"],
                    collection.field_ids["note"]: columns["note"],
                },
                "constants": {},
                "write_semantics": "upsert_update",
                "conflict_target_id": pk["constraint_id"],
                "update_column_ids": [columns["parent_id"], columns["note"]],
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }
    revised, _ = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    assert revised["target_groups"][0]["update_column_ids"] == [columns["note"]]


def test_flags_are_independently_ablatable():
    config = Stage2InterventionConfig.from_mapping(
        {
            "control_field_roles": True,
            "explicit_conflict_preservation": False,
            "update_column_consistency": False,
        }
    )
    assert config.control_field_roles is True
    assert config.explicit_conflict_preservation is False
    assert config.update_column_consistency is False
    assert Stage2InterventionConfig.from_mapping(None).to_dict() == {
        "control_field_roles": False,
        "explicit_conflict_preservation": False,
        "update_column_consistency": False,
    }


def test_v2_v3_free_text_preserves_explicit_conflict_and_update_set():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    plan = {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": parent["table_id"],
                "rows": [],
                "write_semantics": "needs_clarification",
                "conflict_target_id": None,
                "update_column_ids": [columns["name"]],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }
    request = (
        "For parent, ON CONFLICT(id) DO UPDATE SET name, count. "
        "Use the supplied values."
    )
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        request,
        profile,
        Stage2InterventionConfig(
            explicit_conflict_preservation=True,
            update_column_consistency=True,
        ),
    )
    group = revised["write_groups"][0]
    assert group["write_semantics"] == "upsert_update"
    assert group["conflict_target_id"] == pk["constraint_id"]
    assert group["update_column_ids"] == [columns["name"], columns["count"]]
    assert any(item.error_code == "EXPLICIT_CONFLICT_SEMANTICS_DROPPED" for item in diagnostics)
    assert any(item.error_code == "REQUIRED_UPDATE_COLUMNS_DROPPED" for item in diagnostics)


def test_stage2_config_chain_exposes_independent_abc_flags():
    from nldbwrite_v3.experiments.run_method import _load_method_config

    original, _ = _load_method_config("configs/stage2/original.json")
    v1, _ = _load_method_config("configs/stage2/v1_control.json")
    v2, _ = _load_method_config("configs/stage2/v2_conflict.json")
    v3, _ = _load_method_config("configs/stage2/v3_update.json")
    assert original["stage2_interventions"] == {
        "control_field_roles": False,
        "explicit_conflict_preservation": False,
        "update_column_consistency": False,
    }
    assert v1["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": False,
        "update_column_consistency": False,
    }
    assert v2["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": True,
        "update_column_consistency": False,
    }
    assert v3["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": True,
        "update_column_consistency": True,
    }


def test_consumed_control_requires_role_and_consumed_by_for_verification():
    profile, parent, *_ = _profile_and_ids()
    payload = _payload({"operation": "plain_insert", "id": "p1", "name": "One"})
    mapping = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection": "records",
                "source_rows": "$[*]",
                "field_mapping": {"id": "id", "name": "name"},
                "constants": {},
                "table": parent["name"],
                "conflict": {"action": "error", "target": [], "update_columns": []},
            }
        ],
        "ignored_fields": {},
        "dependencies": [],
    }
    plan = materialize_mapping_plan(mapping, payload, control_field_roles=True)
    control = next(item for item in plan["unresolved_fields"] if item["field"] == "operation")
    control["consumed_by"] = ""
    result = verify_write_plan(plan, profile)
    assert not result.valid
    assert "UNRESOLVED_SOURCE_FIELD" in {item.error_code for item in result.errors}


def test_v2_unresolvable_explicit_conflict_target_fails_closed():
    profile, parent, columns, *_ = _profile_and_ids()
    payload = _payload(
        {
            "operation": "insert_ignore",
            "conflict_target": "not_a_real_column",
            "id": "p1",
            "name": "One",
        }
    )
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
                "write_semantics": "needs_clarification",
                "conflict_target_id": None,
                "update_column_ids": [],
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }
    _, diagnostics = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    assert any(item.error_code == "EXPLICIT_CONFLICT_SEMANTICS_DROPPED" for item in errors)


def test_v3_unresolvable_explicit_update_column_fails_closed():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    payload = _payload(
        {
            "operation": "upsert_update",
            "conflict_target": "id",
            "update_columns": "name,not_a_real_column",
            "id": "p1",
            "name": "One",
        }
    )
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
                "write_semantics": "upsert_update",
                "conflict_target_id": pk["constraint_id"],
                "update_column_ids": [columns["name"]],
            }
        ],
        "dependencies": [],
        "ignored_fields": {},
    }
    _, diagnostics = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    assert any(item.error_code == "REQUIRED_UPDATE_COLUMNS_UNRESOLVED" for item in errors)


def test_v1_consumes_typed_control_block_but_not_payload_like_action_value():
    profile, parent, *_ = _profile_and_ids()
    payload = _payload(
        {
            "operation": "upsert_update",
            "table": "parent",
            "conflict_target": "id",
            "update_columns": "name",
            "id": "p1",
            "name": "One",
        }
    )
    mapping = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection": "records",
                "source_rows": "$[*]",
                "field_mapping": {"id": "id", "name": "name"},
                "constants": {},
                "table": parent["name"],
                "conflict": {"action": "error", "target": [], "update_columns": []},
            }
        ],
        "ignored_fields": {},
        "dependencies": [],
    }
    materialized = materialize_mapping_plan(mapping, payload, control_field_roles=True)
    controls = {
        item["field"]: item
        for item in materialized["unresolved_fields"]
        if item.get("status") == "consumed_control"
    }
    assert set(controls) == {"operation", "table", "conflict_target", "update_columns"}

    ambiguous_payload = _payload({"action": "click", "id": "p1", "name": "One"})
    materialized2 = materialize_mapping_plan(mapping, ambiguous_payload, control_field_roles=True)
    action = next(item for item in materialized2["unresolved_fields"] if item["field"] == "action")
    assert action["status"] == "unresolved"


def test_compiler_logs_compiled_update_columns_only_for_stage2_trace():
    from nldbwrite_v3.compiler import compile_verified_plan

    profile, parent, columns, pk, *_ = _profile_and_ids()
    traced_plan = {
        "version": "3.0",
        "source": {"mode": "semi_structured", "row_count": 1},
        "write_groups": [
            {
                "group_id": "g1",
                "table": parent["name"],
                "action": "upsert",
                "rows": [{"id": "p1", "name": "One", "count": 2}],
                "conflict": {
                    "action": "do_update",
                    "target": ["id"],
                    "update_columns": ["name", "count"],
                },
                "reference_trace": {
                    "stage2_intervention_trace": {
                        "requested_update_columns": ["name", "count"],
                        "planned_update_columns": ["name"],
                    }
                },
            }
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }
    compiled = compile_verified_plan(traced_plan, profile)
    assert compiled.success
    trace = compiled.statements[0].semantic_trace
    assert trace["requested_update_columns"] == ["name", "count"]
    assert trace["planned_update_columns"] == ["name"]
    assert trace["materialized_update_columns"] == ["name", "count"]
    assert trace["compiled_update_columns"] == ["name", "count"]

    baseline_plan = {
        **traced_plan,
        "write_groups": [
            {
                **traced_plan["write_groups"][0],
                "reference_trace": {},
            }
        ],
    }
    baseline = compile_verified_plan(baseline_plan, profile)
    statement_dict = baseline.statements[0].to_dict()
    assert "semantic_trace" not in statement_dict
