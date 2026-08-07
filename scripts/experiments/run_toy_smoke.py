from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from nldbwrite_v3.experiments.run_method import run_method


def _profile() -> dict:
    return {
        "db_id": "student_club",
        "tables": [
            {
                "name": "majors",
                "columns": [
                    {
                        "name": "major_id",
                        "type": "TEXT",
                        "not_null": True,
                        "is_primary_key": True,
                        "is_insertable": True,
                    },
                    {
                        "name": "major_name",
                        "type": "TEXT",
                        "not_null": True,
                        "is_primary_key": False,
                        "is_insertable": True,
                    },
                ],
                "required_insert_columns": ["major_id", "major_name"],
                "primary_keys": ["major_id"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["major_id"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [],
            },
            {
                "name": "students",
                "columns": [
                    {
                        "name": "student_id",
                        "type": "TEXT",
                        "not_null": True,
                        "is_primary_key": True,
                        "is_insertable": True,
                    },
                    {
                        "name": "name",
                        "type": "TEXT",
                        "not_null": True,
                        "is_primary_key": False,
                        "is_insertable": True,
                    },
                    {
                        "name": "age",
                        "type": "INTEGER",
                        "not_null": False,
                        "is_primary_key": False,
                        "is_insertable": True,
                    },
                    {
                        "name": "major_id",
                        "type": "TEXT",
                        "not_null": True,
                        "is_primary_key": False,
                        "is_insertable": True,
                    },
                ],
                "required_insert_columns": ["student_id", "name", "major_id"],
                "primary_keys": ["student_id"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["student_id"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [
                    {
                        "from_column": "major_id",
                        "to_table": "majors",
                        "to_column": "major_id",
                    }
                ],
            },
        ],
    }


def _samples() -> list[dict]:
    return [
        {
            "id": "toy_001",
            "db_id": "student_club",
            "input_text": (
                "Add a new student with id S001 named Nguyen Van A, "
                "age 20, major AI."
            ),
            "gold_sql": [
                "INSERT INTO students "
                "(student_id, name, age, major_id) "
                "VALUES ('S001', 'Nguyen Van A', 20, 'AI')"
            ],
            "gold_records": [
                {
                    "student_id": "S001",
                    "name": "Nguyen Van A",
                    "age": 20,
                    "major_id": "AI",
                }
            ],
            "gold_columns": [
                "students.student_id",
                "students.name",
                "students.age",
                "students.major_id",
            ],
        },
        {
            "id": "toy_002",
            "db_id": "student_club",
            "input_text": "Add major DS with name Data Science.",
            "gold_sql": [
                "INSERT INTO majors (major_id, major_name) "
                "VALUES ('DS', 'Data Science')"
            ],
            "gold_records": [
                {"major_id": "DS", "major_name": "Data Science"}
            ],
            "gold_columns": ["majors.major_id", "majors.major_name"],
        },
        {
            "id": "toy_003",
            "db_id": "student_club",
            "input_text": (
                "Add S002 named Tran B, age 21, major CS and "
                "S003 named Le C, age 19, major AI."
            ),
            "gold_sql": [
                "INSERT INTO students "
                "(student_id, name, age, major_id) VALUES "
                "('S002', 'Tran B', 21, 'CS'), "
                "('S003', 'Le C', 19, 'AI')"
            ],
            "gold_records": [
                {
                    "student_id": "S002",
                    "name": "Tran B",
                    "age": 21,
                    "major_id": "CS",
                },
                {
                    "student_id": "S003",
                    "name": "Le C",
                    "age": 19,
                    "major_id": "AI",
                },
            ],
            "gold_columns": [
                "students.student_id",
                "students.name",
                "students.age",
                "students.major_id",
            ],
        },
    ]


def _reference_cell(
    evidence_id: str,
    normalization: str = "identity",
) -> dict[str, str]:
    return {
        "value_from": evidence_id,
        "normalization": normalization,
    }


def _reference_plan(
    group_id: str,
    table_id: str,
    rows: list[dict[str, dict[str, str]]],
) -> str:
    return json.dumps(
        {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": group_id,
                    "table_id": table_id,
                    "rows": rows,
                    "write_semantics": "plain_insert",
                    "conflict_target_id": None,
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _mp_fs_plus_config() -> dict:
    return {
        "base_config": "configs/final/mp_fs_plus.json",
        "batch_size": 2,
        "inference": {
            "backend": "mock",
            "mock_responses": {
                "toy_001": _reference_plan(
                    "students",
                    "t2",
                    [
                        {
                            "t2.c4": _reference_cell("e2"),
                            "t2.c3": _reference_cell("e3"),
                            "t2.c1": _reference_cell(
                                "e6",
                                "lossless_integer_parsing",
                            ),
                            "t2.c2": _reference_cell("e7"),
                        }
                    ],
                ),
                "toy_002": _reference_plan(
                    "majors",
                    "t1",
                    [
                        {
                            "t1.c1": _reference_cell("e2"),
                            "t1.c2": _reference_cell("e3"),
                        }
                    ],
                ),
                "toy_003": _reference_plan(
                    "students",
                    "t2",
                    [
                        {
                            "t2.c4": _reference_cell("e3"),
                            "t2.c3": _reference_cell("e4"),
                            "t2.c1": _reference_cell(
                                "e6",
                                "lossless_integer_parsing",
                            ),
                            "t2.c2": _reference_cell("e7"),
                        },
                        {
                            "t2.c4": _reference_cell("e8"),
                            "t2.c3": _reference_cell("e9"),
                            "t2.c1": _reference_cell(
                                "e11",
                                "lossless_integer_parsing",
                            ),
                            "t2.c2": _reference_cell("e12"),
                        },
                    ],
                ),
            },
        },
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    data_path = root / "dataset.json"
    ids_path = root / "ids.txt"
    profile_dir = root / "profiles"
    db_root = root / "databases"
    db_dir = db_root / "student_club"
    database_path = db_dir / "student_club.sqlite"

    _write_json(data_path, _samples())
    ids_path.write_text("toy_001\ntoy_002\ntoy_003\n", encoding="utf-8")
    _write_json(profile_dir / "student_club.json", _profile())
    db_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            DROP TABLE IF EXISTS students;
            DROP TABLE IF EXISTS majors;
            CREATE TABLE majors (
                major_id TEXT PRIMARY KEY,
                major_name TEXT NOT NULL
            );
            CREATE TABLE students (
                student_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER,
                major_id TEXT NOT NULL REFERENCES majors(major_id)
            );
            INSERT INTO majors (major_id, major_name)
            VALUES ('AI', 'Artificial Intelligence'),
                   ('CS', 'Computer Science');
            """
        )
        connection.commit()
    finally:
        connection.close()
    return data_path, ids_path, profile_dir, db_root


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a deterministic three-sample mapping smoke with mock outputs."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="experiments/smoke/toy_mp_fs_plus_mock",
    )
    parser.add_argument(
        "--method",
        choices=["mp-fs-plus", "legacy-mp"],
        default="mp-fs-plus",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    output_dir = (project_root / args.output_dir).resolve()
    fixture_dir = output_dir / "_fixture"
    data_path, ids_path, profile_dir, db_root = prepare_fixture(fixture_dir)
    if args.method == "mp-fs-plus":
        method_config = fixture_dir / "mp_fs_plus_mock.json"
        _write_json(method_config, _mp_fs_plus_config())
    else:
        method_config = project_root / "configs" / "smoke" / "toy_mp_mock.json"
    metrics = run_method(
        method_config,
        data_path,
        ids_path,
        profile_dir,
        db_root,
        output_dir,
        resume=False,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics.get("strict_full_state_accuracy") == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
