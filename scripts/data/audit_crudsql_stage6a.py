#!/usr/bin/env python3
"""CPU-only Stage 6A PATCH2 eligibility audit for CRUDSQL.

This script does not call a model and does not register a confirmation set. It
audits whether the public CRUDSQL official test split can be deterministically
adapted into a Stage 6B SQLite INSERT confirmation source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
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
EXPECTED_REFERENCE_SOURCES = [
    "stage4_fresh_300",
    "final_holdout_release_300",
    "archived_677_pool",
]
NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


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


def display_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sqlite_tables(con: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]


def table_info(con: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    return [
        {
            "cid": row[0],
            "name": row[1],
            "type": row[2],
            "notnull": row[3],
            "default": row[4],
            "pk": row[5],
        }
        for row in con.execute(f'PRAGMA table_info("{table_name}")')
    ]


def table_rows_with_rowid(con: sqlite3.Connection, table_name: str) -> list[list[Any]]:
    return [
        list(row)
        for row in con.execute(f'SELECT rowid, * FROM "{table_name}" ORDER BY rowid')
    ]


def table_fingerprint(con: sqlite3.Connection, table_name: str) -> dict[str, Any]:
    create_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()[0]
    schema_payload = {
        "table_name": table_name,
        "create_sql": create_sql,
        "table_info": table_info(con, table_name),
    }
    state_payload = {
        "table_name": table_name,
        "rows": table_rows_with_rowid(con, table_name),
    }
    schema_sha = sha256_text(canonical_json(schema_payload))
    state_sha = sha256_text(canonical_json(state_payload))
    combined_sha = sha256_text(canonical_json({"schema": schema_sha, "initial_state": state_sha}))
    return {
        "table_name": table_name,
        "schema_sha256": schema_sha,
        "initial_state_sha256": state_sha,
        "schema_plus_initial_state_sha256": combined_sha,
        "row_count": len(state_payload["rows"]),
        "column_count": len(schema_payload["table_info"]),
    }


def validate_sqlite_db(db_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = sqlite_tables(con)
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


def create_isolated_table_dbs(crudsql_root: Path, out_dir: Path) -> list[dict[str, Any]]:
    db_dir = out_dir / "isolated_table_dbs"
    if db_dir.exists():
        resolved = db_dir.resolve()
        if out_dir.resolve() not in resolved.parents:
            raise RuntimeError(f"Refusing to remove isolated DB directory outside out_dir: {resolved}")
        shutil.rmtree(db_dir)
    db_dir.mkdir(parents=True)

    source = sqlite3.connect(split_paths(crudsql_root, "test")["db"])
    manifest: list[dict[str, Any]] = []
    try:
        for table_name in sqlite_tables(source):
            table_id = table_name.removeprefix("Table_")
            target_rel = f"isolated_table_dbs/crudsql_db_{table_id}.sqlite"
            target_path = out_dir / target_rel
            target = sqlite3.connect(target_path)
            try:
                create_sql = source.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
                ).fetchone()[0]
                target.execute(create_sql)
                rows = source.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid').fetchall()
                column_count = len(table_info(source, table_name))
                placeholders = ", ".join("?" for _ in range(column_count))
                target.executemany(f'INSERT INTO "{table_name}" VALUES ({placeholders})', rows)
                target.commit()
                integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
                fingerprint = table_fingerprint(target, table_name)
            finally:
                target.close()
            manifest.append(
                {
                    "table_id": table_id,
                    "table_name": table_name,
                    "isolated_db_path": target_rel,
                    "isolated_db_sha256": sha256_file(target_path),
                    "integrity_check": integrity,
                    **fingerprint,
                }
            )
    finally:
        source.close()
    return sorted(manifest, key=lambda row: row["table_id"])


def compile_type0_insert(
    sample: dict[str, Any],
    table_by_id: dict[str, dict[str, Any]],
    official_split: str,
    official_split_index: int,
    type0_ordinal: int,
) -> dict[str, Any]:
    table_id = sample["table_id"]
    table = table_by_id[table_id]
    sql = sample["sql"]
    question_sha = sha256_text(sample["question"])
    upstream_locator = f"crudsql:{official_split}:{official_split_index:04d}:{table_id}:{question_sha[:16]}"
    columns: list[str] = []
    column_indexes: list[int] = []
    values: list[Any] = []
    errors: list[str] = []
    seen_columns: set[int] = set()
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
        if column_index in seen_columns:
            errors.append("duplicate_insert_column")
            continue
        seen_columns.add(column_index)
        column_indexes.append(column_index)
        columns.append(f"col_{column_index + 1}")
        values.append(value)
    if not columns:
        errors.append("no_insert_columns")
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    statement = f'INSERT INTO "Table_{table_id}" ({quoted_columns}) VALUES ({placeholders})'
    return {
        "stage6_sample_id": f"stage6_crudsql_{type0_ordinal:04d}",
        "upstream_sample_locator": upstream_locator,
        "official_split": official_split,
        "official_split_index": official_split_index,
        "type0_ordinal": type0_ordinal,
        "table_id": table_id,
        "question": sample["question"],
        "question_sha256": question_sha,
        "canonical_content_sha256": sha256_text(
            canonical_json(
                {
                    "official_split": official_split,
                    "official_split_index": official_split_index,
                    "table_id": table_id,
                    "question": sample["question"],
                    "sql": sample["sql"],
                }
            )
        ),
        "insert_sql_template": statement,
        "column_indexes": column_indexes,
        "columns": columns,
        "values": values,
        "compile_errors": errors,
    }


def sqlite_affinity_value(value: Any, declared_type: str) -> Any:
    if value is None:
        return None
    dtype = (declared_type or "").upper()
    if "INT" in dtype:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and NUMERIC_RE.match(value.strip()):
            number = float(value)
            if number.is_integer():
                return int(number)
        return value
    if any(token in dtype for token in ["REAL", "FLOA", "DOUB"]):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str) and NUMERIC_RE.match(value.strip()):
            return float(value)
        return value
    if any(token in dtype for token in ["CHAR", "CLOB", "TEXT"]):
        return str(value)
    return value


def expected_row_after_insert(
    info: list[dict[str, Any]],
    column_indexes: list[int],
    values: list[Any],
) -> list[Any]:
    expected = [None for _ in info]
    for column_index, value in zip(column_indexes, values):
        expected[column_index] = sqlite_affinity_value(value, str(info[column_index]["type"]))
    return expected


def execute_one_type0_adapter(
    isolated_db_path: Path,
    row: dict[str, Any],
) -> tuple[dict[str, Any], Counter[str]]:
    failures: Counter[str] = Counter()
    source = sqlite3.connect(isolated_db_path)
    con = sqlite3.connect(":memory:")
    try:
        source.backup(con)
    finally:
        source.close()

    table_name = f'Table_{row["table_id"]}'
    try:
        info = table_info(con, table_name)
        before = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        initial_fp = table_fingerprint(con, table_name)
        cursor = con.execute(row["insert_sql_template"], row["values"])
        inserted_rowid = cursor.lastrowid
        after = con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        inserted = list(
            con.execute(f'SELECT * FROM "{table_name}" WHERE rowid=?', (inserted_rowid,)).fetchone()
        )
        expected = expected_row_after_insert(info, row["column_indexes"], row["values"])
        post_fp = table_fingerprint(con, table_name)
        con.commit()
    except Exception as exc:  # pragma: no cover - exact sqlite message varies.
        failures["sqlite_execution_error"] += 1
        row["adapter_status"] = "FAIL"
        row["execution_error"] = str(exc)
        con.close()
        return row, failures
    finally:
        if con:
            con.close()

    if after != before + 1:
        failures["row_count_did_not_increment_by_one"] += 1
    if inserted != expected:
        failures["inserted_row_did_not_match_expected_affinity_values"] += 1
    if any(value is not None for idx, value in enumerate(inserted) if idx not in row["column_indexes"]):
        failures["unspecified_column_not_null"] += 1

    row["pre_insert_row_count"] = before
    row["post_insert_row_count"] = after
    row["inserted_rowid"] = inserted_rowid
    row["expected_inserted_row"] = expected
    row["actual_inserted_row"] = inserted
    row["initial_state_sha256"] = initial_fp["initial_state_sha256"]
    row["post_state_sha256"] = post_fp["initial_state_sha256"]
    row["schema_sha256"] = initial_fp["schema_sha256"]
    row["adapter_status"] = "FAIL" if failures else "PASS"
    return row, failures


def execute_type0_adapters(
    split: str,
    sql_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    isolated_db_manifest: list[dict[str, Any]],
    out_dir: Path,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    table_by_id = {row["id"]: row for row in table_rows}
    isolated_by_table = {row["table_id"]: out_dir / row["isolated_db_path"] for row in isolated_db_manifest}
    results: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    type0_ordinal = 0
    for official_index, sample in enumerate(sql_rows):
        if sample.get("sql", {}).get("type") != 0:
            continue
        row = compile_type0_insert(sample, table_by_id, split, official_index, type0_ordinal)
        type0_ordinal += 1
        if row["compile_errors"]:
            for error in row["compile_errors"]:
                failures[error] += 1
            row["adapter_status"] = "FAIL"
            results.append(row)
            continue
        isolated_db = isolated_by_table.get(row["table_id"])
        if not isolated_db or not isolated_db.is_file():
            failures["missing_isolated_table_db"] += 1
            row["adapter_status"] = "FAIL"
            results.append(row)
            continue
        row, row_failures = execute_one_type0_adapter(isolated_db, row)
        failures.update(row_failures)
        results.append(row)
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


def new_reference_sets() -> dict[str, set[str]]:
    return {
        "sample_ids": set(),
        "source_groups": set(),
        "database_ids": set(),
        "input_text_sha256": set(),
        "canonical_content_sha256": set(),
        "database_profile_fingerprints": set(),
    }


def add_reference_row_digests(reference: dict[str, set[str]], row: dict[str, Any]) -> None:
    sample_id = str(row.get("id") or row.get("sample_id") or row.get("instance_id") or "")
    question = str(
        row.get("question")
        or row.get("input_text")
        or row.get("input")
        or row.get("instruction")
        or ""
    )
    source_group = str(row.get("source_group") or row.get("source") or sample_id)
    database_id = str(row.get("db_id") or row.get("database_id") or row.get("database") or "")
    if sample_id:
        reference["sample_ids"].add(sample_id)
    if source_group:
        reference["source_groups"].add(source_group)
    if database_id:
        reference["database_ids"].add(database_id)
    if question:
        reference["input_text_sha256"].add(sha256_text(question))
    reference["canonical_content_sha256"].add(sha256_text(canonical_json(row)))


def registry_self_hash(registry: dict[str, Any]) -> str:
    return sha256_text(
        canonical_json(
            {
                key: value
                for key, value in registry.items()
                if key != "registry_sha256_excluding_self"
            }
        )
    )


def build_seen_reference_registry(project_root: Path, archived_677_dataset: Path) -> dict[str, Any]:
    forbidden = new_reference_sets()
    sources: list[dict[str, Any]] = []
    source_digest_counts: dict[str, dict[str, int]] = {}

    stage4_manifest_path = project_root / "stage4_fresh_7b_protocol" / "data" / "fresh_dataset_manifest.json"
    if not stage4_manifest_path.is_file():
        raise FileNotFoundError(stage4_manifest_path)
    manifest = read_json(stage4_manifest_path)
    forbidden["sample_ids"].update(str(item) for item in manifest.get("selected_sample_ids") or [])
    forbidden["database_ids"].update(str(item) for item in (manifest.get("database_counts") or {}).keys())
    source_digest_counts["stage4_fresh_300"] = {
        "sample_ids": len(manifest.get("selected_sample_ids") or []),
        "input_text_sha256": 0,
        "canonical_content_sha256": 0,
        "database_profile_fingerprints": 0,
    }
    sources.append(
        {
            "name": "stage4_fresh_300",
            "path": display_path(stage4_manifest_path, project_root),
            "sample_count": manifest.get("sample_count"),
            "source_artifact_sha256": sha256_file(stage4_manifest_path),
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
    if not final_dataset or not final_manifest:
        raise FileNotFoundError(final_zip)
    rows = final_dataset if isinstance(final_dataset, list) else final_dataset.get("samples", [])
    before_final = {key: len(value) for key, value in forbidden.items()}
    for row in rows:
        add_reference_row_digests(forbidden, row)
    forbidden["database_ids"].update(str(item) for item in final_manifest.get("database_ids") or [])
    with zipfile.ZipFile(final_zip) as archive:
        for name in archive.namelist():
            if name.endswith(".sqlite") or "/profiles/" in name:
                forbidden["database_profile_fingerprints"].add(sha256_bytes(archive.read(name)))
    source_digest_counts["final_holdout_release_300"] = {
        key: len(forbidden[key]) - before_final[key] for key in before_final
    }
    sources.append(
        {
            "name": "final_holdout_release_300",
            "path": display_path(final_zip, project_root),
            "sample_count": len(rows),
            "source_artifact_sha256": sha256_file(final_zip),
        }
    )

    if not archived_677_dataset.is_file():
        raise FileNotFoundError(archived_677_dataset)
    archived_rows = read_json(archived_677_dataset)
    if not isinstance(archived_rows, list):
        archived_rows = archived_rows.get("samples", [])
    before_archived = {key: len(value) for key, value in forbidden.items()}
    for row in archived_rows:
        add_reference_row_digests(forbidden, row)
    source_digest_counts["archived_677_pool"] = {
        key: len(forbidden[key]) - before_archived[key] for key in before_archived
    }
    sources.append(
        {
            "name": "archived_677_pool",
            "path": archived_677_dataset.as_posix(),
            "sample_count": len(archived_rows),
            "source_artifact_sha256": sha256_file(archived_677_dataset),
        }
    )

    registry = {
        "stage": "Stage6A_PATCH2_SEEN_REFERENCE_REGISTRY",
        "status": "PASS_REFERENCE_REGISTRY_FROZEN",
        "expected_source_names": EXPECTED_REFERENCE_SOURCES,
        "loaded_source_names": [source["name"] for source in sources],
        "expected_forbidden_reference_set_count": len(EXPECTED_REFERENCE_SOURCES),
        "loaded_forbidden_reference_set_count": len(sources),
        "sources": sources,
        "forbidden_sets": {key: sorted(value) for key, value in forbidden.items()},
        "digest_counts": {key: len(value) for key, value in forbidden.items()},
        "source_digest_counts": source_digest_counts,
        "database_fingerprint_coverage": {
            "database_identity_overlap_checked_for_sources": EXPECTED_REFERENCE_SOURCES,
            "database_byte_or_profile_fingerprint_checked_for_sources": [
                "final_holdout_release_300"
            ],
            "database_byte_or_profile_fingerprint_unavailable_for_sources": [
                "stage4_fresh_300",
                "archived_677_pool",
            ],
        },
    }
    registry["registry_sha256_excluding_self"] = registry_self_hash(registry)
    return registry


def load_seen_reference_registry(registry_path: Path) -> tuple[dict[str, Any], list[str]]:
    if not registry_path.is_file():
        return {
            "stage": "Stage6A_PATCH2_SEEN_REFERENCE_REGISTRY",
            "status": "FAIL_REFERENCE_REGISTRY_MISSING",
            "expected_source_names": EXPECTED_REFERENCE_SOURCES,
            "loaded_source_names": [],
            "expected_forbidden_reference_set_count": len(EXPECTED_REFERENCE_SOURCES),
            "loaded_forbidden_reference_set_count": 0,
            "sources": [],
            "forbidden_sets": {key: [] for key in new_reference_sets()},
            "digest_counts": {key: 0 for key in new_reference_sets()},
            "source_digest_counts": {},
        }, ["reference_registry_missing"]
    registry = read_json(registry_path)
    violations: list[str] = []
    expected = set(registry.get("expected_source_names") or [])
    loaded = set(registry.get("loaded_source_names") or [])
    missing = sorted(set(EXPECTED_REFERENCE_SOURCES) - loaded)
    if expected != set(EXPECTED_REFERENCE_SOURCES):
        violations.append("reference_registry_expected_sources_changed")
    if missing:
        violations.append("reference_registry_missing_expected_sources:" + ",".join(missing))
    if registry.get("loaded_forbidden_reference_set_count") != len(EXPECTED_REFERENCE_SOURCES):
        violations.append("reference_registry_loaded_count_mismatch")
    if registry.get("status") != "PASS_REFERENCE_REGISTRY_FROZEN":
        violations.append("reference_registry_status_not_pass")
    declared_hash = registry.get("registry_sha256_excluding_self")
    recomputed_hash = registry_self_hash(registry)
    if declared_hash != recomputed_hash:
        violations.append("reference_registry_self_hash_mismatch")
    digest_counts = registry.get("digest_counts") or {}
    if int(digest_counts.get("input_text_sha256") or 0) <= 0:
        violations.append("reference_registry_input_text_sha256_empty")
    source_digest_counts = registry.get("source_digest_counts") or {}
    for source_name in ["final_holdout_release_300", "archived_677_pool"]:
        source_counts = source_digest_counts.get(source_name) or {}
        if int(source_counts.get("input_text_sha256") or 0) <= 0:
            violations.append(f"reference_registry_input_text_sha256_empty_for:{source_name}")
    return registry, violations


def overlap_audit(
    eligible_rows: list[dict[str, Any]],
    table_manifest: list[dict[str, Any]],
    registry: dict[str, Any],
    registry_violations: list[str],
) -> dict[str, Any]:
    forbidden = registry.get("forbidden_sets") or {}
    reference_sets = {key: set(forbidden.get(key) or []) for key in new_reference_sets()}
    sample_ids = {row["stage6_sample_id"] for row in eligible_rows}
    upstream_locators = {row["upstream_sample_locator"] for row in eligible_rows}
    source_groups = {f'crudsql_table:{row["table_id"]}' for row in eligible_rows}
    database_ids = {f'crudsql_table:{row["table_id"]}' for row in eligible_rows}
    input_hashes = {row["question_sha256"] for row in eligible_rows}
    content_hashes = {row["canonical_content_sha256"] for row in eligible_rows}
    db_fingerprints = {
        value
        for table in table_manifest
        for value in [
            table["schema_sha256"],
            table["initial_state_sha256"],
            table["schema_plus_initial_state_sha256"],
            table["isolated_db_sha256"],
        ]
    }
    counts = {
        "sample_id_overlap_count": len(sample_ids & reference_sets["sample_ids"]),
        "upstream_locator_overlap_count": len(upstream_locators & reference_sets["sample_ids"]),
        "source_group_overlap_count": len(source_groups & reference_sets["source_groups"]),
        "database_id_namespace_overlap_count": len(database_ids & reference_sets["database_ids"]),
        "database_fingerprint_overlap_count": len(
            db_fingerprints & reference_sets["database_profile_fingerprints"]
        ),
        "input_text_hash_overlap_count": len(input_hashes & reference_sets["input_text_sha256"]),
        "canonical_content_hash_overlap_count": len(
            content_hashes & reference_sets["canonical_content_sha256"]
        ),
    }
    overlap_violations = [
        name for name, value in counts.items() if value != 0 and name != "database_id_namespace_overlap_count"
    ]
    status = "PASS" if not registry_violations and not overlap_violations else "FAIL"
    return {
        "status": status,
        "reference_registry_status": registry.get("status"),
        "reference_registry_violations": registry_violations,
        "expected_forbidden_reference_set_count": len(EXPECTED_REFERENCE_SOURCES),
        "loaded_forbidden_reference_set_count": registry.get("loaded_forbidden_reference_set_count", 0),
        "reference_sources": registry.get("sources") or [],
        "reference_digest_counts": registry.get("digest_counts") or {},
        "reference_source_digest_counts": registry.get("source_digest_counts") or {},
        "database_fingerprint_coverage": registry.get("database_fingerprint_coverage") or {},
        "crudsql_sample_count": len(eligible_rows),
        "crudsql_table_count": len(table_manifest),
        **counts,
        "disclosure": (
            "Stage6A PATCH2 compares stable sample locators, text/content hashes, "
            "source groups, and database identities across all forbidden prior sources. "
            "Database byte/profile fingerprint comparison is limited to prior sources "
            "with packaged DB/profile assets, as recorded in database_fingerprint_coverage. "
            "The final decision ignores no missing expected forbidden source."
        ),
    }


def mcnemar_two_sided_p_no_regressions(favorable: int) -> float:
    if favorable <= 0:
        return 1.0
    return min(1.0, 2.0 ** (1 - favorable))


def mcnemar_threshold_sensitivity(candidate_n: int) -> dict[str, Any]:
    rows = []
    for n in sorted({300, 400, 500, candidate_n}):
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
        "analysis_type": "mcnemar_threshold_sensitivity_not_power_analysis",
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


def remove_stale_renamed_artifacts(artifacts: Path) -> None:
    stale = artifacts / "stage6_sample_size_sensitivity.json"
    if stale.exists():
        stale.unlink()


def run_audit(
    crudsql_root: Path,
    out_dir: Path,
    project_root: Path = PROJECT_ROOT,
    *,
    reference_registry_path: Path | None = None,
    rebuild_reference_registry: bool = False,
    archived_677_dataset: Path | None = None,
) -> dict[str, Any]:
    crudsql_root = crudsql_root.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = out_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    remove_stale_renamed_artifacts(artifacts)

    registry_path = reference_registry_path or artifacts / "stage6_seen_reference_registry.json"
    if rebuild_reference_registry:
        if archived_677_dataset is None:
            raise ValueError("--archived-677-dataset is required with --rebuild-reference-registry")
        registry = build_seen_reference_registry(project_root, archived_677_dataset)
        write_json(registry_path, registry)

    commit = git_output(crudsql_root, "rev-parse", "HEAD")
    status = git_output(crudsql_root, "status", "--porcelain")
    file_hashes = source_file_hashes(crudsql_root)

    split_counts: dict[str, Any] = {}
    sqlite_checks: dict[str, Any] = {}
    for split in ["train", "dev", "test"]:
        sql_rows, _ = load_split(crudsql_root, split)
        counts = Counter(row["sql"]["type"] for row in sql_rows)
        split_counts[split] = {
            "total": len(sql_rows),
            "by_type": {str(key): value for key, value in sorted(counts.items())},
            "by_type_label": {TYPE_LABELS[key]: value for key, value in sorted(counts.items())},
        }
        sqlite_checks[split] = validate_sqlite_db(split_paths(crudsql_root, split)["db"])
        sqlite_checks[split]["path"] = f"data/{split}/{split}.db"

    isolated_manifest = create_isolated_table_dbs(crudsql_root, out_dir)
    test_sql, test_tables = load_split(crudsql_root, "test")
    adapter_rows, adapter_failures = execute_type0_adapters(
        "test",
        test_sql,
        test_tables,
        isolated_manifest,
        out_dir,
    )
    eligible_rows = [row for row in adapter_rows if row["adapter_status"] == "PASS"]

    registry, registry_violations = load_seen_reference_registry(registry_path)
    overlap = overlap_audit(eligible_rows, isolated_manifest, registry, registry_violations)
    sensitivity = mcnemar_threshold_sensitivity(len(eligible_rows))

    exact_fresh_execution_pass = all(
        row.get("pre_insert_row_count") == row.get("post_insert_row_count", 0) - 1
        for row in eligible_rows
    )
    exact_inserted_row_pass = all(
        row.get("expected_inserted_row") == row.get("actual_inserted_row") for row in eligible_rows
    )
    isolated_db_pass = (
        len(isolated_manifest) == 125
        and all(row["integrity_check"] == "ok" for row in isolated_manifest)
        and len({row["isolated_db_sha256"] for row in isolated_manifest}) == len(isolated_manifest)
    )

    decision_status = "PASS_ELIGIBLE_FOR_STAGE6B_REGISTRATION" if (
        commit == EXPECTED_CRUDSQL_COMMIT
        and status == ""
        and split_counts["test"]["by_type"].get("0") == 500
        and len(eligible_rows) >= 300
        and not adapter_failures
        and overlap["status"] == "PASS"
        and exact_fresh_execution_pass
        and exact_inserted_row_pass
        and isolated_db_pass
        and all(row["integrity_check"] == "ok" for row in sqlite_checks.values())
    ) else "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"

    registry_summary = {
        "path": display_path(registry_path, out_dir),
        "sha256": sha256_file(registry_path) if registry_path.is_file() else None,
        "status": registry.get("status"),
        "violations": registry_violations,
    }
    source_registry = {
        "stage": "Stage6A_CRUDSQL_ELIGIBILITY_AUDIT_PATCH2",
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
        "seen_reference_registry": registry_summary,
    }
    audit = {
        "stage": "Stage6A_CRUDSQL_ELIGIBILITY_AUDIT_PATCH2",
        "status": decision_status,
        "model_called": False,
        "gpu_called": False,
        "registration_status": "not_registered_in_stage6a",
        "split_counts": split_counts,
        "sqlite_checks": sqlite_checks,
        "isolated_table_db_audit": {
            "status": "PASS" if isolated_db_pass else "FAIL",
            "table_count": len(isolated_manifest),
            "fresh_single_table_db_per_official_table": True,
            "manifest_path": "artifacts/isolated_table_db_manifest.json",
            "db_dir": "isolated_table_dbs",
        },
        "type0_adapter": {
            "official_test_type0_count": len(adapter_rows),
            "adapter_pass_count": len(eligible_rows),
            "adapter_fail_count": len(adapter_rows) - len(eligible_rows),
            "failure_counts": dict(adapter_failures),
            "fresh_db_per_sample": True,
            "exact_inserted_row_validation": exact_inserted_row_pass,
            "gold_state_policy": (
                "for each official test type=0 sample, start from the isolated fresh "
                "single-table SQLite DB, execute the parameterized CRUDSQL conds-derived "
                "INSERT, verify the exact inserted row including SQLite affinity and NULL "
                "unspecified columns, and hash the post-state"
            ),
        },
        "overlap_audit": overlap,
        "mcnemar_threshold_sensitivity": sensitivity,
        "decision": {
            "eligible_for_stage6b_registration_after_reviewer_acceptance": decision_status.startswith("PASS"),
            "recommended_registration_n": len(eligible_rows),
            "recommended_sampling_policy": "use_all_eligible_official_test_type0_examples_no_random_sampling",
            "claim_boundary": "external_generalization_to_public_Chinese_single_table_SQLite_insert_benchmark",
        },
    }

    write_json(out_dir / "CANDIDATE_SOURCE_REGISTRY.json", source_registry)
    write_json(artifacts / "crudsql_source_file_hashes.json", file_hashes)
    write_json(artifacts / "crudsql_eligibility_audit.json", audit)
    write_json(artifacts / "crudsql_overlap_audit.json", overlap)
    write_json(artifacts / "mcnemar_threshold_sensitivity.json", sensitivity)
    write_json(artifacts / "isolated_table_db_manifest.json", isolated_manifest)
    write_text(
        artifacts / "crudsql_official_test_type0_ids.tsv",
        "stage6_sample_id\tupstream_sample_locator\tofficial_split_index\ttable_id\tquestion_sha256\n"
        + "\n".join(
            "\t".join(
                [
                    row["stage6_sample_id"],
                    row["upstream_sample_locator"],
                    str(row["official_split_index"]),
                    row["table_id"],
                    row["question_sha256"],
                ]
            )
            for row in eligible_rows
        )
        + "\n",
    )
    write_text(
        artifacts / "crudsql_stage6_candidate_samples.jsonl",
        "".join(
            canonical_json(
                {
                    "stage6_sample_id": row["stage6_sample_id"],
                    "upstream_sample_locator": row["upstream_sample_locator"],
                    "official_split_index": row["official_split_index"],
                    "table_id": row["table_id"],
                    "question_sha256": row["question_sha256"],
                    "canonical_content_sha256": row["canonical_content_sha256"],
                    "schema_sha256": row["schema_sha256"],
                    "initial_state_sha256": row["initial_state_sha256"],
                    "post_state_sha256": row["post_state_sha256"],
                }
            )
            + "\n"
            for row in eligible_rows
        ),
    )
    write_text(
        artifacts / "crudsql_gold_adapter_audit.jsonl",
        "".join(canonical_json(row) + "\n" for row in eligible_rows),
    )
    write_text(
        artifacts / "gold_post_state_hashes.jsonl",
        "".join(
            canonical_json(
                {
                    "stage6_sample_id": row["stage6_sample_id"],
                    "upstream_sample_locator": row["upstream_sample_locator"],
                    "isolated_db": f"isolated_table_dbs/crudsql_db_{row['table_id']}.sqlite",
                    "schema_sha256": row["schema_sha256"],
                    "initial_state_sha256": row["initial_state_sha256"],
                    "post_state_sha256": row["post_state_sha256"],
                }
            )
            + "\n"
            for row in eligible_rows
        ),
    )
    preview_rows = [
        {
            "stage6_sample_id": row["stage6_sample_id"],
            "upstream_sample_locator": row["upstream_sample_locator"],
            "official_split_index": row["official_split_index"],
            "table_id": row["table_id"],
            "question_sha256": row["question_sha256"],
            "insert_sql_template": row["insert_sql_template"],
            "columns": row["columns"],
            "expected_inserted_row": row["expected_inserted_row"],
            "adapter_status": row["adapter_status"],
        }
        for row in eligible_rows[:20]
    ]
    write_text(
        artifacts / "crudsql_type0_adapter_preview.jsonl",
        "".join(canonical_json(row) + "\n" for row in preview_rows),
    )
    write_json(out_dir / "STAGE6A_DECISION.json", audit["decision"] | {"status": decision_status})
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crudsql-root", required=True)
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "stage6_crudsql_eligibility_audit"),
    )
    parser.add_argument("--reference-registry")
    parser.add_argument("--rebuild-reference-registry", action="store_true")
    parser.add_argument("--archived-677-dataset")
    args = parser.parse_args(argv)
    report = run_audit(
        Path(args.crudsql_root),
        Path(args.out_dir),
        reference_registry_path=Path(args.reference_registry) if args.reference_registry else None,
        rebuild_reference_registry=args.rebuild_reference_registry,
        archived_677_dataset=Path(args.archived_677_dataset) if args.archived_677_dataset else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if str(report["status"]).startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
