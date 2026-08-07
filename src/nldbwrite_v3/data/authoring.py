from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from nldbwrite_v3.schema import build_profile

from .calibration import CONSUMED_DATABASES, audit_calibration_metadata


OPERATIONS = ("plain_insert", "insert_ignore", "upsert_update")
COMPLEXITIES = ("single_row", "small_batch", "large_or_relational")
SEMI_STRUCTURED_FORMATS = ("json", "key_value", "markdown", "csv_or_mixed")
SOURCE_METADATA_SUFFIXES = (
    "_schema.txt",
    "_column_meaning_base.json",
    "_kb.jsonl",
)
FROZEN_FIELDS = (
    "id",
    "sample_id",
    "db_id",
    "source_group",
    "operation_semantics",
    "input_mode",
    "input_format",
    "complexity",
    "multi_table",
    "workload_shape",
    "semantics_explicit_in_request",
    "semantics_source",
    "state_changing",
    "conflict_sensitive",
    "is_augmented",
)
AUTHORED_CONTENT_FIELDS = (
    "id",
    "sample_id",
    "db_id",
    "input_text",
    "input_mode",
    "input_format",
    "complexity",
    "operation_semantics",
    "semantics_explicit_in_request",
    "semantics_source",
    "state_changing",
    "conflict_sensitive",
    "multi_table",
    "workload_shape",
    "conflict_target",
    "update_columns",
    "gold_sql",
    "gold_plan",
    "gold_records",
    "gold_tables",
    "source_group",
    "author_id",
    "independently_authored",
    "is_augmented",
    "revision",
    "provenance",
)
REVIEW_LEDGER_FIELDS = (
    "sample_id",
    "revision",
    "authored_content_sha256",
    "reviewer_id",
    "decision",
    "issue_codes",
    "reviewed_at_utc",
)
PLACEHOLDER_VALUES = {
    "TODO",
    "TBD",
    "REPLACE",
    "REPLACE_WITH_AUTHOR_ID",
    "REPLACE_WITH_REVIEWER_1",
    "REPLACE_WITH_REVIEWER_2",
}


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def frozen_sample_projection(sample: dict[str, Any]) -> dict[str, Any]:
    return {field: sample.get(field) for field in FROZEN_FIELDS}


def authored_content_sha256(sample: dict[str, Any]) -> str:
    return _sha256_json(
        {field: sample.get(field) for field in AUTHORED_CONTENT_FIELDS}
    )


def _write_json(value: Any, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_noncomment_ids(path: str | Path) -> list[str]:
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _validate_database_allocation(
    calibration_database_ids: Iterable[str],
    reserved_final_database_ids: Iterable[str],
) -> tuple[list[str], list[str]]:
    calibration = [str(value).strip() for value in calibration_database_ids]
    reserved_final = [
        str(value).strip() for value in reserved_final_database_ids
    ]
    if len(calibration) != 2 or len(set(calibration)) != 2:
        raise ValueError("Exactly two distinct calibration databases are required.")
    if not 3 <= len(reserved_final) <= 5:
        raise ValueError("Reserve 3-5 final-holdout databases.")
    if len(set(reserved_final)) != len(reserved_final):
        raise ValueError("Reserved final database IDs must be unique.")
    overlap = sorted(set(calibration) & set(reserved_final))
    if overlap:
        raise ValueError(
            f"Calibration and final database allocations overlap: {overlap}"
        )
    consumed = sorted(
        (set(calibration) | set(reserved_final)) & CONSUMED_DATABASES
    )
    if consumed:
        raise ValueError(f"Allocation includes consumed databases: {consumed}")
    return calibration, reserved_final


def _find_source_database(source_root: Path, db_id: str) -> Path:
    directory = source_root / db_id
    candidates = [
        directory / f"{db_id}_template.sqlite",
        directory / f"{db_id}.sqlite",
        directory / f"{db_id}.db",
    ]
    candidates.extend(sorted(directory.glob("*.sqlite")))
    candidates.extend(sorted(directory.glob("*.db")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"No SQLite database found for {db_id!r} under {directory}"
    )


def _quick_check(path: Path) -> str:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _database_inventory_row(path: Path, db_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        objects = connection.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        tables = [name for name, kind in objects if kind == "table"]
        views = sum(kind == "view" for _, kind in objects)
        triggers = sum(kind == "trigger" for _, kind in objects)
        column_count = 0
        row_count = 0
        foreign_key_count = 0
        pk_table_count = 0
        unique_index_count = 0
        for table in tables:
            quoted = _quote_identifier(table)
            table_info = connection.execute(
                f"PRAGMA table_info({quoted})"
            ).fetchall()
            column_count += len(table_info)
            pk_table_count += int(any(row[5] for row in table_info))
            foreign_key_count += len(
                connection.execute(
                    f"PRAGMA foreign_key_list({quoted})"
                ).fetchall()
            )
            unique_index_count += sum(
                int(row[2])
                for row in connection.execute(
                    f"PRAGMA index_list({quoted})"
                ).fetchall()
            )
            row_count += int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {quoted}"
                ).fetchone()[0]
            )
        return {
            "db_id": db_id,
            "integrity": integrity,
            "tables": len(tables),
            "columns": column_count,
            "rows": row_count,
            "foreign_keys": foreign_key_count,
            "pk_tables": pk_table_count,
            "unique_indexes": unique_index_count,
            "views": views,
            "triggers": triggers,
            "database_sha256": _sha256_file(path),
        }
    finally:
        connection.close()


def _source_files(source_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(source_root).parts
        ),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )


def _write_candidate_pool_evidence(
    *,
    source_root: Path,
    output_dir: Path,
    calibration_database_ids: list[str],
    reserved_final_database_ids: list[str],
    expected_candidate_count: int,
    source_revision: str | None,
) -> dict[str, Any]:
    database_paths = {
        path.parent.name: path
        for path in source_root.glob("*/*_template.sqlite")
        if path.is_file()
    }
    if len(database_paths) != expected_candidate_count:
        raise ValueError(
            "Candidate pool database count mismatch: "
            f"expected {expected_candidate_count}, found {len(database_paths)}"
        )
    allocated = set(calibration_database_ids) | set(reserved_final_database_ids)
    missing_allocated = sorted(allocated - set(database_paths))
    if missing_allocated:
        raise ValueError(
            f"Allocated databases are absent from candidate pool: {missing_allocated}"
        )
    inventory = [
        _database_inventory_row(database_paths[db_id], db_id)
        for db_id in sorted(database_paths)
    ]
    failed = [
        row["db_id"] for row in inventory if row["integrity"] != "ok"
    ]
    if failed:
        raise ValueError(f"Candidate database quick_check failed: {failed}")
    provenance = output_dir / "provenance"
    provenance.mkdir(parents=True, exist_ok=True)
    inventory_path = provenance / "candidate_database_inventory.tsv"
    with inventory_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(inventory[0]),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(inventory)
    source_manifest_path = provenance / "candidate_source_files.sha256"
    source_manifest_lines = [
        f"{_sha256_file(path)}  {path.relative_to(source_root).as_posix()}"
        for path in _source_files(source_root)
    ]
    source_manifest_path.write_text(
        "\n".join(source_manifest_lines) + "\n",
        encoding="utf-8",
    )
    evidence = {
        "version": "2.0",
        "source_revision": source_revision,
        "candidate_database_count": len(inventory),
        "candidate_database_ids": [row["db_id"] for row in inventory],
        "all_database_quick_checks_ok": True,
        "calibration_database_ids": calibration_database_ids,
        "reserved_final_database_ids": reserved_final_database_ids,
        "reserved_final_database_files_included": False,
        "candidate_inventory_sha256": _sha256_file(inventory_path),
        "candidate_source_manifest_sha256": _sha256_file(source_manifest_path),
        "source_file_count": len(source_manifest_lines),
        "database_sha256": {
            row["db_id"]: row["database_sha256"] for row in inventory
        },
    }
    _write_json(evidence, provenance / "candidate_pool_audit.json")
    return evidence


def _balanced_design(db_id: str, db_index: int) -> list[dict[str, Any]]:
    # A state-changing INSERT ... DO NOTHING sample needs at least one
    # conflicting row and at least one non-conflicting row. It therefore
    # cannot be a single-row workload. Keep the marginal 10/10/10 operation
    # and complexity balances per database while excluding that impossible
    # cross-product.
    operation_sequences = {
        "single_row": (
            "plain_insert",
            "upsert_update",
            "plain_insert",
            "upsert_update",
            "plain_insert",
            "upsert_update",
            "plain_insert",
            "upsert_update",
            "plain_insert",
            "upsert_update",
        ),
        "small_batch": (
            "plain_insert",
            "insert_ignore",
            "upsert_update",
            "insert_ignore",
            "insert_ignore",
            "plain_insert",
            "upsert_update",
            "insert_ignore",
            "upsert_update",
            "insert_ignore",
        ),
        "large_or_relational": (
            "plain_insert",
            "insert_ignore",
            "upsert_update",
            "insert_ignore",
            "plain_insert",
            "insert_ignore",
            "upsert_update",
            "insert_ignore",
            "plain_insert",
            "insert_ignore",
        ),
    }
    combinations: list[tuple[str, str]] = []
    for complexity_rank in range(10):
        for complexity in COMPLEXITIES:
            operations = operation_sequences[complexity]
            operation = operations[complexity_rank]
            combinations.append((operation, complexity))
    free_text_positions = {*range(9), 27 + db_index}
    semi_index = 0
    complexity_ranks = Counter()
    rows: list[dict[str, Any]] = []
    for index, (operation, complexity) in enumerate(combinations):
        input_mode = (
            "free_text" if index in free_text_positions else "semi_structured"
        )
        if input_mode == "free_text":
            input_format = "free_text"
        else:
            input_format = SEMI_STRUCTURED_FORMATS[
                semi_index % len(SEMI_STRUCTURED_FORMATS)
            ]
            semi_index += 1
        complexity_rank = complexity_ranks[complexity]
        complexity_ranks[complexity] += 1
        multi_table = (
            complexity != "single_row"
            and (complexity_rank + db_index) % 2 == 0
        )
        workload_shape = (
            f"{complexity}__"
            f"{'multi_table' if multi_table else 'single_table'}"
        )
        sample_id = f"cal_{db_id}_{index + 1:03d}"
        rows.append(
            {
                "id": sample_id,
                "sample_id": sample_id,
                "db_id": db_id,
                "input_text": "",
                "input_mode": input_mode,
                "input_format": input_format,
                "complexity": complexity,
                "operation_semantics": operation,
                "semantics_explicit_in_request": True,
                "semantics_source": "request",
                "state_changing": True,
                "conflict_sensitive": operation != "plain_insert",
                "multi_table": multi_table,
                "workload_shape": workload_shape,
                "conflict_target": [],
                "update_columns": [],
                "gold_sql": [],
                "gold_plan": {},
                "gold_records": [],
                "gold_tables": [],
                "source_group": sample_id,
                "author_id": "",
                "independently_authored": False,
                "is_augmented": False,
                "qa_reviews": [
                    {
                        "reviewer_id": "",
                        "decision": "pending",
                        "semantics_correct": None,
                        "gold_target_correct": None,
                        "conflict_target_correct": None,
                        "update_columns_correct": None,
                        "hidden_policy": None,
                        "reviewed_revision": None,
                        "reviewed_content_sha256": None,
                        "reviewed_at_utc": None,
                    },
                    {
                        "reviewer_id": "",
                        "decision": "pending",
                        "semantics_correct": None,
                        "gold_target_correct": None,
                        "conflict_target_correct": None,
                        "update_columns_correct": None,
                        "hidden_policy": None,
                        "reviewed_revision": None,
                        "reviewed_content_sha256": None,
                        "reviewed_at_utc": None,
                    },
                ],
                "revision": 1,
                "authoring_status": "draft",
                "provenance": {
                    "source_database_id": db_id,
                    "public_task_used": False,
                    "request_origin": "independent_human_authoring",
                },
            }
        )
    return rows


def _matrix_row(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": sample["id"],
        "db_id": sample["db_id"],
        "operation_semantics": sample["operation_semantics"],
        "input_mode": sample["input_mode"],
        "input_format": sample["input_format"],
        "complexity": sample["complexity"],
        "multi_table": sample["multi_table"],
        "workload_shape": sample["workload_shape"],
        "frozen_fields_sha256": _sha256_json(
            frozen_sample_projection(sample)
        ),
        "author_id": "",
        "reviewer_1_id": "",
        "reviewer_2_id": "",
        "authoring_status": "draft",
    }


def create_calibration_authoring_kit(
    *,
    source_root: str | Path,
    output_dir: str | Path,
    calibration_database_ids: Iterable[str],
    reserved_final_database_ids: Iterable[str],
    source_url: str,
    source_license: str,
    source_archive_sha256: str | None = None,
    source_revision: str | None = None,
    expected_candidate_count: int = 18,
) -> dict[str, Any]:
    calibration, reserved_final = _validate_database_allocation(
        calibration_database_ids,
        reserved_final_database_ids,
    )
    source = Path(source_root).resolve()
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"Authoring output must be absent or empty: {output.resolve()}"
        )
    output.mkdir(parents=True, exist_ok=True)
    candidate_evidence = _write_candidate_pool_evidence(
        source_root=source,
        output_dir=output,
        calibration_database_ids=calibration,
        reserved_final_database_ids=reserved_final,
        expected_candidate_count=expected_candidate_count,
        source_revision=source_revision,
    )
    database_hashes: dict[str, str] = {}
    profile_hashes: dict[str, str] = {}
    metadata_hashes: dict[str, str] = {}
    quick_checks: dict[str, str] = {}

    for db_id in calibration:
        source_database = _find_source_database(source, db_id)
        quick_check = _quick_check(source_database)
        if quick_check != "ok":
            raise ValueError(
                f"SQLite integrity check failed for {db_id}: {quick_check}"
            )
        quick_checks[db_id] = quick_check
        database_target = output / "databases" / db_id / f"{db_id}.sqlite"
        database_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_database, database_target)
        database_hashes[db_id] = _sha256_file(database_target)

        metadata_target = output / "source_metadata" / db_id
        metadata_target.mkdir(parents=True, exist_ok=True)
        for suffix in SOURCE_METADATA_SUFFIXES:
            metadata_source = source / db_id / f"{db_id}{suffix}"
            if not metadata_source.is_file():
                raise FileNotFoundError(
                    f"Required source metadata is missing: {metadata_source}"
                )
            copied = metadata_target / metadata_source.name
            shutil.copy2(metadata_source, copied)
            relative = copied.relative_to(output).as_posix()
            metadata_hashes[relative] = _sha256_file(copied)

        profile = build_profile(database_target, db_id=db_id)
        profile["db_path"] = f"databases/{db_id}/{db_id}.sqlite"
        profile_target = output / "profiles" / f"{db_id}.json"
        _write_json(profile, profile_target)
        profile_hashes[db_id] = _sha256_file(profile_target)

    samples = [
        sample
        for db_index, db_id in enumerate(calibration)
        for sample in _balanced_design(db_id, db_index)
    ]
    sample_dir = output / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        _write_json(sample, sample_dir / f"{sample['id']}.json")
    _write_json(samples, output / "dataset.draft.json")
    frozen_samples = [
        {
            "sample_id": sample["id"],
            "values": frozen_sample_projection(sample),
            "sha256": _sha256_json(frozen_sample_projection(sample)),
        }
        for sample in samples
    ]
    frozen_allocation = {
        "version": "2.0",
        "frozen_fields": list(FROZEN_FIELDS),
        "samples": frozen_samples,
        "allocation_sha256": _sha256_json(
            [row["values"] for row in frozen_samples]
        ),
    }
    frozen_path = output / "frozen_allocation_manifest.json"
    _write_json(frozen_allocation, frozen_path)
    (output / "calibration_ids.txt").write_text(
        "\n".join(str(sample["id"]) for sample in samples) + "\n",
        encoding="utf-8",
    )
    (output / "calibration_database_ids.txt").write_text(
        "\n".join(calibration) + "\n",
        encoding="utf-8",
    )
    (output / "reserved_final_database_ids.txt").write_text(
        "\n".join(reserved_final) + "\n",
        encoding="utf-8",
    )
    matrix_path = output / "authoring_matrix.csv"
    matrix_rows = [_matrix_row(sample) for sample in samples]
    with matrix_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(matrix_rows[0]))
        writer.writeheader()
        writer.writerows(matrix_rows)
    ledger_path = output / "review_ledger.csv"
    with ledger_path.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(
            handle,
            fieldnames=list(REVIEW_LEDGER_FIELDS),
        ).writeheader()

    manifest = {
        "version": "2.0",
        "status": "draft_not_paper_eligible",
        "source": {
            "url": source_url,
            "license": source_license,
            "archive_sha256": source_archive_sha256,
            "revision": source_revision,
            "public_task_data_included": False,
        },
        "calibration_database_ids": calibration,
        "reserved_final_database_ids": reserved_final,
        "reserved_final_database_files_included": False,
        "database_quick_checks": quick_checks,
        "database_sha256": database_hashes,
        "profile_sha256": profile_hashes,
        "source_metadata_sha256": dict(sorted(metadata_hashes.items())),
        "candidate_pool_evidence": {
            "candidate_database_count": candidate_evidence[
                "candidate_database_count"
            ],
            "candidate_inventory_sha256": candidate_evidence[
                "candidate_inventory_sha256"
            ],
            "candidate_source_manifest_sha256": candidate_evidence[
                "candidate_source_manifest_sha256"
            ],
            "source_file_count": candidate_evidence["source_file_count"],
        },
        "frozen_allocation_manifest_sha256": _sha256_file(frozen_path),
        "review_ledger_columns": list(REVIEW_LEDGER_FIELDS),
        "sample_count": len(samples),
        "authoring_distribution": {
            "database": dict(Counter(row["db_id"] for row in samples)),
            "operation": dict(
                Counter(row["operation_semantics"] for row in samples)
            ),
            "input_mode": dict(Counter(row["input_mode"] for row in samples)),
            "input_format": dict(
                Counter(row["input_format"] for row in samples)
            ),
            "complexity": dict(
                Counter(row["complexity"] for row in samples)
            ),
            "multi_table": sum(row["multi_table"] is True for row in samples),
            "workload_shape": dict(
                Counter(row["workload_shape"] for row in samples)
            ),
        },
    }
    _write_json(manifest, output / "source_asset_manifest.json")
    _write_json(
        {
            "status": "requires_three_distinct_human_participants",
            "participants": [
                {
                    "participant_id": "",
                    "role": "author",
                    "human": True,
                },
                {
                    "participant_id": "",
                    "role": "reviewer",
                    "human": True,
                },
                {
                    "participant_id": "",
                    "role": "reviewer",
                    "human": True,
                },
            ],
        },
        output / "participant_roster.template.json",
    )
    (output / "SOURCE_ATTRIBUTION.md").write_text(
        "# Source attribution\n\n"
        "The database schemas, contents, column-meaning files, and knowledge-base "
        "metadata in this authoring kit are derived from the BIRD Team's "
        "[LiveSQLBench-Base-Lite-SQLite]("
        f"{source_url}), licensed as {source_license}.\n\n"
        "No published LiveSQLBench user query, solution SQL, external-knowledge "
        "answer, or test case is included or used as an authoring source. "
        "Calibration requests must be written independently by the named human "
        "author.\n",
        encoding="utf-8",
    )
    (output / "README_AUTHORING.md").write_text(
        "# Calibration authoring kit\n\n"
        "Status: **draft; not paper-eligible; GPU runs are blocked**.\n\n"
        "This v2 kit contains exactly 60 frozen design slots: 30 for each of "
        "two calibration databases. `frozen_allocation_manifest.json` locks "
        "every design field per sample. Edit the individual JSON objects under "
        "`samples/`; never change a frozen field.\n\n"
        "The workload allocation separates batch size from relational shape: "
        "20 single-row/single-table, 10 small-batch/single-table, 10 "
        "small-batch/multi-table, 10 large-batch/single-table, and 10 "
        "large-or-relational/multi-table samples.\n\n"
        "For every sample, the human author must write a new request without "
        "copying or paraphrasing a published benchmark task. Complete "
        "`input_text`, conflict policy, gold SQL, `gold_plan.write_groups`, "
        "`gold_records`, `gold_tables`, and `author_id`; then set "
        "`independently_authored` to `true`. Plain inserts need new keys. "
        "Insert-ignore and upsert-update cases need an explicit real unique "
        "constraint and a conflict witness in the pristine database. "
        "Upserts must name only the columns that may be updated.\n\n"
        "Two human reviewers, both distinct from the author, must independently "
        "verify semantics, target rows, conflict keys, update masks, and the "
        "absence of a hidden policy. Record each decision with "
        "`record_calibration_review.py`; it binds the decision to the current "
        "revision and authored-content SHA256. Only two approvals of the same "
        "current hash may set `authoring_status` to `approved`.\n\n"
        "Assemble and validate from the project root:\n\n"
        "```bash\n"
        "python scripts/data/assemble_calibration_dataset.py \\\n"
        "  --samples-dir data/calibration/authoring_kit/samples \\\n"
        "  --ids data/calibration/authoring_kit/calibration_ids.txt \\\n"
        "  --output data/calibration/authoring_kit/dataset.json\n\n"
        "python scripts/data/validate_calibration_authoring.py \\\n"
        "  --kit-dir data/calibration/authoring_kit \\\n"
        "  --data data/calibration/authoring_kit/dataset.json \\\n"
        "  --output artifacts/audit/calibration_authoring.json\n"
        "```\n\n"
        "Use `--allow-draft` only for progress reports. Strict validation "
        "produces `ready_for_freeze`, not paper eligibility. Run "
        "`freeze_calibration_authoring.py` to create the canonical dataset, "
        "then run metadata and Gold-MP execution audits. GPU calibration is "
        "permitted only after Gold-MP reaches 100% with no blocking issue.\n",
        encoding="utf-8",
    )
    return manifest


def assign_calibration_participants(
    *,
    samples_dir: str | Path,
    author_id: str,
    reviewer_ids: Iterable[str],
) -> int:
    author = str(author_id).strip()
    reviewers = [str(value).strip() for value in reviewer_ids]
    if _is_placeholder(author):
        raise ValueError("A non-placeholder human author ID is required.")
    if len(reviewers) != 2 or any(
        _is_placeholder(reviewer) for reviewer in reviewers
    ):
        raise ValueError("Exactly two non-placeholder human reviewer IDs are required.")
    if len({author, *reviewers}) != 3:
        raise ValueError("Author and reviewer IDs must identify three distinct people.")
    updated = 0
    for path in sorted(Path(samples_dir).glob("*.json")):
        sample = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(sample, dict):
            raise ValueError(f"Sample file must contain one JSON object: {path}")
        sample["author_id"] = author
        sample["qa_reviews"] = [
            {
                "reviewer_id": reviewer,
                "decision": "pending",
                "semantics_correct": None,
                "gold_target_correct": None,
                "conflict_target_correct": None,
                "update_columns_correct": None,
                "hidden_policy": None,
                "reviewed_revision": None,
                "reviewed_content_sha256": None,
                "reviewed_at_utc": None,
            }
            for reviewer in reviewers
        ]
        _write_json(sample, path)
        updated += 1
    return updated


def assemble_calibration_samples(
    *,
    samples_dir: str | Path,
    ids_path: str | Path,
    output_path: str | Path,
) -> list[dict[str, Any]]:
    sample_root = Path(samples_dir)
    expected_ids = read_noncomment_ids(ids_path)
    expected_set = set(expected_ids)
    discovered = {path.stem: path for path in sample_root.glob("*.json")}
    missing = sorted(expected_set - set(discovered))
    unexpected = sorted(set(discovered) - expected_set)
    if missing or unexpected:
        raise ValueError(
            f"Sample-file allocation mismatch: missing={missing}, unexpected={unexpected}"
        )
    samples: list[dict[str, Any]] = []
    for sample_id in expected_ids:
        value = json.loads(discovered[sample_id].read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Sample file must contain one JSON object: {sample_id}")
        embedded_id = str(value.get("id") or value.get("sample_id") or "")
        if embedded_id != sample_id:
            raise ValueError(
                f"Sample ID mismatch for {discovered[sample_id]}: {embedded_id!r}"
            )
        samples.append(value)
    _write_json(samples, output_path)
    return samples


def _is_placeholder(value: Any) -> bool:
    normalized = str(value or "").strip().upper()
    return (
        not normalized
        or normalized in PLACEHOLDER_VALUES
        or normalized.startswith("REPLACE_WITH_")
    )


def start_calibration_revision(sample_path: str | Path) -> int:
    path = Path(sample_path)
    sample = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(sample, dict):
        raise ValueError(f"Sample file must contain one JSON object: {path}")
    revision = int(sample.get("revision") or 0) + 1
    sample["revision"] = revision
    sample["authoring_status"] = "authored_pending_review"
    reviews = sample.get("qa_reviews")
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("Sample must have exactly two assigned review slots.")
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("Each review slot must be a JSON object.")
        review.update(
            {
                "decision": "pending",
                "semantics_correct": None,
                "gold_target_correct": None,
                "conflict_target_correct": None,
                "update_columns_correct": None,
                "hidden_policy": None,
                "reviewed_revision": None,
                "reviewed_content_sha256": None,
                "reviewed_at_utc": None,
            }
        )
    _write_json(sample, path)
    return revision


def record_calibration_review(
    *,
    sample_path: str | Path,
    ledger_path: str | Path,
    reviewer_id: str,
    decision: str,
    issue_codes: Iterable[str] = (),
    reviewed_at_utc: str | None = None,
) -> dict[str, Any]:
    path = Path(sample_path)
    sample = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(sample, dict):
        raise ValueError(f"Sample file must contain one JSON object: {path}")
    reviewer = str(reviewer_id).strip()
    if _is_placeholder(reviewer):
        raise ValueError("A non-placeholder reviewer ID is required.")
    if reviewer == str(sample.get("author_id") or ""):
        raise ValueError("The author cannot review their own sample.")
    if sample.get("independently_authored") is not True:
        raise ValueError("The author must attest independent authorship first.")
    normalized_decision = str(decision).strip().lower()
    if normalized_decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    issues = sorted(
        {str(value).strip() for value in issue_codes if str(value).strip()}
    )
    if normalized_decision == "rejected" and not issues:
        raise ValueError("A rejected review requires at least one issue code.")
    reviews = sample.get("qa_reviews")
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("Sample must have exactly two assigned review slots.")
    matching = [
        review
        for review in reviews
        if isinstance(review, dict)
        and str(review.get("reviewer_id") or "") == reviewer
    ]
    if len(matching) != 1:
        raise ValueError(f"Reviewer {reviewer!r} is not assigned exactly once.")
    revision = int(sample.get("revision") or 0)
    if revision < 1:
        raise ValueError("Sample revision must be a positive integer.")
    content_hash = authored_content_sha256(sample)
    timestamp = reviewed_at_utc or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    record = {
        "sample_id": str(sample.get("id") or sample.get("sample_id") or ""),
        "revision": revision,
        "authored_content_sha256": content_hash,
        "reviewer_id": reviewer,
        "decision": normalized_decision,
        "issue_codes": ";".join(issues),
        "reviewed_at_utc": timestamp,
    }
    ledger = Path(ledger_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not ledger.exists() or ledger.stat().st_size == 0
    with ledger.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REVIEW_LEDGER_FIELDS))
        if needs_header:
            writer.writeheader()
        writer.writerow(record)
    review = matching[0]
    review.update(
        {
            "decision": normalized_decision,
            "semantics_correct": (
                True if normalized_decision == "approved" else None
            ),
            "gold_target_correct": (
                True if normalized_decision == "approved" else None
            ),
            "conflict_target_correct": (
                True if normalized_decision == "approved" else None
            ),
            "update_columns_correct": (
                True if normalized_decision == "approved" else None
            ),
            "hidden_policy": (
                False if normalized_decision == "approved" else None
            ),
            "reviewed_revision": revision,
            "reviewed_content_sha256": content_hash,
            "reviewed_at_utc": timestamp,
        }
    )
    current_approvals = {
        str(item.get("reviewer_id"))
        for item in reviews
        if isinstance(item, dict)
        and item.get("decision") == "approved"
        and item.get("reviewed_revision") == revision
        and item.get("reviewed_content_sha256") == content_hash
    }
    sample["authoring_status"] = (
        "approved" if len(current_approvals) == 2 else "authored_pending_review"
    )
    _write_json(sample, path)
    return record


def audit_frozen_allocation(
    samples: list[dict[str, Any]],
    manifest_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    expected_rows = manifest.get("samples")
    issues: list[dict[str, Any]] = []
    if not isinstance(expected_rows, list):
        return [
            {
                "sample_id": None,
                "error_code": "INVALID_FROZEN_ALLOCATION_MANIFEST",
                "message": "Manifest samples must be a JSON array.",
            }
        ], {"frozen_status": "invalid", "frozen_issue_count": 1}
    expected = {
        str(row.get("sample_id")): row
        for row in expected_rows
        if isinstance(row, dict)
    }
    actual = {
        str(sample.get("id") or sample.get("sample_id") or ""): sample
        for sample in samples
    }
    for sample_id in sorted(set(expected) | set(actual)):
        if sample_id not in expected:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "UNEXPECTED_SAMPLE_NOT_IN_FROZEN_ALLOCATION",
                    "message": "Sample is absent from the frozen allocation.",
                }
            )
            continue
        if sample_id not in actual:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "MISSING_FROZEN_SAMPLE",
                    "message": "Frozen sample is absent from the dataset.",
                }
            )
            continue
        projection = frozen_sample_projection(actual[sample_id])
        digest = _sha256_json(projection)
        if digest != str(expected[sample_id].get("sha256") or ""):
            changed = [
                field
                for field in FROZEN_FIELDS
                if projection.get(field)
                != (expected[sample_id].get("values") or {}).get(field)
            ]
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "FROZEN_ALLOCATION_CHANGED",
                    "message": f"Frozen fields changed: {changed}",
                }
            )
    return issues, {
        "frozen_sample_count": len(expected),
        "frozen_issue_count": len(issues),
        "frozen_status": "valid" if not issues else "invalid",
    }


def _read_review_ledger(path: str | Path) -> list[dict[str, str]]:
    ledger = Path(path)
    if not ledger.is_file():
        return []
    with ledger.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def audit_review_ledger(
    samples: list[dict[str, Any]],
    ledger_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _read_review_ledger(ledger_path)
    issues: list[dict[str, Any]] = []
    current_approval_count = 0
    for sample in samples:
        sample_id = str(sample.get("id") or sample.get("sample_id") or "")
        revision = int(sample.get("revision") or 0)
        content_hash = authored_content_sha256(sample)
        current_rows = [
            row
            for row in rows
            if row.get("sample_id") == sample_id
            and str(row.get("revision") or "") == str(revision)
            and row.get("authored_content_sha256") == content_hash
        ]
        approvals = {
            str(row.get("reviewer_id") or "")
            for row in current_rows
            if row.get("decision") == "approved"
            and str(row.get("reviewer_id") or "")
        }
        author_id = str(sample.get("author_id") or "")
        if author_id in approvals:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "AUTHOR_APPROVED_REVIEW_LEDGER",
                    "message": "The author appears as an approving reviewer.",
                }
            )
        if len(approvals - {author_id}) != 2:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "MISSING_CURRENT_REVISION_LEDGER_APPROVALS",
                    "message": (
                        "Two independent approvals of the current authored "
                        "content hash are required."
                    ),
                }
            )
        else:
            current_approval_count += 1
        embedded = {
            str(review.get("reviewer_id") or "")
            for review in sample.get("qa_reviews") or []
            if isinstance(review, dict)
            and review.get("decision") == "approved"
            and review.get("reviewed_revision") == revision
            and review.get("reviewed_content_sha256") == content_hash
        }
        if embedded != approvals:
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "EMBEDDED_REVIEW_LEDGER_MISMATCH",
                    "message": "Embedded approvals do not match the review ledger.",
                }
            )
    return issues, {
        "review_ledger_rows": len(rows),
        "samples_with_two_current_approvals": current_approval_count,
        "review_ledger_issue_count": len(issues),
        "review_ledger_status": "valid" if not issues else "invalid",
    }


def audit_calibration_authoring_completion(
    samples: list[dict[str, Any]],
    *,
    calibration_database_ids: Iterable[str],
    reserved_final_database_ids: Iterable[str],
    consumed_sample_ids: Iterable[str] = (),
    consumed_source_groups: Iterable[str] = (),
    frozen_allocation_manifest: str | Path | None = None,
    review_ledger_path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    calibration, reserved_final = _validate_database_allocation(
        calibration_database_ids,
        reserved_final_database_ids,
    )
    issues, metadata_summary = audit_calibration_metadata(
        samples,
        reserved_final_db_ids=reserved_final,
        consumed_sample_ids=consumed_sample_ids,
        consumed_source_groups=consumed_source_groups,
    )
    frozen_summary: dict[str, Any] = {}
    if frozen_allocation_manifest is not None:
        frozen_issues, frozen_summary = audit_frozen_allocation(
            samples,
            frozen_allocation_manifest,
        )
        issues.extend(frozen_issues)
    review_summary: dict[str, Any] = {}
    if review_ledger_path is not None:
        review_issues, review_summary = audit_review_ledger(
            samples,
            review_ledger_path,
        )
        issues.extend(review_issues)

    def issue(sample_id: str | None, code: str, message: str) -> None:
        issues.append(
            {
                "sample_id": sample_id,
                "error_code": code,
                "message": message,
            }
        )

    for index, sample in enumerate(samples):
        sample_id = str(
            sample.get("id") or sample.get("sample_id") or f"<row-{index}>"
        )
        if str(sample.get("db_id") or "") not in calibration:
            issue(
                sample_id,
                "UNALLOCATED_CALIBRATION_DATABASE",
                "Sample database is not one of the two frozen calibration databases.",
            )
        if sample.get("authoring_status") != "approved":
            issue(
                sample_id,
                "AUTHORING_NOT_APPROVED",
                "authoring_status must be approved before calibration.",
            )
        if _is_placeholder(sample.get("author_id")):
            issue(
                sample_id,
                "PLACEHOLDER_AUTHOR_ID",
                "A traceable human author ID is required.",
            )
        reviews = sample.get("qa_reviews")
        if not isinstance(reviews, list) or len(reviews) != 2:
            issue(
                sample_id,
                "INVALID_REVIEW_SLOT_COUNT",
                "Exactly two independent review records are required.",
            )
        else:
            for review_index, review in enumerate(reviews, start=1):
                reviewer_id = (
                    review.get("reviewer_id")
                    if isinstance(review, dict)
                    else None
                )
                if _is_placeholder(reviewer_id):
                    issue(
                        sample_id,
                        "PLACEHOLDER_REVIEWER_ID",
                        f"Review slot {review_index} needs a human reviewer ID.",
                    )
        gold_plan = sample.get("gold_plan")
        if (
            not isinstance(gold_plan, dict)
            or not isinstance(gold_plan.get("write_groups"), list)
            or not gold_plan.get("write_groups")
        ):
            issue(
                sample_id,
                "EMPTY_GOLD_WRITE_PLAN",
                "gold_plan.write_groups must contain the reviewed gold plan.",
            )
        if not isinstance(sample.get("gold_records"), list) or not sample.get(
            "gold_records"
        ):
            issue(
                sample_id,
                "EMPTY_GOLD_RECORDS",
                "gold_records must describe every intended inserted or updated row.",
            )
        if not isinstance(sample.get("gold_tables"), list) or not sample.get(
            "gold_tables"
        ):
            issue(
                sample_id,
                "EMPTY_GOLD_TABLES",
                "gold_tables must list every intended target table.",
            )
        provenance = sample.get("provenance")
        if (
            not isinstance(provenance, dict)
            or provenance.get("public_task_used") is not False
        ):
            issue(
                sample_id,
                "MISSING_NO_PUBLIC_TASK_ATTESTATION",
                "provenance.public_task_used must be explicitly false.",
            )
        operation = str(sample.get("operation_semantics") or "")
        complexity = str(sample.get("complexity") or "")
        if operation == "insert_ignore" and complexity == "single_row":
            issue(
                sample_id,
                "IMPOSSIBLE_SINGLE_ROW_INSERT_IGNORE",
                "A state-changing insert-ignore case needs both a pristine "
                "conflict witness and a non-conflicting row, so it cannot be "
                "single_row.",
            )
        conflict_target = sample.get("conflict_target")
        if operation == "plain_insert" and conflict_target:
            issue(
                sample_id,
                "UNEXPECTED_PLAIN_INSERT_CONFLICT_TARGET",
                "plain_insert must not declare a conflict target.",
            )
        input_mode = str(sample.get("input_mode") or "")
        input_format = str(sample.get("input_format") or "")
        if input_mode == "free_text" and input_format != "free_text":
            issue(
                sample_id,
                "INPUT_MODE_FORMAT_MISMATCH",
                "free_text mode requires free_text input format.",
            )
        if input_mode == "semi_structured" and input_format == "free_text":
            issue(
                sample_id,
                "INPUT_MODE_FORMAT_MISMATCH",
                "semi_structured mode cannot use free_text input format.",
            )

    issue_counts = Counter(item["error_code"] for item in issues)
    summary = {
        **metadata_summary,
        **frozen_summary,
        **review_summary,
        "allocated_calibration_database_ids": calibration,
        "authoring_blocking_issue_count": len(issues),
        "authoring_issues_by_code": dict(sorted(issue_counts.items())),
        "authoring_status": (
            "ready_for_freeze" if not issues else "draft_or_invalid"
        ),
        "paper_result_eligible": False,
        "gpu_run_authorized": False,
    }
    return issues, summary


def audit_authoring_assets(
    kit_dir: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(kit_dir)
    manifest_path = root / "source_asset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issues: list[dict[str, Any]] = []

    def issue(code: str, message: str) -> None:
        issues.append(
            {"sample_id": None, "error_code": code, "message": message}
        )

    calibration = list(manifest.get("calibration_database_ids") or [])
    reserved_final = list(manifest.get("reserved_final_database_ids") or [])
    try:
        _validate_database_allocation(calibration, reserved_final)
    except ValueError as exc:
        issue("INVALID_ASSET_ALLOCATION", str(exc))
    for db_id, expected in (manifest.get("database_sha256") or {}).items():
        path = root / "databases" / str(db_id) / f"{db_id}.sqlite"
        if not path.is_file():
            issue("MISSING_CALIBRATION_DATABASE", str(path))
        elif _sha256_file(path) != expected:
            issue("CALIBRATION_DATABASE_HASH_MISMATCH", str(path))
    for db_id, expected in (manifest.get("profile_sha256") or {}).items():
        path = root / "profiles" / f"{db_id}.json"
        if not path.is_file():
            issue("MISSING_CALIBRATION_PROFILE", str(path))
        elif _sha256_file(path) != expected:
            issue("CALIBRATION_PROFILE_HASH_MISMATCH", str(path))
    for relative, expected in (
        manifest.get("source_metadata_sha256") or {}
    ).items():
        path = root / str(relative)
        if not path.is_file():
            issue("MISSING_SOURCE_METADATA", str(path))
        elif _sha256_file(path) != expected:
            issue("SOURCE_METADATA_HASH_MISMATCH", str(path))
    frozen_path = root / "frozen_allocation_manifest.json"
    expected_frozen = str(
        manifest.get("frozen_allocation_manifest_sha256") or ""
    )
    if not frozen_path.is_file():
        issue("MISSING_FROZEN_ALLOCATION_MANIFEST", str(frozen_path))
    elif _sha256_file(frozen_path) != expected_frozen:
        issue("FROZEN_ALLOCATION_MANIFEST_HASH_MISMATCH", str(frozen_path))
    evidence = manifest.get("candidate_pool_evidence") or {}
    inventory_path = root / "provenance" / "candidate_database_inventory.tsv"
    source_files_path = root / "provenance" / "candidate_source_files.sha256"
    pool_audit_path = root / "provenance" / "candidate_pool_audit.json"
    if not inventory_path.is_file():
        issue("MISSING_CANDIDATE_INVENTORY", str(inventory_path))
    elif _sha256_file(inventory_path) != str(
        evidence.get("candidate_inventory_sha256") or ""
    ):
        issue("CANDIDATE_INVENTORY_HASH_MISMATCH", str(inventory_path))
    if not source_files_path.is_file():
        issue("MISSING_CANDIDATE_SOURCE_MANIFEST", str(source_files_path))
    elif _sha256_file(source_files_path) != str(
        evidence.get("candidate_source_manifest_sha256") or ""
    ):
        issue("CANDIDATE_SOURCE_MANIFEST_HASH_MISMATCH", str(source_files_path))
    if not pool_audit_path.is_file():
        issue("MISSING_CANDIDATE_POOL_AUDIT", str(pool_audit_path))
    else:
        pool_audit = json.loads(pool_audit_path.read_text(encoding="utf-8"))
        expected_pool_count = int(
            evidence.get("candidate_database_count") or 0
        )
        if pool_audit.get("candidate_database_count") != expected_pool_count:
            issue(
                "INVALID_CANDIDATE_POOL_COUNT",
                "Candidate pool audit count does not match the asset manifest.",
            )
        if pool_audit.get("all_database_quick_checks_ok") is not True:
            issue(
                "CANDIDATE_POOL_INTEGRITY_NOT_VERIFIED",
                "All 18 candidate quick checks must be ok.",
            )
    ledger_path = root / "review_ledger.csv"
    if not ledger_path.is_file():
        issue("MISSING_REVIEW_LEDGER", str(ledger_path))
    else:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            fields = tuple(csv.DictReader(handle).fieldnames or ())
        if fields != REVIEW_LEDGER_FIELDS:
            issue(
                "INVALID_REVIEW_LEDGER_HEADER",
                f"Expected {REVIEW_LEDGER_FIELDS}, got {fields}",
            )
    public_task_files = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.name
        in {
            "livesqlbench_data_sqlite.jsonl",
            "mini_dev_sqlite.json",
            "mini_dev_sqlite_gold.sql",
        }
    ]
    if public_task_files:
        issue(
            "PUBLISHED_TASK_DATA_INCLUDED",
            f"Published task files found in authoring kit: {public_task_files}",
        )
    leaked_final = [
        path.relative_to(root).as_posix()
        for db_id in reserved_final
        for path in root.rglob(f"{db_id}*.sqlite")
    ]
    if leaked_final:
        issue(
            "RESERVED_FINAL_DATABASE_INCLUDED",
            f"Reserved final database files found in authoring kit: {leaked_final}",
        )
    return issues, {
        "asset_issue_count": len(issues),
        "asset_status": "valid" if not issues else "invalid",
        "calibration_database_ids": calibration,
        "reserved_final_database_ids": reserved_final,
        "reserved_final_database_files_included": bool(leaked_final),
        "published_task_files_included": bool(public_task_files),
    }
