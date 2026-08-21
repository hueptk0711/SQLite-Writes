from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
for item in (PROJECT_ROOT, SRC_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from nldbwrite_v3.experiments.run_method import _load_method_config, _prompt_for_sample
from nldbwrite_v3.ir import Diagnostic, SourceCollection, SourcePayload
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner.evidence import extract_evidence_candidates
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.vnext.reference_repair import (
    ConstrainedReferenceRepairConfig,
    attempt_constrained_reference_repair,
    mark_revalidation_outcome,
    repair_free_text_plan_after_diagnostics,
    repair_mapping_plan_after_diagnostics,
)

CONFIG = Path("configs/stage2/v6_constrained_reference_repair.json")
FIXTURE = Path("tests/fixtures/stage2_f_stage1_reference_cases.json")


def _profile() -> dict:
    profile = {
        "db_id": "stage2_f_smoke",
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


def _free_plan(
    request: str,
    profile: dict,
    *,
    raw_column: str | None = None,
    raw_table: str | None = None,
    evidence_id: str | None = None,
) -> dict:
    table = profile["tables"][0]
    columns = {item["name"]: item["column_id"] for item in table["columns"]}
    evidence = extract_evidence_candidates(request)
    return {
        "version": "3.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": raw_table or table["table_id"],
                "rows": [
                    {
                        raw_column or columns["event_id"]: {
                            "value_from": evidence_id or evidence[0]["evidence_id"],
                            "normalization": "identity",
                        }
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


def _diag(code: str, path: str, candidates: list[str], details: dict | None = None) -> Diagnostic:
    return Diagnostic(code, code, path=path, candidates=candidates, details=details or {})


def _semi_payload_and_plan() -> tuple[dict, SourcePayload, dict, dict, dict]:
    profile = {
        "db_id": "stage2_f_semi",
        "tables": [
            {
                "name": "parent",
                "columns": [
                    {"name": "id", "type": "TEXT", "is_primary_key": True, "is_insertable": True, "preserve_as_text": True},
                    {"name": "name", "type": "TEXT", "is_insertable": True, "preserve_as_text": True},
                ],
                "required_insert_columns": ["id"],
                "primary_keys": ["id"],
                "unique_indexes": [{"name": "PRIMARY_KEY", "columns": ["id"], "origin": "pk", "is_primary_key": True}],
                "foreign_keys": [],
            }
        ],
    }
    ensure_reference_ids(profile)
    table = profile["tables"][0]
    columns = {item["name"]: item["column_id"] for item in table["columns"]}
    collection = SourceCollection(
        collection_id="records",
        source_path="$[*]",
        source_format="json_array",
        rows=[{"id": "P1", "name": "One"}],
        fields=["id", "name"],
        reference_id="c1",
        selector_id="s1",
        field_ids={"id": "c1.f1", "name": "c1.f2"},
        metadata={"control_metadata": []},
    )
    payload = SourcePayload(
        mode="semi_structured",
        source_format="json_array",
        collections=[collection],
        instruction_text="",
        raw_text="",
    )
    plan = {
        "target_groups": [
            {
                "group_id": "g1",
                "source_collection_id": "c1",
                "source_selector_id": "s1",
                "table_id": table["table_id"],
                "field_mapping": {"c1.f1": columns["id"], "c1.f2": columns["name"]},
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
    return profile, payload, plan, table, columns


def main() -> None:
    v6, _ = _load_method_config(CONFIG)
    v5, _ = _load_method_config(Path("configs/stage2/v5_free_text_typed_normalization.json"))
    f_config = v6["constrained_reference_repair"]
    config = ConstrainedReferenceRepairConfig.from_mapping(f_config)
    assert config.enabled and config.max_attempts_per_slot == 1
    for key, value in v5.items():
        if key in {"method_variant", "method_version"}:
            continue
        assert v6[key] == value, key

    checks: dict[str, str] = {}

    exact = attempt_constrained_reference_repair(
        "t5.userregistry",
        ["t5.c1", "t5.c7"],
        reference_kind="column",
        slot_path="/x",
        config=config,
        named_references={"t5.c1": "nicklabel", "t5.c7": "userregistry"},
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert exact.replacement == "t5.c7" and exact.trace["repair_succeeded"] is False
    checks["unique_exact_identifier_name"] = "PASS"

    singleton = attempt_constrained_reference_repair(
        "bad", ["only"], reference_kind="column", slot_path="/x",
        config=config, validation_before="UNKNOWN_COLUMN_ID",
    )
    assert singleton.replacement == "only"
    checks["unique_closed_set_candidate"] = "PASS"

    ambiguous = attempt_constrained_reference_repair(
        "bad", ["c1", "c2"], reference_kind="column", slot_path="/x",
        config=config, validation_before="UNKNOWN_COLUMN_ID",
    )
    assert ambiguous.attempted and not ambiguous.applied
    checks["ambiguous_closed_set_fail_closed"] = "PASS"

    valid = attempt_constrained_reference_repair(
        "c1", ["c1", "c2"], reference_kind="column", slot_path="/x",
        config=config, validation_before="PASS",
    )
    assert not valid.attempted and valid.trace["repair_rule"] == "already_valid_reference"
    checks["valid_reference_not_repaired"] = "PASS"

    missing = attempt_constrained_reference_repair(
        "", ["only"], reference_kind="column", slot_path="/x",
        config=config, validation_before="UNKNOWN_COLUMN_ID",
    )
    assert not missing.attempted and not missing.applied
    checks["missing_slot_not_autofilled"] = "PASS"

    fuzzy = attempt_constrained_reference_repair(
        "t5.userregistry",
        ["t5.c1", "t5.c7"],
        reference_kind="column",
        slot_path="/x",
        config=config,
        named_references={"t5.c1": "other", "t5.c7": "user_registry"},
        validation_before="UNKNOWN_COLUMN_ID",
    )
    assert not fuzzy.applied
    checks["no_fuzzy_similarity"] = "PASS"

    finalized = mark_revalidation_outcome([exact.trace], [])
    assert finalized[0]["repair_succeeded"] is True
    assert finalized[0]["validation_after"] == "PASS"
    checks["success_only_after_revalidation"] = "PASS"

    semi_profile, payload, semi_plan, table, columns = _semi_payload_and_plan()
    revised = deepcopy(semi_plan)
    raw = f"{table['table_id']}.name"
    revised["target_groups"][0]["field_mapping"]["c1.f2"] = raw
    outcome = repair_mapping_plan_after_diagnostics(
        revised,
        payload,
        semi_profile,
        [_diag("UNKNOWN_COLUMN_ID", "/target_groups/0/field_mapping/c1.f2", list(columns.values()), {"predicted_column_id": raw})],
        config,
    )
    assert outcome.applied
    assert outcome.plan["target_groups"][0]["field_mapping"]["c1.f2"] == columns["name"]
    assert outcome.plan["target_groups"][0]["write_semantics"] == "plain_insert"
    assert outcome.plan["target_groups"][0]["conflict_target_id"] is None
    assert outcome.plan["target_groups"][0]["update_column_ids"] == []
    checks["preserve_non_reference_semantics"] = "PASS"

    protected = repair_mapping_plan_after_diagnostics(
        semi_plan,
        payload,
        semi_profile,
        [_diag("UNKNOWN_CONSTRAINT_ID", "/target_groups/0/conflict_target_id", [f"{table['table_id']}.u1"])],
        config,
    )
    assert not protected.applied
    assert protected.traces[0]["repair_rule"] == "protected_semantics_not_repairable"
    checks["protected_conflict_semantics"] = "PASS"

    profile = _profile()
    table = profile["tables"][0]
    request = "Insert value Z9"
    exact_plan = _free_plan(request, profile, raw_column=f"{table['table_id']}.event_id")
    pipeline = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=f_config,
    ).run(request, exact_plan)
    assert pipeline.success and pipeline.write_plan is not None
    trace = pipeline.write_plan["write_groups"][0]["reference_trace"]["constrained_reference_repairs"]
    assert len(trace) == 1 and trace[0]["repair_succeeded"] is True
    checks["pipeline_free_text_reference_repair"] = "PASS"

    wrong_evidence = _free_plan("Set id P1 name One.", profile, evidence_id="e999")
    rejected = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=f_config,
    ).run("Set id P1 name One.", wrong_evidence)
    err = next(item for item in rejected.verification.errors if item.error_code == "UNKNOWN_EVIDENCE_ID")
    assert err.details["reference_repair"]["repair_rule"] == "protected_semantics_not_repairable"
    checks["protected_evidence_semantics"] = "PASS"

    one_retry = _free_plan(
        request,
        profile,
        raw_table=f"t99.{table['name']}",
        raw_column="t99.event_id",
    )
    stopped = MappingFirstPipeline(
        profile,
        reference_planning=True,
        constrained_reference_repair=f_config,
    ).run(request, one_retry)
    assert stopped.stage == "evidence_materialization"
    assert any(item.error_code == "UNKNOWN_COLUMN_ID" for item in stopped.verification.errors)
    warnings = [item for item in stopped.verification.warnings if item.error_code == "CONSTRAINED_REFERENCE_REPAIR_APPLIED"]
    assert len(warnings) == 1 and warnings[0].details["reference_kind"] == "table"
    checks["single_retry_no_recursive_repair"] = "PASS"

    collision_plan = _free_plan(request, profile)
    columns_by_name = {item["name"]: item["column_id"] for item in table["columns"]}
    invalid_key = f"{table['table_id']}.event_id"
    collision_plan["write_groups"][0]["rows"][0][invalid_key] = {
        "value_from": "e2",
        "normalization": "identity",
    }
    collision_original = deepcopy(collision_plan)
    collision_outcome = repair_free_text_plan_after_diagnostics(
        collision_plan,
        profile,
        [
            _diag(
                "UNKNOWN_COLUMN_ID",
                f"/write_groups/0/rows/0/{invalid_key}",
                list(columns_by_name.values()),
            )
        ],
        config,
    )
    assert collision_plan == collision_original
    assert collision_outcome.plan == collision_original
    assert not collision_outcome.applied
    assert collision_outcome.traces[0]["repair_rule"] == "replacement_slot_collision"
    assert collision_outcome.traces[0]["repair_applied"] is False
    checks["replacement_key_collision_fail_closed"] = "PASS"

    alias_plan = deepcopy(semi_plan)
    alias_group = alias_plan["target_groups"][0]
    source_collection = payload.collections[0]
    alias_group["field_mapping"].pop(source_collection.field_ids["id"])
    alias_group["field_mapping"]["id"] = columns["id"]
    alias_group["field_mapping"]["c9.id"] = columns["name"]
    alias_original = deepcopy(alias_plan)
    alias_outcome = repair_mapping_plan_after_diagnostics(
        alias_plan,
        payload,
        semi_profile,
        [
            _diag(
                "UNKNOWN_SOURCE_FIELD_ID",
                "/target_groups/0/field_mapping/c9.id",
                list(source_collection.field_ids.values()),
            )
        ],
        config,
    )
    assert alias_plan == alias_original
    assert alias_outcome.plan == alias_original
    assert not alias_outcome.applied
    assert (
        alias_outcome.traces[0]["repair_rule"]
        == "replacement_semantic_slot_collision"
    )
    assert alias_outcome.traces[0]["repair_applied"] is False
    checks["source_field_semantic_alias_collision_fail_closed"] = "PASS"

    sample = {"input_text": request}
    prompt5, source5 = _prompt_for_sample("MP-FS+", sample, profile, v5)
    prompt6, source6 = _prompt_for_sample("MP-FS+", sample, profile, v6)
    assert prompt5 == prompt6 and source5.to_dict() == source6.to_dict()
    checks["prompt_identity_v5_v6"] = "PASS"

    fixture = json.loads(FIXTURE.read_text())
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
    rows = {item["sample_id"]: item for item in fixture["cases"]}
    assert rows["final_vaccine_018"]["classification"] == "NON_REPAIRABLE_REFERENCE"
    assert rows["final_vaccine_033"]["repairable_reference_error_count"] == 8
    checks["stage1_reference_classification"] = "PASS"
    checks["repair_rule_accounting"] = "PASS"

    print(
        json.dumps(
            {
                "status": "PASS",
                "config": {
                    "method_id": v6["method_id"],
                    "method_variant": v6["method_variant"],
                    "method_version": v6["method_version"],
                    "constrained_reference_repair": f_config,
                },
                "constrained_reference_repair": checks,
                "stage1_diagnostic_classification": fixture["counts"],
                "stage1_diagnostic_case_count": len(fixture["cases"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
