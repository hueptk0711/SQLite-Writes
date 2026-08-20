from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.run_method import _load_method_config, _prompt_for_sample
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner.evidence import extract_evidence_candidates
from nldbwrite_v3.schema import ensure_reference_ids
from nldbwrite_v3.vnext.typed_normalization import normalize_free_text_typed_candidate


CONFIG = Path("configs/stage2/v5_free_text_typed_normalization.json")
FIXTURE = Path("tests/fixtures/stage2_e_stage1_normalization_cases.json")


def _profile() -> dict:
    profile = {
        "db_id": "stage2_e_smoke",
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


def _evidence_id(request: str, surface: str) -> str:
    matches = [
        row for row in extract_evidence_candidates(request)
        if row["text"] == surface
    ]
    assert matches, (surface, extract_evidence_candidates(request))
    return str(matches[0]["evidence_id"])


def _reference_plan(request: str, profile: dict) -> dict:
    table = profile["tables"][0]
    columns = {column["name"]: column["column_id"] for column in table["columns"]}
    return {
        "version": "3.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": table["table_id"],
                "rows": [
                    {
                        columns["event_id"]: {
                            "value_from": _evidence_id(request, "EVT1"),
                            "normalization": "identity",
                        },
                        columns["observed_at"]: {
                            "value_from": _evidence_id(
                                request,
                                "2026-07-30 14:47:00",
                            ),
                            "normalization": "iso_date_normalization",
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


def main() -> None:
    v5, _ = _load_method_config(CONFIG)
    v4, _ = _load_method_config(Path("configs/stage2/v4_structured_parser.json"))
    e_config = v5["free_text_typed_normalization"]
    assert e_config["enabled"] is True
    assert v5["stage2_interventions"] == v4["stage2_interventions"]
    assert v5["structured_source_parser"] == v4["structured_source_parser"]

    typed_checks = {}
    for name, value, rule, expected in [
        (
            "datetime_stage1_surface",
            "2026-07-30 14:47:00",
            "iso_date_normalization",
            "2026-07-30 14:47:00",
        ),
        (
            "date_boundary_punctuation",
            "2026-08-19.",
            "iso_date_normalization",
            "2026-08-19",
        ),
        (
            "text_preservation",
            "SC9081.",
            "identity",
            "SC9081.",
        ),
    ]:
        result = normalize_free_text_typed_candidate(
            value,
            {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
            requested_rule=rule,
            candidate_type=(
                "datetime" if " " in value or "T" in value
                else "date" if rule == "iso_date_normalization"
                else "text"
            ),
            config=e_config,
        )
        assert result.error is None
        assert result.value == expected
        if rule == "identity":
            assert result.handled is False
        typed_checks[name] = "PASS"

    ambiguous = normalize_free_text_typed_candidate(
        "01/02/2026",
        {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
        requested_rule="iso_date_normalization",
        candidate_type="date",
        config=e_config,
    )
    assert ambiguous.handled and ambiguous.error is not None
    assert ambiguous.error_code == "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_FORMAT"
    typed_checks["ambiguous_date_fail_closed"] = "PASS"

    wrong_evidence = normalize_free_text_typed_candidate(
        "For",
        {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
        requested_rule="iso_date_normalization",
        candidate_type="text",
        config=e_config,
    )
    assert wrong_evidence.error_code == "TEMPORAL_EVIDENCE_TYPE_MISMATCH"
    typed_checks["wrong_evidence_not_repaired"] = "PASS"

    identifier_target = normalize_free_text_typed_candidate(
        "2026-08-19",
        {"name": "event_id", "type": "TEXT", "semantic_type": "identifier"},
        requested_rule="iso_date_normalization",
        candidate_type="date",
        config=e_config,
    )
    assert identifier_target.error_code == "TEMPORAL_TARGET_SEMANTIC_MISMATCH"
    typed_checks["target_semantic_guard"] = "PASS"

    unchanged_datetime = normalize_free_text_typed_candidate(
        "2026-07-30 14:47:00",
        {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
        requested_rule="iso_date_normalization",
        candidate_type="datetime",
        config=e_config,
    )
    assert unchanged_datetime.error is None
    assert unchanged_datetime.audit["applied"] is True
    assert unchanged_datetime.audit["intervention_applied"] is True
    assert unchanged_datetime.audit["value_changed"] is False
    typed_checks["causal_activation_provenance"] = "PASS"

    missing_type = normalize_free_text_typed_candidate(
        "2026-08-19",
        {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
        requested_rule="iso_date_normalization",
        candidate_type="",
        config=e_config,
    )
    assert missing_type.error_code == "TEMPORAL_EVIDENCE_TYPE_MISSING"
    subtype_mismatch = normalize_free_text_typed_candidate(
        "2026-08-19 12:30:00",
        {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
        requested_rule="iso_date_normalization",
        candidate_type="date",
        config=e_config,
    )
    assert subtype_mismatch.error_code == "TEMPORAL_EVIDENCE_SUBTYPE_MISMATCH"
    typed_checks["candidate_type_invariant"] = "PASS"

    fullwidth = normalize_free_text_typed_candidate(
        "２０２６-０８-１９",
        {"name": "observed_at", "type": "TEXT", "semantic_type": "text"},
        requested_rule="iso_date_normalization",
        candidate_type="date",
        config=e_config,
    )
    assert fullwidth.error_code == "AMBIGUOUS_OR_UNSUPPORTED_TEMPORAL_FORMAT"
    typed_checks["ascii_temporal_grammar"] = "PASS"

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture_results = []
    for case in fixture["cases"]:
        result = normalize_free_text_typed_candidate(
            case["raw_value"],
            {
                "name": "observed_at",
                "type": case["target_column_type"],
                "semantic_type": case["target_semantic_type"],
            },
            requested_rule=case["requested_rule"],
            candidate_type=case["candidate_type"],
            config=e_config,
        )
        if case["expected_status"] == "pass":
            assert result.error is None, case["case_id"]
            assert result.value == case["expected_normalized_value"], case["case_id"]
            status = "PASS"
        else:
            assert result.error_code == "TEMPORAL_EVIDENCE_TYPE_MISMATCH", case["case_id"]
            status = "EXPECTED_REJECT"
        fixture_results.append({"case_id": case["case_id"], "status": status})

    request = "Create event EVT1 observed at 2026-07-30 14:47:00."
    profile = _profile()
    plan = _reference_plan(request, profile)

    prompt4, payload4 = _prompt_for_sample(
        "MP-FS+", {"input_text": request}, profile, v4
    )
    prompt5, payload5 = _prompt_for_sample(
        "MP-FS+", {"input_text": request}, profile, v5
    )
    assert prompt5 == prompt4
    assert payload5.to_dict() == payload4.to_dict()

    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        normalization_mode="lossless",
        stage2_interventions=v5.get("stage2_interventions"),
        structured_source_parser=v5.get("structured_source_parser"),
        free_text_typed_normalization=e_config,
    ).run(request, plan)
    assert result.success, result.to_dict()
    assert result.write_plan is not None
    row = result.write_plan["write_groups"][0]["rows"][0]
    assert row["observed_at"] == "2026-07-30 14:47:00"
    audit = result.write_plan["write_groups"][0]["normalization_audit"][0]["observed_at"]
    assert audit["raw_evidence_span"] == "2026-07-30 14:47:00"
    assert audit["semantic_type"] == "datetime"
    assert audit["intervention_applied"] is True
    assert audit["applied"] is True
    assert audit["value_changed"] is False
    assert result.program is not None
    assert "2026-07-30 14:47:00" in result.program.statements[0].params

    print(
        json.dumps(
            {
                "status": "PASS",
                "config": {
                    "method_id": v5["method_id"],
                    "method_variant": v5["method_variant"],
                    "method_version": v5["method_version"],
                    "free_text_typed_normalization": e_config,
                },
                "typed_normalization": typed_checks,
                "stage1_diagnostic_cases": fixture_results,
                "prompt_identity_v4_v5": "PASS",
                "pipeline": {
                    "normalized_value": row["observed_at"],
                    "raw_evidence_span": audit["raw_evidence_span"],
                    "semantic_type": audit["semantic_type"],
                    "intervention_applied": audit["intervention_applied"],
                    "value_changed": audit["value_changed"],
                    "compiled_params": result.program.statements[0].params,
                    "status": "PASS",
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
