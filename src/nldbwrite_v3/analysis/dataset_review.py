from __future__ import annotations

from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, load_json, read_ids
from nldbwrite_v3.data import audit_gold_dataset
from nldbwrite_v3.evaluator import find_database


def review_added_samples(
    dataset_path: str | Path,
    added_ids_path: str | Path,
    dev_ids_path: str | Path,
    profile_dir: str | Path,
    db_root: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    samples = {str(row["id"]): row for row in load_json(dataset_path)}
    added_ids = read_ids(added_ids_path)
    dev_ids = read_ids(dev_ids_path)
    missing = [sample_id for sample_id in added_ids if sample_id not in samples]
    if missing:
        raise ValueError(f"Added-ID list references {len(missing)} missing samples")
    dev_groups = {
        str(samples[sample_id].get("source_group_id") or sample_id)
        for sample_id in dev_ids
        if sample_id in samples
    }
    plans, issues, audit = audit_gold_dataset(
        dataset_path,
        profile_dir,
        db_root=db_root,
        ids_path=added_ids_path,
    )
    plans_by_id = {str(plan["sample_id"]): plan for plan in plans}
    issues_by_id: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        issues_by_id.setdefault(str(issue.get("sample_id")), []).append(issue)

    rows: list[dict[str, Any]] = []
    for sample_id in added_ids:
        sample = samples[sample_id]
        source_group = str(sample.get("source_group_id") or sample_id)
        sample_issues = issues_by_id.get(sample_id, [])
        issue_codes = [
            str(issue.get("error_code") or "")
            for issue in sample_issues
        ]
        try:
            database_path = str(
                find_database(db_root, str(sample["db_id"])).resolve()
            )
            database_present = True
        except FileNotFoundError:
            database_path = None
            database_present = False
        rows.append(
            {
                "sample_id": sample_id,
                "db_id": sample.get("db_id"),
                "source_group_id": source_group,
                "augmentation_type": sample.get("augmentation_type"),
                "machine_validation_status": sample.get(
                    "machine_validation_status"
                ),
                "database_present": database_present,
                "database_path": database_path,
                "gold_plan_parsed": sample_id in plans_by_id,
                "gold_sql_executable": (
                    "GOLD_SQL_EXECUTION_ERROR" not in issue_codes
                ),
                "annotation_consistent": (
                    "GOLD_RECORDS_SQL_MISMATCH" not in issue_codes
                ),
                "dev_source_group_leakage": source_group in dev_groups,
                "issue_codes": issue_codes,
                "clean": bool(
                    database_present
                    and sample_id in plans_by_id
                    and not sample_issues
                    and source_group not in dev_groups
                ),
            }
        )
    all_clean = all(row["clean"] for row in rows)
    result = {
        "reviewed_samples": len(rows),
        "all_clean": all_clean,
        "recommended_snapshot": "677" if all_clean else "668",
        "dev_source_group_overlap_count": sum(
            bool(row["dev_source_group_leakage"]) for row in rows
        ),
        "audit_metrics": audit,
        "samples": rows,
    }
    dump_json(result, output_path)
    return result

