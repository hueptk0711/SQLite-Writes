from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from nldbwrite_v3.v2_a1.inventories import FORBIDDEN_MODEL_SIDE_KEYS, build_schema_inventory
from nldbwrite_v3.v2_a1.prompt_rendering import sha256_text as v2_sha256_text

from scripts.data.build_stage7c_a3_english_offset_semantics import (
    A1_OFFSET_GUIDE_SPEC_PATH,
    A1_PHASE_M_PROMPT_SPEC_PATH,
    A2_PROMPT_SPEC_PATH,
    A3_PROMPT_SPEC_PATH,
    PATCH_PACKAGE_NAME,
    PHASE_O_OFFSET_SEMANTICS_AMENDMENT,
    STAGE_NAME,
    build_prompt_spec_a3,
    build_run,
    canonical_json,
    oracle_v2_path,
    package_reviewer,
    read_json,
    render_phase_o_a3_messages,
    sha256_file,
)
from scripts.data.validate_stage7c_a3_english_offset_semantics import validate


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def built_stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stage = tmp_path_factory.mktemp("stage7c_a3_patch1") / STAGE_NAME
    build_run(stage)
    return stage


def test_parent_a2_prompt_spec_is_available() -> None:
    assert (ROOT / A2_PROMPT_SPEC_PATH).is_file()
    assert read_json(ROOT / A2_PROMPT_SPEC_PATH)["stage"] == "Stage7C_A2_PHASE_O_PROMPT_FEASIBILITY_AMENDMENT"


def test_a3_system_prompt_is_exact_a2_system_prompt() -> None:
    a3, _ = build_prompt_spec_a3()
    a2 = read_json(ROOT / A2_PROMPT_SPEC_PATH)
    assert a3["system_prompt"] == a2["system_prompt"]
    assert a3["prompt_hashes"]["phase_o_system_prompt_sha256"] == a2["prompt_hashes"]["phase_o_system_prompt_sha256"]


def test_a3_user_prompt_is_a2_plus_exact_offset_block() -> None:
    a3, _ = build_prompt_spec_a3()
    a2 = read_json(ROOT / A2_PROMPT_SPEC_PATH)
    expected = a2["user_prompt_template"].rstrip() + "\n\n" + PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip()
    assert a3["user_prompt_template"] == expected
    assert "end_char is exclusive." in a3["user_prompt_template"]


def test_prompt_change_diff_locks_user_template_only() -> None:
    _, diff = build_prompt_spec_a3()
    assert diff["changed_component"] == "Phase O user prompt template only"
    assert diff["phase_o_system_prompt"]["changed"] is False
    assert diff["phase_o_user_prompt_template"]["changed"] is True


def test_phase_m_hashes_are_unchanged_from_a2_and_a1() -> None:
    a3, diff = build_prompt_spec_a3()
    a2 = read_json(ROOT / A2_PROMPT_SPEC_PATH)
    phase_m = read_json(ROOT / A1_PHASE_M_PROMPT_SPEC_PATH)
    assert a3["prompt_hashes"]["phase_m_system_prompt_sha256"] == a2["prompt_hashes"]["phase_m_system_prompt_sha256"]
    assert a3["prompt_hashes"]["phase_m_user_prompt_template_sha256"] == a2["prompt_hashes"]["phase_m_user_prompt_template_sha256"]
    assert a3["prompt_hashes"]["phase_m_system_prompt_sha256"] == v2_sha256_text(phase_m["system_prompt"])
    assert diff["phase_m_user_prompt_template"]["changed"] is False


def test_offset_guide_serializer_is_unchanged() -> None:
    _, diff = build_prompt_spec_a3()
    guide = read_json(ROOT / A1_OFFSET_GUIDE_SPEC_PATH)
    assert guide["format"] == "one line per Python code point: '<zero_based_index>\\t<character>'"
    assert guide["range_convention"] == "[start_char, end_char)"
    assert diff["offset_guide_serializer"]["changed"] is False


def test_zero_shot_no_examples_and_no_repair() -> None:
    a3, _ = build_prompt_spec_a3()
    assert a3["zero_shot"] is True
    assert a3["examples"] == []
    assert a3["retry"] == 0
    assert a3["repair"] == "none"


def test_rendered_phase_o_a3_messages_include_amendment(built_stage: Path) -> None:
    row = read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")[0]
    messages, digest = render_phase_o_a3_messages(
        row["model_side_input"]["question"],
        row["model_side_input"],
        built_stage / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json",
    )
    assert PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip() in messages[1]["content"]
    assert len(digest) == 64


def test_smoke_set_has_8_unique_fresh_non_gretel_ids(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    ids = [row["sample_id"] for row in rows]
    assert len(rows) == 8
    assert len(set(ids)) == 8
    assert not any(sample_id.startswith("gretel:") for sample_id in ids)


def test_smoke_set_keeps_patch0_question_texts(built_stage: Path) -> None:
    questions = [row["model_side_input"]["question"] for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")]
    assert questions[0] == "Insert account code AC-001, score: 42 into accounts."
    assert questions[-1].startswith("Insert experiment run RUN-A3-008")


def test_model_side_input_has_no_label_leakage(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        assert set(row["model_side_input"]) == {"question", "schema_inventory"}
        assert not FORBIDDEN_MODEL_SIDE_KEYS.intersection(row["model_side_input"])
        assert row["label_side_expected"]["model_side_visible"] is False


def test_value_count_coverage_is_locked_2_3_4_5(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    counts = sorted({len(row["label_side_expected"]["phase_o"]["value_spans"]) for row in rows})
    assert counts == [2, 3, 4, 5]


def test_required_surface_coverage_tags_are_present(built_stage: Path) -> None:
    tags = {tag for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl") for tag in row["coverage_tags"]}
    assert {"text", "integer", "real", "comma", "colon", "quoted_text", "parentheses", "email", "date_like"} <= tags


def test_all_28_spans_use_python_end_exclusive_slicing(built_stage: Path) -> None:
    total = 0
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        question = row["model_side_input"]["question"]
        oracles = row["label_side_expected"]["phase_o_span_text_oracle"]
        for span, oracle in zip(row["label_side_expected"]["phase_o"]["value_spans"], oracles):
            assert question[span["start_char"] : span["end_char"]] == oracle["text"]
            total += 1
    assert total == 28


def test_spans_have_no_surrounding_punctuation_or_labels(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        question = row["model_side_input"]["question"]
        for span in row["label_side_expected"]["phase_o"]["value_spans"]:
            text = question[span["start_char"] : span["end_char"]]
            assert text == text.strip()
            assert not text.startswith(("(", "\"", "'"))
            assert not text.endswith((".", ",", ":", ")", "\"", "'"))


def test_schema_refs_use_v2_tab_col_convention(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        schema = row["model_side_input"]["schema_inventory"]
        assert [item["table_ref"] for item in schema["tables"]] == ["TAB_1"]
        assert [item["column_ref"] for item in schema["columns"]] == [f"COL_{i}" for i in range(1, len(schema["columns"]) + 1)]


def test_phase_m_uses_slot_grounded_v2_contract(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        phase_m = row["label_side_expected"]["phase_m"]
        assert set(phase_m) == {"operation", "table_ref", "assignments"}
        assert "write_groups" not in phase_m and "plan_kind" not in phase_m
        for index, item in enumerate(phase_m["assignments"], start=1):
            assert item == {"slot_ref": f"SLOT_{index}", "evidence_ref": f"EV_{index}", "column_ref": f"COL_{index}"}


def test_v2_oracle_path_admits_all_8_cases(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    results = [oracle_v2_path(row, built_stage / row["synthetic_db_spec"]["sqlite_db_path"]) for row in rows]
    assert sum(result["preflight"] == "ADMITTED" for result in results) == 8
    assert all(result["phase_m_mapping_exact"] for result in results)


def test_v2_oracle_path_reaches_exact_target_state(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        result = oracle_v2_path(row, built_stage / row["synthetic_db_spec"]["sqlite_db_path"])
        assert result["canonical_target_state_exact"] is True
        assert result["observed_target_state_hash"] == row["label_side_expected"]["target_state"]["target_state_hash"]


def test_acceptance_policy_requires_8_of_8_no_average(built_stage: Path) -> None:
    policy = read_json(built_stage / "ACCEPTANCE_POLICY_A3.json")
    acceptance = policy["primary_stage7e0_a3_acceptance"]
    assert acceptance["required_pass_count"] == "8/8"
    assert acceptance["averaging_allowed"] is False
    assert acceptance["seven_of_eight_allowed"] is False


def test_validator_accepts_generated_patch1_artifacts(built_stage: Path) -> None:
    assert validate(built_stage)["status"] == "PASS"


def test_validator_rejects_inclusive_end_char_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    rows = read_jsonl(stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    rows[0]["label_side_expected"]["phase_o"]["value_spans"][0]["end_char"] -= 1
    (stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl").write_text("\n".join(canonical_json(row) for row in rows) + "\n", encoding="utf-8")
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any("python_slice_text_mismatch" in failure or "oracle_" in failure for failure in report["failures"])


def test_validator_rejects_a3_user_prompt_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    spec = read_json(stage / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json")
    spec["user_prompt_template"] = spec["user_prompt_template"].replace("end_char is exclusive.", "end_char is inclusive.")
    write_json(stage / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json", spec)
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert "a3_user_prompt_is_not_a2_plus_exact_amendment" in report["failures"]


def test_validator_rejects_phase_m_hash_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    spec = read_json(stage / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json")
    spec["prompt_hashes"]["phase_m_user_prompt_template_sha256"] = "0" * 64
    write_json(stage / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json", spec)
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert "phase_m_user_hash_not_unchanged" in report["failures"]


def test_validator_rejects_target_state_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_run(stage)
    rows = read_jsonl(stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    rows[0]["label_side_expected"]["target_state"]["typed_target_rows"][0]["score"] = 99
    (stage / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl").write_text("\n".join(canonical_json(row) for row in rows) + "\n", encoding="utf-8")
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any("canonical_target_state_exact" in failure or "oracle_result_mismatch" in failure for failure in report["failures"])


def test_clean_reviewer_zip_validator_passes(built_stage: Path, tmp_path: Path) -> None:
    package = tmp_path / PATCH_PACKAGE_NAME
    package_reviewer(built_stage, package)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(extract)
    result = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7c_a3_english_offset_semantics.py", "--stage-dir", STAGE_NAME],
        cwd=extract,
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"status": "PASS"' in result.stdout


def test_clean_reviewer_zip_pytest_passes(built_stage: Path, tmp_path: Path) -> None:
    if os.environ.get("STAGE7C_SKIP_CLEAN_ZIP_PYTEST") == "1":
        pytest.skip("nested clean-package pytest")
    if not (ROOT / ".git").exists():
        pytest.skip("package extraction has no outer reviewer ZIP to recurse into")
    package = tmp_path / PATCH_PACKAGE_NAME
    package_reviewer(built_stage, package)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(extract)
    env = {**os.environ, "STAGE7C_SKIP_CLEAN_ZIP_PYTEST": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_stage7c_a3_english_offset_semantics.py"],
        cwd=extract,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert "failed" not in result.stdout.lower()


def test_package_sha256_is_raw_zip_hash(built_stage: Path, tmp_path: Path) -> None:
    package = tmp_path / PATCH_PACKAGE_NAME
    digest = package_reviewer(built_stage, package)
    assert digest == sha256_file(package)
    assert package.with_suffix(package.suffix + ".sha256").read_text(encoding="utf-8").startswith(digest)
