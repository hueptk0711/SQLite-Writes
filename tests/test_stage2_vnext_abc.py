from __future__ import annotations

from copy import deepcopy

from nldbwrite_v3.ir import SourceCollection, SourcePayload
from nldbwrite_v3.planner.materialize import materialize_mapping_plan
from nldbwrite_v3.planner.references import resolve_reference_mapping_plan
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.verifier import verify_write_plan
from nldbwrite_v3.vnext import (
    CONFLICT_ACTION_CONTROL,
    CONFLICT_TARGET_CONTROL,
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
    assert classify_source_field_role("conflict_target") == CONFLICT_TARGET_CONTROL
    assert classify_source_field_role("conflict_action") == CONFLICT_ACTION_CONTROL
    assert classify_source_field_role("relationship_columns_not_updated") == UPDATE_CONTROL
    assert classify_source_field_role("customer_name") == PAYLOAD_VALUE


def test_v1_control_field_is_consumed_and_baseline_remains_fail_closed():
    profile, parent, columns, *_ = _profile_and_ids()
    payload = _payload({"operation": "plain_insert", "id": "p1", "name": "One"})
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
            }
        ],
        "ignored_fields": {},
        "dependencies": [],
    }

    baseline_ref, _ = apply_reference_interventions(
        plan, payload, profile, Stage2InterventionConfig()
    )
    baseline_grounded, baseline_errors = resolve_reference_mapping_plan(
        baseline_ref, payload, profile
    )
    assert not baseline_errors
    baseline = materialize_mapping_plan(baseline_grounded, payload)
    baseline_result = verify_write_plan(baseline, profile)
    assert not baseline_result.valid
    assert "UNRESOLVED_SOURCE_FIELD" in {item.error_code for item in baseline_result.errors}

    v1_ref, _ = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(control_field_roles=True),
    )
    v1_grounded, v1_errors = resolve_reference_mapping_plan(
        v1_ref, payload, profile
    )
    assert not v1_errors
    v1 = materialize_mapping_plan(v1_grounded, payload, control_field_roles=True)
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


def test_stage2_configs_dispatch_as_mpfsplus_and_inherit_frozen_config():
    from nldbwrite_v3.experiments.run_method import (
        MAPPING_METHODS,
        PREFLIGHT_METHODS,
        SUPPORTED_METHODS,
        _load_method_config,
    )

    paths = [
        "configs/stage2/original.json",
        "configs/stage2/v1_control.json",
        "configs/stage2/v2_conflict.json",
        "configs/stage2/v3_update.json",
    ]
    configs = [_load_method_config(path)[0] for path in paths]
    original, v1, v2, v3 = configs
    for config in configs:
        assert config["method_id"] == "MP-FS+"
        assert config["method_id"] in MAPPING_METHODS
        assert config["method_id"] in PREFLIGHT_METHODS
        assert config["method_id"] in SUPPORTED_METHODS
        assert config.get("reference_planning") is True

    allowed_differences = {
        "base_config", "method_variant", "method_version", "stage2_interventions"
    }
    frozen_keys = set(original) - allowed_differences
    for candidate in (v1, v2, v3):
        for key in frozen_keys:
            assert candidate.get(key) == original.get(key), key

    assert original["stage2_interventions"] == {
        "control_field_roles": False,
        "explicit_conflict_preservation": False,
        "update_column_consistency": False,
    }
    assert v1["method_variant"] == "vnext-v1-control"
    assert v1["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": False,
        "update_column_consistency": False,
    }
    assert v2["method_variant"] == "vnext-v2-conflict"
    assert v2["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": True,
        "update_column_consistency": False,
    }
    assert v3["method_variant"] == "vnext-v3-update"
    assert v3["stage2_interventions"] == {
        "control_field_roles": True,
        "explicit_conflict_preservation": True,
        "update_column_consistency": True,
    }


def test_consumed_control_requires_role_and_consumed_by_for_verification():
    profile, parent, *_ = _profile_and_ids()
    plan = {
        "version": "3.0",
        "source": {
            "mode": "semi_structured",
            "format": "json_array",
            "row_count": 1,
            "collections": [{"collection_id": "records", "row_count": 1}],
        },
        "write_groups": [
            {
                "group_id": "g1",
                "table": parent["name"],
                "action": "insert",
                "rows": [{"id": "p1", "name": "One"}],
                "conflict": {"action": "error", "target": [], "update_columns": []},
                "provenance": [],
            }
        ],
        "dependencies": [],
        "unresolved_fields": [
            {
                "source_collection": "records",
                "source_row_index": 0,
                "field": "operation",
                "role": OPERATION_CONTROL,
                "status": "consumed_control",
                "consumed_by": "",
            }
        ],
    }
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


def test_v1_consumes_only_typed_operation_control_not_b_or_c_controls():
    profile, parent, columns, *_ = _profile_and_ids()
    payload = _payload(
        {
            "operation": "upsert_update",
            "table": "parent",
            "conflict_target": "id",
            "update_columns": "name",
            "policy": "premium",
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
                "conflict_target_id": None,
                "update_column_ids": [columns["name"]],
            }
        ],
        "ignored_fields": {},
        "dependencies": [],
    }
    revised, _ = apply_reference_interventions(
        plan, payload, profile, Stage2InterventionConfig(control_field_roles=True)
    )
    consumed = revised.get("consumed_control_refs") or []
    assert {item["source_field"] for item in consumed} == {"operation"}

    grounded, errors = resolve_reference_mapping_plan(revised, payload, profile)
    # conflict target is intentionally not fixed by V1, so policy resolution may
    # still fail.  The provenance claim itself must nevertheless stay isolated.
    assert any(item.error_code == "UNKNOWN_CONSTRAINT_ID" for item in errors)


def test_payload_like_action_insert_is_not_operation_control():
    assert classify_source_field_role("action") == PAYLOAD_VALUE
    profile, parent, *_ = _profile_and_ids()
    payload = _payload({"action": "insert", "id": "p1", "name": "One"})
    mapping = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection": "records",
            "source_rows": "$[*]",
            "field_mapping": {"id": "id", "name": "name"},
            "constants": {},
            "table": parent["name"],
            "conflict": {"action": "error", "target": [], "update_columns": []},
        }],
        "ignored_fields": {},
        "dependencies": [],
    }
    materialized = materialize_mapping_plan(mapping, payload, control_field_roles=True)
    action = next(item for item in materialized["unresolved_fields"] if item["field"] == "action")
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


def test_v2_v3_consume_controls_only_after_semantic_resolution():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    payload = _payload({
        "operation": "upsert_update",
        "conflict_target": "id",
        "update_columns": "name,count",
        "id": "p1",
        "name": "One",
        "count": 2,
    })
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
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
            "write_semantics": "needs_clarification",
            "conflict_target_id": None,
            "update_column_ids": [columns["name"]],
        }],
        "ignored_fields": {},
        "dependencies": [],
    }
    revised, diagnostics = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(
            control_field_roles=True,
            explicit_conflict_preservation=True,
            update_column_consistency=True,
        ),
    )
    assert not [item for item in diagnostics if item.severity == "error"]
    consumed = {item["source_field"]: item for item in revised["consumed_control_refs"]}
    assert set(consumed) == {"operation", "conflict_target", "update_columns"}
    assert consumed["operation"]["consumed_by"] == "instruction_semantics.operation"
    assert consumed["conflict_target"]["consumed_by"] == "explicit_conflict_preservation.target"
    assert consumed["update_columns"]["consumed_by"] == "update_column_consistency.requested"
    group = revised["target_groups"][0]
    assert group["conflict_target_id"] == pk["constraint_id"]
    assert group["update_column_ids"] == [columns["name"], columns["count"]]


def test_free_text_conflict_preservation_is_group_scoped():
    profile, parent, parent_cols, parent_pk, child, child_cols, child_pk = _profile_and_ids()
    plan = {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g_parent",
                "table_id": parent["table_id"],
                "rows": [],
                "write_semantics": "needs_clarification",
                "conflict_target_id": None,
                "update_column_ids": [parent_cols["name"]],
            },
            {
                "group_id": "g_child",
                "table_id": child["table_id"],
                "rows": [],
                "write_semantics": "needs_clarification",
                "conflict_target_id": None,
                "update_column_ids": [],
            },
        ],
        "dependencies": [],
        "unresolved_fields": [],
    }
    request = (
        "For parent, ON CONFLICT(id) DO NOTHING. "
        "For child, ON CONFLICT(id) DO UPDATE SET note = excluded.note;"
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
    assert not [item for item in diagnostics if item.severity == "error"]
    parent_group, child_group = revised["write_groups"]
    assert parent_group["write_semantics"] == "insert_ignore"
    assert parent_group["conflict_target_id"] == parent_pk["constraint_id"]
    assert parent_group["update_column_ids"] == []
    assert child_group["write_semantics"] == "upsert_update"
    assert child_group["conflict_target_id"] == child_pk["constraint_id"]
    assert child_group["update_column_ids"] == [child_cols["note"]]
    assert parent_group["stage2_intervention_trace"]["request_scope"] == "group_local"
    assert child_group["stage2_intervention_trace"]["request_scope"] == "group_local"


def test_free_text_unknown_update_column_fails_closed_even_when_one_name_is_valid():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    plan = {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [{
            "group_id": "g1",
            "table_id": parent["table_id"],
            "rows": [],
            "write_semantics": "upsert_update",
            "conflict_target_id": pk["constraint_id"],
            "update_column_ids": [columns["name"]],
        }],
        "dependencies": [],
        "unresolved_fields": [],
    }
    request = (
        "For parent, ON CONFLICT(id) DO UPDATE SET "
        "name = excluded.name, not_a_real_column = 1;"
    )
    _, diagnostics = apply_free_text_reference_interventions(
        plan,
        request,
        profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    error = next(item for item in errors if item.error_code == "REQUIRED_UPDATE_COLUMNS_UNRESOLVED")
    assert "not_a_real_column" in error.details["unresolved_column_names"]


def test_free_text_set_parser_uses_assignment_lhs_not_rhs_mentions():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    plan = {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [{
            "group_id": "g1",
            "table_id": parent["table_id"],
            "rows": [],
            "write_semantics": "upsert_update",
            "conflict_target_id": pk["constraint_id"],
            "update_column_ids": [columns["name"]],
        }],
        "dependencies": [],
        "unresolved_fields": [],
    }
    request = (
        "For parent, ON CONFLICT(id) DO UPDATE SET "
        "name = CASE WHEN id = 'p1' THEN excluded.name ELSE name END, "
        "count = count + 1;"
    )
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        request,
        profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    assert not [item for item in diagnostics if item.severity == "error"]
    assert revised["write_groups"][0]["update_column_ids"] == [
        columns["name"], columns["count"]
    ]
    assert columns["id"] not in revised["write_groups"][0]["update_column_ids"]


def test_contradictory_requested_and_excluded_update_controls_fail_closed():
    profile, _, _, _, child, columns, pk = _profile_and_ids()
    payload = _payload({
        "operation": "upsert_update",
        "conflict_target": "id",
        "update_columns": "note,parent_id",
        "relationship_columns_not_updated": "parent_id",
        "id": 10,
        "parent_id": "p1",
        "note": "N",
    })
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
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
            "update_column_ids": [columns["note"], columns["parent_id"]],
        }],
        "ignored_fields": {},
        "dependencies": [],
    }
    _, diagnostics = apply_reference_interventions(
        plan,
        payload,
        profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    assert any(item.error_code == "CONTRADICTORY_UPDATE_CONTROL" for item in errors)


def test_semi_structured_intervention_warnings_propagate_to_warning_sink():
    profile, parent, columns, pk, *_ = _profile_and_ids()
    payload = _payload({
        "operation": "insert_ignore",
        "conflict_target": "id",
        "id": "p1",
        "name": "One",
    })
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
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
        }],
        "ignored_fields": {},
        "dependencies": [],
    }
    warnings = []
    grounded, errors = resolve_reference_mapping_plan(
        plan,
        payload,
        profile,
        stage2_interventions={
            "control_field_roles": True,
            "explicit_conflict_preservation": True,
            "update_column_consistency": False,
        },
        warning_sink=warnings,
    )
    assert not errors
    assert grounded["target_groups"][0]["conflict"]["action"] == "do_nothing"
    assert any(item.error_code == "EXPLICIT_CONFLICT_SEMANTICS_DROPPED" for item in warnings)
    assert any(item.error_code == "EXPLICIT_CONFLICT_TARGET_PRESERVED" for item in warnings)



def _identifier_collision_profile():
    profile = test_profile()
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    parent["columns"].extend(
        [
            {
                "name": "user_id",
                "type": "TEXT",
                "is_insertable": True,
                "semantic_type": "identifier",
                "preserve_as_text": True,
            },
            {
                "name": "userid",
                "type": "TEXT",
                "is_insertable": True,
                "semantic_type": "identifier",
                "preserve_as_text": True,
            },
            {
                "name": "ignore",
                "type": "TEXT",
                "is_insertable": True,
                "semantic_type": "identifier",
                "preserve_as_text": True,
            },
        ]
    )
    parent["unique_indexes"].extend(
        [
            {"name": "uq_user_id", "columns": ["user_id"], "origin": "u", "is_primary_key": False},
            {"name": "uq_userid", "columns": ["userid"], "origin": "u", "is_primary_key": False},
            {"name": "uq_ignore", "columns": ["ignore"], "origin": "u", "is_primary_key": False},
        ]
    )
    ensure_reference_ids(profile)
    return profile, parent


def test_exact_identifier_does_not_collapse_user_id_and_userid():
    profile, parent = _identifier_collision_profile()
    cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    constraints = {item["name"]: item["constraint_id"] for item in parent["unique_indexes"]}
    payload = _payload(
        {
            "operation": "insert_ignore",
            "conflict_target": "user_id",
            "id": "p1",
            "name": "One",
            "user_id": "U-1",
            "userid": "U-2",
        }
    )
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": parent["table_id"],
            "field_mapping": {
                collection.field_ids["id"]: cols["id"],
                collection.field_ids["name"]: cols["name"],
                collection.field_ids["user_id"]: cols["user_id"],
                collection.field_ids["userid"]: cols["userid"],
            },
            "constants": {},
            "write_semantics": "needs_clarification",
            "conflict_target_id": None,
            "update_column_ids": [],
        }],
        "dependencies": [],
        "ignored_fields": {},
    }
    revised, diagnostics = apply_reference_interventions(
        plan, payload, profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    assert not errors
    assert revised["target_groups"][0]["conflict_target_id"] == constraints["uq_user_id"]
    assert revised["target_groups"][0]["conflict_target_id"] != constraints["uq_userid"]


def test_requested_user_id_and_excluded_userid_are_not_contradictory():
    profile, parent = _identifier_collision_profile()
    cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    pk = next(item for item in parent["unique_indexes"] if item.get("is_primary_key"))
    payload = _payload(
        {
            "operation": "upsert_update",
            "conflict_target": "id",
            "update_columns": "user_id",
            "relationship_columns_not_updated": "userid",
            "id": "p1",
            "name": "One",
            "user_id": "U-1",
            "userid": "U-2",
        }
    )
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": parent["table_id"],
            "field_mapping": {
                collection.field_ids["id"]: cols["id"],
                collection.field_ids["name"]: cols["name"],
                collection.field_ids["user_id"]: cols["user_id"],
                collection.field_ids["userid"]: cols["userid"],
            },
            "constants": {},
            "write_semantics": "upsert_update",
            "conflict_target_id": pk["constraint_id"],
            "update_column_ids": [cols["userid"]],
        }],
        "dependencies": [],
        "ignored_fields": {},
    }
    revised, diagnostics = apply_reference_interventions(
        plan, payload, profile,
        Stage2InterventionConfig(update_column_consistency=True),
    )
    assert not any(item.error_code == "CONTRADICTORY_UPDATE_CONTROL" for item in diagnostics)
    assert revised["target_groups"][0]["update_column_ids"] == [cols["user_id"]]


def test_conflict_target_value_cannot_define_conflict_action():
    profile, parent = _identifier_collision_profile()
    cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    payload = _payload(
        {
            "conflict_target": "ignore",
            "id": "p1",
            "name": "One",
            "ignore": "K1",
        }
    )
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": parent["table_id"],
            "field_mapping": {
                collection.field_ids["id"]: cols["id"],
                collection.field_ids["name"]: cols["name"],
                collection.field_ids["ignore"]: cols["ignore"],
            },
            "constants": {},
            "write_semantics": "plain_insert",
            "conflict_target_id": None,
            "update_column_ids": [],
        }],
        "dependencies": [],
        "ignored_fields": {},
    }
    revised, diagnostics = apply_reference_interventions(
        plan, payload, profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    assert revised["target_groups"][0]["write_semantics"] == "plain_insert"
    assert revised["target_groups"][0]["conflict_target_id"] is None
    assert not any(item.error_code == "EXPLICIT_CONFLICT_SEMANTICS_DROPPED" for item in diagnostics)


def test_structured_operation_skip_validation_is_not_insert_ignore():
    profile, parent, cols, *_ = _profile_and_ids()
    payload = _payload({"operation": "skip_validation", "id": "p1", "name": "One"})
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": parent["table_id"],
            "field_mapping": {
                collection.field_ids["id"]: cols["id"],
                collection.field_ids["name"]: cols["name"],
            },
            "constants": {},
            "write_semantics": "plain_insert",
            "conflict_target_id": None,
            "update_column_ids": [],
        }],
        "dependencies": [],
        "ignored_fields": {},
    }
    revised, _ = apply_reference_interventions(
        plan, payload, profile,
        Stage2InterventionConfig(control_field_roles=True),
    )
    grounded, errors = resolve_reference_mapping_plan(revised, payload, profile)
    assert not errors
    materialized = materialize_mapping_plan(grounded, payload, control_field_roles=True)
    operation_record = next(item for item in materialized["unresolved_fields"] if item["field"] == "operation")
    assert operation_record["status"] == "unresolved"
    assert not verify_write_plan(materialized, profile).valid


def _plain_parent_free_text_plan(profile):
    ensure_reference_ids(profile)
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    return parent, {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [{
            "group_id": "g1",
            "table_id": parent["table_id"],
            "rows": [],
            "write_semantics": "plain_insert",
            "conflict_target_id": None,
            "update_column_ids": [],
        }],
        "dependencies": [],
        "unresolved_fields": [],
    }


def test_free_text_payload_literal_upsert_does_not_change_operation():
    profile = test_profile()
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        "For parent, insert a row with id = 'p1' and name = 'upsert'.",
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    assert revised["write_groups"][0]["write_semantics"] == "plain_insert"
    assert not diagnostics


def test_free_text_payload_literal_do_nothing_does_not_change_operation():
    profile = test_profile()
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        'For parent, insert a row with id = "p1" and name = "do nothing".',
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    assert revised["write_groups"][0]["write_semantics"] == "plain_insert"
    assert not diagnostics



def _free_text_profile_with_other_unique():
    profile = test_profile()
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    parent["columns"].append(
        {"name": "other", "type": "TEXT", "is_insertable": True}
    )
    parent["unique_indexes"].append(
        {"name": "uq_other", "columns": ["other"], "origin": "u", "is_primary_key": False}
    )
    ensure_reference_ids(profile)
    return profile, parent


def test_quoted_payload_conflict_target_does_not_override_target():
    profile, parent = _free_text_profile_with_other_unique()
    constraints = {item["name"]: item["constraint_id"] for item in parent["unique_indexes"]}
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        "For parent, operation: insert_ignore; "
        "description='conflict_target=other'; conflict_target: id; id='p1'.",
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    group = revised["write_groups"][0]
    assert group["write_semantics"] == "insert_ignore"
    assert group["conflict_target_id"] == constraints["PRIMARY_KEY"]
    assert group["conflict_target_id"] != constraints["uq_other"]
    assert not any(item.severity == "error" for item in diagnostics)


def test_quoted_payload_update_columns_does_not_add_columns():
    profile, parent = _free_text_profile_with_other_unique()
    cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    pk = next(item for item in parent["unique_indexes"] if item.get("is_primary_key"))
    _, plan = _plain_parent_free_text_plan(profile)
    plan["write_groups"][0].update({
        "write_semantics": "upsert_update",
        "conflict_target_id": pk["constraint_id"],
        "update_column_ids": [cols["name"]],
    })
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        "For parent, operation: upsert_update; conflict_target: id; "
        "description='update_columns=other'; update_columns: name; id='p1'.",
        profile,
        Stage2InterventionConfig(
            explicit_conflict_preservation=True,
            update_column_consistency=True,
        ),
    )
    group = revised["write_groups"][0]
    assert group["update_column_ids"] == [cols["name"]]
    assert cols["other"] not in group["update_column_ids"]
    assert not any(item.severity == "error" for item in diagnostics)


def test_quoted_identifier_in_on_conflict_still_resolves():
    profile = test_profile()
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    parent["columns"].append(
        {"name": "user_id", "type": "TEXT", "is_insertable": True}
    )
    parent["unique_indexes"].append(
        {"name": "uq_user_id", "columns": ["user_id"], "origin": "u", "is_primary_key": False}
    )
    ensure_reference_ids(profile)
    constraints = {item["name"]: item["constraint_id"] for item in parent["unique_indexes"]}
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        'For parent, ON CONFLICT("user_id") DO NOTHING; id="p1"; user_id="u1".',
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    group = revised["write_groups"][0]
    assert group["write_semantics"] == "insert_ignore"
    assert group["conflict_target_id"] == constraints["uq_user_id"]
    assert not any(item.severity == "error" for item in diagnostics)


def test_quoted_identifier_set_lhs_still_resolves():
    profile = test_profile()
    ensure_reference_ids(profile)
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        'For parent, operation: upsert_update; conflict_target: id; '
        'DO UPDATE SET "name" = excluded."name"; id="p1".',
        profile,
        Stage2InterventionConfig(
            explicit_conflict_preservation=True,
            update_column_consistency=True,
        ),
    )
    group = revised["write_groups"][0]
    assert group["update_column_ids"] == [cols["name"]]
    assert not any(item.severity == "error" for item in diagnostics)


def test_payload_literal_on_conflict_text_is_not_instruction():
    profile = test_profile()
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        "For parent, insert a row with id='p1' and "
        "name='ON CONFLICT(id) DO NOTHING'.",
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    assert revised["write_groups"][0]["write_semantics"] == "plain_insert"
    assert revised["write_groups"][0]["conflict_target_id"] is None
    assert not diagnostics


def test_quoted_control_conflict_target_value_still_resolves():
    profile = test_profile()
    ensure_reference_ids(profile)
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    pk = next(item for item in parent["unique_indexes"] if item.get("is_primary_key"))
    _, plan = _plain_parent_free_text_plan(profile)
    revised, diagnostics = apply_free_text_reference_interventions(
        plan,
        'For parent, operation: insert_ignore; conflict_target: "id"; id="p1".',
        profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    assert revised["write_groups"][0]["conflict_target_id"] == pk["constraint_id"]
    assert not any(item.severity == "error" for item in diagnostics)


def test_v0_materialization_matches_frozen_fixture():
    import json
    from pathlib import Path

    profile, parent, cols, *_ = _profile_and_ids()
    payload = _payload({"operation": "plain_insert", "id": "p1", "name": "One"})
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": parent["table_id"],
            "field_mapping": {
                collection.field_ids["id"]: cols["id"],
                collection.field_ids["name"]: cols["name"],
            },
            "constants": {},
            "write_semantics": "plain_insert",
            "conflict_target_id": None,
            "update_column_ids": [],
        }],
        "ignored_fields": {},
        "dependencies": [],
    }
    grounded, errors = resolve_reference_mapping_plan(plan, payload, profile)
    assert not errors
    actual = materialize_mapping_plan(grounded, payload, control_field_roles=False)
    expected = json.loads(
        (Path(__file__).parent / "fixtures" / "stage2_v0_materialization_frozen.json").read_text(encoding="utf-8")
    )
    assert actual == expected
    operation_record = next(item for item in actual["unresolved_fields"] if item["field"] == "operation")
    assert "role" not in operation_record


def test_run_lock_records_method_variant_and_version(tmp_path):
    import sqlite3
    from nldbwrite_v3.experiments.run_lock import build_run_lock

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "placeholder.py").write_text("x = 1\n", encoding="utf-8")
    dataset = project / "dataset.json"
    split = project / "split.txt"
    config = project / "config.json"
    profile_dir = project / "profiles"
    profile_dir.mkdir()
    db_root = project / "databases"
    (db_root / "test").mkdir(parents=True)
    db_path = db_root / "test" / "test.sqlite"
    sqlite3.connect(db_path).close()
    dataset.write_text("[]\n", encoding="utf-8")
    split.write_text("s1\n", encoding="utf-8")
    config.write_text("{}\n", encoding="utf-8")
    (profile_dir / "test.json").write_text("{}\n", encoding="utf-8")

    lock = build_run_lock(
        project_root=project,
        stage="dev",
        method_id="MP-FS+",
        method_variant="vnext-v3-update",
        method_version="stage2-v3-control-conflict-update",
        method_config_path=config,
        inference_config_path=None,
        base_config_path=None,
        resolved_config_sha256="resolved",
        dataset_path=dataset,
        split_path=split,
        gold_plans_path=None,
        profile_dir=profile_dir,
        db_root=db_root,
        selected_db_ids=["test"],
        prompt_set_sha256="prompt",
        model_metadata={"backend": "mock"},
        dependency_lock_path=None,
        environment_manifest_path=None,
    )
    assert lock["method_id"] == "MP-FS+"
    assert lock["method_variant"] == "vnext-v3-update"
    assert lock["method_version"] == "stage2-v3-control-conflict-update"


def test_case_only_identifier_collision_is_ambiguous_and_fails_closed():
    profile = test_profile()
    parent = next(table for table in profile["tables"] if table["name"] == "parent")
    parent["columns"].extend([
        {"name": "user_id", "type": "TEXT", "is_insertable": True},
        {"name": "USER_ID", "type": "TEXT", "is_insertable": True},
    ])
    parent["unique_indexes"].append(
        {"name": "uq_user_id", "columns": ["user_id"], "origin": "u", "is_primary_key": False}
    )
    ensure_reference_ids(profile)
    cols = {column["name"]: column["column_id"] for column in parent["columns"]}
    payload = _payload({
        "operation": "insert_ignore",
        "conflict_target": "user_id",
        "id": "p1",
        "name": "One",
    })
    collection = payload.collections[0]
    plan = {
        "target_groups": [{
            "group_id": "g1",
            "source_collection_id": collection.reference_id,
            "source_selector_id": collection.selector_id,
            "table_id": parent["table_id"],
            "field_mapping": {
                collection.field_ids["id"]: cols["id"],
                collection.field_ids["name"]: cols["name"],
            },
            "constants": {},
            "write_semantics": "needs_clarification",
            "conflict_target_id": None,
            "update_column_ids": [],
        }],
        "dependencies": [],
        "ignored_fields": {},
    }
    _, diagnostics = apply_reference_interventions(
        plan, payload, profile,
        Stage2InterventionConfig(explicit_conflict_preservation=True),
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    assert any(item.error_code == "AMBIGUOUS_IDENTIFIER" for item in errors)


def test_run_manifest_records_method_variant_and_version(tmp_path):
    import json
    import sqlite3
    from nldbwrite_v3.experiments.run_method import run_method

    dataset = [{
        "id": "s1",
        "sample_id": "s1",
        "db_id": "test",
        "input_text": "For parent, insert id='p1' and name='One'.",
        "input_mode": "free_text",
        "input_format": "free_text",
        "gold_sql": ["INSERT INTO parent (id,name) VALUES ('p1','One');"],
        "operation_semantics": "plain_insert",
        "conflict_sensitive": False,
        "state_changing": True,
    }]
    data_path = tmp_path / "dataset.json"
    ids_path = tmp_path / "ids.txt"
    profile_dir = tmp_path / "profiles"
    db_root = tmp_path / "databases"
    inference_path = tmp_path / "mock.json"
    output_dir = tmp_path / "out"
    data_path.write_text(json.dumps(dataset), encoding="utf-8")
    ids_path.write_text("s1\n", encoding="utf-8")
    profile_dir.mkdir()
    (profile_dir / "test.json").write_text(
        json.dumps(test_profile()), encoding="utf-8"
    )
    (db_root / "test").mkdir(parents=True)
    connection = sqlite3.connect(db_root / "test" / "test.sqlite")
    connection.executescript(
        "CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT NOT NULL, count INTEGER);"
        "CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id TEXT NOT NULL,note TEXT NOT NULL,"
        " FOREIGN KEY(parent_id) REFERENCES parent(id));"
        "CREATE TABLE pair(a TEXT,b TEXT,value TEXT NOT NULL, PRIMARY KEY(a,b));"
    )
    connection.close()
    inference_path.write_text(
        json.dumps({"backend": "mock", "batch_size": 1, "mock_default_response": "{}"}),
        encoding="utf-8",
    )

    run_method(
        "configs/stage2/v3_update.json",
        data_path,
        ids_path,
        profile_dir,
        db_root,
        output_dir,
        inference_config_path=inference_path,
        resume=False,
        stage="dev",
    )

    expected = {
        "method_id": "MP-FS+",
        "method_variant": "vnext-v3-update",
        "method_version": "stage2-v3-control-conflict-update",
    }
    for artifact in ("run_lock.json", "manifest.json", "summary_metadata.json"):
        value = json.loads((output_dir / artifact).read_text(encoding="utf-8"))
        for key, expected_value in expected.items():
            assert value[key] == expected_value

    evaluation = json.loads(
        (output_dir / "evaluation.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    for key, expected_value in expected.items():
        assert evaluation[key] == expected_value
