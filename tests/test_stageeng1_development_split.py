from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.data.build_stageeng1_development_split import (
    build_run,
    leakage_components,
    package_reviewer,
    read_json,
    read_jsonl,
)
from scripts.data.validate_stageeng1_development_split import validate


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def candidate(sample: int, schema: str, prompt: str | None = None, sql: str | None = None) -> dict[str, object]:
    return {
        "sample_id": f"gretel:train:{sample}:{sample:06d}",
        "source_split": "train",
        "source_index": sample,
        "development_allowed": True,
        "official_test_confirmation_only": False,
        "operation": "INSERT",
        "complexity_class": "single_row_insert",
        "v2_literal_grounded_primary_eligible": True,
        "schema_database_group": schema,
        "context_hash": f"context-{schema}",
        "prompt_hash": prompt or f"prompt-{sample}",
        "sql_hash": sql or f"sql-{sample}",
        "raw_row_hash": f"raw-{sample}",
        "initial_state_hash": f"before-{sample}",
        "gold_post_state_hash": f"after-{sample}",
        "insert_assignment_grounding": {
            "assignment_count": 1,
            "all_assignments_supported_direct_literal": True,
            "all_assignments_individually_source_alignable": True,
            "jointly_source_representable": True,
        },
    }


def confirmation(sample: int) -> dict[str, object]:
    row = candidate(sample, f"official-schema-{sample}")
    row["sample_id"] = f"gretel:test:{sample}:{sample:06d}"
    row["source_split"] = "test"
    row["development_allowed"] = False
    row["official_test_confirmation_only"] = True
    row["insert_assignment_grounding"] = {
        "assignment_count": 1,
        "all_assignments_supported_direct_literal": True,
        "all_assignments_individually_source_alignable": True,
        "jointly_source_representable": True,
    }
    return row


def stage0_fixture(tmp_path: Path) -> Path:
    stage0 = tmp_path / "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
    rows = [
        candidate(1, "schema-a", prompt="same-prompt"),
        candidate(2, "schema-a", prompt="same-prompt"),
        candidate(3, "schema-b"),
        candidate(4, "schema-c"),
        candidate(5, "schema-d"),
        candidate(6, "schema-e"),
    ]
    write_jsonl(stage0 / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl", rows)
    write_jsonl(stage0 / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl", [confirmation(101), confirmation(102)])
    write_json(
        stage0 / "STAGEENG0_LOCK.json",
        {
            "stage": "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION",
            "status": "PASS_QUALIFICATION_ARTIFACTS_BUILT",
            "model_called": False,
            "gpu_called": False,
            "git_commit": "fixture",
        },
    )
    write_json(stage0 / "DERIVED_ARTIFACT_MANIFEST.json", {"stage": "fixture", "artifacts": []})
    return stage0


def test_stageeng1_build_freezes_dev_split_without_official_test(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"

    summary = build_run(stage0, stage1, pilot_target=2)

    assert summary["development_dev_count"] == 2
    assert summary["development_train_count"] == 4
    assert summary["development_pilot_pool_count"] == 2
    assert summary["stage0_official_confirmation_count"] == 2
    assert summary["cross_split_signature_violation_count"] == 0
    train_ids = {row["sample_id"] for row in read_jsonl(stage1 / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")}
    dev_ids = {row["sample_id"] for row in read_jsonl(stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")}
    pilot_ids = {row["sample_id"] for row in read_jsonl(stage1 / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")}
    assert not train_ids & dev_ids
    assert pilot_ids <= train_ids
    assert not pilot_ids & dev_ids
    official_ids = read_json(stage1 / "OFFICIAL_TEST_ISOLATION_AUDIT.json")[
        "official_test_confirmation_only_ids_in_stageeng1_split"
    ]
    assert official_ids == []


def test_stageeng1_validator_accepts_generated_fixture(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)

    result = validate(stage1, stage0, rebuild=False, strict_counts=False)

    assert result["status"] == "PASS"
    assert result["development_dev_count"] == 2
    assert result["development_train_count"] == 4


def test_stageeng1_manifest_row_semantics_separate_dev_and_pilot(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)

    dev_rows = read_jsonl(stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")
    train_rows = read_jsonl(stage1 / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    pilot_rows = read_jsonl(stage1 / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")

    assert all(row["development_pilot_pool"] is False for row in dev_rows)
    assert all(row["stageeng1_split"] == "development_train" for row in pilot_rows)
    assert all(row["development_pilot_pool"] is True for row in pilot_rows)
    pilot_ids = {row["sample_id"] for row in pilot_rows}
    assert {
        row["sample_id"] for row in train_rows if row["development_pilot_pool"] is True
    } == pilot_ids


def test_stageeng1_train_dev_union_is_exact_stage0_development_population(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)

    stage0_ids = {
        row["sample_id"] for row in read_jsonl(stage0 / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl")
    }
    train_ids = {row["sample_id"] for row in read_jsonl(stage1 / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")}
    dev_ids = {row["sample_id"] for row in read_jsonl(stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")}

    assert train_ids | dev_ids == stage0_ids
    assert not train_ids & dev_ids


def test_leakage_components_connect_duplicate_prompt_and_schema() -> None:
    rows = [
        {
            **candidate(1, "schema-a", prompt="same-prompt"),
            "normalized_prompt_hash": "same-prompt",
            "sql_template_hash": "template-a",
            "source_row_key": "train:1",
        },
        {
            **candidate(2, "schema-b", prompt="same-prompt"),
            "normalized_prompt_hash": "same-prompt",
            "sql_template_hash": "template-b",
            "source_row_key": "train:2",
        },
    ]

    groups = leakage_components(rows)

    assert len(groups) == 1
    assert groups[0]["sample_ids"] == ["gretel:train:1:000001", "gretel:train:2:000002"]


def test_stageeng1_validator_rejects_official_test_in_split(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)
    path = stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"
    rows = read_jsonl(path)
    bad = dict(rows[0])
    bad["sample_id"] = "gretel:test:101:000101"
    bad["source_split"] = "test"
    bad["development_allowed"] = False
    bad["official_test_confirmation_only"] = True
    rows.append(bad)
    write_jsonl(path, rows)

    result = validate(stage1, stage0, rebuild=False, strict_counts=False)

    assert result["status"] == "FAIL"
    assert "official_test_confirmation_ids_in_stageeng1_split" in result["failures"]


def test_stageeng1_validator_rejects_pilot_row_moved_into_dev(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)
    pilot_rows = read_jsonl(stage1 / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")
    dev_path = stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"
    dev_rows = read_jsonl(dev_path)
    bad = dict(pilot_rows[0])
    bad["stageeng1_split"] = "development_dev"
    bad["development_pilot_pool"] = True
    dev_rows.append(bad)
    write_jsonl(dev_path, dev_rows)

    result = validate(stage1, stage0, rebuild=False, strict_counts=False)

    assert result["status"] == "FAIL"
    assert "pilot_pool_intersects_development_dev" in result["failures"]


def test_stageeng1_validator_rejects_dev_row_marked_as_pilot(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)
    dev_path = stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"
    dev_rows = read_jsonl(dev_path)
    dev_rows[0]["development_pilot_pool"] = True
    write_jsonl(dev_path, dev_rows)

    result = validate(stage1, stage0, rebuild=False, strict_counts=False)

    assert result["status"] == "FAIL"
    assert any(failure.startswith("development_dev_row_marked_pilot:") for failure in result["failures"])


def test_stageeng1_validator_rejects_cross_split_signature(tmp_path: Path) -> None:
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)
    train_path = stage1 / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl"
    dev_path = stage1 / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"
    train_rows = read_jsonl(train_path)
    dev_rows = read_jsonl(dev_path)
    dev_rows[0]["schema_database_group"] = train_rows[0]["schema_database_group"]
    write_jsonl(dev_path, dev_rows)

    result = validate(stage1, stage0, rebuild=False, strict_counts=False)

    assert result["status"] == "FAIL"
    assert any(failure.startswith("signature_cross_split_violation:") for failure in result["failures"])


def test_stageeng1_reviewer_package_is_self_contained(tmp_path: Path) -> None:
    if os.environ.get("STAGEENG1_CLEAN_PACKAGE_CHILD") == "1":
        pytest.skip("avoid recursive clean-package subprocess test")
    stage0 = stage0_fixture(tmp_path)
    stage1 = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
    build_run(stage0, stage1, pilot_target=2)
    package = tmp_path / "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT_PATCH1_FINAL_REVIEWER_PACKAGE_20260830.zip"
    package_reviewer(stage0, stage1, package)
    extract_dir = tmp_path / "extract"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(extract_dir)

    assert (extract_dir / "scripts" / "data" / "build_stageeng0_gretel_qualification.py").is_file()
    validator = (
        "from pathlib import Path; "
        "from scripts.data.validate_stageeng1_development_split import validate; "
        "r=validate(Path('StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT'), "
        "Path('StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION'), "
        "rebuild=False, strict_counts=False); "
        "print(r); "
        "raise SystemExit(0 if r['status']=='PASS' else 1)"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([".", "tests/support/windows_py314_pytest_tempdir"]),
        "STAGEENG1_CLEAN_PACKAGE_CHILD": "1",
    }
    subprocess.run([sys.executable, "-c", validator], cwd=extract_dir, env=env, check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_stageeng1_development_split.py",
            "--basetemp",
            "clean_package_pytest_tmp",
        ],
        cwd=extract_dir,
        env=env,
        check=True,
    )
