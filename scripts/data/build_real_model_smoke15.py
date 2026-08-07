from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, iter_jsonl, load_json, write_jsonl


SELECTED_DEV_IDS = [
    "seed_000039",
    "aug_seed_000034_single_row_subset_001",
    "aug_seed_000055_single_row_subset_001",
    "seed_000071",
    "aug_seed_000096_small_batch_subset_001",
    "seed_000001",
    "seed_000021",
    "seed_000006",
    "seed_000262",
    "aug_seed_000333_relational_format_variant_002",
    "seed_000359",
]

FREE_TEXT_ACCEPT_PROBES = [
    {
        "id": "smoke_free_accept_001",
        "db_id": "european_football_2",
        "input_text": (
            "Add exactly one record to the Country table. Set id to 990001 "
            "and name to Smoke Validation Country. If id 990001 already "
            "exists, ignore the duplicate and do not update it."
        ),
        "gold_sql": [
            'INSERT INTO "Country" ("id", "name") '
            "VALUES (990001, 'Smoke Validation Country') "
            "ON CONFLICT DO NOTHING;"
        ],
        "gold_tables": ["Country"],
        "gold_columns": ["Country.id", "Country.name"],
    },
    {
        "id": "smoke_free_accept_002",
        "db_id": "debit_card_specializing",
        "input_text": (
            "Add exactly one record to the gasstations table. Set ChainID to "
            "9901, GasStationID to 990001, and Segment to Technical smoke. "
            "If GasStationID 990001 already exists, ignore the duplicate and "
            "do not update it."
        ),
        "gold_sql": [
            'INSERT INTO "gasstations" '
            '("ChainID", "GasStationID", "Segment") '
            "VALUES (9901, 990001, 'Technical smoke') "
            "ON CONFLICT DO NOTHING;"
        ],
        "gold_tables": ["gasstations"],
        "gold_columns": [
            "gasstations.ChainID",
            "gasstations.GasStationID",
            "gasstations.Segment",
        ],
    },
]

CLARIFICATION_PROBES = [
    {
        "id": "smoke_clarification_001",
        "db_id": "student_club",
        "input_text": (
            "Add an expense with expense id recA1b2C3d4E5f6G7H, expense "
            "date 2019-12-01, and approved true. If that expense ID already "
            "exists, handle the conflict appropriately."
        ),
    },
    {
        "id": "smoke_clarification_002",
        "db_id": "student_club",
        "input_text": (
            "Please register expense recB2c3D4e5F6g7H8I dated 2019-12-05 "
            "with approved false. When a duplicate is found, use whichever "
            "duplicate policy is suitable."
        ),
    },
]


def _free_text_accept_probe(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value,
        "source_id": None,
        "gold_records": [],
        "operation_type": "insert",
        "operation_semantics": "insert_ignore",
        "input_type": "natural_language",
        "impact_scope": "row_single_table",
        "difficulty": "technical_probe",
        "num_tables": 1,
        "num_records": 1,
        "row_count": 1,
        "row_count_bucket": "1",
        "table_count": 1,
        "column_count": len(value.get("gold_columns") or []),
        "sql_statement_count": 1,
        "source_group_id": value["id"],
        "is_augmented": True,
        "augmentation_type": "technical_free_text_accept_probe",
        "machine_validation_status": "gold_sql_pending_runtime_validation",
        "expected_outcome": "accept_and_execute",
        "paper_result_eligible": False,
        "technical_smoke_only": True,
        "provenance": {
            "source_dataset": "mp_fs_plus_gpu_smoke15",
            "transformation": "purpose_built_technical_probe",
            "paper_result_eligible": False,
        },
    }


def _operation_semantics(sample: dict[str, Any]) -> str:
    sql = " ".join(str(item) for item in sample.get("gold_sql") or []).upper()
    if "DO UPDATE" in sql:
        return "upsert_update"
    if "DO NOTHING" in sql or "INSERT OR IGNORE" in sql:
        return "insert_ignore"
    return "plain_insert"


def _probe(value: dict[str, str]) -> dict[str, Any]:
    return {
        **value,
        "source_id": None,
        "gold_tables": [],
        "gold_columns": [],
        "gold_records": [],
        "gold_sql": [],
        "operation_type": "needs_clarification",
        "operation_semantics": "needs_clarification",
        "input_type": "natural_language",
        "impact_scope": "row_single_table",
        "difficulty": "technical_probe",
        "num_tables": 1,
        "num_records": 1,
        "row_count": 1,
        "row_count_bucket": "1",
        "table_count": 1,
        "column_count": 3,
        "sql_statement_count": 0,
        "source_group_id": value["id"],
        "is_augmented": True,
        "augmentation_type": "technical_clarification_probe",
        "machine_validation_status": "expected_no_change_if_abstained",
        "expected_outcome": "abstain",
        "expected_error_code": "NEEDS_CLARIFICATION",
        "provenance": {
            "source_dataset": "mp_fs_plus_gpu_smoke15",
            "transformation": "purpose_built_technical_probe",
            "paper_result_eligible": False,
        },
    }


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    operation_counts = Counter()
    input_counts = Counter()
    complexity_counts = Counter()
    database_counts = Counter()
    for sample in samples:
        semantics = str(
            sample.get("operation_semantics") or _operation_semantics(sample)
        )
        operation_counts[semantics] += 1
        input_counts[str(sample.get("input_type") or "unknown")] += 1
        impact = str(sample.get("impact_scope") or "unknown")
        complexity_counts[impact] += 1
        database_counts[str(sample.get("db_id") or "unknown")] += 1
    return {
        "sample_count": len(samples),
        "operation_counts": dict(sorted(operation_counts.items())),
        "input_type_counts": dict(sorted(input_counts.items())),
        "impact_scope_counts": dict(sorted(complexity_counts.items())),
        "database_counts": dict(sorted(database_counts.items())),
        "multi_table_count": sum(
            int(sample.get("num_tables") or sample.get("table_count") or 0) > 1
            for sample in samples
        ),
        "large_batch_count": sum(
            int(sample.get("row_count") or sample.get("num_records") or 0) > 20
            for sample in samples
        ),
        "clarification_probe_count": sum(
            sample.get("expected_outcome") == "abstain" for sample in samples
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the non-reportable 15-sample real-model technical smoke set."
        )
    )
    parser.add_argument(
        "--data",
        default="data/frozen/dev/dataset_dev_v3.json",
    )
    parser.add_argument(
        "--gold-plans",
        default="data/frozen/dev/gold_write_plans_dev_v3.jsonl",
    )
    parser.add_argument(
        "--frozen-manifest",
        default="data/frozen/dev/frozen_manifest_dev.json",
    )
    parser.add_argument(
        "--output-dir",
        default="data/smoke/real_model_smoke15",
    )
    args = parser.parse_args()

    all_samples = {
        str(sample["id"]): sample for sample in load_json(args.data)
    }
    missing = [sample_id for sample_id in SELECTED_DEV_IDS if sample_id not in all_samples]
    if missing:
        raise ValueError("Missing selected DEV samples: " + ", ".join(missing))
    samples = [deepcopy(all_samples[sample_id]) for sample_id in SELECTED_DEV_IDS]
    for sample in samples:
        sample["operation_semantics"] = _operation_semantics(sample)
        sample["paper_result_eligible"] = False
        sample["technical_smoke_only"] = True
    samples.extend(
        _free_text_accept_probe(item) for item in FREE_TEXT_ACCEPT_PROBES
    )
    samples.extend(_probe(item) for item in CLARIFICATION_PROBES)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_json(samples, output_dir / "dataset.json")
    ids = [str(sample["id"]) for sample in samples]
    (output_dir / "ids.txt").write_text(
        "".join(f"{sample_id}\n" for sample_id in ids),
        encoding="utf-8",
    )

    selected = set(SELECTED_DEV_IDS)
    gold_rows = [
        row
        for row in iter_jsonl(args.gold_plans)
        if str(row.get("sample_id") or row.get("id") or "") in selected
    ]
    if len(gold_rows) != len(selected):
        raise ValueError(
            f"Expected {len(selected)} selected gold plans, got {len(gold_rows)}"
        )
    write_jsonl(gold_rows, output_dir / "gold_write_plans.jsonl")

    summary = _summarize(samples)
    if summary["sample_count"] != 15:
        raise ValueError(f"Smoke set must contain 15 samples, got {summary}")
    if summary["clarification_probe_count"] != 2:
        raise ValueError("Smoke set must contain exactly two clarification probes")
    if summary["multi_table_count"] < 3 or summary["large_batch_count"] < 4:
        raise ValueError(f"Smoke coverage is too narrow: {summary}")
    manifest = {
        "smoke_set_id": "mp_fs_plus_real_model_smoke15_v2",
        "purpose": "technical_model_backend_validation_only",
        "paper_result_eligible": False,
        "accuracy_claim_permitted": False,
        "selected_consumed_dev_ids": SELECTED_DEV_IDS,
        "expected_abstention_ids": [
            str(item["id"]) for item in CLARIFICATION_PROBES
        ],
        "expected_accept_and_execute_ids": [
            str(item["id"]) for item in FREE_TEXT_ACCEPT_PROBES
        ],
        "summary": summary,
    }
    dump_json(manifest, output_dir / "selection_manifest.json")
    frozen_manifest = load_json(args.frozen_manifest)
    selected_databases = sorted(
        {str(sample["db_id"]) for sample in samples}
    )
    frozen_hashes = frozen_manifest.get("hashes") or {}
    server_asset_manifest = {
        "manifest_id": "mp_fs_plus_smoke15_external_assets_v1",
        "purpose": "verify_preexisting_server_profiles_and_databases",
        "hashes": {
            "database_sha256": {
                db_id: (frozen_hashes.get("database_sha256") or {})[db_id]
                for db_id in selected_databases
            },
            "profile_sha256": {
                db_id: (frozen_hashes.get("profile_sha256") or {})[db_id]
                for db_id in selected_databases
            },
        },
    }
    dump_json(
        server_asset_manifest,
        output_dir / "server_external_assets_manifest.json",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
