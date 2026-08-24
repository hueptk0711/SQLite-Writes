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


def main() -> None:
    request = "Insert event_id EVT9081. Leave all other fields unchanged."
    profile = {
        "db_id": "stage2_g1_cpu_smoke",
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
                    }
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
    table = profile["tables"][0]
    column = table["columns"][0]
    selected = next(
        item
        for item in extract_evidence_candidates(request)
        if item["text"] == "EVT9081."
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
                            "value_from": selected["evidence_id"],
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
    result = MappingFirstPipeline(
        profile,
        reference_planning=True,
        diagnostic_targeted_repair=G1_CONFIG,
    ).run(request, plan)
    assert result.success
    assert result.program is not None
    assert result.program.statements[0].params == ["EVT9081"]
    trace = result.write_plan["write_groups"][0]["reference_trace"][
        "diagnostic_targeted_repairs"
    ][0]
    assert trace["repair_applied"] is True
    assert trace["repair_succeeded"] is True
    assert trace["revalidation_attempts"] == 1
    print("CPU_SMOKE_G1: PASS")


if __name__ == "__main__":
    main()
