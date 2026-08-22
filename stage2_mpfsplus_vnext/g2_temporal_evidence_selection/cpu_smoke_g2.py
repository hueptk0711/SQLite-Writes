from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
for item in (PROJECT_ROOT, SRC_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import extract_evidence_candidates
from nldbwrite_v3.schema import ensure_reference_ids


E_CONFIG = {
    "enabled": True,
    "date_normalization": True,
    "datetime_normalization": True,
    "preserve_raw_evidence": True,
    "fail_closed_on_ambiguous_format": True,
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


def main() -> None:
    request = "Use timemark For 2024-07-17 11:50:00."
    profile = {
        "db_id": "stage2_g2_cpu_smoke",
        "tables": [
            {
                "name": "events",
                "columns": [
                    {
                        "name": "timemark",
                        "type": "TEXT",
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    }
                ],
                "required_insert_columns": [],
                "primary_keys": [],
                "unique_indexes": [],
                "foreign_keys": [],
            }
        ],
    }
    ensure_reference_ids(profile)
    table = profile["tables"][0]
    column = table["columns"][0]
    candidates = extract_evidence_candidates(request)
    rejected = next(item for item in candidates if item["text"] == "For")
    expected = next(
        item
        for item in candidates
        if item["candidate_type"] == "datetime"
    )
    plan = {
        "version": "4.0",
        "plan_kind": "reference_write_plan",
        "write_groups": [
            {
                "group_id": "g1",
                "table_id": table["table_id"],
                "rows": [
                    {
                        column["column_id"]: {
                            "value_from": rejected["evidence_id"],
                            "normalization": "iso_date_normalization",
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
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        free_text_typed_normalization=E_CONFIG,
        diagnostic_targeted_repair=G2_CONFIG,
    ).run(request, plan)
    assert result.success
    assert result.program is not None
    assert result.program.statements[0].params == ["2024-07-17 11:50:00"]
    trace = result.write_plan["write_groups"][0]["reference_trace"][
        "diagnostic_targeted_repairs"
    ][0]
    assert trace["old_reference"] == rejected["evidence_id"]
    assert trace["selected_repair"] == expected["evidence_id"]
    assert trace["repair_applied"] is True
    assert trace["repair_succeeded"] is True
    assert trace["revalidation_attempts"] == 1
    print("CPU_SMOKE_G2: PASS")


if __name__ == "__main__":
    main()
