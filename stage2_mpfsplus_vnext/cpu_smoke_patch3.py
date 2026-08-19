from __future__ import annotations

import json
import sqlite3
import tempfile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.run_method import (
    MAPPING_METHODS,
    PREFLIGHT_METHODS,
    SUPPORTED_METHODS,
    _load_method_config,
    run_method,
)


CONFIGS = [
    Path("configs/stage2/original.json"),
    Path("configs/stage2/v1_control.json"),
    Path("configs/stage2/v2_conflict.json"),
    Path("configs/stage2/v3_update.json"),
]


def _minimal_profile() -> dict:
    return {
        "db_id": "test",
        "tables": [
            {
                "name": "parent",
                "columns": [
                    {
                        "name": "id",
                        "type": "TEXT",
                        "is_primary_key": True,
                        "is_insertable": True,
                        "semantic_type": "identifier",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "name",
                        "type": "TEXT",
                        "not_null": True,
                        "is_insertable": True,
                        "semantic_type": "text",
                        "preserve_as_text": True,
                    },
                    {
                        "name": "count",
                        "type": "INTEGER",
                        "is_insertable": True,
                        "semantic_type": "count",
                    },
                ],
                "required_insert_columns": ["id", "name"],
                "primary_keys": ["id"],
                "unique_indexes": [
                    {
                        "name": "PRIMARY_KEY",
                        "columns": ["id"],
                        "origin": "pk",
                        "is_primary_key": True,
                    }
                ],
                "foreign_keys": [],
            },
            {
                "name": "child",
                "columns": [
                    {"name": "id", "type": "INTEGER", "is_primary_key": True, "is_insertable": True},
                    {"name": "parent_id", "type": "TEXT", "not_null": True, "is_insertable": True},
                    {"name": "note", "type": "TEXT", "not_null": True, "is_insertable": True},
                ],
                "required_insert_columns": ["parent_id", "note"],
                "primary_keys": ["id"],
                "unique_indexes": [
                    {"name": "PRIMARY_KEY", "columns": ["id"], "origin": "pk", "is_primary_key": True}
                ],
                "foreign_keys": [
                    {"from_column": "parent_id", "to_table": "parent", "to_column": "id"}
                ],
            },
            {
                "name": "pair",
                "columns": [
                    {"name": "a", "type": "TEXT", "is_primary_key": True, "is_insertable": True},
                    {"name": "b", "type": "TEXT", "is_primary_key": True, "is_insertable": True},
                    {"name": "value", "type": "TEXT", "not_null": True, "is_insertable": True},
                ],
                "required_insert_columns": ["a", "b", "value"],
                "primary_keys": ["a", "b"],
                "unique_indexes": [
                    {"name": "PRIMARY_KEY", "columns": ["a", "b"], "origin": "pk", "is_primary_key": True}
                ],
                "foreign_keys": [],
            },
        ],
    }


def main() -> None:
    identities = []
    for path in CONFIGS:
        config, _ = _load_method_config(path)
        method = str(config.get("method_id") or "")
        assert method in MAPPING_METHODS
        assert method in PREFLIGHT_METHODS
        assert method in SUPPORTED_METHODS
        identities.append(
            {
                "config": str(path),
                "method_id": method,
                "method_variant": config.get("method_variant"),
                "method_version": config.get("method_version"),
                "stage2_interventions": config.get("stage2_interventions"),
            }
        )

    with tempfile.TemporaryDirectory(prefix="stage2_patch3_smoke_") as td:
        root = Path(td)
        dataset_path = root / "dataset.json"
        ids_path = root / "ids.txt"
        profile_dir = root / "profiles"
        db_root = root / "databases"
        inference_path = root / "mock.json"
        output_dir = root / "run"

        dataset = [
            {
                "id": "s1",
                "sample_id": "s1",
                "db_id": "test",
                "input_text": "For parent, insert id='p1' and name='One'.",
                "input_mode": "free_text",
                "input_format": "free_text",
                "gold_sql": ["INSERT INTO parent (id,name) VALUES ('p1','One');"],
                "operation_semantics": "plain_insert",
                "conflict_sensitive": False,
                "state_changing": True,
            }
        ]
        dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
        ids_path.write_text("s1\n", encoding="utf-8")
        profile_dir.mkdir()
        (profile_dir / "test.json").write_text(
            json.dumps(_minimal_profile()), encoding="utf-8"
        )
        (db_root / "test").mkdir(parents=True)
        connection = sqlite3.connect(db_root / "test" / "test.sqlite")
        connection.executescript(
            "CREATE TABLE parent(id TEXT PRIMARY KEY, name TEXT NOT NULL, count INTEGER);"
            "CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id TEXT NOT NULL,note TEXT NOT NULL,"
            " FOREIGN KEY(parent_id) REFERENCES parent(id));"
            "CREATE TABLE pair(a TEXT,b TEXT,value TEXT NOT NULL, PRIMARY KEY(a,b));"
        )
        connection.close()
        inference_path.write_text(
            json.dumps({"backend": "mock", "batch_size": 1, "mock_default_response": "{}"}),
            encoding="utf-8",
        )

        run_method(
            CONFIGS[-1],
            dataset_path,
            ids_path,
            profile_dir,
            db_root,
            output_dir,
            inference_config_path=inference_path,
            resume=False,
            stage="dev",
        )

        expected = {
            "method_id": "MP-FS+",
            "method_variant": "vnext-v3-update",
            "method_version": "stage2-v3-control-conflict-update",
        }
        checked = {}
        for artifact in ("run_lock.json", "manifest.json", "summary_metadata.json"):
            value = json.loads((output_dir / artifact).read_text(encoding="utf-8"))
            checked[artifact] = {key: value.get(key) for key in expected}
            assert checked[artifact] == expected
        evaluation = json.loads(
            (output_dir / "evaluation.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        checked["evaluation.jsonl"] = {key: evaluation.get(key) for key in expected}
        assert checked["evaluation.jsonl"] == expected

    print(json.dumps({"status": "PASS", "configs": identities, "provenance_artifacts": checked}, indent=2))


if __name__ == "__main__":
    main()
