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

from scripts.data.build_stage7c_a4_candidate_span_phase_o_protocol import (
    PACKAGE_NAME,
    PHASE_O_SYSTEM_PROMPT,
    PHASE_O_USER_PROMPT_TEMPLATE,
    STAGE_NAME,
    STAGE7B_SELECTED_VARIANT,
    build_stage,
    canonical_json,
    oracle_span_ref_path,
    package_reviewer,
    prompt_spec,
    read_json,
    render_phase_o_messages,
    sha256_file,
    sha256_text,
)
from scripts.data.validate_stage7c_a4_candidate_span_phase_o_protocol import validate


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def built_stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stage = tmp_path_factory.mktemp("stage7c_a4_patch0") / STAGE_NAME
    build_stage(stage)
    return stage


def test_prompt_spec_freezes_span_ref_only_contract() -> None:
    spec = prompt_spec()
    assert spec["system_prompt"] == PHASE_O_SYSTEM_PROMPT
    assert spec["user_prompt_template"] == PHASE_O_USER_PROMPT_TEMPLATE
    assert spec["prompt_hashes"]["phase_o_system_prompt_sha256"] == sha256_text(PHASE_O_SYSTEM_PROMPT)
    assert spec["prompt_hashes"]["phase_o_user_prompt_template_sha256"] == sha256_text(PHASE_O_USER_PROMPT_TEMPLATE)
    assert spec["model_generates_character_offsets"] is False
    assert spec["model_generates_values"] is False
    assert spec["model_generates_column_refs"] is False
    assert spec["model_selects_span_refs"] is True
    assert spec["examples"] == []
    assert spec["retry"] == 0
    assert spec["repair"] == "none"


def test_stage_has_10_fresh_non_gretel_cases_with_required_surfaces(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl")
    ids = [row["sample_id"] for row in rows]
    tags = {tag for row in rows for tag in row["coverage_tags"]}
    assert len(rows) == 10
    assert len(set(ids)) == 10
    assert not any(sample_id.startswith("gretel:") for sample_id in ids)
    assert {"2_values", "3_values", "4_values", "5_values"} <= tags
    assert {"email", "identifier", "hex_identifier", "date", "percent", "integer", "real"} <= tags
    assert {"three_word_value", "quoted_multiword", "overlap_distractors"} <= tags


def test_model_side_input_contains_no_labels_or_offsets(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"):
        assert set(row["model_side_input"]) == {"question", "schema_inventory", "candidate_inventory_text"}
        assert "start_char" not in row["model_side_input"]["candidate_inventory_text"]
        assert "end_char" not in row["model_side_input"]["candidate_inventory_text"]
        assert row["label_side_expected"]["model_side_visible"] is False


def test_dynamic_schema_enum_equals_exact_candidate_refs(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"):
        candidates = row["runtime_constraints"]["candidate_inventory"]
        enum = row["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["items"]["enum"]
        assert enum == [candidate["span_ref"] for candidate in candidates]
        assert "SPAN_9999" not in enum
        assert "pattern" not in row["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["items"]
        assert row["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["uniqueItems"] is True


def test_gold_phase_o_output_uses_only_operation_and_span_refs(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"):
        phase_o = row["label_side_expected"]["phase_o"]
        assert sorted(phase_o) == ["operation", "span_refs"]
        assert phase_o["operation"] == "INSERT"
        assert not {"value_spans", "start_char", "end_char", "values", "column_refs"} & set(phase_o)
        assert len(phase_o["span_refs"]) == len(set(phase_o["span_refs"]))


def test_gold_refs_slice_exact_values_and_cover_candidate_inventory(built_stage: Path) -> None:
    total = 0
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"):
        question = row["model_side_input"]["question"]
        candidate_refs = {candidate["span_ref"] for candidate in row["runtime_constraints"]["candidate_inventory"]}
        for gold in row["label_side_expected"]["gold_value_span_ref_oracle"]:
            assert question[gold["start_char"] : gold["end_char"]] == gold["text"]
            assert gold["candidate_span_ref"] in candidate_refs
            total += 1
    assert total == 35


def test_rendered_prompt_contains_question_schema_and_candidate_inventory(built_stage: Path) -> None:
    row = read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl")[0]
    messages, user, digest = render_phase_o_messages(row)
    assert len(messages) == 2
    assert row["model_side_input"]["question"] in user
    assert "Schema inventory:" in user
    assert "Candidate span inventory:" in user
    assert "SPAN_" in user
    assert len(digest) == 64


def test_oracle_span_ref_path_admits_all_cases_and_exact_target_state(built_stage: Path) -> None:
    rows = read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl")
    results = [oracle_span_ref_path(row, built_stage / row["synthetic_db_spec"]["sqlite_db_path"]) for row in rows]
    assert sum(result["preflight"] == "ADMITTED" for result in results) == 10
    assert all(result["canonical_target_state_exact"] for result in results)
    assert all(result["phase_o_output_keys_exact"] for result in results)
    assert all(result["dynamic_enum_exact"] for result in results)


def test_phase_m_downstream_contract_is_unchanged_insert_slots(built_stage: Path) -> None:
    for row in read_jsonl(built_stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"):
        phase_m = row["label_side_expected"]["phase_m"]
        assert set(phase_m) == {"operation", "table_ref", "assignments"}
        assert phase_m["operation"] == "INSERT"
        assert phase_m["table_ref"] == "TAB_1"
        for index, item in enumerate(phase_m["assignments"], start=1):
            assert item == {"slot_ref": f"SLOT_{index}", "evidence_ref": f"EV_{index}", "column_ref": f"COL_{index}"}


def test_token_audit_records_full_rendered_prompt_burden(built_stage: Path) -> None:
    audit = read_json(built_stage / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
    assert audit["fresh_case_count"] == 10
    assert audit["rendered_prompt_char_stats"]["p95"] >= audit["rendered_prompt_char_stats"]["median"]
    assert audit["tokenizer_status"] in {"PASS", "NOT_RUN"}
    if audit["tokenizer_status"] == "PASS":
        assert audit["rendered_prompt_token_stats"]["p95"] >= audit["rendered_prompt_token_stats"]["median"]


def test_candidate_miss_policy_locks_denominator_failure(built_stage: Path) -> None:
    policy = read_json(built_stage / "CANDIDATE_MISS_FAILURE_POLICY.json")
    lock = read_json(built_stage / "STAGE7C_A4_LOCK.json")
    assert policy["may_exclude_sample_for_candidate_miss"] is False
    assert policy["pilot_dev_test_denominator_locked"] is True
    assert lock["candidate_miss_is_method_failure"] is True
    assert lock["candidate_miss_can_exclude_samples"] is False


def test_validator_accepts_generated_artifacts(built_stage: Path) -> None:
    report = validate(built_stage)
    assert report["status"] == "PASS", report["failures"]
    assert report["fresh_english_case_count"] == 10
    assert report["gold_value_count"] == 35
    assert report["oracle_preflight_admitted_count"] == 10


def test_validator_rejects_unknown_span_ref_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    rows = read_jsonl(stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl")
    rows[0]["label_side_expected"]["phase_o"]["span_refs"][0] = "SPAN_9999"
    (stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl").write_text(
        "\n".join(canonical_json(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any("gold_span_ref_missing_from_inventory" in failure or "oracle_exception" in failure for failure in report["failures"])


def test_validator_rejects_dynamic_enum_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    rows = read_jsonl(stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl")
    rows[0]["runtime_constraints"]["phase_o_schema"]["properties"]["span_refs"]["items"]["enum"].append("SPAN_9999")
    (stage / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl").write_text(
        "\n".join(canonical_json(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any("dynamic_enum_not_exact" in failure for failure in report["failures"])


def test_validator_rejects_prompt_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    spec = read_json(stage / "PHASE_O_PROMPT_SPEC_A4_ENGLISH.json")
    spec["user_prompt_template"] = spec["user_prompt_template"].replace("Do not output character offsets", "Output character offsets")
    write_json(stage / "PHASE_O_PROMPT_SPEC_A4_ENGLISH.json", spec)
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert "prompt_user_template_mismatch" in report["failures"]


def test_validator_rejects_source_manifest_tamper(tmp_path: Path) -> None:
    stage = tmp_path / STAGE_NAME
    build_stage(stage)
    manifest = read_json(stage / "SOURCE_INPUT_MANIFEST.json")
    manifest["source_files"][0]["sha256"] = "0" * 64
    write_json(stage / "SOURCE_INPUT_MANIFEST.json", manifest)
    report = validate(stage)
    assert report["status"] == "FAIL"
    assert any(failure.startswith("source_manifest_hash_mismatch") for failure in report["failures"])


def test_clean_reviewer_zip_validator_passes(built_stage: Path, tmp_path: Path) -> None:
    package = tmp_path / PACKAGE_NAME
    package_reviewer(built_stage, package)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        archive.extractall(extract)
    result = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py", "--stage-dir", STAGE_NAME],
        cwd=extract,
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"status": "PASS"' in result.stdout


def test_clean_reviewer_zip_pytest_passes(built_stage: Path, tmp_path: Path) -> None:
    if os.environ.get("STAGE7C_A4_SKIP_CLEAN_ZIP_PYTEST") == "1":
        pytest.skip("nested clean-package pytest")
    package = tmp_path / PACKAGE_NAME
    package_reviewer(built_stage, package)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(extract)
    nested_tmp = extract / "clean_package_pytest_tmp"
    nested_tmp.mkdir()
    env = {
        **os.environ,
        "STAGE7C_A4_SKIP_CLEAN_ZIP_PYTEST": "1",
        "PYTHONPATH": ".",
        "TMP": str(nested_tmp),
        "TEMP": str(nested_tmp),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(nested_tmp),
            "tests/test_stage7c_a4_candidate_span_phase_o_protocol.py",
        ],
        cwd=extract,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert "failed" not in result.stdout.lower()


def test_package_sha256_is_raw_zip_hash(built_stage: Path, tmp_path: Path) -> None:
    package = tmp_path / PACKAGE_NAME
    digest = package_reviewer(built_stage, package)
    assert digest == sha256_file(package)
    assert package.with_suffix(package.suffix + ".sha256").read_text(encoding="utf-8").startswith(digest)


def test_lock_records_no_model_gpu_or_gretel_usage(built_stage: Path) -> None:
    lock = read_json(built_stage / "STAGE7C_A4_LOCK.json")
    assert lock["candidate_generator_variant"] == STAGE7B_SELECTED_VARIANT
    assert lock["model_called"] is False
    assert lock["gpu_called"] is False
    assert lock["gretel_pilot_opened"] is False
    assert lock["development_dev_used"] is False
    assert lock["official_test_used"] is False
