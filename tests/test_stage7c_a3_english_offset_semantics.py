from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

from nldbwrite_v3.planner.prompt import (
    PHASE_O_OFFSET_SEMANTICS_AMENDMENT,
    build_free_text_prompt,
)
from scripts.data.build_stage7c_a3_english_offset_semantics import (
    PATCH_PACKAGE_NAME,
    STAGE_NAME,
    build_run,
    package_reviewer,
)
from scripts.data.validate_stage7c_a3_english_offset_semantics import validate


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def tiny_profile() -> dict[str, object]:
    return {
        "tables": [
            {
                "name": "accounts",
                "columns": [
                    {
                        "name": "account_code",
                        "type": "TEXT",
                        "semantic_type": "identifier",
                        "not_null": True,
                    },
                    {
                        "name": "score",
                        "type": "INTEGER",
                        "semantic_type": "measure",
                        "not_null": True,
                    },
                ],
                "unique_indexes": [],
                "foreign_keys": [],
            }
        ]
    }


def test_phase_o_prompt_contains_python_slice_offset_contract() -> None:
    prompt = build_free_text_prompt(
        "Insert account code AC-001, score: 42 into accounts.",
        tiny_profile(),
        {"reference_planning": True, "conflict_default_policy": "plain_insert"},
    )

    assert PHASE_O_OFFSET_SEMANTICS_AMENDMENT in prompt
    assert "start_char is inclusive." in prompt
    assert "end_char is exclusive." in prompt
    assert "Q[start_char:end_char]" in prompt
    assert "end_char = j + 1." in prompt


def test_stage7c_build_freezes_eight_fresh_english_cases(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME

    summary = build_run(stage)

    cases = read_jsonl(stage / "FRESH_ENGLISH_SYNTHETIC_CASES.jsonl")
    spans = read_jsonl(stage / "PHASE_O_EXPECTED_SPANS.jsonl")
    lock = read_json(stage / "STAGE7C_SMOKE_LOCK.json")
    assert summary["fresh_english_case_count"] == 8
    assert len(cases) == 8
    assert len(spans) == 28
    assert lock["model_called"] is False
    assert lock["gpu_called"] is False
    assert lock["gretel_pilot_opened"] is False


def test_stage7c_expected_spans_are_python_slice_exact(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    cases = {row["case_id"]: row for row in read_jsonl(stage / "FRESH_ENGLISH_SYNTHETIC_CASES.jsonl")}

    for span in read_jsonl(stage / "PHASE_O_EXPECTED_SPANS.jsonl"):
        request = str(cases[span["case_id"]]["request"])
        start = int(span["start_char"])
        end = int(span["end_char"])
        assert request[start:end] == span["text"]
        assert not str(span["text"]).startswith(("(", "\"", "'"))
        assert not str(span["text"]).endswith((".", ",", ":", ")", "\"", "'"))


def test_stage7c_validator_accepts_generated_artifacts(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)

    result = validate(stage)

    assert result["status"] == "PASS"
    assert result["fresh_english_case_count"] == 8
    assert result["sqlite_db_count"] == 8


def test_stage7c_validator_rejects_inclusive_end_char_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    path = stage / "PHASE_O_EXPECTED_SPANS.jsonl"
    spans = read_jsonl(path)
    spans[0]["end_char"] = int(spans[0]["end_char"]) - 1
    write_jsonl(path, spans)

    result = validate(stage)

    assert result["status"] == "FAIL"
    assert any(failure.startswith("python_slice_text_mismatch:") for failure in result["failures"])


def test_stage7c_validator_rejects_surrounding_punctuation_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    path = stage / "PHASE_O_EXPECTED_SPANS.jsonl"
    spans = read_jsonl(path)
    quoted = next(row for row in spans if row["text"] == "Mina Tran")
    quoted["start_char"] = int(quoted["start_char"]) - 1
    quoted["text"] = "\"Mina Tran"
    quoted["selected_text"] = "\"Mina Tran"
    write_jsonl(path, spans)

    result = validate(stage)

    assert result["status"] == "FAIL"
    assert any(failure.startswith("span_contains_leading_punctuation:") for failure in result["failures"])


def test_stage7c_validator_reexecutes_gold_sql_to_target_state(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    path = stage / "TYPED_TARGET_STATES.jsonl"
    states = read_jsonl(path)
    states[0]["typed_target_rows"][0]["score"] = 43
    write_jsonl(path, states)

    result = validate(stage)

    assert result["status"] == "FAIL"
    assert any(failure.startswith("typed_target_state_mismatch:") for failure in result["failures"])


def test_stage7c_coverage_tags_include_requested_surface_forms(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    cases = read_jsonl(stage / "FRESH_ENGLISH_SYNTHETIC_CASES.jsonl")

    tags = {tag for case in cases for tag in case["coverage_tags"]}
    assert {"2_values", "3_values", "4_values", "5_values"} <= tags
    assert {"text", "integer", "real", "comma", "colon", "quoted_text"} <= tags
    assert {"parentheses", "email", "date_like"} <= tags


def test_stage7c_reviewer_package_clean_validator(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    package = tmp_path / PATCH_PACKAGE_NAME
    package_reviewer(stage, package)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(extract)

    assert (extract / "src" / "nldbwrite_v3" / "planner" / "prompt.py").is_file()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7c_a3_english_offset_semantics.py",
            "--stage-dir",
            STAGE_NAME,
        ],
        cwd=extract,
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"status": "PASS"' in result.stdout
