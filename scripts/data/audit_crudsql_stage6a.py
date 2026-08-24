#!/usr/bin/env python3
"""CPU-only Stage 6A eligibility audit for CRUDSQL.

This script does not call a model and does not register a confirmation set.
It verifies whether the public CRUDSQL official test split has an eligible
type=0 Create/insert subset that can be deterministically adapted to SQLite
write-state evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CRUDSQL_COMMIT = "63bfce67d8391185453a812751e115a499201363"
EXPECTED_REPO_URL = "https://github.com/bizard-lab/CRUDSQL.git"
TYPE_LABELS = {0: "Create", 1: "Delete", 2: "Update", 3: "Read"}
STAGE5_METHOD_FREEZE_COMMIT = "79f6a82144ec0407444ef37121f70eed2b20e01c"
STAGE5_PROTOCOL_COMMIT = "a7742b4c9150ab208e7c5d6708f0dff40bf05440"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def git_output(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def split_paths(crudsql_root: Path, split: str) -> dict[str, Path]:
    return {
        "sql": crudsql_root / "data" / split / f"crud_{split}_sql.json",
        "table": crudsql_root / "data" / split / f"crud_{split}_table.json",
        "db": crudsql_root / "data" / split / f"{split}.db",
    }


def load_split(crudsql_root: Path, split: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = split_paths(crudsql_root, split)
    return read_json(paths["sql"]), read_json(paths["table"])


def validate_sqlite_db(db_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        row_counts = {
            table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
    finally:
        con.close()
    return {
        "path": db_path.as_posix(),
        "sha256": sha256_file(db_path),
        "opens_with_sqlite": True,
        "integrity_check": integrity,
        "table_count": len(tables),
        "total_rows": sum(row_counts.values()),
    }


def display_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def compile_type0_insert(
    sample: dict[str, Any],
    table_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    table_id = sample["table_id"]
    table = table_by_id[table_id]
    sql = sample["sql"]
    columns: list[str] = []
    values: list[Any] = []
    errors: list[str] = []
    for condition in sql.get("conds") or []:
        if len(condition) != 3:
            errors.append("condition_not_triplet")
            continue
        column_index, operator_index, value = condition
        if operator_index != 2:
            errors.append("type0_condition_operator_not_equals")
        if not isinstance(column_index, int) or not 0 <= column_index < len(table["header"]):
            errors.append("condition_column_index_out_of_range")
            continue
        columns.append(f"col_{column_index + 1}")
        values.append(value)
    if not columns:
        errors.append("no_insert_columns")
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    statement = f'INSERT INTO "Table_{table_id}" ({quoted_columns}) VALUES ({placeholders})'
    return {
        "sample_id": sample.get("instance_id") or sample.get("id") or "",
        "table_id": table_id,
        "question": sample["question"],
        "question_sha256": sha256_text(sample["question"]),
        "canonical_content_sha256": sha256_text(
            canonical_json(
                {
                    "table_id": table_id,
                    "question": sample["question"],
                    "sql": sample["sql"],
                }
            )
        ),
        "insert_sql_template": statement,
        "columns": columns,
        "values": values,
        "compile_errors": errors,
    }


def execute_type0_adapters(
    crudsql_root: Path,
    split: str,
    sql_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    table_by_id = {row["id"]: row for row in table_rows}
    type0_rows = [row for row in sql_rows if row.get("sql", {}).get("type") == 0]
    results: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    source = sqlite3.connect(split_paths(crudsql_root, split)["db"])
    con = sqlite3.connect(":memory:")
    try:
        source.backup(con)
    finally:
        source.close()
    try:
        for index, sample in enumerate(type0_rows):
            row = compile_type0_insert(sample, table_by_id)
            row["official_split"] = split
            row["official_split_index"] = index
            if row["compile_errors"]:
                for error in row["compile_errors"]:
                    failures[error] += 1
                row["adapter_status"] = "FAIL"
                results.append(row)
                continue
            table_name = f'Table_{row["table_id"]}'
            try:
                before = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                con.execute(row["insert_sql_template"], row["values"])
                after = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                con.commit()
            except Exception as exc:  # pragma: no cover - exact sqlite message varies.
                failures["sqlite_execution_error"] += 1
                row["adapter_status"] = "FAIL"
                row["execution_error"] = str(exc)
                results.append(row)
                continue
            if after != before + 1:
                failures["row_count_did_not_increment_by_one"] += 1
                row["adapter_status"] = "FAIL"
            else:
                row["adapter_status"] = "PASS"
            row["pre_insert_row_count"] = before
            row["post_insert_row_count"] = after
            results.append(row)
    finally:
        con.close()
    return results, failures


def read_zip_json(zip_path: Path, suffix: str) -> Any | None:
    if not zip_path.is_file():
        return None
    with zipfile.ZipFile(zip_path) as archive:
        matches = [name for name in archive.namelist() if name.endswith(suffix)]
        if not matches:
            return None
        with archive.open(matches[0]) as handle:
            return json.loads(handle.read().decode("utf-8"))


def load_seen_reference_sets(project_root: Path) -> dict[str, Any]:
    references: dict[str, Any] = {
        "sample_ids": set(),
        "source_groups": set(),
        "database_ids": set(),
        "input_text_sha256": set(),
        "canonical_content_sha256": set(),
        "sources": [],
    }

    stage4_manifest_path = project_root / "stage4_fresh_7b_protocol" / "data" / "fresh_dataset_manifest.json"
    if stage4_manifest_path.is_file():
        manifest = read_json(stage4_manifest_path)
        references["sample_ids"].update(manifest.get("selected_sample_ids") or [])
        references["database_ids"].update((manifest.get("database_counts") or {}).keys())
        references["sources"].append(
            {
                "name": "stage4_fresh_300",
                "path": display_path(stage4_manifest_path, project_root),
                "sample_count": manifest.get("sample_count"),
                "sha256": sha256_file(stage4_manifest_path),
            }
        )

    final_zip = (
        project_root
        / "03_protocol_and_data"
        / "final_holdout_release"
        / "mp_fs_plus_external_holdout_300_20260731.zip"
    )
    final_dataset = read_zip_json(final_zip, "dataset.final.json")
    final_manifest = read_zip_json(final_zip, "FINAL_RELEASE_MANIFEST.json")
    if final_manifest:
        references["database_ids"].update(final_manifest.get("database_ids") or [])
    if final_dataset:
        rows = final_dataset if isinstance(final_dataset, list) else final_dataset.get("samples", [])
        for row in rows:
            sample_id = str(row.get("id") or row.get("sample_id") or "")
            question = str(row.get("question") or row.get("input") or row.get("instruction") or "")
            references["sample_ids"].add(sample_id)
            references["source_groups"].add(str(row.get("source_group") or sample_id))
            references["database_ids"].add(str(row.get("db_id") or row.get("database_id") or ""))
            if question:
                references["input_text_sha256"].add(sha256_text(question))
            references["canonical_content_sha256"].add(sha256_text(canonical_json(row)))
        references["sources"].append(
            {
                "name": "final_holdout_release_300",
                "path": display_path(final_zip, project_root),
                "sample_count": len(rows),
                "sha256": sha256_file(final_zip),
            }
        )

    archived_stage4_dataset = Path(
        r"D:\paper kltn\text to sql\99_archive_history_20260731\legacy_sources_and_results\server_downloads\paper_v3_release_20260726\source\paper_v3_mapping_first\data\frozen\test\dataset_test_v3.json"
    )
    if archived_stage4_dataset.is_file():
        rows = read_json(archived_stage4_dataset)
        if not isinstance(rows, list):
            rows = rows.get("samples", [])
        for row in rows:
            sample_id = str(row.get("id") or row.get("sample_id") or "")
            question = str(row.get("question") or row.get("input") or row.get("instruction") or "")
            references["sample_ids"].add(sample_id)
            references["source_groups"].add(str(row.get("source_group") or sample_id))
            references["database_ids"].add(str(row.get("db_id") or row.get("database_id") or ""))
            if question:
                references["input_text_sha256"].add(sha256_text(question))
            references["canonical_content_sha256"].add(sha256_text(canonical_json(row)))
        references["sources"].append(
            {
                "name": "archived_677_pool",
                "path": archived_stage4_dataset.as_posix(),
                "sample_count": len(rows),
                "sha256": sha256_file(archived_stage4_dataset),
            }
        )

    return references


def overlap_audit(eligible_rows: list[dict[str, Any]], references: dict[str, Any]) -> dict[str, Any]:
    sample_ids = {row["sample_id"] for row in eligible_rows}
    source_groups = {f'crudsql:{row["table_id"]}' for row in eligible_rows}
    database_ids = {f'crudsql:{row["table_id"]}' for row in eligible_rows}
    input_hashes = {row["question_sha256"] for row in eligible_rows}
    content_hashes = {row["canonical_content_sha256"] for row in eligible_rows}
    return {
        "status": "PASS",
        "reference_sources": references["sources"],
        "crudsql_sample_count": len(eligible_rows),
        "sample_id_overlap_count": len(sample_ids & references["sample_ids"]),
        "source_group_overlap_count": len(source_groups & references["source_groups"]),
        "database_overlap_count": len(database_ids & references["database_ids"]),
        "input_text_hash_overlap_count": len(input_hashes & references["input_text_sha256"]),
        "canonical_content_hash_overlap_count": len(
            content_hashes & references["canonical_content_sha256"]
        ),
        "disclosure": (
            "CRUDSQL uses upstream table IDs prefixed with crudsql: for source_group "
            "and database identity in this audit."
        ),
    }


def mcnemar_two_sided_p_no_regressions(favorable: int) -> float:
    if favorable <= 0:
        return 1.0
    return min(1.0, 2.0 ** (1 - favorable))


def sample_size_sensitivity(candidate_n: int) -> dict[str, Any]:
    rows = []
    for n in [300, 400, 500, candidate_n]:
        if n <= 0:
            continue
        favorable = 1
        while mcnemar_two_sided_p_no_regressions(favorable) >= 0.025:
            favorable += 1
        rows.append(
            {
                "n": n,
                "minimum_favorable_discordant_pairs_with_zero_regressions_for_p_lt_0_025": favorable,
                "minimum_favorable_rate": favorable / n,
                "exact_two_sided_mcnemar_p": mcnemar_two_sided_p_no_regressions(favorable),
            }
        )
    return {
        "status": "PASS",
        "hypothesis": "H2_D_F_G1_vs_D_G1",
        "declared_family": ["H1_method_level_confirmation", "H2_F_incremental_confirmation"],
        "holm_floor_used_for_sensitivity": 0.025,
        "recommendation": "use_all_official_test_type0_examples_if_eligibility_passes",
        "candidate_n": candidate_n,
        "rows": rows,
    }


def source_file_hashes(crudsql_root: Path) -> dict[str, str]:
    paths = [
        "README.md",
        "LICENSE",
        "data/train/crud_train_sql.json",
        "data/train/crud_train_table.json",
        "data/train/train.db",
        "data/dev/crud_dev_sql.json",
        "data/dev/crud_dev_table.json",
        "data/dev/dev.db",
        "data/test/crud_test_sql.json",
        "data/test/crud_test_table.json",
        "data/test/test.db",
    ]
    return {path: sha256_file(crudsql_root / path) for path in paths if (crudsql_root / path).is_file()}


def run_audit(crudsql_root: Path, out_dir: Path, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    crudsql_root = crudsql_root.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = out_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    commit = git_output(crudsql_root, "rev-parse", "HEAD")
    status = git_output(crudsql_root, "status", "--porcelain")
    file_hashes = source_file_hashes(crudsql_root)

    split_counts: dict[str, Any] = {}
    sqlite_checks: dict[str, Any] = {}
    for split in ["train", "dev", "test"]:
        sql_rows, _ = load_split(crudsql_root, split)
        split_counts[split] = {
            "total": len(sql_rows),
            "by_type": {
                str(key): value for key, value in sorted(Counter(row["sql"]["type"] for row in sql_rows).items())
            },
            "by_type_label": {
                TYPE_LABELS[key]: value
                for key, value in sorted(Counter(row["sql"]["type"] for row in sql_rows).items())
            },
        }
        sqlite_checks[split] = validate_sqlite_db(split_paths(crudsql_root, split)["db"])
        sqlite_checks[split]["path"] = f"data/{split}/{split}.db"

    test_sql, test_tables = load_split(crudsql_root, "test")
    adapter_rows, adapter_failures = execute_type0_adapters(crudsql_root, "test", test_sql, test_tables)
    eligible_rows = [row for row in adapter_rows if row["adapter_status"] == "PASS"]
    official_ids = [
        f"crudsql_test_type0_{row['official_split_index']:04d}_{row['table_id']}"
        for row in eligible_rows
    ]
    for sample_id, row in zip(official_ids, eligible_rows):
        row["stage6_candidate_sample_id"] = sample_id

    references = load_seen_reference_sets(project_root)
    overlap = overlap_audit(eligible_rows, references)
    sensitivity = sample_size_sensitivity(len(eligible_rows))

    decision_status = "PASS_ELIGIBLE_FOR_STAGE6B_REGISTRATION" if (
        commit == EXPECTED_CRUDSQL_COMMIT
        and status == ""
        and split_counts["test"]["by_type"].get("0") == 500
        and len(eligible_rows) >= 300
        and not adapter_failures
        and overlap["sample_id_overlap_count"] == 0
        and overlap["input_text_hash_overlap_count"] == 0
        and overlap["canonical_content_hash_overlap_count"] == 0
        and all(row["integrity_check"] == "ok" for row in sqlite_checks.values())
    ) else "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"

    registry = {
        "stage": "Stage6A_CRUDSQL_ELIGIBILITY_AUDIT",
        "status": decision_status,
        "registration_status": "not_registered_in_stage6a",
        "model_called": False,
        "gpu_called": False,
        "source_name": "CRUDSQL",
        "source_repository": EXPECTED_REPO_URL,
        "source_commit": commit,
        "expected_source_commit": EXPECTED_CRUDSQL_COMMIT,
        "source_git_status_porcelain": status,
        "license": {
            "path": "LICENSE",
            "declared_license": "GPL-3.0",
            "sha256": file_hashes.get("LICENSE"),
        },
        "official_split_policy": "official_test_split_only",
        "candidate_subset_policy": "all_official_test_type0_Create_examples_if_adapter_passes",
        "no_translation_or_paraphrase": True,
        "sample_count_floor": 300,
        "recommended_exact_n": len(eligible_rows),
        "stage5_method_freeze_commit": STAGE5_METHOD_FREEZE_COMMIT,
        "stage5_protocol_commit": STAGE5_PROTOCOL_COMMIT,
    }
    audit = {
        "stage": "Stage6A_CRUDSQL_ELIGIBILITY_AUDIT",
        "status": decision_status,
        "model_called": False,
        "gpu_called": False,
        "registration_status": "not_registered_in_stage6a",
        "split_counts": split_counts,
        "sqlite_checks": sqlite_checks,
        "type0_adapter": {
            "official_test_type0_count": len(adapter_rows),
            "adapter_pass_count": len(eligible_rows),
            "adapter_fail_count": len(adapter_rows) - len(eligible_rows),
            "failure_counts": dict(adapter_failures),
            "gold_state_policy": (
                "compile CRUDSQL type=0 conds with equality operators into one SQLite "
                "INSERT over col_{index+1}; verify row count increments by one on a DB copy"
            ),
        },
        "overlap_audit": overlap,
        "sample_size_sensitivity": sensitivity,
        "decision": {
            "eligible_for_stage6b_registration_after_reviewer_acceptance": decision_status.startswith("PASS"),
            "recommended_registration_n": len(eligible_rows),
            "recommended_sampling_policy": "use_all_eligible_official_test_type0_examples_no_random_sampling",
            "claim_boundary": "external_generalization_to_public_Chinese_single_table_SQLite_insert_benchmark",
        },
    }

    write_json(out_dir / "CANDIDATE_SOURCE_REGISTRY.json", registry)
    write_json(artifacts / "crudsql_source_file_hashes.json", file_hashes)
    write_json(artifacts / "crudsql_eligibility_audit.json", audit)
    write_json(artifacts / "crudsql_overlap_audit.json", overlap)
    write_json(artifacts / "stage6_sample_size_sensitivity.json", sensitivity)
    write_text(artifacts / "crudsql_official_test_type0_ids.txt", "\n".join(official_ids) + "\n")
    preview_path = artifacts / "crudsql_type0_adapter_preview.jsonl"
    preview_rows = [
        {
            "stage6_candidate_sample_id": row["stage6_candidate_sample_id"],
            "table_id": row["table_id"],
            "question_sha256": row["question_sha256"],
            "insert_sql_template": row["insert_sql_template"],
            "columns": row["columns"],
            "adapter_status": row["adapter_status"],
        }
        for row in eligible_rows[:20]
    ]
    write_text(preview_path, "".join(canonical_json(row) + "\n" for row in preview_rows))
    write_json(out_dir / "STAGE6A_DECISION.json", audit["decision"] | {"status": decision_status})
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crudsql-root", required=True)
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "stage6_crudsql_eligibility_audit"),
    )
    args = parser.parse_args(argv)
    report = run_audit(Path(args.crudsql_root), Path(args.out_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if str(report["status"]).startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
