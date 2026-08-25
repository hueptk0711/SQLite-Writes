from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from scripts.data.audit_crudsql_stage6a import (
    EXPECTED_REFERENCE_SOURCES,
    add_reference_row_digests,
    new_reference_sets,
    registry_self_hash,
    run_audit,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_reference_registry(path: Path, *, missing_archived_677: bool = False) -> None:
    loaded = [
        source
        for source in EXPECTED_REFERENCE_SOURCES
        if not (missing_archived_677 and source == "archived_677_pool")
    ]
    forbidden = {
        "sample_ids": ["prior_sample"],
        "source_groups": ["prior_source"],
        "database_ids": ["prior_database"],
        "input_text_sha256": ["not_a_crudsql_question_hash"],
        "canonical_content_sha256": ["not_a_crudsql_content_hash"],
        "database_profile_fingerprints": ["not_a_crudsql_database_hash"],
    }
    source_digest_counts = {
        source: {
            "sample_ids": 1,
            "source_groups": 1,
            "database_ids": 1,
            "input_text_sha256": 1,
            "canonical_content_sha256": 1,
            "database_profile_fingerprints": 1 if source == "final_holdout_release_300" else 0,
        }
        for source in loaded
    }
    registry = {
        "stage": "Stage6A_PATCH2_SEEN_REFERENCE_REGISTRY",
        "status": "PASS_REFERENCE_REGISTRY_FROZEN",
        "expected_source_names": EXPECTED_REFERENCE_SOURCES,
        "loaded_source_names": loaded,
        "expected_forbidden_reference_set_count": len(EXPECTED_REFERENCE_SOURCES),
        "loaded_forbidden_reference_set_count": len(loaded),
        "sources": [
            {"name": source, "sample_count": 0, "source_artifact_sha256": "0"} for source in loaded
        ],
        "forbidden_sets": forbidden,
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
    write_json(path, registry)


def make_type0(table_id: str, question: str, conds: list[list[object]]) -> dict[str, object]:
    return {
        "table_id": table_id,
        "question": question,
        "sql": {
            "agg": [0],
            "cond_conn_op": 1,
            "sel": [-1],
            "conds": conds,
            "u_express": [[-1, 2, [-1, 0, ""]]],
            "type": 0,
        },
    }


def make_read(table_id: str = "abc") -> dict[str, object]:
    return {
        "table_id": table_id,
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


def make_fixture(root: Path, *, bad_column_mapping: bool = False) -> Path:
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
        "header": ["name", "score", "note"],
        "types": ["text", "real", "text"],
        "rows": [["old", 1.0, "seed"]],
    }
    conds = [[99 if bad_column_mapping else 0, 2, "new"], [1, 2, "2.0"]]
    type0_a = make_type0("abc", "add first row", conds)
    type0_b = make_type0("abc", "add second row", [[0, 2, "again"], [1, 2, 3]])
    read = make_read()
    split_rows = {
        "train": [read],
        "dev": [type0_a],
        "test": [read, type0_a, type0_b],
    }
    for split, rows in split_rows.items():
        write_json(crud / "data" / split / f"crud_{split}_table.json", [table])
        write_json(crud / "data" / split / f"crud_{split}_sql.json", rows)
        con = sqlite3.connect(crud / "data" / split / f"{split}.db")
        try:
            con.execute('CREATE TABLE "Table_abc" ("col_1" TEXT, "col_2" REAL, "col_3" TEXT)')
            con.execute('INSERT INTO "Table_abc" VALUES (?, ?, ?)', ("old", 1.0, "seed"))
            con.commit()
        finally:
            con.close()
    subprocess.check_call(["git", "add", "."], cwd=crud)
    subprocess.check_call(["git", "commit", "-m", "fixture"], cwd=crud)
    return crud


def test_crudsql_stage6a_uses_stable_upstream_locator_without_source_id(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)
    registry = tmp_path / "registry.json"
    write_reference_registry(registry)

    report = run_audit(
        crud,
        tmp_path / "out",
        project_root=tmp_path / "missing_project",
        reference_registry_path=registry,
    )

    assert report["type0_adapter"]["official_test_type0_count"] == 2
    assert report["type0_adapter"]["adapter_pass_count"] == 2
    ids = (tmp_path / "out" / "artifacts" / "crudsql_official_test_type0_ids.tsv").read_text(
        encoding="utf-8"
    )
    assert "stage6_crudsql_0000\tcrudsql:test:0001:abc:" in ids
    assert "stage6_crudsql_0001\tcrudsql:test:0002:abc:" in ids


def test_crudsql_stage6a_fails_closed_when_reference_registry_missing(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)

    report = run_audit(crud, tmp_path / "out", project_root=tmp_path / "missing_project")

    assert report["status"] == "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"
    assert report["overlap_audit"]["status"] == "FAIL"
    assert "reference_registry_missing" in report["overlap_audit"]["reference_registry_violations"]


def test_crudsql_stage6a_fails_when_archived_677_reference_missing(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)
    registry = tmp_path / "registry.json"
    write_reference_registry(registry, missing_archived_677=True)

    report = run_audit(
        crud,
        tmp_path / "out",
        project_root=tmp_path / "missing_project",
        reference_registry_path=registry,
    )

    assert report["status"] == "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"
    assert report["overlap_audit"]["status"] == "FAIL"
    assert any(
        "archived_677_pool" in item for item in report["overlap_audit"]["reference_registry_violations"]
    )


def test_crudsql_stage6a_fails_when_registry_self_hash_is_mutated(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)
    registry = tmp_path / "registry.json"
    write_reference_registry(registry)
    registry_json = json.loads(registry.read_text(encoding="utf-8"))
    registry_json["forbidden_sets"]["input_text_sha256"].append("mutated_without_hash_update")
    write_json(registry, registry_json)

    report = run_audit(
        crud,
        tmp_path / "out",
        project_root=tmp_path / "missing_project",
        reference_registry_path=registry,
    )

    assert report["status"] == "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"
    assert "reference_registry_self_hash_mismatch" in report["overlap_audit"][
        "reference_registry_violations"
    ]


def test_reference_row_digests_include_input_text_field() -> None:
    reference = new_reference_sets()
    add_reference_row_digests(
        reference,
        {
            "sample_id": "prior_1",
            "input_text": "Add one new equipment record.",
            "database_id": "prior_db",
        },
    )

    assert len(reference["input_text_sha256"]) == 1


def test_crudsql_stage6a_uses_fresh_isolated_db_for_each_sample(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path)
    registry = tmp_path / "registry.json"
    write_reference_registry(registry)

    run_audit(
        crud,
        tmp_path / "out",
        project_root=tmp_path / "missing_project",
        reference_registry_path=registry,
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "out" / "artifacts" / "crudsql_gold_adapter_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert [row["pre_insert_row_count"] for row in rows] == [1, 1]
    assert [row["post_insert_row_count"] for row in rows] == [2, 2]
    assert (tmp_path / "out" / "isolated_table_dbs" / "crudsql_db_abc.sqlite").is_file()


def test_crudsql_stage6a_verifies_exact_inserted_row_and_null_unspecified_columns(
    tmp_path: Path,
) -> None:
    crud = make_fixture(tmp_path)
    registry = tmp_path / "registry.json"
    write_reference_registry(registry)

    run_audit(
        crud,
        tmp_path / "out",
        project_root=tmp_path / "missing_project",
        reference_registry_path=registry,
    )
    first = json.loads(
        (tmp_path / "out" / "artifacts" / "crudsql_gold_adapter_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )

    assert first["expected_inserted_row"] == ["new", 2.0, None]
    assert first["actual_inserted_row"] == ["new", 2.0, None]


def test_crudsql_stage6a_incorrect_column_mapping_fails(tmp_path: Path) -> None:
    crud = make_fixture(tmp_path, bad_column_mapping=True)
    registry = tmp_path / "registry.json"
    write_reference_registry(registry)

    report = run_audit(
        crud,
        tmp_path / "out",
        project_root=tmp_path / "missing_project",
        reference_registry_path=registry,
    )

    assert report["status"] == "FAIL_NOT_ELIGIBLE_FOR_REGISTRATION"
    assert report["type0_adapter"]["failure_counts"]["condition_column_index_out_of_range"] == 1
