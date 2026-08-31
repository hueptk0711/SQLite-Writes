from __future__ import annotations

import json
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

from scripts.data.build_stage7e0_a4_english_preflight import PACKAGE_NAME, STAGE_NAME, build_stage
from scripts.data.validate_stage7e0_a4_english_preflight import validate
from scripts.server.run_stage7e0_a4_english import (
    A4_PROMPT_SPEC_REL,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    MODEL_ID,
    MODEL_REVISION,
    LabelMockGenerator,
    build_phase_o_span_ref_constraint_grammar,
    load_stage7c_a4_rows,
    parse_phase_o_span_ref_output,
    render_phase_o_a4_messages,
    run_stage7e0,
)
from nldbwrite_v3.v2_a1.types import V2A1Error


def accepted_commit_for_tests() -> str:
    if (ROOT / ".git").exists():
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    lock_path = ROOT / STAGE_NAME / "STAGE7E0_A4_LOCK.json"
    if lock_path.is_file():
        return read_json(lock_path)["git_commit"]
    return "UNKNOWN"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stage7e0_a4_uses_exact_candidate_span_prompt() -> None:
    spec = read_json(ROOT / A4_PROMPT_SPEC_REL)
    assert spec["model_id"] == MODEL_ID
    assert spec["model_revision"] == MODEL_REVISION
    rows = load_stage7c_a4_rows(ROOT)
    messages, digest = render_phase_o_a4_messages(rows[0], root=ROOT)
    assert len(digest) == 64
    assert messages[0]["content"] == spec["system_prompt"]
    assert "Candidate span inventory:" in messages[1]["content"]
    assert "SPAN_" in messages[1]["content"]
    assert "start_char" not in messages[1]["content"]
    assert "end_char" not in messages[1]["content"]


def test_phase_o_span_ref_parser_rejects_offsets_unknown_and_duplicates() -> None:
    allowed = ["SPAN_0001", "SPAN_0002"]
    assert parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_0001"]}', allowed) == {
        "operation": "INSERT",
        "span_refs": ["SPAN_0001"],
    }
    with pytest.raises(V2A1Error, match="only operation and span_refs"):
        parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_0001"],"value_spans":[]}', allowed)
    with pytest.raises(V2A1Error) as unknown:
        parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_9999"]}', allowed)
    assert unknown.value.reason_code == "phase_o_unknown_span_refs"
    with pytest.raises(V2A1Error) as duplicate:
        parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_0001","SPAN_0001"]}', allowed)
    assert duplicate.value.reason_code == "phase_o_duplicate_span_refs"


def test_phase_o_constrained_grammar_uses_exact_dynamic_enum() -> None:
    row = load_stage7c_a4_rows(ROOT)[0]
    grammar = build_phase_o_span_ref_constraint_grammar(row["runtime_constraints"]["phase_o_schema"])
    candidate_refs = [candidate["span_ref"] for candidate in row["runtime_constraints"]["candidate_inventory"]]
    assert grammar.span_ref_choices == candidate_refs
    assert grammar.is_complete('{"operation":"INSERT","span_refs":["SPAN_0010"]}')
    assert not grammar.is_prefix('{"operation":"INSERT","span_refs":["SPAN_9999"]}')
    metadata = grammar.metadata()
    assert metadata["label_side_data_used_for_constraints"] is False
    assert metadata["finite_complete_object_enumeration"] is False
    assert metadata["finite_known_answer_candidates"] is False


def test_label_mock_generator_is_explicitly_non_model_evidence() -> None:
    rows = load_stage7c_a4_rows(ROOT)
    metadata = LabelMockGenerator(rows).metadata()
    assert metadata["backend"] == "mock"
    assert metadata["model_called"] is False
    assert metadata["mock_uses_label_side_expected"] is True


def test_mock_runner_primary_gate_passes_10_of_10(tmp_path: Path) -> None:
    args = type(
        "Args",
        (),
        {
            "accepted_protocol_commit": accepted_commit_for_tests(),
            "result_root": str(tmp_path / "result"),
            "backend": "mock",
            "model_name_or_path": DEFAULT_MODEL_PATH,
            "quantization": "none",
            "phase_o_max_new_tokens": 512,
            "phase_m_max_new_tokens": 8192,
            "max_input_tokens": 28672,
            "seed": 42,
            "trust_remote_code": False,
            "resume": False,
            "skip_git_assertions": True,
            "allow_result_root_inside_git": True,
        },
    )()
    summary = run_stage7e0(args)
    assert summary["status"] == "PASS"
    assert summary["primary_pass_count"] == "10/10"
    assert summary["model_called"] is False
    assert summary["gpu_called"] is False
    assert summary["gretel_pilot_opened"] is False
    assert len(read_jsonl(tmp_path / "result" / "primary_case_results.jsonl")) == 10
    assert len(read_jsonl(tmp_path / "result" / "raw_phase_o_generations.jsonl")) == 10
    assert len(read_jsonl(tmp_path / "result" / "raw_phase_m_generations.jsonl")) == 10


def test_builder_and_validator_pass(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    summary = build_stage(stage_dir, package_path)
    assert summary["status"] == "PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT"
    assert summary["mock_primary_pass_count"] == "10/10"
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]
    assert package_path.is_file()
    assert package_path.with_suffix(package_path.suffix + ".sha256").is_file()
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None


def test_stage_lock_forbids_9_of_10_and_gretel(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, tmp_path / PACKAGE_NAME)
    lock = read_json(stage_dir / "STAGE7E0_A4_LOCK.json")
    policy = read_json(stage_dir / "PRIMARY_ACCEPTANCE_POLICY_A4.json")
    protocol = read_json(stage_dir / "RUNNER_PROTOCOL_A4.json")
    assert lock["primary_acceptance"] == "10/10 required; no average and no 9/10 acceptance"
    assert policy["required_pass_count"] == "10/10"
    assert policy["nine_of_ten_allowed"] is False
    assert policy["averaging_allowed"] is False
    assert lock["gretel_pilot_opened"] is False
    assert lock["development_dev_used"] is False
    assert lock["official_test_used"] is False
    assert protocol["model"]["expected_chat_template_sha256"] == EXPECTED_CHAT_TEMPLATE_SHA256
    assert protocol["model"]["quantization_default"] == "none"
    assert protocol["generation_contract"]["resume_allowed"] is False
    assert protocol["generation_contract"]["backend"] == "incremental_json_schema_grammar"
    assert protocol["prompt_contract"]["phase_o_output_keys"] == ["operation", "span_refs"]


def test_clean_reviewer_zip_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, package_path)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        archive.extractall(extract)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7e0_a4_english_preflight.py",
            "--stage-dir",
            STAGE_NAME,
        ],
        cwd=extract,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
