from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from scripts.data.audit_crudsql_stage6a import run_audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_fixture(root: Path) -> Path:
    crud = root / "CRUDSQL"
    subprocess.check_call(["git", "init"], cwd=root)
    subprocess.check_call(["git", "config", "user.email", "fixture@example.test"], cwd=root)
    subprocess.check_call(["git", "config", "user.name", "Fixture"], cwd=root)
    (crud / "data" / "test").mkdir(parents=True)
    (crud / "data" / "train").mkdir(parents=True)
    (crud / "data" / "dev").mkdir(parents=True)
    (crud / "README.md").write_text("CRUDSQL fixture\n", encoding="utf-8")
    (crud / "LICENSE").write_text("GPL-3.0 fixture\n", encoding="utf-8")
    table = {
        "id": "abc",
        "name": "Table_abc",
        "header": ["name", "score"],
        "types": ["text", "real"],
        "rows": [["old", 1.0]],
    }
    type0 = {
        "table_id": "abc",
        "question": "add a row",
        "sql": {
            "agg": [0],
            "cond_conn_op": 1,
            "sel": [-1],
            "conds": [[0, 2, "new"], [1, 2, "2.0"]],
            "u_express": [[-1, 2, [-1, 0, ""]]],
            "type": 0,
        },
    }
    read = {
        "table_id": "abc",
        "question": "read rows",
        "sql": {
            "agg": [0],
            "cond_conn_op": 0,
            "sel": [0],
            "conds": [],
            "u_express": [[-1, 2, [-1, 0, ""]]],
            "type": 3,
        },
    }
    for split, rows in {"train": [read], "dev": [type0], "test": [type0, read]}.items():
        write_json(crud / "data" / split / f"crud_{split}_table.json", [table])
        write_json(crud / "data" / split / f"crud_{split}_sql.json", rows)
        con = sqlite3.connect(crud / "data" / split / f"{split}.db")
        try:
            con.execute('CREATE TABLE "Table_abc" ("col_1" TEXT, "col_2" REAL)')
            con.execute('INSERT INTO "Table_abc" VALUES (?, ?)', ("old", 1.0))
            con.commit()
        finally:
            con.close()
    subprocess.check_call(["git", "add", "."], cwd=crud)
    subprocess.check_call(["git", "commit", "-m", "fixture"], cwd=crud)
    return crud


def test_crudsql_stage6a_audit_compiles_type0_insert_fixture(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)
    report = run_audit(crud, tmp_path / "out", project_root=tmp_path / "missing_project")

    assert report["type0_adapter"]["official_test_type0_count"] == 1
    assert report["type0_adapter"]["adapter_pass_count"] == 1
    assert report["type0_adapter"]["adapter_fail_count"] == 0
    assert report["sqlite_checks"]["test"]["integrity_check"] == "ok"

    ids = (tmp_path / "out" / "artifacts" / "crudsql_official_test_type0_ids.txt").read_text(
        encoding="utf-8"
    )
    assert "crudsql_test_type0_0000_abc" in ids


def test_crudsql_stage6a_fixture_not_registered_when_below_floor(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)
    report = run_audit(crud, tmp_path / "out", project_root=tmp_path / "missing_project")

    assert report["status"] == "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"
    decision = json.loads((tmp_path / "out" / "STAGE6A_DECISION.json").read_text(encoding="utf-8"))
    assert decision["recommended_registration_n"] == 1
    assert decision["recommended_sampling_policy"] == (
        "use_all_eligible_official_test_type0_examples_no_random_sampling"
    )
