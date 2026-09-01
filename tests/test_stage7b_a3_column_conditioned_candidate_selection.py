from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.data.build_stage7b_a2_candidate_span_reference import (  # noqa: E402
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
)
from scripts.data.build_stage7b_a3_column_conditioned_candidate_selection import (  # noqa: E402
    PACKAGE_NAME,
    STAGE_NAME,
    build_stage,
    candidate_type_compatible,
    dynamic_schema_for_columns,
    package_reviewer,
    parse_create_table_columns,
    parse_insert_target,
    split_top_level,
)
from scripts.data.validate_stage7b_a3_column_conditioned_candidate_selection import validate  # noqa: E402


RAW_DIR = ROOT.parent.parent / "external_sources" / "gretel_synthetic_text_to_sql_740ab236"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def raw_dir_or_skip() -> Path:
    if not (RAW_DIR / "synthetic_text_to_sql_train.snappy.parquet").is_file():
        pytest.skip("local Gretel parquet raw_dir is not available")
    pytest.importorskip("pyarrow")
    return RAW_DIR


def test_parse_insert_and_create_table_columns() -> None:
    sql = "INSERT INTO bus_fares (city, avg_fare) VALUES ('Sydney', 3.20);"
    table, columns = parse_insert_target(sql)
    assert table == "bus_fares"
    assert columns == ["city", "avg_fare"]

    context = "CREATE TABLE if not exists bus_fares (id INT, city VARCHAR(20), avg_fare DECIMAL(3,2));"
    parsed = parse_create_table_columns(context, table)
    assert [column.column_name for column in parsed] == ["id", "city", "avg_fare"]
    assert [column.column_ref for column in parsed] == ["COL_1", "COL_2", "COL_3"]
    assert parsed[0].nullable is True


def test_split_top_level_respects_nested_type_commas() -> None:
    value = "id INT, amount DECIMAL(3,2), label TEXT DEFAULT 'a,b'"
    assert split_top_level(value) == ["id INT", "amount DECIMAL(3,2)", "label TEXT DEFAULT 'a,b'"]


def test_dynamic_schema_requires_every_column_and_blocks_free_span_set() -> None:
    columns = parse_create_table_columns(
        'CREATE TABLE "products" ("name" TEXT NOT NULL, "price" REAL, "active" INTEGER DEFAULT 1);',
        "products",
    )
    schema = dynamic_schema_for_columns(columns, ["SPAN_0001", "SPAN_0002"])
    assert schema["required"] == ["operation", "table_ref", "column_span_refs"]
    assert "span_refs" not in schema["properties"]
    assert schema["properties"]["column_span_refs"]["required"] == ["COL_1", "COL_2", "COL_3"]
    for column_schema in schema["properties"]["column_span_refs"]["properties"].values():
        assert column_schema["enum"] == ["OMIT", "SPAN_0001", "SPAN_0002"]


def test_type_compatibility_is_strict_for_numeric_columns() -> None:
    assert candidate_type_compatible("12", "INTEGER")
    assert not candidate_type_compatible("trust_level 5", "INTEGER")
    assert candidate_type_compatible("29.99", "DECIMAL(3,2)")
    assert not candidate_type_compatible("price 29.99", "DECIMAL(3,2)")
    assert candidate_type_compatible("price 29.99", "TEXT")


def test_build_and_validator_pass_on_728_design_train(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]

    freeze = read_json(stage_dir / "A4_VALID_FAIL_FREEZE.json")
    root_cause = read_json(stage_dir / "A4_ROOT_CAUSE_CLASSIFICATION.json")
    schema_audit = read_json(stage_dir / "DESIGN_TRAIN_COLUMN_SCHEMA_AUDIT.json")
    representability = read_json(stage_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.json")
    comparison = read_json(stage_dir / "REPRESENTATION_COMPARISON_AUDIT.json")
    rows = read_jsonl(stage_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.jsonl")

    assert freeze["primary_pass_count"] == "6/10"
    assert freeze["primary_gate_status"] == "FAIL"
    assert freeze["scientific_result_eligible"] is True
    assert freeze["gretel_pilot_opened"] is False
    assert root_cause["root_cause_counts"]["phase_o_severe_under_selection"] == 3
    assert root_cause["root_cause_counts"]["phase_o_non_atomic_broader_span_selection"] == 1
    assert schema_audit["design_sample_count"] == EXPECTED_DESIGN_SAMPLE_COUNT
    assert schema_audit["parse_failure_count"] == 0
    assert representability["assignment_count"] == EXPECTED_ASSIGNMENT_COUNT
    assert representability["assignment_candidate_coverage"] >= 0.99
    assert len(rows) == EXPECTED_DESIGN_SAMPLE_COUNT
    assert comparison["current_free_span_set"]["early_stop_after_one_value_schema_valid"] is True
    assert comparison["column_conditioned_selection"]["early_stop_after_one_value_schema_valid"] is False


def test_every_oracle_row_has_one_decision_per_required_column(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    for row in read_jsonl(stage_dir / "ORACLE_COLUMN_CONDITIONED_REPRESENTABILITY_AUDIT.jsonl"):
        decisions = row["oracle_phase_o_output"]["column_span_refs"]
        assert sorted(decisions) == sorted(row["column_conditioned_schema_required_columns"])
        assert row["dynamic_domain_size_per_column"] == row["candidate_count"] + 1
        assert "span_refs" not in row["oracle_phase_o_output"]


def test_validator_rejects_a4_freeze_tamper(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    freeze_path = stage_dir / "A4_VALID_FAIL_FREEZE.json"
    freeze = read_json(freeze_path)
    freeze["primary_pass_count"] = "10/10"
    freeze_path.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = validate(stage_dir)
    assert report["status"] == "FAIL"
    assert "a4_primary_count_mismatch" in report["failures"]
    assert "derived_manifest_hash_mismatch:A4_VALID_FAIL_FREEZE.json" in report["failures"]


def test_reviewer_package_clean_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    digest = package_reviewer(stage_dir, package_path)
    assert len(digest) == 64

    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        archive.extractall(extract)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "tests/support/windows_py314_pytest_tempdir"])
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7b_a3_column_conditioned_candidate_selection.py",
            "--stage-dir",
            STAGE_NAME,
        ],
        cwd=extract,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
