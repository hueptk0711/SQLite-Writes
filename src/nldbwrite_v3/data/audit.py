from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, load_json, read_ids, sha256_file, write_jsonl
from nldbwrite_v3.compiler import compile_verified_plan
from nldbwrite_v3.data.gold_sql import parse_gold_dataset
from nldbwrite_v3.evaluator import find_database, snapshot_database
from nldbwrite_v3.schema import load_profile
from nldbwrite_v3.verifier import verify_write_plan


def compare_snapshots(
    left_path: str | Path,
    right_path: str | Path,
    *,
    left_ids_path: str | Path | None = None,
    right_ids_path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    left_rows = {str(row["id"]): row for row in load_json(left_path)}
    right_rows = {str(row["id"]): row for row in load_json(right_path)}
    left_ids = set(read_ids(left_ids_path)) if left_ids_path else set(left_rows)
    right_ids = set(read_ids(right_ids_path)) if right_ids_path else set(right_rows)
    fields = ("input_text", "gold_sql", "gold_records")
    differences: list[dict[str, Any]] = []
    for sample_id in sorted(left_ids | right_ids):
        if sample_id not in left_ids:
            differences.append({"sample_id": sample_id, "issue_type": "right_only"})
            continue
        if sample_id not in right_ids:
            differences.append({"sample_id": sample_id, "issue_type": "left_only"})
            continue
        left = left_rows.get(sample_id)
        right = right_rows.get(sample_id)
        if left is None or right is None:
            differences.append(
                {
                    "sample_id": sample_id,
                    "issue_type": "split_references_missing_sample",
                }
            )
            continue
        for field in fields:
            if left.get(field) != right.get(field):
                differences.append(
                    {
                        "sample_id": sample_id,
                        "db_id": left.get("db_id") or right.get("db_id"),
                        "issue_type": f"different_{field}",
                        "left_value": json.dumps(
                            left.get(field), ensure_ascii=False, sort_keys=True
                        ),
                        "right_value": json.dumps(
                            right.get(field), ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
    counts = Counter(row["issue_type"] for row in differences)
    return differences, {
        "left_dataset": str(left_path),
        "right_dataset": str(right_path),
        "left_selected_samples": len(left_ids),
        "right_selected_samples": len(right_ids),
        "difference_rows": len(differences),
        "differences_by_type": dict(sorted(counts.items())),
    }


def _write_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _load_profiles(profile_dir: str | Path) -> dict[str, dict[str, Any]]:
    return {
        path.stem: load_profile(path)
        for path in Path(profile_dir).glob("*.json")
    }


def _flatten_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"table": group["table"], "values": row}
        for group in plan.get("write_groups") or []
        for row in group.get("rows") or []
    ]


def _normalized_records(records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        json.dumps(
            {
                "table": record.get("table"),
                "values": record.get("values") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        for record in records
    )


def _gold_executes(
    sample: dict[str, Any],
    database: sqlite3.Connection,
) -> tuple[bool, str | None]:
    conn = database
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("SAVEPOINT audit")
        for statement in sample.get("gold_sql") or []:
            conn.execute(statement)
        conn.execute("ROLLBACK TO audit")
        conn.execute("RELEASE audit")
        return True, None
    except sqlite3.Error as exc:
        try:
            conn.execute("ROLLBACK TO audit")
            conn.execute("RELEASE audit")
        except sqlite3.Error:
            conn.rollback()
        return False, str(exc)


def audit_gold_dataset(
    dataset_path: str | Path,
    profile_dir: str | Path,
    *,
    db_root: str | Path | None = None,
    ids_path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    all_samples = load_json(dataset_path)
    selected_ids = set(read_ids(ids_path)) if ids_path else None
    samples = [
        sample
        for sample in all_samples
        if selected_ids is None or str(sample["id"]) in selected_ids
    ]
    profiles = _load_profiles(profile_dir)
    plans, parse_diagnostics = parse_gold_dataset(samples, profiles=profiles)
    plans_by_id = {str(plan["sample_id"]): plan for plan in plans}
    issues = list(parse_diagnostics)
    metrics = Counter()
    metrics["samples"] = len(samples)
    metrics["parsed"] = len(plans)
    database_cache: dict[str, sqlite3.Connection] = {}
    try:
        for sample in sorted(
            samples,
            key=lambda item: (str(item.get("db_id")), str(item.get("id"))),
        ):
            sample_id = str(sample["id"])
            plan = plans_by_id.get(sample_id)
            if not plan:
                metrics["parse_failure"] += 1
                continue
            profile = profiles.get(str(sample["db_id"]))
            if profile is None:
                metrics["missing_profile"] += 1
                issues.append(
                    {
                        "sample_id": sample_id,
                        "db_id": sample.get("db_id"),
                        "error_code": "MISSING_PROFILE",
                        "message": "No schema profile found.",
                    }
                )
                continue
            verification = verify_write_plan(plan, profile)
            if verification.valid:
                metrics["schema_valid_plan"] += 1
                program = compile_verified_plan(
                    verification.normalized_plan,
                    profile,
                    normalize_values=False,
                )
                if program.status == "success":
                    metrics["compiler_build_success"] += 1
                    metrics["compiled_statement_count"] += len(program.statements)
                else:
                    metrics["compiler_build_failure"] += 1
                    for error in program.errors:
                        issues.append(
                            {
                                "sample_id": sample_id,
                                "db_id": sample.get("db_id"),
                                **error.to_dict(),
                            }
                        )
            else:
                metrics["schema_invalid_plan"] += 1
                for error in verification.errors:
                    issues.append(
                        {
                            "sample_id": sample_id,
                            "db_id": sample.get("db_id"),
                            **error.to_dict(),
                        }
                    )
            old_records = sample.get("gold_records") or []
            if _normalized_records(old_records) == _normalized_records(
                _flatten_records(plan)
            ):
                metrics["gold_records_match_sql"] += 1
            else:
                metrics["gold_records_mismatch_sql"] += 1
                issues.append(
                    {
                        "sample_id": sample_id,
                        "db_id": sample.get("db_id"),
                        "error_code": "GOLD_RECORDS_SQL_MISMATCH",
                        "message": "gold_records differ from rows parsed from gold_sql.",
                    }
                )
            has_non_error_conflict = False
            for group in plan["write_groups"]:
                metrics[f"conflict_{group['conflict']['action']}"] += 1
                has_non_error_conflict = (
                    has_non_error_conflict
                    or group["conflict"]["action"] != "error"
                )
            if has_non_error_conflict:
                metrics["samples_with_non_error_conflict"] += 1
                old_records = sample.get("gold_records") or []
                old_has_explicit_conflict = any(
                    isinstance(record, dict)
                    and (
                        record.get("conflict_action") is not None
                        or isinstance(record.get("conflict"), dict)
                    )
                    for record in old_records
                )
                if not old_has_explicit_conflict:
                    metrics["samples_missing_conflict_in_old_representation"] += 1
            if db_root:
                db_id = str(sample["db_id"])
                try:
                    if db_id not in database_cache:
                        for database in database_cache.values():
                            database.close()
                        database_cache.clear()
                        database_cache[db_id] = snapshot_database(
                            find_database(db_root, db_id)
                        )
                    executable, error = _gold_executes(
                        sample,
                        database_cache[db_id],
                    )
                except FileNotFoundError as exc:
                    executable, error = False, str(exc)
                if executable:
                    metrics["gold_sql_executable"] += 1
                else:
                    metrics["gold_sql_execution_failure"] += 1
                    issues.append(
                        {
                            "sample_id": sample_id,
                            "db_id": sample.get("db_id"),
                            "error_code": "GOLD_SQL_EXECUTION_ERROR",
                            "message": error,
                        }
                    )
    finally:
        for database in database_cache.values():
            database.close()
    return plans, issues, dict(sorted(metrics.items()))


def freeze_dataset(
    dataset_path: str | Path,
    split_path: str | Path,
    profile_dir: str | Path,
    output_dir: str | Path,
    *,
    db_root: str | Path | None = None,
    role: str | None = None,
    disjoint_split_path: str | Path | None = None,
) -> dict[str, Any]:
    if db_root is None:
        raise ValueError(
            "Freeze requires db_root so every gold SQL program is execution-audited"
        )
    selected_ids = read_ids(split_path)
    selected_set = set(selected_ids)
    samples = [
        sample
        for sample in load_json(dataset_path)
        if str(sample["id"]) in selected_set
    ]
    missing = sorted(selected_set - {str(sample["id"]) for sample in samples})
    if missing:
        raise ValueError(f"Split references {len(missing)} missing samples")
    disjointness: dict[str, Any] | None = None
    if disjoint_split_path is not None:
        comparison_ids = set(read_ids(disjoint_split_path))
        all_rows = {
            str(sample["id"]): sample for sample in load_json(dataset_path)
        }
        missing_comparison = sorted(comparison_ids - set(all_rows))
        if missing_comparison:
            raise ValueError(
                "Disjoint comparison split references "
                f"{len(missing_comparison)} missing samples"
            )

        def source_group(sample: dict[str, Any]) -> str:
            provenance = sample.get("provenance")
            return str(
                sample.get("source_group_id")
                or sample.get("source_id")
                or (
                    provenance.get("source_sample_id")
                    if isinstance(provenance, dict)
                    else None
                )
                or sample["id"]
            )

        selected_groups = {source_group(sample) for sample in samples}
        comparison_groups = {
            source_group(all_rows[sample_id])
            for sample_id in comparison_ids
        }
        id_overlap = sorted(selected_set & comparison_ids)
        group_overlap = sorted(selected_groups & comparison_groups)
        if id_overlap or group_overlap:
            raise ValueError(
                "Freeze rejected: disjoint split overlaps by "
                f"{len(id_overlap)} sample IDs and "
                f"{len(group_overlap)} source groups"
            )
        disjointness = {
            "comparison_split": str(Path(disjoint_split_path).resolve()),
            "comparison_split_sha256": sha256_file(disjoint_split_path),
            "sample_id_overlap_count": 0,
            "source_group_overlap_count": 0,
            "selected_source_group_count": len(selected_groups),
            "comparison_source_group_count": len(comparison_groups),
        }
    plans, issues, metrics = audit_gold_dataset(
        dataset_path,
        profile_dir,
        db_root=db_root,
        ids_path=split_path,
    )
    blocking = list(issues)
    if blocking:
        raise ValueError(
            f"Freeze rejected: {len(blocking)} blocking gold-data issues remain"
        )
    if metrics.get("gold_sql_executable") != len(samples):
        raise ValueError(
            "Freeze rejected: gold SQL execution coverage is not 100%"
        )
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    if role not in {None, "dev", "test"}:
        raise ValueError("role must be one of: dev, test")
    artifact_role = role or "test"
    dataset_out = target / f"dataset_{artifact_role}_v3.json"
    split_out = target / f"{artifact_role}_ids_v3.txt"
    plans_out = target / f"gold_write_plans_{artifact_role}_v3.jsonl"
    dump_json(samples, dataset_out)
    split_out.write_text("\n".join(selected_ids) + "\n", encoding="utf-8")
    write_jsonl(plans, plans_out)
    manifest = {
        "version": "3.0",
        "source_dataset": str(Path(dataset_path).resolve()),
        "source_split": str(Path(split_path).resolve()),
        "sample_count": len(samples),
        "role": artifact_role,
        "audit_metrics": metrics,
        "non_blocking_issue_count": len(issues),
        "hashes": {
            "source_dataset_sha256": sha256_file(dataset_path),
            "source_split_sha256": sha256_file(split_path),
            "frozen_dataset_sha256": sha256_file(dataset_out),
            "frozen_split_sha256": sha256_file(split_out),
            "gold_write_plans_sha256": sha256_file(plans_out),
            "database_sha256": {
                db_id: sha256_file(find_database(db_root, db_id))
                for db_id in sorted({str(sample["db_id"]) for sample in samples})
            },
            "profile_sha256": {
                path.stem: sha256_file(path)
                for path in sorted(Path(profile_dir).glob("*.json"))
                if path.stem in {str(sample["db_id"]) for sample in samples}
            },
        },
    }
    if disjointness is not None:
        manifest["disjointness"] = disjointness
    manifest["db_root"] = str(Path(db_root).resolve())
    dump_json(manifest, target / f"frozen_manifest_{artifact_role}.json")
    return manifest


def write_snapshot_report(
    differences: list[dict[str, Any]],
    summary: dict[str, Any],
    csv_path: str | Path,
    summary_path: str | Path,
) -> None:
    _write_csv(differences, csv_path)
    dump_json(summary, summary_path)
